---
phase: 10-external-strategy-research
plan: 03
subsystem: testing
tags: [backtesting, research, cli, cache-only, argparse]

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "01"
    provides: "backtester/experimental/arms.json (frozen 15-arm defaults), docs/research/2026-08-18-external-strategies-hypotheses.md"
  - phase: 10-external-strategy-research
    plan: "02"
    provides: "backtester.experimental.engine.{Engine,build_frame,group_by_day}, backtester.experimental.strategies.{ext2_signals,orb_signals,vwap_pb_signals}, backtester.experimental.indicators.{weekly_regime,regime_for_day}, backtester.report.write_report(extra_fields=...)"
provides:
  - "backtester/experimental/run.py — cache-only CLI: WINDOWS/MEGA24/COST_PROFILES tables, build_arg_parser, main; window resolution + hard cache-presence guard; per-window arm loop (one shared feed, one shared group_by_day pass); --tjl-regime day-filter mode"
  - "tests/backtester/experimental/test_run.py — 27 CLI-level tests, zero static top-level import of backtester.experimental.{engine,strategies,indicators}"
affects: ["10-04-PLAN (consumes run.py to execute the actual arm matrix)", "10-05-PLAN", "10-06-PLAN"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two named seams (_build_engine, _load_regime_labels) plus additional ad-hoc function-local imports inside _run_arms/_run_tjl_regime for build_frame/group_by_day/strategy signal fns/regime_for_day — all reach backtester.experimental.{engine,strategies,indicators} only at call time, never at module import time (parallelism contract with 10-02 landing in the same wave)"
    - "Tests patch function-local imports at their SOURCE module (e.g. monkeypatch.setattr(\"backtester.experimental.engine.group_by_day\", fake)) rather than at run.py's own namespace, since a `from X import Y` executed inside a function body re-resolves X.Y fresh at call time and picks up the patched callable"
    - "Fail-closed data source: MassiveDataSource(api_key=\"\") constructed whenever --allow-fetch is absent, so a cache-guard miss (bug, race, or future symbol not covered by _required_cache_paths) 401s instead of silently pulling data — second line of defence behind the guard itself"

key-files:
  created:
    - backtester/experimental/run.py
    - tests/backtester/experimental/test_run.py

key-decisions:
  - "Frame cache in _run_arms keyed by code alone (not a params signature) — build_frame(feed, code, params) verified to not consume `params` in its current 10-02 implementation, so code-only caching gives every arm maximal reuse (one build_frame call per code per window, not per arm) while staying provably correct; documented inline as the reason, not a params-signature dict as the plan prose loosely suggested"
  - "--tjl-regime validated to require --baseline via an early explicit check in main() (before the try/except block), since the mode's own argparse flags (--window/--start/--end/--arms) are irrelevant and skipped entirely — the early return happens before any window resolution or cache guard runs"
  - "window_label in <out>/<window>/<cost>/<arm>/ falls back to f'{start}_{end}' when the run used explicit --start/--end instead of --window (not specified verbatim by the plan, but required for the output-path template to have a value in both modes)"

requirements-completed: [XSR-02, XSR-04]

duration: ~25min
completed: 2026-08-18
---

# Phase 10 Plan 03: Cache-only CLI (run.py) Summary

**Built `backtester/experimental/run.py`: a locked five-window/24-symbol/three-cost-profile CLI that refuses to run on any missing Massive cache file, replays every selected arm against one shared feed and one shared day-grouping pass, and re-reports the existing 226-trade TJL baseline's bear-day-filtered variant without ever invoking the TJL harness.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-08-18T19:33:00Z (approx, session start)
- **Completed:** 2026-08-18T19:49:00Z
- **Tasks:** 3
- **Files modified:** 2 (both created)

## Accomplishments

- `WINDOWS`/`MEGA24`/`COST_PROFILES` locked as the single source of truth (window C = `2024-09-02`..`2024-12-31`, verified against the real committed `backtester/cache/massive/AAPL_5m_2024-08-03_2024-12-31.csv` filename).
- `_required_cache_paths` derives the exact padded Massive cache keys `SimulatedBarFeed._load_massive` will read (never a hand-typed padded literal), and `_assert_cache` refuses to proceed — printing every missing path — unless `--allow-fetch` is passed (never used by this phase's run matrix). A fail-closed `MassiveDataSource(api_key="")` is the second line of defence when `--allow-fetch` is absent.
- `_run_arms` constructs exactly ONE `SimulatedBarFeed` and calls `group_by_day` exactly once per window run (verified by call-count test), shared by every selected arm; each arm writes `trades.csv`/`equity_curve.csv`/`summary.json`/`params.json` under `<out>/<window>/<cost>/<arm>/`, with `trades.csv`'s header extended by `side,strategy,arm,n_legs,regime` after the frozen 8 base columns.
- `_run_tjl_regime` reads the existing TJL baseline's `trades.csv` read-only, re-reports it unfiltered into `tjl_base/` (parity check), and writes the bear-day-filtered remainder into `tjl_regime/` via `regime_for_day(labels, opened_at[:10])` — string slicing only (`opened_at` carries a `-04:00`/`-05:00` offset suffix), never a `datetime.fromisoformat` parse. Zero `BacktestHarness` references anywhere in the file.
- Every `backtester.experimental.{engine,strategies,indicators}` reference in `run.py` is a function-local import (`grep -n '^from backtester.experimental'` returns nothing) — this file stays importable independently of plan 10-02's package, satisfying the same-wave parallelism contract even though 10-02 had already landed by the time this plan executed.

## Task Commits

Each task was committed atomically:

1. **Task 1: run.py skeleton — WINDOWS/MEGA24/cost profiles, arg parser, cache-presence guard** — `5cf8d24` (feat)
2. **Task 2: per-window arm loop — one feed, one day-grouping, per-arm reports + params.json** — `e7df277` (feat)
3. **Task 3: --tjl-regime mode — day-filter the existing TJL baseline, never re-run the harness** — `53035ce` (feat)

**Plan metadata:** committed separately below (final metadata commit).

## Files Created/Modified

- `backtester/experimental/run.py` (470 lines) - `WINDOWS`/`MEGA24`/`COST_PROFILES`, `build_arg_parser`, `main`, `_resolve_window`, `_required_cache_paths`, `_assert_cache`, `_load_arms`, `_parse_set_overrides`, `_resolve_params`, `_git_sha`, `_build_engine`/`_load_regime_labels` seams, `_run_arms`, `_run_tjl_regime`, `_load_baseline_trades`
- `tests/backtester/experimental/test_run.py` (27 tests) - window/cache-guard unit tests, arm-loop integration tests (fake `_build_engine` + patched `group_by_day`/`build_frame`/strategy signal fns), `--tjl-regime` mode tests with a synthetic 3-trade baseline + canned regime labels

## Decisions Made

See `key-decisions` in frontmatter — summarized: frame cache in the arm loop is keyed by code alone (build_frame doesn't consume params in the current 10-02 implementation, verified by reading engine.py, so code-only caching is both correct and maximally efficient); `--tjl-regime` requires `--baseline` via an explicit early check before the try/except block covering the normal-mode arg validation; `window_label` falls back to `f"{start}_{end}"` when explicit `--start`/`--end` are used instead of `--window`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module docstring literal strings tripped the file's own threat-model greps**
- **Found during:** Task 1 and Task 3, running the new grep-based self-tests
- **Issue:** The module docstring's own prose used the words "StateStore" and "BacktestHarness" (describing what this module deliberately does NOT do), which made `grep -c 'MoomooGateway\|StateStore\|place_order'` and `grep -c 'BacktestHarness'` return 1 instead of 0 — the acceptance criteria's own literal-text greps don't distinguish code from comments.
- **Fix:** Reworded the docstring prose to describe the same invariants without using the literal banned tokens ("any live-bot persistence layer" instead of "a StateStore"; "the TJL replay harness" instead of "BacktestHarness").
- **Files modified:** `backtester/experimental/run.py`
- **Verification:** `grep -c 'MoomooGateway\|StateStore\|place_order'` and `grep -c 'BacktestHarness'` both return `0`; full test suite green.
- **Committed in:** `5cf8d24` (Task 1) and `53035ce` (Task 3)

---

**Total deviations:** 1 auto-fixed (1 bug, caught by the plan's own literal-text acceptance greps before commit)
**Impact on plan:** Cosmetic docstring wording only — no behavioral change. No scope creep.

## Issues Encountered

None beyond the one auto-fixed docstring wording issue above (caught immediately by the plan's own grep-based tests before any commit).

## User Setup Required

None - no external service configuration required (pure cache reads, zero network in every test).

## Next Phase Readiness

- `backtester.experimental.run.main(argv)` is ready for 10-04/10-05 to invoke against real windows/arms/cost profiles once those plans define the actual run matrix and evidence-collection scripts.
- `python3 -m backtester.experimental.run --window C --symbols US.NOSUCHSYM --arms backtester/experimental/arms.json --out /tmp/x` verified live: exits 1, lists both missing padded cache paths, constructs no `MassiveDataSource`.
- The real `backtester/cache/massive/AAPL_5m_2024-08-03_2024-12-31.csv` (Phase 9 warm-cache-pool artifact) already covers window C for at least AAPL — 10-04/10-05 should confirm full MEGA24 coverage for every window before their first real (non-`--allow-fetch`) run.
- `bot/`, `rules.json`, `rules_options.json`, `backtester/results/` remain untouched (`git status --porcelain` empty for all four).
- Full suite green (1114 passed, 1 skipped) after all three tasks; no network egress in any new test.

---
*Phase: 10-external-strategy-research*
*Completed: 2026-08-18*

## Self-Check: PASSED

Both created files found on disk; all 3 task commits (`5cf8d24`, `e7df277`, `53035ce`) found in `git log --oneline --all`.
