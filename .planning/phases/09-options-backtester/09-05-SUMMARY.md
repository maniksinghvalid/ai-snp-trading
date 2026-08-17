---
phase: 09-options-backtester
plan: 05
subsystem: backtester-options-data
tags: [massive-api, options-data, lazy-fetch, cr-01, verification-gap-closure]

requires:
  - phase: 09-options-backtester
    provides: backtester/options/{data,greeks,engine,report}.py, backtester/options_run.py, backtester/massive.py (Plans 09-01/09-02/09-03/09-04)
provides:
  - backtester.options.data::OptionChainSource.rows_for/expiries_for_day/_ensure_bars (lazy per-decision-day fetch)
  - backtester.massive::MassiveDataSource.cached_contracts (expired=true+false union), cached_option_bars (ticker-keyed negative cache)
  - backtester.options.engine::OptionsBacktestEngine.update_iv/close_open_at_end, unrealized-aware daily-loss breaker
  - backtester.options.greeks::implied_vol NaN/inf fail-closed guard
  - backtester.options_run::--workers flag, validate-before-write CLI ordering, single exception boundary
  - docs/research/2026-08-17-options-backtest-results.md corrected root cause + operator runbook
affects: []

tech-stack:
  added: []
  patterns: [lazy-per-decision-day-fetch, in-memory-fetch-memoisation, ticker-keyed-negative-cache, validate-before-filesystem-write]

key-files:
  created: []
  modified:
    - backtester/massive.py
    - backtester/options/data.py
    - backtester/options/engine.py
    - backtester/options/greeks.py
    - backtester/options_run.py
    - tests/backtester/options/test_data.py
    - tests/backtester/options/test_engine.py
    - tests/backtester/options/test_greeks.py
    - tests/backtester/options/test_options_run.py
    - docs/research/2026-08-17-options-backtest-results.md
    - .planning/phases/09-options-backtester/09-VALIDATION.md

key-decisions:
  - "T-09-13 and T-09-14 landed as two commits split by file overlap (data.py/massive.py exclusive to T-09-13; engine.py/options_run.py/greeks.py shared by both tasks per the plan's own files_modified lists) rather than one commit per literal task ID -- true hunk-level separation of interleaved edits to the same functions was not worth the risk of a broken intermediate diff"
  - "massive.py's fetch_contracts sort key changed from r.get('strike_price', 0) to r.get('strike_price') or 0 (Rule 1) while already rewriting that block for the expired=true/false union -- a present-but-null strike_price previously raised TypeError mid-sort (REVIEW.md IN-08)"
  - "No live Massive fetch was run in this plan (offline-only per its own scope) -- all three hypotheses remain INSUFFICIENT-EVIDENCE in the results doc; the gap is now bounded wall-clock (~25h SPY once on the free tier) rather than structural infeasibility (~183h/window under the old eager design)"

requirements-completed: [OBT-02, OBT-03, OBT-05, OBT-07]

duration: ~55min
completed: 2026-08-17
---

# Phase 9 Plan 05: Options backtester gap closure (lazy fetch, CR-01/WR-01/WR-02/WR-05, results correction) Summary

Replaced the eager "fetch every strike-band contract x every expiry up front" data layer with
a lazy per-decision-day design that touches ~7.5k contracts for SPY's entire 2024-08→2026-08
history (vs 58,366+ for one IS window under the old design), fixed the CR-01 survivorship-bias
gap (open positions at `--end` were silently dropped from every reported metric) plus three
REVIEW.md warnings (NaN-poisoned IVR, breaker ignoring unrealized P&L, a rejected CLI override
leaving a half-populated run directory), and corrected the results doc's root-cause narrative
from "observed rate limit" to the actual server-side 5-request tier cap, with exact operator
commands and runtime projections for both the free and paid Massive tiers.

## Performance

- **Duration:** ~55 min
- **Completed:** 2026-08-17T20:09:16Z
- **Tasks:** 3 (T-09-13, T-09-14, T-09-15)
- **Files modified:** 11 (5 source, 4 test, 1 doc, 1 validation tracker)

## Accomplishments

- **VERIFICATION gap 3 closed** (data-layer feasibility): `OptionChainSource.rows_for(day,
  expiry, underlying_px, band_pct)` fetches bars ONLY for the one expiry `pick_expiry` selects
  on a decision day, and only for strikes in a narrow OTM-side band — offline-measured at
  ~7.5k contracts for SPY's entire entitled history at a ±10% band (480 decision days, 55
  distinct chosen expiries), vs 58,366+ candidates for one IS window under the eager design.
  `fetch_contracts` now unions `expired=true`/`expired=false` (WR-03); `cached_option_bars` is
  keyed by ticker only with negative caching for empty results (WR-04); `--workers` enables
  parallel fetch on a tier without the 5-req/min cap.
- **CR-01 (critical) closed**: `close_open_at_end(day)` marks and records every position still
  open at the replay window's end with `exit_reason="end_of_window"` — no position opened
  during the window is silently dropped from `total_pnl_usd`/`win_rate`/`profit_factor`/
  drawdown. `open_positions_at_end` and `end_of_window_pnl_usd` are recorded in
  `summary.json`'s `assumptions`.
- **WR-01/WR-02/WR-05 closed**: `implied_vol` fails closed on NaN/inf price or spot
  (`math.isfinite` guard) instead of drifting to ~5.0 and poisoning the IV-rank series; the
  daily-loss breaker now trips on realized + unrealized P&L, mirroring live's two trip points
  (`_job_entry_scan` and `_job_manage`); `options_run.main` validates every argument, the
  effective config (via a throwaway temp file), and the API key BEFORE creating the run
  directory, and wraps chain-load/replay/report in one exception boundary.
- **Results doc corrected** (T-09-15): root cause restated as a server-side 5-request-then-429
  tier cap (0.23s raw latency), not a gradually degrading rate; runtime projections for the
  free tier (~25h SPY once, ~2-3 days for the 6-ETF pool) and a paid tier (~5 min with
  `--workers 8`); exact cache-warm + twelve hypothesis-arm commands, resume behavior, and how
  to read `[fetch]` progress lines. All three hypotheses remain INSUFFICIENT-EVIDENCE — no
  live Massive fetch ran in this offline-only plan.

## Task Commits

1. **T-09-13 (data-layer half): lazy per-expiry data layer** - `ae0270c` (feat)
2. **T-09-13 (engine/CLI half) + T-09-14: engine/CLI wiring + CR-01/WR-01/WR-02/WR-05** - `fc34153` (feat)
3. **T-09-15: results doc correction + full-suite gate** - `60a51a9` (docs)

**Plan metadata:** committed at the end of this task (docs), together with STATE.md/ROADMAP.md.

## Files Created/Modified

- `backtester/massive.py` — `fetch_contracts` unions `expired=true`/`expired=false`;
  `cached_option_bars` keyed by ticker only with negative caching, capped at the contract's
  own expiry
- `backtester/options/data.py` — `OptionChainSource.rows_for`/`_ensure_bars`
  (`ThreadPoolExecutor` when `workers>1`), reference-based `expiries_for_day`, `load()` fetches
  the contracts reference + underlying bars only (no per-contract bar fetch); `contracts_for_day` removed (dead code)
- `backtester/options/engine.py` — `update_iv(day)` (single source of truth for the IV-update
  half of a decision day), `_entry_scan` uses `rows_for`, `close_open_at_end(day)`,
  unrealized-aware `_check_daily_breaker`, `strike_band_pct` constructor param
- `backtester/options/greeks.py` — `implied_vol` NaN/inf/non-positive fail-closed guard
- `backtester/options_run.py` — `--workers` flag, `--strike-band-pct` default 20.0→10.0,
  validate-before-filesystem-write ordering, single exception boundary,
  `close_open_at_end`/`open_positions_at_end`/`fetch_stats` wiring
- `tests/backtester/options/test_data.py` — expired union, negative-cache, ticker-keyed cache,
  lazy one-expiry-in-band-per-call proof, `load()`-time DTE-range narrowing
- `tests/backtester/options/test_engine.py` — unrealized-only breaker trip, `_manage_day`
  unrealized accumulation, `close_open_at_end` settlement + unmarkable-residue case, one
  expiry-per-day fetch proof
- `tests/backtester/options/test_greeks.py` — NaN/inf/non-positive `implied_vol` inputs
- `tests/backtester/options/test_options_run.py` — `--set` override reaching a full successful
  run, API-key failure leaving no run dir, unhandled replay exception exits 1
- `docs/research/2026-08-17-options-backtest-results.md` — corrected root cause, runtime
  projections, operator commands
- `.planning/phases/09-options-backtester/09-VALIDATION.md` — T-09-13/14/15 rows added, green

## Decisions Made

- T-09-13 and T-09-14 committed as two commits (not three, one per literal task ID) because
  the plan's own `files_modified` lists overlap heavily in `engine.py`/`options_run.py`;
  `massive.py`/`data.py` (T-09-13-exclusive) landed in commit 1, the shared engine/CLI files
  (both tasks) landed in commit 2.
- `massive.py`'s `fetch_contracts` sort key changed to `r.get("strike_price") or 0` (Rule 1,
  REVIEW.md IN-08) while already rewriting that block for the `expired` union.
- No live Massive fetch was attempted in this plan — offline-only per its own explicit scope
  ("Do NOT run any live Massive fetch in this plan"); the results doc's verdicts remain
  INSUFFICIENT-EVIDENCE, now with the exact commands to close the gap documented for the
  orchestrator's follow-up cache-warming run.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `fetch_contracts` sort key crashes on a present-but-null `strike_price`**
- **Found during:** Task T-09-13, rewriting `fetch_contracts` for the `expired=true`/`false` union
- **Issue:** `r.get("strike_price", 0)` returns `None` (not `0`) when the key is present with a
  JSON `null` value, and sorting a mixed `None`/`float` key list raises `TypeError` — this was
  already flagged as REVIEW.md IN-08 (info-severity, not in this plan's task list) but sat
  directly in the block being rewritten for WR-03.
- **Fix:** `r.get("strike_price") or 0`.
- **Files modified:** `backtester/massive.py`
- **Commit:** `ae0270c`

Or otherwise: plan executed as written for T-09-13/T-09-14/T-09-15.

---

**Total deviations:** 1 auto-fixed (Rule 1)
**Impact on plan:** Zero scope creep — a one-line bug fix directly adjacent to code already
being rewritten for the plan's own acceptance criteria.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. The orchestrator's planned follow-up
(cache-warming run against live Massive data) requires `MASSIVE_API_KEY` to already be set,
which is pre-existing project configuration, not new setup from this plan.

## Next Phase Readiness

- Full suite green: `pytest -q` → **1038 passed, 1 skipped** (baseline 1027; +11 net from new
  gap-closure tests).
- `rules_options.json` and `bot/options/*` unchanged (`git diff --exit-code` clean, both commits).
- `python3 -m backtester.options_run --help` lists `--workers`.
- The orchestrator's planned next step (cache-warming run, then the twelve hypothesis-arm
  invocations from the results doc's "Operator commands" section) can now run within a bounded
  wall-clock budget (~25h SPY once on the free tier) instead of the previously-measured ~183h/
  window infeasibility — this is a scheduling decision, not a further code change.
- One pre-existing untracked file, `.planning/phases/09-options-backtester/09-VERIFICATION.md`,
  was present in the working tree before this plan started and was read as required context but
  not modified or committed by this plan (out of this plan's `files_modified` scope) — flagged
  here so the orchestrator can decide whether it needs a separate commit.

## Self-Check: PASSED

- `backtester/massive.py` — FOUND
- `backtester/options/data.py` — FOUND
- `backtester/options/engine.py` — FOUND
- `backtester/options/greeks.py` — FOUND
- `backtester/options_run.py` — FOUND
- `docs/research/2026-08-17-options-backtest-results.md` — FOUND
- `.planning/phases/09-options-backtester/09-05-SUMMARY.md` — FOUND
- Commit `ae0270c` (T-09-13 data layer) — FOUND
- Commit `fc34153` (T-09-13 engine/CLI + T-09-14) — FOUND
- Commit `60a51a9` (T-09-15 results doc) — FOUND
- Commit `66bb471` (summary + validation) — FOUND

---
*Phase: 09-options-backtester*
*Completed: 2026-08-17*
