---
phase: "04-order-and-position-management"
plan: "02"
subsystem: "position-manager"
tags: ["position", "fsm", "db-first", "fill-reconciliation", "daily-count", "restart", "POS-05"]
dependency_graph:
  requires:
    - "bot/position/state.py PositionPhase + PositionState FSM (04-01)"
    - "bot/execution/events.py FillEvent (04-01)"
    - "bot/state/store.py upsert_position + get_open_positions (04-01)"
    - "bot/execution/engine.py ExecutionEngine manage_exit (04-03)"
    - "bot/strategy/trend_join_long.py compute_swing_low_2_2"
    - "bot/signal/bar_aggregator.py _bar_buffer Dict[str, deque]"
    - "bot/safety/audit_log.py append_audit"
    - "bot/safety/et_helpers.py now_et/to_et"
  provides:
    - "bot/position/manager.py PositionManager: on_fill, on_bar, _persist_position"
    - "bot/position/manager.py: _trigger_partial_profit, _trigger_breakeven, _trigger_stop_out, _trigger_trail_up"
    - "bot/position/manager.py: reconstruct_from_store, flush_all, register_position"
    - "bot/position/manager.py: _row_to_position_state helper"
    - "tests/position/test_manager.py: 29 passing tests + 1 stub for 04-04"
  affects:
    - "tests/position/test_manager.py — test_restart_reconciliation implemented (POS-05); test_force_close_half_day stub for 04-04"
tech_stack:
  added: []
  patterns:
    - "DB-first persistence: StateStore.upsert_position() committed before in-memory PositionState mutation (Pitfall G)"
    - "order_id-only fill matching: _find_position_by_entry_order_id / _find_position_by_exit_order_id keyed by order_id string, never by code+qty"
    - "Never-loosen trail: max(trail_stop, new_swing_low) applied in evaluate_close(); swing-low from bar_buffer via compute_swing_low_2_2()"
    - "Partial exit guard: remaining_quantity decremented by filled_qty; CLOSED only when remaining_quantity==0 (Pitfall E)"
    - "Dependency injection: ExecutionEngine injected into PositionManager (avoids circular import with 04-03)"
    - "bar_buffer injection: optional Dict[str, deque] from BarAggregator._bar_buffer; None-safe (_compute_swing_low returns None if no buffer)"
key_files:
  created:
    - "bot/position/manager.py"
  modified:
    - "tests/position/test_manager.py"
decisions:
  - "ExecutionEngine injected into PositionManager (not imported) to avoid circular import between 04-02 and 04-03"
  - "bar_buffer injected as optional constructor param; _compute_swing_low returns None when None (test-safe; no live BarAggregator needed in unit tests)"
  - "_place_exit_order returns None — manage_exit is fire-and-forget; exit fill reconciliation handled via on_fill matching by exit_order_id set separately"
  - "PositionState.opened_at/updated_at always required non-None for upsert_position (NOT NULL constraint from migration 0001); tests always set defaults"
  - "asyncio.run() used in async tests (Python 3.14 removed implicit default event loop)"
metrics:
  duration: "~25 minutes"
  completed: "2026-06-24"
  tasks: 1
  files: 2
---

# Phase 04 Plan 02: PositionManager Summary

**One-liner:** PositionManager orchestrates the per-position FSM (fill/bar processing, DB-first persistence, order_id-keyed fill matching, daily-count increment, pending-intent resolution, swing-low trail, and restart reconstruction), backed by 29 green unit tests covering all POS-05 and EXEC-05/Pitfall E/G invariants.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | PositionManager fill + bar processing with DB-first persistence and order_id reconciliation | 713ac2f | bot/position/manager.py, tests/position/test_manager.py |

## Artifacts Produced

### New File

**bot/position/manager.py** — `PositionManager` class:

- `__init__(store, engine, cfg, strategy, bar_buffer=None)`: injects all dependencies; `bar_buffer` is the optional `BarAggregator._bar_buffer` dict for swing-low computation.
- `on_fill(fill)`: dispatches to `_on_entry_fill` (AWAITING_FILL→ACTIVE, daily count +1, pending_intent RESOLVED) or `_on_exit_fill` (remaining_quantity decremented by order_id-matched qty; CLOSED only when remaining_quantity==0).
- `on_bar(bar)`: evaluates FSM transitions on bar close for ACTIVE/PARTIAL_TAKEN/BREAKEVEN/TRAILING positions; computes swing-low from `_bar_buffer` if available; dispatches to `_trigger_*` handlers.
- `_persist_position(pos, event)`: DB-first — calls `store.upsert_position(pos)` and commits BEFORE returning; appends audit entry.
- `_trigger_partial_profit(pos, qty, time_key)`: persists PARTIAL_TAKEN phase DB-first; asks engine to place exit.
- `_trigger_breakeven(pos, time_key)`: persists BREAKEVEN (trail_stop=entry_price) DB-first.
- `_trigger_stop_out(pos, qty, time_key)`: persists CLOSED DB-first; asks engine to place exit.
- `_trigger_trail_up(pos, time_key)`: persists TRAILING with new trail_stop DB-first.
- `reconstruct_from_store()`: loads all non-CLOSED positions from StateStore into `_positions` on startup (POS-05).
- `flush_all()`: persists all in-memory positions (kill-switch registration hook, 04-04).
- `register_position(pos)`: registers a new AWAITING_FILL position in `_positions` and DB.
- `_find_position_by_entry_order_id(order_id)`: order_id-only matching for entry fills (EXEC-05).
- `_find_position_by_exit_order_id(order_id)`: order_id-only matching for exit fills (EXEC-05).
- `_compute_swing_low(code)`: builds DataFrame from bar_buffer deque; calls `strategy.compute_swing_low_2_2(df)`.
- `_increment_daily_filled_count(fill_time)`: upserts daily_trade_count.filled_count+1 for the ET session date (D-08).
- `_resolve_pending_intent(intent_id, fill_time)`: sets pending_intents status PENDING→RESOLVED (D-12).
- `_row_to_position_state(row)` (module-level helper): converts StateStore DB row dict to PositionState (POS-05 reconstruction).

### Modified File

**tests/position/test_manager.py** — 29 new passing tests across:

- `TestEntryFill` (5 tests): phase→ACTIVE, DB persisted, daily_count+1, pending_intent RESOLVED, unknown order_id noop
- `TestExitFill` (4 tests): partial exit does not close, full exit closes, order_id-only matching, unknown order_id noop
- `TestDBFirstPersistence` (2 tests): crash-sim asserts DB row reflects new phase; second-connection read confirms immediate commit
- `TestTrailNeverLoosen` (3 tests): lower swing-low leaves trail_stop unchanged, higher raises it, None bar_buffer is no-op
- `TestOnBarStopOut` (4 tests): close at trail_stop triggers CLOSED, AWAITING_FILL noop, CLOSED noop, no-position noop
- `TestOnBarPartialProfit` (1 test): close at 0.75R triggers PARTIAL_TAKEN, remaining_quantity=201
- `TestRestartReconciliation` (4 tests): full restart round-trip (POS-05), multiple positions, empty store, BREAKEVEN trail_stop preserved
- `TestFlushAll` (1 test): flush_all writes all positions to DB
- `TestRegisterPosition` (1 test): register_position stores in _positions + DB
- `TestOrderIdOnlyMatching` (1 test): source grep — no `(code, qty)` tuple key in manager.py (T-04-05)
- `TestRowToPositionState` (2 tests): round-trip DB row, invalid phase raises ValueError
- `test_force_close_half_day` — **intentional stub** for 04-04

## Verification Results

```
# Targeted acceptance test
pytest tests/position/test_manager.py::TestRestartReconciliation::test_restart_reconciliation -x -q
  1 passed in 0.03s

# Full position suite
pytest tests/position/ -q
  32 passed, 1 failed (test_force_close_half_day intentional stub for 04-04)

# Full suite (regression check)
pytest -q
  369 passed, 3 failed
  - test_entry_placed_simulate: pre-existing manual SIMULATE stub (04-03)
  - test_duplicate_guard: pre-existing stub for 04-04
  - test_force_close_half_day: intentional stub for 04-04
  Previously: 340 passed, 4 failed — net gain of 29 tests, 0 regressions

# order_id-only matching confirmed
grep -n "(code, qty)" bot/position/manager.py  → no results (grep gate: PASSED)
grep -n "entry_order_id\|exit_order_id" bot/position/manager.py  → 17 hits (only order_id matching)

# No hardcoded thresholds
grep -n "0.75\|0.3333\|1.0.*R" bot/position/manager.py  → no results (CFG-01: PASSED)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] daily_trade_count requires updated_at NOT NULL**
- **Found during:** Task 1 verification
- **Issue:** Migration 0003 schema for `daily_trade_count` has `updated_at TEXT NOT NULL`. The original `_increment_daily_filled_count` INSERT omitted `updated_at`, causing a SQLite integrity constraint violation on first fill.
- **Fix:** Added `updated_at` (using `now_et().isoformat()`) to both the INSERT and the ON CONFLICT DO UPDATE in `_increment_daily_filled_count`.
- **Files modified:** `bot/position/manager.py`
- **Commit:** 713ac2f

**2. [Rule 1 - Bug] Test helper `_make_pos` needed default opened_at/updated_at**
- **Found during:** Task 1 test execution
- **Issue:** `positions` table has `opened_at TEXT NOT NULL` and `updated_at TEXT NOT NULL` (migration 0001). The original `_make_pos` factory set them to `None`, causing upsert_position to fail with `NOT NULL constraint failed: positions.opened_at`.
- **Fix:** Added default `_DEFAULT_OPENED_AT` / `_DEFAULT_UPDATED_AT` (UTC datetime) to `_make_pos` helper; updated affected test fixtures.
- **Files modified:** `tests/position/test_manager.py`
- **Commit:** 713ac2f

**3. [Rule 1 - Bug] Source grep test false-positive on docstring**
- **Found during:** Task 1 test execution (test_manager_source_uses_order_id_not_qty_tuple)
- **Issue:** The grep guard for `"(code, qty)"` in manager.py matched docstring text in the module-level safety docstring and `on_fill` docstring, not any actual fill-matching code.
- **Fix:** Changed docstring phrasing to `"code+quantity pair"` / `"code+qty"` instead of `"(code, qty)"` — preserves the documentation intent without tripping the source-inspection test.
- **Files modified:** `bot/position/manager.py`
- **Commit:** 713ac2f

**4. [Rule 1 - Bug] asyncio.get_event_loop() no longer works in Python 3.14**
- **Found during:** Task 1 test execution
- **Issue:** Python 3.14 raises `RuntimeError: There is no current event loop in thread 'MainThread'` when `asyncio.get_event_loop()` is called outside an async context. The async on_bar calls in tests used this pattern.
- **Fix:** Replaced all `asyncio.get_event_loop().run_until_complete(...)` with `asyncio.run(...)` in test_manager.py (same pattern established in 03-02).
- **Files modified:** `tests/position/test_manager.py`
- **Commit:** 713ac2f

## Known Stubs

| File | Test | Reason |
|------|------|--------|
| tests/position/test_manager.py | test_force_close_half_day | Implemented in 04-04 (POS-04 EOD force-close) |
| tests/execution/test_engine.py | test_entry_placed_simulate | Manual live SIMULATE test (pre-existing from 04-03) |
| tests/execution/test_engine.py | test_duplicate_guard | Implemented in 04-04 (EXEC-04 broker guard) |

## Threat Flags

No new threat surfaces beyond the plan's threat model. Mitigations confirmed implemented:

| Threat ID | Mitigation Status |
|-----------|------------------|
| T-04-05 (exit fill mis-keying) | `_find_position_by_exit_order_id` uses `str(pos.exit_order_id) == str(order_id)`; `test_exit_fill_matched_by_order_id_only` confirms same-qty different-order positions are not confused |
| T-04-06 (crash mid-transition) | `_persist_position` calls `store.upsert_position()` (which commits immediately) BEFORE returning; `test_db_row_updated_before_inmemory_mutation` and `test_persist_position_commits_immediately` both verify this |
| T-04-07 (trail loosen on bar) | `evaluate_close()` in state.py uses `max(trail_stop, new_swing_low)`; `test_lower_swing_low_does_not_reduce_trail_stop` asserts trail_stop unchanged when swing-low=95.0 and trail_stop=98.0 |
| T-04-08 (half-exit leaves risk unmanaged) | `remaining_quantity == 0` is the only CLOSED gate; `test_partial_exit_fill_does_not_close_position` verifies 100-of-300 fill leaves remaining=200 and phase!=CLOSED |
| T-04-09 (audit disclosure) | `append_audit` in `_persist_position` logs code/phase/qty/order_id only — no credentials or sensitive data |

## Self-Check: PASSED

- bot/position/manager.py: FOUND
- tests/position/test_manager.py: FOUND (updated)
- Commit 713ac2f: FOUND (git log)
- 29 new manager tests GREEN: CONFIRMED
- test_restart_reconciliation GREEN: CONFIRMED
- test_force_close_half_day remains RED (intentional stub): CONFIRMED
- Full suite: 369 passed, 3 intentional stubs — 0 regressions vs prior 340: CONFIRMED
- order_id grep gate: PASSED (no code+qty tuple matching)
- CFG-01 no-literals gate: PASSED (no 0.75/0.3333/1.0 in manager.py)
