---
phase: 06-backtester
plan: 11
subsystem: backtester
tags: [circuit-breaker, gate-7, state-store, slippage, regression-test, gap-closure]

# Dependency graph
requires:
  - phase: 06-backtester
    provides: BacktestHarness replay controller, SimulatedExecution N+1-open fills, StateStore
provides:
  - StateStore.record_trade — parameterized, lock-guarded INSERT INTO trades
  - BacktestHarness._capture_closed_trades now persists every closed trade into the scratch trades table
  - Correct adverse-direction exit slippage in SimulatedExecution.manage_exit
  - Gate-7 (-2R daily circuit breaker) regression test proving FSM parity live/backtest
  - Exit-slippage-sign regression test
affects: [06-backtester, 06.2-code-review-remediation, backtest report integrity (BT-03)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "StateStore write methods mirror record_position exactly: with self._lock: -> single parameterized INSERT -> self._conn.commit()"
    - "Runtime-derived NYSE trading day (pandas_market_calendars.valid_days) anchoring for new backtester test fixtures — avoids joining the 06-12 fixture time-bomb"

key-files:
  created: []
  modified:
    - bot/state/store.py
    - backtester/harness.py
    - backtester/execution.py
    - tests/backtester/test_harness.py
    - tests/backtester/test_execution.py

key-decisions:
  - "record_trade is purely additive (no existing call site changes) — live bot behavior is completely unaffected; only the backtest harness calls it"
  - "New Gate-7 test fixture anchors to a runtime-derived recent NYSE trading day instead of a hardcoded 2026-... literal, per the plan's read_first guidance (matches Plan 06-10 Task 2's pattern) — the shared existing helpers (_make_5m_frame, _make_premarket_frame_for) hardcode 2026-06-01/02, so local variants (_make_cb_5m_frame, _make_cb_premarket_frame) were written instead of reusing them for this new fixture"

patterns-established:
  - "Local, day-parameterized frame-builder helpers for new backtester test fixtures instead of reusing the module's hardcoded-date shared helpers, so future NYSE-calendar drift doesn't silently break new tests"

requirements-completed: [BT-01, BT-03]

# Metrics
duration: 40min
completed: 2026-07-07
---

# Phase 6 Plan 11: Backtest Gate-7 Circuit-Breaker Parity + Exit-Slippage-Sign Fix Summary

**Backtest replays now write every closed trade into the scratch StateStore's `trades` table via a new parameterized `StateStore.record_trade`, so SignalEngine's Gate 7 (-2R daily circuit breaker) — reused unmodified from the live FSM — reads real realized P&L and can actually trip during a replay; exit fills now apply slippage in the adverse (downward) direction instead of inflating exit prices.**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-07-07 (session start)
- **Completed:** 2026-07-07T14:16:07Z
- **Tasks:** 3 completed
- **Files modified:** 5

## Accomplishments
- Closed BLOCKER Gap 2 from 06-VERIFICATION: the -2R daily circuit breaker (Gate 7) can now actually trip during a backtest, because `_capture_closed_trades` persists each newly-CLOSED position into the scratch `trades` table via the new `StateStore.record_trade`.
- Fixed the wrong-sign exit slippage in `backtester/execution.py` (`manage_exit`'s force-close and normal legs now subtract slippage — adverse for a long-only SELL exit — instead of adding it, which previously inflated every exit price and overstated performance whenever `slippage_usd > 0`).
- Corrected the harness module docstring's false claim that Gate 7 "can still block entries" without qualification — it now accurately states Gate 7 reads the backtest's own recorded trades.
- Added a real, end-to-end regression test (`test_gate7_circuit_breaker_trips_from_backtest_recorded_trades`) that drives an engineered losing day through the actual `BacktestHarness`/`SignalEngine`/`RiskEngine`/`PositionManager` pipeline and proves the breaker blocks a later, otherwise gate-passing entry the same day — verified to FAIL if the `record_trade` call is reverted.
- Added `test_exit_slippage_is_adverse` pinning the sign of all three fill legs (entry BUY up, normal exit SELL down, force-close exit SELL down).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add parameterized StateStore.record_trade — INSERT INTO trades, lock-guarded** - `59d5342` (feat)
2. **Task 2: Persist captured trades from the harness + fix exit-slippage sign + correct the Gate-7 docstring** - `a035d89` (fix)
3. **Task 3: Regression tests — Gate-7 trips from backtest trades; exit slippage is adverse** - `6bdd67f` (test)

_Note: Task 3 introduces two new tests (no RED/GREEN/REFACTOR TDD cycle — this is a standard `type: execute` plan, not `type: tdd`)._

## Files Created/Modified
- `bot/state/store.py` — new `record_trade(position_id, code, entry_price, exit_price, quantity, exit_reason, r_multiple, closed_at, trade_id=None)` method; mirrors `record_position`'s lock-guarded, single-parameterized-INSERT structure exactly; generates `trade_id` via `uuid4()` when not supplied; normalizes `closed_at` (datetime → `.isoformat()`, str passthrough). Added `from uuid import uuid4` import.
- `backtester/harness.py` — `_capture_closed_trades` now also calls `self._store.record_trade(...)` for every newly-CLOSED position (same values already assembled for the `trade_log` row); module docstring (lines ~38-46) and the `_capture_closed_trades` section header/docstring reworded to reflect that Gate 7 reads the backtest's own recorded trades, not an inert dependency.
- `backtester/execution.py` — `manage_exit`'s force-close leg (`last["close"]`) and normal leg (`next_bar["open"]`) now subtract `self._slippage` instead of adding it; `consume_intent`'s entry fill stays `+ self._slippage` (a BUY correctly slips up); added inline comments and updated docstrings noting the long-only SELL-exit adverse-direction rationale.
- `tests/backtester/test_harness.py` — new `test_gate7_circuit_breaker_trips_from_backtest_recorded_trades` plus supporting fixtures (`_CB_DAY` runtime-derived via `pandas_market_calendars`, `_CB_LOSER_BARS`, `_CB_WINNER_BARS`, `_make_cb_5m_frame`, `_make_cb_premarket_frame`, `_mock_yf_download_gate7`).
- `tests/backtester/test_execution.py` — new `test_exit_slippage_is_adverse`.

## Decisions Made
- **record_trade is purely additive.** No existing StateStore call site changes — live bot behavior is completely unaffected by this plan; only `BacktestHarness` calls the new method.
- **New test fixtures anchor to a runtime-derived recent NYSE trading day**, not a hardcoded `2026-...` literal, per the plan's read_first guidance (mirrors Plan 06-10 Task 2's `pandas_market_calendars.valid_days(...)` anchoring pattern). Because the module's existing shared helpers (`_make_5m_frame`, `_make_premarket_frame_for`, `_make_daily_frame`) hardcode `2026-06-01`/`2026-06-02` internally for their own prior-session/index construction, reusing them directly for a dynamically-anchored day would have broken the "prior sessions strictly before `day`" invariant (or silently drifted once the hardcoded dates fall outside the live yfinance ~60-day window). Wrote local, day-parameterized variants (`_make_cb_5m_frame`, `_make_cb_premarket_frame`) instead — `_make_daily_frame()` itself was still safely reused as-is since its content is irrelevant once `_evaluate_symbol` is patched with the always-candidate fake (same reasoning the existing multi-day test already relies on).

## Deviations from Plan

None - plan executed exactly as written. The plan's own read_first section anticipated the runtime-derived-day requirement for new fixtures (referencing Plan 06-10 Task 2's precedent), and that guidance was followed faithfully rather than treated as a deviation.

## Issues Encountered

None. One self-correction during Task 3 execution (not a deviation from the plan, but worth recording): the first draft of the new Gate-7 test used a hardcoded `"2026-06-01"` day (matching the existing single-day fixture's convention) before re-reading the plan's acceptance criteria, which explicitly require a runtime-derived day for new fixtures. Caught and fixed before committing — the committed test uses `pandas_market_calendars` exclusively, verified with `grep -c '"2026-'` returning `0` for the new fixture block.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- BLOCKER Gap 2 (BT-01 exact-same-FSM parity, BT-03 report integrity) from 06-VERIFICATION is closed: the -2R daily circuit breaker now genuinely participates in backtest replays, and exit fills are directionally honest under non-zero slippage.
- `python3 -m pytest tests/ -q` — 631 passed, 1 skipped (was 629 passed, 1 skipped before this plan; net +2 new tests, zero regressions).
- `run.py` untouched — the live-DB collision guard and scratch-store isolation are unaffected.
- Remaining backtester gap-closure items (per MEMORY.md): 06-10 (NaN feed hygiene, time-bomb fix for the OLDER hardcoded fixtures already in the suite) and 06-12 are separate, already-planned follow-ups — this plan does not touch those pre-existing hardcoded-date fixtures, only the two NEW ones it introduces.

## Verification Results

- `python3 -m pytest tests/backtester/ -q` → 42 passed (was 40 before this plan).
- `python3 -m pytest tests/ -q` → 631 passed, 1 skipped (was 629 passed, 1 skipped).
- `grep -n "INSERT INTO trades" bot/state/store.py` → exactly one parameterized insert (line 393).
- `grep -rn "INTO trades" backtester/` → only a docstring reference in `report.py` (still accurate: nothing in `bot/` writes it); the actual write goes through `store.record_trade`, no raw SQL in the harness.
- `grep -n "self._slippage" backtester/execution.py` → entry `+` (line 76), both exit legs `-` (lines 116, 132).
- `git diff --name-only` across all three task commits → only `bot/state/store.py`, `backtester/harness.py`, `backtester/execution.py`, `tests/backtester/test_harness.py`, `tests/backtester/test_execution.py`; `run.py` never touched.
- Manually verified the new Gate-7 test's fail-fast property: temporarily reverting the `store.record_trade` call in `_capture_closed_trades` and re-running `test_gate7_circuit_breaker_trips_from_backtest_recorded_trades` produces a failure (`store.get_circuit_breaker_date()` returns `None` instead of the session date, and `US.WINNER` incorrectly enters) — confirming the test actually exercises the fix rather than passing vacuously. File restored immediately after (confirmed via `git diff --stat` showing no residual diff).

## Self-Check: PASSED

All 5 modified files verified present on disk; all 3 task commit hashes (`59d5342`, `a035d89`, `6bdd67f`) verified present in `git log`. Full test suite (`python3 -m pytest tests/ -q`) re-run: 631 passed, 1 skipped.

---
*Phase: 06-backtester*
*Completed: 2026-07-07*
