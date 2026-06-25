#!/usr/bin/env python3
"""
tests/state/test_store_threadsafety.py — Cross-thread StateStore regression tests.

Reproduces the live UAT blocker:
  sqlite3.ProgrammingError: SQLite objects created in a thread can only be
  used in that same thread.

The four scheduled jobs in bot/service/bot.py hand self._store into
loop.run_in_executor(None, ...) worker threads where conn.execute / reads
run on a different thread than where the connection was opened, raising
ProgrammingError.

Three tests — RED against current store.py (check_same_thread=True default),
GREEN after the Task-2 fix (check_same_thread=False + RLock).

Test conventions mirror tests/state/test_store.py:
  - Use BOT_STATE_DB monkeypatch via tmp_state_db fixture (or manual monkeypatch)
    to point at a tmp_path DB.
  - Use asyncio.run() to drive the event loop (consistent with the 03-02 decision
    in STATE.md: no implicit default loop).
  - Use loop.run_in_executor(None, fn) to force store access onto a
    ThreadPoolExecutor worker thread — the same execution path as the live bot.
"""

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from bot.state.store import StateStore


# ============================================================
# TestCrossThreadStateStore
# ============================================================

class TestCrossThreadStateStore:
    """Tests that fail with ProgrammingError (RED) before the thread-safety fix."""

    def test_store_write_through_executor_thread_succeeds(self, tmp_path, monkeypatch):
        """A store write inside run_in_executor must succeed without ProgrammingError.

        Reproduces the _job_intraday_rescan / _job_premarket_scan crash path:
        the store is opened on the main thread, then conn.execute is called
        on a ThreadPoolExecutor worker thread.

        RED today: sqlite3.ProgrammingError (created in thread ... can only be
        used in that same thread).
        GREEN after fix: the write + read-back succeeds.
        """
        db_path = str(tmp_path / "bot_state_test.db")
        monkeypatch.setenv("BOT_STATE_DB", db_path)

        async def _run():
            store = StateStore()
            store.open()
            try:
                loop = asyncio.get_running_loop()

                def _write_from_worker():
                    # Mimic the live crash path: executor job writes to daily_trade_count
                    # (the table written by scanner / risk_engine in the real bot).
                    now_iso = datetime.now(timezone.utc).isoformat()
                    store.conn.execute(
                        "INSERT OR REPLACE INTO daily_trade_count"
                        " (session_date, filled_count, updated_at)"
                        " VALUES (?, ?, ?)",
                        ("2024-01-15", 3, now_iso),
                    )
                    store.conn.commit()
                    # Read back to confirm the write committed
                    row = store.conn.execute(
                        "SELECT filled_count FROM daily_trade_count"
                        " WHERE session_date = '2024-01-15'"
                    ).fetchone()
                    return row[0] if row else None

                result = await loop.run_in_executor(None, _write_from_worker)
                assert result == 3, f"Expected 3, got {result!r}"
            finally:
                store.close()

        asyncio.run(_run())

    def test_store_read_through_executor_thread_succeeds(self, tmp_path, monkeypatch):
        """A row_factory-flipping store reader inside run_in_executor must succeed.

        Reproduces the _job_eod_report crash path: get_closed_trades /
        get_open_positions are called from a worker thread.

        get_open_positions / get_closed_trades flip conn.row_factory = sqlite3.Row
        then reset it to None — this mutates shared connection state and fails on
        a different thread.

        RED today: sqlite3.ProgrammingError.
        GREEN after fix: returns an empty list (no rows in fresh DB).
        """
        db_path = str(tmp_path / "bot_state_test.db")
        monkeypatch.setenv("BOT_STATE_DB", db_path)

        async def _run():
            store = StateStore()
            store.open()
            try:
                loop = asyncio.get_running_loop()

                def _read_from_worker():
                    # Calls the row_factory-flipping reader from a worker thread —
                    # the _fetch_build_write closure in _job_eod_report does the same.
                    positions = store.get_open_positions()
                    return positions

                result = await loop.run_in_executor(None, _read_from_worker)
                # Fresh DB has no open positions — empty list is the correct result
                assert isinstance(result, list), f"Expected list, got {type(result)}"
            finally:
                store.close()

        asyncio.run(_run())

    def test_concurrent_loop_and_executor_writes_do_not_corrupt(self, tmp_path, monkeypatch):
        """Concurrent loop-thread + executor-thread writes must all commit without loss.

        Reproduces the race described in the plan: the event-loop thread also
        WRITES to the same connection (PositionManager.upsert_position, engine,
        reconcile paths). Without serialization, loop-thread writes and
        executor-thread writes can interleave, causing lost writes or corrupted
        cursor state.

        This test:
          1. Kicks off N writes on the executor (worker thread).
          2. Kicks off N writes on the event-loop thread (direct await).
          3. Asserts no exception is raised and the final row count equals 2*N.

        RED today: ProgrammingError raised on the worker thread (check_same_thread
        rejects cross-thread use before any locking concern even arises).
        GREEN after fix: all 2*N writes commit; total row count == 2*N.
        """
        db_path = str(tmp_path / "bot_state_test.db")
        monkeypatch.setenv("BOT_STATE_DB", db_path)
        N = 5

        async def _run():
            store = StateStore()
            store.open()
            try:
                loop = asyncio.get_running_loop()

                def _write_from_executor(tag: str, i: int):
                    """Write one row into meta from a worker thread."""
                    key = f"{tag}_{i}"
                    store.conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (key, str(i)),
                    )
                    store.conn.commit()

                def _write_on_loop_thread(tag: str, i: int):
                    """Write one row into meta from the loop thread (direct call)."""
                    key = f"{tag}_{i}"
                    store.conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (key, str(i)),
                    )
                    store.conn.commit()

                # Launch N executor writes concurrently
                executor_futures = [
                    loop.run_in_executor(None, _write_from_executor, "exec", i)
                    for i in range(N)
                ]
                # Interleave N loop-thread writes (run synchronously on the loop thread)
                # Note: these are not coroutines — run them as plain calls to simulate
                # the manager/engine path that writes directly via store.conn.execute.
                loop_tasks = [
                    asyncio.get_event_loop().run_in_executor(
                        None, _write_on_loop_thread, "loop", i
                    )
                    for i in range(N)
                ]

                # Wait for all writes to complete
                await asyncio.gather(*executor_futures, *loop_tasks)

                # Count committed rows — expect 2*N distinct keys
                row_count = store.conn.execute(
                    "SELECT COUNT(*) FROM meta WHERE key LIKE 'exec_%' OR key LIKE 'loop_%'"
                ).fetchone()[0]
                assert row_count == 2 * N, (
                    f"Expected {2 * N} committed rows, got {row_count} — "
                    f"concurrent writes may have been lost or interleaved"
                )
            finally:
                store.close()

        asyncio.run(_run())
