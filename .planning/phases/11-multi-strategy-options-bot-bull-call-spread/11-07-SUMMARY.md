---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 07
subsystem: options-bot
tags: [options, risk, manage-loop, quote-validation, defined-risk, gap-closure]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    provides: multi-strategy options service (plans 01-06), 11-VERIFICATION.md (CR-01 BLOCKER gap), 11-REVIEW.md (WR-01/WR-05/EX-01/Q-01 findings)
provides:
  - "_quote_ok(q) gate in bot/options/service.py — every leg must have a two-sided numeric quote before mark_spread/decision/close_legs"
  - "Q-01 near-expiry escalation: an unquotable leg inside the assignment-guard window sets NEEDS_ATTENTION + alert instead of a silent skip"
  - "close_legs exception guard in _manage_position — a close_legs exception always ends NEEDS_ATTENTION, never CLOSING"
  - "WR-01: startup reconcile + _manage_position both fail closed on a structure-kind mismatch between a position and its named strategy (options_strategy_structure_mismatch)"
  - "WR-05: open_max_loss_total and per-strategy open_count sum every ACTIVE row (OPENING/OPEN/CLOSING/NEEDS_ATTENTION), not just OPEN/OPENING"
  - "EX-01: bot/options/execution.py close_legs never sells a long wing once any short has failed to close for its full quantity (close_longs_skipped_short_open)"
affects: [12-live-uat-and-milestone-close]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_quote_ok as the single gate every manage-path consumer (mark_spread, manage_decision(_debit), close_legs) routes through"
    - "try/except around an executor call that falls through to an EXISTING alert/audit branch, rather than duplicating it"

key-files:
  created: []
  modified:
    - bot/options/service.py
    - bot/options/execution.py
    - tests/options/test_service.py
    - tests/options/test_execution.py

key-decisions:
  - "CR-01/Q-01/WR-01/WR-05 fixed inside bot/options/service.py only; EX-01 fixed inside bot/options/execution.py close_legs only (plus its module-docstring line) — every other frozen file (strategy.py, config.py, store.py, universe.py, gateway/, both rules files) is byte-unchanged, confirmed via git diff against 530667d"
  - "WR-01's kind check compares structure KIND (bull_call_spread vs any credit structure), not exact type string, per the plan's explicit interpretation — iron_condor and put_credit_spread share manage_decision and identical config fields"
  - "EX-02 (open_position's entry-unwind discarding close_legs' result) stays out of scope per the plan's Deferred backlog — EX-01 already removes the naked-short outcome for that path even though the ABORTED reporting stays pre-existing"

requirements-completed: [MSO-05, MSO-07, MSO-08]

# Metrics
duration: ~20min
completed: 2026-09-24
---

# Phase 11 Plan 07: Gap closure (CR-01, WR-01, WR-05, EX-01, Q-01) Summary

**Quote-validity gate (CR-01/Q-01) closes the phase's one verification BLOCKER; structure-kind fail-closed (WR-01), ACTIVE-row BP/concurrent counting (WR-05), and a short-before-long close ordering guarantee (EX-01) close the four approved review follow-ups — full suite 1299 passed / 1 skipped / 0 failed.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-09-24T09:30:00-07:00 (approx, first Read call)
- **Completed:** 2026-09-24T09:51:00-07:00
- **Tasks:** 3
- **Files modified:** 4 (bot/options/service.py, bot/options/execution.py, tests/options/test_service.py, tests/options/test_execution.py)

## Accomplishments

- Closed the phase's one verification BLOCKER (CR-01): `_manage_position` now refuses to mark, decide on, or close a spread against any leg without a two-sided numeric quote — no more false profit-target exits, false daily-loss-breaker trips, or synthetic-$0 naked-short buy-backs.
- Q-01: inside the assignment-guard window an unquotable leg escalates once to NEEDS_ATTENTION with an alert instead of being silently retried forever into expiry.
- A `close_legs` exception (e.g. a literal `'N/A'` price raising `ValueError`) now always ends NEEDS_ATTENTION with an alert, never leaves the row stuck in CLOSING.
- WR-01 (D-29 completion): a position whose structure KIND no longer matches its named strategy's configured kind is fail-closed at startup reconcile (new `options_strategy_structure_mismatch` audit event) and defended-in-depth in `_manage_position`.
- WR-05 (D-22 completion): global BP headroom and per-strategy concurrent-position counts now include every `_ACTIVE_STATUSES` row, so a stuck NEEDS_ATTENTION/CLOSING row still consumes its exposure instead of silently vanishing from the risk math.
- EX-01: `LegExecutor.close_legs` now refuses to sell any long wing once a short has failed to close for its full quantity (no quote, unfilled, or partial) — the spread stays defined-risk instead of becoming a naked short.

## Task Commits

Each task was committed atomically:

1. **Task 1: CR-01 + Q-01 quote-validity gate, near-expiry escalation, close_legs exception guard** - `4a065fd` (fix)
2. **Task 2: WR-01 structure-kind mismatch + WR-05 ACTIVE-row BP/concurrent counts** - `6c3b375` (fix)
3. **Task 3: EX-01 close_legs never sells a long once a short failed** - `38b2538` (fix)

## Red Evidence

Per-task RED runs (test file authored first, run against the pre-edit source, before implementing the fix):

- **Red evidence (Task 1):** `python3 -m pytest -q tests/options/test_service.py -k "quote_ok or invalid_quote or invalid_long_quote or close_exception or unquotable"` against 530667d's `service.py` → `26 failed, 54 deselected in 0.86s`. Green after the edit: `26 passed, 54 deselected in 0.45s`.
- **Red evidence (Task 2):** `python3 -m pytest -q tests/options/test_service.py -k "structure_mismatch or needs_attention_and_closing"` against Task-1's `service.py` → `5 failed, 80 deselected in 0.48s`. Green after the edit: `5 passed, 80 deselected in 0.34s`.
- **Red evidence (Task 3):** `python3 -m pytest -q tests/options/test_execution.py -k "short_failure_sells_no_long or long_failure_still_attempts_other_longs"` against 530667d's `execution.py` → `3 failed, 1 passed, 26 deselected in 0.86s` (the 1 passed is the preservation test, expected green before and after by design). Green after the edit: `4 passed, 26 deselected in 0.55s`.

## Full Suite

`python3 -m pytest -q` → **1299 passed, 1 skipped in 67.41s** (0 failed). Matches the plan's phase gate exactly: 1265 baseline (530667d) − 1 replaced test (`test_close_legs_false_when_a_leg_does_not_close`) + 35 new test cases (26 + 5 + 4) = 1299.

## Files Created/Modified

- `bot/options/service.py` - `_quote_ok` pure helper (CR-01); quote gate + Q-01 near-expiry escalation + close_legs exception guard in `_manage_position`; widened D-29 reconcile branch (WR-01) covering both unknown-strategy and structure-kind-mismatch cases; structure-kind defense-in-depth check in `_manage_position` before the quote gate; `_scan_and_open`'s `open_max_loss_total`/`open_count` now sum every `_ACTIVE_STATUSES` row (WR-05); `_OPEN_STATUSES` constant deleted (no remaining users)
- `bot/options/execution.py` - `close_legs` tracks `open_shorts` and refuses to sell any long once a short has failed to close for its full quantity, logging `close_longs_skipped_short_open` (EX-01); docstring + module-docstring line updated
- `tests/options/test_service.py` - 31 new test cases across CR-01/Q-01 (26) and WR-01/WR-05 (5) sections
- `tests/options/test_execution.py` - `test_close_legs_false_when_a_leg_does_not_close` replaced by `test_close_legs_short_failure_sells_no_long` (3 cases) + `test_close_legs_long_failure_still_attempts_other_longs` (preservation)

## Decisions Made

- WR-01's mismatch check compares structure KIND (`bull_call_spread` vs any credit structure), not exact `structure_type` string equality — matches the plan's explicit interpretation since `iron_condor` and `put_credit_spread` share `manage_decision` and identical config fields. Marked with `ponytail:` comments at both call sites (reconcile + `_manage_position`) naming the upgrade path (strict equality) if that assumption ever changes.
- The `_manage_position` structure-kind check runs BEFORE the CR-01 quote gate, so a mismatched row is never escalated to NEEDS_ATTENTION using the wrong strategy's `assignment_guard_dte`.
- EX-01's `open_shorts` list is appended in both existing short-failure spots (no-quote continue, and result-None-or-partial-qty), and checked at the top of the loop body before every long leg is even quoted — so no long SELL order is ever placed once any short is known to be open.

## Deviations from Plan

None - plan executed exactly as written. All acceptance-criteria greps, diff-hunk-boundary checks, and file-freeze invariants (`strategy.py`, `config.py`, `store.py`, `universe.py`, `bot/gateway/`, `rules_options.json`, `rules.json`, `scripts/` byte-unchanged vs 530667d) were verified directly and passed without needing any Rule 1-4 deviation.

## Issues Encountered

None. Every RED run matched the plan's predicted failure count exactly (26 failed / 5 failed / 3 failed+1 passed), and every acceptance-criteria grep matched on the first attempt.

## User Setup Required

None - no external service configuration required. This plan is offline-only (source + pytest); no OpenD connection, no bot start, no `data/*.db` access.

## Next Phase Readiness

- Verification gap CR-01 (the phase's one BLOCKER) is closed; the phase's ROADMAP success criteria are now fully green pending the one Human Verification item already on record in 11-VERIFICATION.md (live paper run with both books registered), which is unaffected by this plan's changes.
- Backlog items WR-02, WR-03, WR-04, IN-01..IN-06, and the new EX-02 (recorded during the EX-01 call-site audit — `open_position`'s entry-unwind discards `close_legs`' result) remain explicitly out of scope and unaddressed; they are candidates for a future gap-closure or hardening plan.
- No blockers for milestone close from this plan's scope.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED

- FOUND: bot/options/service.py
- FOUND: bot/options/execution.py
- FOUND: tests/options/test_service.py
- FOUND: tests/options/test_execution.py
- FOUND: 4a065fd (Task 1 commit)
- FOUND: 6c3b375 (Task 2 commit)
- FOUND: 38b2538 (Task 3 commit)
