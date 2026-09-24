---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 01
subsystem: options-bot-config
tags: [jsonschema, dataclasses, config-loader, fail-closed]

# Dependency graph
requires:
  - phase: 08-options-premium-selling
    provides: OPTIONS_SCHEMA, OptionsConfig, load_options_config, _IMPLEMENTED_STRUCTURES fail-closed pattern
provides:
  - STRATEGIES_SCHEMA (structural-only jsonschema for the multi-strategy rules_options.json shape)
  - OptionsConfig with 6 new Optional fields (name, universe_source, long_delta, max_debit_to_width, profit_target_pct_of_max, equity_state_db) and 6 credit-only fields now Optional
  - OptionsBook dataclass (tuple of flat OptionsConfig per strategy + shared risk/execution/service dicts)
  - load_options_book(path) -> OptionsBook
  - load_options_config(path, strategy=None) -> OptionsConfig (unchanged name/return type, widened signature)
  - legacy_view(raw, name=None) -> dict (exact inverse of _wrap_legacy, shares the _RELOCATED table)
  - _check_strategy fail-closed business rules for D-04/D-05/D-11 (duplicate names, universe XOR universe_source, unsupported universe_source, unimplemented structure, credit/debit key requiredness, equity_state_db vs state_db collision)
affects: [11-02, 11-03, 11-04, 11-05, 11-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Loader-side dispatch on file shape (strategies key present or not), never a jsonschema oneOf/anyOf/if-then-else conditional"
    - "One relocation table (_RELOCATED) walked by both _wrap_legacy and legacy_view — inverse operations sharing one mapping so they cannot drift"
    - "New dataclass fields appended last with no Python-level default; both loader paths always pass every field explicitly (avoids field-ordering issues entirely)"

key-files:
  created: []
  modified:
    - bot/options/schema.py
    - bot/options/config.py
    - tests/options/conftest.py
    - tests/options/test_config.py

key-decisions:
  - "New OptionsConfig fields (name, universe_source, long_delta, max_debit_to_width, profit_target_pct_of_max, equity_state_db) carry no dataclass-level default — both loader code paths pass every field explicitly in one OptionsConfig(...) call each, avoiding field-ordering errors entirely (research Pattern 3 recommendation A1)"
  - "_check_strategy's structure-conditional checks cover both the entry IV-gate keys and the manage profit-target/stop-loss key pair (not just entry, per research Pitfall 3/Architecture Pattern 4) via the shared _CREDIT_KEYS/_DEBIT_KEYS tuples"
  - "STRATEGIES_SCHEMA reuses OPTIONS_SCHEMA's entry/execution properties dicts by reference (not copies) so IV-gate keys stay typed when present without duplicating the schema"

patterns-established:
  - "Multi-strategy config loader: schema stays structural-only; every per-structure/per-shape business rule is a plain Python `if ...: raise ConfigError(...)` in the loader, mirroring the existing _IMPLEMENTED_EXIT_MODELS/_IMPLEMENTED_STRUCTURES idiom"

requirements-completed: [MSO-01, MSO-02, MSO-03]

# Metrics
duration: 25min
completed: 2026-09-24
---

# Phase 11 Plan 01: Multi-strategy config loader Summary

**`rules_options.json` gains a `strategies` array + shared `risk`/`execution`/`service` blocks; `load_options_book` flattens every strategy into an `OptionsConfig`, `load_options_config` keeps its exact flat contract, and `legacy_view` projects a strategy back to the legacy shape — all through one shared field-relocation table so legacy and multi-strategy files can never map differently.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-09-24T14:30:56Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- `STRATEGIES_SCHEMA` added to `bot/options/schema.py` (structural only, zero jsonschema conditionals) alongside the frozen legacy `OPTIONS_SCHEMA`; `structure.type` enum (shared `_STRUCTURE_TYPES`) now includes `bull_call_spread` in both schemas
- `OptionsConfig` extended with 6 new fields and 6 existing credit-only fields made `Optional`; `OptionsBook`, `load_options_book`, widened `load_options_config(path, strategy=None)`, and `legacy_view(raw, name=None)` added to `bot/options/config.py`
- Every D-11 fail-closed rule implemented in `_check_strategy` and `load_options_book`: duplicate strategy names, universe XOR universe_source, unsupported `universe_source`, unimplemented structure (existing guard, unchanged message), credit/debit key requiredness across both `entry` and `manage` blocks, relocated-global-in-strategy-block rejection, and `service.equity_state_db` vs `service.state_db` collision
- 50 new tests added (`TestLoadOptionsBook`, `TestLegacyView`, `TestStrategiesShapeFailsClosed`); zero existing tests edited

## Task Commits

1. **Task 1: STRATEGIES_SCHEMA + OptionsConfig fields + OptionsBook + load_options_book / load_options_config(strategy) / legacy_view** - `9fcdd43` (feat)
2. **Task 2: Fail-closed loader rules for the strategies shape** - `8d2a57f` (feat)

_Both tasks used `tdd="true"` in the plan; tests and implementation were written together per task since each task's `<behavior>` bullets specify exact assertions rather than a separate red/green cadence — every test was verified against the implementation before commit (RED-would-fail confirmed for Task 2's checks by running the tests against Task 1's code first)._

## Files Created/Modified
- `bot/options/schema.py` - `_STRUCTURE_TYPES` shared constant; `STRATEGIES_SCHEMA` (structural-only, new); `OPTIONS_SCHEMA`'s `structure.type` enum now references the shared constant
- `bot/options/config.py` - `OptionsConfig` (+6 fields, 6 annotations widened to `Optional`); `OptionsBook`; `_RELOCATED`/`_UNIVERSE_SOURCES`/`_DEBIT_STRUCTURES`/`_CREDIT_KEYS`/`_DEBIT_KEYS`/`_DEFAULT_EQUITY_STATE_DB` constants; `_read_json`/`_validate`/`_opt`/`_wrap_legacy`/`_flatten`/`_check_strategy` helpers; `load_options_book`, `load_options_config(path, strategy=None)`, `legacy_view(raw, name=None)`
- `tests/options/conftest.py` - `options_book_rules` (literal strategies-shape fixture: tasty_credit_spreads + super_bull_call) and `options_book` fixtures
- `tests/options/test_config.py` - `TestLoadOptionsBook` (14 tests), `TestLegacyView` (8 tests), `TestStrategiesShapeFailsClosed` (30 tests, including 3 parametrized groups); module-level `_PRE_EXISTING_FIELDS` (43-field list, D-26 comparator)

## Decisions Made
- New `OptionsConfig` fields carry no Python-level default (research recommendation A1) — both loader paths pass every field explicitly, sidestepping dataclass field-ordering constraints entirely
- `_check_strategy`'s per-structure requiredness covers the `manage` block's `profit_target_pct_of_credit`/`stop_loss_credit_multiple` vs `profit_target_pct_of_max` split, not just `entry`'s IV-gate keys (research Pitfall 3) — `_CREDIT_KEYS`/`_DEBIT_KEYS` tuples span both blocks
- `legacy_view` and `_wrap_legacy` share one `_RELOCATED` table (research Open Question 3, resolved) instead of two independently-maintained mapping tables

## Deviations from Plan

None - plan executed exactly as written. All `<behavior>` bullets and acceptance criteria for both tasks pass, including the acceptance-criteria greps (`oneOf|anyOf|"if"` count 0 in schema.py, `sys.exit` absent from config.py, existing test classes byte-identical per `git diff 405c8d2` producing zero deleted lines).

## Issues Encountered
- Initial docstring in `schema.py` mentioned `oneOf`/`anyOf`/`if-then-else` in prose, which the acceptance-criteria grep (`grep -c "oneOf\|anyOf\|\"if\""`) flagged as a false positive (count 1, not 0). Reworded the docstring to avoid the literal substrings while keeping the same meaning — resolved before commit, not a deviation requiring the deviation-rule protocol (doc wording only, zero behavior change).
- Initial import edit in `test_config.py` modified the pre-existing `from bot.options.config import load_options_config, OptionsConfig` line (to merge in new names), which would have failed the acceptance criterion requiring zero deleted lines in `git diff 405c8d2 -- tests/options/test_config.py`. Fixed by adding a second, separate import line instead of editing the first — resolved before commit.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `load_options_book`/`load_options_config`/`legacy_view` are ready for plan 11-02 onward (bull-call strategy core, universe reader, store migration, service composition, `bot/main.py` dispatch fix, `backtester/options_run.py --strategy`).
- The shipped `rules_options.json` itself is still the legacy flat shape — plan 11-04 converts it to the `strategies` shape (D-26) and adds the field-for-field equivalence test against this plan's loader.
- Full suite green: 1184 passed, 1 skipped (baseline 1134 passed / 1 skipped + 50 new tests, 0 new failures, 0 new skips). Quick suite (`tests/options tests/backtester/options`): 311 passed (baseline 261 + 50).

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED

All created/modified files verified present on disk; both task commits (`9fcdd43`, `8d2a57f`) verified present in git log.
