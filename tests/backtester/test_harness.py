#!/usr/bin/env python3
"""
tests.backtester.test_harness — BT-01: BacktestHarness real-construction wiring + replay.

Wave 0 stub (plan 06-01): `pytest.importorskip` guards this whole module until
backtester/harness.py exists (plan 06-05, depends on backtester/feed.py from 06-02 and
backtester/execution.py from 06-03) -- the suite SKIPs cleanly until then, then these real
assertions run RED -> GREEN.

Mirrors tests/test_main_wiring.py's pattern (RISK-TICK-STOP regression, v1.0-MILESTONE-AUDIT
Gap 1): drive the REAL construction sequence with only I/O seams patched (here: yfinance,
the feed's data source), capture the real PositionManager instance, and assert on it directly
-- do NOT hand-build a parallel test-only pipeline (that pattern is exactly what let the
Phase 7 gateway-wiring bug ship).

Dataset design (06-05): unlike the shared ahead-only fixture (tests.backtester.fixtures,
used by test_execution.py's N+1-open proof), this module builds its OWN dedicated dataset
because a full end-to-end replay must additionally satisfy the REAL SignalEngine gates:
  - I1 (close > premarket_high): a separate prepost=True 5m fixture below 09:30 ET.
  - I2 (close >= hod): bot.signal.bar_aggregator's hod snapshot INCLUDES the closing bar's
    own high (confirmed by reading bar_aggregator.py's snap_hod capture order), so the
    signal bar must close AT its own high (no upper wick) to satisfy this gate exactly.
  - I3 (rvol >= 2.0): the harness computes the RVOL-TOD baseline from 14 STRICTLY PRIOR
    sessions (BT-02 no-look-ahead -- harness.py excludes `day` itself before calling
    _compute_tod_baselines), so this fixture supplies 14 flat/low-volume prior business
    days at the SAME 5m bucket times as today's bars, making today's real volume spike a
    genuine (non-self-referential) RVOL breakout.
  - the entry window (10:05-15:30 ET per rules.json): today's bars start at 10:05, not
    09:30, so the patched replay clock is inside the window when the signal fires.
A subsequent crash bar closes below the initial stop so the position actually reaches
CLOSED (only CLOSED positions are captured into trade_log) -- proving the full FSM round
trip: signal -> N+1-open fill -> stop-out -> N+1-open exit -> captured trade.

Asserts:
  - position_manager built by BacktestHarness has gateway=None (Anti-Pattern: no real/mocked
    MoomooGateway in a bar-only replay -- arm_stop_protection() must no-op)
  - setup_day reuses bot.scanner.scanner._evaluate_symbol / _compute_tod_baselines and calls
    signal_engine.set_premarket_highs (point-in-time reuse, BT-02)
  - the patched replay clock drives SignalEngine's entry-window gate (T-06-11)
  - a full replay of the dedicated fixture dataset produces at least one captured trade,
    with the entry fill priced at bar N+1's open (not the signal bar's close -- BT-02)
  - bar_buffer is populated during the replay (Pitfall 6 wiring)
"""
import asyncio
from datetime import datetime, timedelta

import pandas as pd
import pandas_market_calendars as mcal
import pytest

mod = pytest.importorskip("backtester.harness")
feed_mod = pytest.importorskip("backtester.feed")

BacktestHarness = mod.BacktestHarness
SimulatedBarFeed = feed_mod.SimulatedBarFeed

from bot.position.state import PositionPhase
from tests.backtester.fixtures import recent_session_days

# _DAY MUST equal _MD_DAY1 (both derived from the SAME recent_session_days(2) call):
# _make_5m_frame's shared prior-14-business-day warm-up anchor (below) is built relative
# to _DAY, and that same helper is reused by the multi-day test's _mock_yf_download_multiday
# for BOTH _TESTA_BARS and _TESTB_BARS (each anchored to _MD_DAY1/_MD_DAY2). If _DAY and
# _MD_DAY1 were derived independently they could diverge, causing _make_5m_frame's warm-up
# window to overlap _MD_DAY1 itself and silently duplicate-inject quiet-volume bars into
# that day's replay stream.
_MD_DAY1, _MD_DAY2 = recent_session_days(2)
_DAY = _MD_DAY1
_SIGNAL_BUCKETS = ["10:05", "10:10", "10:15", "10:20", "10:25", "10:30", "10:35"]

# (time_key, open, high, low, close, volume)
_TODAY_BARS = [
    (f"{_DAY} 10:05:00", 100.00, 100.50, 99.50, 100.20, 1_000),
    (f"{_DAY} 10:10:00", 100.20, 100.80, 100.00, 100.60, 1_000),
    (f"{_DAY} 10:15:00", 100.60, 101.00, 100.30, 100.90, 1_000),
    # Signal bar: closes AT its own high (no upper wick) so I2 (close >= hod, where hod
    # INCLUDES this bar's own high) is satisfiable; volume spike drives I3 (rvol >= 2.0).
    (f"{_DAY} 10:20:00", 100.90, 105.00, 100.80, 105.00, 40_000),
    # N+1 bar: gap DOWN from the signal bar's close (105.00) -- the entry must fill here
    # (open=103.00), never at the signal bar's close (BT-02 look-ahead proof).
    (f"{_DAY} 10:25:00", 103.00, 103.50, 101.00, 101.50, 5_000),
    # Crash bar: closes well below the initial stop (LOD(99.50) * 0.99 = 98.505) -- triggers
    # STOP_OUT so the position actually reaches CLOSED (captured into trade_log).
    (f"{_DAY} 10:30:00", 101.50, 101.50, 90.00, 90.00, 5_000),
    # Exit fill bar (N+1 relative to the crash bar).
    (f"{_DAY} 10:35:00", 91.00, 91.50, 89.00, 90.50, 5_000),
]


def _make_daily_frame():
    """Tz-naive 200-business-day daily frame ending the day before _DAY (matches real
    yfinance daily-bar tz-naive convention, tests/scanner/test_scanner.py) with closes
    rising linearly from 70.0 to 90.0 so the REAL _evaluate_symbol (never mocked here --
    CR-06 requires "US.TEST" to actually land in the capped watchlist for the full-replay
    test) produces a passing candidate: 200 prior rows satisfy both the RVOL lookback
    (14) and the hardcoded SMA200 window (mean ~80.0, below the most recent close of
    90.0 -- D2 prior_close > sma200); the most recent close/high (90.0/90.5) sit below
    the injected TodayPrice's synthetic close (97.5, from _make_premarket_frame's last
    bar) so D1 (today close > prior-day high) and D3 (>= 3% gap) both pass too.
    """
    periods = 200
    dates = pd.bdate_range(end=pd.Timestamp(_DAY) - pd.Timedelta(days=1), periods=periods)
    closes = [70.0 + i * (90.0 - 70.0) / (periods - 1) for i in range(periods)]
    return pd.DataFrame(
        {"Open": closes, "High": [c + 0.5 for c in closes], "Low": [c - 0.5 for c in closes],
         "Close": closes, "Volume": [500_000] * periods},
        index=dates,
    )


def _make_premarket_frame():
    """prepost=True 5m frame with bars before 09:30 ET on _DAY -- premarket_high=98.0,
    comfortably below the signal bar's close (105.00) so I1 passes."""
    index = pd.to_datetime(
        [f"{_DAY} 09:00:00", f"{_DAY} 09:15:00"]
    ).tz_localize("America/New_York")
    return pd.DataFrame(
        {"Open": [97.0, 97.5], "High": [98.0, 97.8], "Low": [96.5, 97.0],
         "Close": [97.8, 97.5], "Volume": [2_000, 2_000]},
        index=index,
    )


def _make_5m_frame(today_bars=_TODAY_BARS):
    """Combined 5m frame: 14 prior business days (flat, low volume, SAME bucket times as
    today) + today's dedicated signal/fill/stop-out/exit sequence. Serves BOTH the feed's
    own replay load and the harness's TOD-baseline call (both request interval="5m",
    prepost=False) -- the harness itself is responsible for excluding `day` before
    computing baselines (BT-02 no-look-ahead; see backtester.harness._prior_sessions_only).

    today_bars: overridable so a test can replace the default signal/fill/stop-out/exit
    sequence with its own (e.g. a shorter sequence that leaves a position open at EOD,
    CR-05)."""
    rows = []
    index = []
    for time_key, o, h, l, c, v in today_bars:
        index.append(pd.Timestamp(time_key))
        rows.append((o, h, l, c, v))

    prior_dates = pd.bdate_range(end=pd.Timestamp(_DAY) - pd.Timedelta(days=1), periods=14)
    for d in prior_dates:
        for bucket in _SIGNAL_BUCKETS:
            index.append(pd.Timestamp(f"{d.date()} {bucket}:00"))
            rows.append((100.0, 100.2, 99.8, 100.0, 1_000))

    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(index))
    df = df.sort_index()
    df.index = df.index.tz_localize("America/New_York")
    return df


def _mock_yf_download(*_args, **kwargs):
    interval = kwargs.get("interval")
    prepost = kwargs.get("prepost", False)
    if interval == "1d":
        return _make_daily_frame()
    if interval == "5m" and prepost:
        return _make_premarket_frame()
    if interval == "5m":
        return _make_5m_frame()
    return pd.DataFrame()


def _build_real_harness(monkeypatch, tmp_path):
    """Construct the REAL BacktestHarness with only the feed's yfinance seam patched."""
    from bot.config.loader import load_strategy_config
    from bot.state.store import StateStore

    monkeypatch.setattr("yfinance.download", _mock_yf_download)

    cfg = load_strategy_config("rules.json")
    store = StateStore(db_path=str(tmp_path / "backtest_scratch.db")).open()
    feed = SimulatedBarFeed(["US.TEST"], start=_DAY, end=_DAY, cache_dir=str(tmp_path / "cache"))

    harness = BacktestHarness(cfg=cfg, feed=feed, store=store)
    return harness


def test_harness_builds_position_manager_with_gateway_none(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    assert harness.position_manager._gateway is None, (
        "BacktestHarness must pass gateway=None into PositionManager -- a bar-only replay "
        "has no tick-level stop mechanism, so arm_stop_protection() must correctly no-op "
        "(06-PATTERNS Anti-Patterns; matches production's use_broker_stop_orders=false path)."
    )


def test_harness_store_uses_scratch_db_path(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    assert harness._store._db_path != "data/bot_state.db", (
        "StateStore must be opened at a scratch db_path -- never the live bot's "
        "data/bot_state.db (06-RESEARCH Pitfall 4)."
    )


def test_harness_wires_simulated_gateway_into_signal_and_risk_engines(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    assert harness.signal_engine._gateway is harness.sim_gateway
    assert harness.risk_engine._gateway is harness.sim_gateway


def test_setup_day_reuses_scanner_point_in_time_functions(monkeypatch, tmp_path):
    """BT-01/BT-02: setup_day must reuse _evaluate_symbol/_compute_tod_baselines (never
    reimplement SMA/RVOL math), STORE (never apply) the day's premarket highs (CR-01 --
    applying immediately would clobber every other day's highs since run.py's driver
    calls setup_day for ALL days before any of them is replayed), and cap+rank the
    watchlist (CR-06)."""
    harness = _build_real_harness(monkeypatch, tmp_path)

    calls = {}

    def fake_evaluate_symbol(symbol, data, cfg, scan_date, today_price):
        calls["evaluate_symbol_scan_date"] = scan_date
        calls["evaluate_symbol_symbol"] = symbol
        return {
            "code": "US.TEST", "gap_pct": 5.0, "prior_day_high": 90.0,
            "prior_close": 88.0, "sma200": 80.0, "rvol_baseline": 10_000.0,
        }

    def fake_compute_tod_baselines(frame, lookback_days):
        calls["tod_lookback_days"] = lookback_days
        return {"10:20": 4_000.0}

    monkeypatch.setattr("backtester.harness._evaluate_symbol", fake_evaluate_symbol)
    monkeypatch.setattr("backtester.harness._compute_tod_baselines", fake_compute_tod_baselines)

    premarket_calls = []
    monkeypatch.setattr(
        harness.signal_engine, "set_premarket_highs",
        lambda mapping: premarket_calls.append(mapping),
    )

    harness.setup_day(_DAY, ["US.TEST"])

    assert calls["evaluate_symbol_scan_date"] == _DAY
    assert calls["evaluate_symbol_symbol"] == "TEST"
    assert calls["tod_lookback_days"] == harness._cfg.rvol_tod_lookback_days

    # CR-01: setup_day stores the day's highs but does NOT call set_premarket_highs --
    # only replay_day (right before that day's own bars) applies them.
    assert _DAY in harness._premarket_highs_by_day
    assert premarket_calls == []

    # CR-06: the single candidate lands in the capped/ranked watchlist for the day.
    assert harness._watchlist_by_day[_DAY] == {"US.TEST"}

    # persist_watchlist / upsert_tod_baselines wrote rows keyed by the SAME session date.
    watchlist_row = harness._store.conn.execute(
        "SELECT scan_date, code, gap_pct FROM daily_scan WHERE code='US.TEST'"
    ).fetchone()
    assert tuple(watchlist_row) == (_DAY, "US.TEST", 5.0)

    tod_row = harness._store.conn.execute(
        "SELECT scan_date, code, time_bucket, cum_vol_mean FROM tod_baselines WHERE code='US.TEST'"
    ).fetchone()
    assert tuple(tod_row) == (_DAY, "US.TEST", "10:20", 4_000.0)

    # replay_day (never setup_day) is what actually applies the frozen highs (CR-01).
    asyncio.run(harness.replay_day(_DAY))
    assert len(premarket_calls) == 1
    assert premarket_calls[0] == harness._premarket_highs_by_day[_DAY]


def test_replay_clock_drives_entry_window_gate(monkeypatch, tmp_path):
    """T-06-11: the patched now_et must return the REPLAY bar's ET time, not the wall
    clock -- proven by flipping _in_entry_window() with two different replay-clock values."""
    import bot.signal.signal_engine as se_mod

    harness = _build_real_harness(monkeypatch, tmp_path)
    original_now_et = se_mod.now_et
    try:
        se_mod.now_et = lambda: harness._parse_time_key_et(f"{_DAY} 10:20:00")  # inside window
        assert harness.signal_engine._in_entry_window() is True

        se_mod.now_et = lambda: harness._parse_time_key_et(f"{_DAY} 09:00:00")  # before earliest
        assert harness.signal_engine._in_entry_window() is False
    finally:
        se_mod.now_et = original_now_et


def test_full_replay_produces_a_closed_trade_filled_at_next_bar_open(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    harness.setup_day(_DAY, ["US.TEST"])
    asyncio.run(harness.run())

    assert len(harness.trade_log) >= 1, (
        "a full replay of the dedicated signal/fill/stop-out fixture must capture at "
        "least one closed trade (the N+1-open fill on the breakout signal, followed by "
        "a stop-out exit)."
    )

    trade = harness.trade_log[0]
    assert trade["entry_price"] == 103.00, (
        "entry_price must equal bar N+1's open (103.00) -- never the signal bar's own "
        "close (105.00), which would be a look-ahead bug (BT-02)."
    )
    assert trade["code"] == "US.TEST"
    assert trade["exit_reason"] == "stop_out"
    assert trade.get("opened_at") is not None, (
        "trade_log rows must carry opened_at (exposure metrics, trades.csv column)"
    )

    # Pitfall 6: bar_buffer must have been populated during the replay so the swing-low
    # trail (POS-03) has data to compute from.
    assert len(harness._bar_buffer["US.TEST"]) > 0


# Same signal/fill sequence as _TODAY_BARS but with NO crash/exit bar afterward -- the
# entry fills at 10:25's open (N+1) and the position is simply still open when the day's
# last replayed bar (10:25) ends (CR-05: force_close_all must reach it at EOD).
_OPEN_AT_EOD_BARS = [
    (f"{_DAY} 10:05:00", 100.00, 100.50, 99.50, 100.20, 1_000),
    (f"{_DAY} 10:10:00", 100.20, 100.80, 100.00, 100.60, 1_000),
    (f"{_DAY} 10:15:00", 100.60, 101.00, 100.30, 100.90, 1_000),
    (f"{_DAY} 10:20:00", 100.90, 105.00, 100.80, 105.00, 40_000),
    (f"{_DAY} 10:25:00", 103.00, 103.50, 101.00, 103.20, 5_000),
]


def test_replay_day_force_closes_position_left_open_at_eod(monkeypatch, tmp_path):
    """CR-05: a position still open at the day's last replayed bar must be force-closed
    by run() -- reaching CLOSED and landing in trade_log with exit_reason == 'force_close'
    and a real exit price (the last observed bar's close, per backtester/execution.py's
    force-close fill mechanic, not a fabricated/None price)."""
    from bot.config.loader import load_strategy_config
    from bot.state.store import StateStore

    def _mock_yf_download_no_exit(*_args, **kwargs):
        interval = kwargs.get("interval")
        prepost = kwargs.get("prepost", False)
        if interval == "1d":
            return _make_daily_frame()
        if interval == "5m" and prepost:
            return _make_premarket_frame()
        if interval == "5m":
            return _make_5m_frame(_OPEN_AT_EOD_BARS)
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", _mock_yf_download_no_exit)

    cfg = load_strategy_config("rules.json")
    store = StateStore(db_path=str(tmp_path / "backtest_scratch.db")).open()
    feed = SimulatedBarFeed(["US.TEST"], start=_DAY, end=_DAY, cache_dir=str(tmp_path / "cache"))
    harness = BacktestHarness(cfg=cfg, feed=feed, store=store)

    harness.setup_day(_DAY, ["US.TEST"])
    asyncio.run(harness.run())

    assert all(
        p.phase == PositionPhase.CLOSED for p in harness.position_manager._positions.values()
    ), "CR-05: every position must reach CLOSED by end of run(), including one still open at EOD."

    forced = [t for t in harness.trade_log if t["exit_reason"] == "force_close"]
    assert len(forced) == 1, "the EOD-open position must be captured into trade_log as a force_close."
    assert forced[0]["exit_price"] == 103.20, (
        "force-close exit_price must be the last observed bar's close (BT-02/CR-05), "
        "never a fabricated or None price."
    )


# ============================================================
# Multi-day (2 NYSE trading days) regression: CR-01/CR-04/CR-05/CR-06 (Task 3)
# ============================================================
#
# Two symbols isolate the two distinct regressions under test on the SAME days:
#   US.TESTA -- fires and fills mid-day1 (2026-06-01), stays open the rest of the day,
#               and is force-closed at day1's EOD (proves CR-01 + CR-05).
#   US.TESTB -- fires ONLY on day1's own LAST bar (no further TESTB bars that day) but
#               DOES have a later bar on day2 -- proving next_bar() must never treat
#               that day2 bar as day1's "N+1" fill (CR-04), through the full harness.
# _MD_DAY1/_MD_DAY2 are already defined at module top (== _DAY / the day after it).

# Day1: warm-up + breakout signal (closes at its own high, big volume spike) that must
# be gated by DAY 1's OWN premarket high (98.0) -- NOT day 2's much higher one (130.0);
# if CR-01's clobber bug were reintroduced, day1's Gate 1 would see 130.0 and 105.00
# would fail to break it, producing zero entries. No bar after 10:25 -- the filled
# position simply stays open until force_close_all reaches it at day1's EOD.
_TESTA_BARS = [
    (f"{_MD_DAY1} 10:05:00", 100.00, 100.50, 99.50, 100.20, 1_000),
    (f"{_MD_DAY1} 10:10:00", 100.20, 100.80, 100.00, 100.60, 1_000),
    (f"{_MD_DAY1} 10:15:00", 100.60, 101.00, 100.30, 100.90, 1_000),
    (f"{_MD_DAY1} 10:20:00", 100.90, 105.00, 100.80, 105.00, 40_000),
    (f"{_MD_DAY1} 10:25:00", 103.00, 103.50, 101.00, 103.20, 5_000),
    # Quiet day2 bar: below day2's own (higher) premarket high, so it never re-enters --
    # only present so the feed has non-empty day2 coverage for this code too.
    (f"{_MD_DAY2} 10:05:00", 100.00, 100.20, 99.80, 100.00, 1_000),
]

# Day1: TESTB's ONLY bar this day IS its signal bar (closes at its own high, volume
# spike) -- there is no later TESTB bar on day1, so the entry can only ever fill on a
# STRICTLY-later bar; a day2 bar deliberately exists so a buggy next_bar() that
# ignores the session boundary would wrongly return it as the "N+1" fill.
_TESTB_BARS = [
    (f"{_MD_DAY1} 10:20:00", 100.90, 108.00, 100.80, 108.00, 40_000),
    (f"{_MD_DAY2} 10:05:00", 100.00, 100.00, 99.00, 99.50, 1_000),
]


def _make_premarket_frame_for(day1_high, day2_high=None):
    """Premarket 5m frame with a distinct, engineered high per day -- day2's high is
    deliberately HIGHER than day1's so a cross-day clobber (CR-01) would be observable
    (a correctly-gated day1 entry would fail if it saw day2's higher number instead)."""
    idx = [f"{_MD_DAY1} 09:00:00"]
    highs = [day1_high]
    if day2_high is not None:
        idx.append(f"{_MD_DAY2} 09:00:00")
        highs.append(day2_high)
    index = pd.to_datetime(idx).tz_localize("America/New_York")
    return pd.DataFrame(
        {"Open": [h - 1.0 for h in highs], "High": highs, "Low": [h - 2.0 for h in highs],
         "Close": [h - 0.5 for h in highs], "Volume": [2_000] * len(highs)},
        index=index,
    )


def _mock_yf_download_multiday(*_args, **kwargs):
    """Multi-symbol yfinance.download stub: returns a {symbol: frame} dict (never a bare
    DataFrame) since _to_data_dict only accepts a bare flat-column frame for a SINGLE
    requested ticker -- this feed requests US.TESTA + US.TESTB together."""
    tickers = kwargs.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    interval = kwargs.get("interval")
    prepost = kwargs.get("prepost", False)

    if interval == "1d":
        return {t: _make_daily_frame() for t in tickers}
    if interval == "5m" and prepost:
        result = {}
        if "TESTA" in tickers:
            result["TESTA"] = _make_premarket_frame_for(98.0, 130.0)
        if "TESTB" in tickers:
            result["TESTB"] = _make_premarket_frame_for(98.0)
        return result
    if interval == "5m":
        result = {}
        if "TESTA" in tickers:
            result["TESTA"] = _make_5m_frame(_TESTA_BARS)
        if "TESTB" in tickers:
            result["TESTB"] = _make_5m_frame(_TESTB_BARS)
        return result
    return {}


def _fake_evaluate_symbol_always_candidate(symbol, data, cfg, scan_date, today_price):
    """setup_day's daily-filter reuse is already proven by the single-symbol tests above
    (test_setup_day_reuses_scanner_point_in_time_functions); this multi-day test's job is
    the replay-time regressions (CR-01/CR-04/CR-05), so both symbols unconditionally land
    in the watchlist for every day, regardless of the (irrelevant, dummy) daily frame."""
    return {
        "code": f"US.{symbol}", "gap_pct": 5.0, "prior_day_high": 90.0,
        "prior_close": 88.0, "sma200": 80.0, "rvol_baseline": 10_000.0,
    }


def test_multiday_replay_proves_cr01_cr04_cr05_cr06(monkeypatch, tmp_path):
    """Drives a real 2-trading-day replay through BacktestHarness (only yfinance's I/O
    seam patched) and asserts all four verification-required behaviours:

    (a) CR-01: day1's Gate 1 used day1's OWN premarket high (98.0), not day2's much
        higher one (130.0) -- proven by TESTA's day1 entry actually occurring.
    (b) CR-04: TESTB's signal on day1's own last bar never fills on day2's bar, even
        though a later TESTB bar exists on day2 (through the full harness, not just
        feed.next_bar in isolation).
    (c) CR-05: zero non-CLOSED positions remain after run(), and TESTA's EOD-open
        position is captured into trade_log with exit_reason=='force_close' and a
        real (non-fabricated) exit price.
    (d) CR-06 (incidental): the watchlist cap/gate from Task 1 does not interfere --
        both symbols are legitimately in the day's watchlist here.
    """
    from bot.config.loader import load_strategy_config
    from bot.state.store import StateStore

    monkeypatch.setattr("yfinance.download", _mock_yf_download_multiday)
    monkeypatch.setattr("backtester.harness._evaluate_symbol", _fake_evaluate_symbol_always_candidate)

    cfg = load_strategy_config("rules.json")
    store = StateStore(db_path=str(tmp_path / "backtest_scratch.db")).open()
    symbols = ["US.TESTA", "US.TESTB"]
    feed = SimulatedBarFeed(symbols, start=_MD_DAY1, end=_MD_DAY2, cache_dir=str(tmp_path / "cache"))
    harness = BacktestHarness(cfg=cfg, feed=feed, store=store)

    for day in (_MD_DAY1, _MD_DAY2):
        harness.setup_day(day, symbols)

    # CR-01 sanity: the two days' frozen highs really do differ for TESTA -- otherwise
    # a clobber would be undetectable.
    assert (
        harness._premarket_highs_by_day[_MD_DAY1]["US.TESTA"]
        != harness._premarket_highs_by_day[_MD_DAY2]["US.TESTA"]
    )

    asyncio.run(harness.run())

    # (a) CR-01: TESTA's day-1 entry actually fired (gated by day1's OWN 98.0 high, not
    # day2's 130.0 -- had the clobber bug been present, close=105.00 < 130.0 would have
    # failed Gate 1 and no entry would exist at all).
    testa_trades = [t for t in harness.trade_log if t["code"] == "US.TESTA"]
    assert len(testa_trades) == 1, "TESTA must have entered on day1, gated by day1's own premarket high."
    assert testa_trades[0]["entry_price"] == 103.00

    # (b) CR-04: TESTB's day1-last-bar signal must never fill on day2's bar.
    # sim_execution.fills mixes FillEvent objects (entries) with plain exit-fill dicts
    # (manage_exit) -- normalise both shapes' "code" before filtering.
    def _fill_code(f):
        return f.code if hasattr(f, "code") else f.get("code")

    testb_fills = [f for f in harness.sim_execution.fills if _fill_code(f) == "US.TESTB"]
    assert testb_fills == [], (
        "a signal on day1's last bar must not fill on day2's open/bar -- next_bar() must "
        "never cross the session boundary (CR-04), even though a later TESTB bar exists "
        "on day2."
    )
    assert "US.TESTB" not in {
        p.code for p in harness.position_manager._positions.values()
    }, "an abandoned (never-filled) intent must never register a PositionState."

    # (c) CR-05: zero non-CLOSED positions after run(), and TESTA's EOD-open position
    # was captured with a real force-close exit price.
    assert all(
        p.phase == PositionPhase.CLOSED for p in harness.position_manager._positions.values()
    ), "every position must reach CLOSED by end of run(), across the full multi-day replay."
    assert testa_trades[0]["exit_reason"] == "force_close"
    assert testa_trades[0]["exit_price"] == 103.20  # last observed TESTA bar's close


# ============================================================
# Gate-7 circuit-breaker regression (Task 3, Plan 06-11 gap-closure): a
# backtest-recorded trade must trip SignalEngine's -2R daily circuit breaker
# and block a LATER, otherwise gate-passing, same-day entry.
# ============================================================
#
# rules.json: max_risk_per_trade_pct=1.0, sizing_equity_usd=100000,
# daily_circuit_breaker_r=2.0 -> 1R = $1000, trip threshold = -$2000.
#
# Anchored to a RUNTIME-DERIVED recent NYSE trading day (never a hardcoded 2026-...
# literal) so this fixture never joins the 06-12 fixture time-bomb (feed.py's window
# guard requires start >= now - ~60 calendar days) -- same anchoring approach as
# Plan 06-10 Task 2.
_CB_DAY = mcal.get_calendar("NYSE").valid_days(
    start_date=(datetime.now() - timedelta(days=15)).date(),
    end_date=(datetime.now() - timedelta(days=2)).date(),
)[-1].strftime("%Y-%m-%d")

# US.LOSER: same signal/fill shape as _TODAY_BARS (breakout at 10:20, N+1-open fill
# at 10:25's open=103.00), but crashes MUCH harder on 10:30 (close=60.00, deep below
# the 1%-below-LOD initial stop ~98.505) so the STOP_OUT exit -- filled at 10:35's
# N+1 open (61.00) -- realizes roughly (61.00-103.00)*95 ~= -$3990, comfortably past
# the -$2000 (-2R) circuit-breaker threshold.
_CB_LOSER_BARS = [
    (f"{_CB_DAY} 10:05:00", 100.00, 100.50, 99.50, 100.20, 1_000),
    (f"{_CB_DAY} 10:10:00", 100.20, 100.80, 100.00, 100.60, 1_000),
    (f"{_CB_DAY} 10:15:00", 100.60, 101.00, 100.30, 100.90, 1_000),
    (f"{_CB_DAY} 10:20:00", 100.90, 105.00, 100.80, 105.00, 40_000),
    (f"{_CB_DAY} 10:25:00", 103.00, 103.50, 101.00, 101.50, 5_000),
    (f"{_CB_DAY} 10:30:00", 101.50, 101.50, 60.00, 60.00, 5_000),
    (f"{_CB_DAY} 10:35:00", 61.00, 65.00, 55.00, 60.00, 5_000),
]

# US.WINNER: a clean, gate-passing breakout on its OWN first bar of the day (10:35 --
# strictly after LOSER's 10:30 stop-out is recorded into the trades table at the end
# of that bar's processing), closing at its own high with a volume spike -- would
# enter absent the breaker (I1/I2/I3 all satisfied the same way the single-symbol
# fixture above proves it: close at own high for I2, volume spike vs the 14
# quiet-prior-day baseline for I3, close comfortably above its own premarket high for
# I1). Bar order between LOSER's and WINNER's own 10:35 bars is irrelevant here --
# the breaker already tripped at the END of LOSER's 10:30 bar, strictly before any
# 10:35-timestamped bar of either symbol is processed.
_CB_WINNER_BARS = [
    (f"{_CB_DAY} 10:35:00", 118.00, 120.00, 117.50, 120.00, 50_000),
]


def _make_cb_5m_frame(today_bars):
    """Local variant of the shared _make_5m_frame helper, anchored to the runtime-derived
    _CB_DAY instead of that helper's hardcoded "2026-06-01" -- the prior-14-business-day
    baseline window must stay STRICTLY prior to _CB_DAY regardless of which real calendar
    day _CB_DAY resolves to at test-run time."""
    rows = []
    index = []
    for time_key, o, h, l, c, v in today_bars:
        index.append(pd.Timestamp(time_key))
        rows.append((o, h, l, c, v))

    prior_dates = pd.bdate_range(end=pd.Timestamp(_CB_DAY) - pd.Timedelta(days=1), periods=14)
    for d in prior_dates:
        for bucket in _SIGNAL_BUCKETS:
            index.append(pd.Timestamp(f"{d.date()} {bucket}:00"))
            rows.append((100.0, 100.2, 99.8, 100.0, 1_000))

    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(index))
    df = df.sort_index()
    df.index = df.index.tz_localize("America/New_York")
    return df


def _make_cb_premarket_frame(high):
    """Local variant of _make_premarket_frame_for, anchored to _CB_DAY (that helper
    hardcodes "2026-06-01"/"2026-06-02")."""
    index = pd.to_datetime([f"{_CB_DAY} 09:00:00"]).tz_localize("America/New_York")
    return pd.DataFrame(
        {"Open": [high - 1.0], "High": [high], "Low": [high - 2.0],
         "Close": [high - 0.5], "Volume": [2_000]},
        index=index,
    )


def _mock_yf_download_gate7(*_args, **kwargs):
    """Two-symbol single-day yfinance stub (mirrors _mock_yf_download_multiday's
    {symbol: frame} dict shape -- required for a multi-ticker request). The daily
    (interval="1d") frame's actual dates are irrelevant here: _evaluate_symbol is
    patched with the always-candidate fake below, so nothing reads _make_daily_frame's
    content -- only its non-emptiness matters (same reasoning as the existing
    multiday test)."""
    tickers = kwargs.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    interval = kwargs.get("interval")
    prepost = kwargs.get("prepost", False)

    if interval == "1d":
        return {t: _make_daily_frame() for t in tickers}
    if interval == "5m" and prepost:
        result = {}
        if "LOSER" in tickers:
            result["LOSER"] = _make_cb_premarket_frame(98.0)
        if "WINNER" in tickers:
            result["WINNER"] = _make_cb_premarket_frame(108.0)
        return result
    if interval == "5m":
        result = {}
        if "LOSER" in tickers:
            result["LOSER"] = _make_cb_5m_frame(_CB_LOSER_BARS)
        if "WINNER" in tickers:
            result["WINNER"] = _make_cb_5m_frame(_CB_WINNER_BARS)
        return result
    return {}


def test_gate7_circuit_breaker_trips_from_backtest_recorded_trades(monkeypatch, tmp_path):
    """BLOCKER Gap 2 (BT-01 exact-same-FSM parity): the -2R daily circuit breaker must
    trip from the backtest's OWN recorded trades (Task 2's harness._store.record_trade
    call), blocking a later, otherwise gate-passing entry the same day -- exactly as a
    live losing day would. Reverting Task 2's record_trade call makes this test fail:
    get_daily_trade_stats would always return realized_pnl=0.0 and the breaker could
    never trip, so WINNER would (wrongly) enter."""
    from bot.config.loader import load_strategy_config
    from bot.state.store import StateStore

    monkeypatch.setattr("yfinance.download", _mock_yf_download_gate7)
    monkeypatch.setattr("backtester.harness._evaluate_symbol", _fake_evaluate_symbol_always_candidate)

    cfg = load_strategy_config("rules.json")
    store = StateStore(db_path=str(tmp_path / "backtest_scratch.db")).open()
    symbols = ["US.LOSER", "US.WINNER"]
    feed = SimulatedBarFeed(symbols, start=_CB_DAY, end=_CB_DAY, cache_dir=str(tmp_path / "cache"))
    harness = BacktestHarness(cfg=cfg, feed=feed, store=store)

    harness.setup_day(_CB_DAY, symbols)
    asyncio.run(harness.run())

    # (1) Only LOSER ever registered a PositionState -- WINNER's later, gate-passing
    # breakout was blocked by the tripped breaker (exactly one entry, not two).
    codes_with_positions = {p.code for p in harness.position_manager._positions.values()}
    assert codes_with_positions == {"US.LOSER"}, (
        "WINNER's later breakout must be blocked by Gate 7 once LOSER's recorded "
        "loss trips the -2R breaker -- only LOSER should ever register a position."
    )

    # (2) The breaker actually persisted a trip for this session's ET date.
    assert store.get_circuit_breaker_date() == _CB_DAY, (
        "store.get_circuit_breaker_date() must equal the session ET date once the "
        "breaker trips."
    )

    # (3) The trades table (not just the in-memory trade_log) holds LOSER's loss --
    # this is what Gate 7 actually reads. Proves the WRITE, not merely the append.
    stats = store.get_daily_trade_stats(_CB_DAY)
    assert stats["realized_pnl"] <= -2000, (
        f"store.get_daily_trade_stats must reflect LOSER's recorded loss "
        f"(<=-2000, got {stats['realized_pnl']})."
    )

    # Log and DB agree: the harness's own trade_log also captured LOSER's trade.
    loser_trades = [t for t in harness.trade_log if t["code"] == "US.LOSER"]
    assert len(loser_trades) == 1
    assert loser_trades[0]["exit_reason"] == "stop_out"
