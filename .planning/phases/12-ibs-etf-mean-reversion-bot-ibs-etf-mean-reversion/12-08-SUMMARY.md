---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 08
subsystem: trading-bot
tags: [ibs, entries, hard-cancel, apscheduler, asyncio, sqlite]

requires:
  - phase: 12-06
    provides: IbsBot service (reconcile, _decide, _work, _run_exits, _decision_task)
  - phase: 12-04
    provides: decide_entries, size_position
  - phase: 12-05
    provides: IbsStore (insert_position, mark_opened, get_orders, set_order_status)
provides:
  - IbsBot._run_entries (entry batch strictly after exits)
  - IbsBot._sweep_orders / _job_hard_cancel (close - 1 min cancel of every WORKING order)
affects: [12-09 (arm_today schedules _job_hard_cancel, EOD, shutdown, run/main)]

tech-stack:
  added: []
  patterns:
    - "Fail-closed fresh broker read before entries (260926-kvt analog)"
    - "OPENING row inserted before BUY under partial unique index; IntegrityError = skip"

key-files:
  created: [tests/ibs/test_service_entries.py]
  modified: [bot/ibs/service.py, tests/ibs/test_service.py]

key-decisions:
  - "free slots = max_concurrent_positions - len(active rows read AFTER exits), so failed/partial exits and NEEDS_ATTENTION rows keep their slot"
  - "Unfilled entry -> ABORTED/entry_unfilled, silent (log only); no carry-over"
  - "Hard-cancel sweeps WORKING orders of any session, so a leftover from a failed day is also cleaned"

patterns-established:
  - "Entry exclusion set = active | exited-this-session | external | WORKING-order codes"

requirements-completed: [IBS-04, IBS-05, IBS-07]

duration: 25min
completed: 2026-10-04
---

# Phase 12 Plan 08: IBS entry batch and hard-cancel sweep Summary

**Entry batch after exits with external-holding / same-session / duplicate guards, plus a close-1-min sweep that cancels the in-flight decision and every WORKING IBS order.**

## Performance

- **Tasks:** 2/2 (TDD red then green each)
- **Files:** bot/ibs/service.py (+~150 lines), tests/ibs/test_service_entries.py (new, 23 tests), tests/ibs/test_service.py (2 assertions adjusted)
- **Tests:** full suite 1635 passed, 1 skipped (baseline 1612 + 23 new), 0 regressions

## Accomplishments

- `_decide` now calls `_run_entries(today, deadline, quotes, exited)` after `_run_exits`, unless the kill switch fired or entries were disabled (watchdog) in the meantime (logs `ibs_entry_skipped`).
- `_run_entries`: fresh `_broker_shares()` read (unreadable -> alert "IBS entries skipped", zero entries); excludes active, exited-this-session (ruling 3), external universe holdings (logged `ibs_external_holdings`) and WORKING-order codes; slots from post-exit active rows; sizing from `round(last + buffer, 2)`; qty < 1, no time left (`worst_case_order_s`), IntegrityError and halt each logged as `ibs_entry_skipped` with a reason; OPENING row before the BUY; filled/partial -> OPEN with "IBS entry" alert (+ "partial n/m"), unfilled -> ABORTED, exception -> NEEDS_ATTENTION + audit `ibs_entry_unknown` + alert, loop continues.
- `_sweep_orders(reason)` cancels every WORKING row (audit `ibs_order_cancel`); failure -> CANCEL_FAILED + audit `ibs_order_cancel_failed` + one alert without exception text. `_job_hard_cancel` skips non-trading days, cancels a still-running decision task (audit `ibs_decision_cancelled`), then sweeps; swallows errors (`ibs_hard_cancel_error`), re-raises CancelledError.

## Task Commits

1. Task 1 RED: f8190e9 `test(12-08): failing entry-batch tests`
2. Task 1 GREEN: 87828d8 `feat(12-08): IBS entry batch with external-holding and same-session guards`
3. Task 2 RED: 8e5dc60 `test(12-08): failing hard-cancel sweep tests`
4. Task 2 GREEN: 684e49b `feat(12-08): IBS hard-cancel sweep`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Two Plan 06 assertions encoded the pre-entry call pattern**
- **Found during:** Task 1 GREEN
- **Issue:** `test_decide_writes_meta_first_and_is_idempotent` expected exactly one `get_positions` call and `test_reconcile_precedes_snapshot` expected exactly `["positions", "snapshot"]`; the entry batch's mandatory post-exit broker read adds a second `get_positions`.
- **Fix:** Updated the expectations (`seen == [date, date]`, `order[:2] == [...]`); the meaning of both tests (meta written before first broker call; reconcile before snapshot) is unchanged.
- **Files modified:** tests/ibs/test_service.py
- **Commit:** 87828d8

Otherwise the plan was executed as written. Acceptance note: the entry-guard AST check and `OPENING`/`CANCEL_FAILED` greps hold.

## Known Stubs

None.

## Threat Flags

None; all new surface (BUY path, cancel path) is covered by T-12-05b/10b/03e/03f/06b/03g in the plan.

## Next Phase Readiness

Plan 09 can schedule `_job_hard_cancel` at close - 1 min (misfire grace per config) and call `_sweep_orders("shutdown")` from shutdown; no structural changes needed in `IbsBot`.

## Self-Check: PASSED
