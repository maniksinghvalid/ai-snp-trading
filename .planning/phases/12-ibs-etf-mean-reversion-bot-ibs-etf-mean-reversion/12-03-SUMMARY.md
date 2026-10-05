---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 03
subsystem: shared-infra
tags: [logging, calendar, sqlite-migration, test-isolation, ibs]
requires: []
provides:
  - "configure_logging(log_dir, level, *, log_name='bot.log', force=False)"
  - "bot.scanner.calendar.trading_days_between(start_exclusive, end_inclusive) -> List[date]"
  - "migration 0008: ibs_positions, ibs_trades, ibs_orders, ux_ibs_positions_active_code; CURRENT_VERSION = 8"
  - "tests/conftest.py session-autouse _isolate_audit_log"
affects: [later bot/ibs plans (store, service, main)]
tech-stack:
  added: []
  patterns: ["additive-only shared-file changes; kw-only params survive the conftest __defaults__ patch"]
key-files:
  created: []
  modified:
    - bot/safety/logger.py
    - bot/scanner/calendar.py
    - bot/state/migrations.py
    - tests/safety/test_logger.py
    - tests/scanner/test_calendar.py
    - tests/state/test_migrations.py
    - tests/safety/test_audit_log.py
    - tests/conftest.py
key-decisions:
  - "IBS tables are ibs_* only, so the equity startup_reconcile run by OpenDWatchdog sees empty positions/pending_intents"
  - "One-active-row-per-code (D-05) enforced as a partial UNIQUE index over OPENING/OPEN/CLOSING/NEEDS_ATTENTION"
  - "log_name/force are keyword-only so conftest's configure_logging.__defaults__ patch keeps working"
requirements-completed: [IBS-06, IBS-08]
duration: ~12 min
completed: 2026-10-04
---

# Phase 12 Plan 03: Shared-file additions for the IBS bot Summary

Four additive shared-file changes: per-bot log file name with `force` re-targeting, a NYSE `trading_days_between` helper, migration 0008 with the `ibs_*` tables and an active-code unique index, and session-wide audit-log isolation for tests.

## Tasks

| Task | Commits | Notes |
|------|---------|-------|
| 1. log_name/force + trading_days_between | f86af7e (RED), 7f586a7 (GREEN) | 7 new tests |
| 2. Migration 0008 + audit isolation | 0b46e0f (RED), d53318c (GREEN) | 9 new tests; two `== 7` assertions bumped to `== 8` |

## Verification

- Full suite: 1490 passed, 1 skipped (baseline 1474 + 16 new), zero regressions.
- Diff of `bot/state/migrations.py` removes exactly one line (`CURRENT_VERSION = 7`); migrations 0001-0007 untouched.
- `_migration_0008` uses `conn.execute` per statement (no executescript), IF NOT EXISTS throughout; v7 to v8 upgrade test keeps an existing option_positions row.

## Deviations from Plan

None - plan executed as written.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-12-09, T-12-04b, T-12-04c, T-12-06a, T-12-05a implemented as planned.

## Self-Check: PASSED

All four task commits exist; the modified files are present; the full suite is green.
