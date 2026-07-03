---
phase: 07-strategy-optimization
plan: "01"
subsystem: database
tags: [sqlite, migrations, state-store, config, jsonschema, rvol-tod, circuit-breaker]

# Dependency graph
requires:
  - phase: 06.2-code-review-remediation
    provides: StateStore guarded-methods pattern, StrategyConfig dataclass, migration runner WR-03 pattern
provides:
  - Migration 0005 (tod_baselines table + broker_stop_order_id column on positions)
  - StateStore.get_tod_baseline / upsert_tod_baselines (TOD baseline CRUD)
  - StateStore.get/set/clear_circuit_breaker_date (circuit-breaker meta persistence)
  - StrategyConfig fields daily_circuit_breaker_r, use_broker_stop_orders, rvol_tod_lookback_days
  - rules.json keys risk.daily_circuit_breaker_r, execution.use_broker_stop_orders, intraday_filters.I3_rvol_tod_lookback_days
affects:
  - 07-02 (RVOL-TOD gate — reads tod_baselines via get_tod_baseline + upsert_tod_baselines from scanner)
  - 07-03 (broker stop order — reads use_broker_stop_orders; stores broker_stop_order_id on positions)
  - 07-05 (circuit breaker — reads/sets circuit_breaker_date + reads daily_circuit_breaker_r)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Callable migration + PRAGMA table_info guard (WR-03): idempotent ALTER TABLE pattern from _migration_0002/0004"
    - "INSERT OR REPLACE batch upsert in executemany for TOD baseline rows"
    - "meta table as key-value store for circuit-breaker trip date (get/set/delete pattern)"
    - "Optional config fields with .get() defaults in loader — no strategy literal in feature code (CFG-01)"

key-files:
  created: []
  modified:
    - bot/state/migrations.py
    - bot/state/store.py
    - bot/config/loader.py
    - bot/config/schema.py
    - rules.json
    - tests/state/test_migrations.py
    - tests/state/test_store.py
    - tests/config/test_loader.py

key-decisions:
  - "INSERT OR REPLACE (not ON CONFLICT DO UPDATE) for upsert_tod_baselines: simpler batch upsert since full-row replacement is acceptable (no partial-update needed for baseline rows)"
  - "Three new config keys are optional in schema (not in required lists) so older configs without them still validate — safe defaults applied by loader"
  - "Optional[str] return type for get_circuit_breaker_date: None signals no trip, string is the ET date; caller compares to today to detect stale dates"

patterns-established:
  - "Phase 7 callable migration pattern: CREATE TABLE IF NOT EXISTS + PRAGMA table_info guard on ALTER — D-08 compliant, WR-03 atomic"
  - "Phase 7 store method pattern: with self._lock: + parameterized SQL (?) — T-07-02 SQL injection prevention"
  - "Phase 7 config pattern: StrategyConfig field with float/bool/int default + .get() in loader + typed schema property — CFG-01 no-hardcode rule"

requirements-completed: [SIG-RVOL-TOD, RISK-CIRCUIT, RISK-TICK-STOP]

# Metrics
duration: 65min
completed: 2026-07-03
---

# Phase 07 Plan 01: Data + Config Foundation Summary

**Migration 0005 (tod_baselines + broker_stop_order_id), five lock-guarded StateStore methods, and three config-driven thresholds (daily_circuit_breaker_r / use_broker_stop_orders / rvol_tod_lookback_days) provide the shared substrate for all Phase 7 feature plans**

## Performance

- **Duration:** 65 min
- **Started:** 2026-07-03T22:00:00Z
- **Completed:** 2026-07-03T23:05:00Z
- **Tasks:** 3 (each with TDD RED + GREEN commits)
- **Files modified:** 8

## Accomplishments

- Migration 0005 adds the `tod_baselines` table (composite PK on scan_date/code/time_bucket) and `broker_stop_order_id` TEXT column on `positions` — both idempotent via CREATE TABLE IF NOT EXISTS + PRAGMA table_info guard (WR-03)
- Five new StateStore methods: `get_tod_baseline` / `upsert_tod_baselines` for TOD baseline CRUD and `get/set/clear_circuit_breaker_date` for durable circuit-breaker persistence — all lock-guarded with parameterized SQL
- Three Phase 7 config keys in `rules.json` + `schema.py` + `StrategyConfig` with safe defaults; malformed types fail closed via jsonschema (T-07-01 mitigated)
- 535 tests pass (up from 514 baseline); 21 new tests across migrations / store / loader suites

## Task Commits

Each task used TDD (RED then GREEN):

1. **Task 1 RED: migration 0005 failing tests** — `6f1453c` (test)
2. **Task 1 GREEN: migration 0005 implementation** — `2a510ad` (feat)
3. **Task 2 RED: StateStore accessor failing tests** — `ed9a2b7` (test)
4. **Task 2 GREEN: StateStore accessor implementation** — `073149a` (feat)
5. **Task 3 RED: Phase 7 config key failing tests** — `f89b1c0` (test)
6. **Task 3 GREEN: Phase 7 config key implementation** — `cd601f3` (feat)

## Files Created/Modified

- `bot/state/migrations.py` — Added `_POSITIONS_0005_COLUMNS`, `_migration_0005` function, appended to `MIGRATIONS` list, `CURRENT_VERSION` bumped 4 → 5
- `bot/state/store.py` — Added `Optional` import, five new StateStore methods (TOD baseline CRUD + circuit-breaker meta persistence)
- `bot/config/loader.py` — Added `daily_circuit_breaker_r`, `use_broker_stop_orders`, `rvol_tod_lookback_days` to `StrategyConfig` dataclass and mapping block
- `bot/config/schema.py` — Added typed schema properties for all 3 new keys under their respective sections (optional, not required)
- `rules.json` — Added `risk.daily_circuit_breaker_r=2.0`, `execution.use_broker_stop_orders=true`, `intraday_filters.I3_rvol_tod_lookback_days=14`
- `tests/state/test_migrations.py` — Added `TestMigration0005FreshDb` (5 tests) and `TestMigration0005Idempotency` (2 tests)
- `tests/state/test_store.py` — Added `TestTodBaselines` (4 tests) and `TestCircuitBreaker` (4 tests) including close/reopen persistence test (D-07)
- `tests/config/test_loader.py` — Added `TestPhase7ConfigKeys` (6 tests): real rules.json assertions + wrong-type ConfigError + absent-keys defaults

## Decisions Made

- **INSERT OR REPLACE for TOD baselines**: The `upsert_tod_baselines` method uses `executemany` with `INSERT OR REPLACE` (simpler than `ON CONFLICT DO UPDATE` when replacing the entire row is acceptable — no partial-update needed for baseline rows)
- **Optional schema properties**: The 3 new keys are declared in schema `properties` but not added to `required` lists — older configs without them validate cleanly; the loader provides safe defaults via `.get()`
- **`Optional[str]` return for `get_circuit_breaker_date`**: `None` signals no trip; a non-None date string is compared by the caller to detect and auto-reset stale prior-session dates (D-07)

## Deviations from Plan

None — plan executed exactly as written. The PATTERNS.md bodies were followed verbatim for all five store methods and the migration. The merge of the local `develop` branch into the worktree was needed because the worktree was branched from `origin/develop` (ec826eb) before the Phase 7 planning commits landed locally (80af923) — this is an expected worktree setup detail, not a plan deviation.

## Issues Encountered

- Worktree was initialized from `origin/develop` (ec826eb) rather than the local `develop` branch (80af923) that contained the Phase 7 plan files and PATTERNS.md. Resolved by fast-forward merging `develop` into the worktree branch before execution — all 5 planning commits applied cleanly with no conflicts.

## Known Stubs

None — all new code is fully functional. No placeholder values, hardcoded empties, or TODO markers introduced.

## Threat Flags

None — no new external network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond those catalogued in the plan's `<threat_model>`. SQL injection mitigation (T-07-02) and config type validation (T-07-01) are both implemented as specified.

## TDD Gate Compliance

All three tasks followed the RED → GREEN cycle:
1. RED commit (`test(07-01): ...`) verified to fail before implementation
2. GREEN commit (`feat(07-01): ...`) verified to make all new tests pass
3. Full regression suite (535 tests) passes after all three GREEN commits

## Next Phase Readiness

- Plan 07-02 (RVOL-TOD data path: `download_intraday_5m` + `_compute_tod_baselines` in scanner) can proceed immediately — `upsert_tod_baselines` and `rvol_tod_lookback_days` are ready
- Plan 07-03 (broker-side stop order: `place_stop_order` in gateway, `broker_stop_order_id` in manager) can proceed immediately — `broker_stop_order_id` column and `use_broker_stop_orders` config flag are ready
- Plan 07-05 (circuit breaker in signal_engine) can proceed immediately — all three circuit-breaker meta methods and `daily_circuit_breaker_r` config field are ready

---
*Phase: 07-strategy-optimization*
*Completed: 2026-07-03*

## Self-Check: PASSED
