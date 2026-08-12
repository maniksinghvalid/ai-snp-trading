#!/usr/bin/env python3
"""
tests/signal/test_signal_engine.py — Tests for bot/signal/signal_engine.py

Verifies (no broker — gateway is mocked):
  - SIG-03: signal only when all 3 intraday filters + time gate pass
  - SIG-04: no signal when concurrent positions >= max_concurrent_positions
  - RISK-05: daily cap blocks new entries after max_trades_per_day reached
  - D-01: premarket high frozen at session-init; codes without it get no signal
  - D-03: codes with zero/missing premarket high are excluded
  - D-08/D-09: pending-tally + filled-count gate enforced
  - D-10: re-entry blocked by pending intent even when broker-flat
  - D-11: blocked signals consume no pending tally
"""

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from bot.config.loader import StrategyConfig
from bot.signal.events import BarEvent, SignalEvent
from bot.signal.signal_engine import SignalEngine
from bot.state.store import StateStore


# ============================================================
# Helpers / Fixtures
# ============================================================

# Phase 4 (04-01) added 10 required execution fields to StrategyConfig.
# These signal tests don't exercise execution behavior, so make_cfg spreads
# these canonical defaults (matching rules.json) to satisfy the constructor.
_EXECUTION_DEFAULTS = {
    "entry_limit_buffer_usd": 0.05,
    "entry_ttl_seconds": 20,
    "entry_max_retries": 2,
    "entry_poll_interval_seconds": 5,
    "exit_limit_buffer_usd": 0.05,
    "exit_ttl_seconds": 15,
    "exit_escalation_step_usd": 0.10,
    "exit_escalation_cadence_seconds": 10,
    "force_close_escalation_step_usd": 0.20,
    "force_close_escalation_cadence_seconds": 15,
}

# ============================================================
# Service defaults (Phase 5 — canonical rules.json service block)
# ============================================================
#
# StrategyConfig gained 15 required service fields in Phase 5 (05-00).
# These tests don't exercise service/scheduler behavior, so every
# StrategyConfig(...) call spreads these canonical defaults to satisfy the
# constructor. Values match the service block in rules.json exactly.
_SERVICE_DEFAULTS = {
    "premarket_scan_et": "08:30",
    "market_open_et": "09:30",
    "intraday_rescan_interval_min": 30,
    "intraday_rescan_start_et": "09:55",
    "intraday_rescan_end_et": "12:55",
    "eod_report_et": "15:55",
    "watchdog_poll_interval_s": 60,
    "watchdog_reconnect_initial_s": 5,
    "watchdog_reconnect_cap_s": 60,
    "alerts_enabled": True,
    "misfire_grace_scan_s": 3600,
    "misfire_grace_rescan_s": 600,
    "force_close_misfire_grace_s": 300,
    "launchd_throttle_interval_s": 30,
    "crash_loop_alert_threshold": 5,
}


def make_cfg(**overrides) -> StrategyConfig:
    """Build a minimal StrategyConfig for tests. All params config-driven."""
    defaults = dict(
        min_price_usd=10.0,
        d3_min_gap_pct=1.0,
        rvol_min=2.0,
        rvol_lookback_days=14,
        earliest_entry_et="10:05",
        latest_entry_et="15:30",
        force_close_et="15:45",
        initial_stop_pct=1.0,
        partial_profit_trigger_r=1.0,
        partial_profit_fraction=0.3333,
        breakeven_trigger_r=1.5,
        max_risk_per_trade_pct=1.0,
        max_position_size_pct=10,
        max_concurrent_positions=5,
        max_trades_per_day=5,
        **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def make_bar(code="US.AAPL", close=155.0, hod=154.0, lod=148.0, cum_volume=1_000_000) -> BarEvent:
    """Build a BarEvent that will satisfy I1/I2/I3 when premarket_high < close and a
    tod_baseline is seeded at bucket "10:10" (this bar's fixed time_key bucket) --
    see seed_rvol_baseline. cum_volume defaults to 1_000_000 so
    seed_rvol_baseline's default tod_baseline=400_000 gives rvol=2.5, comfortably
    >= rvol_min=2.0 (the legacy event.volume/rvol_baseline fallback this fixture
    used to rely on was removed, strategy-audit P1-A -- TOD is now the sole path)."""
    return BarEvent(
        code=code,
        time_key="2026-06-24 10:10:00",
        open=150.0,
        high=close,
        low=149.0,
        close=close,
        volume=1_000_000,
        hod=hod,
        lod=lod,
        cum_volume=cum_volume,
    )


def make_store_with_daily_count(filled_count: int, session_date: str = "2026-06-24") -> StateStore:
    """Open an in-memory StateStore seeded with a daily_trade_count row."""
    store = StateStore(db_path=":memory:")
    store.open()
    if filled_count > 0:
        store.conn.execute(
            "INSERT INTO daily_trade_count (session_date, filled_count, updated_at) VALUES (?, ?, ?)",
            (session_date, filled_count, datetime.utcnow().isoformat()),
        )
        store.conn.commit()
    return store


def seed_rvol_baseline(store: StateStore, scan_date: str, code: str = "US.AAPL",
                       time_bucket: str = "10:10", tod_baseline: float = 400_000.0,
                       rvol_baseline: float = 500_000.0, gap_pct: float = 2.0,
                       rank: int = 1) -> None:
    """Seed the daily_scan row (rvol_baseline: scanner metadata, still written) AND
    the TOD baseline SignalEngine actually reads for I3 -- the sole RVOL path since
    the legacy event.volume/rvol_baseline fallback was removed (strategy-audit
    P1-A). Most callers just need Gate 2 out of the way to exercise some OTHER
    gate; the defaults (tod_baseline=400_000 against make_bar's cum_volume=1M)
    pass I3 with margin. Pass a higher tod_baseline to deliberately fail I3."""
    store.conn.execute(
        """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (scan_date, code, gap_pct, rank, f"{scan_date}T09:30:00", rvol_baseline),
    )
    store.conn.commit()
    store.upsert_tod_baselines(scan_date, code, {time_bucket: tod_baseline})


def make_engine(
    cfg: StrategyConfig = None,
    gateway=None,
    store: StateStore = None,
    premarket_highs: dict = None,
) -> SignalEngine:
    """Build a SignalEngine with mocked gateway/store and optional premarket highs."""
    cfg = cfg or make_cfg()
    gateway = gateway or MagicMock()
    if store is None:
        store = StateStore(db_path=":memory:")
        store.open()
    engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)
    if premarket_highs is not None:
        engine.set_premarket_highs(premarket_highs)
    return engine


def run(coro):
    """Synchronous helper to run a coroutine in tests."""
    return asyncio.run(coro)


# ============================================================
# SIG-03: gate combinations
# ============================================================

class TestSignalEngineGates:
    """All three intraday filters + time gate must pass for a signal to emit."""

    def test_all_gates_required(self):
        """
        Signal emits only when all 3 filters (I1: above premarket high,
        I2: above HOD, I3: RVOL >= 2.0) AND the entry time window pass.
        Failing any single gate must produce no SignalEvent.
        """
        cfg = make_cfg(rvol_min=2.0, earliest_entry_et="10:05", latest_entry_et="15:30")
        premarket_high = 150.0
        hod = 154.0  # I2: close must be >= hod

        # base bar that passes all: close=155 > premarket_high=150, close=155 >= hod=154, rvol=2.5
        base_bar = make_bar(close=155.0, hod=hod)
        # rvol comes from the tod_baselines table — mock it via store
        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = "2026-06-24"
        seed_rvol_baseline(store, scan_date, "US.AAPL")

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        # Patch now_et to be in-window
        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs={"US.AAPL": premarket_high})

            # ALL gates pass: should emit a signal
            result = run(engine.on_bar(base_bar))
            assert result is not None, "Expected SignalEvent when all gates pass"
            assert isinstance(result, SignalEvent)

            # Fail I1: close below premarket_high
            bar_fail_i1 = make_bar(close=149.0, hod=hod)  # 149 < 150 (premarket_high)
            engine2 = make_engine(cfg=cfg, gateway=gateway, store=store,
                                  premarket_highs={"US.AAPL": premarket_high})
            result_i1 = run(engine2.on_bar(bar_fail_i1))
            assert result_i1 is None, "Expected no signal when I1 fails (close < premarket_high)"

            # Fail I2: close below HOD
            bar_fail_i2 = make_bar(close=155.0, hod=160.0)  # 155 < 160 (hod)
            engine3 = make_engine(cfg=cfg, gateway=gateway, store=store,
                                  premarket_highs={"US.AAPL": premarket_high})
            result_i2 = run(engine3.on_bar(bar_fail_i2))
            assert result_i2 is None, "Expected no signal when I2 fails (close < hod)"

            # Fail I3: rvol below min
            store2 = StateStore(db_path=":memory:")
            store2.open()
            # tod_baseline high enough that cum_volume=1_000_000 / tod_baseline < 2.0
            # 1_000_000 / 600_000 = 1.67 < rvol_min=2.0
            seed_rvol_baseline(store2, scan_date, "US.AAPL", tod_baseline=600_000.0)
            engine4 = make_engine(cfg=cfg, gateway=gateway, store=store2,
                                  premarket_highs={"US.AAPL": premarket_high})
            result_i3 = run(engine4.on_bar(base_bar))
            assert result_i3 is None, "Expected no signal when I3 fails (rvol < rvol_min)"

    def test_missing_premarket_high_no_signal(self):
        """
        A code whose premarket high is missing or zero emits no signal for
        the session (D-03: never fall back to prior-day high).
        """
        cfg = make_cfg()
        store = StateStore(db_path=":memory:")
        store.open()

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        bar = make_bar(code="US.TSLA", close=200.0, hod=199.0)

        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            # No premarket high set for US.TSLA
            engine = make_engine(cfg=cfg, gateway=gateway, store=store, premarket_highs={})
            result = run(engine.on_bar(bar))
            assert result is None, "Expected no signal when premarket_high is missing (D-03)"

            # Premarket high explicitly zero
            engine2 = make_engine(cfg=cfg, gateway=gateway, store=store,
                                  premarket_highs={"US.TSLA": 0.0})
            result2 = run(engine2.on_bar(bar))
            assert result2 is None, "Expected no signal when premarket_high is 0.0 (D-03)"

    def test_config_driven_window(self):
        """
        Entry window boundaries are read from cfg.earliest_entry_et and
        cfg.latest_entry_et — no hardcoded time literals in SignalEngine.
        Behavioral CFG-01 proof: swapping the config changes which times pass.
        """
        # Config with a narrow custom window: 11:00–12:00
        cfg_narrow = make_cfg(earliest_entry_et="11:00", latest_entry_et="12:00",
                              max_concurrent_positions=5, max_trades_per_day=5)

        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = "2026-06-24"
        seed_rvol_baseline(store, scan_date, "US.AAPL")

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        # 10:30 should be OUT for this narrow window (< 11:00)
        time_1030 = datetime(2026, 6, 24, 10, 30, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=time_1030):
            engine = make_engine(cfg=cfg_narrow, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            result = run(engine.on_bar(bar))
            assert result is None, "10:30 should be outside narrow 11:00–12:00 window"

        # 11:30 should be IN for this narrow window
        time_1130 = datetime(2026, 6, 24, 11, 30, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=time_1130):
            store2 = StateStore(db_path=":memory:")
            store2.open()
            seed_rvol_baseline(store2, scan_date, "US.AAPL")
            engine2 = make_engine(cfg=cfg_narrow, gateway=gateway, store=store2,
                                  premarket_highs=premarket_highs)
            result2 = run(engine2.on_bar(bar))
            assert result2 is not None, "11:30 should be inside narrow 11:00–12:00 window"


# ============================================================
# SIG-03: entry window boundaries
# ============================================================

class TestSignalEngineEntryWindow:
    """Entry window: 10:05 ET inclusive, 15:30 ET exclusive (Pitfall 6)."""

    @pytest.mark.parametrize("hour,minute,second,expected_in_window", [
        (10, 4, 59, False),   # 10:04:59 → out (too early)
        (10, 5, 0, True),     # 10:05:00 → in (earliest inclusive)
        (15, 29, 59, True),   # 15:29:59 → in (last valid second)
        (15, 30, 0, False),   # 15:30:00 → out (latest exclusive)
    ])
    def test_entry_window_boundaries(self, hour, minute, second, expected_in_window):
        """
        Table-driven test for boundary behaviour:
          10:04:59 ET → out (too early)
          10:05:00 ET → in (earliest inclusive boundary)
          15:29:59 ET → in (last valid second)
          15:30:00 ET → out (latest exclusive boundary)
        (RESEARCH Pitfall 6 / Canonical boundary definition)
        """
        cfg = make_cfg(earliest_entry_et="10:05", latest_entry_et="15:30",
                       max_concurrent_positions=5, max_trades_per_day=5)

        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = "2026-06-24"
        seed_rvol_baseline(store, scan_date, "US.AAPL")

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        boundary_dt = datetime(2026, 6, 24, hour, minute, second)
        with patch("bot.signal.signal_engine.now_et", return_value=boundary_dt):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            result = run(engine.on_bar(bar))

        if expected_in_window:
            assert result is not None, (
                f"{hour:02d}:{minute:02d}:{second:02d} should be IN window, got no signal"
            )
        else:
            assert result is None, (
                f"{hour:02d}:{minute:02d}:{second:02d} should be OUT of window, got signal"
            )


# ============================================================
# D-01: premarket high freeze
# ============================================================

class TestFetchPremarketHighs:
    """fetch_premarket_highs reads pre_high_price, applies D-03, and freezes."""

    def test_fetch_premarket_highs_freezes(self):
        """
        After session-init, premarket highs are frozen — subsequent on_bar() calls
        use the frozen value (not re-fetched from broker).
        fetch_premarket_highs calls gateway.get_market_snapshot(codes) once,
        reads pre_high_price, includes codes where pre_high_price > 0, and freezes.
        """
        cfg = make_cfg()
        store = StateStore(db_path=":memory:")
        store.open()

        # Build a mock gateway returning snapshot data with pre_high_price
        snap_data = pd.DataFrame({
            "code": ["US.AAPL", "US.MSFT"],
            "pre_high_price": [155.0, 300.0],
        })
        gateway = MagicMock()
        gateway.get_market_snapshot = AsyncMock(return_value=(0, snap_data))  # RET_OK = 0

        engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)
        result = run(engine.fetch_premarket_highs(["US.AAPL", "US.MSFT"]))

        # Should return a dict with both codes
        assert result == {"US.AAPL": 155.0, "US.MSFT": 300.0}

        # Should have been frozen on the engine
        assert engine._premarket_highs == {"US.AAPL": 155.0, "US.MSFT": 300.0}

        # get_market_snapshot called exactly once
        gateway.get_market_snapshot.assert_called_once_with(["US.AAPL", "US.MSFT"])

    def test_fetch_premarket_highs_excludes_missing(self):
        """
        Codes where pre_high_price is 0 or missing are excluded from the
        frozen premarket-high dict (D-03). A non-RET_OK snapshot returns
        an empty dict without raising.
        """
        cfg = make_cfg()
        store = StateStore(db_path=":memory:")
        store.open()

        # Code with 0 pre_high_price → excluded
        snap_data = pd.DataFrame({
            "code": ["US.AAPL", "US.TSLA", "US.MSFT"],
            "pre_high_price": [155.0, 0.0, None],
        })
        gateway = MagicMock()
        gateway.get_market_snapshot = AsyncMock(return_value=(0, snap_data))

        engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)
        result = run(engine.fetch_premarket_highs(["US.AAPL", "US.TSLA", "US.MSFT"]))

        # Only AAPL should be included (TSLA=0, MSFT=None excluded)
        assert "US.AAPL" in result
        assert result["US.AAPL"] == 155.0
        assert "US.TSLA" not in result, "Zero pre_high_price must be excluded (D-03)"
        assert "US.MSFT" not in result, "None/NaN pre_high_price must be excluded (D-03)"

        # Non-RET_OK → empty dict, no raise
        gateway2 = MagicMock()
        gateway2.get_market_snapshot = AsyncMock(return_value=(1, None))  # non-RET_OK
        engine2 = SignalEngine(cfg=cfg, gateway=gateway2, store=store)
        result2 = run(engine2.fetch_premarket_highs(["US.AAPL"]))
        assert result2 == {}, "Non-RET_OK snapshot must return empty dict without raising"


# ============================================================
# SIG-04 / RISK-04: concurrent cap
# ============================================================

def _insert_position_row(store: StateStore, position_id: str, code: str,
                          remaining_quantity: int, phase: str = "OPEN") -> None:
    """Insert a minimal positions row directly (bot-owned DB truth for the cap gate)."""
    store.conn.execute(
        """INSERT INTO positions
           (position_id, code, phase, entry_price, initial_stop, trail_stop,
            full_quantity, remaining_quantity, opened_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (position_id, code, phase, 100.0, 99.0, 99.0,
         100, remaining_quantity, "2026-06-24T09:30:00", "2026-06-24T09:30:00"),
    )
    store.conn.commit()


class TestSignalEngineConcurrentCap:
    """No signal when open positions >= max_concurrent_positions."""

    def test_concurrent_cap(self):
        """
        When the number of open bot-owned (DB) positions equals
        cfg.max_concurrent_positions, the signal engine emits no SignalEvent
        (SIG-04, RISK-04). Below the cap, a passing setup still emits.
        """
        cfg = make_cfg(max_concurrent_positions=3, max_trades_per_day=10)

        scan_date = "2026-06-24"

        def make_positioned_store(n_open: int):
            s = StateStore(db_path=":memory:")
            s.open()
            seed_rvol_baseline(s, scan_date, "US.AAPL")
            for i in range(n_open):
                _insert_position_row(s, f"pos-{i}", f"US.X{i}", remaining_quantity=100)
            return s

        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        # Broker snapshot is irrelevant to the cap count post-2.1 (may show
        # anything, incl. manual holdings) — the gate now counts DB rows only.
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame({"code": ["US.X1"]})))

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            # AT cap: 3 bot-owned open DB positions, max=3 → no signal
            store_cap = make_positioned_store(n_open=3)
            engine_at_cap = make_engine(cfg=cfg, gateway=gateway, store=store_cap,
                                        premarket_highs=premarket_highs)
            result_at_cap = run(engine_at_cap.on_bar(bar))
            assert result_at_cap is None, "At cap (3 DB positions, max=3), expected no signal"

            # BELOW cap: 2 bot-owned open DB positions, max=3 → signal fires
            store_below = make_positioned_store(n_open=2)
            engine_below = make_engine(cfg=cfg, gateway=gateway, store=store_below,
                                       premarket_highs=premarket_highs)
            result_below = run(engine_below.on_bar(bar))
            assert result_below is not None, "Below cap (2 DB positions, max=3), expected signal"

    def test_signal_engine_excludes_zero_qty_and_external_positions(self):
        """
        Regression (Finding 2.1): the concurrent-position cap must count only
        bot-owned (DB positions table) rows with remaining_quantity > 0 — a
        qty=0 row (fully exited but not yet marked CLOSED) and manual/external
        holdings visible only in the broker snapshot on the shared account
        must NOT count toward the cap.
        """
        cfg = make_cfg(max_concurrent_positions=2, max_trades_per_day=10)
        scan_date = "2026-06-24"

        store = StateStore(db_path=":memory:")
        store.open()
        seed_rvol_baseline(store, scan_date, "US.AAPL")

        # One qty=0 row (does not count) and one real qty>0 row (counts = 1).
        _insert_position_row(store, "pos-zero", "US.ZERO", remaining_quantity=0)
        _insert_position_row(store, "pos-real", "US.REAL", remaining_quantity=100)

        # Broker snapshot reports 3 manual/external holdings not tracked in the
        # DB positions table at all — these must never saturate the cap.
        external_df = pd.DataFrame({
            "code": ["US.EXT1", "US.EXT2", "US.EXT3"],
            "qty": [50, 50, 50],
        })
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, external_df))

        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            result = run(engine.on_bar(bar))
            assert result is not None, (
                "Only 1 bot-owned qty>0 DB position exists (below max=2) — "
                "qty=0 row and 3 external broker rows must not count toward the cap"
            )


# ============================================================
# RISK-05: daily cap
# ============================================================

class TestSignalEngineDailyCap:
    """Daily new-entry cap: filled_count + pending_count < max_trades_per_day (D-09)."""

    def _make_daily_store(self, filled_count: int, session_date="2026-06-24") -> StateStore:
        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = session_date
        seed_rvol_baseline(store, scan_date, "US.AAPL")
        if filled_count > 0:
            store.conn.execute(
                "INSERT INTO daily_trade_count (session_date, filled_count, updated_at) VALUES (?, ?, ?)",
                (session_date, filled_count, "2026-06-24T10:00:00"),
            )
            store.conn.commit()
        return store

    def test_daily_cap_blocks_after_max(self):
        """
        Once filled_count + pending_count >= max_trades_per_day, no further
        signals are emitted for the session (RISK-05, D-09).
        """
        cfg = make_cfg(max_concurrent_positions=5, max_trades_per_day=5)
        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        # Positions well below cap
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame({"code": ["US.X1"]})))

        # filled_count = 5 → already at max_trades_per_day=5
        store = self._make_daily_store(filled_count=5)

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            result = run(engine.on_bar(bar))
            assert result is None, "filled_count=5 at max=5 → no signal (RISK-05)"

    def test_daily_cap_independent_of_concurrent(self):
        """
        The daily-entry cap is evaluated independently from the concurrent cap.
        Closing a position frees a concurrent slot but does NOT decrement the
        daily cap counter (RISK-05 / D-08 distinct counters).
        """
        cfg = make_cfg(max_concurrent_positions=5, max_trades_per_day=5)
        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        # ZERO open positions (all closed) → concurrent cap is free
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        # filled_count = 5 → daily cap exhausted
        store = self._make_daily_store(filled_count=5)

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            result = run(engine.on_bar(bar))
            # Even with zero open positions, daily cap must block
            assert result is None, (
                "Daily cap must block even when concurrent slots are free "
                "(closing positions does not reset daily cap)"
            )

    def test_blocked_signal_does_not_consume_pending(self):
        """
        A signal blocked by the concurrent cap or daily cap does NOT increment
        the pending-intent tally (D-11: only emitted SignalEvents consume an entry slot).
        """
        cfg = make_cfg(max_concurrent_positions=3, max_trades_per_day=5)
        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        # Broker snapshot is irrelevant to the cap post-2.1 (gate counts DB rows).
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame({"code": ["US.X1"]})))

        store = self._make_daily_store(filled_count=0)
        # Simulate 3 bot-owned open DB positions (= cap) to block the signal
        _insert_position_row(store, "pos-x1", "US.X1", remaining_quantity=100)
        _insert_position_row(store, "pos-x2", "US.X2", remaining_quantity=100)
        _insert_position_row(store, "pos-x3", "US.X3", remaining_quantity=100)

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)
            initial_pending = engine._pending_count

            # Blocked by concurrent cap
            result = run(engine.on_bar(bar))
            assert result is None, "Should be blocked by concurrent cap"

            # Pending count must not have changed
            assert engine._pending_count == initial_pending, (
                "Blocked signal must not increment pending tally (D-11)"
            )

    def test_session_pending_tally_increments_on_emit(self):
        """
        note_intent_emitted() increments _pending_count; on_bar() does NOT.

        The D-09 burst guard tally is managed solely by RiskEngine calling
        note_intent_emitted() after successfully emitting an OrderIntent (D-11).
        on_bar() must NOT increment _pending_count — doing so double-counts
        (CR-02 fix) and would cap the bot at ~half the configured max_trades_per_day.

        Verify:
          - on_bar() emitting a SignalEvent does NOT touch _pending_count.
          - note_intent_emitted() increments by exactly 1.
          - The D-09 gate correctly uses _pending_count set via note_intent_emitted().
        """
        cfg = make_cfg(max_concurrent_positions=5, max_trades_per_day=2)
        premarket_highs = {"US.AAPL": 150.0}

        scan_date = "2026-06-24"

        def make_store_with_scan():
            s = StateStore(db_path=":memory:")
            s.open()
            seed_rvol_baseline(s, scan_date, "US.AAPL")
            return s

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        store = make_store_with_scan()

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                                 premarket_highs=premarket_highs)

            # filled_count=0, pending=0: max_trades_per_day=2 → first bar should emit
            bar1 = make_bar(code="US.AAPL", close=155.0, hod=154.0)
            result1 = run(engine.on_bar(bar1))
            assert result1 is not None, "First signal should emit (0+0 < 2)"
            # on_bar() must NOT increment _pending_count (CR-02 fix)
            assert engine._pending_count == 0, (
                "on_bar() must NOT increment _pending_count — only "
                "RiskEngine.note_intent_emitted() advances the tally (CR-02)"
            )
            # Simulate RiskEngine calling note_intent_emitted() for the emitted intent
            engine.note_intent_emitted()
            assert engine._pending_count == 1, "note_intent_emitted() must increment by 1"

            # With pending=1, total=1 < 2: a new engine should still emit
            engine2 = SignalEngine(cfg=cfg, gateway=gateway, store=make_store_with_scan())
            engine2.set_premarket_highs(premarket_highs)
            engine2._pending_count = 1  # Simulate one prior intent via note_intent_emitted

            result2 = run(engine2.on_bar(bar1))
            assert result2 is not None, "Signal can emit when pending=1 < max=2"
            # on_bar still must not increment
            assert engine2._pending_count == 1, "on_bar() still must not touch _pending_count"
            # Simulate RiskEngine incrementing
            engine2.note_intent_emitted()
            assert engine2._pending_count == 2

            # Now pending=2, max=2 → daily cap blocks
            engine3 = SignalEngine(cfg=cfg, gateway=gateway, store=make_store_with_scan())
            engine3.set_premarket_highs(premarket_highs)
            engine3._pending_count = 2  # Simulate 2 prior intents

            result3 = run(engine3.on_bar(bar1))
            assert result3 is None, "Signal blocked when pending_count == max_trades_per_day"
            assert engine3._pending_count == 2, "Blocked signal must not increment tally"

    def test_reentry_blocked_by_pending_intent(self):
        """
        Re-entry for a code is blocked when there is an unresolved pending
        intent for that code in the pending_intents table (D-10: must be BOTH
        broker-flat AND no pending intent for the code).
        Once the PENDING row is gone, the code may re-signal (still bounded by daily cap).
        """
        cfg = make_cfg(max_concurrent_positions=5, max_trades_per_day=5)
        bar = make_bar(code="US.AAPL", close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}
        scan_date = "2026-06-24"

        # Broker-FLAT: no positions for US.AAPL
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        def make_store_with_pending(has_pending: bool):
            s = StateStore(db_path=":memory:")
            s.open()
            seed_rvol_baseline(s, scan_date, "US.AAPL")
            if has_pending:
                s.conn.execute(
                    """INSERT INTO pending_intents
                       (intent_id, code, status, entry_price, stop_price, quantity, emitted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("intent-001", "US.AAPL", "PENDING", 154.0, 152.0, 10, "2026-06-24T10:05:00"),
                )
            s.conn.commit()
            return s

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            # With a PENDING intent for US.AAPL → blocked (D-10 second condition)
            store_with_pending = make_store_with_pending(has_pending=True)
            engine_with_pending = make_engine(cfg=cfg, gateway=gateway,
                                              store=store_with_pending,
                                              premarket_highs=premarket_highs)
            result_blocked = run(engine_with_pending.on_bar(bar))
            assert result_blocked is None, (
                "Broker-flat code with live PENDING intent must not re-signal (D-10)"
            )

            # Without a PENDING intent → allowed (both conditions satisfied)
            store_no_pending = make_store_with_pending(has_pending=False)
            engine_no_pending = make_engine(cfg=cfg, gateway=gateway,
                                            store=store_no_pending,
                                            premarket_highs=premarket_highs)
            result_allowed = run(engine_no_pending.on_bar(bar))
            assert result_allowed is not None, (
                "Broker-flat code with no PENDING intent must be allowed to signal (D-10)"
            )


# ============================================================
# Regression: premarket-high-not-seeded (D-01/D-03 wire lock)
#
# Proves that when fetch_premarket_highs() has been called at session-init
# (the seam wired in _job_market_open_subscribe), a qualifying bar drives
# the full on_bar path and emits a SignalEvent.  Locks the wire in place so
# removing the production call restores the original live failure.
# ============================================================

class TestPremarketHighSeededEndToEnd:
    """Regression: entry signal fires end-to-end when premarket highs are seeded (D-01/D-03).

    These tests document the exact seam that was broken before the fix:
      - fetch_premarket_highs() must be called before bars flow.
      - Without it, Gate 1 (signal_skipped_no_premarket_high) blocks every bar.
      - With it, all gates can pass and a SignalEvent is emitted.

    Added as part of fix for premarket-high-not-seeded debug session (2026-06-25).
    """

    def _make_seeded_store(self, session_date: str = "2026-06-24") -> StateStore:
        """Open an in-memory StateStore with a daily_scan row for US.AAPL."""
        s = StateStore(db_path=":memory:")
        s.open()
        seed_rvol_baseline(s, session_date, "US.AAPL")
        return s

    def test_entry_fires_after_fetch_premarket_highs(self):
        """Drives on_bar→signal path with premarket highs seeded via fetch_premarket_highs.

        Regression for the live bug: fetch_premarket_highs was never called in
        production so _premarket_highs stayed empty and Gate 1 blocked every bar.
        After the fix, _job_market_open_subscribe calls fetch_premarket_highs(codes).
        This test proves the seam works end-to-end using the real SignalEngine.
        """
        cfg = make_cfg(rvol_min=2.0, earliest_entry_et="10:05", latest_entry_et="15:30")

        # Mock gateway: snapshot returns valid pre_high_price for US.AAPL
        snap_data = pd.DataFrame({
            "code": ["US.AAPL"],
            "pre_high_price": [150.0],
        })
        gateway = MagicMock()
        gateway.get_market_snapshot = AsyncMock(return_value=(0, snap_data))
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        store = self._make_seeded_store()
        engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)

        # Simulate the production seam: _job_market_open_subscribe calls this.
        # _premarket_highs starts empty — fetch call populates and freezes it.
        run(engine.fetch_premarket_highs(["US.AAPL"]))

        # Confirm Gate 1 is now satisfied (premarket high is frozen)
        assert engine._premarket_highs.get("US.AAPL") == 150.0, (
            "fetch_premarket_highs must freeze pre_high_price in _premarket_highs (D-01)"
        )

        # Bar: close=155 > premarket_high=150 (I1), close=155 >= hod=154 (I2),
        # rvol = 1_000_000/500_000 = 2.0 >= rvol_min=2.0 (I3), inside entry window.
        bar = make_bar(code="US.AAPL", close=155.0, hod=154.0)

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            result = run(engine.on_bar(bar))

        assert result is not None, (
            "Expected SignalEvent after fetch_premarket_highs seeded the session. "
            "Regression: without the production seam call, Gate 1 blocks every bar "
            "with signal_skipped_no_premarket_high."
        )
        assert isinstance(result, SignalEvent)
        assert result.code == "US.AAPL"
        assert result.premarket_high == 150.0

    def test_entry_blocked_without_fetch_premarket_highs(self):
        """Gate 1 blocks every bar when fetch_premarket_highs has NOT been called.

        Regression counterpart: documents the exact failure mode that existed before
        the fix. If the production seam is ever removed, this test stays green but
        test_entry_fires_after_fetch_premarket_highs goes red — which is the right signal.
        """
        cfg = make_cfg(rvol_min=2.0, earliest_entry_et="10:05", latest_entry_et="15:30")

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        store = self._make_seeded_store()
        engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)

        # Deliberately do NOT call fetch_premarket_highs — simulates missing production wire.
        assert engine._premarket_highs == {}, "_premarket_highs must start empty"

        bar = make_bar(code="US.AAPL", close=155.0, hod=154.0)

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            result = run(engine.on_bar(bar))

        assert result is None, (
            "Expected no SignalEvent when _premarket_highs is empty (Gate 1 must block). "
            "This documents the live bug: signal_skipped_no_premarket_high blocks 100% of bars."
        )


# ============================================================
# Regression: rescan-added symbols never seeded (premarket-high-rescan-gap)
#
# Proves that after the 09:30 freeze (set_premarket_highs), intraday rescan
# codes can be merged via add_premarket_highs / fetch_and_merge_premarket_highs
# WITHOUT clobbering the original W0 highs, and that on_bar stops emitting
# signal_skipped_no_premarket_high for the rescan code after the merge.
#
# RED: add_premarket_highs does not exist before the fix (AttributeError).
# GREEN: after add_premarket_highs + fetch_and_merge_premarket_highs are added.
# ============================================================

class TestRescanPremarketHighMerge:
    """Rescan merge path: add_premarket_highs must merge, not replace (D-01 guard).

    Root cause: _job_intraday_rescan discarded run_intraday_rescan's returned
    watchlist and never seeded premarket highs for the new codes. SignalEngine had
    no merge method — set_premarket_highs replaces the dict, so calling it again
    for rescan codes would wipe the 09:30 frozen highs.

    Fix: add add_premarket_highs (merge) + fetch_and_merge_premarket_highs (fetch
    for new codes only, then merge) to SignalEngine; wire the call in
    _job_intraday_rescan after capturing the returned watchlist.

    Added 2026-07-01 for debug session premarket-high-rescan-gap.
    """

    def _make_seeded_store(self, session_date: str = "2026-06-24") -> StateStore:
        """Open an in-memory StateStore with daily_scan rows for AAPL and NVDA."""
        s = StateStore(db_path=":memory:")
        s.open()
        for code, tod in [("US.AAPL", 400_000.0), ("US.NVDA", 800_000.0)]:
            seed_rvol_baseline(s, session_date, code, tod_baseline=tod)
        return s

    def test_add_premarket_highs_merges_without_clobbering_initial_seed(self):
        """add_premarket_highs must merge new rescan codes, not replace the 09:30 dict.

        RED before add_premarket_highs is added to SignalEngine (AttributeError).
        GREEN after the fix: all W0 codes and the rescan code are present.
        """
        engine = make_engine()

        # Simulate 09:30 freeze for initial watchlist W0
        W0 = {"US.AAPL": 150.0, "US.MSFT": 300.0}
        engine.set_premarket_highs(W0)
        assert engine._premarket_highs == W0, "Precondition: W0 must be frozen"

        # Rescan discovers a new code not in W0 — merge it
        engine.add_premarket_highs({"US.NVDA": 120.5})

        # W0 codes must survive the merge
        assert "US.AAPL" in engine._premarket_highs, "W0 code US.AAPL clobbered by rescan merge"
        assert "US.MSFT" in engine._premarket_highs, "W0 code US.MSFT clobbered by rescan merge"
        assert engine._premarket_highs["US.AAPL"] == 150.0, "W0 value must be unchanged"
        # Rescan code must be present
        assert "US.NVDA" in engine._premarket_highs, "Rescan code US.NVDA not seeded after merge"
        assert engine._premarket_highs["US.NVDA"] == 120.5, "Rescan premarket high value wrong"

    def test_fetch_and_merge_premarket_highs_merges_without_clobbering(self):
        """fetch_and_merge_premarket_highs fetches for new codes only and merges.

        Verifies:
        - The 09:30 frozen highs (W0) are preserved.
        - The rescan code is seeded from the snapshot response.
        - Codes already in _premarket_highs are NOT re-fetched (gateway called once
          for only the new codes, not the full set).
        """
        cfg = make_cfg()

        # Mock gateway: snapshot returns valid pre_high_price for the rescan code only
        snap_data = pd.DataFrame({
            "code": ["US.NVDA"],
            "pre_high_price": [120.5],
        })
        gateway = MagicMock()
        gateway.get_market_snapshot = AsyncMock(return_value=(0, snap_data))

        engine = SignalEngine(cfg=cfg, gateway=gateway, store=StateStore(db_path=":memory:"))
        engine._store.open()

        # Simulate 09:30 freeze
        W0 = {"US.AAPL": 150.0, "US.MSFT": 300.0}
        engine.set_premarket_highs(W0)

        # fetch_and_merge_premarket_highs for the rescan watchlist (W0 + new code)
        # The method must skip codes already in _premarket_highs
        all_rescan_codes = ["US.AAPL", "US.MSFT", "US.NVDA"]
        result = run(engine.fetch_and_merge_premarket_highs(all_rescan_codes))

        # Only US.NVDA was fetched (W0 codes skipped)
        gateway.get_market_snapshot.assert_called_once()
        called_codes = gateway.get_market_snapshot.call_args[0][0]
        assert "US.NVDA" in called_codes, "Rescan code must be passed to snapshot"
        assert "US.AAPL" not in called_codes, "W0 codes must not be re-fetched"
        assert "US.MSFT" not in called_codes, "W0 codes must not be re-fetched"

        # W0 codes must survive
        assert engine._premarket_highs["US.AAPL"] == 150.0
        assert engine._premarket_highs["US.MSFT"] == 300.0
        # Rescan code now seeded
        assert engine._premarket_highs["US.NVDA"] == 120.5
        # Return value is only the newly-added codes
        assert result == {"US.NVDA": 120.5}

    def test_on_bar_no_longer_skips_rescan_code_after_merge(self):
        """After add_premarket_highs seeds a rescan code, on_bar must NOT emit
        signal_skipped_no_premarket_high for that code — Gate 1 must pass.
        """
        cfg = make_cfg(rvol_min=2.0, earliest_entry_et="10:05", latest_entry_et="15:30")
        session_date = "2026-06-24"
        store = self._make_seeded_store(session_date=session_date)

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)

        # 09:30 seed — only AAPL and MSFT, NOT NVDA
        engine.set_premarket_highs({"US.AAPL": 150.0, "US.MSFT": 300.0})
        assert "US.NVDA" not in engine._premarket_highs, "Precondition: NVDA not in initial set"

        # Without merge: on_bar for NVDA hits Gate 1 → signal_skipped_no_premarket_high
        bar_nvda = make_bar(code="US.NVDA", close=125.0, hod=124.0)
        in_window = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result_before = run(engine.on_bar(bar_nvda))
        assert result_before is None, (
            "Before merge: on_bar must return None for rescan code (Gate 1 blocks)"
        )

        # Rescan discovers NVDA and merges its premarket high
        engine.add_premarket_highs({"US.NVDA": 120.5})

        # After merge: NVDA bar with close > premarket_high must pass Gate 1
        # close=125.0 > premarket_high=120.5 (I1 passes)
        # close=125.0 >= hod=124.0 (I2 passes)
        # rvol = cum_volume/tod_baseline = 1_000_000/800_000 = 1.25 — below rvol_min=2.0 → Gate 2 blocks
        # That's expected: Gate 1 no longer blocks. Gate 2 may still block (that's correct behavior).
        # We verify gate_1 no longer emits signal_skipped_no_premarket_high by checking
        # that _premarket_highs now contains the code.
        assert engine._premarket_highs.get("US.NVDA") == 120.5, (
            "After merge: NVDA must be in _premarket_highs so Gate 1 can evaluate it"
        )


# ============================================================
# Finding 1.1/#5: note_intent_resolved decrements _pending_count
# ============================================================

class TestNoteIntentResolved:
    """note_intent_resolved must decrement _pending_count and never go below 0."""

    def test_note_intent_resolved_decrements_pending_count(self):
        """After note_intent_emitted then note_intent_resolved, _pending_count returns to prior value.

        Regression for finding 1.1/#5: note_intent_resolved had zero production callers,
        causing _pending_count to leak upward until it permanently blocked new entries.
        """
        engine = make_engine()
        assert engine._pending_count == 0, "count must start at 0"

        engine.note_intent_emitted()
        assert engine._pending_count == 1, "count must be 1 after emit"

        engine.note_intent_resolved()
        assert engine._pending_count == 0, "count must return to 0 after resolve"

    def test_note_intent_resolved_never_goes_below_zero(self):
        """Calling note_intent_resolved when count is already 0 must not produce a negative count."""
        engine = make_engine()
        engine.note_intent_resolved()  # call on zero — must not go negative
        assert engine._pending_count == 0, (
            "note_intent_resolved on count=0 must leave count=0 (underflow guard)"
        )


# ============================================================
# Task 1 (07-05): TOD-normalized I3 RVOL gate (SIG-RVOL-TOD)
# ============================================================

class TestTodNormalizedI3Gate:
    """TOD-normalized I3 gate: event.cum_volume / tod_baseline when baseline present,
    legacy event.volume / rvol_baseline fallback when absent (SIG-RVOL-TOD, Pitfall 4).
    """

    def _make_tod_store(
        self,
        session_date: str = "2026-07-03",
        code: str = "US.AAPL",
        rvol_baseline: int = 500_000,
        tod_baseline: float = None,
        time_bucket: str = "10:10",
    ) -> StateStore:
        """Open an in-memory store seeded with a daily_scan row and optional TOD baseline."""
        store = StateStore(db_path=":memory:")
        store.open()
        store.conn.execute(
            """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_date, code, 2.0, 1, f"{session_date}T09:30:00", rvol_baseline),
        )
        store.conn.commit()
        if tod_baseline is not None:
            store.upsert_tod_baselines(session_date, code, {time_bucket: tod_baseline})
        return store

    def test_tod_baseline_present_uses_cum_volume(self):
        """When TOD baseline exists, rvol = event.cum_volume / tod_baseline; passing at 2× (I3 passes).

        The key distinction from legacy: event.volume is small (0.4× rvol_baseline, which
        would fail I3 in legacy mode), but cum_volume is 2× tod_baseline, so I3 passes via
        the TOD-normalized path. Proves that the primary path is cum_volume / tod_baseline.
        """
        session_date = "2026-07-03"
        tod_baseline = 500_000     # 14-session avg cumulative volume at 10:10
        cum_volume = 1_000_000    # 2× tod_baseline → rvol=2.0 → I3 passes

        store = self._make_tod_store(
            session_date=session_date,
            rvol_baseline=500_000,
            tod_baseline=tod_baseline,
            time_bucket="10:10",
        )
        bar = BarEvent(
            code="US.AAPL",
            time_key=f"{session_date} 10:10:00",
            open=150.0, high=156.0, low=149.0, close=155.0,
            volume=200_000,        # legacy: 200K/500K = 0.4 → I3 fails (proves TOD is primary)
            hod=154.0,
            lod=148.0,
            cum_volume=cum_volume,
        )
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        cfg = make_cfg(rvol_min=2.0)
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs=premarket_highs)

        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is not None, (
            "I3 must pass when cum_volume (2×) / tod_baseline ≥ rvol_min=2.0. "
            "Old code uses event.volume/rvol_baseline = 0.4 → I3 fails (RED)."
        )

    def test_no_tod_baseline_fails_closed(self):
        """When no TOD baseline exists (get_tod_baseline returns 0.0), Gate 2 fails
        closed (strategy-audit P1-A): the legacy event.volume/rvol_baseline
        fallback was DELETED — it compared one 5m bar's volume against the mean
        of 14 prior DAILY volumes, an effectively unpassable ratio (a single 5m
        bar would need to trade >=2x the average FULL DAY's volume), and it was
        the ONLY path available to any intraday-rescan-added code (only the
        premarket scan wrote TOD baselines). A missing tod_baseline now means
        genuinely missing data, not a structural gap -- so on_bar must return
        None rather than silently emitting via a fallback ratio that can't be
        trusted.
        """
        session_date = "2026-07-03"
        store = self._make_tod_store(
            session_date=session_date,
            rvol_baseline=500_000,
            tod_baseline=None,    # no TOD baseline stored -> get_tod_baseline returns 0.0
        )
        bar = BarEvent(
            code="US.AAPL",
            time_key=f"{session_date} 10:10:00",
            open=150.0, high=156.0, low=149.0, close=155.0,
            volume=1_000_000,
            hod=154.0,
            lod=148.0,
            cum_volume=100_000_000,  # irrelevant -- Gate 2 fails before rvol is even computed
        )
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        cfg = make_cfg(rvol_min=2.0)
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs=premarket_highs)

        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is None, (
            "No tod_baseline must fail Gate 2 closed -- there is no legacy fallback "
            "to fall back to."
        )

    def test_tod_time_bucket_extraction(self):
        """time_bucket is extracted as time_key[11:16] ('HH:MM') using ET session date (Pitfall 4).

        TOD baseline is stored ONLY for bucket '10:05'. A bar with time_key '10:05:00' must
        look up bucket '10:05' and find the baseline; a bar with a different bucket would miss it.
        """
        session_date = "2026-07-03"
        time_bucket = "10:05"
        tod_baseline = 400_000
        cum_volume = 800_001      # just over 2× tod_baseline → I3 passes

        store = self._make_tod_store(
            session_date=session_date,
            rvol_baseline=500_000,
            tod_baseline=tod_baseline,
            time_bucket=time_bucket,
        )
        bar = BarEvent(
            code="US.AAPL",
            time_key=f"{session_date} 10:05:00",   # time_key[11:16] == "10:05"
            open=150.0, high=156.0, low=149.0, close=155.0,
            volume=200_000,    # legacy: 200K/500K = 0.4 → I3 fails (proves bucket is resolved)
            hod=154.0,
            lod=148.0,
            cum_volume=cum_volume,
        )
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        cfg = make_cfg(rvol_min=2.0)
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs=premarket_highs)

        # Patch now_et so session_date_str matches session_date
        in_window = datetime(2026, 7, 3, 10, 5, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is not None, (
            "Bucket '10:05' (time_key[11:16]) must resolve the TOD baseline and "
            "make I3 pass via cum_volume / tod_baseline > 2.0. "
            "Old code ignores the bucket and uses legacy rvol=0.4 → I3 fails (RED)."
        )

    def test_tod_baseline_present_below_threshold_i3_fails(self):
        """TOD baseline present but cum_volume below 2× → I3 fails (gate is real, not always-pass).

        event.volume=1_000_000 / rvol_baseline=500_000 = 2.0 → I3 would PASS via legacy.
        But cum_volume=900_000 / tod_baseline=500_000 = 1.8 < 2.0 → I3 must FAIL via TOD.
        Proves that TOD normalization tightens (not loosens) the gate when session volume is below baseline.
        """
        session_date = "2026-07-03"
        tod_baseline = 500_000
        cum_volume = 900_000      # 1.8× tod_baseline → rvol=1.8 < rvol_min=2.0 → I3 fails

        store = self._make_tod_store(
            session_date=session_date,
            rvol_baseline=500_000,
            tod_baseline=tod_baseline,
            time_bucket="10:10",
        )
        bar = BarEvent(
            code="US.AAPL",
            time_key=f"{session_date} 10:10:00",
            open=150.0, high=156.0, low=149.0, close=155.0,
            volume=1_000_000,     # legacy: 1M/500K = 2.0 → would PASS (old code emits signal)
            hod=154.0,
            lod=148.0,
            cum_volume=cum_volume,  # TOD: 900K/500K = 1.8 < 2.0 → I3 fails (new code blocks)
        )
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        cfg = make_cfg(rvol_min=2.0)
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs=premarket_highs)

        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is None, (
            "I3 must fail when cum_volume (1.8×) / tod_baseline < rvol_min=2.0. "
            "Old code uses legacy rvol=2.0 and emits a signal (RED failure)."
        )


# ============================================================
# Task 2 (07-05): Circuit-breaker gate in SignalEngine (RISK-CIRCUIT)
# ============================================================

class TestCircuitBreakerGate:
    """Daily -2R circuit breaker: detect, persist, block new entries (RISK-CIRCUIT, D-05/D-06/D-07).

    Gate 7 (after Gate 3 entry-window, before Gate 4 broker get_positions):
    _is_circuit_breaker_tripped(session_date_str) → blocks signal, skips SDK call.
    """

    def _make_breaker_store(
        self,
        session_date: str = "2026-07-03",
        realized_pnl: float = 0.0,
        breaker_date: str = None,
    ) -> StateStore:
        """Open an in-memory store seeded for circuit breaker tests.

        Args:
            realized_pnl: Closed-trade P&L for today; written via trades table if non-zero.
            breaker_date: If set, writes the circuit_breaker_tripped_date meta key.
        """
        store = StateStore(db_path=":memory:")
        store.open()
        # Seed daily_scan + tod_baseline so Gate 2 passes (no I3 skip)
        seed_rvol_baseline(store, session_date, "US.AAPL")
        # Seed a closed trade so get_daily_trade_stats returns realized_pnl.
        # realized_pnl = (exit_price - entry_price) * quantity (store computes it via SQL).
        # Schema: trade_id, position_id, code, entry_price, exit_price, quantity,
        #         exit_reason, r_multiple, closed_at
        if realized_pnl != 0.0:
            # Compute entry/exit prices that yield realized_pnl for 100 shares:
            # entry_price = 175, exit_price = 175 + realized_pnl/100
            entry_price = 175.0
            exit_price = entry_price + realized_pnl / 100.0
            r_multiple = -1.0 if realized_pnl < 0 else 1.0
            store.conn.execute(
                """INSERT INTO trades
                   (trade_id, position_id, code, entry_price, exit_price,
                    quantity, r_multiple, exit_reason, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "trade-001", "pos-001", "US.AAPL",
                    entry_price, exit_price,
                    100, r_multiple, "stop",
                    f"{session_date}T11:00:00",  # DATE(closed_at) = session_date
                ),
            )
            store.conn.commit()
        if breaker_date is not None:
            store.set_circuit_breaker_date(breaker_date)
        return store

    def _make_in_window_engine(self, cfg, store, premarket_highs):
        """Return a SignalEngine with get_positions mock returning empty DataFrame."""
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        return make_engine(cfg=cfg, gateway=gateway, store=store,
                           premarket_highs=premarket_highs)

    def _make_passing_bar(self, session_date="2026-07-03") -> BarEvent:
        """BarEvent that passes all non-breaker gates (I1/I2/I3, premarket_high<close).

        cum_volume=1_000_000 against _make_breaker_store's seed_rvol_baseline
        default tod_baseline=400_000 at bucket "10:10" -> rvol=2.5, passes I3.
        """
        return BarEvent(
            code="US.AAPL",
            time_key=f"{session_date} 10:10:00",
            open=150.0, high=156.0, low=149.0, close=155.0,
            volume=1_000_000,
            hod=154.0,
            lod=148.0,
            cum_volume=1_000_000,
        )

    def test_breaker_trips_on_loss_persists_and_blocks(self):
        """With realized_pnl <= -2R threshold, on_bar returns None and breaker date is persisted.

        1R = (max_risk_per_trade_pct/100) * sizing_equity_usd = 1% * $100K = $1,000.
        Threshold = -daily_circuit_breaker_r * 1R = -2.0 * $1,000 = -$2,000.
        realized_pnl = -$2,000 <= -$2,000 → breaker trips.

        Verifies:
        - on_bar returns None (blocked).
        - set_circuit_breaker_date called (persisted) — checked via get_circuit_breaker_date.
        - circuit_breaker_tripped log fires (not testable directly; gate behavior is the proxy).
        """
        session_date = "2026-07-03"
        one_r = 1_000.0          # 1% of $100K
        realized_pnl = -2_000.0  # = -2R → exactly at threshold (tripped)

        cfg = make_cfg(
            max_risk_per_trade_pct=1.0,
            sizing_equity_usd=100_000.0,
            daily_circuit_breaker_r=2.0,
        )
        store = self._make_breaker_store(session_date=session_date, realized_pnl=realized_pnl)
        premarket_highs = {"US.AAPL": 150.0}
        engine = self._make_in_window_engine(cfg, store, premarket_highs)

        bar = self._make_passing_bar(session_date=session_date)
        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is None, (
            "Circuit breaker must block on_bar when realized_pnl <= -2R threshold "
            "(RISK-CIRCUIT / D-06). No _is_circuit_breaker_tripped in old code → RED."
        )
        # Breaker date must have been persisted
        assert store.get_circuit_breaker_date() == session_date, (
            "set_circuit_breaker_date must be called on first trip so restart survives (D-07)."
        )

    def test_breaker_not_tripped_above_threshold_passes(self):
        """With realized_pnl above the threshold, the breaker does not trip and gate passes through."""
        session_date = "2026-07-03"
        realized_pnl = -500.0    # -0.5R < threshold of -2R → no trip

        cfg = make_cfg(
            max_risk_per_trade_pct=1.0,
            sizing_equity_usd=100_000.0,
            daily_circuit_breaker_r=2.0,
        )
        store = self._make_breaker_store(session_date=session_date, realized_pnl=realized_pnl)
        premarket_highs = {"US.AAPL": 150.0}
        engine = self._make_in_window_engine(cfg, store, premarket_highs)

        bar = self._make_passing_bar(session_date=session_date)
        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is not None, (
            "Circuit breaker must NOT block when realized_pnl (-$500) > -2R threshold (-$2K)."
        )
        assert store.get_circuit_breaker_date() is None, (
            "No breaker date must be stored when threshold not reached."
        )

    def test_already_tripped_short_circuits_without_pnl_requery(self):
        """If circuit_breaker_tripped_date == today, on_bar returns None WITHOUT re-querying P&L.

        The already-tripped path must avoid a DB round-trip for trade stats on every bar.
        Verified by patching get_daily_trade_stats to raise — if it is called, the test fails.
        """
        session_date = "2026-07-03"
        cfg = make_cfg(
            max_risk_per_trade_pct=1.0,
            sizing_equity_usd=100_000.0,
            daily_circuit_breaker_r=2.0,
        )
        # Pre-set the breaker date to today → already tripped
        store = self._make_breaker_store(session_date=session_date, realized_pnl=0.0,
                                         breaker_date=session_date)
        premarket_highs = {"US.AAPL": 150.0}
        engine = self._make_in_window_engine(cfg, store, premarket_highs)

        bar = self._make_passing_bar(session_date=session_date)
        in_window = datetime(2026, 7, 3, 10, 10, 0)

        # Patch get_daily_trade_stats to raise — must NOT be called on the already-tripped path
        with patch.object(store, "get_daily_trade_stats",
                          side_effect=AssertionError("get_daily_trade_stats must not be called on already-tripped day")), \
             patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is None, (
            "on_bar must return None when breaker is already tripped for today. "
            "Old code has no _is_circuit_breaker_tripped → does not block (RED)."
        )

    def test_auto_reset_prior_date_clears_breaker(self):
        """If stored breaker date is from a PRIOR session, clear_circuit_breaker is called (D-07).

        Auto-reset: a stale entry from yesterday must not block today's trading.
        After clear, and with realized_pnl above threshold, gate passes.
        """
        session_date = "2026-07-03"
        prior_date = "2026-07-02"

        cfg = make_cfg(
            max_risk_per_trade_pct=1.0,
            sizing_equity_usd=100_000.0,
            daily_circuit_breaker_r=2.0,
        )
        # Store a PRIOR date (yesterday's trip) and no today realized loss
        store = self._make_breaker_store(session_date=session_date, realized_pnl=0.0,
                                         breaker_date=prior_date)
        premarket_highs = {"US.AAPL": 150.0}
        engine = self._make_in_window_engine(cfg, store, premarket_highs)

        bar = self._make_passing_bar(session_date=session_date)
        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        # Gate should pass (auto-reset) and clear_circuit_breaker must have been called
        assert result is not None, (
            "Stale prior-date breaker must auto-reset (clear_circuit_breaker) and "
            "NOT block today's trading (D-07)."
        )
        # clear_circuit_breaker removes the meta row
        assert store.get_circuit_breaker_date() is None, (
            "clear_circuit_breaker must have removed the stale prior-date entry (D-07)."
        )

    def test_breaker_gate_before_get_positions_no_sdk_call_on_tripped_day(self):
        """Gate 7 is evaluated before Gate 4 (get_positions) — no broker SDK call on a tripped day.

        On a tripped day, get_positions must NOT be called (avoids network round-trip every bar).
        """
        session_date = "2026-07-03"
        cfg = make_cfg(
            max_risk_per_trade_pct=1.0,
            sizing_equity_usd=100_000.0,
            daily_circuit_breaker_r=2.0,
        )
        # Already tripped today
        store = self._make_breaker_store(session_date=session_date, realized_pnl=0.0,
                                         breaker_date=session_date)
        premarket_highs = {"US.AAPL": 150.0}

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs=premarket_highs)

        bar = self._make_passing_bar(session_date=session_date)
        in_window = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result = run(engine.on_bar(bar))

        assert result is None, "Tripped breaker must block on_bar."
        gateway.get_positions.assert_not_called(), (
            "get_positions must NOT be called when circuit breaker is tripped "
            "(Gate 7 before Gate 4 — T-07-18 mitigation)."
        )


# ============================================================
# I2 close_above_prior_hod tracker (strategy-audit finding)
# ============================================================

class TestI2ModeTracker:
    """SignalEngine's per-code (session_day, hod) prior-bar tracker: updates
    regardless of which gate a bar fails, and resets across a session boundary.
    """

    # Every 5m bucket this class's directly-constructed BarEvents use across its
    # three tests -- seeding all of them means each test doesn't have to derive
    # its own passing cum_volume/tod_baseline math per bar.
    _BUCKETS = ("10:05", "10:10", "15:25")

    @classmethod
    def _seed_rvol(cls, store: StateStore, session_date: str, code: str = "US.AAPL") -> None:
        for i, bucket in enumerate(cls._BUCKETS):
            # gap_pct/rank only matter for the daily_scan row's first insert per
            # (session_date, code); seed_rvol_baseline's INSERT is idempotent-safe
            # here because each bucket call targets the SAME row via upsert_tod_baselines
            # for the tod_baselines table, but daily_scan has no upsert -- only seed
            # that row once (i == 0) and add tod_baselines for the rest.
            if i == 0:
                seed_rvol_baseline(store, session_date, code, time_bucket=bucket, tod_baseline=200_000.0)
            else:
                store.upsert_tod_baselines(session_date, code, {bucket: 200_000.0})

    def test_tracker_updates_even_when_an_earlier_gate_fails(self):
        """Bar 1 fails Gate 1 (no premarket high yet) and returns None -- the
        prior-hod tracker must still have recorded bar 1's hod, so bar 2 (once
        the premarket high becomes available, e.g. via a later intraday rescan)
        sees the correct hod_prev instead of None."""
        cfg = make_cfg(i2_mode="close_above_prior_hod")
        session_date = "2026-07-03"
        store = StateStore(db_path=":memory:")
        store.open()
        self._seed_rvol(store, session_date)

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        # No premarket_highs seeded yet -- bar 1 must fail Gate 1.
        engine = make_engine(cfg=cfg, gateway=gateway, store=store)

        bar1 = BarEvent(
            code="US.AAPL", time_key=f"{session_date} 10:05:00",
            open=140.0, high=148.0, low=139.0, close=145.0,
            volume=1_000_000, hod=148.0, lod=138.0, cum_volume=1_000_000,
        )
        in_window = datetime(2026, 7, 3, 10, 5, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window):
            result1 = run(engine.on_bar(bar1))
        assert result1 is None, "bar 1 must fail Gate 1 (no premarket high yet)"

        # Premarket high becomes available (e.g. a later rescan) -- bar 2 closes
        # ABOVE bar 1's hod=148 (I2 close_above_prior_hod) but BELOW its own
        # running hod=153 (I2 close_at_hod would fail this same bar), proving
        # both that the tracker survived bar 1's early Gate-1 failure and that
        # the two I2 modes are genuinely different.
        engine.set_premarket_highs({"US.AAPL": 140.0})
        bar2 = BarEvent(
            code="US.AAPL", time_key=f"{session_date} 10:10:00",
            open=145.0, high=153.0, low=144.0, close=150.0,
            volume=1_000_000, hod=153.0, lod=138.0, cum_volume=2_000_000,
        )
        in_window2 = datetime(2026, 7, 3, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window2):
            result2 = run(engine.on_bar(bar2))

        assert result2 is not None, (
            "bar 2 (close=150 > bar1's hod_prev=148) must pass I2 under "
            "close_above_prior_hod despite bar 1 never reaching Gate 2."
        )

    def test_close_at_hod_mode_fails_the_same_bar_that_close_above_prior_hod_passes(self):
        """Same two-bar sequence as above, default i2_mode -- proves the discriminator
        is real: close_at_hod correctly rejects bar 2 (close=150 < its own hod=153)."""
        cfg = make_cfg()  # i2_mode defaults to "close_at_hod"
        session_date = "2026-07-03"
        store = StateStore(db_path=":memory:")
        store.open()
        self._seed_rvol(store, session_date)

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs={"US.AAPL": 140.0})

        bar1 = BarEvent(
            code="US.AAPL", time_key=f"{session_date} 10:05:00",
            open=140.0, high=148.0, low=139.0, close=145.0,
            volume=1_000_000, hod=148.0, lod=138.0, cum_volume=1_000_000,
        )
        bar2 = BarEvent(
            code="US.AAPL", time_key=f"{session_date} 10:10:00",
            open=145.0, high=153.0, low=144.0, close=150.0,
            volume=1_000_000, hod=153.0, lod=138.0, cum_volume=2_000_000,
        )
        with patch("bot.signal.signal_engine.now_et", return_value=datetime(2026, 7, 3, 10, 5, 0)):
            run(engine.on_bar(bar1))
        with patch("bot.signal.signal_engine.now_et", return_value=datetime(2026, 7, 3, 10, 10, 0)):
            result2 = run(engine.on_bar(bar2))

        assert result2 is None, "close_at_hod must reject close=150 < hod=153"

    def test_tracker_resets_across_a_session_boundary(self):
        """A bar on day 2 must not inherit day 1's hod -- hod_prev fails closed
        (I2 rejects) rather than leaking yesterday's high across the boundary."""
        cfg = make_cfg(i2_mode="close_above_prior_hod")
        day1, day2 = "2026-07-02", "2026-07-03"
        store = StateStore(db_path=":memory:")
        store.open()
        self._seed_rvol(store, day1)
        self._seed_rvol(store, day2)

        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        engine = make_engine(cfg=cfg, gateway=gateway, store=store,
                             premarket_highs={"US.AAPL": 140.0})

        bar_day1 = BarEvent(
            code="US.AAPL", time_key=f"{day1} 15:25:00",
            open=140.0, high=148.0, low=139.0, close=145.0,
            volume=1_000_000, hod=148.0, lod=138.0, cum_volume=1_000_000,
        )
        with patch("bot.signal.signal_engine.now_et", return_value=datetime(2026, 7, 2, 15, 25, 0)):
            run(engine.on_bar(bar_day1))

        # Day 2's first bar: close=150 WOULD pass I2 if hod_prev leaked in as
        # day 1's 148 (150 > 148) -- it must instead fail closed (hod_prev=None
        # because the tracker's stored session_day "2026-07-02" != "2026-07-03").
        bar_day2 = BarEvent(
            code="US.AAPL", time_key=f"{day2} 10:05:00",
            open=145.0, high=153.0, low=144.0, close=150.0,
            volume=1_000_000, hod=153.0, lod=138.0, cum_volume=1_000_000,
        )
        with patch("bot.signal.signal_engine.now_et", return_value=datetime(2026, 7, 3, 10, 5, 0)):
            result_day2 = run(engine.on_bar(bar_day2))

        assert result_day2 is None, (
            "hod_prev must reset across the session boundary, not leak day 1's hod=148"
        )
