---
phase: 04-order-and-position-management
verified: 2026-06-24T00:00:00Z
status: human_needed
score: 12/12 must-haves verified (automated); 1 item requires live-broker human test
overrides_applied: 0
human_verification:
  - test: "Live limit-order entry fill on SIMULATE paper account"
    expected: "Entry order placed via ExecutionEngine, FillEvent recorded in StateStore and appended to ~/.futu_trade_audit.jsonl; order_id matches the filled record"
    why_human: "Requires OpenD running and logged in on 127.0.0.1:11111 with a SIMULATE account during market hours; cannot run in CI. The automated mock-gateway path (test_entry_placed_simulate_unit) proves the code path but not the live broker round-trip. This is the EXEC-01 acceptance criterion that also verifies the StateStore + audit-log write on a real fill."
gaps: []
deferred: []
---

# Phase 4: Order and Position Management — Verification Report

**Phase Goal:** The bot turns Phase 3's verified OrderIntents into real orders on the SIMULATE (paper) account, drives each position through the full lifecycle FSM (AWAITING_FILL → ACTIVE → PARTIAL_TAKEN at 0.75R → BREAKEVEN at 1.0R → TRAILING on 5m swing-low → CLOSED), force-closes every open position at 15:51 ET (calendar-aware), and reconstructs all position state after a mid-session restart without re-entering — all judged on the 5m bar close.

**Verified:** 2026-06-24
**Status:** human_needed
**Re-verification:** No — initial verification

**Test suite result (confirmed by running `python3 -m pytest tests/ -q`):**
`372 passed, 1 skipped` — the skip is `test_entry_placed_simulate` (manual-only live SIMULATE test, documented in 04-VALIDATION.md Manual-Only Verifications; its automated counterpart `test_entry_placed_simulate_unit` is green).

---

## Goal Achievement

### Observable Truths (12 Acceptance Criteria)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | OrderIntent placed as limit buy on SIMULATE; fill recorded in StateStore + audit log | VERIFIED (auto) / UNCERTAIN (live) | `test_entry_placed_simulate_unit` (green): mock gateway verifies OrderType.NORMAL + is_entry=True FillEvent. `append_audit` called in `gateway.place_order` (line 538) and `engine._manage_entry_order` (line 281). Live broker round-trip is the manual-only item. |
| 2 | No order — entry, exit, or force-close — is ever submitted as a market order | VERIFIED | `grep -c "OrderType.MARKET" bot/execution/engine.py` → 0. `test_no_market_orders` (green): (a) grep gate asserts absence of `OrderType.MARKET` in source; (b) behavioral: place_order call args contain no "MARKET" string. `gateway.place_order` hardcodes `order_type=OrderType.NORMAL` (line 530). |
| 3 | FSM transitions AWAITING_FILL → ACTIVE → PARTIAL_TAKEN → BREAKEVEN → TRAILING → CLOSED, each unit-tested | VERIFIED | All 6 phases declared in `PositionPhase(str, Enum)` (state.py lines 52-57). `test_partial_profit_trigger`, `test_breakeven_trigger`, `test_trail_never_loosens` (all green) cover ACTIVE→PARTIAL_TAKEN, PARTIAL_TAKEN→BREAKEVEN, BREAKEVEN/TRAILING→TRAILING/CLOSED. AWAITING_FILL→ACTIVE covered in `TestEntryFill` (manager tests, 6 green tests). |
| 4 | 0.75R partial, 1.0R breakeven, and stop-out fire only on bar close; intrabar wick never triggers | VERIFIED | `evaluate_close()` signature takes a single `close: float` parameter (state.py line 153). Comment at line 165-166: "All checks use bar.close ONLY — No bar.low or bar.high is referenced here." `test_partial_profit_trigger` explicitly tests: close=100.5 above stop with wick (no trigger); close=96.0 triggers stop. `test_breakeven_trigger` tests: close=103.99 does not trigger breakeven. |
| 5 | Trailing stop only ever ratchets up; later lower swing-low never loosens it | VERIFIED | `evaluate_close()` applies `new_stop = max(self.trail_stop, new_swing_low)` (state.py line 232). `test_trail_never_loosens` (green): (b) lower swing-low=100.0 with trail_stop=101.5 leaves stop at 101.5; restart simulation: persisted trail_stop=105.0 with stale swing_low=102.0 stays at 105.0. `TestTrailNeverLoosen::test_lower_swing_low_does_not_reduce_trail_stop` (green) confirms via manager layer. |
| 6 | Unfilled entry cancel-replaced within TTL; abandoned (slot released, pending_intents resolved) after retry cap | VERIFIED | `test_ttl_cancel_replace` (green): Scenario 2 — all TTLs expire, `result is None`, `store.conn.execute` called with `EXPIRED` status. Scenario 3 — config-swap `entry_max_retries=1` produces fewer place calls than `entry_max_retries=2`. `_resolve_intent_expired` (engine.py line 454) writes `status='EXPIRED'` to pending_intents. |
| 7 | Partial entry fill kept as position sized to filled shares; under-risk, never over-risk | VERIFIED | `test_fill_by_order_id` (green): entry for 300 shares, only 100-share matched-order_id fill returned → `fill_event.filled_qty == 100`. `apply_entry_fill()` sets `full_quantity = fill.filled_qty` (state.py line 147). After matching fill, `cancel_order()` called on remainder (engine.py line 268). |
| 8 | Duplicate entry for symbol with existing broker position blocked by get_positions() check | VERIFIED | `test_duplicate_guard` (green): Path (a) — broker DataFrame with "US.AAPL" → `consume_intent` returns None, `place_order` never called. Path (b) — empty positions but open BUY order → blocked. Path (c) — no duplicate → entry proceeds. Guard at engine.py lines 131-196 uses `get_positions(refresh_cache=True)` AND `get_order_status()` before any `place_order`. |
| 9 | 1/3 partial exit reconciled by order_id; not misread as full stop-out | VERIFIED | `test_fill_by_order_id` (green): synthetic fills with `ENTRY_ORDER_ID` + `OTHER_ORDER_ID` — only matching row counted (100 shares, not 200+100=300). `_find_position_by_exit_order_id` keys on `str(pos.exit_order_id) == str(order_id)` (manager.py line 662). `TestExitFill::test_partial_exit_fill_does_not_close_position` (green): 100-of-300 fill leaves remaining=200, phase≠CLOSED. `TestOrderIdOnlyMatching::test_manager_source_uses_order_id_not_qty_tuple` (green): grep gate confirms no `(code, qty)` tuple key in manager.py. |
| 10 | All open positions force-closed at calendar-aware time; force_close_stuck emitted if still open | VERIFIED | `test_force_close_half_day` (green): (a) mock `get_market_close_et="13:00"` → `get_force_close_time_et` returns `time(12, 51)`; (b) "16:00" → `time(15, 51)`; (c) `manage_exit` called for 2 open positions (AAPL, TSLA), not for CLOSED (MSFT). `force_close_all` uses `engine.manage_exit` (EXEC-02 upheld). `force_close_stuck` audit appended at manager.py lines 350, 378. "15:51" in manager.py only appears as a docstring comment (line 68); actual time derived from `get_market_close_et() - 9 min` (lines 81-89). |
| 11 | After restart: known positions reconstructed without loosening stop; orphan adopted with derived stop; feeds re-subscribed; no re-entry | VERIFIED | `test_restart_reconciliation` (green): (a) US.AAPL absent from broker → DB phase='CLOSED'; (b) US.TSLA qty mismatch 300→250 adopted; (c) US.GOOG orphan inserted ACTIVE with `stop = lod * 0.99` (line 839-952), `subscribe` called; (d) US.NVDA trail_stop=99.5 unchanged by reconcile. `reconstruct_from_store` (manager.py line 222) loads DB rows back into `_positions`. `startup_reconcile` is in gateway.py and never calls `place_order` (no re-entry path). |
| 12 | Every numeric tunable read from rules.json; no execution/strategy literal hardcoded | VERIFIED | `rules.json` has `"execution"` block with 10 tunables (entry_limit_buffer_usd, entry_ttl_seconds, entry_max_retries, entry_poll_interval_seconds, exit_limit_buffer_usd, exit_ttl_seconds, exit_escalation_step_usd, exit_escalation_cadence_seconds, force_close_escalation_step_usd, force_close_escalation_cadence_seconds). All 10 mapped in `StrategyConfig` (loader.py lines 102-111) and used via `self._cfg` in engine and manager. Config-swap proof: `test_ttl_cancel_replace` Scenario 3 and `test_breakeven_trigger` config-swap both change behavior when cfg values differ. Grep confirms no `0.75`, `0.3333`, `1.0` as code literals in state.py/manager.py/engine.py; "15:51" appears only in a comment. |

**Score:** 12/12 truths verified (automated evidence) — 1 criterion has a live-broker dimension requiring human confirmation.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/execution/__init__.py` | Package root | VERIFIED | Exists |
| `bot/execution/events.py` | FillEvent dataclass with order_id | VERIFIED | `FillEvent` with fields: order_id, intent_id, code, filled_qty, avg_fill_price, is_entry, fill_time |
| `bot/execution/engine.py` | ExecutionEngine with consume_intent + manage_exit | VERIFIED | 470 lines; consume_intent (duplicate guard + entry loop), manage_exit (retry-until-flat), all tunables from cfg |
| `bot/position/__init__.py` | Package root | VERIFIED | Exists |
| `bot/position/state.py` | PositionPhase enum + PositionState FSM | VERIFIED | PositionPhase(str, Enum) 6 values; evaluate_close() + apply_entry_fill() pure FSM methods |
| `bot/position/manager.py` | PositionManager orchestrator | VERIFIED | 890 lines; on_fill, on_bar, reconstruct_from_store, flush_all, force_close_all, get_force_close_time_et |
| `bot/gateway/gateway.py` | place_order, cancel_order, get_order_fills, get_order_status, get_ask_price, get_bid_price, startup_reconcile | VERIFIED | 6 new order methods (lines 499-701) + startup_reconcile (lines 729-909) + _derive_lod_for_orphan + _compute_orphan_stop |
| `bot/state/migrations.py` | _migration_0004 adding entry_order_id, exit_order_id, avg_fill_price; CURRENT_VERSION=4 | VERIFIED | _migration_0004 (lines 203-217) idempotent with PRAGMA table_info guard; MIGRATIONS list has 4 entries; CURRENT_VERSION=4 |
| `bot/config/loader.py` | StrategyConfig with 10 execution tunables | VERIFIED | All 10 execution fields declared (lines 102-111); loaded from `data.get("execution", {})` (line 161) |
| `rules.json` | "execution" block with 10 tunables | VERIFIED | All 10 present: entry_limit_buffer_usd=0.05, entry_ttl_seconds=20, entry_max_retries=2, entry_poll_interval_seconds=5, exit_limit_buffer_usd=0.05, exit_ttl_seconds=15, exit_escalation_step_usd=0.10, exit_escalation_cadence_seconds=10, force_close_escalation_step_usd=0.20, force_close_escalation_cadence_seconds=15 |
| `tests/execution/test_engine.py` | EXEC-01 through EXEC-05 tests | VERIFIED | 5 passing tests (1 skipped manual-only): test_entry_placed_simulate_unit, test_no_market_orders, test_ttl_cancel_replace, test_duplicate_guard, test_fill_by_order_id |
| `tests/position/test_fsm.py` | POS-01/02/03 FSM tests | VERIFIED | 3 passing: test_partial_profit_trigger, test_breakeven_trigger, test_trail_never_loosens |
| `tests/position/test_manager.py` | POS-04/05 manager tests | VERIFIED | 34 passing: includes test_restart_reconciliation, test_force_close_half_day, plus 29 manager unit tests from 04-02 + 1 module-level restart from 04-04 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `ExecutionEngine.consume_intent` | `MoomooGateway.place_order` | `self._gw.place_order(code, qty, limit_price, trd_side)` | WIRED | engine.py lines 225, 307, 379 |
| `ExecutionEngine._manage_entry_order` | `MoomooGateway.get_order_fills` | `self._gw.get_order_fills()` | WIRED | engine.py lines 252, 397 |
| `ExecutionEngine.consume_intent` | `MoomooGateway.get_positions` | `self._gw.get_positions(refresh_cache=True)` | WIRED | engine.py line 131 (EXEC-04 guard) |
| `PositionManager.on_fill` | `StateStore.upsert_position` | `self._store.upsert_position(pos)` via `_persist_position` | WIRED | manager.py line 624 |
| `PositionManager.on_bar` | `PositionState.evaluate_close` | `pos.evaluate_close(bar.close, self._cfg, new_swing_low)` | WIRED | manager.py line 190 |
| `PositionManager.force_close_all` | `get_market_close_et` | `get_force_close_time_et(today)` → `get_market_close_et(today)` | WIRED | manager.py lines 62-90 |
| `MoomooGateway.startup_reconcile` | `StateStore.get_open_positions` | `store.get_open_positions()` | WIRED | gateway.py line 776 |
| `MoomooGateway.startup_reconcile` | `MoomooGateway.get_positions` | `self.get_positions(refresh_cache=True)` | WIRED | gateway.py line 761 |
| `MoomooGateway.place_order` | `OrderType.NORMAL` | `order_type=OrderType.NORMAL` (hardcoded in _place_blocking) | WIRED | gateway.py line 530 — never MARKET |
| `MoomooGateway.connect` | `assert_paper_account` | `assert_paper_account(self.cfg, self._trade_ctx)` | WIRED | gateway.py line 291 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `engine._manage_entry_order` | `fills` (fill poll) | `self._gw.get_order_fills()` → `deal_list_query(refresh_cache=True)` | Yes (broker query with mandatory cache bypass) | FLOWING |
| `engine.manage_exit` | `total_filled` | `self._gw.get_order_fills()` → fill match by order_id | Yes | FLOWING |
| `manager.on_bar` | `action, qty` | `pos.evaluate_close(bar.close, cfg, new_swing_low)` | Yes (pure FSM on real bar data) | FLOWING |
| `manager._increment_daily_filled_count` | `filled_count` | `INSERT ... ON CONFLICT DO UPDATE SET filled_count = filled_count + 1` | Yes (SQLite upsert) | FLOWING |
| `gateway.startup_reconcile` | `broker_map` | `get_positions(refresh_cache=True)` | Yes (fresh broker query) | FLOWING |
| `get_force_close_time_et` | `force_close_time` | `get_market_close_et(today)` (from scanner/calendar.py) | Yes (calendar-derived) | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite green | `python3 -m pytest tests/ -q` | `372 passed, 1 skipped in 4.14s` | PASS |
| No MARKET order type in execution path | `grep -c "OrderType.MARKET" bot/execution/engine.py bot/gateway/gateway.py` | `0` on both files | PASS |
| FSM phases complete | `python3 -c "from bot.position.state import PositionPhase; print([p.value for p in PositionPhase])"` | `['AWAITING_FILL', 'ACTIVE', 'PARTIAL_TAKEN', 'BREAKEVEN', 'TRAILING', 'CLOSED']` | PASS |
| No hardcoded 15:51 time literal | `grep -n "time(15\|time(12" bot/position/manager.py` | No results (only docstring comment with text "15:51") | PASS |
| No hardcoded R-threshold literals in state.py code | `grep -n "0\.75\|0\.3333\|1\.0" bot/position/state.py` (excluding comments/docs) | Results only in comments/docstrings (e.g., lines 177-179 are `— e.g. 0.75` doc text) | PASS |
| CURRENT_VERSION = 4 | `python3 -c "from bot.state.migrations import CURRENT_VERSION; assert CURRENT_VERSION==4"` | PASS (confirmed in migrations.py line 228) | PASS |

---

### Probe Execution

No probe scripts declared or found for this phase. The 04-VALIDATION.md validation map uses pytest as the verification mechanism.

---

### Requirements Coverage

| Requirement | Plan | Description | Status | Evidence |
|-------------|------|-------------|--------|----------|
| EXEC-01 | 04-03 | Entry placed as SIMULATE limit order; fill in StateStore + audit log | SATISFIED (auto) / UNCERTAIN (live) | test_entry_placed_simulate_unit green; live path is the human-verification item |
| EXEC-02 | 04-03 | No market orders ever | SATISFIED | test_no_market_orders green; grep confirms 0 MARKET refs; gateway.place_order hardcodes NORMAL |
| EXEC-03 | 04-03 | TTL cancel-replace + abandon at retry cap | SATISFIED | test_ttl_cancel_replace green (3 scenarios incl. config-swap) |
| EXEC-04 | 04-04 | Broker-verified duplicate guard | SATISFIED | test_duplicate_guard green (3 paths) |
| EXEC-05 | 04-03 | Fill reconciliation by order_id only | SATISFIED | test_fill_by_order_id green; test_exit_fill_matched_by_order_id_only green; source grep gate green |
| POS-01 | 04-01 | 1/3 partial at 0.75R bar close | SATISFIED | test_partial_profit_trigger green; FSM uses cfg.partial_profit_trigger_r (no literal) |
| POS-02 | 04-01 | Breakeven stop at 1.0R bar close | SATISFIED | test_breakeven_trigger green (incl. config-swap to 1.5R) |
| POS-03 | 04-01 | 5m swing-low trail, never loosened | SATISFIED | test_trail_never_loosens green (incl. D-11 restart simulation) |
| POS-04 | 04-04 | EOD force-close calendar-aware; force_close_stuck alert | SATISFIED | test_force_close_half_day green; force_close_stuck audit at manager.py lines 350, 378 |
| POS-05 | 04-04 | Restart reconstruction without re-entry | SATISFIED | test_restart_reconciliation green (4 sub-scenarios: D-09 close, D-09 qty, D-10 orphan, D-11 never-loosen) |
| CFG-01 | all | All tunables from rules.json; no hardcoded literals | SATISFIED | 10 execution tunables in rules.json; StrategyConfig loads all; config-swap tests prove behavioral compliance |
| SAFE-01 | 04-03 | Paper-only guard on every order path | SATISFIED | assert_paper_account wired at gateway.connect() (line 291); _parse_trd_env defaults to SIMULATE; TrdEnv.REAL requires explicit "REAL" string |

---

### Anti-Patterns Found

No debt markers (TBD, FIXME, XXX), placeholder comments, or empty-implementation stubs found in phase-4 files. All `return None` occurrences in engine.py and manager.py are functional guards (duplicate-blocked, TTL-abandoned, not-found lookups) not stubs — each is paired with a condition and a log/audit entry.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None found | — | — | — | — |

---

### Human Verification Required

#### 1. Live SIMULATE Entry Order Round-Trip (EXEC-01)

**Test:** Start OpenD on 127.0.0.1:11111, log in to the SIMULATE paper account. Call `ExecutionEngine.consume_intent(intent)` with a real liquid S&P 500 symbol during market hours.

**Expected:** The entry order appears in the OpenD order list as a LIMIT order with status SUBMITTED/FILLED; on fill, `FillEvent.is_entry=True` is emitted with the matching `order_id`; the StateStore positions table gains a new row with `phase='ACTIVE'`; `~/.futu_trade_audit.jsonl` contains entries `entry_order_placed` and `entry_fill_detected` with the matching `order_id`.

**Why human:** Requires a running OpenD GUI and a logged-in SIMULATE account during market hours. Cannot run in CI. The automated `test_entry_placed_simulate_unit` verifies the code path with a mock gateway; this live test additionally confirms the broker round-trip (fill model, API field names, `order_id` stability). Also confirms `audit_log` write at the filesystem level.

---

### Gaps Summary

No gaps found. All 12 acceptance criteria are satisfied by substantive, wired, data-flowing code and green tests. The single human-verification item is the live SIMULATE entry order round-trip, which was explicitly documented in 04-VALIDATION.md as a manual-only verification before phase planning began — it is not a gap, it is an intentional scope boundary.

The 1 skipped test (`test_entry_placed_simulate`) is correctly marked `@pytest.mark.skip` with a clear reason message pointing to 04-VALIDATION.md, and its automated counterpart (`test_entry_placed_simulate_unit`) is green.

---

*Verified: 2026-06-24*
*Verifier: Claude (gsd-verifier)*
