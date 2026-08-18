---
phase: 10-external-strategy-research
plan: 01
subsystem: testing
tags: [backtesting, research, pre-registration, yfinance, reddit-strategy-extraction]

# Dependency graph
requires: []
provides:
  - "docs/research/2026-08-18-external-strategies-hypotheses.md — 14-section pre-registration doc (H1-H8 with a-priori predictions, universe/windows/costs/metric-of-record/evidence-floor/verdict rules, arm table, strategy definitions + OP-wording deviations, limitations, rules.json-change statement, SPY regime provenance)"
  - "backtester/experimental/arms.json — frozen 15-arm machine-readable definition list (ext2/orb/vwap_pb defaults + per-arm param deltas)"
  - "backtester/experimental/__init__.py and tests/backtester/experimental/__init__.py — empty package markers for Wave-2/3 code"
  - "backtester/cache/SPY_1d_regime.csv — SPY daily OHLCV 2022-01-03..2026-08-17 (1,159 rows, gitignored) feeding the future weekly_regime() indicator"
affects: [10-02-PLAN, 10-03-PLAN, 10-04-PLAN, 10-05-PLAN, 10-06-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pre-registration-before-any-run-artifact discipline (mirrors Phase 9 OBT-06): hypotheses doc + frozen arms.json committed in ONE commit before backtester/results/experimental/ exists"
    - "arms.json config schema: {version, frozen, defaults.{ext2,orb,vwap_pb}, arms:[{name,strategy,params,diagnostic?,purpose}]} — deltas-only params per arm against strategy defaults"

key-files:
  created:
    - docs/research/2026-08-18-external-strategies-hypotheses.md
    - backtester/experimental/arms.json
    - backtester/experimental/__init__.py
    - tests/backtester/experimental/__init__.py
    - backtester/cache/SPY_1d_regime.csv (gitignored)
  modified: []

key-decisions:
  - "Task-commit protocol deviation (plan-mandated, not an executor choice): the plan's own Task 3 action + T-10-01 threat mitigation require the hypotheses doc and arms.json to land in ONE commit, not per-task commits — Tasks 1 and 2 were executed and verified but left uncommitted until Task 3's single combined commit, so git history proves pre-registration precedes any run artifact"
  - "diagnostic flag placed at arm-level (arms[].diagnostic), not nested inside params, per the plan's own 'Artifacts this phase produces' schema (arms[].{name,strategy,params,diagnostic}) rather than the looser acceptance-criteria prose"
  - "Added an optional purpose field to each arms.json entry (not required by the schema) so the hypotheses doc's arm table can be reproduced directly from the JSON with zero drift risk"

requirements-completed: [XSR-01, XSR-03]

duration: 10min
completed: 2026-08-18
---

# Phase 10 Plan 01: Pre-registration Summary

**Committed the 14-section H1-H8 pre-registration doc and the frozen 15-arm arms.json in one commit, then fetched and cache-recorded the SPY daily regime series via yfinance — zero Massive requests, zero bot/rules.json changes.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-08-18T18:53:00Z (approx, session start)
- **Completed:** 2026-08-18T18:59:17Z
- **Tasks:** 3
- **Files modified:** 5 (4 committed, 1 gitignored cache CSV)

## Accomplishments

- Froze `backtester/experimental/arms.json`: 15 arms across `ext2`/`orb`/`vwap_pb` with per-strategy defaults and delta-only overrides; validated by the plan's structural assertion script.
- Wrote `docs/research/2026-08-18-external-strategies-hypotheses.md`: all 12 required `##` sections plus `### H1`..`### H8` verbatim from the approved design (`~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md`), including full Reddit-thread credibility extraction (Appendix A transcribed, not re-fetched — reddit.com blocks direct fetch from this environment), the explicit MACD-12/26/9 and 10-period-vs-10-day-SMA deviation notes, and the `## What would change rules.json` per-hypothesis statement.
- Fetched SPY daily bars 2022-01-01..2026-08-18 via `bot.scanner.fetcher._download_batch` (project's shared yfinance kernel, not a raw `yf.download`), normalized via `get_ticker_frame`, cached to `backtester/cache/SPY_1d_regime.csv` (1,159 rows, 2022-01-03..2026-08-17), and recorded full provenance (source, range, actual dates, row count, sha256) in the doc's `## Regime series provenance` section.
- Committed doc + arms.json + both package markers in exactly ONE commit (`f8439aa`), proven by `git log --diff-filter=A` and confirmed `backtester/results/experimental/` did not exist at commit time.

## Task Commits

Per the plan's own explicit instruction (Task 3 action + threat T-10-01), Tasks 1 and 2 were executed and independently verified but intentionally left **uncommitted** — the plan requires the doc and arms.json to land in a single atomic commit so git history proves pre-registration precedes any real-data run artifact. This is not a deviation from GSD's normal per-task commit protocol; it is what this specific plan's falsifiability discipline requires.

1. **Task 1: Freeze the 15-arm definition list** — verified (arms.json loads, 15 unique arm names, `ext2_uncapped` present, `combo` absent); left uncommitted pending Task 3.
2. **Task 2: Write the pre-registration doc** — verified (all H1-H8 headings + all 12 sections present, 24-symbol universe verbatim, window C = 2024-09-02, all 15 arm names cross-referenced from arms.json); left uncommitted pending Task 3.
3. **Task 3: Fetch SPY regime series + commit** — `f8439aa` (docs)

**Plan metadata:** committed separately below (final metadata commit).

## Files Created/Modified

- `docs/research/2026-08-18-external-strategies-hypotheses.md` - 14-section pre-registration protocol, H1-H8, arm table, strategy definitions + deviations, limitations, regime-series provenance
- `backtester/experimental/arms.json` - frozen 15-arm machine-readable definition list
- `backtester/experimental/__init__.py` - empty package marker
- `tests/backtester/experimental/__init__.py` - empty test-package marker
- `backtester/cache/SPY_1d_regime.csv` (gitignored) - SPY daily OHLCV, 1,159 rows, 2022-01-03..2026-08-17, sha256 `45873a7227101ed15224ae20c89a2fa90bac873df6f73d9256b7d911c06fc38e`

## Decisions Made

- Single combined commit for doc + arms.json (plan-mandated, see Task Commits note above) — the only way to make the `git log --diff-filter=A` pre-registration proof meaningful.
- `diagnostic` field lives at the arm level (`arms[].diagnostic`) rather than nested in `params`, matching the plan's "Artifacts this phase produces" schema statement `arms[].{name,strategy,params,diagnostic}`.
- Added a non-required `purpose` string to every arm so the doc's arm table is a direct, driftless reproduction of `arms.json` rather than independently authored prose.

## Deviations from Plan

None - plan executed exactly as written. (The uncommitted-until-Task-3 sequencing described above is the plan's own explicit instruction, not an executor deviation.)

## Issues Encountered

None. The yfinance fetch via `_download_batch` succeeded on the first attempt with zero failed symbols; no retries or fallback paths were exercised.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `backtester/experimental/arms.json` is the frozen, machine-readable source of truth Wave 2/3 plans (10-02..10-05) will consume via `--arms`.
- `backtester/experimental/__init__.py` and `tests/backtester/experimental/__init__.py` exist so the two parallel Wave-2 plans (10-02, 10-03) can add modules/tests to the same package without a race on package-marker creation.
- `backtester/cache/SPY_1d_regime.csv` is ready for the `weekly_regime()` indicator function that 10-02/10-03 will implement.
- Pre-registration commit `f8439aa` is on record before any file exists under `backtester/results/experimental/` — the falsifiability gate for the rest of the phase is now closed and provable.
- Full test suite green (1039 passed, 1 skipped) after this plan; the Phase 9 `warm-cache-pool` background process (pid confirmed alive) was not disturbed.

---
*Phase: 10-external-strategy-research*
*Completed: 2026-08-18*

## Self-Check: PASSED

All 5 created files found on disk; commit `f8439aa` found in `git log --oneline --all`.
