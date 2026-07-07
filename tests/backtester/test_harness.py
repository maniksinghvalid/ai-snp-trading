#!/usr/bin/env python3
"""
tests.backtester.test_harness — BT-01: BacktestHarness real-construction wiring + replay.

Wave 0 stub (plan 06-01): `pytest.importorskip` guards this whole module until
backtester/harness.py exists (plan 06-05, depends on backtester/feed.py from 06-02 and
backtester/execution.py from 06-03) — the suite SKIPs cleanly until then, then these real
assertions run RED -> GREEN.

Mirrors tests/test_main_wiring.py's pattern (RISK-TICK-STOP regression, v1.0-MILESTONE-AUDIT
Gap 1): drive the REAL construction sequence with only I/O seams patched (here: yfinance,
the feed's data source), capture the real PositionManager instance, and assert on it directly
-- do NOT hand-build a parallel test-only pipeline (that pattern is exactly what let the
Phase 7 gateway-wiring bug ship).

Asserts:
  - position_manager built by BacktestHarness has gateway=None (Anti-Pattern: no real/mocked
    MoomooGateway in a bar-only replay -- arm_stop_protection() must no-op)
  - a full replay of the ahead-only fixture dataset produces at least one captured trade
"""
import asyncio

import pandas as pd
import pytest

from tests.backtester.fixtures import make_ahead_only_5m_dataset

mod = pytest.importorskip("backtester.harness")
feed_mod = pytest.importorskip("backtester.feed")

BacktestHarness = mod.BacktestHarness
SimulatedBarFeed = feed_mod.SimulatedBarFeed


def _make_multi_bar_frame():
    """Title-Case OHLCV frame mimicking a raw yf.download() 5m result, mirrors the
    ahead-only fixture's bar sequence so the harness's own signal path can fire."""
    bars = make_ahead_only_5m_dataset()
    index = pd.to_datetime([b["time_key"] for b in bars]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [b["open"] for b in bars],
        "High": [b["high"] for b in bars],
        "Low": [b["low"] for b in bars],
        "Close": [b["close"] for b in bars],
        "Volume": [b["volume"] for b in bars],
    }, index=index)


def _build_real_harness(monkeypatch, tmp_path):
    """Construct the REAL BacktestHarness with only the feed's yfinance seam patched."""
    from bot.config.loader import load_strategy_config
    from bot.state.store import StateStore

    monkeypatch.setattr("yfinance.download", lambda *a, **k: _make_multi_bar_frame())

    cfg = load_strategy_config("rules.json")
    store = StateStore(db_path=str(tmp_path / "backtest_scratch.db")).open()
    feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                             cache_dir=str(tmp_path / "cache"))

    harness = BacktestHarness(cfg=cfg, feed=feed, store=store)
    return harness


def test_harness_builds_position_manager_with_gateway_none(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    assert harness.position_manager._gateway is None, (
        "BacktestHarness must pass gateway=None into PositionManager -- a bar-only replay "
        "has no tick-level stop mechanism, so arm_stop_protection() must correctly no-op "
        "(06-PATTERNS Anti-Patterns; matches production's use_broker_stop_orders=false path)."
    )


def test_full_replay_of_ahead_only_fixture_produces_at_least_one_trade(monkeypatch, tmp_path):
    harness = _build_real_harness(monkeypatch, tmp_path)

    harness.setup_day("2026-06-01", ["US.TEST"])
    asyncio.run(harness.run())

    assert len(harness.trade_log) >= 1, (
        "a full replay of the ahead-only dataset must capture at least one closed trade "
        "(the N+1-open fill on bar N's breakout signal)"
    )
