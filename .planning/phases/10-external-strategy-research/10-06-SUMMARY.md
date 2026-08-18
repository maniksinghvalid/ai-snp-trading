---
phase: 10-external-strategy-research
plan: 06
subsystem: research-gate
tags: [gate, conditional, production-integration, checkpoint]
status: in-progress

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "05"
    provides: "docs/research/2026-08-18-external-strategies-results.md — the Verdict table, the sole input to this plan's Task 1 gate"
provides: "TBD once plan completes (either NOT-APPLICABLE record, or feature/phase10-<arm> branch link)"
affects: []

requirements-completed: []
duration: TBD
completed: TBD
---

# Phase 10 Plan 06: Conditional Production Integration — Summary (IN PROGRESS)

**Status: awaiting operator checkpoint decision (Task 2). This document currently records
only Task 1's gate outcome; it will be updated/finalized once Task 2/3 resolve.**

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

## Task 2: CHECKPOINT (TRIGGERED path — presented to operator, awaiting decision)

Not yet resolved. See CHECKPOINT REACHED message returned alongside this commit.

## Task 3: Implementation (pending Task 2's decision)

Not yet started — blocked on Task 2.
