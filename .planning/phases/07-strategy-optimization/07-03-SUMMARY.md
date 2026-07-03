---
phase: 07-strategy-optimization
plan: "03"
subsystem: position-management, gateway, stop-protection
tags: [tdd, stop-orders, broker-stop, quote-tick, risk-tick-stop]
dependency_graph:
  requires: [07-01]
  provides: [RISK-TICK-STOP, D-01, D-02, D-04]
  affects: [bot/gateway/gateway.py, bot/position/manager.py, bot/service/bot.py, bot/position/state.py, bot/state/store.py]
tech_stack:
  added: [QuoteTickHandler, subscribe_quote, place_stop_order]
  patterns: [TDD RED/GREEN, cancel-replace (finding-1.4), deferred-import, run_in_executor, one-shot guard]
key_files:
  created: []
  modified:
    - bot/gateway/gateway.py
    - bot/position/manager.py
    - bot/position/state.py
    - bot/state/store.py
    - bot/service/bot.py
    - tests/gateway/test_order_methods.py
    - tests/position/test_manager.py
    - tests/service/test_bot.py
    - tests/state/test_store_concurrency.py
decisions:
  - "place_stop_order uses OrderType.STOP + price=0.0 + aux_price=stop_price (EXEC-02 amendment D-01)"
  - "arm_stop_protection dispatches on use_broker_stop_orders; non-fatal on placement failure (D-03 backstop)"
  - "_sync_broker_stop uses cancel→re-place pattern; swallows cancel exceptions (finding-1.4)"
  - "_on_quote one-shot guard prevents double-fire on repeated ticks (D-02 exactly-once)"
  - "_trigger_breakeven and _trigger_trail_up made async to await _sync_broker_stop"
metrics:
  duration: "~90 minutes (session resumed from summary)"
  completed: "2026-07-03"
  tasks_completed: 3
  tasks_total: 3
  tests_before: 545
  tests_after: 557
  files_changed: 9
---

# Phase 07 Plan 03: Tick/Broker-Level Stop Invalidation (RISK-TICK-STOP) Summary

Broker-side Stop-Market protective SELL orders and quote-tick fallback monitoring for tick-granularity stop invalidation (D-01/D-02/D-04), filling the gap where stops only fired at the next 5m bar close.

## What Was Built

### Task 1: Gateway.place_stop_order + arm_stop_protection broker path

- `Gateway.place_stop_order(code, qty, stop_price, trd_side)`: the sole `OrderType.STOP` path (EXEC-02 amendment D-01). Uses `price=0.0` + `aux_price=stop_price`, same `run_in_executor`/deferred-import/_check_ret/append_audit shape as `place_order`. Audit event `stop_order_placed`. Returns broker order_id.
- `PositionState.broker_stop_order_id: Optional[str] = None`: new dataclass field persisted to the `positions.broker_stop_order_id` column (migration 0005).
- `upsert_position` in `store.py`: extended to persist `broker_stop_order_id` in INSERT + ON CONFLICT UPDATE.
- `_row_to_position_state` in `manager.py`: reconstructs `broker_stop_order_id` from DB row on restart.
- `PositionManager.__init__`: added `gateway=None` param stored as `self._gateway`.
- `arm_stop_protection(pos)`: dispatches on `cfg.use_broker_stop_orders`. Broker path calls `place_stop_order(TrdSide.SELL)`, persists `broker_stop_placed`. Non-fatal on failure (bar-close FSM is D-03 backstop).
- Post-fill hook in `bot.service.bot._process_bar`: `await self._position_manager.arm_stop_protection(pos)` after `on_fill`.

**Commits:** `3303475` (RED), `5580384` (GREEN)

### Task 2: _sync_broker_stop cancel-replace on trail ratchet (D-04/D-11)

- `_sync_broker_stop(pos)`: cancel-replace pattern (finding-1.4). Cancels `old_id` (swallows exceptions for already-filled/cancelled), re-places at the current `pos.trail_stop` (already ratcheted by `evaluate_close` max() — D-11 enforced upstream). Updates `pos.broker_stop_order_id`. DB-first persist with `broker_stop_replaced` event.
- `_trigger_breakeven` and `_trigger_trail_up` made **async** and extended with `await self._sync_broker_stop(pos)` guard behind `cfg.use_broker_stop_orders`. `on_bar` updated to `await` both.

**Commits:** `5671ddf` (RED), `965e3ec` (GREEN)

### Task 3: D-02 quote-tick fallback + subscribe_quote + _on_quote

- `QuoteTickHandler`: module-level handler class (QuoteHandlerBase pattern). `on_recv_rsp` bridges each quote push to a `callback(code, bid_price)`. Non-fatal on exceptions.
- `Gateway.subscribe_quote(codes, callback)`: deferred `from moomoo import SubType`. Registers `QuoteTickHandler` then subscribes `[SubType.QUOTE]`. Raises `GatewayError` on non-RET_OK.
- `arm_stop_protection` else branch: when `use_broker_stop_orders=False`, calls `gateway.subscribe_quote([pos.code], callback=...)` where callback dispatches to `_on_quote`. Non-fatal.
- `_on_quote(code, bid_price)`: if bid <= `pos.trail_stop` and code not in `_quote_exiting`, adds to `_quote_exiting` (one-shot guard), logs `quote_stop_triggered`, calls `engine.manage_exit`. Silent if bid > trail_stop.
- `_quote_exiting: set` added to `PositionManager.__init__` for the one-shot guard.

**Commits:** `49efcdc` (RED), `3b29af8` (GREEN)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_store_concurrency.py` SimpleNamespace missing `broker_stop_order_id`**
- **Found during:** Task 1 GREEN full-suite run
- **Issue:** Concurrency test `_seed_positions` builds a `types.SimpleNamespace` as a mock pos object. After extending `upsert_position` to include `broker_stop_order_id`, the SimpleNamespace hit `AttributeError`.
- **Fix:** Added `broker_stop_order_id=None` to the SimpleNamespace in `_seed_positions`.
- **Files modified:** `tests/state/test_store_concurrency.py`
- **Commit:** `5580384`

**2. [Rule 1 - Bug] `test_bot.py` `_make_bot_with_mocks` mock missing `arm_stop_protection` AsyncMock**
- **Found during:** Task 1 GREEN full-suite run
- **Issue:** `_make_bot_with_mocks` sets up `mock_position_manager = MagicMock()` with `on_bar = AsyncMock()` but not `arm_stop_protection`. After adding `await self._position_manager.arm_stop_protection(pos)` in `_process_bar`, two existing tests hit `TypeError: 'MagicMock' object can't be awaited`.
- **Fix:** Added `mock_position_manager.arm_stop_protection = AsyncMock()` to the fixture.
- **Files modified:** `tests/service/test_bot.py`
- **Commit:** `5580384`

**3. [Rule 1 - Bug] `test_on_bar_trail_up_triggers_sync_broker_stop` test design: bar_buffer required**
- **Found during:** Task 2 GREEN targeted run
- **Issue:** The test for trail_up integration provided `bar_buffer=None` (default). `_compute_swing_low` short-circuits to `None` before reaching the mock strategy, so `evaluate_close` saw `new_swing_low=None` and returned `FSM_ACTION_NONE`. The trail_up path never fired.
- **Fix:** Provided a minimal `bar_buffer` dict with a non-empty `deque` for `US.AAPL` so `_compute_swing_low` reaches `strategy.compute_swing_low_2_2` (which returns 103.0).
- **Files modified:** `tests/position/test_manager.py`
- **Commit:** `965e3ec`

## Security / Threat Mitigations

| Threat | Mitigation Applied |
|--------|--------------------|
| T-07-09: EXEC-02 stop path leaks real-money order | `place_stop_order` uses `self.cfg.trd_env` (SIMULATE); no `TrdEnv.REAL` literal; SAFE-01 paper guard unchanged |
| T-07-10: Wrong trd_side on protective stop opens exposure | All callers pass `TrdSide.SELL`; asserted in tests (`test_broker_path_uses_trd_side_sell`) |

## Threat Flags

None — all new paths confined to SIMULATE; no new network endpoints or trust-boundary crossings beyond those in the threat register.

## Known Stubs

None — all behavior wired end-to-end. Quote-fallback callback uses `asyncio.get_event_loop().create_task(...)` which is correct for the push thread → event loop bridge; this is not a stub.

## Verification Results

```
python3 -m pytest tests/ -q
557 passed, 1 skipped in 5.83s
```

Tests before this plan: 545. Tests after: 557 (+12 new tests, 6 RED gate commits + 6 GREEN).

## Self-Check: PASSED

- [x] `bot/gateway/gateway.py` — `place_stop_order` and `subscribe_quote` present
- [x] `bot/position/manager.py` — `arm_stop_protection`, `_sync_broker_stop`, `_on_quote` present
- [x] `bot/position/state.py` — `broker_stop_order_id` field present
- [x] `bot/state/store.py` — `broker_stop_order_id` in upsert_position
- [x] `bot/service/bot.py` — `arm_stop_protection(pos)` called post-fill
- [x] All 6 task commits present: 3303475, 5580384, 5671ddf, 965e3ec, 49efcdc, 3b29af8
- [x] 557 tests pass, 0 failures
