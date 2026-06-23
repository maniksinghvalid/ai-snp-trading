---
phase: 01-foundation
plan: "02"
subsystem: state-persistence
tags: [sqlite, migrations, atomic-write, crash-injection, state-store, pragma-user-version]
dependency_graph:
  requires:
    - bot.__init__ (package root, task 01-01)
    - tests.conftest.tmp_state_db (fixture from 01-01)
  provides:
    - bot.state.migrations.MIGRATIONS
    - bot.state.migrations.CURRENT_VERSION
    - bot.state.migrations.run_migrations
    - bot.state.store.StateStore
    - bot.state.store.atomic_write_json
    - bot.state.store.resolve_db_path
    - bot.state.store.DEFAULT_DB_PATH
  affects:
    - Phase 2 scanner (daily_scan table + UNIQUE(scan_date, code))
    - Phase 3 signal engine (bar_cache table + UNIQUE(code, time_key))
    - Phase 4 position manager (positions table, trades table)
    - Phase 4 reconciliation (StateStore opened at startup, conn available)
    - Phase 6 backtester (trades table for result storage)
tech_stack:
  added:
    - sqlite3 (stdlib, no new deps — pure stdlib per D-08)
    - tempfile (stdlib — mkstemp for atomic write)
    - os.replace (stdlib — POSIX atomic rename)
    - stat (stdlib — 0600 permissions on snapshot files)
  patterns:
    - PRAGMA user_version migration runner (D-08)
    - Ordered MIGRATIONS list (index N = migration step N+1)
    - Atomic temp-file + os.replace snapshot writer (D-10/PITFALLS #10)
    - Parse-validate before atomic swap (write → json.load → os.replace)
    - BOT_STATE_DB env override for test isolation (D-09)
    - StateStore context manager support (__enter__ / __exit__)
    - 0600 file permissions on state snapshots (T-01-07)
key_files:
  created:
    - bot/state/__init__.py
    - bot/state/migrations.py
    - bot/state/store.py
    - tests/state/__init__.py
    - tests/state/test_migrations.py
    - tests/state/test_atomic_write.py
    - tests/state/test_store.py
  modified: []
decisions:
  - "bot/state/__init__.py started minimal (Task 1) and gained re-exports after store.py was created (Task 2) — avoids circular import during incremental task execution (same pattern as 01-01 gateway/__init__.py)"
  - "StateStore.open() enables WAL journal mode for better concurrency (non-breaking for tests)"
  - "atomic_write_json uses tempfile.mkstemp with suffix='.tmp' for easy cleanup glob; temp file is in the same directory as the target to guarantee same-filesystem atomic rename"
  - "0600 permissions applied after os.replace (not before) to avoid exposing the file window between creation and permission set; chmod failure is non-fatal"
metrics:
  duration: "4 minutes"
  completed_date: "2026-06-23"
  tasks_completed: 2
  tasks_total: 2
  files_created: 7
  files_modified: 0
  tests_added: 49
---

# Phase 01 Plan 02: StateStore — SQLite Migration Runner + Atomic Write Summary

**One-liner:** PRAGMA user_version migration runner that creates the full v1 schema (positions, trades, daily_scan, bar_cache, meta) as migration 0001, plus a crash-injection-proven atomic temp-file + os.replace JSON snapshot writer with 0600 permissions.

## What Was Built

This plan delivers the durable persistence layer every later phase targets:

1. **`bot/state/migrations.py`** (`MIGRATIONS`, `CURRENT_VERSION = 1`, `run_migrations(conn)`):
   - Migration 0001 creates all five v1 tables via `CREATE TABLE IF NOT EXISTS` executescript.
   - `positions` holds the full PositionState FSM fields (position_id, code, phase, entry_price, initial_stop, trail_stop, full_quantity, remaining_quantity, opened_at, updated_at).
   - `trades` is append-only closed-trade log (trade_id, position_id, code, entry/exit price, quantity, exit_reason, r_multiple, closed_at).
   - `daily_scan` has `UNIQUE(scan_date, code)` — idempotent scan inserts for Phase 2.
   - `bar_cache` has `UNIQUE(code, time_key)` — upsert-ready for Phase 3 bar caching.
   - `meta` is a simple key/value store for bot-level persistent flags.
   - `run_migrations` reads `PRAGMA user_version`, applies only `MIGRATIONS[version:]`, sets `PRAGMA user_version = i` after each step and commits. Second call is a silent no-op.

2. **`bot/state/store.py`** (`StateStore`, `atomic_write_json`, `resolve_db_path`, `DEFAULT_DB_PATH`):
   - `DEFAULT_DB_PATH = os.path.join("data", "bot_state.db")` (gitignored data/ directory).
   - `resolve_db_path()` returns `BOT_STATE_DB` env var or `DEFAULT_DB_PATH` (D-09 test isolation).
   - `StateStore.open()` ensures `data/` exists, opens a `sqlite3.connect`, enables WAL mode, and calls `run_migrations(conn)`. Returns `self` for chaining.
   - `StateStore.conn` property provides the raw `sqlite3.Connection` for later phases.
   - `StateStore` implements context manager protocol (`__enter__`/`__exit__`).
   - `atomic_write_json(path, data)`: temp-file in same directory → `json.dump` → `json.load` validate → `os.replace` swap → `chmod 0600`. On any exception before swap, temp file is unlinked; original is never touched.

3. **Test suite** (49 tests across three test files, all pass):
   - `test_migrations.py` (18 tests): fresh-DB schema, all five tables, user_version=1, idempotency, UNIQUE constraint enforcement for daily_scan and bar_cache.
   - `test_atomic_write.py` (23 tests): success path, unicode round-trip, permissions, crash-injection via monkeypatched `os.replace`, corrupt temp write via monkeypatched `json.dump`, orphan temp file cleanup in all failure cases.
   - `test_store.py` (18 tests): path resolution, open migrates schema, conn accessible, RuntimeError before open, re-open idempotency, context manager, explicit db_path override, safe close before open.

## Task Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 265ea9b | Schema migration 0001 + PRAGMA user_version runner |
| 2 | 68a4ac7 | StateStore open/path-resolution + atomic write with crash-injection tests |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] bot/state/__init__.py premature import of store.py at Task 1**
- **Found during:** Task 1 test run (ModuleNotFoundError: No module named 'bot.state.store')
- **Issue:** The plan's `__init__.py` re-exported from `store.py` before store.py existed, causing collection errors for `test_migrations.py` which only imports from `migrations`.
- **Fix:** Applied the same pattern as 01-01 gateway/__init__.py — `__init__.py` started as a minimal docstring-only file in Task 1, then gained full re-exports in Task 2 after `store.py` was created.
- **Files modified:** `bot/state/__init__.py` (updated twice across tasks)
- **Commit:** Inline with task commits

None other — plan executed as written.

## Known Stubs

None — all code is wired to real functionality. The StateStore is ready for use in Phase 2+.

## Threat Flags

No new threat surface beyond the plan's threat model. Mitigations confirmed in place:

| Threat | Mitigation Status |
|--------|-------------------|
| T-01-06 (Tampering — atomic_write_json) | Mitigated: temp-file + os.replace + json.load validate before swap. Crash-injection test (test_atomic_write.py) enforces it. |
| T-01-07 (Information Disclosure — snapshot file) | Mitigated: `chmod 0600` applied after every successful write. Test asserts group/other read bits are clear. |
| T-01-08 (DoS — corrupt DB on open) | Accepted: `CREATE TABLE IF NOT EXISTS` is resilient; full reconstruct from broker is Phase 4 scope. |

## Self-Check: PASSED

- [x] `bot/state/migrations.py` exists and contains `PRAGMA user_version`, `run_migrations`, `CURRENT_VERSION = 1`, and `MIGRATIONS` list
- [x] `bot/state/store.py` exists and contains `StateStore`, `atomic_write_json`, `resolve_db_path`, `DEFAULT_DB_PATH`
- [x] `grep -n "os.replace" bot/state/store.py` returns matches (line 87)
- [x] `grep -n "PRAGMA user_version" bot/state/migrations.py` returns matches (lines 108, 120, 123)
- [x] `grep -n "tempfile.mkstemp" bot/state/store.py` returns match (line 73)
- [x] `python3 -m pytest tests/state -q` exits 0 (49 tests pass)
- [x] `python3 -m pytest tests/ -q` exits 0 (103 total tests pass — full suite green)
- [x] Commits 265ea9b and 68a4ac7 exist in git log
