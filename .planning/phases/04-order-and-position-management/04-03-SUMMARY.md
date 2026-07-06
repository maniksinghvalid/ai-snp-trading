---
phase: "04-order-and-position-management"
plan: "03"
subsystem: "execution-engine"
tags: ["execution", "gateway", "order", "fill", "ttl", "cancel-replace", "EXEC-01", "EXEC-02", "EXEC-03", "EXEC-05"]
dependency_graph:
  requires:
    - "bot/execution/events.py FillEvent (04-01)"
    - "bot/config/loader.py StrategyConfig execution block (04-01)"
    - "bot/state/store.py StateStore + pending_intents (04-01)"
    - "bot/gateway/gateway.py MoomooGateway base (Phase 1)"
  provides:
    - "bot/gateway/gateway.py: place_order, cancel_order, get_order_fills, get_order_status, get_ask_price, get_bid_price"
    - "bot/execution/engine.py: ExecutionEngine with consume_intent, _manage_entry_order, manage_exit"
    - "tests/gateway/test_order_methods.py: 13 gateway order method tests"
    - "tests/execution/test_engine.py: 4 engine tests green (EXEC-01/02/03/05)"
  affects:
    - "bot/gateway/gateway.py — 6 new async order methods added after unsubscribe()"
    - "tests/execution/test_engine.py — 3 stubs replaced with real tests + 1 new unit test"
tech_stack:
  added: []
  patterns:
    - "Deferred SDK import in gateway methods — mirrors subscribe/unsubscribe pattern (lines 433/470)"
    - "TTL poll loop: asyncio event loop time + entry_poll_interval_seconds sleep (engine._manage_entry_order)"
    - "order_id reconciliation: [f for f in fills if str(f['order_id']) == order_id] (EXEC-05)"
    - "Deferred TrdSide sentinel (_TrdSide_BUY = None, lazily resolved via _get_trd_side_buy())"
    - "config-swap test pattern: _MockCfg(entry_max_retries=N) to prove CFG-01 behavioral compliance"
key_files:
  created:
    - "bot/execution/engine.py"
    - "tests/gateway/test_order_methods.py"
  modified:
    - "bot/gateway/gateway.py"
    - "tests/execution/test_engine.py"
decisions:
  - "Engine delegates OrderType.NORMAL to gateway.place_order — engine.py never references market order type (EXEC-02)"
  - "Stateful async mock pattern used in test_ttl_cancel_replace: placed_orders list tracks which order_id was placed, fills respond only after second place — avoids side_effect StopAsyncIteration from too-few mocked returns"
  - "Rule 1 auto-fix: removed skills/moomooapi path references from gateway.py docstrings (triggered D02 compliance check test_gateway_does_not_import_from_common)"
metrics:
  duration: "~20 minutes"
  completed: "2026-06-24"
  tasks: 2
  files: 4
---

# Phase 04 Plan 03: Gateway Order Methods + ExecutionEngine Summary

**One-liner:** Gateway gains 6 async order methods (place_order/cancel_order/fills/status/ask/bid, deferred imports, OrderType.NORMAL only), and ExecutionEngine implements the entry TTL poll loop (ask+buffer, cancel-replace up to entry_max_retries, EXPIRED abandon, partial-fill accepted), exit escalation (retry-until-flat), and order_id fill reconciliation — all SIMULATE, never a market order, all tunables from rules.json.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Gateway order methods — place_order/cancel_order/fills/status/bid/ask | 370606c | bot/gateway/gateway.py, tests/gateway/test_order_methods.py |
| 2 | ExecutionEngine — entry TTL loop, exit escalation, order_id reconciliation | 8e11afe | bot/execution/engine.py, tests/execution/test_engine.py, bot/gateway/gateway.py |

## Artifacts Produced

### New Files

- **bot/execution/engine.py** — `ExecutionEngine` class:
  - `consume_intent(intent)` public wrapper
  - `_manage_entry_order(intent)` async TTL poll loop: ask+buffer pricing (D-04), place via gateway (EXEC-02), poll deal_list_query by order_id (EXEC-05), cancel-replace on TTL (EXEC-03), abandon after entry_max_retries with EXPIRED pending_intent (D-05), accept partial fill (D-06)
  - `manage_exit(code, qty, side, ...)` retry-until-flat exit with escalating prices (D-07)
  - `_resolve_intent_expired(intent_id)` writes EXPIRED status to StateStore
  - Module-level `_TrdSide_BUY`/`_TrdSide_SELL` lazy sentinels (deferred moomoo import)
  - All tunables from `cfg` (StrategyConfig) — no literals (CFG-01)

- **tests/gateway/test_order_methods.py** — 13 unit tests covering:
  - `TestPlaceOrder`: returns order_id, uses OrderType.NORMAL, EXEC-02 grep gate
  - `TestCancelOrder`: modify_order with ModifyOrderOp.CANCEL, qty=0, price=0
  - `TestGetOrderFills`: refresh_cache=True asserted, list of dicts with order_id
  - `TestGetOrderStatus`: refresh_cache=True asserted, list of dicts with order_id
  - `TestSnapshotPriceMethods`: ask/bid column resolution, last_price fallback when 0

### Modified Files

- **bot/gateway/gateway.py** — 6 new async methods (after `unsubscribe`, before `reconcile_once`):
  - `place_order(code, qty, price, trd_side) -> str`: OrderType.NORMAL only, audit log entry
  - `cancel_order(order_id) -> None`: modify_order(op=ModifyOrderOp.CANCEL, qty=0, price=0)
  - `get_order_fills(refresh_cache=True) -> list`: deal_list_query, returns dicts with order_id
  - `get_order_status(order_id="") -> list`: order_list_query, refresh_cache=True mandatory
  - `get_ask_price(code) -> float`: ask_price column, fallback to last_price when 0
  - `get_bid_price(code) -> float`: bid_price column, fallback to last_price when 0

- **tests/execution/test_engine.py** — 4 stubs replaced with real tests + 1 new:
  - `test_entry_placed_simulate_unit` (NEW): EXEC-01 automated mock-gateway path
  - `test_no_market_orders` (implemented): grep gate + behavioral assert (EXEC-02)
  - `test_ttl_cancel_replace` (implemented): 3-scenario test (EXEC-03/D-05/CFG-01 config-swap)
  - `test_fill_by_order_id` (implemented): order_id reconciliation vs. same-code different-order_id (EXEC-05)
  - `test_entry_placed_simulate` (remains stub): manual live SIMULATE test (04-VALIDATION)
  - `test_duplicate_guard` (remains stub): implemented in 04-04

## Verification Results

```
# Plan acceptance criteria verification
pytest tests/execution/test_engine.py::test_entry_placed_simulate_unit \
       tests/execution/test_engine.py::test_no_market_orders \
       tests/execution/test_engine.py::test_ttl_cancel_replace \
       tests/execution/test_engine.py::test_fill_by_order_id -x -q
4 passed in 0.47s

pytest tests/gateway/test_order_methods.py -x -q
13 passed in 0.13s

grep -c "OrderType.MARKET" bot/execution/engine.py bot/gateway/gateway.py
  engine.py: 0  gateway.py: 0

python3 -c "import bot.execution.engine; import bot.gateway.gateway"
  PASSED (no moomoo-api installed)

# Full suite
pytest -q
  340 passed, 4 failed (all 4 are intentional stubs for 04-04)
  Previously: 323 passed, 7 failed — net gain of 17 tests
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed skills/moomooapi references from gateway.py docstrings**
- **Found during:** Task 2 full suite run
- **Issue:** Task 1's gateway.py docstrings for `cancel_order`, `get_ask_price`, and `get_bid_price` referenced the `skills/moomooapi/` path (e.g., `skills/moomooapi/scripts/trade/cancel_order.py lines 56-63`). The existing test `TestD02Compliance::test_gateway_does_not_import_from_common` in `tests/gateway/test_gateway.py` asserts `"skills/moomooapi" not in source` — this test was passing before Task 1 but broke when our docstrings added those path references.
- **Fix:** Replaced the path references with description-only text (no file path strings). The docstrings now say "per the moomoo SDK cancel semantics (the SDK requires qty/price even for a cancel operation)" instead of citing the skills path.
- **Files modified:** `bot/gateway/gateway.py`
- **Commit:** 8e11afe (bundled into Task 2 commit)

## Known Stubs

The following intentional stubs remain for future plans:

| File | Test | Reason |
|------|------|--------|
| tests/execution/test_engine.py | test_entry_placed_simulate | Manual live SIMULATE test (04-VALIDATION Manual-Only) |
| tests/execution/test_engine.py | test_duplicate_guard | Implemented in 04-04 (EXEC-04 broker guard) |
| tests/position/test_manager.py | test_force_close_half_day | Implemented in 04-04 |
| tests/position/test_manager.py | test_restart_reconciliation | Implemented in 04-04 |

## Threat Flags

No new threat surfaces beyond the plan's threat model. Mitigations confirmed implemented:

| Threat ID | Mitigation Status |
|-----------|------------------|
| T-04-10 (env elevation) | gateway.place_order uses _parse_trd_env(self.cfg.trd_env) which defaults to SIMULATE |
| T-04-11 (order type tampering) | OrderType.NORMAL in place_order; grep confirmed 0 hits for MARKET in both files |
| T-04-12 (runaway loop) | entry bounded by cfg.entry_max_retries; exit bounded by remaining==0 |
| T-04-13 (fill mis-reconciliation) | order_id matching in engine; test_fill_by_order_id proves same-code different-order_id excluded |
| T-04-14 (stale fill data) | refresh_cache=True in get_order_fills/get_order_status; asserted in tests |
| T-04-15 (audit log disclosure) | append_audit logs order_id/code/qty/price only — no credentials |

## Self-Check: PASSED
