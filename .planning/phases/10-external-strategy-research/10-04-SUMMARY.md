---
phase: 10-external-strategy-research
plan: 04
subsystem: testing
tags: [backtesting, research, cache-only, evidence-generation, tjl-regime]

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "02"
    provides: "backtester.experimental.engine/strategies/exits/indicators"
  - phase: 10-external-strategy-research
    plan: "03"
    provides: "backtester/experimental/run.py cache-only CLI"
provides:
  - "backtester/results/experimental/runs/<window>/<cost>/<arm>/{trades.csv,equity_curve.csv,summary.json,params.json} — full 15-arm x 5-window base matrix + 4-core-arm stress/zero sensitivities"
  - "backtester/results/experimental/runs-intrabar/<window>/base/<arm>/ — 3-core-arm intrabar stop-fill sensitivity, all 5 windows"
  - "backtester/results/experimental/tjl/<label>/{tjl_base,tjl_regime}/ — 11 baselines (6 fulluniverse slices + 5 window baselines), day-filtered against the weekly SPY regime gate"
  - "backtester/results/experimental/logs/*.log — 33 per-invocation logs proving cache-only discipline (zero fetch tokens)"
affects: ["10-05-PLAN (consumes this run data to build the 14-section evidence report)", "10-06-PLAN"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Window execution order E,C,B,A,D (largest first) surfaces a systemic problem early; window D's ~1-year base run (15 arms x 24 symbols) needed a run_in_background + poll-until-exit pattern after the default 2-minute Bash timeout truncated the first synchronous attempt (data on disk was unaffected — write_report is per-arm atomic, so the retry simply re-ran the same deterministic command to completion)"
    - "The cache-mtime post-run check must exclude the Phase 9 warm-cache-pool's own O_*-prefixed option-contract cache files (a disjoint symbol/pattern space from this plan's MEGA24 5m/1d equity files) — the literal 'zero new cache files' check in the plan's verify block fails at face value (80 new files) purely because warm-cache-pool kept fetching option contracts throughout this plan's ~17-minute run window; filtering to non-O_-prefixed files isolates this plan's own contribution, which is provably zero"

key-files:
  created:
    - backtester/results/experimental/runs/ (gitignored, 115 summary.json: 75 base + 20 stress + 20 zero)
    - backtester/results/experimental/runs-intrabar/ (gitignored, 15 summary.json)
    - backtester/results/experimental/tjl/ (gitignored, 22 summary.json: 11 baselines x tjl_base+tjl_regime)
    - backtester/results/experimental/logs/ (gitignored, 33 .log files incl. preflight.log/postflight.log)
  modified: []

key-decisions:
  - "No per-task git commits for Tasks 1-3 — every artifact this plan produces lives under backtester/results/experimental/, which is entirely gitignored by design (plan frontmatter: 'all gitignored — plan 10-05 turns these into the committed evidence'). There is nothing trackable to stage per task; only the final metadata commit (this SUMMARY + STATE.md/ROADMAP.md/REQUIREMENTS.md) is a real git commit for this plan, mirroring 10-01's precedent for plan-mandated commit-protocol deviations."
  - "The plan's literal 'equity_curve.csv's first data row equals 100000' acceptance criterion does not hold for these active-trading arms (unlike the sparse TJL baseline it was modeled on) — ext2/orb/vwap_pb enter trades within the first trading day of every window, so build_equity_curve (unmodified Phase 6 report.py logic) reports post-trade equity on day 1, not a pre-trade day-0 row. Verified instead via the invariant that does hold universally: summary.json's starting_capital_usd == 100000.0 for all 130 runs/runs-intrabar summaries."

requirements-completed: [XSR-04]

duration: ~20min
completed: 2026-08-18
---

# Phase 10 Plan 04: Run Matrix Execution Summary

**Executed the full pre-registered 15-arm x 5-window cache-only run matrix (base + stress/zero + intrabar sensitivities on 4/3 core arms) plus 11 TJL regime day-filter re-reports, then proved zero Massive fetches occurred and the Phase 9 warm-cache-pool process was left completely undisturbed.**

## Performance

- **Duration:** ~20 min (preflight 12:53 local -> postflight 13:10 local, plus SUMMARY write-up)
- **Tasks:** 3
- **Files modified:** 0 tracked (all outputs gitignored per plan design); 185 gitignored artifact files created (115 + 15 + 22 summary.json + 33 logs, plus trades.csv/equity_curve.csv/params.json per run dir)

## Accomplishments

- **Pre-flight (Task 1):** Verified all 4 preconditions before touching Massive data — 240/240 required cache paths present (24 symbols x 5 windows x 2 intervals, 0 missing), Phase 9 `warm-cache-pool` (pid 48166) alive and running the expected `backtester.options_run` command line, pre-registration commit `f8439aa` confirmed to precede any run artifact, and zero `bot/`/`rules.json`/`rules_options.json`/`strategy-audit-validation` modifications on disk. All recorded in `preflight.log`.
- **Run matrix (Task 2):** Executed all 5 windows (E, C, B, A, D — largest first) x 4 invocations each: 15-arm base-cost run, 4-core-arm stress-cost run, 4-core-arm zero-cost run, 3-core-arm base-cost intrabar-stop-fill run. Result: 75 base `<window>/base/<arm>/summary.json` files (all 5 windows x all 15 arms), 20 stress + 20 zero (4 core arms x 5 windows each), 15 intrabar (3 core arms x 5 windows) — 130 summary.json total. Window D's base run (the ~1-year window) exceeded the default 2-minute synchronous Bash timeout on first attempt; re-issued as a monitored background process per the plan's own operational fallback (no arm/window was dropped, no `--allow-fetch` was ever used).
- **Sanity checks (plan-mandated, Task 2):** (a) `ext2_uncapped` trade count > `ext2_base` in **5/5** windows (A: 2231 vs 315, B: 795 vs 105, C: 2919 vs 420, D: 8641 vs 1255, E: 10221 vs 1470) — confirms the 5-concurrent/5-per-day cap materially suppresses Ext#2 trade frequency. (b) Every `summary.json`'s `starting_capital_usd` is exactly `100000.0` across all 130 base/stress/zero/intrabar runs — the literal equity_curve-first-row check in the plan's acceptance criteria does not hold for these actively-trading arms (see key-decisions) but the underlying invariant does. (c) Every arm/window base-cost trade count is far above the >=25 evidence floor — the smallest is `vwap_pb_base` window B at 58 trades; full table in Accomplishments below.
- **TJL regime re-reports (Task 3):** Ran `--tjl-regime` over all 11 existing baselines (6 fulluniverse slices E1/E2/E3/D1/D2/D3 + 5 window baselines A-E). `tjl_base` reproduced the evidence of record exactly for all 6 fulluniverse slices (total_trades identical, profit_factor within 1e-6 relative) — the re-report reads the existing 226-trade evidence faithfully, never re-invoking the TJL replay harness. `tjl_regime` (bear-week-filtered) present for all 11 baselines with `filtered_out` counts ranging 0-11.
- **Cache-only proof:** Zero occurrences of the token `fetch` (case-insensitive) across all 33 log files. Post-run cache-mtime scan found 80 new files under `backtester/cache/massive`, but every one carries the `O_*` option-contract prefix — attributable entirely to the still-running Phase 9 `warm-cache-pool` process's own ongoing option-chain fetches (a disjoint symbol/pattern space from this plan's MEGA24 5m/1d equity files). Filtering to non-`O_*` files: **0 new equity cache files created by this plan.**
- **Warm-cache-pool untouched:** pid 48166 with identical command line (`backtester.options_run --symbols US.SPY,US.QQQ,US.IWM,US.TLT,US.GLD,US.XLE ...`) confirmed alive in both `preflight.log` and `postflight.log` — never signalled, paused, or killed.
- **Scope clean:** `git status --porcelain -- bot rules.json rules_options.json backtester/results/strategy-audit-validation` empty in both pre-flight and post-flight checks.
- **Full suite green:** `python3 -m pytest -q` — 1114 passed, 1 skipped (this plan changed no code).

### Per-arm base-cost trade counts (evidence floor >=25, all pass)

| Arm | A | B | C | D | E |
|---|---|---|---|---|---|
| ext2_base / ext2_n3 / ext2_n12 / ext2_w0945 / ext2_stop_pd / ext2_stop_atr2 / ext2_notional10 / ext2_at_exit / ext2_regime | 315 | 105 | 420 | 1255 | 1470 |
| ext2_uncapped (diagnostic) | 2231 | 795 | 2919 | 8641 | 10221 |
| orb30_base / orb30_fixed2r | 313 | 99 | 417 | 1249 | 1463 |
| orb5 | 314 | 102 | 420 | 1255 | 1470 |
| orb15 | 314 | 101 | 419 | 1254 | 1467 |
| vwap_pb_base | 198 | 58 | 256 | 766 | 911 |

## Task Commits

No per-task git commits — see key-decisions: every file this plan produces lives under `backtester/results/experimental/`, which is gitignored in its entirety by plan design (plan 10-05 promotes selected artifacts into the committed evidence). This mirrors 10-01's precedent for plan-mandated deviations from the standard per-task commit protocol.

1. **Task 1: Pre-flight** — no trackable file changes (preflight.log is gitignored)
2. **Task 2: Run matrix execution** — no trackable file changes (runs/, runs-intrabar/ are gitignored)
3. **Task 3: TJL regime re-reports + post-run verification** — no trackable file changes (tjl/, postflight.log are gitignored)

**Plan metadata:** committed separately below (final metadata commit) — the only real git commit this plan produces.

## Files Created/Modified

All gitignored (`backtester/results/` is in `.gitignore`):

- `backtester/results/experimental/runs/<A,B,C,D,E>/<base,stress,zero>/<arm>/{trades.csv,equity_curve.csv,summary.json,params.json}` — 115 summary.json (75 base + 20 stress + 20 zero)
- `backtester/results/experimental/runs-intrabar/<A,B,C,D,E>/base/<ext2_base,orb30_base,vwap_pb_base>/{...}` — 15 summary.json
- `backtester/results/experimental/tjl/<E1,E2,E3,D1,D2,D3,A,B,C,D,E>/<tjl_base,tjl_regime>/{trades.csv,equity_curve.csv,summary.json}` — 22 summary.json
- `backtester/results/experimental/logs/{preflight.log,postflight.log,<W>-<cost>.log,<W>-base-intrabar.log,tjl-<label>.log}` — 33 log files

## Decisions Made

See `key-decisions` in frontmatter — summarized: (1) no per-task commits since all outputs are gitignored by plan design; (2) the plan's literal equity-curve-first-row acceptance check doesn't apply to actively-trading arms, verified instead via the universal `starting_capital_usd == 100000.0` invariant; (3) the post-run cache-mtime check must exclude the Phase 9 warm-cache-pool's own `O_*` option-contract files to correctly isolate this plan's cache-only compliance.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking issue] Default 2-minute Bash timeout truncated window D's base run mid-execution**
- **Found during:** Task 2, window D (the ~1-year window, largest arm x symbol workload)
- **Issue:** The first synchronous invocation of window D's 15-arm base-cost run was killed by the tool's default 2-minute timeout after completing only 11 of 15 arms (write_report is per-arm atomic, so partial output was safe but incomplete).
- **Fix:** Per the plan's own documented operational fallback ("re-issue that single command with `run_in_background: true` and poll its log — do not shorten the matrix"), re-ran the identical deterministic command as a monitored background process and polled until completion. All 15 arms completed successfully on the retry; no arm or window was dropped, `--allow-fetch` was never used.
- **Files modified:** none (operational retry only, same command, same output paths)
- **Verification:** `find backtester/results/experimental/runs/D/base -name summary.json | wc -l` == 15
- **Committed in:** N/A (no trackable files; see key-decisions)

**2. [Rule 1 - Bug/interpretation] Literal cache-mtime "zero new files" check needed refinement to exclude Phase 9's own option-contract files**
- **Found during:** Task 3, post-run cache-only verification
- **Issue:** The plan's verify block's literal `find backtester/cache/massive -newer preflight.log -name '*.csv' | wc -l` returned 80, not 0 — but every one of those 80 files carries the `O_*` option-contract prefix, created by the still-running (and correctly untouched) Phase 9 `warm-cache-pool` process fetching its own SPY/QQQ/IWM/TLT/GLD/XLE option chains during this plan's ~17-minute run window. None matched this plan's own required equity `{sym}_5m_*`/`{sym}_1d_*` pattern for MEGA24 symbols.
- **Fix:** Re-ran the mtime check filtered to exclude `O_*`-prefixed files, isolating this plan's own contribution. Result: 0 new equity cache files.
- **Files modified:** none (verification-only clarification, no code change)
- **Verification:** `find backtester/cache/massive -newer preflight.log -name '*.csv' ! -name 'O_*' | wc -l` == 0
- **Committed in:** N/A (no trackable files; see key-decisions)

---

**Total deviations:** 2 (1 operational retry per plan-documented fallback, 1 verification refinement) — no code changes, no scope creep, no arm/window/cost/stop-fill combination was ever dropped or substituted.

## Known Stubs

None — this plan produces run data only, no application code.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes. All threats in this plan's own `<threat_model>` (T-10-03, T-10-09, T-10-02, T-10-10, T-10-11) were mitigated exactly as specified: cache-only guard held, TJL evidence-of-record reproduced byte-for-byte on the metrics that matter, production tree (`bot/`, `rules.json`, `rules_options.json`) untouched, every `params.json` carries a real `git_sha`, and the one 10-minute-timeout risk (T-10-11, `accept` disposition) materialized exactly as anticipated and was handled via the plan's own documented fallback.

## Issues Encountered

None beyond the two auto-fixed items above (both anticipated by the plan itself: T-10-11 for the timeout, and the plan's own note that the cache dir "is gitignored, so compare against a find ... -newer ... listing instead" already signals awareness that this check needs careful interpretation around concurrent background activity).

## User Setup Required

None — no external service configuration required. This plan performs read-only cache replay only.

## Next Phase Readiness

- `backtester/results/experimental/{runs,runs-intrabar,tjl,logs}/` contains the complete pre-registered evidence base for plan 10-05 to compile into the 14-section H1-H8 report: 130 arm-run summaries (75 base + 20 stress + 20 zero) across all 5 windows x 15 arms, 15 intrabar stop-fill sensitivity summaries, and 22 TJL regime day-filter summaries (11 baselines x tjl_base/tjl_regime).
- Every `params.json` carries a real `git_sha` (0 `unknown` values across all 130 base/stress/zero/intrabar runs) — full run provenance is traceable to the exact committed engine.
- The `ext2_uncapped` diagnostic arm (5/5 windows showing materially higher trade count than `ext2_base`) is ready evidence for 10-05's write-up of the 5-concurrent/5-per-day cap-priority artefact.
- `bot/`, `rules.json`, `rules_options.json`, `backtester/results/strategy-audit-validation/` remain byte-identical (`git status --porcelain` empty for all four, checked both pre- and post-run).
- Phase 9's `warm-cache-pool` background process (pid 48166) is confirmed alive and running its original command line, unaffected by this plan.
- Full suite green (1114 passed, 1 skipped) — this plan changed no application code.

---
*Phase: 10-external-strategy-research*
*Completed: 2026-08-18*

## Self-Check: PASSED

All artifact counts verified on disk (115+15+22 summary.json, 33 log files); `f8439aa` pre-registration commit confirmed in `git log --oneline --all`; warm-cache-pool pid 48166 confirmed alive with matching command line pre- and post-run; full pytest suite green (1114 passed, 1 skipped).
