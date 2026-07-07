---
phase: 06-backtester
plan: 07
subsystem: backtester
tags: [yfinance, backtest, point-in-time, look-ahead, pandas-market-calendars]

# Dependency graph
requires:
  - phase: 06-backtester (plans 01-06)
    provides: SimulatedBarFeed, BacktestHarness, SimulatedExecution, report writer — the
      full offline replay pipeline this plan corrects the data-source defects in
provides:
  - Real 60-calendar-day yfinance 5m replay window that agrees with what is actually fetched
  - Per-trading-day coverage guard (BacktestWindowError naming any zero-bar day)
  - Same-session next_bar() — a signal/stop can never fill on the following day's open
  - Point-in-time premarket_highs(day) and synthetic_today_price(code, day), both built
    from a one-time premarket-only load, correct for any replay day regardless of age
affects: [06-08, 06-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "One-time load + in-memory point-in-time accessor (not per-call live fetch) for any
      data keyed by historical replay day — premarket bars now load once in __init__ and
      are filtered by day string on every accessor call, eliminating a call-time-relative
      staleness ceiling"
    - "Coverage guard as a post-load invariant check (_enforce_coverage) — computed from
      what actually loaded, not from what was requested, so a partial/degraded fetch fails
      loudly by day name instead of silently replaying an empty session"

key-files:
  created: []
  modified:
    - backtester/feed.py
    - tests/backtester/test_feed.py

key-decisions:
  - "INTRADAY_5M_WINDOW_CALENDAR_DAYS=60 replaces the mismatched 58-trading-day/7:5
    calendar-day derivation pair — one true number instead of two that disagreed"
  - "_load_5m calls _download_batch directly with period=60d instead of
    download_intraday_5m (hardcoded period=30d) so the fetch matches the advertised window;
    download_intraday_5m is left in place for intraday_5m_for_tod (RVOL-TOD baselines),
    which is not a listed gap"
  - "_enforce_coverage runs after _load_5m inside __init__, using pandas_market_calendars
    (already a project dependency) rather than hand-rolling a holiday calendar"
  - "_load_premarket is a one-time load (not per-call) mirroring _load_5m's CSV
    read-through pattern under a distinct '5m_pre' interval cache tag"
  - "synthetic_today_price mirrors resolve_today_price's PREMARKET branch exclusively —
    the prior RTH-branch implementation was itself the CR-07 look-ahead defect"

patterns-established:
  - "Kwargs-dispatching yfinance.download side_effect in tests (side_effect inspecting
    kwargs.get('prepost')) — needed once a feed method makes two distinct-shaped
    _download_batch calls (RTH vs premarket) during construction"

requirements-completed: [BT-02, BT-04]

# Metrics
duration: ~20min
completed: 2026-07-07
---

# Phase 06 Plan 07: Feed window/coverage/point-in-time gap closure Summary

**Closed 4 of 7 confirmed multi-day-replay BLOCKER defects in `backtester/feed.py` (CR-02, CR-03, CR-04, CR-07): the replay window guard now matches the actual 60-calendar-day fetch, every requested trading day with zero bars fails loudly by name, `next_bar` can never cross a session boundary, and both premarket accessors are now point-in-time premarket-only for the exact replay day instead of relative-to-"now" or full-day look-ahead.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2 completed
- **Files modified:** 2 (`backtester/feed.py`, `tests/backtester/test_feed.py`)

## Accomplishments

- **CR-03 (window/coverage mismatch):** replaced the mismatched `INTRADAY_5M_WINDOW_TRADING_DAYS=58` / `_WINDOW_CALENDAR_DAYS=round(58*7/5)` pair with one `INTRADAY_5M_WINDOW_CALENDAR_DAYS=60`, and switched `_load_5m`'s network fetch from `download_intraday_5m` (hardcoded `period="30d"`, ~21 trading days) to a direct `_download_batch(..., download_kwargs={"period": "60d", ...})` call so the window guard and the real fetch finally agree. Added `_enforce_coverage()`, called from `__init__` after `_load_5m`, which computes NYSE trading days in `[start, end]` via `pandas_market_calendars` and raises `BacktestWindowError` naming any day with zero bars across every requested code.
- **CR-04 (session-boundary look-ahead):** `next_bar(code, after)` now checks that its candidate bar's date matches `after`'s date; a later-day candidate returns `None` instead of crossing into the next session.
- **CR-02 (premarket-high staleness):** replaced `premarket_highs`'s per-call `period="5d"`-from-now fetch with a one-time `_load_premarket()` load (called from `__init__`) that stores pre-09:30-ET bars per code in `self._premarket_bars_by_code`, CSV-cached under a distinct `"5m_pre"` interval tag. `premarket_highs(day)` now reads this in-memory store with zero network calls per invocation — correct for any replay day regardless of age.
- **CR-07 (synthetic TodayPrice look-ahead):** `synthetic_today_price(code, day)` rewritten to mirror `resolve_today_price`'s PREMARKET branch (latest premarket close for both `today_open`/`today_price`, max premarket high for `today_high`) instead of the RTH branch, which had been leaking the day's full-session close/max-high into the daily-scan price.

## Task Commits

1. **Task 1: Real 60-day replay window + per-day coverage guard + same-session next_bar (CR-03, CR-04)** - `3a1fa29` (feat)
2. **Task 2: Point-in-time premarket data source — premarket_highs + synthetic_today_price (CR-02, CR-07)** - `cb91656` (feat)

_Note: this is a gap-closure/bug-fix plan on already-tested code (not new-feature scaffolding); each task commit bundles its regression tests with its implementation fix, following the same convention plan 06-02 used for this file (see `31ef9ef`, `35c2194`)._

## Files Created/Modified

- `backtester/feed.py` - `INTRADAY_5M_WINDOW_CALENDAR_DAYS=60`; `_load_5m` fetches via `_download_batch(period="60d")` directly; new `_enforce_coverage()` and `_load_premarket()` methods; `next_bar` enforces same-session date; `premarket_highs`/`synthetic_today_price` rewritten to read from the one-time premarket load
- `tests/backtester/test_feed.py` - 6 new tests for Task 1 (window/fetch agreement, coverage-guard naming, session-boundary `next_bar`); Task 2 replaced 2 obsolete RTH-based tests with 4 premarket-point-in-time tests and updated the CSV-cache-hit test's network-call-count expectations (construction now makes 2 calls: RTH + premarket)

## Decisions Made

- Kept `download_intraday_5m` (period="30d") in place for `intraday_5m_for_tod` (RVOL-TOD baselines) per the plan's explicit instruction — not a listed gap, and the daily rvol_baseline fallback already covers older days point-in-time.
- Placed `_load_premarket()`'s call in `__init__` immediately after `_load_5m()` and before `_enforce_coverage()` — coverage only inspects `_bars_by_code` (RTH), so ordering relative to the premarket load doesn't affect its correctness, but grouping the two loads together keeps `__init__` readable.

## Deviations from Plan

None — plan executed as specified. One incidental note: the plan's acceptance-criteria grep `grep -n "INTRADAY_5M_WINDOW_TRADING_DAYS\|_WINDOW_CALENDAR_DAYS" backtester/feed.py` (expected to return nothing) will actually match the *new* constant name `INTRADAY_5M_WINDOW_CALENDAR_DAYS` as a substring — this is a plan-wording overlap, not a real defect; the old derived `_WINDOW_CALENDAR_DAYS` variable and the old `INTRADAY_5M_WINDOW_TRADING_DAYS` constant are both fully removed, which was the actual intent.

## Verification

- `python3 -m pytest tests/backtester/ -q` → 35 passed (was 33 before this plan; +6 new Task 1 tests, +2 net Task 2 tests after replacing 2 obsolete tests with 4).
- `python3 -m pytest -q` (full repo) → 624 passed, 1 skipped — no regressions outside `backtester/`.
- `grep -n "INTRADAY_5M_WINDOW_CALENDAR_DAYS = 60" backtester/feed.py` and `grep -n '"period": "60d"' backtester/feed.py` both match (the latter matches twice: `_load_5m` and `_load_premarket`).
- `grep -n "_premarket_bars_by_code" backtester/feed.py` shows the attribute set in `__init__` and read by both `premarket_highs` and `synthetic_today_price`.
- `grep -n '"period": "5d"' backtester/feed.py` returns nothing (the from-now 5d fetch is gone).
- `grep -n "gateway" backtester/feed.py` returns only the module-docstring sentence stating no broker gateway is imported — no broker gateway import introduced.

## Known Stubs

None.

## Threat Flags

None — this plan closes existing threat-register items (T-06-07-01/02/03 per the plan's own threat model) rather than introducing new surface. No new network endpoints, auth paths, or schema changes were added.

## Self-Check: PASSED

- FOUND: `backtester/feed.py` (modified, exists)
- FOUND: `tests/backtester/test_feed.py` (modified, exists)
- FOUND: commit `3a1fa29` in git log
- FOUND: commit `cb91656` in git log
