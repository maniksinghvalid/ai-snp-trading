---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 04
subsystem: options-bot-config
tags: [config-loader, cli, dispatch, fail-closed]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 01
    provides: legacy_view, load_options_book, load_options_config(path, strategy=None), OptionsBook
provides:
  - backtester/options_run.py --strategy flag projecting a chosen strategy to the legacy shape via legacy_view before --set overrides
  - options_run debit-structure (bull_call_spread) rejection before any run dir exists
  - rules_options.json converted to the strategies shape (tasty_credit_spreads + super_bull_call), no top-level strategy_name
  - bot/main.py dispatch widened to route both the legacy strategy_name marker and the strategies-shape file to the options bot
  - D-26 field-for-field no-drift test proving the converted shipped file's default view equals the pre-change legacy config on all 43 pre-existing OptionsConfig fields
affects: [11-05, 11-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "CLI strategy projection: legacy_view(raw, args.strategy) runs BEFORE apply_overrides so every --set dotted-path override keeps resolving against the same legacy shape regardless of the on-disk file shape"
    - "bot/main.py dispatch widened by OR-ing a second structural marker ('strategies' in data) onto the existing strategy_name equality check, rather than restructuring the peek"

key-files:
  created: []
  modified:
    - backtester/options_run.py
    - tests/backtester/options/test_options_run.py
    - rules_options.json
    - bot/main.py
    - tests/options/test_config.py
    - tests/options/test_dispatch.py

key-decisions:
  - "options_run's debit-structure rejection check reads the effective config (post --set) so `--set structure.type=bull_call_spread` is caught for any base strategy, not just a literal --strategy super_bull_call selection"
  - "legacy_view exceptions (unknown --strategy name) are caught in their own try/except block, positioned before apply_overrides, keeping the WR-05 ordering invariant (no run dir before every check passes) intact"

patterns-established:
  - "Debit-structure rejection lives right after apply_overrides and right before the temp-file schema validation — the smallest slot that guarantees no run dir can exist yet while still validating the fully-effective (post-override) config"

requirements-completed: [MSO-02, MSO-09]

# Metrics
duration: 8min
completed: 2026-09-24
---

# Phase 11 Plan 04: Backtester strategy projection + shipped config conversion Summary

**`backtester/options_run.py` gained `--strategy` + `legacy_view` projection (before `--set`) and a debit-structure `[ERROR]`/exit-1 guard; the shipped `rules_options.json` is now the two-strategy book (tasty_credit_spreads unchanged + new super_bull_call); `bot/main.py`'s dispatch was widened so `python3 -m bot --rules rules_options.json` still routes to the options bot; a D-26 field-for-field test proves zero drift in the tasty view.**

## Performance

- **Duration:** ~8 min
- **Completed:** 2026-09-24T15:04:41Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- `backtester/options_run.py` accepts `--strategy NAME` (default: first strategy), projects it to the legacy flat shape via `legacy_view` before `apply_overrides`, and rejects an effective `structure.type == "bull_call_spread"` with `[ERROR]`/exit 1 before any run directory is created — every documented `--set` arm command (`entry.ivr_min=20`, `structure.short_delta=0.16`, `structure.type=put_credit_spread`, `entry.min_dte=1`, `entry.max_dte=10`) still runs verbatim against the converted file
- `rules_options.json` converted to the `strategies` shape: `tasty_credit_spreads` values copied verbatim, `super_bull_call` added per D-16, shared `risk`/`execution`/`service` blocks, no top-level `strategy_name` key
- `bot/main.py`'s dispatch widened to `data.get("strategy_name", "") == "tasty_credit_spreads" or "strategies" in data` (research Pitfall 1 / T-11-03) — the live launch command keeps routing to the options bot after the conversion
- D-26 field-for-field equivalence test (`test_shipped_tasty_view_equals_pre_change_config_field_for_field`) proves the converted file's default (`tasty_credit_spreads`) view is identical, value and type, to the pre-change legacy config on all 43 pre-existing `OptionsConfig` fields
- 4 new shipped-file tests (`test_shipped_file_matches_fixture` updated to compare against `options_book_rules`, `test_shipped_legacy_view_is_the_pre_change_file`, `test_shipped_book_has_both_strategies_in_order`, `test_shipped_super_bull_call_values`), 2 new dispatch regression tests, and 6 new `options_run` tests (4 for Task 1's `--strategy`/debit behavior, `test_strategy_super_bull_call_rejected`, and a 5-arm parametrized `test_documented_arm_commands_still_work`)

## Task Commits

1. **Task 1: options_run --strategy + legacy_view before --set + debit-structure rejection (D-10)** - `d70eedc` (feat)
2. **Task 2: Convert shipped rules_options.json + widen bot/main.py dispatch + D-26 no-drift tests** - `43c0e10` (feat)

_Both tasks used `tdd="true"` in the plan; tests were written alongside the implementation per task since each task's `<behavior>` bullets specify exact assertions rather than a separate red/green cadence — every new test was verified to fail against the pre-task code path (unknown flag / old dispatch / old fixture comparand) before the implementation change made it pass._

## Files Created/Modified
- `backtester/options_run.py` - `--strategy` CLI flag; `legacy_view(raw, args.strategy)` call before `apply_overrides`; debit-structure `[ERROR]`/exit-1 guard on the effective config; updated module/parser/main docstrings
- `tests/backtester/options/test_options_run.py` - `test_strategy_flag_selects_named_strategy`, `test_unknown_strategy_exits_1`, `test_debit_structure_override_rejected`, `test_help_lists_strategy_flag`, `test_strategy_super_bull_call_rejected`, parametrized `test_documented_arm_commands_still_work`
- `rules_options.json` - converted to the `strategies` shape (tasty_credit_spreads + super_bull_call, D-16/D-26/D-28)
- `bot/main.py` - dispatch condition widened to also match `"strategies" in data`; Step 1b comment updated
- `tests/options/test_config.py` - `test_shipped_file_matches_fixture` now compares against `options_book_rules`; added `test_shipped_tasty_view_equals_pre_change_config_field_for_field`, `test_shipped_legacy_view_is_the_pre_change_file`, `test_shipped_book_has_both_strategies_in_order`, `test_shipped_super_bull_call_values`
- `tests/options/test_dispatch.py` - `test_strategies_shape_dispatches_to_options_main`, `test_shipped_rules_options_dispatches_to_options_main`

## Decisions Made
- The debit-structure check reads `effective` (the post-`--set` config), not `args.strategy` — this correctly rejects `--set structure.type=bull_call_spread` applied to any base strategy, matching the plan's `<behavior>` spec exactly
- `legacy_view`'s `ConfigError` (unknown `--strategy`) is handled in its own `try/except`, inserted between the V5 raw-file read and `apply_overrides`, preserving the existing WR-05 no-partial-run-dir ordering

## Deviations from Plan

None — plan executed exactly as written. One minor wording note: `test_shipped_file_matches_fixture`'s docstring was updated (as directed by the plan's `<action>` text) in addition to its signature and assert line, so `git diff 405c8d2 -- tests/options/test_config.py | grep '^-[^-]'` shows 3 removed lines (signature, docstring, assert) rather than the 2 the acceptance-criteria prose implied; this is a direct, harmless consequence of following the plan's explicit docstring instruction and does not affect any behavior or the D-26 comparator.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required. Per STATE.md/CLAUDE.md convention, the live options bot process (if running) must be restarted after this change lands on `develop` for the new `rules_options.json` shape to take effect — no hot-reload exists.

## Next Phase Readiness

- `load_options_config`/`load_options_book`/`legacy_view` (11-01) and the shipped `rules_options.json` (this plan) are now mutually consistent and drift-tested; plans 11-05/11-06 (universe reader, store migration, service composition) can build against the real shipped file instead of a synthetic fixture.
- Full suite green: 1241 passed, 1 skipped (baseline 1225 passed, 1 skipped + 16 new tests, 0 new failures, 0 new skips). Quick suite (`tests/options tests/backtester/options`): 353 passed (up from the 337 baseline quoted for wave 1 + this plan's 16 new tests).
- Safety: no OpenD connection, no live bot launch, and no network Massive call were made in this plan's verification — every test is offline/mocked per the plan's safety constraints.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED
