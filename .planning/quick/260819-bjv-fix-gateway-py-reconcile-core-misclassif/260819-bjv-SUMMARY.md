---
phase: quick
plan: 260819-bjv
subsystem: gateway
tags: [reconcile, options, safety, gateway, logging]

requires: []
provides:
  - "_reconcile_core option-code skip (SAFE-OG-01 / Phase 8 invariant hardening)"
  - "reconcile_external_position_ignored dedup (once per code per process)"
affects: [gateway, options-bot-safety]

tech-stack:
  added: []
  patterns:
    - "Reuse module-level regex predicates (_OPTION_CODE_RE) instead of threading new fields through row dicts"
    - "Session-scope instance set for per-code log dedup"

key-files:
  created: []
  modified:
    - bot/gateway/gateway.py
    - tests/gateway/test_gateway.py

key-decisions:
  - "Option-code skip placed before the SAFE-OG-01 ownership/long guard, not folded into it — guarantees option legs can never be adopted even if that guard's checks later loosen"
  - "Dedup set (self._external_ignored_logged) is unbounded/never cleared — bounded in practice by distinct broker codes on one paper account; classification is static within a process run"

requirements-completed: [SAFE-OG-01, SAFE-03]

duration: 8min
completed: 2026-08-19
---

# Quick Task 260819-bjv Summary

**Equity gateway's `_reconcile_core` no longer misclassifies the options bot's own iron-condor legs as external positions; the legit manual-equity warning now logs once per code per process instead of every ~75s cycle.**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-08-19T15:15:00Z (approx)
- **Completed:** 2026-08-19T15:25:05Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Option codes (matched via the existing `_OPTION_CODE_RE`) are now skipped at the top of `_reconcile_core`'s orphan-adoption loop, before the SAFE-OG-01 ownership/long guard — they never produce a warning and are never adopted, for both `reconcile_once` and `startup_reconcile` (single shared function).
- Added `self._external_ignored_logged` session-scope set so the legitimate `reconcile_external_position_ignored` warning (manual operator equity holdings) fires once per code per process, not every ~75s reconcile cycle.
- Three new regression tests lock in the behavior; full suite green at 1130 passed / 1 skipped (was 1127/1).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add failing regression tests for option-code reconcile behavior** - `dcffa33` (test)
2. **Task 2: Skip option codes in _reconcile_core and dedupe external warnings** - `6ca0d36` (fix)

_Note: metadata commit (SUMMARY.md/STATE.md) is made separately by the orchestrator, not by this executor._

## Files Created/Modified
- `bot/gateway/gateway.py` - `MoomooGateway.__init__` gains `self._external_ignored_logged: set`; `_reconcile_core`'s orphan-adoption loop gains an `_OPTION_CODE_RE.match(code)` skip (logs `reconcile_skip_option_code` at debug) immediately after the existing `exiting_codes` check, and the `reconcile_external_position_ignored` warning is now gated on `code not in self._external_ignored_logged`.
- `tests/gateway/test_gateway.py` - New `class TestOptionCodesSkippedByReconcile` with 3 tests: option short leg skipped/not-adopted, option long wing skipped/not-adopted, manual equity still warned once then deduped on a second `reconcile_once` cycle.

## Decisions Made
- Followed the plan's pre-diagnosed root cause exactly: no re-investigation needed. The option-code guard sits structurally before, not inside, the SAFE-OG-01 ownership/long guard block — a defense-in-depth choice so a future loosening of `_is_long`/`_bot_owned` can never let an options leg slip through.
- Kept the dedup set unbounded per the plan's explicit `ponytail:` guidance — a code's classification (manual vs bot-owned vs option) never changes within one process run, so no eviction logic is warranted.

## Deviations from Plan

None - plan executed exactly as written. Task 1 produced the expected RED state (3 failures: two on the `reconcile_external_position_ignored` assertion for option codes, one on the second-cycle dedup assertion for manual equity), confirmed before writing the fix in Task 2.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- No live bot process (PIDs 62448, 62160) or warm-cache-pool job (PID 48166) was touched — code + tests only, per plan constraint.
- `git diff --name-only` for this plan's commits is exactly `bot/gateway/gateway.py` and `tests/gateway/test_gateway.py` — no `bot/options/service.py` changes.
- Both live equity-bot and options-bot processes will pick up the fix on their next restart; no action required before then since this only reduces log noise and closes a latent adoption-guard gap that had never actually fired (options legs currently only get logged, never adopted, due to `not_long`/`not_bot_owned` already rejecting them — this closes the gap defensively per T-bjv-01).

## Self-Check: PASSED

- FOUND: bot/gateway/gateway.py (modified, diff confirmed above)
- FOUND: tests/gateway/test_gateway.py (modified, diff confirmed above)
- FOUND: dcffa33 (git log confirms commit exists)
- FOUND: 6ca0d36 (git log confirms commit exists)

---
*Phase: quick*
*Completed: 2026-08-19*
