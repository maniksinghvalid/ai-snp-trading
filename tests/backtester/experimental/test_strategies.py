#!/usr/bin/env python3
"""
tests.backtester.experimental.test_strategies — prefix-invariance (no
look-ahead) + hand-built positive/negative/boundary cases for
backtester.experimental.strategies (Phase 10, plan 10-02 Task 2).

Plain def test_* functions, no pytest markers (project convention).
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from bot.safety.et_helpers import ET
from backtester.experimental import strategies


def _build_frame(rows: list) -> pd.DataFrame:
    """rows: [{"day","time","open","high","low","close","volume"}, ...].

    Mirrors the shape backtester.experimental.engine.build_frame produces: a
    tz-aware ET DatetimeIndex, a time_key string column, and per-session
    running hod/lod columns (INCLUDING the current bar).
    """
    idx = pd.DatetimeIndex(
        [pd.Timestamp(f"{r['day']} {r['time']}", tz=ET) for r in rows]
    )
    frame = pd.DataFrame({
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
        "volume": [r["volume"] for r in rows],
    }, index=idx)
    frame["time_key"] = [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in idx]
    session = pd.Series(idx.date, index=idx)
    frame["hod"] = frame["high"].groupby(session).cummax()
    frame["lod"] = frame["low"].groupby(session).cummin()
    return frame


# ============================================================
# Prefix-invariance (no look-ahead) — all three strategies
# ============================================================

def _prefix_test_frame() -> pd.DataFrame:
    day = "2026-01-05"
    t = datetime.strptime("09:30", "%H:%M")
    rows = []
    price = 100.0
    for i in range(40):
        delta = ((i * 37) % 11 - 5) * 0.1
        price = max(50.0, price + delta)
        o = price
        h = price + abs(delta) + 0.3
        l = price - abs(delta) - 0.3
        c = round(price + delta * 0.5, 4)
        v = 1000 + (i * 53) % 500
        rows.append({
            "day": day, "time": t.strftime("%H:%M:%S"),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
        price = c
        t += timedelta(minutes=5)
    return _build_frame(rows)


def test_prefix_invariance_no_lookahead_for_all_three_strategies():
    frame = _prefix_test_frame()
    fns = [strategies.ext2_signals, strategies.orb_signals, strategies.vwap_pb_signals]
    prefix_lengths = [8, 15, 22, 30, 39]

    for fn in fns:
        full = fn(frame, {})
        for k in prefix_lengths:
            prefix = fn(frame.iloc[:k], {})
            row_full = full.iloc[k - 1]
            row_prefix = prefix.iloc[k - 1]
            for col in ("long", "short", "stop_long", "stop_short"):
                a, b = row_full[col], row_prefix[col]
                a_nan = isinstance(a, float) and pd.isna(a)
                b_nan = isinstance(b, float) and pd.isna(b)
                if a_nan and b_nan:
                    continue
                assert a == pytest.approx(b) if isinstance(a, float) else a == b, (
                    f"{fn.__name__} col={col!r} k={k} diverged: full={a!r} prefix={b!r}"
                )


# ============================================================
# ext2: positive (uptrend/downtrend) + negative (flat) cases
# ============================================================

def _trend_frame(day: str, slope: float, n: int = 20, flat_bars: int = 5) -> pd.DataFrame:
    """`flat_bars` bars at a constant price (so SMA settles at that price and an
    actual crossing event exists), followed by a monotonic move of `slope` per
    bar -- a strictly monotonic series from bar 0 never produces a real SMA
    cross (price starts above/below its own not-yet-warmed-up SMA and never
    dips back), so the flat lead-in is required to trigger a real signal."""
    t = datetime.strptime("09:30", "%H:%M")
    rows = []
    price = 100.0
    for i in range(n):
        c = price if i < flat_bars else round(price + slope, 4)
        rows.append({
            "day": day, "time": t.strftime("%H:%M:%S"),
            "open": price, "high": max(price, c) + 0.1, "low": min(price, c) - 0.1,
            "close": c, "volume": 1000,
        })
        price = c
        t += timedelta(minutes=5)
    return _build_frame(rows)


_EXT2_WIDE_WINDOW = {"sma_period": 3, "macd": [2, 3, 2], "confirm_bars": 20,
                     "entry_start": "00:00", "entry_end": "23:59"}


def test_ext2_signals_fires_long_on_strong_uptrend():
    frame = _trend_frame("2026-01-05", slope=1.0)
    signals = strategies.ext2_signals(frame, _EXT2_WIDE_WINDOW)
    assert bool(signals["long"].any()) is True


def test_ext2_signals_fires_short_on_strong_downtrend():
    frame = _trend_frame("2026-01-05", slope=-1.0)
    signals = strategies.ext2_signals(frame, _EXT2_WIDE_WINDOW)
    assert bool(signals["short"].any()) is True


def test_ext2_signals_flat_series_fires_nothing():
    frame = _trend_frame("2026-01-05", slope=0.0)
    signals = strategies.ext2_signals(frame, _EXT2_WIDE_WINDOW)
    assert not signals["long"].any()
    assert not signals["short"].any()


# ============================================================
# orb: positive + negative (no breakout) cases
# ============================================================

def _orb_two_bar_frame(bar1_close: float, bar1_high: float, bar1_low: float) -> pd.DataFrame:
    """bar0 (09:30) forms the 1-bar OR; bar1 (10:30) is the candidate entry bar,
    placed exactly when the completed [09:30,10:30) hourly bin becomes visible
    (span=1 so htf_ema == that bin's close == bar0's close)."""
    rows = [
        {"day": "2026-01-05", "time": "09:30:00",
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "volume": 1000},
        {"day": "2026-01-05", "time": "10:30:00",
         "open": 100.0, "high": bar1_high, "low": bar1_low, "close": bar1_close, "volume": 1000},
    ]
    return _build_frame(rows)


_ORB_PARAMS = {"or_bars": 1, "htf_ema_span": 1}


def test_orb_signals_fires_long_on_or_vwap_htf_breakout():
    frame = _orb_two_bar_frame(bar1_close=110.0, bar1_high=111.0, bar1_low=109.0)
    signals = strategies.orb_signals(frame, _ORB_PARAMS)
    assert bool(signals["long"].iloc[-1]) is True
    assert bool(signals["short"].iloc[-1]) is False


def test_orb_signals_no_breakout_fires_nothing():
    frame = _orb_two_bar_frame(bar1_close=100.2, bar1_high=100.4, bar1_low=99.8)
    signals = strategies.orb_signals(frame, _ORB_PARAMS)
    assert bool(signals["long"].iloc[-1]) is False
    assert bool(signals["short"].iloc[-1]) is False


# ============================================================
# vwap_pb: positive (pullback reclaim) + negative (volume too light) cases
# ============================================================

def _vwap_pb_frame(bar3_volume: float) -> pd.DataFrame:
    rows = [
        {"day": "2026-01-05", "time": "09:30:00",
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "volume": 1000},
        {"day": "2026-01-05", "time": "09:35:00",
         "open": 100.2, "high": 100.8, "low": 100.0, "close": 100.5, "volume": 1000},
        {"day": "2026-01-05", "time": "10:00:00",  # breakout bar (confirms first-hour trend up)
         "open": 101.0, "high": 105.0, "low": 100.8, "close": 100.5, "volume": 1000},
        {"day": "2026-01-05", "time": "10:30:00",  # pullback / reclaim entry bar
         "open": 101.0, "high": 102.0, "low": 100.9, "close": 101.5, "volume": bar3_volume},
    ]
    return _build_frame(rows)


_VWAP_PB_PARAMS = {"or_bars": 2, "htf_ema_span": 1, "vol_lookback": 3}


def test_vwap_pb_signals_fires_long_on_vwap_reclaim_with_volume():
    frame = _vwap_pb_frame(bar3_volume=1500)  # >= 1.2 * mean(1000,1000,1000) = 1200
    signals = strategies.vwap_pb_signals(frame, _VWAP_PB_PARAMS)
    assert bool(signals["long"].iloc[-1]) is True


def test_vwap_pb_signals_no_signal_when_volume_too_light():
    frame = _vwap_pb_frame(bar3_volume=500)  # below the 1.2x average threshold
    signals = strategies.vwap_pb_signals(frame, _VWAP_PB_PARAMS)
    assert bool(signals["long"].iloc[-1]) is False


# ============================================================
# Time-window boundary — half-open [entry_start, entry_end)
# ============================================================

def test_time_window_mask_is_half_open_start_inclusive_end_exclusive():
    idx = pd.DatetimeIndex([
        pd.Timestamp("2026-01-05 09:30:00", tz=ET),
        pd.Timestamp("2026-01-05 09:35:00", tz=ET),
        pd.Timestamp("2026-01-05 09:40:00", tz=ET),
    ])
    frame = pd.DataFrame(
        {"time_key": [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in idx]}, index=idx
    )
    mask = strategies._time_window_mask(frame, "09:35", "09:40")
    assert mask.tolist() == [False, True, False]


# ============================================================
# DEFAULTS parity with arms.json
# ============================================================

def test_defaults_matches_arms_json_defaults_block():
    import json
    import os

    arms_path = os.path.join("backtester", "experimental", "arms.json")
    with open(arms_path, encoding="utf-8") as f:
        arms = json.load(f)["defaults"]

    for strat, params in arms.items():
        for key, value in params.items():
            assert strategies.DEFAULTS[strat][key] == value, (
                f"DEFAULTS[{strat!r}][{key!r}] drifted from arms.json"
            )
