---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 04
subsystem: strategy-core
tags: [ibs, pure-functions, parity, tdd]
requires: ["12-01", "12-02"]
provides:
  - "bot.ibs.strategy: compute_ibs, parse_snapshot, decide_exits, decide_entries, size_position, trading_days_held"
  - "tests/ibs/test_parity.py: _load_research_simulate (AST extraction), _replay_production"
affects: [later bot/ibs plans (service, probe)]
tech-stack:
  added: []
  patterns: ["pure module (imports only math/datetime); research function loaded by AST, never imported"]
key-files:
  created:
    - bot/ibs/strategy.py
    - tests/ibs/test_strategy.py
    - tests/ibs/test_parity.py
  modified: []
key-decisions:
  - "Ruling 3 divergence pinned by test: production never re-enters a code exited the same session; research does"
  - "Parity scenarios use exact-binary prices/IBS targets so ties are exact, not float noise"
requirements-completed: [IBS-03]
duration: ~15 min
completed: 2026-10-04
---

# Phase 12 Plan 04: IBS pure strategy core Summary

Pure decision core for the IBS bot (D-02..D-05, D-16) with fail-closed per-code snapshot validation, proven identical to the research `simulate` on a 12-trade multi-day scenario; the one deliberate divergence (no same-day re-entry, ruling 3) is pinned by its own test.

## Tasks

| Task | Commits |
|------|---------|
| 1. Pure functions (TDD) | `1a36159` (RED), `f93e6f7` (GREEN) |
| 2. Parity + divergence tests | `3297ddf` |

## Verification

- `tests/ibs`: 125+ passed; full suite 1544 passed, 1 skipped (baseline 1490 + 54 new, zero regressions).
- `bot/ibs/strategy.py` imports are exactly `datetime`, `math`; no 0.2/0.8/10000/100000 literals.
- Parity test never imports the research module (AST FunctionDef extraction), runs in well under a second, no network. Research file untouched.

## Deviations from Plan

**1. [Rule 1 - Test bug] Parity scenario IBS ties were float noise, not exact.** First draft built H/L from percentage ranges, so "tied" IBS values differed by ~1e-17 and broke the intended column-order tie-break (production and research both sorted on the noise). Fixed by building frames from quarter-dollar closes, range 4.0 and sixteenth-multiple IBS targets (all exact binary floats). Test-only; no production change.

**2. Scenario B uses 10 sessions rather than max_hold + 3.** Research trade 2 is only recorded at its own exit, and production trade 2 enters one session later, so 10 sessions are needed to observe trade 1 and 2 in both. Assertions are on the first two trades as specified.

## Known Stubs

None.

## Threat Flags

None. T-12-02 / T-12-02b / T-12-13 mitigations implemented as planned (fail-closed `parse_snapshot`, zero shares on bad price, parity test).

## Self-Check: PASSED
