---
phase: 4
slug: order-and-position-management
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-24
validated: 2026-07-07
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing in repo; 320 tests green from Phase 3) |
| **Config file** | none detected — tests run via `pytest tests/` |
| **Quick run command** | `pytest tests/position/ tests/execution/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~30 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/position/ tests/execution/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Req ID | Plan | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|--------|------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| EXEC-01 | 04-03 | Entry placed via SIMULATE; FillEvent recorded in StateStore + JSONL audit | — | SIMULATE-only order path (triple-assert env guard) | unit (mock gateway) + manual (paper) | `pytest tests/execution/test_engine.py::test_entry_placed_simulate_unit -x` | ✅ | ✅ green |
| EXEC-02 | 04-03 | No market order ever placed; force-close uses marketable limit | — | Never OrderType.MARKET; limit cap bounds fill | unit (AST + behavioral) | `pytest tests/execution/test_engine.py::test_no_market_orders -x` | ✅ | ✅ green |
| EXEC-03 | 04-03 | TTL cancel-replace fires; abandon after max retries; config-swap changes behavior | — | Bounded retry; no runaway re-submit | unit (synthetic fill mock) | `pytest tests/execution/test_engine.py::test_ttl_cancel_replace -x` | ✅ | ✅ green |
| EXEC-04 | 04-04 | Broker `get_positions()` blocks duplicate entry (not just in-memory guard) | — | Broker-truth duplicate guard | unit (mocked get_positions) | `pytest tests/execution/test_engine.py::test_duplicate_guard -x` | ✅ | ✅ green |
| EXEC-05 | 04-03 | Fill matched by `order_id`; partial exit ≠ stop-out | — | order_id reconciliation, never qty-matching | unit (synthetic deal rows) | `pytest tests/execution/test_engine.py::test_fill_by_order_id -x` | ✅ | ✅ green |
| POS-01 | 04-01 | ⅓ partial sell at 0.75R bar close; FSM ACTIVE→PARTIAL_TAKEN | — | Judged on close (no wick repaint) | unit (synthetic BarEvent) | `pytest tests/position/test_fsm.py::test_partial_profit_trigger -x` | ✅ | ✅ green |
| POS-02 | 04-01 | Stop moves to entry at 1.0R bar close; FSM PARTIAL_TAKEN→BREAKEVEN | — | Judged on close | unit (synthetic BarEvent) | `pytest tests/position/test_fsm.py::test_breakeven_trigger -x` | ✅ | ✅ green |
| POS-03 | 04-01 | Trail stop ratchets up on swing-low; never moves down | — | Stop never loosens (`max(persisted, new)`) | unit (synthetic bars + restart) | `pytest tests/position/test_fsm.py::test_trail_never_loosens -x` | ✅ | ✅ green |
| POS-04 | 04-04 | Force-close at half-day early close vs 15:51 on normal day | — | Calendar-aware flatten; never carry overnight | unit (mock calendar) | `pytest tests/position/test_manager.py::test_force_close_half_day -x` | ✅ | ✅ green |
| POS-05 | 04-04 | Restart reconstructs FSM from StateStore; broker-truth wins; no re-entry | — | Broker-truth reconciliation; no double-entry | unit (in-mem SQLite + mock broker) | `pytest tests/position/test_manager.py::test_restart_reconciliation -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

> **EXEC-01 note:** Automated coverage is `test_entry_placed_simulate_unit` (mock-gateway, green in CI). The live-paper `test_entry_placed_simulate` is `@pytest.mark.skip` (requires OpenD) and remains a Manual-Only verification below.

---

## Wave 0 Requirements

- [x] `bot/execution/__init__.py` — new package
- [x] `bot/position/__init__.py` — new package
- [x] `tests/execution/__init__.py`
- [x] `tests/execution/test_engine.py` — EXEC-01 through EXEC-05 (all green)
- [x] `tests/position/__init__.py`
- [x] `tests/position/test_fsm.py` — POS-01/POS-02/POS-03 (pure FSM, no broker; green)
- [x] `tests/position/test_manager.py` — POS-04/POS-05 (manager + calendar + reconciliation; green)
- [x] `tests/conftest.py` — extended with synthetic FillEvent/BarEvent + mock-gateway fixtures

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live limit-order fill on SIMULATE paper account | EXEC-01, EXEC-03 | Requires OpenD running + logged-in paper account; cannot run in CI | Start OpenD on 127.0.0.1:11111, run an entry against a liquid S&P symbol during market hours, confirm FillEvent in StateStore + `~/.futu_trade_audit.jsonl` |
| Cancel-replace verified against paper account | EXEC-03 | Needs live unfilled order to age past TTL | Place a far-from-market limit, confirm TTL cancel + re-submit at fresh price via order_list_query |

*Other behaviors have automated verification via synthetic Fill/Bar events and mocked gateway.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (new bot/execution, bot/position, tests/* packages)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-07-07

---

## Validation Audit 2026-07-07

| Metric | Count |
|--------|-------|
| Requirements | 10 |
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

All 10 requirements (EXEC-01..05, POS-01..05) COVERED by green automated tests.
Verified live: `pytest tests/position/ tests/execution/ -q` → **80 passed, 1 skipped**
(the 1 skip is EXEC-01's manual live-paper test; its automated unit substitute
`test_entry_placed_simulate_unit` is green). No test generation needed — the
planning-time draft was stale; this audit reconciles it with the implemented,
passing suite.
