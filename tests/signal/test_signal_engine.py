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
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def make_bar(code="US.AAPL", close=155.0, hod=154.0, lod=148.0) -> BarEvent:
    """Build a BarEvent that will satisfy I1/I2/I3 when premarket_high < close."""
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

        # base bar that passes all: close=155 > premarket_high=150, close=155 >= hod=154, rvol=3.0
        base_bar = make_bar(close=155.0, hod=hod)
        # rvol comes from the daily_scan table — mock it via store
        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = "2026-06-24"
        store.conn.execute(
            """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
        )
        store.conn.commit()

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
            # Insert very small rvol_baseline so rvol = volume/baseline < 2.0
            # volume=1_000_000, if baseline=600_000 → rvol=1.67 < 2.0
            store2.conn.execute(
                """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 600_000),
            )
            store2.conn.commit()
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
        store.conn.execute(
            """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
        )
        store.conn.commit()

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
            store2.conn.execute(
                """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
            )
            store2.conn.commit()
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
        store.conn.execute(
            """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
        )
        store.conn.commit()

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

class TestSignalEngineConcurrentCap:
    """No signal when open positions >= max_concurrent_positions."""

    def test_concurrent_cap(self):
        """
        When the number of open broker positions equals cfg.max_concurrent_positions,
        the signal engine emits no SignalEvent (SIG-04, RISK-04).
        Below the cap, a passing setup still emits.
        """
        cfg = make_cfg(max_concurrent_positions=3, max_trades_per_day=10)

        scan_date = "2026-06-24"

        def make_positioned_store():
            s = StateStore(db_path=":memory:")
            s.open()
            s.conn.execute(
                """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
            )
            s.conn.commit()
            return s

        bar = make_bar(close=155.0, hod=154.0)
        premarket_highs = {"US.AAPL": 150.0}

        # Simulate 3 open positions (= cap)
        open_pos_df = pd.DataFrame({
            "code": ["US.X1", "US.X2", "US.X3"],
            "qty": [100, 100, 100],
        })
        gateway_at_cap = MagicMock()
        gateway_at_cap.get_positions = AsyncMock(return_value=(0, open_pos_df))

        in_window_time = datetime(2026, 6, 24, 10, 10, 0)
        with patch("bot.signal.signal_engine.now_et", return_value=in_window_time):
            # AT cap: no signal
            store_cap = make_positioned_store()
            engine_at_cap = make_engine(cfg=cfg, gateway=gateway_at_cap, store=store_cap,
                                        premarket_highs=premarket_highs)
            result_at_cap = run(engine_at_cap.on_bar(bar))
            assert result_at_cap is None, "At cap (3 positions, max=3), expected no signal"

            # BELOW cap: signal should fire
            below_cap_df = pd.DataFrame({
                "code": ["US.X1", "US.X2"],
                "qty": [100, 100],
            })
            gateway_below_cap = MagicMock()
            gateway_below_cap.get_positions = AsyncMock(return_value=(0, below_cap_df))
            store_below = make_positioned_store()
            engine_below = make_engine(cfg=cfg, gateway=gateway_below_cap, store=store_below,
                                       premarket_highs=premarket_highs)
            result_below = run(engine_below.on_bar(bar))
            assert result_below is not None, "Below cap (2 positions, max=3), expected signal"


# ============================================================
# RISK-05: daily cap
# ============================================================

class TestSignalEngineDailyCap:
    """Daily new-entry cap: filled_count + pending_count < max_trades_per_day (D-09)."""

    def _make_daily_store(self, filled_count: int, session_date="2026-06-24") -> StateStore:
        store = StateStore(db_path=":memory:")
        store.open()
        scan_date = session_date
        store.conn.execute(
            """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
        )
        store.conn.commit()
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

        # Simulate position count AT cap to block the signal
        open_pos_df = pd.DataFrame({"code": ["US.X1", "US.X2", "US.X3"]})
        gateway = MagicMock()
        gateway.get_positions = AsyncMock(return_value=(0, open_pos_df))

        store = self._make_daily_store(filled_count=0)

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
        Each emitted SignalEvent increments the in-memory pending_count so
        that subsequent signals see the correct D-09 tally (burst guard).
        """
        cfg = make_cfg(max_concurrent_positions=5, max_trades_per_day=2)
        premarket_highs = {"US.AAPL": 150.0}

        scan_date = "2026-06-24"

        def make_store_with_scan():
            s = StateStore(db_path=":memory:")
            s.open()
            s.conn.execute(
                """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
            )
            s.conn.commit()
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
            assert engine._pending_count == 1, "Pending count should increment to 1 after emit"

            # Second call with same code is blocked by re-entry gate
            # We need a fresh engine that can emit for a DIFFERENT scenario
            # Let's manually manipulate: set _pending_count to 1 on a fresh engine
            engine2 = SignalEngine(cfg=cfg, gateway=gateway, store=make_store_with_scan())
            engine2.set_premarket_highs(premarket_highs)
            engine2._pending_count = 1  # Simulate one pending from earlier

            # filled=0, pending=1 → total=1 < 2 → should still emit
            result2 = run(engine2.on_bar(bar1))
            assert result2 is not None, "Second signal can emit when total=1 < 2"
            assert engine2._pending_count == 2

            # Now pending=2, max=2 → blocked
            engine3 = SignalEngine(cfg=cfg, gateway=gateway, store=make_store_with_scan())
            engine3.set_premarket_highs(premarket_highs)
            engine3._pending_count = 2  # Simulate 2 pending

            result3 = run(engine3.on_bar(bar1))
            assert result3 is None, "Signal blocked when total pending == max_trades_per_day"
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
            s.conn.execute(
                """INSERT INTO daily_scan (scan_date, code, gap_pct, rank, created_at, rvol_baseline)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scan_date, "US.AAPL", 2.0, 1, "2026-06-24T09:30:00", 500_000),
            )
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
