---
phase: 06-backtester
plan: 10
subsystem: backtester
tags: [pandas, dropna, yfinance, data-hygiene, gap-closure]

requires:
  - phase: 06-backtester
    provides: SimulatedBarFeed (backtester/feed.py) from earlier 06-01..06-06 plans
provides:
  - dropna guard in _materialize_bars and _load_premarket preventing NaN crash/poisoning
  - regression test reproducing the realistic union-index NaN-padding shape
affects: [06-backtester verification, BT-04 500-symbol-scale correctness, BT-02 point-in-time premarket-high correctness]

tech-stack:
  added: []
  patterns:
    - "dropna(subset=[...]) immediately after sort_index() and before any per-row loop that does int()/float() conversion on OHLCV fields"

key-files:
  created: []
  modified:
    - backtester/feed.py
    - tests/backtester/test_feed.py

key-decisions:
  - "dropna placed AFTER sort_index() and BEFORE the per-row loop in both _materialize_bars and _load_premarket, so the guard applies identically to the CSV-cache-hit path (cached files preserve NaN rows from an earlier unguarded fetch) and the fresh-network path"
  - "A symbol whose entire frame becomes empty after dropna is handled by the existing empty-frame and coverage guards exactly as a genuinely-missing symbol would be -- no new empty-handling logic needed"
  - "Regression test uses a runtime-derived recent NYSE trading day (via pandas_market_calendars), never a hardcoded 2026 literal, to avoid contributing to the 06-12 window-guard time bomb"
  - "Test frame gives US.HALTED one real bar plus one NaN-padded bar (not an all-NaN frame) -- an all-NaN frame is already filtered out entirely by get_ticker_frame's dropna(how='all').empty check upstream, so it would never reach _materialize_bars; the realistic union-index shape is partial padding on an otherwise-real frame"

patterns-established:
  - "Numeric-conversion loops over yfinance-sourced frames must dropna(subset=[...]) immediately before the loop, on every load path (network + cache)"

requirements-completed: [BT-04, BT-02]

duration: 12min
completed: 2026-07-07
---

# Phase 06 Plan 10: NaN Union-Index Padding Guard Summary

**Closed 06-VERIFICATION BLOCKER Gap 1 by adding `dropna(subset=['open','high','low','close','volume'])` to both `_materialize_bars` and `_load_premarket` in `backtester/feed.py`, preventing a realistic multi-ticker yfinance union-index NaN-padded frame from crashing `int(row["volume"])` or silently poisoning `premarket_highs()` with a NaN that would make Gate 1's `close > premarket_high` evaluate False forever.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-07T13:50:xx Z (approx)
- **Completed:** 2026-07-07T14:02:39Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- `_materialize_bars` drops any row missing an OHLCV field immediately after `sort_index()` and before the per-row `int()`/`float()` loop — a NaN row can no longer raise `ValueError: cannot convert float NaN to integer`.
- `_load_premarket` applies the identical dropna to each per-symbol frame before its per-row loop — a NaN premarket high can no longer reach `premarket_highs()`'s `max(...)`, so Gate 1 (`close > premarket_high`) can never be silently poisoned to always-False for an affected symbol.
- Both guards sit after `sort_index()`/before the loop, so they apply uniformly to the CSV-cache-hit path (a cache file written by an earlier unguarded fetch preserves NaN rows) and the fresh-network path.
- New regression test `test_union_index_nan_rows_do_not_crash_or_poison_premarket_high` reproduces the realistic two-symbol union-index shape (one symbol with real bars throughout, a second symbol sharing those timestamps but NaN at one RTH bar and one premarket bar) and asserts construction does not raise and `premarket_highs()` never returns a NaN.

## Task Commits

Each task was committed atomically:

1. **Task 1: Drop NaN rows before the per-row loop in `_materialize_bars` and `_load_premarket`** - `83eb788` (fix)
2. **Task 2: Regression test — union-index NaN-padded multi-ticker frame** - `88ac41b` (test)

_Note: this was a `type: execute` plan (not `type: tdd`), so tasks were committed in fix-then-test order per the plan's own task numbering rather than a RED/GREEN cycle._

## Files Created/Modified

- `backtester/feed.py` - Added `dropna(subset=['open','high','low','close','volume'])` in `_materialize_bars` (after `sort_index()`, before the per-row loop) and in `_load_premarket` (assigning `pre = frame.sort_index().dropna(...)` and iterating `pre.iterrows()`)
- `tests/backtester/test_feed.py` - Added `test_union_index_nan_rows_do_not_crash_or_poison_premarket_high`, reproducing the union-index NaN-padding shape across both the RTH and premarket load paths, plus a direct `_materialize_bars` call mirroring the verifier's exact reproduction

## Decisions Made

- Placed both dropna calls immediately after `sort_index()` so the guard is a single, uniformly-applied step covering both load paths (network + CSV cache), rather than duplicating the guard per-path.
- Did not add any new empty-frame handling: a symbol whose frame becomes fully empty after dropna is already handled correctly by the existing empty-frame guards (`if frame is None or frame.empty: continue`) and by `_enforce_coverage`, which only requires that *some* code has bars for a given trading day — not every requested code.
- Built the regression test's "HALTED" symbol with one real bar plus one NaN-padded bar (not an all-NaN frame), since an all-NaN frame is already filtered out upstream by `get_ticker_frame`'s `dropna(how="all").empty` check and would never reach `_materialize_bars` at all — the realistic union-index shape is *partial* padding on an otherwise-valid frame.
- Anchored the new test's trading day at runtime via `pandas_market_calendars` (15–2 days back from "now") instead of a hardcoded date literal, so the test can never become a second contributor to the 06-12 window-guard time bomb.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- 06-VERIFICATION Gap 1 (BLOCKER) is closed: a realistic multi-ticker yfinance frame with union-index NaN padding rows now loads through `SimulatedBarFeed` without raising and never produces a NaN premarket high, on both the network and CSV-cache paths.
- No `bot/` files were touched — this plan's scope was strictly `backtester/feed.py` + its test, as required.
- Remaining 06-VERIFICATION gaps (Gate-7 trades persistence, exit-slippage sign, 06-12 fixtures time-bomb) are out of scope for this plan and tracked separately (06-11, 06-12).

## Self-Check: PASSED

- `backtester/feed.py` exists: FOUND
- `tests/backtester/test_feed.py` exists: FOUND
- Commit `83eb788` (fix): FOUND in `git log --oneline --all`
- Commit `88ac41b` (test): FOUND in `git log --oneline --all`
- `grep -n "dropna(subset=" backtester/feed.py` returns exactly 2 matches: PASSED
- `python3 -m pytest tests/backtester/ -q` passes 41 tests (was 40 before this plan): PASSED
- `git diff --name-only HEAD~2 HEAD` shows only `backtester/feed.py` and `tests/backtester/test_feed.py`, no `bot/` file: PASSED
- New test contains no hardcoded `2026-...` literal: PASSED

---
*Phase: 06-backtester*
*Completed: 2026-07-07*
