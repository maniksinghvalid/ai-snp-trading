---
phase: 260817-0ph
plan: 01
status: complete
wave: 1
completed: 2026-08-17
tests_before: 731 passed, 1 skipped
tests_after: 844 passed, 1 skipped
tests_added: 113
commits:
  - 14d9ab5 feat(options) migration 0006 — option_positions + option_legs at schema v6
  - b5f1c85 feat(options) rules_options.json + OPTIONS_SCHEMA + OptionsConfig loader
  - 10aef4e feat(options) pure strategy core (expiry, strikes, sizing, management)
---

# Phase 8 Wave 1: `tasty_credit_spreads` pure core — Summary

Landed the zero-I/O foundation of the options strategy: SQLite schema v6, the
`bot/options` config/schema/strategy package, `rules_options.json`, and their
tests. No gateway, service, execution, or `bot/main.py` code was touched; the
731 pre-existing equity tests are untouched and green.

## Commits

| Commit | Task | Files |
|--------|------|-------|
| `14d9ab5` | 1 — migration 0006 | `bot/state/migrations.py`, `tests/state/test_migrations.py` |
| `b5f1c85` | 2 — config + schema | `bot/options/{__init__,schema,config}.py`, `rules_options.json`, `tests/options/{__init__,conftest,test_config}.py` |
| `10aef4e` | 3 — strategy core | `bot/options/strategy.py`, `tests/options/test_strategy.py` |

## Test counts

| | Tests |
|---|---|
| Before | 731 passed, 1 skipped |
| After | 844 passed, 1 skipped |
| Added | +113 (10 migration 0006, 25 config, 78 strategy) |

`tests/state`: 97 → 108. `tests/options`: new, 92. Full suite `python3 -m pytest -q`
green in 48s. No pre-existing test broke other than the two hard-coded `== 5`
version asserts, which were relaxed to `== CURRENT_VERSION` as the plan directed
(names kept for git-history continuity, with the same "originally tested for 5" note
the 0002/0003 tests carry).

## OptionsConfig field names

Flat, one field per JSON leaf, JSON leaf names verbatim. Exactly one rename, as
specified: `structure.type` → `structure_type`.

- top level: `strategy_name`, `universe` (tuple)
- entry: `ivr_min`, `ivp_min`, `fear_drop_pct`, `fear_ivr_min`,
  `max_spread_pct_of_mid`, `min_open_interest`, `target_dte`, `min_dte`,
  `max_dte`, `prefer_monthly`, `entry_scan_et`, `second_entry_scan_et`
- structure: `structure_type`, `short_delta`, `wing_width_pct_of_underlying`,
  `min_credit_to_width`
- sizing: `sizing_equity_usd`, `max_risk_per_trade_pct`, `max_bp_usage_pct`,
  `max_concurrent_positions`, `max_new_positions_per_day`, `daily_loss_limit_pct`
- manage: `manage_interval_min`, `profit_target_pct_of_credit`, `manage_dte`,
  `stop_loss_credit_multiple`, `assignment_guard_dte`
- execution: `limit_buffer_usd`, `poll_interval_s`, `ttl_s`,
  `escalation_step_usd`, `max_retries`
- service: `eod_report_et`, `watchdog_poll_interval_s`,
  `watchdog_reconnect_initial_s`, `watchdog_reconnect_cap_s`, `state_db`,
  `kill_file`, `report_dir`

Optionals (`ivp_min`, `second_entry_scan_et`, `stop_loss_credit_multiple`)
preserve `None`. No field has a default — every key is required by the schema, so
the dataclass never silently invents a strategy number.

## Design ambiguities and how they were resolved

1. **`pick_strikes` with an unrecognised structure.** The design specifies the
   two supported structures but not the behaviour for anything else. Silently
   degrading an unknown structure to the put-side-only spread would be a
   dangerous default, and `None` would be indistinguishable from "no valid
   strikes today". Resolved: `raise ValueError` — a programming error, not a
   data condition. `_IMPLEMENTED_STRUCTURES` in the loader already stops config
   from reaching it. Covered by `test_unknown_structure_raises`.
2. **Leg order within the long-wing group (iron condor).** The design fixes
   "long wings first, then shorts" but not the intra-group order. Resolved:
   `[long_put, long_call, short_put, short_call]` — put side before call side in
   both groups, so the leg list reads as two symmetric verticals.
3. **`passes_entry_gate` with missing fields.** Not specified. Resolved: fail
   closed — missing `ivr_pct` returns False; missing `ivp_pct` returns False only
   when `cfg.ivp_min` is set (when the dual gate is off, a missing IVP is
   irrelevant and must not block a valid entry).
4. **Fear threshold is a replacement, not a floor.** `change_pct <= -fear_drop_pct`
   sets the threshold to `fear_ivr_min` outright, per the design's wording
   ("IVR gate lowers to fear_ivr_min"). With the shipped defaults (30 → 20) this
   only ever loosens; a config with `fear_ivr_min > ivr_min` would tighten on
   fear days. Left as written rather than clamping, since clamping would be a
   silent config override.
5. **`universe` immutability.** The design shows a JSON list; the plan requires a
   tuple. Resolved as a tuple — config is read-only state shared across scheduler
   jobs.
6. **Schema `pattern` on a nullable time key.** `second_entry_scan_et` is
   `{"type": ["string","null"], "pattern": "^\\d{2}:\\d{2}$"}` — jsonschema
   applies `pattern` only to strings, so `null` validates and `"2pm"` does not.

## Deviations

- **`python` is not on PATH in this environment; used `python3`.** The plan's
  verify commands are written as `python -m pytest -q`. Only the interpreter
  invocation differed — the same commands ran and passed under `python3`
  (Python 3.14.5). No code change.
- **Test-grid tuning (test-only, no implementation change).** The synthetic
  strike grid's fixed $0.02-wide quotes are 8–20% of mid at the cheap wing
  strikes, which the shipped 5% liquidity gate correctly rejects. The
  `pick_strikes` tests therefore use a `grid_cfg` fixture that relaxes
  **only** `max_spread_pct_of_mid` to 30; every other knob stays at the shipped
  default. `leg_is_liquid` is tested against the shipped 5% gate directly.
- No deviation rules 1–4 were triggered; no threat-model additions.

## Threat model

`T-0ph-01` (structure tampering) mitigated: schema enum + `_IMPLEMENTED_STRUCTURES`
fail-closed guard, with a monkeypatched test proving the guard fires even for a
schema-valid value. `T-0ph-02` (sizing DoS) mitigated: `size_position` returns 0
for non-positive risk and clamps the BP cap at 0. `T-0ph-03`: no new packages.

## Known stubs

None. Every function in this wave is fully implemented. `bot/options/store.py`,
`execution.py`, and `service.py` do not exist yet by design (Waves 2–4) and
nothing in this wave imports them.

## Self-Check: PASSED

All created files exist on disk (`bot/options/{__init__,schema,config,strategy}.py`,
`rules_options.json`, `tests/options/{__init__,conftest,test_config,test_strategy}.py`)
and all three commits (`14d9ab5`, `b5f1c85`, `10aef4e`) are present in
`git log` on `develop`.

Plan verification commands, all passing:
- `python3 -m pytest -q` → 844 passed, 1 skipped
- migration probe → `6 [... 'option_legs', 'option_positions' ...]`
- config probe → `15 iron_condor 0.16 21`
- `git diff` on `bot/state/migrations.py` → 89 insertions, 1 deletion (the
  `CURRENT_VERSION` line); migrations 0001–0005 byte-identical
- no `moomoo`/`bot.gateway` import under `bot/options`; no I/O in `strategy.py`

## Next wave

W2 — data + persistence: `gateway.py` +3 async methods (`get_stock_ids`,
`screen_options`, `get_option_positions`) and `bot/options/store.py`
(`OptionsStore(StateStore)`) against the v6 tables landed here.
