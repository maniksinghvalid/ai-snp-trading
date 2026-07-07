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

    def test_exit_fill_writes_trade_row_to_store(self, manager, open_store):
        """Finding 2.3 regression: a full exit fill (remaining_quantity==0) must
        write exactly one row to the trades table (entry_price/exit_price match),
        so get_closed_trades/get_daily_trade_stats reflect real trades. A PARTIAL
        exit (remaining_quantity>0) must write NO trades row.
        """
        # Partial exit: 100 of 300 — no trade row expected
        pos_partial = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=300,
            exit_order_id="ORDER-EXIT-PARTIAL",
            position_id="POS-PARTIAL",
        )
        manager._positions["US.AAPL"] = pos_partial
        open_store.upsert_position(pos_partial)

        partial_fill = _make_fill(
            order_id="ORDER-EXIT-PARTIAL",
            intent_id="",
            code="US.AAPL",
            filled_qty=100,
            avg_fill_price=101.0,
            is_entry=False,
        )
        manager.on_fill(partial_fill)

        assert pos_partial.remaining_quantity == 200
        assert open_store.get_closed_trades("2026-06-24") == [], (
            "A partial exit must not write a trades row (schema requires a full close)"
        )

        # Full exit: exactly remaining_quantity — must write one trade row
        pos_full = _make_pos(
            code="US.TSLA",
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=100,
            full_quantity=100,
            exit_order_id="ORDER-EXIT-FULL",
            position_id="POS-FULL",
        )
        manager._positions["US.TSLA"] = pos_full
        open_store.upsert_position(pos_full)

        full_fill = _make_fill(
            order_id="ORDER-EXIT-FULL",
            intent_id="",
            code="US.TSLA",
            filled_qty=100,
            avg_fill_price=103.0,
            is_entry=False,
            fill_time=datetime(2026, 6, 24, 15, 0, 0, tzinfo=timezone.utc),
        )
        manager.on_fill(full_fill)

        assert pos_full.remaining_quantity == 0
        assert pos_full.phase == PositionPhase.CLOSED

        rows = open_store.get_closed_trades("2026-06-24")
        assert len(rows) == 1, f"Expected exactly one closed trade row, got {rows!r}"
        row = rows[0]
        assert row["position_id"] == "POS-FULL"
        assert row["code"] == "US.TSLA"
        assert row["entry_price"] == 100.0
        assert row["exit_price"] == 103.0
        assert row["quantity"] == 100

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
        self, open_store, mock_strategy
    ):
        """A bar.close <= trail_stop triggers a STOP_OUT transition and persists CLOSED.

        Uses a full-fill engine (manage_exit returns full remaining_quantity) to assert
        that a successful stop-out leaves phase==CLOSED and DB row CLOSED.
        """
        # Full-fill engine so the stop-out completes
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=300)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Close AT the trail_stop — triggers stop-out (D-02: close <= trail_stop)
        bar = _make_bar(close=97.5)

        asyncio.run(mgr.on_bar(bar))

        # FSM transitioned to CLOSED (full fill)
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

    def test_zero_fill_stop_out_reprotects(
        self, open_store, mock_strategy
    ):
        """CR-01: zero fill from manage_exit reverts position to managed phase, NOT CLOSED.

        Stop triggers (close <= trail_stop). manage_exit returns 0 (broker reject).
        After on_bar:
          - remaining_quantity unchanged (> 0)
          - phase reverted to a managed (non-CLOSED) phase (prev_phase)
          - DB row NOT CLOSED
          - stop_out_incomplete logged
          - A SECOND on_bar with close still <= trail_stop calls manage_exit again (retry).
        """
        import structlog.testing as stl_testing

        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=0)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=97.5)  # triggers stop-out

        with stl_testing.capture_logs() as cap:
            asyncio.run(mgr.on_bar(bar))

        # remaining_quantity unchanged (broker didn't sell anything)
        assert pos.remaining_quantity == 300, (
            f"remaining_quantity must stay 300 on zero fill, got {pos.remaining_quantity}"
        )
        # phase must NOT be CLOSED — reverted to managed
        assert pos.phase != PositionPhase.CLOSED, (
            f"phase must not be CLOSED on zero fill stop-out, got {pos.phase}"
        )
        # DB must NOT show CLOSED
        rows = open_store.conn.execute(
            "SELECT phase FROM positions WHERE code='US.AAPL'"
        ).fetchall()
        assert not any(r[0] == "CLOSED" for r in rows), (
            f"DB must not persist CLOSED on zero fill; got rows: {rows}"
        )
        # stop_out_incomplete must be logged
        keys = [e.get("event") for e in cap]
        assert "stop_out_incomplete" in keys, (
            f"Expected 'stop_out_incomplete' log on zero fill, got: {keys}"
        )

        # Second bar still triggers — manage_exit called again (retry proof)
        call_count_before = engine.manage_exit.call_count
        asyncio.run(mgr.on_bar(bar))
        assert engine.manage_exit.call_count > call_count_before, (
            "manage_exit must be called again on the second triggering bar (retry)"
        )

    def test_short_fill_stop_out_reprotects(
        self, open_store, mock_strategy
    ):
        """WR-02 + CR-01: short fill credits filled shares; remaining and phase reverted.

        Stop triggers; manage_exit returns 120 (short of 300 requested).
        After on_bar:
          - remaining_quantity == 180 (300 - 120 credited)
          - phase reverted to managed (not CLOSED)
          - stop_out_incomplete logged
          - A second triggering bar re-attempts exit of remaining 180.
        """
        import structlog.testing as stl_testing

        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=120)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=97.5)

        with stl_testing.capture_logs() as cap:
            asyncio.run(mgr.on_bar(bar))

        assert pos.remaining_quantity == 180, (
            f"Expected 180 (300-120 credited), got {pos.remaining_quantity}"
        )
        assert pos.phase != PositionPhase.CLOSED, (
            f"phase must not be CLOSED on short fill, got {pos.phase}"
        )
        keys = [e.get("event") for e in cap]
        assert "stop_out_incomplete" in keys, (
            f"Expected 'stop_out_incomplete' log on short fill, got: {keys}"
        )

        # Second bar re-attempts exit
        call_count_before = engine.manage_exit.call_count
        asyncio.run(mgr.on_bar(bar))
        assert engine.manage_exit.call_count > call_count_before, (
            "manage_exit must retry on next triggering bar after short fill"
        )

    def test_full_fill_stop_out_closes(
        self, open_store, mock_strategy
    ):
        """Full fill: remaining_quantity == 0, phase == CLOSED, alert fires once.

        stop triggers; manage_exit returns 300 (full fill). After on_bar:
          - remaining_quantity == 0
          - phase == CLOSED
          - stop_out_filled persisted
          - exactly one exit alert
        """
        alert_calls = []
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=300)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=lambda code, reason, r: alert_calls.append((code, reason, r)),
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            trail_stop=98.0,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=97.5)
        asyncio.run(mgr.on_bar(bar))

        assert pos.remaining_quantity == 0, (
            f"Expected 0 after full fill, got {pos.remaining_quantity}"
        )
        assert pos.phase == PositionPhase.CLOSED, (
            f"Expected CLOSED after full fill, got {pos.phase}"
        )
        assert len(alert_calls) == 1, (
            f"Expected exactly 1 exit alert on full fill, got {len(alert_calls)}"
        )


# ============================================================
# Test: on_bar partial-profit transition
# ============================================================

class TestOnBarPartialProfit:
    """Verify partial-profit transition from on_bar (POS-01 / D-03)."""

    def test_close_at_0_75R_triggers_partial_profit(
        self, manager, open_store, mock_engine
    ):
        """A bar.close >= entry + 0.75R triggers PARTIAL_TAKEN; remaining_quantity decremented.

        With mock manage_exit returning 0 (no fill), remaining_quantity stays at 300.
        The handler owns the decrement (handler-owns convention); with zero fill nothing
        is decremented.
        """
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
        # mock manage_exit returns 0 → handler decrement is 0 → remaining stays 300
        assert pos.remaining_quantity == 300

    def test_partial_fill_keeps_remaining_at_broker_truth(
        self, open_store, mock_engine, mock_strategy
    ):
        """NON-ZERO fill: remaining_quantity == broker truth (300 - 99 = 201).

        CR-02 regression test. entry=100, stop=98, R=2; partial_qty = floor(300*0.3333)=99.
        manage_exit returns 99 (full requested qty filled). Handler applies exactly ONE
        decrement of 99, leaving remaining_quantity == 201 (broker truth).
        """
        # Wire manage_exit to return 99 (non-zero real fill)
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=99)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)
        asyncio.run(mgr.on_bar(bar))

        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        # Broker sold 99 of 300 → broker holds 201; bot must agree
        assert pos.remaining_quantity == 201, (
            f"Expected 201 (broker truth), got {pos.remaining_quantity}"
        )

    def test_partial_short_fill_restores_shortfall(
        self, open_store, mock_engine, mock_strategy
    ):
        """SHORT fill: remaining_quantity == 300 - 40 = 260; partial_short_fill logged.

        manage_exit returns 40 (short of the requested 99). Handler credits only the 40
        actually filled, leaving remaining_quantity == 260.
        """
        import structlog.testing as stl_testing

        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=40)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)
        with stl_testing.capture_logs() as cap:
            asyncio.run(mgr.on_bar(bar))

        assert pos.remaining_quantity == 260, (
            f"Expected 260 (300 - 40 filled), got {pos.remaining_quantity}"
        )
        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        keys = [e.get("event") for e in cap]
        assert "partial_short_fill" in keys, (
            f"Expected 'partial_short_fill' log, got keys: {keys}"
        )

    def test_partial_zero_fill_keeps_full_remaining(
        self, open_store, mock_engine, mock_strategy
    ):
        """ZERO fill: remaining_quantity stays at 300; phase is PARTIAL_TAKEN.

        manage_exit returns 0 (broker reject). Handler decrement is 0, so remaining
        stays at 300.
        """
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=0)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)
        asyncio.run(mgr.on_bar(bar))

        assert pos.remaining_quantity == 300, (
            f"Expected 300 (nothing sold on zero fill), got {pos.remaining_quantity}"
        )
        assert pos.phase == PositionPhase.PARTIAL_TAKEN

    def test_partial_drives_remaining_to_zero_marks_closed(
        self, open_store, mock_engine, mock_strategy
    ):
        """WR-01: partial fill that zeroes remaining_quantity marks position CLOSED.

        Position has remaining_quantity=99; partial_qty=floor(99*0.3333)=33.
        But we inject a fill of 99 (full remaining) to drive remaining to 0.
        """
        engine = MagicMock()
        # manage_exit returns the full remaining quantity
        engine.manage_exit = AsyncMock(return_value=99)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        # Use remaining_quantity=99 so a fill of 99 drives remaining to 0
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=99,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)
        asyncio.run(mgr.on_bar(bar))

        assert pos.remaining_quantity == 0, (
            f"Expected 0 after full fill, got {pos.remaining_quantity}"
        )
        assert pos.phase == PositionPhase.CLOSED, (
            f"Expected CLOSED when remaining hits 0, got {pos.phase}"
        )


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
# Test: POS-05 + D-09/D-10/D-11 — startup_reconcile via MoomooGateway (04-04)
# ============================================================

def test_restart_reconciliation():
    """POS-05/D-09/D-10/D-11: startup_reconcile makes broker truth win.

    Four sub-scenarios in one comprehensive test:

    (a) D-09 close: StateStore has a position the broker says is flat →
        reconcile marks it CLOSED in the DB.

    (b) D-09 adopt-qty: StateStore has qty=300 but broker reports qty=250 →
        reconcile adopts the broker qty (remaining_quantity updated to 250).

    (c) D-10 orphan: broker has "US.GOOG" with no StateStore record → reconcile
        inserts an ACTIVE position with avg_cost as entry_price and a computed
        stop, and emits an orphan_adopted audit entry.

    (d) D-11 never-loosen: a known position's post-restart swing-low is lower
        than the persisted trail_stop → the stop must remain unchanged (never
        loosened). The stop is persisted as-is; only on_bar raises it later.
    """
    import asyncio
    import pandas as pd
    from unittest.mock import AsyncMock, MagicMock, patch

    from bot.gateway.gateway import MoomooGateway, GatewayConfig
    from bot.state.store import StateStore

    # ---- Setup in-memory StateStore ----
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".db")
    store = StateStore(db_path=tmp).open()

    # ---- Seed StateStore positions ----
    # (a) Position that broker says is flat
    pos_a_id = "POS-A-CLOSED"
    store.conn.execute(
        """INSERT INTO positions
           (position_id, code, phase, entry_price, initial_stop, trail_stop,
            full_quantity, remaining_quantity, entry_order_id, avg_fill_price,
            opened_at, updated_at)
           VALUES (?, 'US.AAPL', 'ACTIVE', 180.0, 178.0, 178.0,
                   300, 300, 'ORDER-A', 180.0,
                   '2026-06-24T09:30:00+00:00', '2026-06-24T09:30:00+00:00')""",
        (pos_a_id,),
    )
    # (b) Position with qty mismatch (300 in DB, broker has 250)
    pos_b_id = "POS-B-QTYMATCH"
    store.conn.execute(
        """INSERT INTO positions
           (position_id, code, phase, entry_price, initial_stop, trail_stop,
            full_quantity, remaining_quantity, entry_order_id, avg_fill_price,
            opened_at, updated_at)
           VALUES (?, 'US.TSLA', 'ACTIVE', 200.0, 197.0, 199.0,
                   300, 300, 'ORDER-B', 200.0,
                   '2026-06-24T09:30:00+00:00', '2026-06-24T09:30:00+00:00')""",
        (pos_b_id,),
    )
    # (d) Known position with persisted trail_stop=99.5 — broker still open with 200 qty
    pos_d_id = "POS-D-NEVER-LOOSEN"
    store.conn.execute(
        """INSERT INTO positions
           (position_id, code, phase, entry_price, initial_stop, trail_stop,
            full_quantity, remaining_quantity, entry_order_id, avg_fill_price,
            opened_at, updated_at)
           VALUES (?, 'US.NVDA', 'TRAILING', 100.0, 98.0, 99.5,
                   200, 200, 'ORDER-D', 100.0,
                   '2026-06-24T09:30:00+00:00', '2026-06-24T09:30:00+00:00')""",
        (pos_d_id,),
    )
    store.conn.commit()

    # ---- Seed a PENDING intent for US.GOOG (SAFE-OG-01 crash-recovery) ----
    # startup_reconcile now enforces bot-ownership: an orphan is only adopted
    # when has_pending_intent(code) is True. This reflects the real crash-recovery
    # scenario: the bot wrote the pending_intent BEFORE placing the order, crashed,
    # and the position row was never persisted. On restart, the intent is still
    # PENDING → adoption proceeds (D-10).
    store.insert_pending_intent(
        "INTENT-GOOG-D10-001", "US.GOOG", 175.0, 168.3, 50,
        "2026-06-24T09:30:00+00:00",
    )

    # ---- Build broker positions DataFrame ----
    # US.AAPL is ABSENT (broker says flat → D-09 close)
    # US.TSLA has qty=250 (differs from stored 300 → D-09 adopt-qty)
    # US.GOOG is a bot-owned orphan with PENDING intent (crash-recovery → D-10)
    # US.NVDA is present with qty=200 (same as stored → D-11 never-loosen)
    broker_df = pd.DataFrame([
        {"code": "US.TSLA", "qty": 250, "average_cost": 200.0},
        {"code": "US.GOOG", "qty": 50,  "average_cost": 175.0},
        {"code": "US.NVDA", "qty": 200, "average_cost": 100.0},
    ])

    # ---- Build a patched MoomooGateway that never connects to OpenD ----
    cfg = GatewayConfig()
    gw = object.__new__(MoomooGateway)
    gw.cfg = cfg
    gw._quote_ctx = None
    gw._trade_ctx = None

    # Mock get_positions to return the broker DataFrame
    gw.get_positions = AsyncMock(return_value=(0, broker_df))
    # Mock get_market_snapshot so _derive_lod_for_orphan gets a plausible LOD
    snapshot_df = pd.DataFrame([{
        "code": "US.GOOG",
        "low_price": 170.0,
        "last_price": 176.0,
        "ask_price": 177.0,
        "bid_price": 175.5,
    }])
    gw.get_market_snapshot = AsyncMock(return_value=(0, snapshot_df))
    gw.subscribe = AsyncMock()

    # Patch append_audit to capture events without writing to disk
    # append_audit is imported locally in startup_reconcile from bot.safety.audit_log,
    # so we patch it at the source module (bot.safety.audit_log.append_audit).
    audit_events = []

    with patch("bot.safety.audit_log.append_audit", side_effect=audit_events.append):
        asyncio.run(gw.startup_reconcile(store, manager=None))

    # ---- (a) D-09: US.AAPL not in broker → must be CLOSED in DB ----
    row_a = store.conn.execute(
        "SELECT phase FROM positions WHERE position_id=?", (pos_a_id,)
    ).fetchone()
    assert row_a is not None
    assert row_a[0] == "CLOSED", (
        f"D-09: US.AAPL absent from broker must be CLOSED in DB, got {row_a[0]!r}"
    )

    # ---- (b) D-09: US.TSLA qty mismatch → remaining_quantity updated to 250 ----
    row_b = store.conn.execute(
        "SELECT remaining_quantity FROM positions WHERE position_id=?", (pos_b_id,)
    ).fetchone()
    assert row_b is not None
    assert row_b[0] == 250, (
        f"D-09: US.TSLA qty must be adopted from broker (250), got {row_b[0]}"
    )

    # ---- (c) D-10: US.GOOG orphan → ACTIVE row inserted with stop and audit ----
    import sqlite3 as _sqlite3
    store.conn.row_factory = _sqlite3.Row
    orphan_rows = store.conn.execute(
        "SELECT * FROM positions WHERE code='US.GOOG'"
    ).fetchall()
    store.conn.row_factory = None
    orphan_dicts = [dict(r) for r in orphan_rows]
    assert len(orphan_dicts) == 1, (
        f"D-10: orphan US.GOOG must be inserted into StateStore (got {len(orphan_dicts)} rows)"
    )
    orphan = orphan_dicts[0]
    assert orphan["phase"] == "ACTIVE", (
        f"D-10: orphan must be inserted as ACTIVE, got {orphan['phase']!r}"
    )
    assert orphan["entry_price"] == 175.0, (
        f"D-10: orphan entry_price must be broker avg_cost (175.0), got {orphan['entry_price']}"
    )
    # Stop must be below LOD (170.0 * 0.99 = 168.3)
    assert orphan["trail_stop"] < 170.0, (
        f"D-10: orphan stop must be below LOD (170.0), got {orphan['trail_stop']}"
    )
    assert orphan["trail_stop"] > 0.0, (
        f"D-10: orphan stop must be > 0.0, got {orphan['trail_stop']}"
    )
    # Confirm orphan_adopted audit entry was emitted
    orphan_audits = [e for e in audit_events if e.get("event") == "orphan_adopted"]
    assert len(orphan_audits) == 1, (
        f"D-10: expected 1 orphan_adopted audit entry, got {len(orphan_audits)}"
    )
    assert orphan_audits[0]["code"] == "US.GOOG"

    # ---- (d) D-11: US.NVDA known — trail_stop must NOT be loosened ----
    row_d = store.conn.execute(
        "SELECT trail_stop, remaining_quantity FROM positions WHERE position_id=?",
        (pos_d_id,),
    ).fetchone()
    assert row_d is not None
    assert row_d[0] == 99.5, (
        f"D-11: US.NVDA trail_stop must remain 99.5 (never loosened), got {row_d[0]}"
    )
    assert row_d[1] == 200, (
        f"D-11: US.NVDA remaining_quantity unchanged (broker matches), got {row_d[1]}"
    )

    # Cleanup
    store.close()
    try:
        os.unlink(tmp)
    except Exception:
        pass


# ============================================================
# Test: POS-04 — Force-close at calendar-aware time (04-04)
# ============================================================

def test_force_close_half_day():
    """POS-04: Force-close at half-day early close vs 15:51 on normal day.

    Tests:
      (a) get_force_close_time_et with mocked get_market_close_et returning "13:00"
          → result == datetime.time(12, 51) (12:51 ET on half-day).
      (b) get_force_close_time_et with mocked get_market_close_et returning "16:00"
          → result == datetime.time(15, 51) (15:51 ET on normal day).
      (c) force_close_all() with now_et mocked past the force-close time:
          calls engine.manage_exit for each non-CLOSED position with SELL + force_close params.
          No market order requested (D-08/EXEC-02).
    """
    import asyncio
    import datetime
    from unittest.mock import AsyncMock, MagicMock, patch, call

    from bot.position.manager import PositionManager, get_force_close_time_et
    from bot.position.state import PositionPhase, PositionState

    # ---- (a) Half-day: get_market_close_et returns "13:00" → force-close = 12:51 ----
    with patch("bot.position.manager.get_market_close_et", return_value="13:00"):
        result_half = get_force_close_time_et(datetime.date(2026, 6, 24))
    assert result_half == datetime.time(12, 51), (
        f"POS-04 half-day: expected 12:51, got {result_half}"
    )

    # ---- (b) Normal day: get_market_close_et returns "16:00" → force-close = 15:51 ----
    with patch("bot.position.manager.get_market_close_et", return_value="16:00"):
        result_normal = get_force_close_time_et(datetime.date(2026, 6, 24))
    assert result_normal == datetime.time(15, 51), (
        f"POS-04 normal day: expected 15:51, got {result_normal}"
    )

    # ---- (c) force_close_all calls engine.manage_exit for each non-CLOSED position ----
    mock_engine = MagicMock()
    mock_engine.manage_exit = AsyncMock(return_value=0)
    mock_store = MagicMock()
    mock_store.upsert_position = MagicMock()

    cfg = MagicMock()
    cfg.force_close_escalation_step_usd = 0.20
    cfg.force_close_escalation_cadence_seconds = 0.01
    cfg.exit_ttl_seconds = 10.0
    cfg.exit_limit_buffer_usd = 0.05

    mock_strategy = MagicMock()

    manager = PositionManager(
        store=mock_store,
        engine=mock_engine,
        cfg=cfg,
        strategy=mock_strategy,
    )

    # Add two open positions and one CLOSED position
    pos_open1 = PositionState(
        position_id="POS-FC-1",
        code="US.AAPL",
        phase=PositionPhase.ACTIVE,
        entry_price=180.0,
        initial_stop=178.0,
        trail_stop=178.0,
        full_quantity=100,
        remaining_quantity=100,
        entry_order_id="O-FC-1",
        exit_order_id=None,
        avg_fill_price=180.0,
        opened_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
    )
    pos_open2 = PositionState(
        position_id="POS-FC-2",
        code="US.TSLA",
        phase=PositionPhase.TRAILING,
        entry_price=200.0,
        initial_stop=197.0,
        trail_stop=199.0,
        full_quantity=50,
        remaining_quantity=50,
        entry_order_id="O-FC-2",
        exit_order_id=None,
        avg_fill_price=200.0,
        opened_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
    )
    pos_closed = PositionState(
        position_id="POS-FC-3",
        code="US.MSFT",
        phase=PositionPhase.CLOSED,
        entry_price=300.0,
        initial_stop=296.0,
        trail_stop=296.0,
        full_quantity=30,
        remaining_quantity=0,
        entry_order_id="O-FC-3",
        exit_order_id=None,
        avg_fill_price=300.0,
        opened_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 6, 24, 9, 30, 0, tzinfo=datetime.timezone.utc),
    )
    manager._positions["US.AAPL"] = pos_open1
    manager._positions["US.TSLA"] = pos_open2
    manager._positions["US.MSFT"] = pos_closed

    # Mock now_et to be past the force-close time (15:52 ET > 15:51)
    mock_now = datetime.datetime(2026, 6, 24, 15, 52, 0,
                                 tzinfo=datetime.timezone.utc)

    with patch("bot.position.manager.get_market_close_et", return_value="16:00"), \
         patch("bot.position.manager.now_et", return_value=mock_now):
        asyncio.run(manager.force_close_all(datetime.date(2026, 6, 24)))

    # manage_exit must have been called for each non-CLOSED position
    assert mock_engine.manage_exit.await_count == 2, (
        f"POS-04: manage_exit must be called for 2 open positions, "
        f"got {mock_engine.manage_exit.await_count}"
    )

    # Check that manage_exit was called with SELL side and force_close_* params
    for call_args in mock_engine.manage_exit.call_args_list:
        kwargs = call_args.kwargs if call_args.kwargs else {}
        args = call_args.args

        # code is 1st positional arg, qty 2nd, side 3rd
        called_code = args[0] if args else kwargs.get("code", "")
        assert called_code in ("US.AAPL", "US.TSLA"), (
            f"POS-04: manage_exit called for unexpected code {called_code!r}"
        )

        # side must be TrdSide.SELL — expressed as a sentinel (lazy import)
        # In test env without moomoo-api, side sentinel is truthy but not MARKET
        # Verify no "MARKET" in the side argument (D-08/EXEC-02)
        side_arg = args[2] if len(args) > 2 else kwargs.get("side", "")
        assert "MARKET" not in str(side_arg), (
            f"D-08: force_close must never use a market order, got side={side_arg!r}"
        )

    # CLOSED position must NOT be included in force-close
    closed_calls = [
        c for c in mock_engine.manage_exit.call_args_list
        if (c.args[0] if c.args else c.kwargs.get("code", "")) == "US.MSFT"
    ]
    assert len(closed_calls) == 0, (
        "POS-04: manage_exit must NOT be called for already-CLOSED positions"
    )


# ============================================================
# Test: Task 1 — pending_exit_reason recorded at FSM trigger points (ALERT-02)
# ============================================================

class TestPendingExitReason:
    """Verify pending_exit_reason is recorded on PositionState at FSM trigger points.

    These tests assert the FIELD value set by the manager's trigger methods.
    Alert-dispatch assertions are in TestExitAlertRealReason (Task 2).
    """

    def test_stop_out_records_stop_out_reason(
        self, open_store, mock_strategy
    ):
        """ACTIVE phase → close <= trail_stop → pending_exit_reason == 'stop_out'.

        Uses a full-fill engine (manage_exit returns 300) so the stop-out completes
        and the position transitions to CLOSED.
        """
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=300)
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Close below trail_stop triggers stop_out from ACTIVE phase
        bar = _make_bar(close=97.0)
        asyncio.run(mgr.on_bar(bar))

        assert pos.phase == PositionPhase.CLOSED
        assert pos.pending_exit_reason == "stop_out", (
            f"Expected 'stop_out' from ACTIVE phase stop, got {pos.pending_exit_reason!r}"
        )

    def test_trailing_stop_records_trail_stop_reason(
        self, open_store, mock_strategy
    ):
        """TRAILING phase → close <= trail_stop → pending_exit_reason == 'trail_stop'.

        Uses a full-fill engine (manage_exit returns 300).
        """
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=300)
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.TRAILING,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=99.5,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Close below trail_stop from TRAILING phase → trail_stop reason
        bar = _make_bar(close=99.0)
        asyncio.run(mgr.on_bar(bar))

        assert pos.phase == PositionPhase.CLOSED
        assert pos.pending_exit_reason == "trail_stop", (
            f"Expected 'trail_stop' from TRAILING phase stop, got {pos.pending_exit_reason!r}"
        )

    def test_breakeven_stop_records_breakeven_reason(
        self, open_store, mock_strategy
    ):
        """BREAKEVEN phase → close <= trail_stop → pending_exit_reason == 'breakeven'.

        Uses a full-fill engine (manage_exit returns 300).
        """
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=300)
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.BREAKEVEN,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=100.0,  # stop at entry (breakeven)
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Close at entry price (which is the trail_stop) → stop_out from BREAKEVEN
        bar = _make_bar(close=99.8)
        asyncio.run(mgr.on_bar(bar))

        assert pos.phase == PositionPhase.CLOSED
        assert pos.pending_exit_reason == "breakeven", (
            f"Expected 'breakeven' from BREAKEVEN phase stop, got {pos.pending_exit_reason!r}"
        )

    def test_partial_records_partial_reason(
        self, manager, open_store, mock_engine
    ):
        """ACTIVE phase → close >= 0.75R → pending_exit_reason == 'partial'."""
        # entry=100, initial_stop=98 → R=2; 0.75R = 1.5 → threshold = 101.5
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

        bar = _make_bar(close=101.5)
        asyncio.run(manager.on_bar(bar))

        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        assert pos.pending_exit_reason == "partial", (
            f"Expected 'partial' after scale-out trigger, got {pos.pending_exit_reason!r}"
        )

    def test_force_close_records_force_close_reason(
        self, open_store, mock_engine, mock_strategy
    ):
        """force_close_all on an open position records pending_exit_reason == 'force_close'."""
        import datetime
        from unittest.mock import patch

        cfg = _minimal_cfg()
        cfg.force_close_escalation_step_usd = 0.20
        cfg.force_close_escalation_cadence_seconds = 0.01
        cfg.exit_ttl_seconds = 10.0

        # Engine fully fills the position
        mock_engine.manage_exit = AsyncMock(return_value=100)

        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        mock_now = datetime.datetime(
            2026, 6, 24, 15, 52, 0, tzinfo=datetime.timezone.utc
        )
        with patch("bot.position.manager.get_market_close_et", return_value="16:00"), \
             patch("bot.position.manager.now_et", return_value=mock_now):
            asyncio.run(mgr.force_close_all(datetime.date(2026, 6, 24)))

        assert pos.pending_exit_reason == "force_close", (
            f"Expected 'force_close' after force_close_all, got {pos.pending_exit_reason!r}"
        )


# ============================================================
# Test: Task 2 — real reason threaded to on_exit_alert + partial fires alert (ALERT-02/04)
# ============================================================

class TestExitAlertRealReason:
    """Verify on_exit_alert receives the real exit reason (not hardcoded 'exit_fill').

    Also verifies: partial scale-out fires exactly one alert while remaining_quantity > 0,
    no double-fire on later full close, and callback exceptions are swallowed (ALERT-04).
    """

    def test_full_exit_fill_alerts_real_reason(self, open_store, mock_engine, mock_strategy):
        """Exit fill with pending_exit_reason set passes the real reason to on_exit_alert."""
        captured = []
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=lambda code, reason, r: captured.append((code, reason, r)),
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-001",
        )
        # Simulate that a stop_out was triggered — reason pre-recorded on pos
        pos.pending_exit_reason = "stop_out"
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-EXIT-001",
            intent_id="",
            filled_qty=100,
            avg_fill_price=97.5,
            is_entry=False,
        )
        mgr.on_fill(fill)

        assert pos.remaining_quantity == 0
        assert len(captured) == 1, f"Expected 1 alert call, got {len(captured)}"
        code, reason, r = captured[0]
        assert reason == "stop_out", (
            f"Expected 'stop_out' passed to on_exit_alert, got {reason!r}"
        )
        assert reason != "exit_fill", "on_exit_alert must NOT receive hardcoded 'exit_fill'"

    def test_full_exit_fill_falls_back_to_exit_fill_when_no_reason(
        self, open_store, mock_engine, mock_strategy
    ):
        """Exit fill with pending_exit_reason=None falls back to 'exit_fill' (defensive)."""
        captured = []
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=lambda code, reason, r: captured.append((code, reason, r)),
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-002",
        )
        # No pending_exit_reason — defensive fallback expected
        assert pos.pending_exit_reason is None
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-EXIT-002",
            intent_id="",
            filled_qty=100,
            avg_fill_price=100.0,
            is_entry=False,
        )
        mgr.on_fill(fill)

        assert len(captured) == 1
        _, reason, _ = captured[0]
        assert reason == "exit_fill", (
            f"Expected fallback to 'exit_fill' when no reason recorded, got {reason!r}"
        )

    def test_partial_scaleout_fires_exit_alert(
        self, open_store, mock_engine, mock_strategy
    ):
        """A partial scale-out via on_bar fires exactly one 'partial' alert while remaining > 0."""
        captured = []
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=lambda code, reason, r: captured.append((code, reason, r)),
        )

        # entry=100, initial_stop=98 → R=2; 0.75R=1.5 → threshold=101.5
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)
        asyncio.run(mgr.on_bar(bar))

        # Position is PARTIAL_TAKEN (remaining_quantity > 0)
        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        assert pos.remaining_quantity > 0

        # Alert must have fired exactly once with reason "partial"
        assert len(captured) == 1, (
            f"Expected 1 partial alert, got {len(captured)}: {captured}"
        )
        code, reason, r = captured[0]
        assert reason == "partial", (
            f"Expected 'partial' reason for scale-out alert, got {reason!r}"
        )

    def test_partial_does_not_double_alert_on_later_full_close(
        self, open_store, mock_engine, mock_strategy
    ):
        """Partial alert fires once at scale-out; later full-close fires its own single alert."""
        captured = []
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=lambda code, reason, r: captured.append((code, reason, r)),
        )

        # entry=100, initial_stop=98, R=2
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        # Step 1: partial at 0.75R → fires 1 "partial" alert
        bar_partial = _make_bar(close=101.5)
        asyncio.run(mgr.on_bar(bar_partial))

        assert len(captured) == 1
        assert captured[0][1] == "partial"

        # Step 2: simulate the full-close fill (remaining shares)
        remaining = pos.remaining_quantity  # should be 201 after partial
        assert remaining > 0, "remaining_quantity must be > 0 after partial"

        pos.exit_order_id = "ORDER-EXIT-FULL"
        open_store.upsert_position(pos)

        # Set the reason the stop-out would have recorded
        pos.pending_exit_reason = "stop_out"

        fill = _make_fill(
            order_id="ORDER-EXIT-FULL",
            intent_id="",
            filled_qty=remaining,
            avg_fill_price=97.5,
            is_entry=False,
        )
        mgr.on_fill(fill)

        # Must have exactly 2 total alerts: partial + stop_out (no double-fire)
        assert len(captured) == 2, (
            f"Expected 2 total alerts (partial + stop_out), got {len(captured)}: {captured}"
        )
        assert captured[1][1] == "stop_out", (
            f"Second alert reason should be 'stop_out', got {captured[1][1]!r}"
        )

    def test_exit_alert_callback_exception_is_swallowed_full_close(
        self, open_store, mock_engine, mock_strategy
    ):
        """on_exit_alert raising on full-close must not propagate — ALERT-04."""
        def _raising_callback(code, reason, r):
            raise RuntimeError("Telegram down!")

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=_raising_callback,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            remaining_quantity=100,
            exit_order_id="ORDER-EXIT-003",
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        fill = _make_fill(
            order_id="ORDER-EXIT-003",
            intent_id="",
            filled_qty=100,
            is_entry=False,
        )

        # Must NOT raise even though the callback throws
        try:
            mgr.on_fill(fill)
        except Exception as exc:
            pytest.fail(
                f"on_exit_alert exception propagated from on_fill: {exc} — ALERT-04 violated"
            )

        # Position was still closed despite the callback failure
        assert pos.remaining_quantity == 0
        assert pos.phase == PositionPhase.CLOSED

    def test_exit_alert_callback_exception_is_swallowed_on_partial(
        self, open_store, mock_engine, mock_strategy
    ):
        """on_exit_alert raising on partial scale-out must not propagate — ALERT-04."""
        call_count = [0]

        def _raising_callback(code, reason, r):
            call_count[0] += 1
            raise RuntimeError("Telegram down!")

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=_raising_callback,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            full_quantity=300,
            remaining_quantity=300,
        )
        mgr._positions["US.AAPL"] = pos
        open_store.upsert_position(pos)

        bar = _make_bar(close=101.5)

        # Must NOT raise even though the callback throws
        try:
            asyncio.run(mgr.on_bar(bar))
        except Exception as exc:
            pytest.fail(
                f"on_exit_alert exception propagated from partial on_bar: {exc} — ALERT-04 violated"
            )

        # Partial exit still happened despite callback failure
        assert pos.phase == PositionPhase.PARTIAL_TAKEN
        assert call_count[0] == 1, "Callback must have been attempted once"


# ============================================================
# Test: exit alert fires after manage_exit() returns filled_qty (D-01/D-02 / WARNING-01)
# ============================================================

def test_exit_alert_fires_after_manage_exit(tmp_state_db):
    """After D-01/D-02 fix: manage_exit() return value drives on_exit_alert (WARNING-01).

    With engine.manage_exit mocked to return filled_qty=100 (full fill), a stop-out
    (bar.close below trail_stop) must fire the on_exit_alert callback with (code, reason,
    r_multiple). The test asserts the D-01/D-02 contract: alert driven by the
    manage_exit() return value, not by exit_order_id push-match.

    Note: uses full fill (100 == remaining_quantity) so the full-fill path fires the alert.
    A short fill (filled_qty < qty) is tested by test_short_fill_stop_out_reprotects which
    asserts no alert fires on incomplete exits.
    """
    from bot.state.store import StateStore

    # Build mock engine whose manage_exit returns filled_qty=100 (full fill of 100 remaining)
    mock_engine = MagicMock()
    mock_engine.manage_exit = AsyncMock(return_value=100)

    mock_strategy = MagicMock()
    mock_strategy.compute_swing_low_2_2 = MagicMock(return_value=99.0)

    # Track on_exit_alert calls
    alert_calls = []

    def on_exit_alert_cb(code, reason, r_multiple):
        alert_calls.append((code, reason, r_multiple))

    store = StateStore(db_path=tmp_state_db).open()
    try:
        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
            on_exit_alert=on_exit_alert_cb,
        )

        # Position in ACTIVE phase; trail_stop=98.0 → bar.close=97.0 triggers stop-out
        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            avg_fill_price=100.0,
            full_quantity=100,
            remaining_quantity=100,
        )
        mgr._positions["US.AAPL"] = pos
        store.upsert_position(pos)

        # bar.close=97.0 is below trail_stop=98.0 — triggers stop-out path
        bar = _make_bar(close=97.0)

        # INTENDED RED: manage_exit() returns 10 but _place_exit_order() returns None
        # today, so the stop-out block sets pos.exit_order_id=None and never calls
        # on_exit_alert. The assertion below will fail until Plan 06.1-03 lands.
        asyncio.run(mgr.on_bar(bar))

        assert len(alert_calls) == 1, (
            f"on_exit_alert must be called once after stop-out fill; calls={alert_calls}"
        )
        code, reason, r_multiple = alert_calls[0]
        assert code == "US.AAPL", f"Expected code='US.AAPL', got {code!r}"
    finally:
        store.close()


# ============================================================
# Test: manager.adopt_orphan() — CR-03 in-memory registration
# ============================================================

class TestAdoptOrphan:
    """Tests for PositionManager.adopt_orphan() — CR-03 gap closure.

    An orphan adopted mid-session must be registered in manager._positions AND
    immediately reachable by on_bar (stop/trail/exit the same cycle — SAFE-03
    'adopt-and-protect').
    """

    def test_adopt_orphan_registers_in_positions(
        self, manager, open_store, mock_engine, mock_strategy
    ):
        """adopt_orphan writes PositionState to _positions with correct fields.

        After adopt_orphan(code="US.AAPL", qty=200, avg_cost=150.0, stop=148.5,
        position_id="P1"), manager._positions["US.AAPL"] must exist with:
          - phase == PositionPhase.ACTIVE
          - remaining_quantity == 200
          - full_quantity == 200
          - entry_price == 150.0
          - initial_stop == 148.5
          - trail_stop == 148.5
        The row must also be persisted to DB (get_open_positions returns it).
        """
        result = manager.adopt_orphan(
            code="US.AAPL",
            qty=200,
            avg_cost=150.0,
            stop=148.5,
            position_id="P1",
        )

        # In-memory registration
        assert "US.AAPL" in manager._positions, (
            "adopt_orphan must register position in manager._positions"
        )
        pos = manager._positions["US.AAPL"]
        assert pos.phase == PositionPhase.ACTIVE, (
            f"Expected ACTIVE, got {pos.phase}"
        )
        assert pos.remaining_quantity == 200, (
            f"Expected remaining_quantity=200, got {pos.remaining_quantity}"
        )
        assert pos.full_quantity == 200, (
            f"Expected full_quantity=200, got {pos.full_quantity}"
        )
        assert pos.entry_price == 150.0, (
            f"Expected entry_price=150.0, got {pos.entry_price}"
        )
        assert pos.initial_stop == 148.5, (
            f"Expected initial_stop=148.5, got {pos.initial_stop}"
        )
        assert pos.trail_stop == 148.5, (
            f"Expected trail_stop=148.5, got {pos.trail_stop}"
        )
        assert pos.entry_order_id == "", (
            f"Expected entry_order_id='', got {pos.entry_order_id!r}"
        )
        assert result is pos, "adopt_orphan must return the registered PositionState"

        # DB persistence — get_open_positions must include it
        rows = open_store.get_open_positions()
        codes_in_db = [r["code"] for r in rows]
        assert "US.AAPL" in codes_in_db, (
            "adopt_orphan must persist the position to DB (get_open_positions)"
        )

    def test_adopt_orphan_position_is_managed_by_on_bar(
        self, open_store, mock_strategy
    ):
        """After adopt_orphan, on_bar with close <= trail_stop triggers STOP_OUT.

        Proves the adopted position is reachable by on_bar the same cycle —
        the 'protect' half of adopt-and-protect (CR-03 / SAFE-03).
        """
        stop_out_calls = []

        # Wire manage_exit to return 200 (full fill) so the STOP_OUT path completes
        engine = MagicMock()
        engine.manage_exit = AsyncMock(return_value=200)

        cfg = _minimal_cfg()
        mgr = PositionManager(
            store=open_store,
            engine=engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        # Adopt the orphan into the manager
        mgr.adopt_orphan(
            code="US.AAPL",
            qty=200,
            avg_cost=150.0,
            stop=148.5,
        )

        # Confirm it is registered
        assert "US.AAPL" in mgr._positions, (
            "Position must be in _positions before on_bar test"
        )

        # bar.close <= trail_stop (148.5) → must trigger STOP_OUT
        bar = _make_bar(close=147.0)
        asyncio.run(mgr.on_bar(bar))

        pos = mgr._positions.get("US.AAPL")
        # After a full fill, the position should be CLOSED (STOP_OUT path closes it)
        assert pos is None or pos.phase == PositionPhase.CLOSED, (
            f"Expected CLOSED after stop-out on adopted position, got "
            f"{pos.phase if pos else 'None (removed)'}"
        )


# ============================================================
# Test: arm_stop_protection — broker stop placement on entry fill (D-01/D-03)
# ============================================================

class TestArmStopProtection:
    """Tests for arm_stop_protection (07-03 D-01/D-03 broker stop after fill)."""

    def test_broker_path_calls_place_stop_order(self, open_store, mock_strategy):
        """arm_stop_protection with use_broker_stop_orders=True calls gateway.place_stop_order once
        with (code, remaining_quantity, trail_stop, TrdSide.SELL) and sets pos.broker_stop_order_id."""
        import sys
        import types

        mock_gateway = MagicMock()
        mock_gateway.place_stop_order = AsyncMock(return_value="STOP-BROKER-001")

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
            trail_stop=98.0,
        )
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager.arm_stop_protection(pos))

        mock_gateway.place_stop_order.assert_called_once()
        assert pos.broker_stop_order_id == "STOP-BROKER-001", (
            "arm_stop_protection must set pos.broker_stop_order_id to the returned order_id"
        )

    def test_broker_path_uses_trd_side_sell(self, open_store, mock_strategy):
        """arm_stop_protection passes TrdSide.SELL to place_stop_order (Pitfall 7 — long-only)."""
        import sys
        import types

        mock_gateway = MagicMock()
        mock_gateway.place_stop_order = AsyncMock(return_value="STOP-SELL-001")

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        sell_sentinel = object()
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = sell_sentinel

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        pos = _make_pos(phase=PositionPhase.ACTIVE, remaining_quantity=100, trail_stop=98.0)
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager.arm_stop_protection(pos))

        call_args = mock_gateway.place_stop_order.call_args
        # trd_side must be TrdSide.SELL (sentinel) — long-only protective stop
        passed_side = call_args.kwargs.get("trd_side") or (call_args.args[3] if len(call_args.args) > 3 else None)
        assert passed_side is sell_sentinel, (
            "arm_stop_protection must pass TrdSide.SELL (not BUY) — Pitfall 7 long-only guard"
        )

    def test_no_op_when_gateway_none(self, open_store, mock_strategy):
        """arm_stop_protection is a no-op (no raise) when self._gateway is None."""
        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=None,
        )

        pos = _make_pos(phase=PositionPhase.ACTIVE, remaining_quantity=100, trail_stop=98.0)

        # Must not raise — no-op path
        try:
            asyncio.run(manager.arm_stop_protection(pos))
        except Exception as exc:
            pytest.fail(f"arm_stop_protection raised with gateway=None: {exc}")

        # broker_stop_order_id must remain unset/None
        assert getattr(pos, "broker_stop_order_id", None) is None

    def test_broker_stop_placement_failure_is_non_fatal(self, open_store, mock_strategy):
        """arm_stop_protection swallows place_stop_order exceptions (non-fatal D-03 backstop)."""
        mock_gateway = MagicMock()
        mock_gateway.place_stop_order = AsyncMock(
            side_effect=Exception("SIMULATE stop order rejected")
        )

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        pos = _make_pos(phase=PositionPhase.ACTIVE, remaining_quantity=100, trail_stop=98.0)
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        # place_stop_order raises but arm_stop_protection must swallow it
        try:
            import sys
            import types
            moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
            moomoo_mod.TrdSide = MagicMock()
            moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"
            with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
                asyncio.run(manager.arm_stop_protection(pos))
        except Exception as exc:
            pytest.fail(
                f"arm_stop_protection must swallow place_stop_order exceptions; raised: {exc}"
            )


# ============================================================
# Test: _sync_broker_stop — cancel-replace on trail ratchet (D-04/D-11)
# ============================================================

class TestSyncBrokerStop:
    """Tests for _sync_broker_stop (07-03 D-04 cancel-replace on breakeven/trail_up)."""

    def _make_manager_with_gateway(self, open_store, mock_strategy, cancel_mock=None, place_mock=None):
        """Helper: PositionManager with a mock gateway having cancel_order + place_stop_order."""
        import sys
        import types

        mock_gateway = MagicMock()
        mock_gateway.cancel_order = cancel_mock or AsyncMock()
        mock_gateway.place_stop_order = place_mock or AsyncMock(return_value="STOP-NEW-001")

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        # Patch moomoo so deferred import of TrdSide succeeds
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )
        return manager, mock_gateway

    def test_sync_broker_stop_cancels_old_and_places_new(self, open_store, mock_strategy):
        """_sync_broker_stop cancels the existing broker stop then places a new one
        at the updated trail_stop; pos.broker_stop_order_id is updated to the new id."""
        import sys
        import types

        cancel_mock = AsyncMock()
        place_mock = AsyncMock(return_value="STOP-REPLACED-001")

        manager, mock_gateway = self._make_manager_with_gateway(
            open_store, mock_strategy, cancel_mock=cancel_mock, place_mock=place_mock
        )

        pos = _make_pos(
            phase=PositionPhase.BREAKEVEN,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=100.0,   # stop moved to entry (breakeven)
            remaining_quantity=200,
        )
        pos.broker_stop_order_id = "STOP-OLD-001"
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager._sync_broker_stop(pos))

        cancel_mock.assert_called_once_with("STOP-OLD-001")
        place_mock.assert_called_once()
        assert pos.broker_stop_order_id == "STOP-REPLACED-001", (
            "_sync_broker_stop must update pos.broker_stop_order_id to the new order_id"
        )

    def test_sync_broker_stop_never_loosens_stop_price(self, open_store, mock_strategy):
        """After trail_up, _sync_broker_stop places a new stop at the ratcheted (higher)
        trail_stop; D-11 never-loosen is enforced by the FSM before this method is called,
        and the replacement always uses pos.trail_stop (the current, non-loosened value)."""
        import sys
        import types

        placed_prices = []

        async def _capture_place(code, qty, stop_price, trd_side):
            placed_prices.append(stop_price)
            return "STOP-TRAIL-001"

        mock_gateway = MagicMock()
        mock_gateway.cancel_order = AsyncMock()
        mock_gateway.place_stop_order = _capture_place

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        # trail_stop ratcheted to 101.5 (a new swing-low > old 100.0)
        pos = _make_pos(
            phase=PositionPhase.TRAILING,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=101.5,
            remaining_quantity=150,
        )
        pos.broker_stop_order_id = "STOP-OLD-TRAIL"
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager._sync_broker_stop(pos))

        assert len(placed_prices) == 1, "_sync_broker_stop must place exactly one replacement stop"
        assert placed_prices[0] == 101.5, (
            f"Replacement stop price must equal the ratcheted trail_stop=101.5, got {placed_prices[0]}"
        )

    def test_sync_broker_stop_noop_when_no_broker_stop_order_id(self, open_store, mock_strategy):
        """_sync_broker_stop is a no-op when pos.broker_stop_order_id is None
        (position was armed without a broker stop, D-03 fallback active)."""
        manager, mock_gateway = self._make_manager_with_gateway(open_store, mock_strategy)

        pos = _make_pos(phase=PositionPhase.ACTIVE, remaining_quantity=100, trail_stop=98.0)
        pos.broker_stop_order_id = None    # no broker stop placed
        manager._positions[pos.code] = pos

        import sys
        import types
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager._sync_broker_stop(pos))

        mock_gateway.cancel_order.assert_not_called()
        mock_gateway.place_stop_order.assert_not_called()

    def test_sync_broker_stop_swallows_cancel_exception_and_still_places(self, open_store, mock_strategy):
        """If cancel_order raises (already filled/cancelled), the exception is swallowed
        and the replacement stop is still placed (finding-1.4 tolerance)."""
        import sys
        import types

        cancel_mock = AsyncMock(side_effect=Exception("order already cancelled"))
        place_mock = AsyncMock(return_value="STOP-AFTER-CANCEL-ERR")

        manager, mock_gateway = self._make_manager_with_gateway(
            open_store, mock_strategy, cancel_mock=cancel_mock, place_mock=place_mock
        )

        pos = _make_pos(
            phase=PositionPhase.BREAKEVEN,
            remaining_quantity=200,
            trail_stop=100.0,
        )
        pos.broker_stop_order_id = "STOP-STALE-001"
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        try:
            with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
                asyncio.run(manager._sync_broker_stop(pos))
        except Exception as exc:
            pytest.fail(f"_sync_broker_stop must swallow cancel exceptions; raised: {exc}")

        place_mock.assert_called_once(), (
            "Replacement stop must still be placed even after cancel_order raises"
        )
        assert pos.broker_stop_order_id == "STOP-AFTER-CANCEL-ERR"

    @pytest.mark.asyncio
    async def test_on_bar_breakeven_triggers_sync_broker_stop(self, open_store, mock_strategy):
        """When breakeven transition fires on on_bar and use_broker_stop_orders=True,
        the broker stop is cancel-replaced via _sync_broker_stop."""
        import sys
        import types

        cancel_mock = AsyncMock()
        place_mock = AsyncMock(return_value="STOP-BREAKEVEN-SYNC")

        mock_gateway = MagicMock()
        mock_gateway.cancel_order = cancel_mock
        mock_gateway.place_stop_order = place_mock

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True
        # breakeven_trigger_r=1.0; entry=100, stop=98 → R=2 → breakeven at close>=102

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        # PARTIAL_TAKEN: ready for breakeven transition at 1R close
        pos = _make_pos(
            phase=PositionPhase.PARTIAL_TAKEN,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=98.0,
            remaining_quantity=200,
        )
        pos.broker_stop_order_id = "STOP-PRE-BREAKEVEN"
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        # Close at 102.0 → 1R → breakeven transition
        bar = _make_bar(code=pos.code, close=102.0)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            await manager.on_bar(bar)

        # _trigger_breakeven must call _sync_broker_stop: cancel old + place new
        cancel_mock.assert_called_once_with("STOP-PRE-BREAKEVEN")
        place_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_bar_trail_up_triggers_sync_broker_stop(self, open_store, mock_strategy):
        """When trail_up fires on on_bar and use_broker_stop_orders=True, the broker stop
        is cancel-replaced at the new (higher) trail_stop."""
        import sys
        import types
        from collections import deque

        cancel_mock = AsyncMock()
        new_stop_prices = []

        async def _capture_place(code, qty, stop_price, trd_side):
            new_stop_prices.append(stop_price)
            return "STOP-TRAIL-SYNC"

        mock_gateway = MagicMock()
        mock_gateway.cancel_order = cancel_mock
        mock_gateway.place_stop_order = _capture_place

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = True

        # Strategy returns swing-low at 103.0 (above current 100.0 breakeven stop).
        # bar_buffer must be non-empty so _compute_swing_low reaches the strategy mock.
        mock_strategy_trail = MagicMock()
        mock_strategy_trail.compute_swing_low_2_2 = MagicMock(return_value=103.0)

        # Provide a minimal bar_buffer so _compute_swing_low doesn't short-circuit.
        bar_buf = deque([
            {"time_key": "2026-06-24 10:00:00", "open": 100.0, "high": 104.0,
             "low": 99.0, "close": 103.0, "volume": 10000},
            {"time_key": "2026-06-24 10:05:00", "open": 103.0, "high": 106.0,
             "low": 102.0, "close": 105.0, "volume": 8000},
        ])
        bar_buffer = {"US.AAPL": bar_buf}

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy_trail,
            gateway=mock_gateway,
            bar_buffer=bar_buffer,
        )

        # BREAKEVEN: trail_stop at entry (100.0); swing-low 103.0 will ratchet it up
        pos = _make_pos(
            phase=PositionPhase.BREAKEVEN,
            entry_price=100.0,
            initial_stop=98.0,
            trail_stop=100.0,
            remaining_quantity=150,
        )
        pos.broker_stop_order_id = "STOP-PRE-TRAIL"
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        # Bar close at 105.0 (well above breakeven stop; swing_low=103 > trail_stop=100 → TRAIL_UP)
        bar = _make_bar(code=pos.code, close=105.0)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            await manager.on_bar(bar)

        cancel_mock.assert_called_once_with("STOP-PRE-TRAIL")
        assert len(new_stop_prices) == 1, "_sync_broker_stop must place exactly one replacement"
        assert new_stop_prices[0] == 103.0, (
            f"Replacement must use ratcheted trail_stop=103.0, got {new_stop_prices[0]}"
        )


# ============================================================
# Test: _on_quote + quote-tick fallback arm path (D-02)
# ============================================================

class TestQuoteTickFallback:
    """Tests for arm_stop_protection else-branch (use_broker_stop_orders=False)
    and _on_quote one-shot tick-level stop (07-03 D-02)."""

    def test_fallback_path_does_not_call_place_stop_order(self, open_store, mock_strategy):
        """With use_broker_stop_orders=False, arm_stop_protection must NOT call
        place_stop_order; instead it arms the quote-tick monitor."""
        import sys
        import types

        mock_gateway = MagicMock()
        mock_gateway.place_stop_order = AsyncMock(return_value="SHOULD-NOT-BE-CALLED")
        mock_gateway.subscribe_quote = AsyncMock()

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = False  # quote-fallback path

        manager = PositionManager(
            store=open_store,
            engine=MagicMock(),
            cfg=cfg,
            strategy=mock_strategy,
            gateway=mock_gateway,
        )

        pos = _make_pos(phase=PositionPhase.ACTIVE, remaining_quantity=100, trail_stop=98.0)
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.SELL = "SELL_SENTINEL"

        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            asyncio.run(manager.arm_stop_protection(pos))

        mock_gateway.place_stop_order.assert_not_called()
        mock_gateway.subscribe_quote.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_quote_below_trail_stop_fires_manage_exit(self, open_store, mock_strategy):
        """_on_quote with bid_price <= pos.trail_stop invokes engine.manage_exit
        once for the remaining_quantity."""
        mock_engine = MagicMock()
        mock_engine.manage_exit = AsyncMock(return_value=100)

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = False

        manager = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=200,
            trail_stop=98.0,
        )
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        # bid at exactly trail_stop — should fire
        await manager._on_quote(pos.code, bid_price=98.0)

        mock_engine.manage_exit.assert_called_once()
        call_kwargs = mock_engine.manage_exit.call_args
        assert 200 in (call_kwargs.args + tuple(call_kwargs.kwargs.values())), (
            "_on_quote must pass remaining_quantity=200 to manage_exit"
        )

    @pytest.mark.asyncio
    async def test_on_quote_above_trail_stop_does_nothing(self, open_store, mock_strategy):
        """_on_quote with bid_price > pos.trail_stop must NOT invoke manage_exit."""
        mock_engine = MagicMock()
        mock_engine.manage_exit = AsyncMock(return_value=0)

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = False

        manager = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=200,
            trail_stop=98.0,
        )
        manager._positions[pos.code] = pos

        # bid above trail_stop — should NOT fire
        await manager._on_quote(pos.code, bid_price=100.0)

        mock_engine.manage_exit.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_quote_fires_at_most_once(self, open_store, mock_strategy):
        """_on_quote fires the exit at most once per position (one-shot guard).
        A second tick at the same or lower bid must NOT call manage_exit again."""
        mock_engine = MagicMock()
        mock_engine.manage_exit = AsyncMock(return_value=200)

        cfg = _minimal_cfg()
        cfg.use_broker_stop_orders = False

        manager = PositionManager(
            store=open_store,
            engine=mock_engine,
            cfg=cfg,
            strategy=mock_strategy,
        )

        pos = _make_pos(
            phase=PositionPhase.ACTIVE,
            remaining_quantity=200,
            trail_stop=98.0,
        )
        manager._positions[pos.code] = pos
        open_store.upsert_position(pos)

        # First tick: fires
        await manager._on_quote(pos.code, bid_price=97.5)
        # Second tick: same bid — must NOT fire again
        await manager._on_quote(pos.code, bid_price=97.0)

        assert mock_engine.manage_exit.call_count == 1, (
            "_on_quote one-shot guard must prevent double-fire on repeated ticks"
        )
