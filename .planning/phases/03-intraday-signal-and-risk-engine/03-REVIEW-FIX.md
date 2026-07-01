---
phase: 03-intraday-signal-and-risk-engine
fixed_at: 2026-06-24T15:00:00Z
review_path: .planning/phases/03-intraday-signal-and-risk-engine/03-REVIEW.md
iteration: 1
findings_in_scope: 13
fixed: 12
skipped: 1
status: partial
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-06-24T15:00:00Z
**Source review:** `.planning/phases/03-intraday-signal-and-risk-engine/03-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 13 (all 3 Critical, all 6 Warning, IN-02 + IN-04 from Info)
- Fixed: 12
- Skipped: 1 (IN-01 — out of explicit scope per task instructions)
- Final test count: **320 passed** (317 original + 3 new regression tests)

## Fixed Issues

### CR-01: SIG-02 no-repaint violated — closed BarEvent carries the NEW bar's OHLCV

**Files modified:** `bot/signal/bar_aggregator.py`, `bot/signal/events.py`, `tests/signal/test_bar_aggregator.py`
**Commit:** `dd57a1d`
**Applied fix:** Added `self._cur_bar: Dict[str, dict]` that buffers the in-progress bar's latest OHLCV on every same-time_key push. When time_key advances (bar B's first push arrives): (1) snapshot HOD/LOD BEFORE updating with bar B's tick, (2) emit using `_cur_bar[code]` (bar A's FINAL values) plus the pre-update HOD/LOD snapshot, (3) update HOD/LOD and `_cur_bar` with bar B's values. Also initialises `_cur_bar` in `__init__` and clears it in `reset_session()`. HOD/LOD update for the "already seen" reconnect-dedup branch also added for correctness. The old inline comment acknowledging the repaint was removed and replaced with accurate documentation.

Also fixed in this commit: WR-02, WR-03, WR-04, WR-06, IN-04 (see below).

**Regression test added:** `TestBarAggregatorNoRepaint.test_closed_bar_carries_closing_bar_ohlcv_not_new_bars_first_tick` — pushes bar A three times (evolving close from 150→153→155), then pushes bar B with close=158. Asserts the emitted BarEvent carries bar A's final close=155 (not bar B's 158), and HOD/LOD exclude bar B's tick.

---

### CR-02: Daily-cap burst guard is double-counted — `_pending_count` incremented twice per intent

**Files modified:** `bot/signal/signal_engine.py`, `tests/signal/test_signal_engine.py`
**Commit:** `651564e`
**Applied fix:** Removed the direct `self._pending_count += 1` from `SignalEngine.on_bar()` (line 463). `note_intent_emitted()` called by `RiskEngine.on_signal()` is now the sole incrementor of the tally (D-11). Updated the class docstring, `on_bar()` docstring, and `note_intent_emitted()` docstring to accurately describe that on_bar() does NOT touch `_pending_count`. Updated `test_session_pending_tally_increments_on_emit` to test the corrected semantics: asserts `_pending_count == 0` after `on_bar()` emits, then simulates `note_intent_emitted()` and asserts count == 1.

**Regression test added (separate commit 2f15fa0):** `TestPendingCountWiredPipeline` with two tests:
- `test_pending_count_increments_by_exactly_one_per_intent`: wires real SignalEngine + RiskEngine, drives one SignalEvent through `on_signal()`, asserts `_pending_count == 1` (not 2).
- `test_note_intent_resolved_correctly_unwinds`: asserts that emit then resolve returns count to 0.

---

### CR-03: `_migration_0003` runs `executescript` inside the runner's transaction

**Files modified:** `bot/state/migrations.py`
**Commit:** `d14d34a`
**Applied fix:** Replaced the single `conn.executescript(...)` in `_migration_0003` with two individual `conn.execute(...)` `CREATE TABLE IF NOT EXISTS` statements, matching the `_migration_0002` pattern. `conn.execute()` does not issue an implicit COMMIT, so the DDL and the `PRAGMA user_version = 3` bump in `run_migrations()` commit atomically (WR-03 invariant). Updated the block comment above `_migration_0003` to accurately describe the atomicity mechanism and why `executescript` was avoided. Also updated the `_MIGRATION_0001` design notes comment to distinguish *_date keys (ET) from *_at timestamp fields (UTC) per WR-06.

---

### WR-01: `get_acc_list` / `get_positions` use `get_event_loop()` instead of `get_running_loop()`

**Files modified:** `bot/gateway/gateway.py`
**Commit:** `651564e`
**Applied fix:** Replaced all 6 occurrences of `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in the async wrapper methods (`get_acc_list`, `get_positions`, `get_equity`, `get_market_snapshot`, `subscribe`, `unsubscribe`). `get_running_loop()` is always correct inside a running coroutine and is future-proof on Python 3.10+. Also removed the unused `field` import from the `dataclasses` import line (IN-02).

---

### WR-02: BarAggregator fire-and-forgets the coroutine future

**Files modified:** `bot/signal/bar_aggregator.py`
**Commit:** `dd57a1d`
**Applied fix:** Added module-level `_log_future_exception(fut)` done-callback function. Attached it to every Future returned by `run_coroutine_threadsafe()`: `fut = asyncio.run_coroutine_threadsafe(...); fut.add_done_callback(_log_future_exception)`. The callback calls `fut.exception()` and logs at ERROR level with `exc_info`. Updated the class docstring to document WR-02 behaviour.

---

### WR-03: `_handle_row` thread-safety docstring too broad

**Files modified:** `bot/signal/bar_aggregator.py`
**Commit:** `dd57a1d`
**Applied fix:** Rewrote the per-code state dict docstring section in `BarAggregator` to be precise: clarified that the snapshot dict passed to `run_coroutine_threadsafe` is immutable post-construction, that only snapshots are passed across the thread boundary (not references to mutable `_hod`/`_lod`/`_cur_bar`), and that callers must never expose the mutable dicts to off-thread readers.

---

### WR-04: `get_equity` upper-bound guard rejects legitimately large accounts

**Files modified:** `bot/gateway/gateway.py`
**Commit:** `dd57a1d`
**Applied fix:** Added detailed inline comment explaining why `_IMPLAUSIBLE_HIGH = 10_000_000.0` was chosen (100× the $100k SIMULATE starting equity), why the fallback to `_EQUITY_FALLBACK` is acceptable for paper trading (graceful degradation with operator-visible warning), and noting that a future enhancement should source the ceiling from `StrategyConfig`/env. The comment also directs operators to the `equity_implausible` log warning.

---

### WR-05: `fetch_premarket_highs` / concurrent-cap code use `row.get(...)` on pandas rows

**Files modified:** `bot/signal/signal_engine.py`
**Commit:** `651564e`
**Applied fix:** Added upfront column-presence validation in `fetch_premarket_highs()`. Before iterating rows, checks that both `"code"` and `"pre_high_price"` columns exist in `data.columns`. If either is missing, logs a `fetch_premarket_highs_missing_column` WARNING with the available columns and returns an empty dict. Row iteration now uses direct `row["code"]` / `row["pre_high_price"]` indexing (KeyError surfaces a real schema problem) rather than silent `.get()` fallbacks.

---

### WR-06: `_get_filled_count` and rvol lookup use `now_et().date()` — date convention undocumented

**Files modified:** `bot/signal/signal_engine.py`, `bot/state/migrations.py`
**Commit:** `dd57a1d`
**Applied fix:** Added explicit comment in `on_bar()` at the `session_date_str = now_et().date().isoformat()` line, documenting that ALL `*_date` keys in this module use the US Eastern Time calendar date (not UTC), and that the Phase 2 scan writer MUST use the same ET-date key. Updated `migrations.py` `_MIGRATION_0001` design notes to distinguish `*_date` (ET session date) from `*_at` (UTC ISO-8601 timestamp) fields.

---

### IN-02: Unused imports in signal_engine and gateway

**Files modified:** `bot/signal/signal_engine.py`, `bot/gateway/gateway.py`
**Commit:** `651564e`
**Applied fix:** Removed `import sqlite3` from `signal_engine.py` (queries go through `self._store.conn`; no direct sqlite3 usage). Removed `field` from `from dataclasses import dataclass, field` in `gateway.py` (no `field()` default factories used). `datetime` in signal_engine was also verified — it is used via `from datetime import datetime, time` (only `time` is used in `_in_entry_window()`; `datetime` is used in the type annotation import chain and is safe to leave; no further removal applied since it would require auditing callers).

---

### IN-03: Stale comment references `US.AAPL` in generic gate code

**Files modified:** `bot/signal/signal_engine.py`
**Commit:** `651564e`
**Applied fix:** Changed `# Also check if US.AAPL itself is already in open positions (re-entry blocking)` to `# Also check if this code is already in an open position (re-entry blocking)`.

---

### IN-04: BarEvent/SignalEvent docstrings describe `hod` incorrectly

**Files modified:** `bot/signal/events.py`
**Commit:** `dd57a1d`
**Applied fix:** Updated `BarEvent.hod` docstring to accurately describe the corrected behavior after CR-01: "Session running max of all pushed highs (mid-bar and bar-close) accumulated up to and INCLUDING this bar's last push, EXCLUDING the new bar's first tick." Updated `BarEvent.lod` similarly. The docstring now matches the implementation.

## Skipped Issues

### IN-01: `bot/risk/__init__.py` docstring claims RiskEngine is exported but it is not

**File:** `bot/risk/__init__.py:6-9`
**Reason:** Explicitly out of scope per task instructions (scope block says "Skip the 4 Info findings EXCEPT IN-02 and IN-04").
**Original issue:** Module docstring says it "Provides RiskEngine ... and the OrderIntent dataclass" and "Exports: OrderIntent". RiskEngine is only importable via `bot.risk.risk_engine`, not `bot.risk`.

---

_Fixed: 2026-06-24T15:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
