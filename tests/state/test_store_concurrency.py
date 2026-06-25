#!/usr/bin/env python3
"""
tests/state/test_store_concurrency.py — Loop-vs-executor concurrency regression test.

Closes WR-01 from 06.1-08-REVIEW: the existing test_store_threadsafety.py only drives
executor-thread reads, leaving the key race unexercised — the asyncio EVENT-LOOP THREAD
performing row_factory-flipping reads + commits (reconcile_once style) CONCURRENTLY with
ThreadPoolExecutor worker reads. That race can produce:
  - sqlite3.ProgrammingError: recursive use of cursors / created in thread
  - Silent dict-shape corruption (tuple observed mid-flip instead of a dict)

RED (pre-Task-2/3): this test calls the NEW store methods added in Task 2
(get_pending_intent_codes, insert_pending_intent) on the loop-thread writer coroutine.
Before Task 2 those methods do NOT exist — the writer falls back to raw conn access that
is NOT guarded on the loop thread, exposing the race. The test is structured to FAIL with
AttributeError (method missing) on the pre-Task-2 tree, and FAIL with sqlite3.ProgrammingError
or dict-shape AssertionError if the writer uses the unguarded fallback pattern.

GREEN (post-Task-2+3): all access is through guarded StateStore methods;
the test passes — no exception, every read result has the expected column-name dict shape.

References: CR-01, CR-02, WR-01 (06.1-08-REVIEW); T-06.1-09-01/02 (threat model).

Conventions mirror tests/state/test_store_threadsafety.py:
  - BOT_STATE_DB monkeypatch onto a tmp_path DB.
  - asyncio.run() to drive the event loop (03-02 decision: no implicit default loop).
  - loop.run_in_executor(None, fn) to force executor-thread access.
"""

import asyncio
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

from bot.state.store import StateStore

# Number of concurrent executor-thread readers (must be >= 4 per plan)
N_READERS = 6

# Number of loop-thread writer iterations (enough to interleave with executor readers)
N_WRITER_ITERATIONS = 200


# ============================================================
# TestLoopVsExecutorConcurrency
# ============================================================

class TestLoopVsExecutorConcurrency:
    """Regression test for the actual loop-vs-executor race missed by 06.1-08.

    06.1-08 only drove executor-thread reads guarded by store.lock(); this test also
    drives an event-loop-thread writer that calls the new store.get_pending_intent_codes()
    and store.insert_pending_intent() / increment_daily_filled_count() methods added in
    Task 2. Before Task 2 these methods don't exist — the test reports AttributeError.
    After Task 2+3 the test passes because all DB access is routed through guarded methods.
    """

    def test_concurrent_loop_writer_and_executor_readers_no_corruption(
        self, tmp_path, monkeypatch
    ):
        """Drive the actual reconcile-style race: loop-thread writer + executor readers.

        Protocol:
        1. Open a StateStore on a tmp DB.
        2. Seed >=1 positions row (store.upsert_position) and >=1 pending_intents row.
        3. On the EVENT-LOOP THREAD, run a coroutine that calls the new guarded store
           methods: get_pending_intent_codes() (row_factory-flip read) and
           increment_daily_filled_count() (write+commit), yielding between iterations.
        4. CONCURRENTLY schedule N (>=4) executor-thread readers via run_in_executor
           calling store.get_open_positions() / store.get_closed_trades().
        5. await asyncio.gather(writer_coro, *reader_futures).
        6. ASSERT: no exception; every pending-intent dict has keys {"intent_id","code"};
           every position dict has key "position_id".

        RED (pre-Task-2) expectation: store.get_pending_intent_codes() and
        store.increment_daily_filled_count() do not yet exist -> AttributeError on the
        first loop-thread writer iteration -> test reports FAILED for the right reason.

        GREEN (post-Task-2+3): methods exist and are guarded -> test PASSES.
        """
        db_path = str(tmp_path / "bot_state_concurrency_test.db")
        monkeypatch.setenv("BOT_STATE_DB", db_path)

        all_reader_results = []

        async def _run():
            store = StateStore()
            store.open()
            try:
                # ---- Seed: insert positions + pending_intents rows ----
                _seed_positions(store)
                _seed_pending_intent(store)

                loop = asyncio.get_running_loop()

                # ---- EVENT-LOOP THREAD WRITER coroutine ----
                # Calls the NEW guarded store methods (Task 2 artifacts):
                #   - get_pending_intent_codes(): row_factory-flip read inside lock
                #   - increment_daily_filled_count(): INSERT...ON CONFLICT inside lock
                # Before Task 2 these raise AttributeError -> RED failure.
                async def _loop_thread_writer():
                    session_date = "2026-01-15"
                    now_ts = datetime.now(timezone.utc).isoformat()
                    for _ in range(N_WRITER_ITERATIONS):
                        # row_factory-flip read on the loop thread (guarded in Task 2)
                        pending = store.get_pending_intent_codes("PENDING")
                        # Validate shape immediately so a mid-flip tuple is caught here
                        for item in pending:
                            assert isinstance(item, dict), (
                                f"get_pending_intent_codes returned non-dict item: "
                                f"{type(item)!r} = {item!r}"
                            )
                            assert "intent_id" in item and "code" in item, (
                                f"pending item missing expected keys: {list(item.keys())}"
                            )
                        # Write+commit on the loop thread (guarded in Task 2)
                        store.increment_daily_filled_count(session_date, now_ts)
                        # Yield so executor readers can run concurrently
                        await asyncio.sleep(0)

                # ---- EXECUTOR THREAD READERS ----
                # Call existing guarded row_factory-flipping readers from worker threads.
                def _executor_reader():
                    """Call row_factory-flipping readers from a worker thread."""
                    positions = store.get_open_positions()
                    trades = store.get_closed_trades("2026-01-15")
                    return positions, trades

                # Schedule N_READERS executor futures before starting gather
                reader_futures = [
                    loop.run_in_executor(None, _executor_reader)
                    for _ in range(N_READERS)
                ]

                # Run writer + all readers concurrently
                results = await asyncio.gather(
                    _loop_thread_writer(),
                    *reader_futures,
                    return_exceptions=True,
                )

                # ---- Collect and validate results ----
                # results[0] = writer return (None) or exception
                # results[1..N] = reader (positions, trades) tuples or exceptions
                writer_result = results[0]
                reader_results = results[1:]

                # Propagate any exception raised by the writer
                if isinstance(writer_result, BaseException):
                    raise writer_result

                for i, r in enumerate(reader_results):
                    if isinstance(r, BaseException):
                        raise r
                    positions, trades = r
                    all_reader_results.append((positions, trades))

                    # Positions must be a list of dicts with correct keys
                    assert isinstance(positions, list), (
                        f"Reader {i}: positions is {type(positions)}, not list — "
                        "dict-shape corruption (row_factory race)"
                    )
                    for j, pos_row in enumerate(positions):
                        assert isinstance(pos_row, dict), (
                            f"Reader {i} position[{j}] is {type(pos_row)!r}, not dict — "
                            "row_factory race corrupted result to plain tuple"
                        )
                        assert "position_id" in pos_row, (
                            f"Reader {i} position[{j}] missing 'position_id' key; "
                            f"got keys: {list(pos_row.keys())}"
                        )

                    # Trades must be a list (empty is fine for fresh DB with no closed trades)
                    assert isinstance(trades, list), (
                        f"Reader {i}: trades is {type(trades)}, not list"
                    )

            finally:
                store.close()

        asyncio.run(_run())

        # Verify we actually completed all concurrent readers
        assert len(all_reader_results) == N_READERS, (
            f"Expected {N_READERS} reader results, got {len(all_reader_results)}"
        )


# ============================================================
# Seed helpers
# ============================================================

def _seed_positions(store: StateStore) -> None:
    """Insert a live positions row so get_open_positions returns a non-empty dict list.

    Uses the StateStore's guarded upsert_position method so seeding itself does
    not interfere with the race under test.
    """
    import types
    import enum

    class _Phase(enum.Enum):
        ACTIVE = "ACTIVE"

    pos = types.SimpleNamespace(
        position_id="test-pos-concurrency-001",
        code="US.AAPL",
        phase=_Phase.ACTIVE,
        entry_price=175.0,
        initial_stop=172.0,
        trail_stop=172.0,
        full_quantity=10,
        remaining_quantity=10,
        entry_order_id="entry-001",
        exit_order_id=None,
        avg_fill_price=175.0,
        opened_at=datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
    )
    store.upsert_position(pos)


def _seed_pending_intent(store: StateStore) -> None:
    """Insert a pending_intents row so get_pending_intent_codes returns non-empty.

    Seeds via direct conn access under the lock — safe at setup time before
    the concurrent test begins.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with store._lock:
        store._conn.execute(
            "INSERT OR IGNORE INTO pending_intents "
            "(intent_id, code, status, entry_price, stop_price, quantity, emitted_at) "
            "VALUES (?, ?, 'PENDING', ?, ?, ?, ?)",
            ("intent-concurrency-001", "US.AAPL", 175.5, 172.0, 10, now_iso),
        )
        store._conn.commit()
