---
phase: 06-backtester
plan: 09
subsystem: backtester
tags: [backtester, multi-day-replay, force-close, watchlist, premarket-high, python]

requires:
  - phase: 06-07
    provides: point-in-time feed correctness (session-bounded next_bar, coverage guard, PIT premarket/today-price)
  - phase: 06-08
    provides: SimulatedExecution force-close fill mechanic (_force_close flag, last-observed-bar-close exit)
provides:
  - Per-day premarket-high freeze in BacktestHarness (CR-01)
  - Capped/ranked per-day watchlist entry gate in BacktestHarness (CR-06)
  - EOD/end-of-run force-close wiring + position-manager replay-clock rebind (CR-05)
  - Multi-day (2-trading-day) regression test proving CR-01/CR-04/CR-05 through the full harness
affects: [07-strategy-optimization, milestone-audit]

tech-stack:
  added: []
  patterns:
    - "Per-day setup state (premarket highs, watchlist) stored keyed by day at setup_day time, applied only at the top of replay_day for that specific day -- never applied eagerly, since run.py's driver calls setup_day for every day before run() replays any of them"
    - "Module-level now_et rebind pattern extended to a second module (bot.position.manager) alongside bot.signal.signal_engine -- both saved/restored in the SAME try/finally in run()"

key-files:
  created: []
  modified:
    - backtester/harness.py
    - tests/backtester/test_harness.py

key-decisions:
  - "_premarket_highs_by_day stores the freeze per-day; replay_day applies self._premarket_highs_by_day.get(day, {}) as its first statement, never setup_day"
  - "_watchlist_by_day keyed by str(day) (a plain date string) since _process_bar derives the lookup key from bar.time_key[:10]; _premarket_highs_by_day keyed by the raw `day` value passed into setup_day/replay_day (whatever type the caller uses) since both call sites always pass the same value through self._days"
  - "Entry gate in _process_bar skips the ENTIRE signal->risk->execution chain (including signal_engine.on_bar itself) for a non-watchlist code, mirroring live's watchlist-scoped subscriptions (SIG-01) -- position management (on_bar, bar_buffer append, sim_execution.on_bar) is unconditional"
  - "replay_day's force-close step pins the replay clock to that day's get_force_close_time_et moment (datetime.combine) so force_close_all's own now_et() guard always fires regardless of the day's actual last bar time -- covers both a short/degraded day and the natural end-of-run case (last day force-closed last)"
  - "_make_daily_frame expanded from 5 to 200 rising business days so the REAL (unmocked) _evaluate_symbol produces a passing candidate for the single-day fixture's US.TEST -- the new CR-06 watchlist gate would otherwise silently drop it since the old 5-row fixture always failed the real function's insufficient-history check"

patterns-established:
  - "Two-symbol multi-day test isolation: one symbol proves the mid-day entry + EOD force-close path, a second symbol proves the last-bar-of-day-never-fills-next-day path -- avoids needing two separate entries on the same code (blocked by the concurrent-position gate)"

requirements-completed: [BT-01, BT-02, BT-03]

duration: 20min
completed: 2026-07-07
---

# Phase 06 Plan 09: Multi-day replay gap closure (CR-01/CR-05/CR-06) Summary

**Per-day premarket-high freeze + capped watchlist entry gate + EOD/end-of-run force-close in BacktestHarness, proven by a real 2-trading-day regression test**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-06T23:44:00-07:00 (approx, first test run)
- **Completed:** 2026-07-06T23:55:01-07:00
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments
- `BacktestHarness.setup_day` now STORES each day's premarket highs (`_premarket_highs_by_day`) instead of applying them immediately; `replay_day` applies only the day being replayed, as its first statement -- closing CR-01 (the cross-day clobber that left every day's Gate 1 evaluating against the LAST day's highs).
- `setup_day` caps candidates to `_WATCHLIST_CAP` (imported from `bot.scanner.scanner`, mirroring `run_daily_scan`'s own sort→cap→rank sequence) and records the day's watchlist code set; `_process_bar` gates the entire entry chain on watchlist membership while position management always runs -- closing CR-06 (an unfiltered `--symbols` code could previously trade even after failing the daily filter).
- `run()` now rebinds `bot.position.manager.now_et` (not just `bot.signal.signal_engine.now_et`) to the replay clock; `replay_day` pins the clock to each day's calendar-aware force-close moment and awaits the unmodified live `force_close_all`, toggling `sim_execution._force_close` around the call and capturing any newly-CLOSED positions -- closing CR-05 (positions open at day/range end were previously silently dropped from the trade log).
- Added a real 2-NYSE-trading-day regression test (`test_multiday_replay_proves_cr01_cr04_cr05_cr06`) that drives the actual `BacktestHarness` + `SimulatedBarFeed` (only yfinance's I/O seam and the daily-filter reuse patched) and proves all three closed gaps plus CR-04 (session-boundary fill guard) through the full harness, not just the feed in isolation.
- `tests/backtester/` grew from 28 to 40 passing tests; full repo suite (629 tests) passes.

## Task Commits

Each task was committed atomically:

1. **Task 1: Per-day premarket-high freeze (CR-01) + watchlist cap and entry gate (CR-06)** - `ac73ec5` (feat)
2. **Task 2: EOD + end-of-run force-close wiring and replay-clock rebind for the manager (CR-05)** - `d1420f5` (feat)
3. **Task 3: Multi-day (2+ trading day) regression test proving CR-01/CR-04/CR-05/CR-06 closed** - `859f731` (test)

_Note: TDD gating was not requested for this plan (`autonomous: true`, no `tdd="true"` tasks); each task's own automated verification (`pytest`) gated the commit._

## Files Created/Modified
- `backtester/harness.py` - `_premarket_highs_by_day`/`_watchlist_by_day` per-day state; `setup_day` caps+stores instead of applying eagerly; `replay_day` applies the day's frozen highs first, then force-closes at EOD; `run()` rebinds both `signal_engine.now_et` and `position.manager.now_et`; `_process_bar` gates the entry branch on watchlist membership.
- `tests/backtester/test_harness.py` - updated `test_setup_day_reuses_scanner_point_in_time_functions` for the new store/apply split; expanded `_make_daily_frame` to 200 rows so the real daily filter passes for the single-day fixture; added `test_replay_day_force_closes_position_left_open_at_eod` and `test_multiday_replay_proves_cr01_cr04_cr05_cr06`.

## Decisions Made
- Watchlist-by-day is keyed by `str(day)` (matches `bar.time_key[:10]`'s string shape at lookup time) while premarket-highs-by-day is keyed by the raw `day` value passed into `setup_day`/`replay_day` (both call sites always pass the identical value via `self._days`, so no normalization is needed there).
- The entry gate in `_process_bar` skips `signal_engine.on_bar` itself (not just the risk/execution steps after it) for a non-watchlist code -- mirrors live's watchlist-scoped subscriptions (only watchlist codes are ever pushed bars for signal evaluation in production).
- CR-03 (out-of-coverage `BacktestWindowError`) is NOT re-tested in this plan's new test file -- Plan 07 already added `tests/backtester/test_feed.py::test_coverage_guard_names_the_uncovered_trading_day`, which exactly satisfies the plan's (d) acceptance criterion; reused per the plan's own guidance rather than duplicated.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Expanded `_make_daily_frame` fixture from 5 to 200 rows so the real `_evaluate_symbol` produces a passing candidate**
- **Found during:** Task 1 (running `pytest tests/backtester/` after adding the CR-06 watchlist gate)
- **Issue:** `test_full_replay_produces_a_closed_trade_filled_at_next_bar_open` builds its harness with the REAL (unmocked) `_evaluate_symbol`, and the existing `_make_daily_frame()` fixture only supplied 5 business days -- deliberately insufficient for `_evaluate_symbol`'s real SMA200/RVOL-lookback requirements (its docstring explicitly said "Not needed for the signal path... only exercised by the setup_day point-in-time-reuse test", which was true before this plan since the watchlist was never checked before an entry). Once Task 1 added the CR-06 entry gate, `US.TEST` no longer had a real daily-filter candidate and was silently excluded from its own day's watchlist, breaking the existing full-replay entry test.
- **Fix:** Rebuilt `_make_daily_frame()` as 200 rising business days (closes 70.0 -> 90.0) so SMA200 is computable and below the most recent close (D2 passes), the most recent close/high (90.0/90.5) sit below the injected `TodayPrice`'s synthetic close (97.5) so D1 and D3 (>=3% gap) both pass, and the universe price filter passes trivially.
- **Files modified:** `tests/backtester/test_harness.py`
- **Verification:** `python3 -m pytest tests/backtester/ -q` -- all 38 tests (at that point) passed, including the previously-broken full-replay test.
- **Committed in:** `ac73ec5` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1)
**Impact on plan:** Necessary for correctness -- the fixture change was required for the new CR-06 gate to coexist with the existing full-replay test's use of the real (unmocked) daily-filter function; no scope creep beyond what Task 1's own read_first notes flagged as a risk.

## Issues Encountered
`harness.sim_execution.fills` mixes `FillEvent` objects (entry fills) with plain dicts (exit/force-close fills, appended by `manage_exit`) -- the multi-day test's initial CR-04 assertion (`f.code == "US.TESTB"`) raised `AttributeError` on the dict-shaped exit-fill entries. Resolved by normalizing both shapes with a small `_fill_code()` helper before filtering (test-only change, no production code touched).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
Multi-day replay is now correct end-to-end: each day uses its own premarket highs and capped watchlist, no signal fills across a session boundary (CR-04, re-verified through the full harness), and every position is force-closed and captured into the trade log by end of run(). This closes the last of the 7 multi-day replay BLOCKERs (CR-01..CR-07) tracked since the 2026-07-06 gap-closure plan. `backtester/` is now ready for `/gsd-secure-phase 06` re-audit of 06-07..06-09 and for the v1.0 milestone's remaining backtester gap-closure item to be marked resolved.

## Self-Check: PASSED
All created/modified files exist on disk; all 3 task commit hashes (ac73ec5, d1420f5, 859f731) found in git log.
