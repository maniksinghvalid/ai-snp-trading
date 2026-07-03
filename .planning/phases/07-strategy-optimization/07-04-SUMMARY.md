---
phase: 07-strategy-optimization
plan: 04
subsystem: config
tags: [exit-model, config, schema, fail-closed, CFG-01, EXIT-MODEL]
dependency_graph:
  requires: [07-01]
  provides: [exit.model config surface, StrategyConfig.exit_model, fail-closed guard for unimplemented exit variants]
  affects: [bot/config/loader.py, bot/config/schema.py, rules.json, tests/config/test_loader.py]
tech_stack:
  added: []
  patterns: [fail-closed guard via _IMPLEMENTED_EXIT_MODELS set, jsonschema enum for string validation, dataclass field with default at end]
key_files:
  created: []
  modified:
    - rules.json
    - bot/config/schema.py
    - bot/config/loader.py
    - tests/config/test_loader.py
decisions:
  - "exit_model field placed last in StrategyConfig with default='partial_be_trail' to satisfy Python dataclass ordering (fields with defaults must follow fields without defaults)"
  - "Schema enum validates the three candidate strings; _IMPLEMENTED_EXIT_MODELS then gates only the currently-backtest-proven model (partial_be_trail); two-stage check ensures unknown strings fail schema before the implemented guard fires"
  - "No FSM behavior changed — only config surface, schema validation, and fail-closed loader guard added"
metrics:
  duration_seconds: 228
  completed_date: "2026-07-03"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 4
---

# Phase 07 Plan 04: Exit-Model Config Surface Summary

Exit-model config surface established with `exit.model` in `rules.json`, schema enum validation, `StrategyConfig.exit_model` field, and a fail-closed loader guard for unimplemented variants — satisfying EXIT-MODEL success criterion 5 without shipping any unvalidated exit behavior.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | exit_model failing tests | 9864c07 | tests/config/test_loader.py |
| 1 (GREEN) | exit.model config + schema + loader | deaa6d8 | rules.json, bot/config/schema.py, bot/config/loader.py |

## What Was Built

**rules.json** — Added `"model": "partial_be_trail"` to the exit block, making the currently-implicit exit model choice explicit and machine-readable (CFG-01).

**bot/config/schema.py** — Added `model` property under `exit` with `{"type": "string", "enum": ["partial_be_trail", "fixed_2r", "full_to_1p5r_trail"]}`. Not added to `exit.required` (absence defaults to `"partial_be_trail"` in the loader). Unknown strings (e.g. `"moon"`) fail jsonschema validation before the implemented-set guard fires.

**bot/config/loader.py** — Added:
- `_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)` — module-level constant; only models with Phase 6 backtest evidence belong here
- `exit_model: str = "partial_be_trail"` on `StrategyConfig` — placed at the end of the dataclass with a default value to comply with Python dataclass ordering rules (fields with defaults must follow fields without)
- Fail-closed guard in `load_strategy_config`: reads `ex.get("model", "partial_be_trail")`, raises `ConfigError` with a Phase 6 / plan 07-06 pointer for any value not in `_IMPLEMENTED_EXIT_MODELS`
- `exit_model=model_raw` in the `StrategyConfig(...)` constructor call

**tests/config/test_loader.py** — 5 new tests in `TestExitModelConfig`:
1. `test_real_rules_json_exit_model_is_partial_be_trail` — real rules.json produces `cfg.exit_model == "partial_be_trail"`
2. `test_exit_model_omitted_defaults_to_partial_be_trail` — absent key defaults cleanly
3. `test_exit_model_fixed_2r_raises_config_error` — fail-closed with Phase 6 pointer
4. `test_exit_model_full_to_1p5r_trail_raises_config_error` — fail-closed with Phase 6 pointer
5. `test_exit_model_unknown_string_fails_schema_validation` — unknown string rejected at schema layer

## Verification Results

```
tests/config/test_loader.py — 28 passed (23 existing + 5 new)
tests/ (full suite) — 491 passed, 1 skipped
```

No FSM/behavior files modified. `git diff --name-only` for this plan covers only `rules.json`, `bot/config/schema.py`, `bot/config/loader.py`, `tests/config/test_loader.py`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Dataclass field ordering broke direct StrategyConfig construction**

- **Found during:** GREEN phase, regression run
- **Issue:** Adding `exit_model: str` in the middle of the StrategyConfig exit section (before required fields `initial_stop_pct`, `partial_profit_trigger_r`, etc.) caused `TypeError: StrategyConfig.__init__() missing 1 required positional argument: 'exit_model'` in tests that construct StrategyConfig directly (e.g. `test_config_driven.py`). Python dataclass rules require all fields with defaults to follow all fields without defaults.
- **Fix:** Moved `exit_model: str = "partial_be_trail"` to the last field in the dataclass (after `crash_loop_alert_threshold`). The loader uses keyword arguments so constructor call order is unaffected.
- **Files modified:** `bot/config/loader.py`
- **Commit:** deaa6d8

## Decisions Made

1. `exit_model` placed at end of `StrategyConfig` with default so Python dataclass ordering is valid and existing direct constructions remain unbroken.
2. Two-stage validation: jsonschema enum for the three candidates, then `_IMPLEMENTED_EXIT_MODELS` guard for the implemented subset. This gives distinct error messages (schema error vs. "not yet implemented, see plan 07-06").
3. Explicit default `"partial_be_trail"` in both `ex.get()` and the dataclass field default — belt-and-suspenders ensuring the default is consistent whether or not the field is set in `rules.json`.

## Threat Flag Coverage

T-07-14 (exit.model drives live exits without backtesting) — mitigated: `_IMPLEMENTED_EXIT_MODELS` guard ensures only `partial_be_trail` loads.
T-07-15 (garbage exit.model string) — mitigated: jsonschema enum rejects any value outside the three candidates.

## Known Stubs

None — all five exit_model tests exercise real behavior with no placeholder logic.

## TDD Gate Compliance

- RED commit: 9864c07 — `test(07-04): RED — exit_model config tests (partial_be_trail, fail-closed, schema enum)`
- GREEN commit: deaa6d8 — `feat(07-04): exit.model config surface — schema enum, fail-closed loader guard, StrategyConfig.exit_model`

Both gates satisfied.

## Self-Check: PASSED

- rules.json has `"model": "partial_be_trail"` at line 31 — FOUND
- bot/config/schema.py has `partial_be_trail` enum — FOUND
- bot/config/loader.py has `_IMPLEMENTED_EXIT_MODELS` and `exit_model` — FOUND
- Commits 9864c07 (RED) and deaa6d8 (GREEN) — FOUND in git log
- 491 tests pass, 1 skipped — VERIFIED
