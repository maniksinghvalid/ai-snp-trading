---
phase: 06-backtester
plan: 12
subsystem: backtester
tags: [pandas-market-calendars, test-fixtures, time-bomb, gap-closure]

# Dependency graph
requires:
  - phase: 06-backtester
    provides: SimulatedBarFeed._enforce_window (rolling 60-calendar-day 5m window guard), backtester test suite from 06-01..06-11
provides:
  - recent_session_days(n) helper in tests/backtester/fixtures.py — n most recent consecutive NYSE trading days, runtime-derived
  - Self-dating backtester test suite: every fixture date derives from a recent NYSE trading day, never a hardcoded literal
affects: [06-backtester verification (WR-06), future backtester test additions]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "recent_session_days(n) — module-level date anchors computed once at test-module import time via pandas_market_calendars.get_calendar('NYSE').valid_days(...), never a hardcoded YYYY-MM-DD literal"
    - "Shared multi-anchor invariant: when a helper (e.g. _make_5m_frame) builds a fixed-length prior-day warm-up window relative to one anchor, every OTHER anchor consumed by the SAME helper must be DERIVED FROM (not independently computed alongside) that anchor, or the warm-up window can silently overlap a 'today' anchor and duplicate-inject rows into its replay stream"

key-files:
  created: []
  modified:
    - tests/backtester/fixtures.py
    - tests/backtester/test_feed.py
    - tests/backtester/test_harness.py
    - tests/backtester/test_run.py

key-decisions:
  - "recent_session_days(n) lives in fixtures.py (not feed.py or a new module) since fixtures.py is the existing NO-network/NO-backtester-import pure-function fixture module all four test files already share"
  - "_DAY (test_harness.py's single-day anchor) is derived from the SAME recent_session_days(2) call as _MD_DAY1/_MD_DAY2, with _DAY = _MD_DAY1 — NOT from an independent recent_session_days(1) call. The original hardcoded file had _DAY == _MD_DAY1 == '2026-06-01' by literal coincidence; deriving them independently at runtime (recent_session_days(1) resolves to the LATEST day in the window, not the same day as recent_session_days(2)'s FIRST entry) broke that invariant and caused _make_5m_frame's shared prior-14-business-day warm-up anchor (built relative to _DAY) to silently overlap _MD_DAY1's own signal-day bars in the multi-day replay test, duplicate-injecting quiet-volume rows"
  - "test_feed.py and test_run.py each get their own independent recent_session_days() call/module-level anchor (_DAY1/_DAY2 in test_feed.py, _DAY in test_run.py) since they don't share any date-relative helper across single/multi-day fixtures the way test_harness.py's _make_5m_frame does — no cross-file anchor sharing needed"
  - "Left the already-runtime-derived _CB_DAY block in test_harness.py (added in Plan 06-11) and the already-runtime-derived NaN-padding test in test_feed.py (added in Plan 06-10) untouched — both already followed the correct dynamic-date pattern before this plan started, per the plan's own read_first guidance"

patterns-established:
  - "Any future backtester test fixture MUST derive its date(s) from tests.backtester.fixtures.recent_session_days(n), never a hardcoded YYYY-MM-DD literal"

requirements-completed: [BT-04, BT-02]

# Metrics
duration: 25min
completed: 2026-07-07
---

# Phase 06 Plan 12: Backtester Test Suite Self-Dating (Fixture Time-Bomb Closure) Summary

**Closed the non-blocking WR-06 time bomb by adding `recent_session_days(n)` to `tests/backtester/fixtures.py` and retrofitting every hardcoded `2026-06-01`/`2026-06-02` fixture-date literal across `fixtures.py`, `test_feed.py`, `test_harness.py`, and `test_run.py` to derive from it — the rolling 60-calendar-day `_enforce_window` guard can no longer turn the suite red on a future calendar date (~2026-08-01), with zero production-code changes and zero regression-assertion weakening.**

## Performance

- **Duration:** 25 min
- **Started:** 2026-07-07 (session start)
- **Completed:** 2026-07-07T14:35:00Z (approx)
- **Tasks:** 1
- **Files modified:** 4

## Accomplishments

- `recent_session_days(n=1)` added to `tests/backtester/fixtures.py`: returns the `n` most recent consecutive NYSE trading days (via `pandas_market_calendars.get_calendar("NYSE").valid_days(...)`), ending a few days before "now" so every anchor stays comfortably inside the ~60-calendar-day yfinance 5m window regardless of when the suite runs.
- Every hardcoded `2026-06-01`/`2026-06-02` code literal across `tests/backtester/*.py` (fixtures.py, test_feed.py, test_harness.py, test_run.py) is now derived from `recent_session_days` — verified via `grep -rn "2026-0" tests/backtester/*.py`, which returns only 3 prose-comment lines (explicitly allowed by the plan's acceptance criteria).
- Discovered and fixed a real bug surfaced by the retrofit itself (not present in the original file, where the coincidence of `_DAY == _MD_DAY1 == "2026-06-01"` masked it): `test_harness.py`'s shared `_make_5m_frame` helper builds a 14-business-day quiet warm-up window relative to the module-level `_DAY` constant, and that SAME helper is reused by the multi-day test's `_TESTA_BARS`/`_TESTB_BARS` fixtures (anchored to `_MD_DAY1`/`_MD_DAY2`). Deriving `_DAY` and `_MD_DAY1` from independent `recent_session_days()` calls caused them to diverge at runtime (`recent_session_days(1)` resolves to the LATEST day in the window, not `recent_session_days(2)`'s FIRST entry), which made `_make_5m_frame`'s warm-up window silently overlap `_MD_DAY1`'s own signal-day bars — duplicate-injecting quiet-volume rows into the multi-day replay and causing a spurious TESTB entry fill. Fixed by deriving both `_DAY` and `_MD_DAY1`/`_MD_DAY2` from a single `recent_session_days(2)` call with `_DAY = _MD_DAY1`, restoring the original file's implicit invariant explicitly.
- Preserved every existing invariant the plan required: `_MD_DAY1`/`_MD_DAY2` remain two consecutive NYSE trading days; `_make_daily_frame`'s 200-business-day window and `_make_5m_frame`'s 14-business-day RVOL-TOD baseline window remain strictly prior to the signal day; `_CB_DAY` (already runtime-derived in Plan 06-11) and the union-index NaN-padding test's own runtime-derived day (already added in Plan 06-10) were left untouched.
- Simulated-clock sanity check: monkeypatching `datetime.now()` to `2026-08-15` inside `tests.backtester.fixtures` and calling `recent_session_days(1)`/`recent_session_days(2)` correctly re-anchors to `['2026-08-13']` / `['2026-08-12', '2026-08-13']` — proving the suite will not time-bomb on the future calendar date the verification flagged.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add recent_session_days helper and retrofit hardcoded fixture dates across the backtester suite** - `175c578` (test)

_Note: this is a `type: execute` plan (not `type: tdd`), single task, committed once with all four file changes together since they form one cohesive retrofit — no RED/GREEN/REFACTOR cycle applies._

## Files Created/Modified

- `tests/backtester/fixtures.py` — added `recent_session_days(n=1)`; `make_ahead_only_5m_dataset()` and `make_trade_log()` now build their bar/closed_at timestamps from `recent_session_days(1)`/`recent_session_days(2)` instead of hardcoded `2026-06-01`/`2026-06-02` literals.
- `tests/backtester/test_feed.py` — added `from tests.backtester.fixtures import recent_session_days`; module-level `_DAY1, _DAY2 = recent_session_days(2)`; every hardcoded start/end/replay/next_bar/premarket_highs/synthetic_today_price literal and every frame-builder helper (`_make_multi_bar_frame`, `_make_premarket_5m_frame`, `_make_two_day_frame`, `_make_two_day_premarket_frame`) now derives from `_DAY1`/`_DAY2`. Left the existing `datetime.now() - timedelta(...)` near/far window-guard tests and the already-runtime-derived NaN-padding test (`day` computed via `mcal` directly) unchanged.
- `tests/backtester/test_harness.py` — added the same import; `_MD_DAY1, _MD_DAY2 = recent_session_days(2)` computed FIRST, then `_DAY = _MD_DAY1` (single-day anchor now explicitly tied to the multi-day pair's first day); every `_TODAY_BARS`/`_OPEN_AT_EOD_BARS`/`_TESTA_BARS`/`_TESTB_BARS` bar literal, `_make_daily_frame`'s `bdate_range` end, `_make_premarket_frame`'s index, `_make_5m_frame`'s prior-day anchor, the window-clock literals in `test_replay_clock_drives_entry_window_gate`, and `_make_premarket_frame_for`'s index now derive from `_DAY`/`_MD_DAY1`/`_MD_DAY2`. The already-runtime-derived `_CB_DAY`/Gate-7 block (Plan 06-11) was left untouched.
- `tests/backtester/test_run.py` — added the import and a module-level `_DAY = recent_session_days(1)[0]`; `_base_args`'s default `--start`/`--end` and the two ad-hoc CLI-arg-list literals in `test_bad_start_date_exits_nonzero_before_fetch`/`test_empty_symbols_exits_nonzero` now use `_DAY` (these tests fail before the date value is ever consulted, so any valid trading day works). The intentionally-ancient `"2000-01-01"`/`"2000-01-02"` out-of-window literals were left unchanged (correct, deliberately old dates for that specific guard test).

## Decisions Made

- **`_DAY = _MD_DAY1` (not two independent `recent_session_days()` calls).** See Accomplishments above — this restores an invariant the original hardcoded file had only by coincidence, and is now an explicit, commented requirement in the file to prevent future regressions if either constant is edited independently.
- **`recent_session_days` placed in `fixtures.py`**, not a new module — it's a pure function with no network/backtester-module dependency, matching that file's existing "NO import of any `backtester.*` module" constraint (only `pandas_market_calendars`/`datetime` added, both already used elsewhere in the suite).
- **`test_feed.py` and `test_run.py` each compute their own independent anchor(s)** rather than importing test_harness.py's `_DAY`/`_MD_DAY1`/`_MD_DAY2` — they don't share any date-relative fixture-building helper across files the way `test_harness.py`'s single-day and multi-day tests share `_make_5m_frame` internally, so there was no cross-file invariant to preserve.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed `_DAY`/`_MD_DAY1` anchor divergence causing a spurious duplicate-bar injection in the multi-day replay test**
- **Found during:** Task 1, verification step (`python3 -m pytest tests/backtester/ -q`) — `test_multiday_replay_proves_cr01_cr04_cr05_cr06` failed with an unexpected `US.TESTB` fill that should never have occurred (CR-04 assertion).
- **Issue:** My initial retrofit derived `_DAY = recent_session_days(1)[0]` and `_MD_DAY1, _MD_DAY2 = recent_session_days(2)` as two SEPARATE calls. `recent_session_days(1)` resolves to the single LATEST trading day in the lookback window, which is NOT the same date as `recent_session_days(2)`'s first (earlier) entry — so `_DAY` and `_MD_DAY1` diverged at runtime. `test_harness.py`'s shared `_make_5m_frame(today_bars)` helper builds its 14-business-day quiet RVOL-TOD warm-up window ending the day before `_DAY`; since `_make_5m_frame` is ALSO reused (with different `today_bars`) to build `_TESTA_BARS`'/`_TESTB_BARS`' frames for the multi-day test, the warm-up window (anchored to the now-different `_DAY`) silently overlapped `_MD_DAY1`'s own date, injecting duplicate quiet-volume rows at `_MD_DAY1`'s timestamps alongside the real signal-day bars — corrupting `US.TESTB`'s next-bar lookup and producing a fill that should have been abandoned (CR-04).
- **Fix:** Computed `_MD_DAY1, _MD_DAY2 = recent_session_days(2)` once, at the top of the file, and set `_DAY = _MD_DAY1` — restoring the invariant the original hardcoded file had (both literally `"2026-06-01"`) explicitly and permanently, with an inline comment explaining why the two constants must never be derived independently.
- **Files modified:** `tests/backtester/test_harness.py` (same commit as the rest of the retrofit — this was caught and fixed before any commit was made, per the plan's fix-attempt-limit guidance).
- **Verification:** `python3 -m pytest tests/backtester/ -q` → 43 passed (was 42 immediately post-retrofit-before-fix, with 1 failure); `python3 -m pytest tests/ -q` → 632 passed, 1 skipped (full suite, zero regressions).
- **Committed in:** `175c578` (single task commit — the bug was caught during the same task's verification step, before any commit existed, so no separate fix commit was needed).

---

**Total deviations:** 1 auto-fixed (1 bug, caught during the task's own verification step before committing).
**Impact on plan:** The fix is essential for correctness — without it, the retrofit itself would have introduced a NEW regression (a bar-duplication bug in the multi-day CR-04 test) while trying to fix the pre-existing time-bomb. No scope creep: the fix only touches the same anchor-derivation logic the plan already required editing, and required zero additional files or production-code changes.

## Issues Encountered

None beyond the deviation documented above (caught and fixed within the same task, before commit).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The non-blocking WR-06 time-bomb (06-VERIFICATION/06-REVIEW) is closed: `tests/backtester/` derives every fixture date from `recent_session_days`, verified to correctly re-anchor when the wall clock is simulated past 2026-08-01.
- `python3 -m pytest tests/backtester/ -q` → 43 passed (unchanged test count from before this plan — no assertion weakened, deleted, or newly added).
- `python3 -m pytest tests/ -q` → 632 passed, 1 skipped (full suite, zero regressions from this plan or the fix within it).
- `git diff --name-only` (this commit) shows only `tests/backtester/fixtures.py`, `tests/backtester/test_feed.py`, `tests/backtester/test_harness.py`, `tests/backtester/test_run.py` — zero production-code files touched, matching the plan's stated scope.
- This closes the last item noted in MEMORY.md's remaining v1.0 backtester gap-closure list (fixtures time-bomb). Remaining v1.0 work per MEMORY.md: live trade-loop T4-7 and scanner premarket confirmation (outside Phase 06's scope).

## Self-Check: PASSED

- `tests/backtester/fixtures.py` exists: FOUND
- `tests/backtester/test_feed.py` exists: FOUND
- `tests/backtester/test_harness.py` exists: FOUND
- `tests/backtester/test_run.py` exists: FOUND
- Commit `175c578` (test): FOUND in `git log --oneline --all`
- `grep -n "def recent_session_days" tests/backtester/fixtures.py`: FOUND (line 32)
- `grep -rn "2026-0" tests/backtester/*.py` returns only 3 comment lines (no code literal): PASSED
- `python3 -m pytest tests/backtester/ -q` → 43 passed (test count unchanged from pre-plan baseline): PASSED
- `python3 -m pytest tests/ -q` → 632 passed, 1 skipped (zero regressions): PASSED
- `git diff --name-only HEAD~1 HEAD` shows only the 4 planned `tests/backtester/*.py` files, no production code: PASSED
- Simulated future-clock check (`datetime.now()` mocked to 2026-08-15): `recent_session_days` correctly re-anchors to `['2026-08-12', '2026-08-13']` / `['2026-08-13']`, proving the suite will not time-bomb on the flagged future date: PASSED

---
*Phase: 06-backtester*
*Completed: 2026-07-07*
