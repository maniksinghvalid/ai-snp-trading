---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 01
subsystem: research-provenance
tags: [research, ibs, mean-reversion, provenance, backtest]
requires: []
provides:
  - "backtester/experimental/ibs_search/ six research scripts (reference implementation of the IBS rule)"
  - "docs/research/2026-10-04-ibs-etf-strategy-search.md results doc + captured evidence assets"
  - "tests/ibs/test_research_provenance.py static provenance checks"
affects: [12-04 parity test (AST-extracts strategy_search.simulate)]
tech-stack:
  added: []
  patterns: ["scripts resolve ROOT/HERE from __file__; outputs to gitignored backtester/results/ibs_search/"]
key-files:
  created:
    - backtester/experimental/ibs_search/strategy_search.py
    - backtester/experimental/ibs_search/strategy_search_r2.py
    - backtester/experimental/ibs_search/ibs_robust.py
    - backtester/experimental/ibs_search/r5_robust.py
    - backtester/experimental/ibs_search/ibs_sizing.py
    - backtester/experimental/ibs_search/screen_study.py
    - docs/research/2026-10-04-ibs-etf-strategy-search.md
    - docs/research/assets/2026-10-04-ibs-etf-strategy-search/ (8 files)
    - tests/ibs/test_research_provenance.py
  modified: []
key-decisions:
  - "HERE/ROOT in strategy_search.py defined BELOW the data marker so the exec()'d prelude (run with an empty namespace, no __file__) still works"
  - "Doc quotes authoring-session reference numbers and shows the 2026-10-04 re-run delta side by side (yfinance adjustment drift)"
requirements-completed: [IBS-10]
duration: ~25 min
completed: 2026-10-04
---

# Phase 12 Plan 01: Research provenance Summary

Six IBS research scripts committed with only path constants changed, plus a results doc (two pre-registered rounds, robustness, sizing, screen study, limitations) built from captured script output.

## Tasks

| Task | Commit | Notes |
|------|--------|-------|
| 1 Scripts moved + static tests | 7a432e8 | 3 tests |
| 2 Evidence + results doc + doc tests | 66773fa | 5 tests total |

## Verification

- `python3 -m pytest tests/ibs -q`: 5 passed. Full suite: 1406 passed / 1 skipped (baseline 1401 + 5).
- All five network scripts re-run successfully on 2026-10-04 (exit 0); `screen_study.py` not re-run (needs ~14k cached Massive 5m files) — its 2026-10-03 CSVs were copied unchanged.
- The re-run differs from the authoring-session reference only by yfinance drift (S3 OOS close: 3162 vs 3164 trades, PF 1.62 vs 1.63, CAGR 16.45 vs 16.48); both are shown in doc Section 9. Reference numbers from CONTEXT are quoted verbatim.

## Deviations from Plan

**1. [Rule 1 - Bug] HERE/ROOT placed below the prelude data marker in strategy_search.py**
- **Issue:** The plan said to replace the ROOT line (above the marker) with a `__file__`-based definition. Dependents (`strategy_search_r2`, `ibs_robust`, `r5_robust`, `ibs_sizing`) `exec()` the above-marker prelude into an empty namespace where `__file__` is undefined, which would raise NameError.
- **Fix:** Removed the ROOT line above the marker and defined `HERE`/`ROOT` just below it (ROOT is only used below the marker). `import os` added above it.
- **Files:** backtester/experimental/ibs_search/strategy_search.py. **Commit:** 7a432e8. The plan's diff-filter acceptance check still passes (empty diff for all six).

**2. Test file authored in two stages** — Task 1 commit contains the 3 static tests only; the doc/asset tests were added in Task 2 as planned.

## Notes

- Doc Section 8 funnel/rank statistics (mean 3.87 candidates/session etc.) come from the session evidence file, not from the committed CSVs; the doc says so.
- `timeout` is unavailable on macOS; scripts were run without it.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED

Files and commits (7a432e8, 66773fa) verified present; `git status --porcelain data/ backtester/results/` empty.
