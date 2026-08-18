---
phase: 10-external-strategy-research
verified: 2026-08-18T00:00:00Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
---

# Phase 10: External Strategy Research Verification Report

**Phase Goal:** Two Reddit-sourced day-trading strategies are critically extracted, their
credibility assessed, and their automatable cores backtested cache-only against Trend Join
Long (TJL, no-edge since 2026-08-13) and candidate improvements, with a pre-registered
hypothesis protocol, so any recommendation to change rules.json is evidence-gated rather than
default. Research-only: no production code or config changes as part of this phase.

**Verified:** 2026-08-18
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Both Reddit threads extracted into explicit rules, separated from assumptions, credibility assessed | ✓ VERIFIED | `docs/research/2026-08-18-external-strategies-hypotheses.md` §2 Sources and credibility (thread IDs, scores, OP disclaimers, folk-statistic critique); `docs/research/2026-08-18-external-strategies-results.md` §2/§3 reproduce the extraction + credibility assessment with pushback comment scores |
| 2 | `backtester/experimental/` implements the 3 automatable cores as pluggable, tested modules reusing `SimulatedBarFeed`/`report.py` conventions; `bot/`/`rules.json`/`rules_options.json` untouched | ✓ VERIFIED | `backtester/experimental/{indicators,strategies,exits,engine,run,aggregate,charts}.py` exist, import cleanly, 15 arms across ext2/orb/vwap_pb; `git status --porcelain -- bot rules.json rules_options.json` empty; `git diff --stat` on `f8439aa~..HEAD` for `backtester/report.py` shows 15-line surgical patch (< 20-line bar) |
| 3 | Hypotheses doc committed BEFORE any real-data run — git history proves ordering | ✓ VERIFIED | `git log --diff-filter=A -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` → single commit `f8439aa` (2026-08-18), predating all `backtester/results/experimental/` run artifacts per 10-04's own preflight/postflight log checks |
| 4 | All backtests run cache-only (zero new Massive requests) at realistic cost, standard+extended metrics across 5 windows/9 regime slices | ✓ VERIFIED | 10-04-SUMMARY: 130 base/stress/zero/intrabar summary.json + 22 TJL comparator summary.json produced; zero `fetch` tokens in 33 run logs; post-run cache-mtime scan isolated to Phase-9's own `O_*` option files (0 new MEGA24 equity cache files); warm-cache-pool pid/cmdline identical pre/post |
| 5 | Results doc reports every hypothesis H1-H8 as SUPPORTED/REJECTED/INSUFFICIENT-EVIDENCE with numbers, 14 sections; rules.json/rules_options.json unchanged regardless of verdict | ✓ VERIFIED | `docs/research/2026-08-18-external-strategies-results.md` has 14 numbered `##` sections + a `## Verdict table` with all 8 rows; spot-checked H1/H2 numbers against `results.csv` — exact match (`ext2_base` IS PF 0.89682… ≈ 0.897, `ext2_at_exit` IS PF 0.90725… ≈ 0.907, OOS 0.97466…/0.97950… ≈ 0.975/0.980); `git status --porcelain -- rules.json rules_options.json bot` empty |
| 6 | Full test suite green (`python3 -m pytest -q`) | ✓ VERIFIED | Ran live: `1127 passed, 1 skipped in 56.41s` |
| 7 | If any hypothesis SUPPORTED in both IS and OOS: production integration on unmerged feature branch (default-off, schema-valid, tests green) — OR operator-deferred with deferral explicitly recorded | ✓ VERIFIED | Gate mechanically TRIGGERED via H2 (10-06-SUMMARY.md, verified against the plan's own unmodified verify script); operator selected `defer` at the Task 2 checkpoint; no `feature/phase10-*` branch exists (`git branch -a` checked live); `bot/`/`rules.json`/`rules_options.json` untouched; deferral + future file/function list recorded in results doc §12/§13 |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `docs/research/2026-08-18-external-strategies-hypotheses.md` | Pre-registration: H1-H8, universe, windows, costs, evidence floor, verdict rules | ✓ VERIFIED | All 8 `### H` headings present; 12 required `##` sections present; evidence floor `>=25` present |
| `backtester/experimental/arms.json` | Frozen 15-arm list | ✓ VERIFIED | 15 unique arm names, `ext2_uncapped` present, no `combo` (never triggered — IS PF never exceeded 1.0) |
| `backtester/experimental/{indicators,strategies,exits,engine}.py` | Pure, tested strategy cores | ✓ VERIFIED | Exist, exported names match parallelism contract, unit-tested (prefix-invariance, short-sign, N+1 fills) per 10-02-SUMMARY |
| `backtester/experimental/run.py` | Cache-only CLI | ✓ VERIFIED | `WINDOWS`/`MEGA24`/`COST_PROFILES` present; cache-presence guard fail-closed; `--tjl-regime` mode present |
| `backtester/experimental/aggregate.py`, `charts.py` | Evidence aggregation + SVG charts | ✓ VERIFIED | Present, exports `SLICES`/`IS_SLICES`/`OOS_SLICES`/`aggregate`/`bootstrap_ci`/`walk_forward`; `equity_svg`/`bars_svg` present |
| `docs/research/assets/2026-08-18-external-strategies/{results.csv,results.md,results-intrabar.csv,*.svg}` | Committed durable evidence | ✓ VERIFIED | All files present on disk; `results.csv` contains `ext2_base`/`tjl_base` rows and pooled `IS`/`OOS` slices |
| `docs/research/2026-08-18-external-strategies-results.md` | 14-section report w/ verdict table | ✓ VERIFIED | 14 numbered sections confirmed via live grep; verdict table has all 8 hypothesis rows |
| `.planning/phases/10-external-strategy-research/10-06-SUMMARY.md` | Gate decision record | ✓ VERIFIED | Records TRIGGERED (not conflated with NOT-TRIGGERED), operator `defer` selection, Task 3 skipped explicitly |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `docs/.../hypotheses.md` | `backtester/experimental/arms.json` | arm table reproduced verbatim | ✓ WIRED | `ext2_base` and all 15 arm names present in doc |
| `backtester/experimental/engine.py` | `bot.strategy.indicators.swing_low_2_2` | import for trailing stop | ✓ WIRED | Confirmed via 10-02-SUMMARY grep (`from bot` count == 2) |
| `backtester/experimental/engine.py` | `bot.position.manager.get_force_close_time_et` | half-day-aware force-flat | ✓ WIRED | Same grep confirms the 2 permitted `bot` imports |
| `backtester/experimental/run.py` | `backtester/cache/massive/*.csv` | cache-presence guard | ✓ WIRED | `_required_cache_paths` derives real padded keys; live pre-flight found 240/240 paths present |
| `docs/.../results.md` | `docs/.../hypotheses.md` | verdict per pre-registered hypothesis | ✓ WIRED | All numbers in verdict table sourced from and spot-check-matched against `results.csv` |
| results doc §12/§13 gate framing | 10-06-SUMMARY.md gate mechanism | H2-not-H8 correction | ✓ WIRED | Both docs now consistently state "any hypothesis SUPPORTED in both IS/OOS" (not H8-specific), confirmed by direct read of both files |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| XSR-01 | 10-01, 10-05 | External strategies extracted + credibility assessed | ✓ SATISFIED | Hypotheses doc §2-3, results doc §2-3 |
| XSR-02 | 10-02, 10-03 | `backtester/experimental/` implements automatable cores, `bot`/`rules.json` untouched | ✓ SATISFIED | Modules present/tested; scope-clean git status |
| XSR-03 | 10-01 | Hypotheses pre-registered before real-data run | ✓ SATISFIED | Commit `f8439aa` precedes all run artifacts |
| XSR-04 | 10-03, 10-04 | Cache-only backtests, realistic costs, standard+extended metrics | ✓ SATISFIED | 130+22 summary.json produced, zero fetch tokens |
| XSR-05 | 10-05 | Every hypothesis verdicted with numbers; rules unchanged | ✓ SATISFIED | 14-section report, verdict table, scope-clean |
| XSR-06 | 10-06 | If SUPPORTED in both IS/OOS: production integration on feature branch, OR deferred with record | ✓ SATISFIED | Gate TRIGGERED via H2; operator deferred; deferral + file list recorded; no feature branch exists |

No orphaned requirements found — REQUIREMENTS.md Phase 10 traceability table lists exactly XSR-01..06, all mapped to plans in this phase. (Note: REQUIREMENTS.md's traceability table rows still say "Planned" for XSR-01..06 despite the checkbox list marking them `[x]` — a stale-prose inconsistency in the requirements doc itself, not a phase-goal gap. Non-blocking.)

### Anti-Patterns Found

None blocking. Searched all `backtester/experimental/*.py` and both `docs/research/2026-08-18-external-strategies-*.md` files for `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` — zero matches.

The independent code review (`10-REVIEW.md`, 2026-08-18) found 0 critical, 6 warnings, 3 info findings — all non-blocking:
- WR-01 (`vwap_pb_signals` cross-session leak in `.shift(1)`): confirmed dormant in all committed runs (masked by NaN opening-range gating + `entry_start=10:00` exclusion) — does not affect published numbers.
- WR-02 (entry evaluation not gated by force-close bar, relies on feed's same-session guard): correct today, flagged as missing defense-in-depth for future reuse.
- WR-03 (SVG x-axis alignment coincidental, not guaranteed): does not affect numeric evidence, only chart rendering robustness.
- WR-04/05/06: design-clarity nits (unused params, duplicated CSV coercion, silent no-op on missing regime_fn) — none corrupt results.

Reviewer explicitly states: "I found no BLOCKER-level defect that corrupts the published numbers." I independently spot-checked 2 verdict-table rows (H1, H2) against `results.csv` and both matched exactly, corroborating this finding.

### Orchestrator-Flagged Consistency Checks (explicit verification)

1. **Results doc internal consistency (H2 vs H8 framing):** VERIFIED. Read `docs/research/2026-08-18-external-strategies-results.md` in full at §1 (Executive summary), §12 (Recommended architecture), §13 (Roadmap), and the Verdict table. All four locations now consistently state the gate is "any hypothesis SUPPORTED in both IS and OOS" (not H8-specific), that H2 IS SUPPORTED, and that H8 itself is independently REJECTED. No lingering "nothing is SUPPORTED" contradiction found anywhere in the document.
2. **10-06-SUMMARY.md TRIGGERED-then-deferred distinction:** VERIFIED. The SUMMARY's own verify-script output block literally shows `GATE: TRIGGERED` (not `NOT-TRIGGERED`), the per-hypothesis verdict table marks H2 `**SUPPORTED**`, and the narrative explicitly separates the mechanical gate result (TRIGGERED) from the operator's downstream choice (`defer`) — these are never conflated into a single NOT-TRIGGERED state anywhere in the file.
3. **No feature/phase10-\* branch or bot/ changes:** VERIFIED. `git branch -a | grep -i phase10` returns nothing; `git status --porcelain -- bot rules.json rules_options.json` is empty at current HEAD.

### Human Verification Required

None. This is a research-only phase whose single judgement checkpoint (Task 2 of plan 10-06, "which arm to productionize") already occurred during execution and is fully recorded with the operator's `defer` selection and rationale. No further human sign-off is needed to confirm the phase goal was achieved.

### Gaps Summary

No gaps found. All 7 ROADMAP success criteria verified against live codebase state (not SUMMARY claims): files exist and are substantive, tests pass live (1127 passed, 1 skipped), git history proves pre-registration ordering, cache-only discipline is evidenced by log content, the H1-H8 verdict table is numerically consistent with the committed `results.csv`, and the conditional XSR-06 gate mechanism worked exactly as designed (mechanically triggered via H2, operator-deferred, deferral recorded, zero production code touched, zero feature branch created).

---

_Verified: 2026-08-18_
_Verifier: Claude (gsd-verifier)_
