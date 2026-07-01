---
phase: "02-premarket-scanner"
plan: "03"
subsystem: "scanner + gateway"
tags: [subscribe, k5m, sig-01, scan-07, d-04, d-05, run-in-executor, intraday-rescan, active-candidate-protection, idempotent-upsert, tdd]
dependency_graph:
  requires:
    - "02-02-SUMMARY.md (run_daily_scan, _persist_watchlist, SEAM(02-03) subscribe hook)"
    - "01-01-SUMMARY.md (MoomooGateway, GatewayError, _check_ret, run_in_executor pattern)"
  provides:
    - "bot/gateway/gateway.py — MoomooGateway.subscribe() async method (K_5M, run_in_executor)"
    - "bot/scanner/scanner.py — _compute_candidates(), run_intraday_rescan(), subscribe wiring in run_daily_scan"
    - "8 new tests (3 gateway subscribe + 2 subscribe wiring + 3 rescan)"
  affects:
    - "Phase 3 (reads persisted watchlist codes for 5m signal evaluation)"
    - "Phase 5 (calls run_daily_scan + run_intraday_rescan on APScheduler schedule)"
tech_stack:
  added: []
  patterns:
    - "Deferred 'from moomoo import SubType, Session' inside method body (avoids top-level SDK import failure in tests)"
    - "asyncio.run() bridge — sync run_daily_scan/run_intraday_rescan call async gateway.subscribe()"
    - "D-04 active_codes protection: active codes front-loaded before gap-ranked fill in protected top-20"
    - "Shared _compute_candidates() kernel — no D1/D2/D3 filter duplication between daily and intraday paths"
    - "_subscribe_new_codes() helper — subscribe only codes absent from active_codes"
key_files:
  created: []
  modified:
    - "bot/gateway/gateway.py (subscribe() method added in Async Wrappers section)"
    - "bot/scanner/scanner.py (_compute_candidates, _subscribe_new_codes, run_intraday_rescan; subscribe seam wired in run_daily_scan)"
    - "tests/gateway/test_gateway.py (3 subscribe tests in TestSubscribe class)"
    - "tests/scanner/test_scanner.py (5 new tests: subscribe wiring + rescan)"
decisions:
  - "asyncio.run() bridge used inside sync run_daily_scan/run_intraday_rescan — keeps both entrypoints sync as specified in 02-PATTERNS.md; no conversion to async"
  - "_compute_candidates() extracted as shared private kernel — daily scan and intraday re-scan share one code path for universe→bars→filter, eliminating D1/D2/D3 duplication"
  - "D-04 implementation: active codes iterated first from sorted-by-gap passing list, then non-active codes fill remaining slots up to _WATCHLIST_CAP=20; guarantees active codes in top-20 even if gap ranks below 20"
  - "gateway=None is a valid call path — both run_daily_scan and run_intraday_rescan skip subscribe when gateway is None (supports unit tests without a broker)"
metrics:
  duration: "~18 minutes"
  completed: "2026-06-23"
  tasks_completed: 3
  tasks_total: 3
  files_changed: 4
---

# Phase 2 Plan 03: Subscribe Wiring + Intraday Re-scan Summary

**One-liner:** MoomooGateway.subscribe() wraps K_5M quote_ctx.subscribe in run_in_executor with GatewayError fail-closed; run_daily_scan subscribes exactly the persisted top-20 codes via asyncio.run() bridge; run_intraday_rescan re-ranks by gap_pct, guarantees active_codes in top-20 (D-04), upserts idempotently (D-05), and subscribes only newly-added codes — 252 tests green, Phase 2 complete.

---

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | MoomooGateway.subscribe() — K_5M run_in_executor wrapper (SIG-01) | cedae23 | bot/gateway/gateway.py, tests/gateway/test_gateway.py |
| 2 | Wire gateway.subscribe into run_daily_scan — top-20 only (SIG-01) | 4f3a88e | bot/scanner/scanner.py, tests/scanner/test_scanner.py |
| 3 | run_intraday_rescan — idempotent merge, protect active candidates (SCAN-07, D-04/D-05) | 01ffec8 | bot/scanner/scanner.py, tests/scanner/test_scanner.py |

---

## Verification Results

- `pytest tests/gateway/test_gateway.py -k subscribe -q` → 3 passed
- `pytest tests/scanner/test_scanner.py -k "subscribe_top20 or subscribe_skipped" -q` → 2 passed
- `pytest tests/scanner/test_scanner.py -k "rescan" -q` → 3 passed
- `pytest tests/scanner/test_scanner.py tests/gateway/test_gateway.py -q` → 45 passed
- `pytest tests/ -q` → 252 passed (Phase 2 complete)
- `grep -c 'async def subscribe' bot/gateway/gateway.py` → 1
- `grep -c 'SubType.K_5M' bot/gateway/gateway.py` → 2 (default value + import usage)
- `grep -c '.subscribe(' bot/scanner/scanner.py` → 3 (run_daily_scan + _subscribe_new_codes x2)

### Success Criteria Check

| Criterion | Status |
|-----------|--------|
| MoomooGateway.subscribe() wraps K_5M subscribe in run_in_executor, fails closed on non-RET_OK | PASS |
| run_daily_scan subscribes exactly the capped top-20 (SIG-01), never the universe | PASS |
| run_intraday_rescan re-ranks by gap%, protects active candidates (D-04), upserts idempotently (D-05) | PASS |
| Re-scan subscribes only codes absent from active_codes | PASS |
| Full suite 252 passed | PASS |

---

## Deviations from Plan

### Factored _compute_candidates() + _subscribe_new_codes() Helpers

**[Rule 2 - Critical Functionality] Extracted shared compute kernel and subscribe helper**

The plan specified factoring shared "compute candidates" logic into `_compute_candidates(cfg, scan_date)` to prevent D1/D2/D3 filter duplication. This was done as directed. Additionally, a `_subscribe_new_codes(gateway, codes, active_codes)` helper was extracted for the D-04 subscribe-only-new pattern — this avoided duplicating the subscribe guard logic between run_daily_scan and run_intraday_rescan.

- **Found during:** Task 3 implementation
- **Action:** Extracted two private helpers (_compute_candidates, _subscribe_new_codes) rather than inlining the logic in run_intraday_rescan
- **Files modified:** bot/scanner/scanner.py

### Removed redundant `import asyncio as _asyncio` alias

When run_daily_scan was originally written in Task 2, `asyncio` was imported as `_asyncio` locally inside the function body. When refactoring for Task 3, `asyncio` was promoted to a top-level module import (it was already in the docstring imports list) — this cleaned up the local alias and enabled consistent use across both entrypoints.

---

## Known Stubs

None. All callable entrypoints are fully implemented. Scheduling of run_daily_scan and run_intraday_rescan via APScheduler is Phase 5 — the callables themselves are complete.

---

## Threat Flags

None. All T-02-10 through T-02-13 mitigations from the plan's threat register are implemented:
- T-02-10: only top-20 passed to gateway.subscribe (SIG-01); test asserts len==20
- T-02-11: D-04 active_codes union guarantee proven by test_active_candidate_protected
- T-02-12: D-05 idempotent upsert proven by test_rescan_idempotent
- T-02-13: gateway.subscribe fails closed via _check_ret → GatewayError; test_subscribe_raises_on_non_ret_ok proves it

---

## Self-Check: PASSED

- `bot/gateway/gateway.py` FOUND
- `bot/scanner/scanner.py` FOUND
- Commit cedae23 FOUND (subscribe method + gateway tests)
- Commit 4f3a88e FOUND (subscribe wiring + scanner tests)
- Commit 01ffec8 FOUND (run_intraday_rescan + rescan tests)
- `pytest tests/ -q` → 252 passed
