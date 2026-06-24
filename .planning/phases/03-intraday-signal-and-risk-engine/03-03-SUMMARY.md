---
phase: 03-intraday-signal-and-risk-engine
plan: "03"
subsystem: risk-engine
tags: [risk, risk-engine, gateway, RISK-01, RISK-02, RISK-03, D-05, D-07, D-12, T-03-07, T-03-08, T-03-09, T-03-10]
dependency_graph:
  requires:
    - bot/gateway/gateway.py (MoomooGateway — extended here)
    - bot/risk/events.py (OrderIntent — 03-01)
    - bot/signal/events.py (SignalEvent — 03-01)
    - bot/signal/signal_engine.py (note_intent_emitted() — 03-02)
    - bot/state/store.py (StateStore, pending_intents — 03-01 migration 0003)
    - bot/strategy/trend_join_long.py (compute_initial_stop — Phase 1)
    - bot/config/loader.py (StrategyConfig, max_risk_per_trade_pct, max_position_size_pct — Phase 1)
    - bot/safety/logger.py (get_logger — Phase 1)
    - bot/safety/et_helpers.py (now_et — Phase 1)
  provides:
    - bot/gateway/gateway.py#MoomooGateway.get_equity() (live total_assets with $100k fallback)
    - bot/risk/risk_engine.py (RiskEngine — sizing math, LOD-1% stop, OrderIntent emission)
  affects:
    - tests/gateway/test_gateway.py (6 new get_equity tests, all green)
    - tests/risk/test_risk_engine.py (10 tests fully fleshed — RISK-01/02/03, D-07/D-12)
tech_stack:
  added: []
  patterns:
    - TDD: RED (failing import/attr) → GREEN (all tests pass) for both tasks
    - run_in_executor async wrapper for accinfo_query (mirrors get_positions pattern)
    - Dual implausible-bound guard: _IMPLAUSIBLE_LOW ($1k) + _IMPLAUSIBLE_HIGH ($10M)
    - math.floor (not int) for round-DOWN share qty (D-07, T-03-08)
    - take-smaller: min(risk_qty, notional_cap_qty) (RISK-02, D-07)
    - parameterized INSERT INTO pending_intents + conn.commit() (T-03-09, no f-string SQL)
    - structlog capture_logs for log event assertions in tests
    - AsyncMock for get_equity() in tests (no live broker)
key_files:
  created:
    - bot/risk/risk_engine.py
  modified:
    - bot/gateway/gateway.py (added get_equity() + _EQUITY_FALLBACK / _IMPLAUSIBLE_LOW / _IMPLAUSIBLE_HIGH constants)
    - tests/gateway/test_gateway.py (replaced 6 Wave 0 skip stubs with real assertions)
    - tests/risk/test_risk_engine.py (replaced 9 Wave 0 skip stubs with 10 full test assertions)
decisions:
  - "get_equity() does NOT call _check_ret — degrades gracefully on failure (D-05), never raises"
  - "Both implausible bounds (LOW $1k and HIGH $10M) applied as named module constants (T-03-07)"
  - "math.floor used for both risk_qty and notional_cap_qty — not int() — for explicit floor semantics (D-07, T-03-08)"
  - "RiskEngine accepts optional signal_engine for note_intent_emitted() wiring (D-09 burst guard)"
  - "No place_order call in risk_engine.py — Phase 3 emits intents only (T-03-10 paper-only invariant)"
  - "structlog.testing.capture_logs used for log assertion in tests"
metrics:
  duration_seconds: 310
  completed_date: "2026-06-24"
  tasks_completed: 2
  files_created: 1
  files_modified: 3
---

# Phase 03 Plan 03: RiskEngine — Live-Equity Sizing, LOD-1% Stop, OrderIntent Persistence Summary

**One-liner:** Adds MoomooGateway.get_equity() (accinfo_query with refresh_cache=True, dual implausible bounds, $100k fallback) and RiskEngine (1%-risk + 10%-notional sizing with math.floor, LOD-1% stop via compute_initial_stop, OrderIntent emitted with structlog audit + pending_intents persistence via parameterized SQL), closing RISK-01/02/03 and D-12 with no orders placed.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 RED | Failing tests for get_equity() | acf91e9 | tests/gateway/test_gateway.py (6 new tests) |
| 1 GREEN | MoomooGateway.get_equity() implementation | 4c9929c | bot/gateway/gateway.py |
| 2 RED | Failing tests for RiskEngine | 3e696ce | tests/risk/test_risk_engine.py (10 full tests) |
| 2 GREEN | RiskEngine implementation | a4beeb4 | bot/risk/risk_engine.py |

## Verification Results

- `pytest tests/gateway/test_gateway.py::TestGetEquity -v` → **6 passed** (happy path + 4 fallback paths + refresh_cache assertion)
- `pytest tests/risk/test_risk_engine.py::test_intent_fields_correct -x` → **1 passed** (RISK-03 stop_price, uuid intent_id, equity_used, notional)
- `pytest tests/risk/test_risk_engine.py::test_notional_cap -x` → **1 passed** (10% cap binds, math.floor, RISK-02/D-07)
- `pytest tests/risk/test_risk_engine.py -x -q` → **10 passed** (all RISK-01/02/03, D-07/D-12 paths green)
- `pytest tests/ -x -q` → **317 passed** (no regression in Phase 1/2/03-01/03-02 suites)
- Source check: no `place_order`/`PlaceOrder` in risk_engine.py (T-03-10 paper-only invariant) → **PASS**

## Deviations from Plan

None — plan executed exactly as written.

## Key Decisions Made

1. **get_equity() degrades, not raises (D-05):** The method catches ALL exceptions and returns `_EQUITY_FALLBACK`. It does NOT call `_check_ret`, which would raise `GatewayError`. This is the correct degrade-gracefully pattern for a sizing input.

2. **Both implausible bounds as named module constants (T-03-07):** `_IMPLAUSIBLE_LOW = 1_000.0` and `_IMPLAUSIBLE_HIGH = 10_000_000.0` are defined at module level (not inline literals in the comparison). The plan's threat register documents the upper bound as a security mitigation against corrupt reads inflating position size.

3. **math.floor over int() (D-07, T-03-08):** `math.floor()` is used for both `risk_qty` and `notional_cap_qty` instead of `int()`. While both truncate toward zero for positive values, `math.floor` is the semantically correct primitive ("round DOWN") and is explicit about intent — consistent with the research anti-pattern note.

4. **signal_engine optional in RiskEngine constructor (D-09):** The `signal_engine` parameter is optional (`None` default). When provided, `note_intent_emitted()` is called after each emitted intent so SignalEngine's `_pending_count` tally stays accurate for the D-09 burst guard. When `None` (e.g., in unit tests), the call is skipped — no AttributeError.

5. **structlog.testing.capture_logs for test assertions (D-12):** Uses `from structlog.testing import capture_logs` context manager to capture `order_intent_emitted` and `intent_skipped_under_budget` events in tests. This is structlog's canonical testing API and doesn't depend on log output format.

6. **No order placement in risk_engine.py (T-03-10):** Source inspection confirmed: `place_order`, `PlaceOrder`, `trd_ctx`, `trade_ctx` — none appear in `bot/risk/risk_engine.py`. Phase 3 ends at OrderIntent emission; Phase 4 owns execution.

## Artifact Verification

All `must_haves.artifacts` verified:

| Artifact | Check | Status |
|----------|-------|--------|
| bot/gateway/gateway.py | `async def get_equity` present | PASS |
| bot/risk/risk_engine.py | `class RiskEngine` present | PASS |
| bot/risk/risk_engine.py | 194 lines (min 90) | PASS |
| tests/risk/test_risk_engine.py | `test_equity_fallback` present | PASS |
| tests/risk/test_risk_engine.py | 10 tests all green | PASS |

## Threat Model Coverage

| Threat | Mitigation | Test |
|--------|-----------|------|
| T-03-07: corrupt equity inflating size | _IMPLAUSIBLE_LOW ($1k) + _IMPLAUSIBLE_HIGH ($10M) named constant bounds | test_equity_fallback_on_implausible_low + test_equity_fallback_on_implausible_high |
| T-03-08: over-sizing via un-rounded qty | math.floor on both risk_qty and notional_cap_qty; take smaller; <1 → no intent | test_notional_cap + test_under_budget_no_intent |
| T-03-09: non-atomic pending_intents write | Single parameterized INSERT + conn.commit() per emission (WAL-mode SQLite) | test_intent_persisted_to_statestore |
| T-03-10: real-money order path introduced | No place_order/trd_ctx call in risk_engine.py; source grep verified | source assertion + full test suite |

## Known Stubs

None — all stub tests from Wave 0 have been fully implemented and are green.

## Self-Check: PASSED

Files exist:
- bot/gateway/gateway.py — get_equity() method present ✓
- bot/risk/risk_engine.py — class RiskEngine, 194 lines ✓
- tests/gateway/test_gateway.py — TestGetEquity with 6 tests ✓
- tests/risk/test_risk_engine.py — 10 tests all passing ✓
- .planning/phases/03-intraday-signal-and-risk-engine/03-03-SUMMARY.md ✓

Commits exist:
- acf91e9 (Task 1 RED) ✓
- 4c9929c (Task 1 GREEN) ✓
- 3e696ce (Task 2 RED) ✓
- a4beeb4 (Task 2 GREEN) ✓
- d16d878 (metadata — SUMMARY, STATE, ROADMAP, REQUIREMENTS) ✓

Full suite: 317 tests pass, 0 failures, 0 regressions.
