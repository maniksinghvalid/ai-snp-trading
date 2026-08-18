---
phase: 10-external-strategy-research
plan: 05
subsystem: testing
tags: [backtesting, research, aggregation, evidence, hypothesis-testing, svg-charts]

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "04"
    provides: "backtester/results/experimental/{runs,runs-intrabar,tjl}/ — the full run matrix + TJL regime re-reports"
provides:
  - "backtester/experimental/aggregate.py — SLICES/IS_SLICES/OOS_SLICES, aggregate(), bootstrap_ci(), walk_forward(), main()"
  - "backtester/experimental/charts.py — equity_svg(), bars_svg(), main()"
  - "docs/research/assets/2026-08-18-external-strategies/{results.csv,results.md,results-intrabar.csv,equity_curves.svg,pf_by_slice.svg} — committed durable evidence"
  - "docs/research/2026-08-18-external-strategies-results.md — 14-section report with the H1-H8 verdict table"
affects: ["10-06-PLAN (conditional productionization, gated on H8 == REJECTED here)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "aggregate.py recomputes every metric from raw trades.csv via backtester.report.compute_metrics per (arm, cost, slice) bucket — never trusts the original run's summary.json, so slice/pool boundaries can be redrawn without re-running the engine"
    - "Extended metrics (avg_trade_usd, expectancy_r, max_consecutive_losses, avg_hold_minutes, per-side split, bootstrap CI, IS/OOS PF ratio) live entirely in aggregate.py, never in report.py — report.py has zero diff from this plan"
    - "walk_forward() takes a pre-built {(arm, slice): row} dict and pre-declared sensitivity families — pure lookup/argmax, provably no engine re-run"
    - "bootstrap_ci() is numpy default_rng(seed)-seeded and vectorized (rng.choice over a (n, len(values)) array) — deterministic and fast enough to run per-row without a runtime concern"

key-files:
  created:
    - backtester/experimental/aggregate.py
    - backtester/experimental/charts.py
    - tests/backtester/experimental/test_aggregate.py
    - tests/backtester/experimental/test_charts.py
    - docs/research/assets/2026-08-18-external-strategies/results.csv
    - docs/research/assets/2026-08-18-external-strategies/results.md
    - docs/research/assets/2026-08-18-external-strategies/results-intrabar.csv
    - docs/research/assets/2026-08-18-external-strategies/equity_curves.svg
    - docs/research/assets/2026-08-18-external-strategies/pf_by_slice.svg
    - docs/research/2026-08-18-external-strategies-results.md
  modified: []

key-decisions:
  - "combo arm NOT appended to arms.json — the pre-registered rule (PF>1 in IS with >=25 trades for some entry family) never fired against the real numbers: the best of all 15 arms' IS profit factor is orb5 at 0.922 (1890 trades), i.e. every arm is below breakeven in-sample. This null result is recorded as the pre-registered outcome, not an omission (Task 2's own commit message states the exact numbers that failed the rule)."
  - "H7's evidence floor turned out to be MET (97 IS / 92 OOS TJL-comparator trades) contrary to the hypotheses doc's a-priori guess of INSUFFICIENT-EVIDENCE for trade-count reasons — the regime gate was tested for real and found to make TJL's IS PF worse (0.798 -> 0.586), so H7 is REJECTED on real evidence, not INSUFFICIENT-EVIDENCE."
  - "H5 (fixed-2R vs partial_be_trail for ORB) has no stated a-priori direction in the hypotheses doc, unlike H1-H4/H6-H8; verdicted REJECTED on the grounds that direction flips between IS (partial_be_trail wins) and OOS (fixed_2r wins) and neither ever exceeds PF 1.0 — no edge either way, explicitly called out as a deviation from the standard SUPPORTED/REJECTED framing in the results doc's verdict-table row."
  - "H8 REJECTED without ever constructing a combo arm: since no entry family cleared even PF>1 in IS (let alone the H8 bar's PF>=1.3), the production-candidate bar is unreachable by any combination this phase could build, so H8 is verdicted directly from the entry-family data rather than waiting on a combo run that was never triggered."
  - "aggregate.py's TJL comparator pools E1+E2+E3 as IS and D1+D2+D3 as OOS (a NARROWER split than the MEGA24 arms' IS_SLICES/OOS_SLICES, which also include C/B/A) — matches the hypotheses doc's explicit statement that H7's fulluniverse comparator uses only E1-E3/D1-D3, kept as a separate _TJL_IS_SLICES/_TJL_OOS_SLICES pair inside aggregate.py rather than overloading the module-level IS_SLICES/OOS_SLICES."

requirements-completed: [XSR-01, XSR-05]

duration: ~2h
completed: 2026-08-18
---

# Phase 10 Plan 05: Aggregate, Charts, Results Doc Summary

**Built the evidence layer (aggregate.py + charts.py), ran it against the full Wave-3 run matrix, confirmed the pre-registered `combo` rule never fires (every arm is below breakeven PF in-sample), and wrote the 14-section results doc verdicting all of H1-H8 — none SUPPORTED in both IS and OOS, recommendation is no production change.**

## Performance

- **Duration:** ~2 hours
- **Tasks:** 3
- **Files modified:** 11 (2 new modules, 2 new test files, 5 committed evidence artifacts, 1 results doc, 1 SUMMARY)

## Accomplishments

- **`backtester/experimental/aggregate.py`** (476 lines): frozen `SLICES`/`IS_SLICES`/
  `OOS_SLICES` tables matching the hypotheses doc exactly; `aggregate(root, tjl_root=None)`
  walks the Wave-3 run tree, buckets every trade into its 9-slice/IS/OOS/ALL bucket by
  `(opened_at or closed_at)[:10]`, and recomputes metrics per bucket via
  `backtester.report.compute_metrics` (never reimplemented) plus this module's own extras
  (avg trade $, expectancy R, max consecutive losses, avg hold minutes, per-side split,
  bootstrap CI on mean R, IS/OOS PF ratio); `bootstrap_ci()` is a deterministic seeded
  numpy resample; `walk_forward()` is a pure, re-run-free best-by-PF lookup over the 9
  slices in chronological order for 3 pre-declared sensitivity families; `main()` writes
  `results.csv` (inf-safe) and `results.md` (4 readable tables: per-arm IS/OOS summary,
  per-slice PF matrix, walk-forward, TJL comparator). 8 new tests cover every `<behavior>`
  bullet, including the PF-1.2/day-slice-PF-2.0/max-consecutive-losses-1 hand-computed
  assertions on `make_trade_log` (via a monkeypatched `SLICES` since the fixture's dates
  are always "recent," never inside the real 2023-2026 ranges) and an infinite-PF case
  that never raises and prints the literal `inf` in the MD output.
- **`backtester/experimental/charts.py`** (213 lines): dependency-free SVG —
  `equity_svg(series_by_label, title)` (one polyline per labelled equity curve, legend,
  handles empty/flat series without dividing by zero), `bars_svg(values_by_group, title,
  hline=1.0)` (grouped PF bars per slice per arm with a dashed `class="hline"` reference
  line, infinite PF clipped to the chart top rather than crashing the layout), `main()`
  (reads a run-tree root for core-arm equity curves + an aggregate `results.csv` for the
  PF bars, writes both SVGs). 5 new tests.
- **Ran the real pipeline** against the committed Wave-3 evidence: `aggregate.py` against
  `runs/` (with `--tjl-root runs/../tjl`) and separately against `runs-intrabar/`;
  `charts.py` against `runs/` + the aggregate `results.csv`. Copied `results.csv`,
  `results.md`, `results-intrabar.csv`, `equity_curves.svg`, `pf_by_slice.svg` into
  `docs/research/assets/2026-08-18-external-strategies/` (durable evidence — the source
  run directories under `backtester/results/` stay gitignored).
- **`combo` rule evaluated against real IS numbers and NOT triggered:** every one of the
  15 arms' IS profit factor is below 1.0 (`ext2` family max 0.907 at `ext2_n12`, `orb`
  family max 0.922 at `orb5`, `vwap_pb_base` 0.897) — the pre-registered threshold (PF>1
  in IS with >=25 trades) is never met by any entry family, so no `combo` arm was appended
  to `arms.json`. This null result is recorded (Task 2's commit message) with the exact
  numbers that failed the rule, per the plan's own instruction that absence-of-combo is a
  valid, pre-registered outcome.
- **`docs/research/2026-08-18-external-strategies-results.md`** (471 lines, 14 numbered
  sections + a `## Verdict table`): every H1-H8 hypothesis verdicted strictly by the
  pre-registered rules, every cited number sourced from
  `docs/research/assets/2026-08-18-external-strategies/results.csv`. H1/H4/H6 REJECTED
  (no entry family has edge); H2 SUPPORTED (`partial_be_trail` beats `pct_ladder` on
  Ext#2's own entries, PF+Sortino agree in both IS and OOS); H3/H7 REJECTED (weekly SPY
  regime gate does not improve Ext#2 or TJL — H7's evidence floor was in fact met, 97 IS /
  92 OOS trades, contrary to the a-priori INSUFFICIENT-EVIDENCE guess); H5 REJECTED (no
  stated a-priori direction; fixed-2R vs `partial_be_trail` flips direction IS->OOS, no
  robust winner either way); H8 REJECTED (no arm, combined or otherwise, is within reach
  of the PF>=1.3 production bar). TJL's evidence of record (226 trades, -0.019R/trade, PF
  1.02/0.59/0.97/0.50/1.08/1.32 across E1,E2,E3,D1,D2,D3) cited verbatim and confirmed
  reproduced exactly by this phase's `tjl_base` re-report. Both required verbatim
  statements present: the 1H-EMA100-redundancy line and the "`rules.json`/
  `rules_options.json` were not modified by this phase" line.
- **Full suite green:** 1127 passed, 1 skipped (up from 1114 baseline; +13 new tests, 0
  regressions). `git status --porcelain -- rules.json rules_options.json bot` empty at
  every checkpoint.

## Task Commits

1. **Task 1: aggregate.py + tests** — `56a20b9`
2. **Task 2: charts.py + committed evidence + combo-rule decision** — `1f17b44`
3. **Task 3: 14-section results doc with H1-H8 verdicts** — `e72012b`

**Plan metadata:** committed separately below (final metadata commit).

## Files Created/Modified

- `backtester/experimental/aggregate.py` — SLICES/IS_SLICES/OOS_SLICES, `aggregate()`,
  `bootstrap_ci()`, `walk_forward()`, `main()`; report.py extras (avg trade $, expectancy
  R, max consecutive losses, avg hold minutes, per-side split, bootstrap CI, IS/OOS PF
  ratio)
- `backtester/experimental/charts.py` — `equity_svg()`, `bars_svg()`, `main()`
- `tests/backtester/experimental/test_aggregate.py` — 8 tests
- `tests/backtester/experimental/test_charts.py` — 5 tests
- `docs/research/assets/2026-08-18-external-strategies/{results.csv,results.md,results-intrabar.csv,equity_curves.svg,pf_by_slice.svg}` — committed durable evidence (302 CSV rows: every arm x cost x slice + IS/OOS/ALL pools)
- `docs/research/2026-08-18-external-strategies-results.md` — the 14-section report with the H1-H8 verdict table

## Decisions Made

See `key-decisions` in frontmatter — summarized: (1) `combo` rule not met, no arm
appended, numbers recorded; (2) H7's floor was actually met (real data beat the a-priori
guess, still REJECTED on the merits); (3) H5 has no stated a-priori direction, verdicted
REJECTED as "no edge either way, direction flips IS->OOS"; (4) H8 REJECTED directly from
entry-family data since no `combo` run was ever triggered; (5) TJL comparator IS/OOS pools
(E1-E3/D1-D3) kept distinct from the MEGA24 arms' IS_SLICES/OOS_SLICES (which also include
C/B/A) per the hypotheses doc's explicit narrower H7 definition.

## Deviations from Plan

None — plan executed exactly as written, including the conditional `combo` rule (evaluated
against real data and correctly not triggered) and the plan's own instruction that this
null result is a valid, pre-registered outcome rather than an omission.

## Known Stubs

None — this plan produces analysis code, committed evidence artifacts, and a results
document; no application code, no UI, no stubbed data paths.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes. All
threats in this plan's own `<threat_model>` (T-10-12, T-10-13, T-10-02, T-10-14, T-10-05,
T-10-SC) were mitigated exactly as specified: verdicts applied strictly by the
pre-registered rules with every table number sourced from the committed `results.csv`; the
`combo` arm's absence is recorded with its triggering (failing) numbers, not silently
omitted; evidence copied under `docs/research/assets/` before `backtester/results/`'s
gitignore could hide it; `inf`/NaN formatting tested explicitly and never crashes;
`backtester/report.py` has zero diff from this plan (`git diff --stat backtester/report.py`
empty); zero new dependencies (bootstrap CI is numpy `default_rng`, charts are hand-rolled
SVG).

## Issues Encountered

None. All three tasks' automated verification blocks passed on the first attempt after the
one grep-hygiene fix documented below.

### Auto-fixed Issues

**1. [Rule 1 - Bug] `aggregate.py`'s own docstring contained the literal string
"fromisoformat," failing its own acceptance grep**
- **Found during:** Task 1, acceptance-criteria verification
- **Issue:** The module docstring explained the no-look-ahead date-slicing convention by
  naming the forbidden method (`datetime.fromisoformat`) for clarity — but the acceptance
  check `grep -c 'fromisoformat' backtester/experimental/aggregate.py` requires 0
  occurrences of the literal substring anywhere in the file, including comments/docstrings.
- **Fix:** Reworded the docstring to describe the same constraint ("never an ISO-datetime
  full-string parse") without using the literal forbidden token.
- **Files modified:** `backtester/experimental/aggregate.py` (docstring only, no logic
  change)
- **Verification:** `grep -c 'fromisoformat' backtester/experimental/aggregate.py` == 0;
  full test suite re-run green.
- **Committed in:** `56a20b9` (folded into Task 1's own commit — caught before the
  original commit, not a follow-up fix)

## User Setup Required

None — no external service configuration required. This plan performed offline aggregation
and report-writing only; the one live command sequence (aggregate/charts CLIs) reads
already-cached Wave-3 run data with zero network calls.

## Next Phase Readiness

- `docs/research/2026-08-18-external-strategies-results.md` states plainly that H8 is
  REJECTED and no arm is SUPPORTED in both IS and OOS — plan 10-06 (conditional
  productionization) is gated on this and should document the "no production change"
  outcome rather than build a feature branch.
- `docs/research/assets/2026-08-18-external-strategies/results.csv` is the durable,
  committed source of every number a future plan might want to re-check or extend without
  re-running the Wave-3 matrix.
- `backtester/experimental/aggregate.py`/`charts.py` are general-purpose (any future
  `arms.json` addition or new window would flow through the same pipeline without code
  changes) if a further research pass is ever pre-registered.
- `rules.json`, `rules_options.json`, `bot/` remain byte-identical throughout this plan
  (checked after every task).
- Full suite green (1127 passed, 1 skipped).

---
*Phase: 10-external-strategy-research*
*Completed: 2026-08-18*

## Self-Check: PASSED

All 11 created files found on disk; commits `56a20b9`, `1f17b44`, `e72012b` confirmed in
`git log --oneline --all`; full pytest suite green (1127 passed, 1 skipped) after this
plan.
