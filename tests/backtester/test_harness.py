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

import pandas as pd
import pytest

mod = pytest.importorskip("backtester.harness")
feed_mod = pytest.importorskip("backtester.feed")

BacktestHarness = mod.BacktestHarness
SimulatedBarFeed = feed_mod.SimulatedBarFeed

_DAY = "2026-06-01"
_SIGNAL_BUCKETS = ["10:05", "10:10", "10:15", "10:20", "10:25", "10:30", "10:35"]

# (time_key, open, high, low, close, volume)
_TODAY_BARS = [
    ("2026-06-01 10:05:00", 100.00, 100.50, 99.50, 100.20, 1_000),
    ("2026-06-01 10:10:00", 100.20, 100.80, 100.00, 100.60, 1_000),
    ("2026-06-01 10:15:00", 100.60, 101.00, 100.30, 100.90, 1_000),
    # Signal bar: closes AT its own high (no upper wick) so I2 (close >= hod, where hod
    # INCLUDES this bar's own high) is satisfiable; volume spike drives I3 (rvol >= 2.0).
    ("2026-06-01 10:20:00", 100.90, 105.00, 100.80, 105.00, 40_000),
    # N+1 bar: gap DOWN from the signal bar's close (105.00) -- the entry must fill here
    # (open=103.00), never at the signal bar's close (BT-02 look-ahead proof).
    ("2026-06-01 10:25:00", 103.00, 103.50, 101.00, 101.50, 5_000),
    # Crash bar: closes well below the initial stop (LOD(99.50) * 0.99 = 98.505) -- triggers
    # STOP_OUT so the position actually reaches CLOSED (captured into trade_log).
    ("2026-06-01 10:30:00", 101.50, 101.50, 90.00, 90.00, 5_000),
    # Exit fill bar (N+1 relative to the crash bar).
    ("2026-06-01 10:35:00", 91.00, 91.50, 89.00, 90.50, 5_000),
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
    dates = pd.bdate_range(end=pd.Timestamp("2026-05-29"), periods=periods)
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
        ["2026-06-01 09:00:00", "2026-06-01 09:15:00"]
    ).tz_localize("America/New_York")
    return pd.DataFrame(
        {"Open": [97.0, 97.5], "High": [98.0, 97.8], "Low": [96.5, 97.0],
         "Close": [97.8, 97.5], "Volume": [2_000, 2_000]},
        index=index,
    )


def _make_5m_frame():
    """Combined 5m frame: 14 prior business days (flat, low volume, SAME bucket times as
    today) + today's dedicated signal/fill/stop-out/exit sequence. Serves BOTH the feed's
    own replay load and the harness's TOD-baseline call (both request interval="5m",
    prepost=False) -- the harness itself is responsible for excluding `day` before
    computing baselines (BT-02 no-look-ahead; see backtester.harness._prior_sessions_only)."""
    rows = []
    index = []
    for time_key, o, h, l, c, v in _TODAY_BARS:
        index.append(pd.Timestamp(time_key))
        rows.append((o, h, l, c, v))

    prior_dates = pd.bdate_range(end=pd.Timestamp("2026-06-01") - pd.Timedelta(days=1), periods=14)
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
        se_mod.now_et = lambda: harness._parse_time_key_et("2026-06-01 10:20:00")  # inside window
        assert harness.signal_engine._in_entry_window() is True

        se_mod.now_et = lambda: harness._parse_time_key_et("2026-06-01 09:00:00")  # before earliest
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

    # Pitfall 6: bar_buffer must have been populated during the replay so the swing-low
    # trail (POS-03) has data to compute from.
    assert len(harness._bar_buffer["US.TEST"]) > 0
