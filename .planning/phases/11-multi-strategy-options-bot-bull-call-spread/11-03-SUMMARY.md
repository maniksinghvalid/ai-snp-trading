---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 03
subsystem: options-bot-data-layer
tags: [sqlite, migrations, options, read-only-sqlite, fail-closed]

# Dependency graph
requires:
  - phase: 08-options-premium-selling
    provides: OptionsStore, _migration_0006 (option_positions/option_legs), guarded-ALTER migration pattern
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 02
    provides: manage_decision_debit's debit=-credit_per_spread sign convention consumed by store's signed credit_per_spread column
provides:
  - "_migration_0007: option_positions.strategy_name TEXT NOT NULL DEFAULT 'tasty_credit_spreads' (guarded, idempotent); CURRENT_VERSION 7"
  - "OptionsStore.insert_option_position omit-None column build (so an omitted/None strategy_name falls back to the SQL default instead of a NOT NULL violation)"
  - "OptionsStore.count_opened_on(date_iso, strategy_name=None) — per-strategy or all-strategy daily entry counting"
  - "bot/options/universe.py: read_equity_watchlist(db_path, scan_date_iso, cap=20, timeout_s=5.0) — read-only, capped, fail-closed equity watchlist reader"
affects: [11-04, 11-05, 11-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Guarded idempotent ALTER TABLE via PRAGMA table_info existing-columns check, mirroring _migration_0002/0004/0005 — new schema objects always land in a new migration, never edit a shipped one (D-08)"
    - "insert builds its column list dynamically (omit-None) rather than binding an explicit NULL, so a NOT NULL DEFAULT column receives the SQL default when the caller omits or passes None for it"
    - "Read-only cross-process SQLite access via a single mode=ro URI connect, zero import of the writer's own state module — isolation enforced structurally (AST-tested), not just by convention"

key-files:
  created:
    - bot/options/universe.py
    - tests/options/test_universe.py
  modified:
    - bot/state/migrations.py
    - bot/options/store.py
    - tests/state/test_migrations.py
    - tests/options/test_store.py

key-decisions:
  - "insert_option_position's cols/values list is now built as [c for c in _POSITION_COLUMNS if pos.get(c) is not None] — this is a behavior-preserving generalization (every other nullable column still lands NULL, since omitting a column from an INSERT with no DEFAULT also yields NULL) rather than a special case just for strategy_name"
  - "count_opened_on's strategy_name filter is an added optional keyword parameter (default None reproduces the exact pre-existing SQL), not a new method, keeping every existing call site untouched"
  - "bot/options/universe.py imports only stdlib sqlite3/pathlib and bot.safety.logger — never bot.state — enforced by an AST-walk test (test_module_does_not_import_equity_state), not just code review"

patterns-established:
  - "Fail-closed cross-process read: catch sqlite3.Error (base class) around the connect+query, return [] with a structured warning log — no crash path, no partial-success path"

requirements-completed: [MSO-06, MSO-07]

# Metrics
duration: 8min
completed: 2026-09-24
---

# Phase 11 Plan 03: Data-Layer Foundations Summary

**Migration 0007 tags every option position with a strategy_name (default `tasty_credit_spreads`, omit-None insert, per-strategy entry counting), and a new read-only `bot/options/universe.py` reader delivers the equity bot's capped, rank-ordered premarket watchlist with zero write path into the equity DB.**

## Performance

- **Duration:** 8 min
- **Completed:** 2026-09-24T14:54:32Z
- **Tasks:** 2/2
- **Files modified:** 4 modified, 2 created

## Accomplishments
- `_migration_0007` adds `option_positions.strategy_name TEXT NOT NULL DEFAULT 'tasty_credit_spreads'` via a guarded, idempotent `ALTER TABLE` (PRAGMA table_info check); `CURRENT_VERSION` is now 7 == `len(MIGRATIONS)`; `_migration_0006` was not touched (D-08).
- `OptionsStore.insert_option_position` now builds its column/value list by omitting any `None`-valued field, so a caller that never mentions `strategy_name` (the existing UAT probe, existing tests) gets the SQL default instead of a `NOT NULL` violation; every other nullable column still lands `NULL` as before.
- `OptionsStore.count_opened_on(date_iso, strategy_name=None)` adds an optional per-strategy filter for the D-22 daily entry cap while leaving the unfiltered (all-strategies) call site byte-identical.
- New `bot/options/universe.py`: `read_equity_watchlist(db_path, scan_date_iso, cap=20, timeout_s=5.0)` opens the equity DB through a single `mode=ro` URI connection (the only `sqlite3.connect` in the module), returns today's `daily_scan` codes by `rank ASC` capped at 20, and fails closed to `[]` with a structured warning on every error path (missing file, locked DB, corrupt file, missing table, empty result) — never importing `bot.state.store.StateStore`.

## Task Commits

Each task was committed atomically:

1. **Task 1: `_migration_0007` strategy_name column + OptionsStore pass-through + per-strategy `count_opened_on`** - `c312dc0` (feat)
2. **Task 2: Read-only equity watchlist reader `bot/options/universe.py`** - `526838c` (feat)

_Both tasks were `tdd="true"`; tests and implementation landed together in each task's single commit, verified green before commit._

## Files Created/Modified
- `bot/state/migrations.py` - `_migration_0007` (new, guarded ALTER on `option_positions.strategy_name`); `MIGRATIONS` gains the 7th entry; `CURRENT_VERSION = 7`
- `bot/options/store.py` - `_POSITION_COLUMNS` gains `"strategy_name"`; `insert_option_position` omit-None column build; `count_opened_on(date_iso, strategy_name=None)`; module docstring updated
- `tests/state/test_migrations.py` - `TestMigration0007` (fresh-column/notnull/default via PRAGMA table_info, double-apply idempotence, v6-upgrade fills default); 3 pre-existing hard-coded `==6` asserts switched to `CURRENT_VERSION` with the established docstring precedent
- `tests/options/test_store.py` - 5 new tests: omit/None strategy_name default, explicit strategy_name round-trip, negative debit `credit_per_spread`/`max_loss_usd` exact round-trip, per-strategy `count_opened_on` filtering
- `bot/options/universe.py` (new) - `_connect_ro`, `read_equity_watchlist`, `_WATCHLIST_CAP = 20`
- `tests/options/test_universe.py` (new) - 11 tests against a real WAL-mode equity schema (via `bot.state.migrations.run_migrations`): rank ordering, 20-cap, scan-date isolation, empty/missing-file/locked/corrupt/missing-table fail-closed paths, WAL concurrent-write visibility, read-only enforcement, AST-based import-isolation guard

## Decisions Made
- `insert_option_position`'s omit-None generalization is the smaller diff than a `strategy_name`-specific special case, and it is provably behavior-preserving for every other nullable column (an omitted column with no `DEFAULT` still yields `NULL`, exactly as an explicit `NULL` bind would have).
- `count_opened_on` gained an optional keyword parameter rather than a second method — `None` is the exact pre-existing SQL, so zero existing call sites needed a change.
- The watchlist reader's isolation guarantee (D-17) is enforced by an `ast`-walk test, not just a code-review convention, so a future edit that accidentally imports `bot.state` fails CI immediately.

## Deviations from Plan

None — plan executed exactly as written. One in-flight self-correction: the migration header/docstring initially repeated the literal string `DEFAULT 'tasty_credit_spreads'` three times (once in the actual column declaration, twice in prose), which would have failed the acceptance-criteria grep requiring exactly one match; reworded the two prose occurrences to describe the same behavior without the literal substring before committing (doc wording only, zero behavior change — not a deviation-rule case).

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `strategy_name` is now on every `option_positions` row (default for legacy rows and omitting callers) and `count_opened_on` supports per-strategy filtering — ready for plan 11-05's service-layer dispatch (D-21/D-22) to consume both.
- `read_equity_watchlist` is ready for the `super_bull_call` entry-scan job (D-28) to call once `service.equity_state_db`-driven wiring lands in a later plan; it is currently unused by any caller (net-new module, zero integration risk to the running options bot).
- Full suite green: 1225 passed, 1 skipped (baseline 1206 passed / 1 skipped + 19 new tests: 8 store/migration + 11 universe — 0 new failures, 0 new skips). Quick suite (`tests/options tests/backtester/options`): 349 passed (baseline 333 + 16 of the 19 new tests; the 3 migration-only tests live in `tests/state`, outside the quick suite's scope).

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED

All created/modified files verified present on disk; both task commits (`c312dc0`, `526838c`) verified present in git log.
