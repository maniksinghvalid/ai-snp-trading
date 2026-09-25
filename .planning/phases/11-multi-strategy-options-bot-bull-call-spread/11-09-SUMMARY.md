---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 09
subsystem: options-bot
tags: [asyncio, options, moomoo, telegram-alerts, gap-closure]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    provides: bull_call_spread multi-strategy options bot (plans 01-08), CR-02 incomplete-unwind
      handling, WR-06/WR-07 near-expiry quote-miss escalation
provides:
  - fill_leg cancels its own working order on any exception or task cancellation after
    place_order returns an order_id (CR-03)
  - three hand-off alerts (reconcile restart, UNWIND INCOMPLETE, close incomplete) now name
    the leg codes and tell the operator to cancel working orders first
  - session-scoped near-expiry miss streak (IN-08) so a day-before-expiry streak cannot make
    the first expiry-day miss escalate
  - one-time "Options expiry warning" (WR-10) fired on the first expiry-day manage cycle that
    cannot manage a position (no-quote or snapshot-outage), independent of later dropped cycles
  - process-level snapshot-outage counter and alert (WR-11), correcting the false T-11-46
    "OpenDWatchdog covers this" rationale
affects: [options-bot-operations, options-bot-alerting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "per-attempt try/except BaseException around fill_leg's post-placement work, with a
      GeneratorExit carve-out and a shielded cancel before re-raising the original exception"
    - "one-time in-memory warned-set (self._expiry_warned) as the idiom for a fire-once alert
      that must not become a new status transition"
    - "process-level consecutive-cycle counter (self._snapshot_outage_cycles), reset by any
      clean cycle, reusing the existing _QUOTE_MISS_ESCALATE_CYCLES threshold"

key-files:
  created: []
  modified:
    - bot/options/execution.py
    - bot/options/service.py
    - tests/options/test_execution.py
    - tests/options/test_service.py

key-decisions:
  - "CR-03: a per-attempt try wraps everything after place_order; except GeneratorExit
    re-raises untouched (a closing coroutine cannot await); except BaseException runs a
    shielded cancel_order then a bare raise of the ORIGINAL exception — never a return value,
    because a partial fill before the exception is unknown exposure the caller must escalate"
  - "No order ids in the three hand-off alerts — they are not cheaply available (the unwind's
    close_legs runs with no callbacks) and adding them would change the execution contract;
    the leg_cancel_on_error_failed audit event carries the only order id that can still be
    working"
  - "WR-10 uses a one-time warning on the first expiry-day cycle that cannot manage the
    position, not a widened final-cycle window — widening still fails if two fires are
    dropped and hands the position off earlier; the precondition is that at least one manage
    cycle reaches the position before the cutoff (T-11-57, accepted)"
  - "IN-08 stores (today, count) tuples in self._quote_miss_streak rather than clearing on a
    date change elsewhere, so the reset rule lives in the one place the streak is read"
  - "WR-11 reuses _QUOTE_MISS_ESCALATE_CYCLES (no new knob) as a process-level counter, alerts
    once via `==`, and is re-armed by any clean snapshot cycle; outages still never touch the
    per-position streak"

requirements-completed: [MSO-05, MSO-07, MSO-08]

# Metrics
duration: ~15min
completed: 2026-09-25
---

# Phase 11 Plan 09: CR-03 / WR-10 / WR-11 / IN-08 gap closure Summary

**fill_leg now cancels its own resting order on any post-placement exception or cancellation before re-raising; expiry-day positions get a one-time warning independent of dropped manage cycles; a persistent snapshot outage now alerts the operator at the process level.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-09-25T09:49:00-07:00 (first RED run)
- **Completed:** 2026-09-25T10:02:17-07:00 (Task 3 commit)
- **Tasks:** 3
- **Files modified:** 2 source files (`bot/options/execution.py`, `bot/options/service.py`), 2 test files (`tests/options/test_execution.py`, `tests/options/test_service.py`)

## Red evidence

- **Task 1** (`-k "cancels_working_order or cancel_failure_on_error or unwind_poll_error or close_poll_error or warns_about_working_orders"` against fc33ba1 sources): `10 failed, 146 deselected in 0.93s`
- **Task 2** (`-k "streak_resets or expiry_day_unquotable or expiry_day_warning"` against the Task-1 service.py): `3 failed, 117 deselected in 0.38s`
- **Task 3** (`-k "outage_alerts_once or expiry_day_snapshot_outage or snapshot_outage_never"` against the Task-2 service.py): `3 failed, 119 deselected in 0.39s`

## Accomplishments

- CR-03 closed: `fill_leg` can no longer leave an order it placed working on the broker when an await after placement raises or the task is cancelled — the only exception is a cancel that itself fails, which is logged and audited with the order_id. The reviewer's `repro_unwind_resting.py` scenario (the entry unwind's own aggressive SELL, O5, left resting after a rate-limit RuntimeError) is now cancelled.
- WR-10 closed: an expiry-day position that any manage cycle could not manage (no quote or snapshot outage) now alerts the operator once, before the cutoff, regardless of how many later cycles APScheduler drops (`max_instances=1` overruns). The automated `assignment_guard` close stays armed and the day before expiry stays silent.
- WR-11 closed: a persistent snapshot outage (OpenD connected, but quote rights failing or the whole batch rejected) now alerts once after 3 consecutive manage cycles and re-arms after any clean cycle — correcting 11-08's T-11-46 rationale, which wrongly assumed `OpenDWatchdog` (which only polls `get_global_state`) would catch this.
- IN-08 closed: the near-expiry miss streak is now scoped to the ET session date, so two misses late on the day before expiry can no longer make the first 09:35 miss on expiry day escalate immediately.

## Task Commits

Each task was committed atomically, and every commit is green (`tests/options tests/backtester/options` run after each):

1. **Task 1: CR-03 fill_leg cancels its working order; hand-off alerts warn about working orders** - `ef38a74` (fix) — 464 passed after
2. **Task 2: IN-08 session-scoped streak; WR-10 one-time expiry-day warning** - `6a3d067` (fix) — 467 passed after
3. **Task 3: WR-11 process-level snapshot-outage alert; expiry-day outage warning; phase gate** - `bcd1c0a` (fix) — 469 passed after

Phase gate (full suite): `1345 passed, 1 skipped, 0 failed` (1330 baseline + 15 new test cases: Task 1 = 10, Task 2 = 3, Task 3 = 2; the one modified assertion is not a new case).

Each commit's `git show --stat --format= HEAD` was confirmed to list exactly that task's source + test files (verified via `git diff --stat fc33ba1 -- bot/ tests/ scripts/`, which lists only `bot/options/execution.py`, `bot/options/service.py`, `tests/options/test_execution.py`, `tests/options/test_service.py` across all three commits combined).

## Files Created/Modified

- `bot/options/execution.py` — `fill_leg`'s per-attempt loop wraps everything after `place_order` in `try:` / `except GeneratorExit: raise` / `except BaseException:` (shielded `cancel_order` then bare `raise`); rewritten "Guarantee (CR-03)" docstring paragraph; module docstring's `fill_leg` bullet extended. `_poll`, `open_position`, `close_legs` byte-unchanged (confirmed by the hunk-range awk check against fc33ba1).
- `bot/options/service.py` — `_WORKING_ORDERS_HINT` constant; three alert texts extended with leg codes + the hint; `self._quote_miss_streak` values became `(date, int)` tuples; new `self._expiry_warned` set and `self._snapshot_outage_cycles` counter; new `_warn_expiry_unmanaged` method; `_manage_once`'s snapshot loop grew the WR-11 counter/alert and calls `_warn_expiry_unmanaged` on an expiry-day outage; `_manage_position`'s retry branch calls `_warn_expiry_unmanaged` on an expiry-day miss.
- `tests/options/test_execution.py` — 6 new CR-03 cases: `test_fill_leg_cancels_working_order_when_an_await_raises` (3 parametrized cases), `test_fill_leg_cancels_working_order_on_task_cancellation`, `test_fill_leg_cancel_failure_on_error_is_logged_audited_and_original_raised`, `test_unwind_poll_error_cancels_the_unwind_order`.
- `tests/options/test_service.py` — 9 new cases across the three tasks (CR-03: 4, IN-08/WR-10: 3, WR-11: 2) plus the module-level `_BAD_BULL_QUOTES` constant, plus one deliberately changed assertion (see below).

## Deliberate Contract Change

`test_manage_snapshot_outage_never_escalates_near_expiry` (WR-06 section): its 4 outage cycles now cross the WR-11 process-level threshold, so the process alerts once even though the position itself is never escalated. Replaced the single line `alerter.send.assert_not_awaited()` with a comment plus two assertions confirming exactly one `<b>Options snapshot outage</b>` alert. The position-level contract stays pinned by the surrounding unchanged lines (B1 stays OPEN, no near-expiry audit event, the per-position outage log, no close). This is the only removed test line across both test files, confirmed by `git diff fc33ba1 -- tests/options/test_service.py | grep '^-[^-]'` printing exactly `-    alerter.send.assert_not_awaited()`.

## Decisions Made

- **CR-03 per-attempt try, BaseException + shield + re-raise, GeneratorExit carve-out.** `BaseException` (not `Exception`) is required because `CancelledError` has not been an `Exception` since Python 3.8, and `asyncio.run`/APScheduler cancel running job tasks at shutdown — that path must cancel the resting order too. `asyncio.shield` ensures a second task cancellation during the broker cancel cannot abort it. `GeneratorExit` propagates untouched first, since a coroutine being closed cannot await.
- **(a) CancelledError: shield the cancel, then re-raise** — documented as a known limit at kill-switch shutdown: `_shutdown` closes the gateway before `scheduler.shutdown` cancels jobs, so that cancel fails and is logged/audited; reordering `_shutdown` is the deferred upgrade path (T-11-56, accepted).
- **(b) An exception is never converted into a return value.** A partly filled order must never look like "nothing filled" (`None`) or a known quantity (a tuple); the exception propagates so `open_position`'s unwind returns `False` and the manage close ends `NEEDS_ATTENTION`. Re-polling after the cancel to return an exact partial quantity was rejected — the poll is the call that just failed, and a guessed quantity would let the unwind treat an unknown as known.
- **(c) The docstring's "Guarantee" is now true, with two named limits:** a cancel that itself fails, and a `place_order` that raises (no order_id to cancel).
- **No order ids in the alerts** — not cheaply available (the unwind's `close_legs` runs with no callbacks, and adding them would change the execution contract); the `leg_cancel_on_error_failed` audit event is the only place an order id that can still be working is recorded.
- **WR-10's one-time expiry warning over widening the final-cycle window** — widening (`now + 2*interval >= cutoff`) still fails if two fires are dropped and hands the position off earlier; counting expiry-day misses with threshold 1 was also rejected because it would cancel the automated close WR-06 protects. Precondition: at least one manage cycle must process the position on expiry day before the cutoff (T-11-57, accepted as an operator decision for a late restart).
- **IN-08's `(today, count)` tuple** — chosen over clearing every streak on the first cycle of a new date, because the reset rule stays in the one place the streak is read and also holds when `_manage_position` is called directly (as several existing tests do).
- **WR-11's once-per-episode counter** reuses `_QUOTE_MISS_ESCALATE_CYCLES` (no new knob); alerts at `==` so it fires once, and any clean cycle resets/re-arms it. An empty book (no snapshot taken) leaves the counter untouched — no evidence either way.

## Corrected Threat Dispositions

- **T-11-38 (CORRECTED):** 11-08 marked the entry-unwind exception path mitigated (`unwound = False` → NEEDS_ATTENTION alert), but the unwind's own aggressive order could still be resting while the alert said "close manually," risking a naked short. Now closed by T-11-51 (the order is cancelled before the exception propagates) and T-11-52 (the alert says to cancel working orders first).
- **T-11-46 (CORRECTED):** 11-08's rationale — "OpenDWatchdog alerts on OpenD disconnect" — is FALSE for a persistent snapshot outage: `bot/service/watchdog.py` polls only `get_global_state` (connection/login) and cannot see a quote-rights error or a whole-batch snapshot rejection while OpenD stays connected. Now mitigated by T-11-54 (process-level alert) and T-11-53 (expiry-day warning on an outage).

## Issues Encountered

None — all three tasks proceeded RED → GREEN → commit exactly as planned, with every commit green.

## Deviations from Plan

None — plan executed exactly as written. The one "deliberate contract change" (see above) was explicitly specified by the plan itself, not an unplanned deviation.

## User Setup Required

None — no external service configuration required. This is an offline-only gap closure (no OpenD connection, no bot start, no `--live-1lot` probe run).

## Next Phase Readiness

Per the plan's stated intent, the operator scoped CR-03 + WR-10 + WR-11 + IN-08 as the final gap closure for phase 11, to stop the review/fix loop. Residuals explicitly recorded and accepted rather than closed:
- T-11-56: a shielded cancel that itself fails at kill-switch shutdown (gateway closed before job cancellation) — upgrade path is reordering `_shutdown`.
- T-11-57: a process restart inside the last manage interval before the expiry-day cutoff gets no manage cycle and no warning — upgrade path is a readiness-gate check.
- T-11-58/T-11-59/WR-09 and the rest of the deferred backlog (WR-02/03/04/08, IN-01..11 minus IN-08, EX-03) remain out of scope, as itemized in the plan's Deferred section.

No new path into `NEEDS_ATTENTION` was added (`set_position_status(pid, "NEEDS_ATTENTION")` count stays 5), and the D-25 invariants (LIMIT-only, one `place_order` call site, longs-first open, shorts-first close, SAFE-OG-01 scope, own DB/kill-file/report-dir, SIMULATE only) all hold.

## Self-Check: PASSED

All 4 modified files and this SUMMARY.md confirmed present on disk; all 3 task commit hashes
(`ef38a74`, `6a3d067`, `bcd1c0a`) confirmed present in `git log --oneline --all`.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Plan: 09*
*Completed: 2026-09-25*
