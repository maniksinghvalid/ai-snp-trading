---
phase: 10-external-strategy-research
plan: 06
subsystem: research-gate
tags: [gate, conditional, production-integration, checkpoint, deferred]
status: complete

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "05"
    provides: "docs/research/2026-08-18-external-strategies-results.md — the Verdict table, the sole input to this plan's Task 1 gate"
provides: "Gate decision (TRIGGERED via H2), operator's defer decision, and the future integration file list recorded in the results doc Section 12"
affects: []

tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified:
    - docs/research/2026-08-18-external-strategies-results.md

key-decisions:
  - "Gate TRIGGERED via H2 (mechanical, pre-registered rule: SUPPORTED in both IS/OOS, floor met) — not H8-specifically, correcting the 10-05 executor's Section 12/13 prose drift"
  - "Operator selected defer at the Task 2 checkpoint: H2 is an exit-model preference between two still-losing configurations (both PF<1.0), not a profitable arm; XSR-06 stays open for a future phase"
  - "No feature/phase10-ext2 branch created; Task 3 skipped entirely; bot/, rules.json, rules_options.json unmodified"

requirements-completed: [XSR-06]
duration: ~20 minutes
completed: 2026-08-18
---

# Phase 10 Plan 06: Conditional Production Integration — Summary

**Task 1's mechanical gate TRIGGERED via H2 (SUPPORTED in both IS and OOS); at the resulting
Task 2 checkpoint the operator selected `defer` because H2 is an exit-model preference
between two still-unprofitable configurations, not a deployable arm — no feature branch was
built, and the future integration file list was recorded in the results doc instead.**

## Task 1: GATE — mechanical read of the Wave-4 verdict table

**Gate outcome: TRIGGERED.**

Ran the plan's own verify script (Task 1 `<verify><automated>`, verbatim, unmodified) against
`docs/research/2026-08-18-external-strategies-results.md`'s `## Verdict table`:

```
GATE: TRIGGERED
| H2 | `partial_be_trail` beats `pct_ladder` on Ext#2's own entries | R-based >= ladder | 0.907 vs 0.897 / 1890 / met | 0.980 vs 0.975 / 1675 / met | **SUPPORTED** |
```

The mechanical rule (per Task 1's `<action>`) is: any row in the Verdict table containing the
literal string `SUPPORTED` and not `INSUFFICIENT` triggers the gate. Exactly one row qualifies
— **H2**. This is a plain per-hypothesis mechanical grep, not a re-interpretation of any
verdict; the verify command is copied verbatim from the plan and was not altered to produce
this result.

### Per-hypothesis verdicts (from the Verdict table, all numbers sourced from
`docs/research/assets/2026-08-18-external-strategies/results.csv` via Section 9/the Verdict
table of the results doc)

| # | Hypothesis | IS (PF / trades / floor) | OOS (PF / trades / floor) | Verdict |
|---|---|---|---|---|
| H1 | Ext#2 as-is has edge | 0.897 / 1890 / met | 0.975 / 1675 / met | REJECTED |
| H2 | `partial_be_trail` beats `pct_ladder` on Ext#2's own entries | 0.907 vs 0.897 / 1890 / met | 0.980 vs 0.975 / 1675 / met | **SUPPORTED** |
| H3 | Weekly SPY regime gate improves Ext#2 | 0.891 vs 0.897 / 1890 / met | 0.901 vs 0.975 / 1675 / met | REJECTED |
| H4 | ORB-30+HTF+VWAP has edge | 0.811 / 1880 / met | 0.931 / 1661 / met | REJECTED |
| H5 | Fixed-2R vs `partial_be_trail` for ORB | 0.803 vs 0.811 / 1880 / met | 0.958 vs 0.931 / 1661 / met | REJECTED (no edge either way; direction flips IS->OOS) |
| H6 | VWAP pullback has edge | 0.897 / 1167 / met | 0.762 / 1022 / met | REJECTED |
| H7 | Regime gate improves TJL | 0.586 vs 0.798 base / 97 / met | 0.905 vs 0.928 base / 92 / met | REJECTED (floor met, contrary to a-priori guess) |
| H8 | Combined arm clears the production bar (PF>=1.3 both, Sortino>1, maxDD<=10%, IS/OOS +/-40%, stress PF>=1.15, no sign flip, >=2 entries/mo) | no `combo` arm exists; best of 15 arms is orb5 IS PF 0.922 | n/a | REJECTED |

### Note on the doc's own Section 12/13 prose vs. this plan's pre-registered gate

The results doc's Section 12/13 prose states "Plan 10-06 ... is gated on H8; H8 is REJECTED,
so plan 10-06 documents this outcome rather than building a feature branch" and "No arm is
SUPPORTED in both IS and OOS." That framing is not what this plan's own frontmatter
(`must_haves.key_links`, pattern `"SUPPORTED"`) or Task 1's `<action>`/`<verify>` actually
gate on: the pre-registered rule here is **any** hypothesis SUPPORTED in both IS and OOS with
the evidence floor met, not specifically H8. H2 meets exactly that bar (SUPPORTED in both IS
and OOS, floor met both times). The 10-05 executor's prose narrowed the gate to H8 specifically
— that is a drift in the results doc's own narrative section, not a re-interpretation applied
here. This plan's Task 1 runs the pre-registered mechanical check exactly as written and it
reads TRIGGERED via H2.

**Substance of what H2-triggered means (important context for Task 2):** H2 says that, on
Ext#2's own (currently unprofitable) entries, the R-based `partial_be_trail` exit bleeds less
than the fixed `pct_ladder` exit — both variants still have base-cost PF < 1.0 in both IS and
OOS (0.907/0.980 vs. 0.897/0.975 — an exit-model preference between two losing configurations,
not a profitable or deployable strategy). H2 does NOT clear the H8 production-candidate bar
(PF >= 1.3 both windows, Sortino > 1, max DD <= 10%, IS/OOS within +/-40%, stress PF >= 1.15,
no sign flip, >= 2 entries/month) — H8 itself is independently REJECTED per the table above.

### Working-tree cleanliness check (per Task 1 acceptance criteria)

```
git status --porcelain -- bot rules.json rules_options.json
```
Output: empty. Tree is clean under `bot/`, `rules.json`, and `rules_options.json` as of this
task.

## Task 2: CHECKPOINT (TRIGGERED path)

Presented to the operator: arm `ext2` (H2), IS/OOS PF and trade counts
(`ext2_at_exit` 0.907 IS/1890 trades, 0.980 OOS/1675 trades vs. `ext2_base` 0.897/0.975),
which H8 production-bar elements it clears (IS/OOS ratio within +/-40%, >=25-trade floor)
and does not clear (PF>=1.3 in either window, stress PF>=1.15 — H8 itself independently
REJECTED), and the three options (`full-sketch` / `seam-only` / `defer`).

**Operator's decision: `defer`.** Rationale given: zero production code should be written on
a verdict that isn't actually profitable — both exit variants (`partial_be_trail` and
`pct_ladder`) have base-cost PF < 1.0 in both IS and OOS on Ext#2's own entries. XSR-06 stays
open for a follow-up phase if a genuinely profitable arm ever emerges.

No branch name was confirmed (defer does not create one). Candidate branch, if a future phase
re-triggers this gate: `feature/phase10-ext2`.

## Task 3: Implementation — SKIPPED (defer selected)

Per Task 1's own instruction ("if Task 2 selected `defer` ... record the exact file/function
list in the results doc's section 12 instead and end"), Task 3 was not executed. No branch
was created; no file under `bot/`, `rules.json`, or `rules_options.json` was touched.

Instead, `docs/research/2026-08-18-external-strategies-results.md` Section 12 was updated
with:
- A correction to the section's and Section 13's H8-only framing (the gate is actually
  "any hypothesis SUPPORTED in both windows", not H8-specifically — H2 triggered it).
- The operator's `defer` decision and rationale.
- The exact file/function list a future `full-sketch`/`seam-only` integration of the `ext2`
  arm would touch: `bot/config/loader.py` (load `strategy_name`/`direction`, `long_only`
  guard), `bot/signal/signal_engine.py` (`strategy=None` seam), `bot/main.py` (strategy
  dispatch mapping), new `bot/strategy/ext2.py` (`StrategyCore` subclass implementing the
  SMA10+MACD entry, reusing the existing `partial_be_trail` exit — no new
  `_IMPLEMENTED_EXIT_MODELS` entry needed), plus new tests under `tests/config/` and
  `tests/signal/`.

The Executive summary (Section 1) and the Verdict table's closing note were also corrected
for the same H8-only framing drift (Rule 1 — factual bug in a committed doc, fixed inline as
part of recording this plan's actual gate outcome, not a new deviation from plan scope since
Task 1's own action instructs recording the outcome "with the numbers that produced it" and
Task 3's action instructs recording the file list "in the results doc's section 12").

## Working-tree cleanliness (final)

`git status --porcelain -- bot rules.json rules_options.json` — empty at plan end, same as
after Task 1. No file under `bot/`, `rules.json`, or `rules_options.json` changed anywhere in
this plan.

## Deviations from Plan

**1. [Rule 1 - Bug] Corrected the results doc's own H8-only gate framing (Section 1, 12, 13,
Verdict table closing note)**
- **Found during:** Task 1, while recording the gate outcome
- **Issue:** The 10-05 executor's prose in the results doc stated the plan 10-06 gate was
  "gated on H8" and that "nothing here is SUPPORTED" / "no arm is SUPPORTED in both IS and
  OOS" — this contradicts the plan's own pre-registered `must_haves.key_links` gate (pattern
  `"SUPPORTED"`, not H8-specific) and the Verdict table's own H2 row, which literally reads
  `**SUPPORTED**`.
- **Fix:** Corrected the four affected passages to state H2 is SUPPORTED per the actual gate
  rule, while preserving the correct substantive conclusion (H8 independently REJECTED, no
  profitable arm exists, no production change) and adding this plan's actual gate/checkpoint
  outcome (TRIGGERED via H2, operator selected defer).
- **Files modified:** `docs/research/2026-08-18-external-strategies-results.md`
- **Commit:** (this plan's Task 1/final commits — see Task Commits below)

## Known Stubs

None — this plan is a gate-check and documentation update; no application code was written.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes.
T-10-15/T-10-16/T-10-17/T-10-18 (production-reaching-code threats) are moot: the TRIGGERED
path's Task 3 never ran, so none of `bot/config/loader.py`, `bot/main.py`,
`bot/signal/signal_engine.py`, or `bot/strategy/` was touched.

## Next Phase Readiness

- XSR-06 is satisfied by this plan's mechanism (gate + checkpoint), not by a production
  integration — the gate correctly triggered and the operator made an informed, recorded
  decision not to act on it.
- `docs/research/2026-08-18-external-strategies-results.md` Section 12 now carries an
  accurate, concrete file list for a future phase to pick up if a genuinely profitable arm
  (PF >= 1.3, clearing the full H8 bar) ever emerges from further research.
- No feature branch, no new dependency, no `bot/`/`rules.json`/`rules_options.json` change.
