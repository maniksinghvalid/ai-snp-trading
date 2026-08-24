---
phase: quick-260824-avx
plan: 01
subsystem: scanner
tags: [scanner, gateway, unsubscribe, moomoo, watchlist, live-uat-fix]

requires:
  - phase: 06.2-code-review-remediation
    provides: run_intraday_rescan D-04 active-code protection baseline
provides:
  - Unconditional managed-code protection in run_intraday_rescan (a managed symbol whose gap collapses stays on the watchlist and keeps its K_5M feed)
  - Idempotent MoomooGateway.unsubscribe (benign "not been subscribed" reply no longer raises and aborts the rescan)
affects: [service, intraday-signal-and-risk-engine]

tech-stack:
  added: []
  patterns:
    - "Managed/active codes carried without a candidate dict are appended to the result list but never enter the persisted set (no fabricated gap_pct/rank)"
    - "Gateway idempotency discriminates on error-message substring, not ret code, to avoid masking genuine failures"

key-files:
  created: []
  modified:
    - bot/scanner/scanner.py
    - bot/gateway/gateway.py
    - tests/scanner/test_scanner.py
    - tests/gateway/test_gateway.py

key-decisions:
  - "carried_active codes are excluded from persist_watchlist entirely rather than persisted with fabricated gap_pct/rank, since the ON CONFLICT UPDATE would clobber the code's real premarket daily_scan row"
  - "_WATCHLIST_CAP truncation of `protected` was deleted (not just skipped) since managed codes must never be truncated away; cap math instead reserves slots for carried_active before filling with new candidates"
  - "Gateway benign-unsubscribe match is on message substring only (not ret==-1) per plan instruction, so a future OpenD ret-code change for the same message still gets caught"

requirements-completed: [SCAN-07, SIG-01]

duration: 12min
completed: 2026-08-24
---

# Quick Task 260824-avx: Fix intraday-rescan crash / protect managed codes Summary

**A managed symbol (open position or pending intent) that fails the intraday re-filter now stays on the watchlist with its live feed intact, and a benign OpenD "not subscribed" reply no longer aborts the whole rescan.**

## Performance

- **Duration:** 12 min
- **Tasks:** 2 completed
- **Files modified:** 4 (2 source, 2 test)

## Accomplishments
- Fix A: `run_intraday_rescan` step 5 now unconditionally protects `active_codes` — a code absent from `passing` (failed re-filter) is appended to the returned watchlist, is never unsubscribed, and gets no fabricated `daily_scan` row.
- Fix B: `MoomooGateway.unsubscribe` treats OpenD's benign `"... has not been subscribed. Cannot unsubscribe."` reply as a no-op warning instead of raising `GatewayError`, so `run_intraday_rescan` always reaches `return result` and the service's premarket-high seeding for rescan-added codes runs.
- Replaced the dead `active_code_evicted` warning path (now structurally unreachable for managed codes) with `active_code_carried`.
- Full test suite green: 964 passed, 1 skipped.

## Task Commits

Each task was committed atomically:

1. **Task 1: Unconditionally protect managed codes in run_intraday_rescan (Fix A)** - `7cf526c` (fix)
2. **Task 2: Make MoomooGateway.unsubscribe idempotent (Fix B) + full-suite gate** - `b1962ef` (fix)

_Note: both tasks were implemented directly (no separate RED/GREEN split) since the plan's `<action>` already specifies exact source + test edits; each commit includes its accompanying test file._

## Files Created/Modified
- `bot/scanner/scanner.py` - `run_intraday_rescan` step 5 rewritten: `carried_active` computed from `active_codes - passing_codes`, appended to `result` without a candidate dict or persisted row; cap math reserves slots for it; dead `_WATCHLIST_CAP` truncation of `protected` removed; `active_code_evicted` warning replaced with `active_code_carried`; docstrings updated; `_unsubscribe_evicted_codes` gets a `# ponytail:` note recording that eviction is now unreachable for managed codes
- `bot/gateway/gateway.py` - `MoomooGateway.unsubscribe`'s `_unsubscribe_blocking` now checks `"not been subscribed" in str(msg).lower()` before calling `_check_ret`, logging `unsubscribe_not_subscribed` and returning instead of raising
- `tests/scanner/test_scanner.py` - `test_rescan_unsubscribes_evicted_active_code` → `test_rescan_never_evicts_managed_active_code` (inverted assertions: code stays, unsubscribe not called, no fabricated row); `test_rescan_logs_active_code_eviction` → `test_rescan_logs_carried_active_code` (asserts `active_code_carried` fires, `active_code_evicted` never does)
- `tests/gateway/test_gateway.py` - Added `test_unsubscribe_not_subscribed_is_benign` and `test_unsubscribe_raises_on_other_error_with_same_ret` to `TestUnsubscribe`

## Decisions Made
- Carried codes are excluded from `persist_watchlist` entirely (no candidate dict exists for them) rather than persisted with placeholder values, per the plan's explicit warning that the upsert's `ON CONFLICT UPDATE` would clobber the code's real premarket `daily_scan` row.
- Deleted the now-unreachable `if len(protected) > _WATCHLIST_CAP: protected = protected[:_WATCHLIST_CAP]` truncation rather than leaving it as dead defensive code, since the plan states it can never fire once `max_concurrent_positions` bounds the managed set below 20.
- Left `_unsubscribe_evicted_codes` and its step-9 call site in place as a safety net (per plan instruction), documenting via `# ponytail:` comment that it is now a no-op for managed codes and does not restore quota-slot release for genuinely-dropped non-managed codes (explicitly out of scope here).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Both defects (Fix A, Fix B) are closed and covered by regression tests; full suite green (964 passed, 1 skipped).
- Live behavior fix is not yet re-verified against the specific US.DLR / US.STLD incidents described in the plan objective — that requires a live/paper session to confirm the fix holds in production, not part of this quick task's scope.
- `_unsubscribe_evicted_codes`'s quota-slot-release gap for non-managed evicted codes remains open (documented ponytail ceiling); revisit if the 20-slot cap is ever exceeded by non-managed churn.

---
*Phase: quick-260824-avx*
*Completed: 2026-08-24*

## Self-Check: PASSED

All modified files (bot/scanner/scanner.py, bot/gateway/gateway.py, tests/scanner/test_scanner.py, tests/gateway/test_gateway.py) and both task commits (7cf526c, b1962ef) verified present.
