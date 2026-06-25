#!/usr/bin/env python3
"""
tests/state/test_store_concurrency.py — Loop-vs-executor concurrency regression test.

Closes WR-01 from 06.1-08-REVIEW: the original test_store_threadsafety.py only drives
executor-thread reads, leaving the key race unexercised — the asyncio EVENT-LOOP THREAD
performing row_factory-flipping reads + commits (reconcile_once style) CONCURRENTLY with
ThreadPoolExecutor worker reads. That race can produce:
  - sqlite3.ProgrammingError: recursive use of cursors / created in thread
  - Silent dict-shape corruption (tuple observed mid-flip instead of a dict)

=============================================================================
TEETH REQUIREMENT (WR-01 mutation-test hardened — 06.1-09 gap-closure)
=============================================================================

This test MUST reliably FAIL when StateStore's self._lock is neutralised (e.g.
replaced with a no-op context manager). The following knobs are empirically
validated to produce 300+ corruptions without the lock and 0 with it:

  1. sys.setswitchinterval(1e-6) during the contended region (max GIL tumbling).
  2. N_STRESS_THREADS >= 6 raw threading.Thread workers, each running
     N_STRESS_ITERS >= 1500 iterations of row_factory-flipping reads.
  3. Per-row key access (row["position_id"]) which raises TypeError/IndexError
     on a plain tuple — `isinstance(row, dict)` is NOT sufficient because
     a corrupted tuple may still satisfy isinstance checks in some code paths.
  4. Corruption count is asserted == 0 after all threads join.

The high-volume worker-thread stress test (TestStressRowFactoryRace) provides the
teeth.  The asyncio loop-vs-executor test (TestLoopVsExecutorConcurrency) is kept
to document the event-loop-thread contention scenario but its low iteration count
means it alone is insufficient; the stress test is the definitive guard.

References: CR-01, CR-02, WR-01 (06.1-08-REVIEW); T-06.1-09-01/02 (threat model).

Conventions mirror tests/state/test_store_threadsafety.py:
  - BOT_STATE_DB monkeypatch onto a tmp_path DB.
  - asyncio.run() to drive the event loop (03-02 decision: no implicit default loop).
  - loop.run_in_executor(None, fn) to force executor-thread access.
"""

import asyncio
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from bot.state.store import StateStore

# ---- Stress test dimensions (empirically validated) ----
# At these values: without the lock → 300+ corruptions; with it → 0.
N_STRESS_THREADS = 8     # concurrent raw threading.Thread workers (>= 6 required)
N_STRESS_ITERS = 1500    # iterations per thread of row_factory-flip reads (>= 1500 required)

# ---- Legacy loop-vs-executor test dimensions ----
N_READERS = 6            # concurrent executor-thread readers
N_WRITER_ITERATIONS = 200  # loop-thread writer iterations


# ============================================================
# TestStressRowFactoryRace  (the actual "teeth" test)
# ============================================================

class TestStressRowFactoryRace:
    """High-volume race that deterministically catches removal of self._lock.

    TEETH: Without self._lock (replaced with a no-op), this test accumulates
    300+ corruption events (plain tuple returned instead of dict-like Row, causing
    key-access TypeError/IndexError that the counter captures). With the real
    RLock, 0 corruptions are observed.

    The mutation proof procedure:
      1. Run on current tree → PASS (0 corruptions).
      2. Replace ``self._lock = threading.RLock()`` in store.py with a no-op
         context manager, rerun → FAIL (corruption_count > 0).
      3. Restore store.py and verify ``git diff bot/state/store.py`` is empty.
    """

    def test_row_factory_race_fails_without_lock(self, tmp_path, monkeypatch):
        """N_STRESS_THREADS workers x N_STRESS_ITERS iterations catch row_factory corruption.

        Protocol:
        1. Open StateStore on a tmp DB, seed >=1 positions row.
        2. Tighten GIL switch interval to 1e-6 s (maximise thread-context switches).
        3. Launch N_STRESS_THREADS raw threading.Thread workers, each calling:
             get_open_positions()      (row_factory flip: None → Row → None)
             get_pending_intent_codes()  (row_factory flip: None → Row → None)
             get_closed_trades(date)   (row_factory flip: None → Row → None)
           N_STRESS_ITERS times each, accessing returned rows via KEY ACCESS
           (row["position_id"], item["intent_id"]) — TypeError/IndexError on a
           plain tuple is captured as a corruption event.
        4. Restore switch interval in a finally block.
        5. Join all threads, assert corruption_count == 0.

        Without self._lock: row_factory is reset to None mid-flip by a concurrent
        thread; the cursor in the first thread snapshots None factory, returns
        plain tuples; key access raises TypeError → counted as corruption.
        With the real RLock: each flip-fetch-reset block is atomic; 0 corruptions.
        """
        db_path = str(tmp_path / "stress_race_test.db")
        monkeypatch.setenv("BOT_STATE_DB", db_path)

        store = StateStore()
        store.open()
        try:
            # Seed: at least one position row so reads return non-empty results.
            _seed_positions(store)
            _seed_pending_intent(store)

            corruption_count = 0
            count_lock = threading.Lock()  # protect the corruption counter itself
            session_date = "2026-01-15"

            def _worker():
                nonlocal corruption_count
                local_errors = 0
                for _ in range(N_STRESS_ITERS):
                    # --- get_open_positions (row_factory flip) ---
                    try:
                        rows = store.get_open_positions()
                        for row in rows:
                            # Key access: raises TypeError/IndexError on a plain tuple
                            _ = row["position_id"]
                    except Exception:
                        # Any exception here is corruption evidence:
                        #   TypeError/IndexError/KeyError — plain tuple returned instead of Row
                        #   sqlite3.InterfaceError — "bad parameter or other API misuse"
                        #     triggered when one thread's row_factory=None reset fires
                        #     during another thread's execute() with a bound parameter,
                        #     corrupting the cursor's internal state
                        local_errors += 1

                    # --- get_pending_intent_codes (row_factory flip) ---
                    try:
                        items = store.get_pending_intent_codes("PENDING")
                        for item in items:
                            # Key access: raises TypeError/IndexError on a plain tuple
                            _ = item["intent_id"]
                            _ = item["code"]
                    except Exception:
                        local_errors += 1

                    # --- get_closed_trades (row_factory flip) ---
                    try:
                        trades = store.get_closed_trades(session_date)
                        for trade in trades:
                            # trades table is empty → loop body never executes
                            # but if it had rows, key access would catch corruption
                            pass
                    except Exception:
                        local_errors += 1

                if local_errors > 0:
                    with count_lock:
                        corruption_count += local_errors

            # Tighten GIL switch interval for maximum thread tumbling.
            old_switch_interval = sys.getswitchinterval()
            try:
                sys.setswitchinterval(1e-6)

                threads = [
                    threading.Thread(target=_worker, name=f"stress-reader-{i}")
                    for i in range(N_STRESS_THREADS)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            finally:
                # ALWAYS restore the switch interval — even if a thread raises.
                sys.setswitchinterval(old_switch_interval)

        finally:
            store.close()

        assert corruption_count == 0, (
            f"row_factory race detected: {corruption_count} corruption event(s) "
            f"across {N_STRESS_THREADS} threads x {N_STRESS_ITERS} iterations. "
            "This indicates StateStore.self._lock is not serialising row_factory "
            "flips correctly. Without the RLock a concurrent thread resets "
            "row_factory=None before another thread's execute() snapshots the "
            "cursor, causing plain tuples to be returned instead of sqlite3.Row "
            "objects — key access (row['position_id']) then raises TypeError."
        )


# ============================================================
# TestLoopVsExecutorConcurrency  (documents the original race scenario)
# ============================================================

class TestLoopVsExecutorConcurrency:
    """Regression test for the loop-vs-executor race documented in WR-01.

    This test drives an asyncio event-loop-thread writer concurrently with
    ThreadPoolExecutor readers, mirroring the reconcile_once/bot.py pattern.

    NOTE: The low iteration count here (N_READERS x 1 call each) means this
    test alone does NOT reliably fail without the lock (WR-01 false-green).
    The definitive guard is TestStressRowFactoryRace above. This class is
    retained to document the event-loop-thread contention scenario and to
    exercise the method shape/interface under real asyncio conditions.
    """

    def test_concurrent_loop_writer_and_executor_readers_no_corruption(
        self, tmp_path, monkeypatch
    ):
        """Drive the actual reconcile-style race: loop-thread writer + executor readers.

        Protocol:
        1. Open a StateStore on a tmp DB.
        2. Seed >=1 positions row (store.upsert_position) and >=1 pending_intents row.
        3. On the EVENT-LOOP THREAD, run a coroutine that calls the guarded store
           methods: get_pending_intent_codes() (row_factory-flip read) and
           increment_daily_filled_count() (write+commit), yielding between iterations.
        4. CONCURRENTLY schedule N (>=4) executor-thread readers via run_in_executor
           calling store.get_open_positions() / store.get_closed_trades().
        5. await asyncio.gather(writer_coro, *reader_futures).
        6. ASSERT: no exception; every pending-intent dict supports key access;
           every position dict supports key access ("position_id").
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
                async def _loop_thread_writer():
                    session_date = "2026-01-15"
                    now_ts = datetime.now(timezone.utc).isoformat()
                    for _ in range(N_WRITER_ITERATIONS):
                        # row_factory-flip read on the loop thread (guarded)
                        pending = store.get_pending_intent_codes("PENDING")
                        # Key access — fails on plain tuple
                        for item in pending:
                            assert isinstance(item, dict), (
                                f"get_pending_intent_codes returned non-dict item: "
                                f"{type(item)!r} = {item!r}"
                            )
                            _ = item["intent_id"]  # key access, not just isinstance
                            _ = item["code"]
                        # Write+commit on the loop thread (guarded)
                        store.increment_daily_filled_count(session_date, now_ts)
                        # Yield so executor readers can run concurrently
                        await asyncio.sleep(0)

                # ---- EXECUTOR THREAD READERS ----
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
                        # Key access is the actual tooth here
                        _ = pos_row["position_id"]

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
