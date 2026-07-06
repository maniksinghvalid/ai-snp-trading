---
status: complete
phase: 04-order-and-position-management
source: [04-01-SUMMARY.md, 04-02-SUMMARY.md, 04-03-SUMMARY.md, 04-04-SUMMARY.md]
started: 2026-06-24T22:08:24Z
updated: 2026-06-24T22:20:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: From a fresh state (delete any temp/dev SQLite DB), `python3 -c "import bot.execution.engine, bot.position.manager, bot.gateway.gateway"` succeeds without moomoo-api installed; opening a new StateStore migrates the schema to CURRENT_VERSION=4 and the positions table has entry_order_id, exit_order_id, avg_fill_price columns.
result: pass
evidence: IMPORTS OK (no moomoo-api); CURRENT_VERSION=4; fresh StateStore.open() → user_version=4; entry_order_id/exit_order_id/avg_fill_price all PRESENT.

### 2. Full Test Suite Green
expected: `pytest -q` reports 372 passed, 1 skipped (the manual-only live-SIMULATE entry test), 0 failures.
result: pass
evidence: 372 passed, 1 skipped in 4.34s.

### 3. Position FSM Transitions (POS-01/02/03)
expected: The PositionState FSM raises partial-profit, moves to breakeven, and trails only upward; `pytest tests/position/test_fsm.py` passes including "trail never loosens".
result: pass
evidence: 3 passed (test_partial_profit_trigger, test_breakeven_trigger, test_trail_never_loosens).

### 4. Order Execution — Marketable Limit, Never Market (EXEC-01/02/03)
expected: ExecutionEngine places marketable LIMIT entries, cancel-replaces on TTL up to entry_max_retries then abandons, and NEVER submits a market order; grep OrderType.MARKET returns 0 hits; engine + gateway order tests pass.
result: pass
evidence: grep OrderType.MARKET → engine.py:0, gateway.py:0; 18 passed, 1 skipped (manual live).

### 5. order_id-Keyed Fill Reconciliation (EXEC-05)
expected: Fills matched strictly by order_id, never by (code, qty); remaining_quantity decremented per matched exit; CLOSED only when remaining_quantity==0.
result: pass
evidence: 12 reconciliation/exit/order-id tests passed; grep "(code, qty)" in manager.py → 0.

### 6. Restart Reconciliation — Broker Truth Wins (POS-05 / D-09/10/11)
expected: startup_reconcile closes broker-flat positions, adopts qty drift, adopts orphans at avg_cost with LOD-1% stop + resubscribe, and preserves trail_stop.
result: pass
evidence: test_restart_reconciliation passed.

### 7. Duplicate-Entry Guard — Broker-Verified (EXEC-04)
expected: consume_intent checks get_positions + get_order_status (refresh_cache=True) before placing; blocks on open position OR open BUY order; place_order never called when blocked; fails open on SDK error.
result: pass
evidence: test_duplicate_guard passed.

### 8. EOD Force-Close — Calendar-Aware, Never Market (POS-04 / D-08)
expected: force_close_all derives cutoff from real market close (16:00→15:51, 13:00→12:51, never hardcoded), closes non-CLOSED positions via escalating LIMIT exits, emits force_close_stuck if not flat.
result: pass
evidence: test_force_close_half_day passed; only 15:51 occurrence is a docstring comment (line 68), not code.

## Summary

total: 8
passed: 8
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]
