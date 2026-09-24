---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 02
subsystem: options
tags: [options, bull-call-spread, strategy-core, tdd]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 01
    provides: "OptionsConfig long_delta / max_debit_to_width / profit_target_pct_of_max fields (read by the SimpleNamespace bull_cfg test fixture here; strategy.py itself only reads attributes, so no import-time dependency)"
provides:
  - "pick_strikes(rows, px, 'bull_call_spread', cfg) -> {legs, debit, width} with BUY leg first"
  - "size_debit_position(debit, cfg, open_max_loss_total) -> int (debit x 100 risk, shared floor + global BP-cap logic)"
  - "manage_decision_debit(mark, debit, width, dte, cfg) -> Optional[str] (assignment_guard / profit_target / dte_exit, no stop loss)"
  - "docs/research/2026-09-24-super-bull-call-spread.md strategy provenance"
affects: [11-03, 11-04, 11-05, 11-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Debit structures reuse the credit path's shared internals (leg_is_liquid, _closest_delta, _pick_wing, _mid, _leg) rather than duplicating them"
    - "size_position/size_debit_position both delegate to a private _size_for_risk(risk, cfg, open_max_loss_total) so the floor+BP-cap math has one implementation"
    - "SimpleNamespace cfg fixtures (precedent: tests/options/test_execution.py) let a plan's tests exercise new cfg fields the loader hasn't shipped yet, since the pure strategy functions only read attributes"

key-files:
  created:
    - docs/research/2026-09-24-super-bull-call-spread.md
  modified:
    - bot/options/strategy.py
    - tests/options/test_strategy.py

key-decisions:
  - "size_position's body was extracted verbatim into _size_for_risk; size_position and the new size_debit_position both call it, so credit-path numeric results are provably unchanged (existing TestSizePosition tests untouched and green)"
  - "_pick_bull_call returns None (not a raised error) for every gate failure, matching the credit path's None-on-reject convention rather than raising"
  - "The provenance doc keeps a full rule-to-config mapping table plus a Deviations section, following the D-27 requirement for a summarized (non-transcript) record"

requirements-completed: [MSO-04, MSO-05, MSO-09]

# Metrics
duration: ~25min
completed: 2026-09-24
---

# Phase 11 Plan 02: Bull Call Spread Strategy Core Summary

**Added `bull_call_spread` strike selection (30-delta long / listed wing above it), the 1/4-rule debit gate, `size_debit_position`, and `manage_decision_debit` to the pure `bot/options/strategy.py` core, proven against the NVDA 225/235 worked example (debit 1.96, width 10) with the credit path byte-identical.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3/3 completed
- **Files modified:** 2 (bot/options/strategy.py, tests/options/test_strategy.py); 1 created (docs/research/2026-09-24-super-bull-call-spread.md)

## Accomplishments
- `pick_strikes(rows, underlying_px, "bull_call_spread", cfg)` now builds a defined-risk debit spread: long call closest to `cfg.long_delta`, short call the listed strike closest to `long_strike + width_target` strictly above it (reusing `_closest_delta`/`_pick_wing`/`leg_is_liquid`), gated by the 1/4 rule (`0 < debit <= cfg.max_debit_to_width * width`), legs returned BUY-first.
- `size_position` and the new `size_debit_position` both delegate to a shared `_size_for_risk` helper, so credit-path sizing math is provably byte-identical while the debit path sizes on `debit * 100` risk with the same floor + global BP-cap logic.
- `manage_decision_debit(mark, debit, width, dte, cfg)` implements the exit order assignment_guard -> profit_target (of max profit) -> optional dte_exit, with no stop-loss branch, and the `-mark` sign flip lives in exactly one place (`mark_spread` itself untouched).
- `docs/research/2026-09-24-super-bull-call-spread.md` records the video's source/retrieval metadata, distilled rules, the NVDA worked example, the shipped `super_bull_call` config mapping, and every deviation from the video, without reproducing transcript text.

## Task Commits

Each task was committed atomically:

1. **Task 1: bull_call_spread strike selection + 1/4-rule gate + size_debit_position (D-12, D-13, D-14)** - `5b2e3f0` (feat)
2. **Task 2: manage_decision_debit — assignment guard, profit target of max, optional DTE exit, no stop (D-15, D-19)** - `f7e62ee` (feat)
3. **Task 3: Strategy provenance doc for Super Bull Call Spread (D-27)** - `58412d5` (docs)

_No TDD RED-only or REFACTOR-only commits were needed — tests and implementation for each task landed together in a single verified commit._

## Files Created/Modified
- `bot/options/strategy.py` - added `_pick_bull_call`, `size_debit_position`, `_size_for_risk`, `manage_decision_debit`; extended `pick_strikes`'s allowed structures and docstring; module docstring/exports updated
- `tests/options/test_strategy.py` - added `_nvda_calls` helper, `bull_cfg` fixture, `TestPickStrikesBullCallSpread`, `TestSizeDebitPosition`, `TestManageDecisionDebit` (30 new tests total)
- `docs/research/2026-09-24-super-bull-call-spread.md` - new provenance doc (76 lines)

## Decisions Made
- Kept `_pick_bull_call` and `manage_decision_debit` as private/public functions colocated with their credit-path counterparts (`pick_strikes`, `manage_decision`) rather than a separate module, so both structures share one D7-pure file the backtester can keep replaying.
- Used `SimpleNamespace(**{**vars(options_cfg), ...})` for `bull_cfg` (matching `tests/options/test_execution.py` precedent) instead of `dataclasses.replace`, since `OptionsConfig` doesn't yet ship the debit-specific fields as non-Optional in every test context — this keeps plan 11-02 independent of plan 11-01's exact field list.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

`bot/options/strategy.py` now has everything the service layer needs to open, size, and manage a `bull_call_spread` position (MSO-04/MSO-05 satisfied). The Phase 9 backtester still rejects `bull_call_spread` (D-10, deferred) — no backtest arm exists for this strategy yet, which the provenance doc's "Not validated" section flags explicitly. Plans wiring the service/store/execution layers to dispatch on `strategy_name` (D-18, D-19, D-21) can now consume `_pick_bull_call`'s output shape (`{"legs", "debit", "width"}`) and `manage_decision_debit`'s reasons directly.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED

- FOUND: bot/options/strategy.py
- FOUND: tests/options/test_strategy.py
- FOUND: docs/research/2026-09-24-super-bull-call-spread.md
- FOUND commit: 5b2e3f0
- FOUND commit: f7e62ee
- FOUND commit: 58412d5
