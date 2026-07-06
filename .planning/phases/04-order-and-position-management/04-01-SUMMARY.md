---
phase: "04-order-and-position-management"
plan: "01"
subsystem: "position-fsm"
tags: ["fsm", "position", "execution", "migration", "config", "scaffold"]
dependency_graph:
  requires: []
  provides:
    - "bot/execution/events.py FillEvent dataclass (EXEC-05 order_id-keyed fill)"
    - "bot/position/state.py PositionPhase enum + PositionState FSM (POS-01/02/03)"
    - "rules.json execution block (10 tunables: TTLs, buffers, retries, escalation)"
    - "bot/config/schema.py + loader.py execution fields in StrategyConfig"
    - "bot/state/migrations.py _migration_0004 + CURRENT_VERSION=4"
    - "bot/state/store.py upsert_position() + get_open_positions()"
    - "tests/execution/ + tests/position/ test packages with 10 failing stubs"
    - "tests/conftest.py make_fill_event + make_bar_event fixtures"
  affects:
    - "bot/config/loader.py — StrategyConfig extended with execution block"
    - "bot/state/migrations.py — MIGRATIONS list extended to length 4"
    - "bot/state/store.py — StateStore extended with position accessors"
    - "tests/conftest.py — minimal_rules + new fixtures"
    - "tests/config/test_loader.py — CANONICAL_RULES updated for schema"
tech_stack:
  added: []
  patterns:
    - "PositionPhase(str, Enum) — string-compatible enum for SQLite TEXT storage"
    - "evaluate_close() pure FSM — no broker/DB/asyncio; returns (action, qty) tuple"
    - "max(trail_stop, new_swing_low) — D-11 never-loosen-stop invariant"
    - "_migration_0004 callable pattern — idempotent PRAGMA table_info guard per column"
    - "upsert_position INSERT ... ON CONFLICT DO UPDATE — single write path for all transitions"
key_files:
  created:
    - "bot/execution/__init__.py"
    - "bot/execution/events.py"
    - "bot/position/__init__.py"
    - "bot/position/state.py"
    - "tests/execution/__init__.py"
    - "tests/execution/test_engine.py"
    - "tests/position/__init__.py"
    - "tests/position/test_fsm.py"
    - "tests/position/test_manager.py"
  modified:
    - "rules.json"
    - "bot/config/schema.py"
    - "bot/config/loader.py"
    - "bot/state/migrations.py"
    - "bot/state/store.py"
    - "tests/conftest.py"
    - "tests/config/test_loader.py"
decisions:
  - "FSM evaluate_close() returns (action_str, partial_qty) tuple — callers switch on string constants; pure, testable without importing moomoo"
  - "PositionPhase(str, Enum) — string values equal phase names for SQLite TEXT compatibility and direct comparisons"
  - "Rule 2 auto-fix: CANONICAL_RULES in test_loader.py updated to include execution block (schema now requires it)"
metrics:
  duration: "~7 minutes"
  completed: "2026-06-24"
  tasks: 2
  files: 16
---

# Phase 04 Plan 01: Wave-0 Scaffold + PositionState FSM Summary

**One-liner:** New bot/execution + bot/position packages with FillEvent, PositionState FSM (POS-01/02/03 close-based transitions, never-loosen trail), execution config block in rules.json, migration 0004 (entry_order_id/exit_order_id/avg_fill_price), and store accessors — all proven by 3 green FSM unit tests plus 7 red stubs for future plans.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wave-0 scaffold — packages, execution config, migration 0004, test stubs | 4eacaa8 | 15 files |
| 2 | PositionState FSM — POS-01/02/03 on-close transitions | 63a85fc | 2 files |

## Artifacts Produced

### New Packages

- **bot/execution/__init__.py** — package root (imports without moomoo-api)
- **bot/execution/events.py** — `FillEvent` dataclass; fields: order_id, intent_id, code, filled_qty, avg_fill_price, is_entry, fill_time; keyed by order_id (EXEC-05)
- **bot/position/__init__.py** — package root
- **bot/position/state.py** — `PositionPhase(str, Enum)` (AWAITING_FILL/ACTIVE/PARTIAL_TAKEN/BREAKEVEN/TRAILING/CLOSED) + `PositionState` FSM dataclass with `apply_entry_fill()` and `evaluate_close()` methods

### Config Changes

- **rules.json** — `execution` block added (10 tunables: entry_limit_buffer_usd=0.05, entry_ttl_seconds=20, entry_max_retries=2, entry_poll_interval_seconds=5, exit_limit_buffer_usd=0.05, exit_ttl_seconds=15, exit_escalation_step_usd=0.10, exit_escalation_cadence_seconds=10, force_close_escalation_step_usd=0.20, force_close_escalation_cadence_seconds=15)
- **bot/config/schema.py** — `"execution"` added to `SCHEMA["required"]` + matching properties block (all 10 fields {"type": "number"})
- **bot/config/loader.py** — 10 new `StrategyConfig` fields (entry_max_retries is `int`; the rest `float`); `load_strategy_config()` reads from `data.get("execution", {})`

### Migration

- **bot/state/migrations.py** — `_migration_0004(conn)` idempotent callable; adds entry_order_id TEXT, exit_order_id TEXT, avg_fill_price REAL to positions table via PRAGMA table_info guard; appended to MIGRATIONS; `CURRENT_VERSION` bumped to 4

### Store Accessors

- **bot/state/store.py** — `upsert_position(pos)`: INSERT...ON CONFLICT DO UPDATE with immediate commit (DB-first pattern, Pitfall G); `get_open_positions()`: SELECT WHERE phase != 'CLOSED' returning list of dicts

### Test Infrastructure

- **tests/execution/__init__.py**, **tests/position/__init__.py** — package roots
- **tests/execution/test_engine.py** — 5 stubs (EXEC-01..05), all RED
- **tests/position/test_fsm.py** — 3 tests implemented and GREEN (test_partial_profit_trigger, test_breakeven_trigger, test_trail_never_loosens)
- **tests/position/test_manager.py** — 2 stubs (POS-04/05), RED
- **tests/conftest.py** — `make_fill_event(**overrides)` + `make_bar_event(**overrides)` fixtures; `minimal_rules` extended with execution block

## Verification Results

```
pytest tests/position/ tests/execution/ -q
  3 passed, 7 failed (7 stubs intentionally red for 04-03/04-04)

pytest tests/config/ -q
  23 passed (all config tests green after execution block extension)

python3 -c "from bot.state.migrations import CURRENT_VERSION; assert CURRENT_VERSION==4"
  PASSED

python3 -c "import bot.execution.events, bot.position; from bot.execution.events import FillEvent"
  PASSED (no moomoo-api installed)

grep for 0.75/0.3333/1.0*R literals in state.py (AST check)
  PASSED — no hardcoded thresholds in code (comments/docstrings only)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Updated CANONICAL_RULES in test_loader.py with execution block**
- **Found during:** Task 1 verification (pytest tests/config/)
- **Issue:** `tests/config/test_loader.py::CANONICAL_RULES` lacked the `execution` block. After adding `"execution"` to `SCHEMA["required"]`, the schema validator correctly rejected the old test data — causing 16 existing config tests to fail.
- **Fix:** Added the execution block (10 keys with canonical defaults) to `CANONICAL_RULES` in `test_loader.py`. This is a correctness requirement: the test fixture must match the schema it is testing.
- **Files modified:** `tests/config/test_loader.py`
- **Commit:** 4eacaa8 (included in Task 1 commit)

## Known Stubs

The following test stubs are intentional and tracked for future plans:

| File | Test | Reason |
|------|------|--------|
| tests/execution/test_engine.py | test_entry_placed_simulate | Implemented in 04-03 (ExecutionEngine) |
| tests/execution/test_engine.py | test_no_market_orders | Implemented in 04-03 |
| tests/execution/test_engine.py | test_ttl_cancel_replace | Implemented in 04-03 |
| tests/execution/test_engine.py | test_duplicate_guard | Implemented in 04-04 |
| tests/execution/test_engine.py | test_fill_by_order_id | Implemented in 04-03 |
| tests/position/test_manager.py | test_force_close_half_day | Implemented in 04-04 |
| tests/position/test_manager.py | test_restart_reconciliation | Implemented in 04-04 |

These stubs are intentional scaffold per the Wave-0 plan — they provide the collection targets for the validation map and MUST remain red until their implementing plans (04-03/04-04) complete them.

## Threat Flags

No new threat surfaces beyond those in the plan's threat model. Mitigations confirmed implemented:

- **T-04-01** (trail_stop tamper): `max(trail_stop, new_swing_low)` in `evaluate_close()` + `test_trail_never_loosens` asserts the invariant including restart-resume simulation
- **T-04-02** (rules.json execution values): `schema.py` `"execution"` in `SCHEMA["required"]` + all 10 fields required in the execution sub-schema; validator rejects malformed/missing execution block at load time; no literal R-thresholds in `state.py`

## Self-Check: PASSED

- bot/execution/events.py: FOUND
- bot/position/state.py: FOUND
- bot/state/migrations.py _migration_0004: FOUND
- bot/state/store.py upsert_position: FOUND
- Commit 4eacaa8: FOUND (git log)
- Commit 63a85fc: FOUND (git log)
- 3 FSM tests GREEN: CONFIRMED (pytest output)
- 23 config tests GREEN: CONFIRMED (pytest output)
