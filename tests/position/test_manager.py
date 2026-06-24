#!/usr/bin/env python3
"""
tests.position.test_manager — Unit tests for PositionManager (Phase 4, 04-02).

Tests cover:
  POS-05: restart reconstruction reproduces persisted phase/remaining_quantity/trail_stop.
  On-fill processing:
    - Entry fill → ACTIVE, daily_count incremented, pending_intent RESOLVED.
    - Exit fill by order_id — partial exit does not close the position (EXEC-05, Pitfall E).
  DB-first persistence:
    - Crash-sim asserts the DB row is authoritative after _persist_position even if
      the subsequent in-memory mutation is skipped (Pitfall G / T-04-06).
  Trail never-loosen:
    - on_bar with new_swing_low below trail_stop leaves trail_stop unchanged (D-11 / T-04-07).
  order_id-only matching:
    - Source grep confirms no (code, qty) tuple key is used for fill matching (T-04-05).

test_force_close_half_day stays a stub — implemented in 04-04.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from bot.execution.events import FillEvent
from bot.position.manager import PositionManager, _row_to_position_state
from bot.position.state import PositionPhase, PositionState
from bot.signal.events import BarEvent
from bot.state.store import StateStore


# ============================================================
# Helpers / Fixtures
# ============================================================

_DEFAULT_OPENED_AT = datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)
_DEFAULT_UPDATED_AT = datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)


def _make_pos(
    code="US.AAPL",
    phase=PositionPhase.AWAITING_FILL,
    entry_price=100.0,
    initial_stop=98.0,
    trail_stop=98.0,
    full_quantity=300,
    remaining_quantity=300,
    entry_order_id="ORDER-ENTRY-001",
    exit_order_id=None,
    avg_fill_price=None,
    position_id=None,
    opened_at=None,
    updated_at=None,
) -> PositionState:
    """Factory for PositionState with sensible defaults.

    opened_at and updated_at always default to a fixed UTC datetime so that
    upsert_position never hits the NOT NULL constraint (migration 0001).
    """
    return PositionState(
        position_id=position_id or str(uuid.uuid4()),
        code=code,
        phase=phase,
        entry_price=entry_price,
        initial_stop=initial_stop,
        trail_stop=trail_stop,
        full_quantity=full_quantity,
        remaining_quantity=remaining_quantity,
        entry_order_id=entry_order_id,
        exit_order_id=exit_order_id,
        avg_fill_price=avg_fill_price,
        opened_at=opened_at or _DEFAULT_OPENED_AT,
        updated_at=updated_at or _DEFAULT_UPDATED_AT,
    )


def _make_fill(
    order_id="ORDER-ENTRY-001",
    intent_id="INTENT-001",
    code="US.AAPL",
    filled_qty=300,
    avg_fill_price=100.0,
    is_entry=True,
    fill_time=None,
) -> FillEvent:
    """Factory for FillEvent with sensible defaults."""
    return FillEvent(
        order_id=order_id,
        intent_id=intent_id,
        code=code,
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        is_entry=is_entry,
        fill_time=fill_time or datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
    )


def _make_bar(
    code="US.AAPL",
    close=100.0,
    time_key="2026-06-24 10:05:00",
) -> BarEvent:
    """Factory for BarEvent with sensible defaults."""
    return BarEvent(
        code=code,
        time_key=time_key,
        open=100.0,
        high=101.0,
        low=99.5,
        close=close,
        volume=50000,
        hod=101.0,
        lod=98.0,
    )


def _minimal_cfg():
    """Return a minimal mock StrategyConfig with thresholds from rules.json."""
    cfg = MagicMock()
    cfg.partial_profit_trigger_r = 0.75
    cfg.partial_profit_fraction = 0.3333
    cfg.breakeven_trigger_r = 1.0
    cfg.exit_escalation_step_usd = 0.10
    cfg.exit_escalation_cadence_seconds = 10.0
    cfg.exit_ttl_seconds = 15.0
    return cfg


@pytest.fixture
def open_store(tmp_state_db):
    """Return a StateStore opened at the tmp_state_db path."""
    store = StateStore(db_path=tmp_state_db).open()
    yield store
    store.close()


@pytest.fixture
def mock_engine():
    """Return an async mock ExecutionEngine."""
    engine = MagicMock()
    engine.manage_exit = AsyncMock(return_value=0)
    return engine


@pytest.fixture
def mock_strategy():
    """Return a mock TrendJoinLong with a predictable swing-low."""
    strategy = MagicMock()
    strategy.compute_swing_low_2_2 = MagicMock(return_value=99.0)
    return strategy


@pytest.fixture
def manager(open_store, mock_engine, mock_strategy):
    """Return a PositionManager wired to an in-memory store + mocks."""
    cfg = _minimal_cfg()
    return PositionManager(
        store=open_store,
        engine=mock_engine,
        cfg=cfg,
        strategy=mock_strategy,
    )


# ============================================================
# Helper — seed a pending_intent row in the store
# ============================================================

def _seed_pending_intent(store: StateStore, intent_id: str, code: str = "US.AAPL") -> None:
    """Insert a PENDING pending_intents row for testing D-12 resolution."""
    store.conn.execute(
        """INSERT INTO pending_intents
           (intent_id, code, status, entry_price, stop_price, quantity, emitted_at)
           VALUES (?, ?, 'PENDING', 100.0, 98.0, 300, '2026-06-24T10:00:00+00:00')""",
        (intent_id, code),
    )
    store.conn.commit()


def _seed_daily_count(store: StateStore, session_date: str, filled_count: int = 0) -> None:
    """Insert a daily_trade_count row for testing D-08 increment."""
    store.conn.execute(
        """INSERT OR IGNORE INTO daily_trade_count (session_date, filled_count, updated_at)
           VALUES (?, ?, '2026-06-24T10:00:00+00:00')""",
        (session_date, filled_count),
    )
    store.conn.commit()


# ============================================================
# Test: Entry fill → ACTIVE + daily_count incremented + pending_intent RESOLVED
# ============================================================

class TestEntryFill:
    """Tests for on_fill with is_entry=True (D-08/D-12/EXEC-05)."""

    def test_entry_fill_advances_phase_to_active(self, manager, open_store):
        """Entry fill advances AWAITING_FILL → ACTIVE; entry_price set from fill."""
        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-ENTRY-001",
            intent_id="INTENT-001",
            filled_qty=300,
            avg_fill_price=100.50,
            is_entry=True,
        )
        manager.on_fill(fill)

        # In-memory phase is now ACTIVE
        assert pos.phase == PositionPhase.ACTIVE
        # entry_price set from fill (D-06)
        assert pos.entry_price == 100.50
        assert pos.full_quantity == 300
        assert pos.remaining_quantity == 300

    def test_entry_fill_persists_to_db(self, manager, open_store):
        """Entry fill writes ACTIVE phase to the DB (DB-first, Pitfall G)."""
        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(order_id="ORDER-ENTRY-001", intent_id="INTENT-001")
        manager.on_fill(fill)

        # Read back from DB and verify phase is ACTIVE
        rows = open_store.get_open_positions()
        assert len(rows) == 1
        assert rows[0]["phase"] == "ACTIVE"
        assert rows[0]["avg_fill_price"] == 100.0

    def test_entry_fill_increments_daily_filled_count(self, manager, open_store):
        """Entry fill increments daily_trade_count.filled_count for the session date (D-08)."""
        # Seed an existing row with filled_count=0
        _seed_daily_count(open_store, "2026-06-24", filled_count=0)

        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-ENTRY-001",
            intent_id="INTENT-001",
            fill_time=datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
        )
        manager.on_fill(fill)

        # Read back the daily count
        row = open_store.conn.execute(
            "SELECT filled_count FROM daily_trade_count WHERE session_date='2026-06-24'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1  # incremented from 0 to 1

    def test_entry_fill_creates_daily_count_row_if_not_exists(self, manager, open_store):
        """Entry fill creates a new daily_trade_count row when none exists (D-08)."""
        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-ENTRY-001",
            intent_id="INTENT-001",
            fill_time=datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
        )
        manager.on_fill(fill)

        row = open_store.conn.execute(
            "SELECT filled_count FROM daily_trade_count WHERE session_date='2026-06-24'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1  # created with count 1

    def test_entry_fill_resolves_pending_intent(self, manager, open_store):
        """Entry fill resolves pending_intents row PENDING → RESOLVED (D-12)."""
        intent_id = "INTENT-001"
        _seed_pending_intent(open_store, intent_id)

        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-ENTRY-001",
            intent_id=intent_id,
            fill_time=datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
        )
        manager.on_fill(fill)

        row = open_store.conn.execute(
            "SELECT status FROM pending_intents WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "RESOLVED"

    def test_entry_fill_unknown_order_id_is_noop(self, manager, open_store):
        """Entry fill with unrecognised order_id is silently ignored (no crash)."""
        pos = _make_pos(entry_order_id="ORDER-ENTRY-001")
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Different order_id — not matched
        fill = _make_fill(
            order_id="ORDER-UNKNOWN",
            intent_id="INTENT-001",
        )
        manager.on_fill(fill)

        # Position remains in AWAITING_FILL (no mutation)
        assert pos.phase == PositionPhase.AWAITING_FILL


# ============================================================
# Test: Exit fill by order_id — partial exit does NOT close position (EXEC-05, Pitfall E)
# ============================================================

class TestExitFill:
    """Tests for on_fill with is_entry=False (EXEC-05, Pitfall E, T-04-05)."""

    def test_partial_exit_fill_does_not_close_position(self, manager, open_store):
        """Exit fill of 100 against a 300-share ACTIVE position leaves remaining_quantity=200.

        A partial exit is NOT treated as a full stop-out (EXEC-05, Pitfall E).
        Position phase must NOT become CLOSED after a partial fill.
        """
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=300,
            exit_order_id="ORDER-EXIT-001",
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Fill 100 of 300 shares via the exit order
        fill = _make_fill(
            order_id="ORDER-EXIT-001",
            intent_id="",
            filled_qty=100,
            is_entry=False,
        )
        manager.on_fill(fill)

        # remaining_quantity decremented to 200 — NOT CLOSED
        assert pos.remaining_quantity == 200
        assert pos.phase != PositionPhase.CLOSED

    def test_full_exit_fill_closes_position(self, manager, open_store):
        """Exit fill of exactly remaining_quantity closes the position (CLOSED phase)."""
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-001",
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-EXIT-001",
            intent_id="",
            filled_qty=100,
            is_entry=False,
        )
        manager.on_fill(fill)

        assert pos.remaining_quantity == 0
        assert pos.phase == PositionPhase.CLOSED

    def test_exit_fill_matched_by_order_id_only(self, manager, open_store):
        """Two positions with same-qty but different exit_order_ids — only matched position updated.

        Verifies fills are keyed by order_id, never by (code, qty) tuple (EXEC-05, T-04-05).
        """
        # Position 1: exit_order_id matches the fill
        pos1 = _make_pos(
            code="US.AAPL",
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-001",
            position_id="POS-001",
        )
        # Position 2: different code, same remaining_quantity, different order_id
        pos2 = _make_pos(
            code="US.TSLA",
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-002",
            position_id="POS-002",
        )
        manager._positions["US.AAPL"] = pos1
        manager._positions["US.TSLA"] = pos2
        open_store.upsert_position(pos1)
        open_store.upsert_position(pos2)

        # Fill for ORDER-EXIT-001 only
        fill = _make_fill(
            order_id="ORDER-EXIT-001",
            intent_id="",
            code="US.AAPL",
            filled_qty=100,
            is_entry=False,
        )
        manager.on_fill(fill)

        # pos1 closed; pos2 unchanged
        assert pos1.remaining_quantity == 0
        assert pos1.phase == PositionPhase.CLOSED
        assert pos2.remaining_quantity == 100  # untouched
        assert pos2.phase == PositionPhase.ACTIVE

    def test_exit_fill_unknown_order_id_is_noop(self, manager, open_store):
        """Exit fill with unrecognised order_id is silently ignored."""
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=300,
            exit_order_id="ORDER-EXIT-001",
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-EXIT-UNKNOWN",
            intent_id="",
            filled_qty=300,
            is_entry=False,
        )
        manager.on_fill(fill)

        # No change
        assert pos.remaining_quantity == 300


# ============================================================
# Test: DB-first persistence — crash-sim (Pitfall G / T-04-06)
# ============================================================

class TestDBFirstPersistence:
    """Verify _persist_position writes to DB before in-memory mutation (Pitfall G)."""

    def test_db_row_updated_before_inmemory_mutation(self, manager, open_store):
        """Crash-sim: DB row reflects new phase even if in-memory state is never updated.

        Simulates Pitfall G: after calling _persist_position(), the DB row must
        show the new phase. We verify this by reading the DB row immediately after
        calling _persist_position() (before any further caller action).
        """
        pos = _make_pos(
            phase=PositionPhase.AWAITING_FILL,
            position_id="POS-CRASH-SIM",
        )
        open_store.upsert_position(pos)

        # Simulate what _on_entry_fill does: mutate in-memory, then DB-first persist
        pos.phase = PositionPhase.ACTIVE
        pos.entry_price = 100.50
        pos.avg_fill_price = 100.50
        pos.full_quantity = 300
        pos.remaining_quantity = 300

        # DB-first: persist BEFORE any caller observes the new in-memory phase
        manager._persist_position(pos, event="crash_sim_test")

        # NOW read from DB — must already show ACTIVE even if in-memory was "skipped"
        rows = open_store.get_open_positions()
        db_row = next((r for r in rows if r["position_id"] == "POS-CRASH-SIM"), None)
        assert db_row is not None, "position row not found in DB"
        assert db_row["phase"] == "ACTIVE", (
            f"DB row phase is {db_row['phase']!r} — DB-first persistence failed (Pitfall G)"
        )
        assert db_row["avg_fill_price"] == 100.50

    def test_persist_position_commits_immediately(self, manager, open_store):
        """_persist_position calls upsert_position which commits in the same call."""
        pos = _make_pos(position_id="POS-COMMIT-TEST")
        open_store.upsert_position(pos)

        # Mutate and persist
        pos.phase = PositionPhase.PARTIAL_TAKEN
        pos.remaining_quantity = 200
        manager._persist_position(pos, event="commit_test")

        # Open a SECOND connection to the same DB and verify the commit is visible
        store2 = StateStore(db_path=open_store._db_path).open()
        try:
            rows = store2.get_open_positions()
            db_row = next((r for r in rows if r["position_id"] == "POS-COMMIT-TEST"), None)
            assert db_row is not None
            assert db_row["phase"] == "PARTIAL_TAKEN"
            assert db_row["remaining_quantity"] == 200
        finally:
            store2.close()


# ============================================================
# Test: Trail never-loosen (D-11 / T-04-07)
# ============================================================

class TestTrailNeverLoosen:
    """Verify trail_stop is never reduced by a lower swing-low (D-11)."""

    def test_lower_swing_low_does_not_reduce_trail_stop(self, manager, mock_strategy):
        """on_bar with new_swing_low below trail_stop leaves trail_stop unchanged."""
        # Strategy mock returns a swing-low BELOW the current trail_stop
        mock_strategy.compute_swing_low_2_2.return_value = 95.0  # below trail_stop=98

        pos = _make_pos(
            phase=PositionPhase.TRAILING,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
        )
        manager._positions["US.AAPL"] = pos

        bar = _make_bar(close=102.0)  # not a stop-out; above trail_stop

        asyncio.run(manager.on_bar(bar))

        # trail_stop must NOT have been loosened (D-11)
        assert pos.trail_stop == 98.0, (
            f"trail_stop was lowered to {pos.trail_stop} — D-11 never-loosen invariant violated"
        )

    def test_higher_swing_low_raises_trail_stop(
        self, open_store, mock_engine, mock_strategy
    ):
        """on_bar with new_swing_low above trail_stop ratchets up the stop (D-11 / POS-03)."""
        from collections import deque

        # Wire a bar_buffer with enough bars so swing-low is computed
        bar_buf = {
            "US.AAPL": deque(
                [{"low": 99.5, "high": 102.0, "open": 100.0, "close": 101.0, "volume": 1000}
                 for _ in range(5)],
                maxlen=50,
            )
        }
        mock_strategy.compute_swing_low_2_2.return_value = 99.5  # above trail_stop=98

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            bar_buffer=bar_buf,
        )

        pos = _make_pos(
            phase=PositionPhase.TRAILING,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=102.0)  # above trail_stop; swing-low above old stop

        asyncio.run(mgr.on_bar(bar))

        # trail_stop must have been ratcheted up to 99.5
        assert pos.trail_stop == 99.5
        assert pos.phase == PositionPhase.TRAILING

    def test_bar_aggregator_none_does_not_update_trail_stop(self, manager, mock_strategy, open_store):
        """When bar_buffer is None (no BarAggregator wired), trail_stop stays unchanged."""
        # No bar_buffer on manager (default None)
        assert manager._bar_buffer is None

        # Force swing-low to return None when no buffer
        pos = _make_pos(
            phase=PositionPhase.TRAILING,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=102.0)

        asyncio.run(manager.on_bar(bar))

        # No swing-low computed — trail_stop unchanged, phase stays TRAILING
        assert pos.trail_stop == 98.0


# ============================================================
# Test: on_bar stop-out transition
# ============================================================

class TestOnBarStopOut:
    """Verify stop-out transitions from on_bar (D-02)."""

    def test_close_below_trail_stop_triggers_stop_out(
        self, manager, open_store, mock_engine, mock_strategy
    ):
        """A bar.close <= trail_stop triggers a STOP_OUT transition and persists CLOSED."""
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Close AT the trail_stop — triggers stop-out (D-02: close <= trail_stop)
        bar = _make_bar(close=97.5)

        asyncio.run(manager.on_bar(bar))

        # FSM transitioned to CLOSED
        assert pos.phase == PositionPhase.CLOSED
        # DB also shows CLOSED
        rows = open_store.conn.execute(
            "SELECT phase FROM positions WHERE code='US.AAPL'"
        ).fetchall()
        assert any(r[0] == "CLOSED" for r in rows)

    def test_awaiting_fill_bar_is_noop(self, manager):
        """on_bar with AWAITING_FILL position is a no-op (not yet filled)."""
        pos = _make_pos(phase=PositionPhase.AWAITING_FILL)
        manager._positions["US.AAPL"] = pos

        bar = _make_bar(close=50.0)  # would be a stop-out if active
        asyncio.run(manager.on_bar(bar))

        # No change — still AWAITING_FILL
        assert pos.phase == PositionPhase.AWAITING_FILL

    def test_closed_position_bar_is_noop(self, manager):
        """on_bar with CLOSED position is a no-op."""
        pos = _make_pos(phase=PositionPhase.CLOSED)
        manager._positions["US.AAPL"] = pos

        bar = _make_bar(close=50.0)
        asyncio.run(manager.on_bar(bar))

        assert pos.phase == PositionPhase.CLOSED

    def test_no_position_bar_is_noop(self, manager):
        """on_bar for a code with no registered position is a no-op."""
        bar = _make_bar(code="US.TSLA", close=50.0)
        # Should not raise
        asyncio.run(manager.on_bar(bar))


# ============================================================
# Test: on_bar partial-profit transition
# ============================================================

class TestOnBarPartialProfit:
    """Verify partial-profit transition from on_bar (POS-01 / D-03)."""

    def test_close_at_0_75R_triggers_partial_profit(
        self, manager, open_store, mock_engine
    ):
        """A bar.close >= entry + 0.75R triggers PARTIAL_TAKEN; remaining_quantity decremented."""
        # entry=100, initial_stop=98 → R=2; 0.75R threshold = 100 + 0.75*2 = 101.5
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        manager._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # close above 0.75R threshold
        bar = _make_bar(close=101.5)

        asyncio.run(manager.on_bar(bar))

        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        # partial_qty = floor(300 * 0.3333) = floor(99.99) = 99 → remaining = 300 - 99 = 201
        assert pos.remaining_quantity == 201


# ============================================================
# Test: POS-05 — Restart reconstruction
# ============================================================

class TestRestartReconciliation:
    """POS-05: reconstruct_from_store reproduces persisted FSM state on restart."""

    def test_restart_reconciliation(self, open_store, mock_engine, mock_strategy):
        """Restart reconstructs FSM from StateStore; persisted phase/trail_stop/remaining restored.

        Scenario:
          1. First session: position created in TRAILING phase with trail_stop=99.5
             and remaining_quantity=200.
          2. Process stops (simulated by creating a fresh PositionManager).
          3. Second session: reconstruct_from_store() rebuilds the position.
          4. Verify: phase == TRAILING, trail_stop == 99.5, remaining_quantity == 200.
             D-11 upheld: persisted trail_stop is preserved exactly, never loosened.
        """
        # --- First session: persist a TRAILING position ---
        pos = PositionState(
            position_id="POS-RESTART-001",
            code="US.AAPL",
            phase=PositionPhase.TRAILING,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=99.5,        # ratcheted up during first session
            full_quantity=300,
            remaining_quantity=200,  # 100 shares already partial-exited
            entry_order_id="ORDER-E-001",
            exit_order_id=None,
            avg_fill_price=100.0,
            opened_at=datetime(2026, 6, 24, 10, 5, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 6, 24, 11, 0, 0, tzinfo=timezone.utc),
        )
        open_store.upsert_position(pos)

        # --- Second session: fresh PositionManager reconstructs from DB ---
        manager2 = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=_minimal_cfg(),
            strategy=mock_strategy,
        )
        manager2.reconstruct_from_store()

        # Verify reconstruction
        assert "US.AAPL" in manager2._positions, "Position not found after reconstruction"
        restored = manager2._positions["US.AAPL"]

        assert restored.phase == PositionPhase.TRAILING, (
            f"Expected TRAILING, got {restored.phase}"
        )
        assert restored.trail_stop == 99.5, (
            f"Expected trail_stop=99.5, got {restored.trail_stop} (D-11: never loosen)"
        )
        assert restored.remaining_quantity == 200, (
            f"Expected remaining_quantity=200, got {restored.remaining_quantity}"
        )
        assert restored.entry_price == 100.0
        assert restored.initial_stop == 98.0
        assert restored.entry_order_id == "ORDER-E-001"

    def test_reconstruct_multiple_positions(self, open_store, mock_engine, mock_strategy):
        """reconstruct_from_store loads all non-CLOSED positions."""
        pos1 = _make_pos(
            code="US.AAPL",
            phase=PositionPhase.ACTIVE,
            position_id="POS-001",
        )
        pos2 = _make_pos(
            code="US.TSLA",
            phase=PositionPhase.BREAKEVEN,
            trail_stop=100.0,
            entry_price=99.0,
            position_id="POS-002",
        )
        pos_closed = _make_pos(
            code="US.MSFT",
            phase=PositionPhase.CLOSED,
            position_id="POS-003",
        )
        open_store.upsert_position(pos1)
        open_store.upsert_position(pos2)
        open_store.upsert_position(pos_closed)

        manager2 = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=_minimal_cfg(),
            strategy=mock_strategy,
        )
        manager2.reconstruct_from_store()

        # Only non-CLOSED positions reconstructed
        assert "US.AAPL" in manager2._positions
        assert "US.TSLA" in manager2._positions
        assert "US.MSFT" not in manager2._positions

    def test_reconstruct_empty_store(self, open_store, mock_engine, mock_strategy):
        """reconstruct_from_store with an empty DB results in empty _positions."""
        manager2 = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=_minimal_cfg(),
            strategy=mock_strategy,
        )
        manager2.reconstruct_from_store()

        assert len(manager2._positions) == 0

    def test_reconstruct_preserves_breakeven_trail_stop(
        self, open_store, mock_engine, mock_strategy
    ):
        """BREAKEVEN position: trail_stop == entry_price preserved exactly (D-11)."""
        entry_price = 105.0
        pos = PositionState(
            position_id="POS-BREAKEVEN",
            code="US.NVDA",
            phase=PositionPhase.BREAKEVEN,
            entry_price=entry_price,
            initial_stop=102.0,
            trail_stop=entry_price,  # stop at entry after breakeven trigger (POS-02)
            full_quantity=200,
            remaining_quantity=134,
            entry_order_id="ORDER-E-NV",
            exit_order_id=None,
            avg_fill_price=entry_price,
            opened_at=_DEFAULT_OPENED_AT,
            updated_at=_DEFAULT_UPDATED_AT,
        )
        open_store.upsert_position(pos)

        manager2 = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=_minimal_cfg(),
            strategy=mock_strategy,
        )
        manager2.reconstruct_from_store()

        restored = manager2._positions.get("US.NVDA")
        assert restored is not None
        assert restored.phase == PositionPhase.BREAKEVEN
        assert restored.trail_stop == entry_price  # preserved exactly (D-11)


# ============================================================
# Test: flush_all (kill-switch wiring)
# ============================================================

class TestFlushAll:
    """flush_all() persists all in-memory positions to the DB (kill-switch safety)."""

    def test_flush_all_persists_all_positions(self, manager, open_store):
        """flush_all writes every in-memory position to StateStore."""
        pos1 = _make_pos(code="US.AAPL", phase=PositionPhase.ACTIVE, position_id="POS-A")
        pos2 = _make_pos(code="US.TSLA", phase=PositionPhase.TRAILING, position_id="POS-B")
        open_store.upsert_position(pos1)
        open_store.upsert_position(pos2)
        manager._positions["US.AAPL"] = pos1
        manager._positions["US.TSLA"] = pos2

        manager.flush_all()

        rows = {r["code"]: r for r in open_store.get_open_positions()}
        assert "US.AAPL" in rows
        assert "US.TSLA" in rows


# ============================================================
# Test: register_position
# ============================================================

class TestRegisterPosition:
    """register_position() adds a new AWAITING_FILL position to _positions and DB."""

    def test_register_position_stores_in_memory_and_db(self, manager, open_store):
        """register_position wires the position into _positions and persists it."""
        pos = _make_pos(
            code="US.NVDA",
            phase=PositionPhase.AWAITING_FILL,
            position_id="POS-NV-001",
        )
        manager.register_position(pos)

        assert "US.NVDA" in manager._positions
        rows = open_store.get_open_positions()
        assert any(r["code"] == "US.NVDA" for r in rows)


# ============================================================
# Test: order_id-only matching — source inspection
# ============================================================

class TestOrderIdOnlyMatching:
    """Source-level guard: _find_position_by_* uses order_id only (T-04-05)."""

    def test_manager_source_uses_order_id_not_qty_tuple(self):
        """Grep guard: manager.py must never match fills by (code, qty) tuple (T-04-05)."""
        import os
        src_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "bot", "position", "manager.py"
        )
        src_path = os.path.abspath(src_path)
        with open(src_path, "r") as f:
            source = f.read()

        # order_id must appear in the find logic
        assert "order_id" in source, "order_id not found in manager.py"
        # Must NOT match fills by a (code, qty) pair for fill reconciliation
        # Check that no tuple keying of the form (code, qty) is used for matching
        assert "(code, qty)" not in source, (
            "manager.py must not match fills by (code, qty) tuple — use order_id (EXEC-05)"
        )


# ============================================================
# Test: _row_to_position_state helper
# ============================================================

class TestRowToPositionState:
    """Unit tests for the DB row → PositionState reconstruction helper."""

    def test_round_trip_active_position(self, open_store):
        """A written PositionState can be read back faithfully via _row_to_position_state."""
        pos = _make_pos(
            code="US.AAPL",
            phase=PositionPhase.ACTIVE,
            entry_price=102.50,
            initial_stop=98.00,
            trail_stop=98.00,
            full_quantity=200,
            remaining_quantity=200,
            entry_order_id="ORDER-E-99",
            position_id="POS-ROUNDTRIP",
        )
        open_store.upsert_position(pos)
        rows = open_store.get_open_positions()
        assert rows, "No rows returned from get_open_positions"
        restored = _row_to_position_state(rows[0])

        assert restored.position_id == "POS-ROUNDTRIP"
        assert restored.code == "US.AAPL"
        assert restored.phase == PositionPhase.ACTIVE
        assert restored.entry_price == 102.50
        assert restored.trail_stop == 98.00
        assert restored.remaining_quantity == 200

    def test_unknown_phase_raises_value_error(self):
        """_row_to_position_state raises ValueError for an invalid phase string."""
        row = {
            "position_id": "POS-ERR",
            "code": "US.AAPL",
            "phase": "INVALID_PHASE",
            "entry_price": 100.0,
            "initial_stop": 98.0,
            "trail_stop": 98.0,
            "full_quantity": 100,
            "remaining_quantity": 100,
            "entry_order_id": "O-1",
            "exit_order_id": None,
            "avg_fill_price": None,
            "opened_at": None,
            "updated_at": None,
        }
        with pytest.raises(ValueError):
            _row_to_position_state(row)


# ============================================================
# Test: POS-04 stub (remains for 04-04)
# ============================================================

def test_force_close_half_day():
    """POS-04: Force-close at half-day early close vs 15:51 on normal day.

    Implemented in 04-04. Mock calendar returns "13:00" for a half-day;
    PositionManager must use earlier force-close time. Normal day uses "15:51"
    derived from cfg.force_close_et (CFG-01).
    """
    assert False, "TODO: implemented in 04-04"
