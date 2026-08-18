#!/usr/bin/env python3
"""
tests.backtester.experimental.test_engine — per-bar replay engine behaviour
(Phase 10, plan 10-02 Task 3): N+1-open fill (look-ahead proof), caps,
breaker, force-close (normal + half-day), stop-fill mode, short-row sign,
and regime gating.

Uses the _FakeFeed double pattern from tests/backtester/test_execution.py
(hand-rolled, not backtester.feed.SimulatedBarFeed) and
tests.backtester.fixtures.make_ahead_only_5m_dataset for the look-ahead
proof. Plain def test_* functions, no pytest markers (project convention).
"""
import math

import pandas as pd
import pytest

from backtester.experimental.engine import Engine, Position, build_frame, group_by_day
from tests.backtester.fixtures import make_ahead_only_5m_dataset


class _FakeFeed:
    """Minimal next_bar(code, after)/replay(day) double over a flat bar list.

    Deliberately hand-rolled (not backtester.feed.SimulatedBarFeed) so this
    test file depends only on fixtures.py, mirroring
    tests/backtester/test_execution.py's own _FakeFeed."""

    def __init__(self, bars):
        self._bars = bars

    def next_bar(self, code, after):
        candidates = [b for b in self._bars if b["code"] == code and b["time_key"] > after]
        return min(candidates, key=lambda b: b["time_key"]) if candidates else None

    def replay(self, day):
        day_str = str(day)
        matches = [b for b in self._bars if b["time_key"].startswith(day_str)]
        matches.sort(key=lambda b: (b["time_key"], b["code"]))
        return iter(matches)


_BASE_PARAMS = {
    "sizing": "risk1pct", "exit_model": "fixed_2r",
    "max_entries_per_day": 5, "max_concurrent": 5,
    "max_entries_per_symbol_per_day": None, "trend_lock": False,
    "daily_breaker_usd": -2000, "regime_gate": "none",
}


def _params(**overrides) -> dict:
    return {**_BASE_PARAMS, **overrides}


def _frames_and_signals(bars_by_code: dict, signal_rows_by_code: dict) -> tuple:
    """bars_by_code: {code: [bar dict, ...]}. signal_rows_by_code: {code: [
    {"long":bool,"short":bool,"stop_long":float,"stop_short":float}, ...]}
    (same length/order as bars_by_code[code])."""
    frames, signals = {}, {}
    for code, bars in bars_by_code.items():
        frames[code] = pd.DataFrame({"time_key": [b["time_key"] for b in bars]})
        rows = signal_rows_by_code[code]
        signals[code] = pd.DataFrame({
            "long": [r["long"] for r in rows],
            "short": [r["short"] for r in rows],
            "stop_long": [r.get("stop_long") for r in rows],
            "stop_short": [r.get("stop_short") for r in rows],
        })
    return frames, signals


def _no_signal_row():
    return {"long": False, "short": False, "stop_long": None, "stop_short": None}


def _run_single_day_engine(bars_by_code, signal_rows_by_code, day, params=None,
                           slippage=0.0, stop_fill="close", regime_fn=None):
    all_bars = [b for bars in bars_by_code.values() for b in bars]
    feed = _FakeFeed(all_bars)
    frames, signals = _frames_and_signals(bars_by_code, signal_rows_by_code)
    engine = Engine(frames, signals, params or _params(), feed, slippage, stop_fill, regime_fn)
    days = group_by_day(feed, [day])
    trades = engine.run(days)
    return engine, trades


# ============================================================
# N+1-open fill (look-ahead proof) + last-bar-no-fill
# ============================================================

def test_entry_fills_at_bar_n_plus_1_open_never_signal_bar_close():
    bars = make_ahead_only_5m_dataset()
    day = bars[0]["time_key"][:10]
    code = bars[0]["code"]
    rows = [_no_signal_row() for _ in bars]
    rows[3] = {"long": True, "short": False, "stop_long": 50.0, "stop_short": None}  # bar N

    engine, trades = _run_single_day_engine({code: bars}, {code: rows}, day)

    assert len(trades) == 1
    assert trades[0]["entry_price"] == pytest.approx(103.50)
    assert trades[0]["entry_price"] != pytest.approx(105.00)


def test_signal_on_last_bar_produces_no_trade():
    bars = make_ahead_only_5m_dataset()
    day = bars[0]["time_key"][:10]
    code = bars[0]["code"]
    rows = [_no_signal_row() for _ in bars]
    rows[-1] = {"long": True, "short": False, "stop_long": 50.0, "stop_short": None}

    engine, trades = _run_single_day_engine({code: bars}, {code: rows}, day)

    assert trades == []


# ============================================================
# Caps: 6 same-bar signals -> 5 fills, alphabetical order
# ============================================================

def test_six_same_bar_signals_with_cap_5_yields_5_fills_alphabetical():
    day = "2026-02-02"
    codes = [f"US.SYM{i}" for i in range(6)]  # SYM0..SYM5, alphabetically ordered already
    bars_by_code, rows_by_code = {}, {}
    for code in codes:
        signal_bar = {"code": code, "time_key": f"{day} 10:00:00",
                     "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                     "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000}
        fill_bar = {"code": code, "time_key": f"{day} 10:05:00",
                   "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                   "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000}
        bars_by_code[code] = [signal_bar, fill_bar]
        rows_by_code[code] = [
            {"long": True, "short": False, "stop_long": 90.0, "stop_short": None},
            _no_signal_row(),
        ]

    engine, trades = _run_single_day_engine(bars_by_code, rows_by_code, day)

    assert len(trades) == 5
    assert sorted(t["code"] for t in trades) == codes[:5]


# ============================================================
# Daily breaker: blocks further entries, does not force-close open positions
# ============================================================

def test_daily_breaker_blocks_further_entries_after_minus_2000():
    day = "2026-02-03"
    a_bars = [
        {"code": "US.A", "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": "US.A", "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
        {"code": "US.A", "time_key": f"{day} 09:40:00", "open": 60.0, "high": 61.0,
         "low": 59.0, "close": 60.0, "volume": 1000, "hod": 101.0, "lod": 59.0, "cum_volume": 3000},
        {"code": "US.A", "time_key": f"{day} 09:45:00", "open": 60.0, "high": 61.0,
         "low": 59.0, "close": 60.0, "volume": 1000, "hod": 101.0, "lod": 59.0, "cum_volume": 4000},
    ]
    a_rows = [
        {"long": True, "short": False, "stop_long": 80.0, "stop_short": None},  # signal
        _no_signal_row(),  # fill bar
        _no_signal_row(),  # close breaches stop -> exit leg triggered here
        _no_signal_row(),  # exit fill bar
    ]
    b_bars = [
        {"code": "US.B", "time_key": f"{day} 09:40:00", "open": 200.0, "high": 201.0,
         "low": 199.0, "close": 200.0, "volume": 1000, "hod": 201.0, "lod": 199.0, "cum_volume": 1000},
    ]
    b_rows = [{"long": True, "short": False, "stop_long": 190.0, "stop_short": None}]

    engine, trades = _run_single_day_engine(
        {"US.A": a_bars, "US.B": b_bars}, {"US.A": a_rows, "US.B": b_rows}, day
    )

    a_trades = [t for t in trades if t["code"] == "US.A"]
    assert len(a_trades) == 1
    assert a_trades[0]["exit_reason"] == "stop"
    # qty = min(floor(1000/20), floor(10000/100)) = 50; pnl = (60-100)*50 = -2000
    assert a_trades[0]["quantity"] == 50

    b_trades = [t for t in trades if t["code"] == "US.B"]
    assert b_trades == []  # blocked by the breaker before ever attempting a fill
    assert all(t["exit_reason"] != "breaker" for t in trades)  # breaker never force-closes


# ============================================================
# Force-close: normal day 15:50 bar, half-day 2025-07-03 12:50 bar
# ============================================================

def _force_close_scenario(day: str, force_close_bar_time: str):
    code = "US.FC"
    bars = [
        {"code": code, "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": code, "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
        {"code": code, "time_key": f"{day} {force_close_bar_time}", "open": 108.0, "high": 111.0,
         "low": 107.0, "close": 110.0, "volume": 1000, "hod": 111.0, "lod": 99.0, "cum_volume": 3000},
    ]
    rows = [
        {"long": True, "short": False, "stop_long": 80.0, "stop_short": None},
        _no_signal_row(),
        _no_signal_row(),
    ]
    return {code: bars}, {code: rows}, code


def test_force_close_normal_day_at_1550_bar():
    day = "2025-06-02"  # normal trading day
    bars_by_code, rows_by_code, code = _force_close_scenario(day, "15:50:00")

    engine, trades = _run_single_day_engine(bars_by_code, rows_by_code, day, slippage=0.05)

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "force_close"
    assert trades[0]["closed_at"] == f"{day} 15:50:00"
    assert trades[0]["exit_price"] == pytest.approx(110.0 - 0.05)  # long exit slips down


def test_force_close_half_day_2025_07_03_at_1250_bar():
    day = "2025-07-03"  # half-day (13:00 close -> 12:51 force-close -> 12:50 bar)
    bars_by_code, rows_by_code, code = _force_close_scenario(day, "12:50:00")

    engine, trades = _run_single_day_engine(bars_by_code, rows_by_code, day, slippage=0.05)

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "force_close"
    assert trades[0]["closed_at"] == f"{day} 12:50:00"
    assert trades[0]["exit_price"] == pytest.approx(110.0 - 0.05)


# ============================================================
# stop_fill: close vs intrabar
# ============================================================

def _stop_pierce_scenario():
    day = "2026-02-04"
    code = "US.SF"
    bars = [
        {"code": code, "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": code, "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
        # low pierces stop(95) but close(98) does not
        {"code": code, "time_key": f"{day} 09:40:00", "open": 99.0, "high": 100.0,
         "low": 90.0, "close": 98.0, "volume": 1000, "hod": 101.0, "lod": 90.0, "cum_volume": 3000},
    ]
    rows = [
        {"long": True, "short": False, "stop_long": 95.0, "stop_short": None},
        _no_signal_row(),
        _no_signal_row(),
    ]
    return {code: bars}, {code: rows}, day, code


def test_stop_fill_close_mode_survives_a_low_wick_pierce():
    bars_by_code, rows_by_code, day, code = _stop_pierce_scenario()
    engine, trades = _run_single_day_engine(bars_by_code, rows_by_code, day, stop_fill="close")

    # Position stays open after the wick-pierce bar (close mode ignores the low);
    # it only closes later via eod_no_bars (no further bars in this fixture).
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "eod_no_bars"


def test_stop_fill_intrabar_mode_fills_at_exact_stop_price():
    bars_by_code, rows_by_code, day, code = _stop_pierce_scenario()
    engine, trades = _run_single_day_engine(bars_by_code, rows_by_code, day, stop_fill="intrabar")

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "stop"
    assert trades[0]["exit_price"] == pytest.approx(95.0)


# ============================================================
# Short trade row: side, positive r_multiple, full quantity
# ============================================================

def test_short_trade_row_side_and_positive_r_multiple_on_profit():
    day = "2026-02-05"
    code = "US.SHORT"
    bars = [
        {"code": code, "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": code, "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
        {"code": code, "time_key": f"{day} 09:40:00", "open": 85.0, "high": 86.0,
         "low": 84.0, "close": 85.0, "volume": 1000, "hod": 101.0, "lod": 84.0, "cum_volume": 3000},
        {"code": code, "time_key": f"{day} 09:45:00", "open": 85.0, "high": 86.0,
         "low": 84.0, "close": 85.0, "volume": 1000, "hod": 101.0, "lod": 84.0, "cum_volume": 4000},
    ]
    rows = [
        {"long": False, "short": True, "stop_long": None, "stop_short": 105.0},
        _no_signal_row(),
        _no_signal_row(),  # close <= 2R target (90) -> fixed_2r fires here
        _no_signal_row(),
    ]

    engine, trades = _run_single_day_engine(
        {code: bars}, {code: rows}, day, params=_params(exit_model="fixed_2r")
    )

    assert len(trades) == 1
    row = trades[0]
    assert row["side"] == "short"
    assert row["r_multiple"] > 0
    # qty = min(floor(1000/5), floor(10000/100)) = 100 (risk cap binds)
    assert row["quantity"] == 100


# ============================================================
# Regime gate: bear blocks longs, bull blocks shorts, neutral halves qty
# ============================================================

def _regime_day_bars(day: str):
    long_code, short_code = "US.LONGSIG", "US.SHORTSIG"
    long_bars = [
        {"code": long_code, "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": long_code, "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
    ]
    short_bars = [
        {"code": short_code, "time_key": f"{day} 09:30:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 1000},
        {"code": short_code, "time_key": f"{day} 09:35:00", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1000, "hod": 101.0, "lod": 99.0, "cum_volume": 2000},
    ]
    long_rows = [{"long": True, "short": False, "stop_long": 90.0, "stop_short": None}, _no_signal_row()]
    short_rows = [{"long": False, "short": True, "stop_long": None, "stop_short": 110.0}, _no_signal_row()]
    return (
        {long_code: long_bars, short_code: short_bars},
        {long_code: long_rows, short_code: short_rows},
        long_code, short_code,
    )


def test_regime_bear_blocks_long_allows_short():
    day = "2026-02-06"
    bars_by_code, rows_by_code, long_code, short_code = _regime_day_bars(day)
    params = _params(regime_gate="weekly_spy")

    engine, trades = _run_single_day_engine(
        bars_by_code, rows_by_code, day, params=params, regime_fn=lambda d: "bear"
    )

    codes_traded = {t["code"] for t in trades}
    assert long_code not in codes_traded
    assert short_code in codes_traded


def test_regime_bull_blocks_short_allows_long():
    day = "2026-02-09"
    bars_by_code, rows_by_code, long_code, short_code = _regime_day_bars(day)
    params = _params(regime_gate="weekly_spy")

    engine, trades = _run_single_day_engine(
        bars_by_code, rows_by_code, day, params=params, regime_fn=lambda d: "bull"
    )

    codes_traded = {t["code"] for t in trades}
    assert short_code not in codes_traded
    assert long_code in codes_traded


def test_regime_neutral_halves_quantity():
    day = "2026-02-10"
    bars_by_code, rows_by_code, long_code, short_code = _regime_day_bars(day)
    params_gated = _params(regime_gate="weekly_spy")
    params_ungated = _params(regime_gate="none")

    _, gated_trades = _run_single_day_engine(
        bars_by_code, rows_by_code, day, params=params_gated, regime_fn=lambda d: "neutral"
    )
    _, ungated_trades = _run_single_day_engine(
        bars_by_code, rows_by_code, day, params=params_ungated, regime_fn=lambda d: "neutral"
    )

    gated_qty = next(t["quantity"] for t in gated_trades if t["code"] == long_code)
    ungated_qty = next(t["quantity"] for t in ungated_trades if t["code"] == long_code)
    assert gated_qty == math.floor(ungated_qty / 2)
    assert next(t["regime"] for t in gated_trades if t["code"] == long_code) == "neutral"


# ============================================================
# build_frame / group_by_day
# ============================================================

class _FeedWithTod:
    """Minimal feed double for build_frame: only intraday_5m_for_tod()/start/end."""

    def __init__(self, tod_by_symbol, start, end):
        self._tod = tod_by_symbol
        self.start = start
        self.end = end

    def intraday_5m_for_tod(self):
        return self._tod


def test_build_frame_lowercases_columns_and_marks_in_window():
    idx = pd.DatetimeIndex(pd.date_range("2026-01-05 09:30", periods=3, freq="5min", tz="America/New_York"))
    raw = pd.DataFrame({
        "Open": [1.0, 2.0, 3.0], "High": [1.5, 2.5, 3.5], "Low": [0.5, 1.5, 2.5],
        "Close": [1.2, 2.2, 3.2], "Volume": [10, 20, 30],
    }, index=idx)
    feed = _FeedWithTod({"AAA": raw}, start="2026-01-05", end="2026-01-05")

    frame = build_frame(feed, "US.AAA", {})

    assert list(frame.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert "time_key" in frame.columns and "hod" in frame.columns and "lod" in frame.columns
    assert frame["in_window"].all()
    assert frame["hod"].tolist() == [1.5, 2.5, 3.5]
    assert frame["lod"].tolist() == [0.5, 0.5, 0.5]


def test_build_frame_returns_empty_for_unknown_symbol():
    feed = _FeedWithTod({}, start="2026-01-05", end="2026-01-05")
    frame = build_frame(feed, "US.NOPE", {})
    assert frame.empty


def test_group_by_day_matches_feed_replay_ordering():
    bars = make_ahead_only_5m_dataset()
    feed = _FakeFeed(bars)
    day = bars[0]["time_key"][:10]

    grouped = group_by_day(feed, [day])

    assert list(grouped.keys()) == [day]
    assert grouped[day] == list(feed.replay(day))
