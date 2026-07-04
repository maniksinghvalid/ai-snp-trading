---
phase: 07-strategy-optimization
plan: "06"
subsystem: decision-gate
tags: [exit-model, phase-6-dependency, checkpoint, decision-only]
status: complete
decision: defer
---

## Summary

EXIT-MODEL selection (Phase 7 success criterion 2) is deferred to a Phase-6-gated follow-up. No source files were modified by this plan.

## Operator Decision

**Decision: DEFER**

The Phase 6 backtester is confirmed absent (no backtester `.py` files in repo — verified via `find . -name "*.py" | grep -i backtest` returning empty). Selecting an exit model without backtest evidence would violate success criterion 2 and CONTEXT.md's explicit "not by default" requirement.

The live bot continues on the validated `partial_be_trail` model. The exit-model config seam (plan 07-04) is already in place:
- `exit.model` is config-driven and schema-validated
- Loader defaults to `partial_be_trail` (zero behavior change)
- Loader FAILS CLOSED for `fixed_2r` and `full_to_1p5r_trail` until they are implemented and backtested

## What Ships in Phase 7 (Waves 1–3)

Pure risk reducers — all independent of exit-model selection:
- **RVOL-TOD gate** (SIG-RVOL-TOD): time-of-day cumulative-volume baseline replaces the fixed RVOL threshold
- **Tick-level stops** (RISK-TICK-STOP): broker-side Stop-Market order armed at fill, cancel-replaced on trail ratchet; D-02 quote-tick fallback for SIMULATE
- **Daily circuit breaker** (RISK-CIRCUIT): −2R session loss halts new entries, persists across restart, auto-resets next day
- **Exit-model config seam** (EXIT-MODEL partial): `exit.model` in `rules.json`, schema-validated, fail-closed for unimplemented variants

## Post-Phase-6 Follow-Up (Bounded)

When Phase 6 backtester lands, the bounded EXIT-MODEL selection follow-up is:

1. Implement the `fixed_2r` FSM branch in `PositionState` dispatched on `cfg.exit_model`
2. Implement the `full_to_1p5r_trail` FSM branch (note Assumption A3: 15m EMA trail may need a separate feed or derivation from the 5m bar buffer — resolve during implementation)
3. Widen `bot/config/loader.py::_IMPLEMENTED_EXIT_MODELS` to include the newly-built variants
4. Run the backtester across all three models on the same `rules.json` + historical 5m data
5. Write the winning model to `rules.json exit.model`
6. Record the comparison evidence (win rate, avg R, max drawdown, profit factor) as the selection justification

## Verification

- `find . -path ./node_modules -prune -o -name "*.py" -print | grep -i backtest` → **empty** (Phase 6 absent — block confirmed)
- `grep "_IMPLEMENTED_EXIT_MODELS" bot/config/loader.py` → `_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)` (fail-closed seam confirmed)
- `git diff --name-only` → no source files modified by this plan

## Self-Check: PASSED

- EXIT-MODEL criterion 2 explicitly gated on Phase 6 ✓
- Operator decision recorded (defer) ✓
- No unvalidated exit behavior ships ✓
- Live model remains `partial_be_trail` ✓
- Post-Phase-6 follow-up pre-scoped and documented ✓
