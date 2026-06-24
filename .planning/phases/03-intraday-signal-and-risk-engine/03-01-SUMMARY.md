---
phase: 03-intraday-signal-and-risk-engine
plan: "01"
subsystem: signal-risk-foundation
tags: [signal, bar-aggregator, risk, events, migration, gateway, SIG-02]
dependency_graph:
  requires:
    - bot/state/migrations.py (run_migrations, CURRENT_VERSION — extended here)
    - bot/gateway/gateway.py (MoomooGateway — extended here)
    - bot/safety/logger.py (get_logger)
  provides:
    - bot/signal/__init__.py (new package)
    - bot/signal/events.py (BarEvent, SignalEvent dataclasses)
    - bot/signal/bar_aggregator.py (BarAggregator CurKlineHandlerBase subclass)
    - bot/risk/__init__.py (new package)
    - bot/risk/events.py (OrderIntent dataclass)
    - bot/state/migrations.py (_migration_0003, CURRENT_VERSION=3)
    - bot/gateway/gateway.py (get_market_snapshot async method)
  affects:
    - tests/signal/ (new package + test stubs + bar_aggregator tests)
    - tests/risk/ (new package + test stubs)
    - tests/gateway/test_gateway.py (get_market_snapshot tests)
    - tests/state/test_migrations.py (migration 0003 tests + version assertions updated)
tech_stack:
  added: []
  patterns:
    - CurKlineHandlerBase subclass with timestamp-advance bar-close detection (SIG-02)
    - asyncio.run_coroutine_threadsafe SDK→asyncio thread bridge (non-blocking)
    - callable migration pattern with executescript for idempotent CREATE TABLE IF NOT EXISTS
    - run_in_executor async wrapper for synchronous SDK calls
key_files:
  created:
    - bot/signal/__init__.py
    - bot/signal/events.py
    - bot/signal/bar_aggregator.py
    - bot/risk/__init__.py
    - bot/risk/events.py
    - tests/signal/__init__.py
    - tests/signal/test_bar_aggregator.py
    - tests/signal/test_signal_engine.py
    - tests/risk/__init__.py
    - tests/risk/test_risk_engine.py
  modified:
    - bot/state/migrations.py (added _migration_0003, CURRENT_VERSION=3)
    - bot/gateway/gateway.py (added get_market_snapshot method)
    - tests/gateway/test_gateway.py (added 9 stubs + 3 real snapshot tests)
    - tests/state/test_migrations.py (added migration 0003 tests; fixed version assertions)
decisions:
  - "SIG-02: BarAggregator fires on_bar_closed exclusively on time_key advance (never mid-bar)"
  - "Pitfall 1: _seen_time_keys dedup set survives SDK reconnects; reset only at reset_session()"
  - "Pitfall 3: _lod tracks session running-min from first K_5M bar, not individual bar low"
  - "Thread bridge: asyncio.run_coroutine_threadsafe chosen (not call_soon_threadsafe) because on_bar_closed is a coroutine"
  - "get_market_snapshot returns raw (ret, data) tuple — interpretation-free gateway read"
  - "Migration 0003 uses executescript inside callable to issue CREATE TABLE IF NOT EXISTS idempotently"
metrics:
  duration_seconds: 642
  completed_date: "2026-06-24"
  tasks_completed: 3
  files_created: 10
  files_modified: 4
---

# Phase 03 Plan 01: Wave 0 Foundation — Signal/Risk Packages, BarAggregator, Migration 0003 Summary

**One-liner:** Establishes bot/signal and bot/risk packages with typed dataclasses, adds BarAggregator (CurKlineHandlerBase subclass with timestamp-advance bar-close detection, session dedup, HOD/LOD tracking, SDK→asyncio bridge), migration 0003 (daily_trade_count + pending_intents tables), MoomooGateway.get_market_snapshot(), and Wave 0 collectible test scaffolds for all three Phase 3 slices.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Wave 0 scaffold — packages, events, migration 0003, test stubs | 5e2d1a9 | bot/signal/events.py, bot/risk/events.py, bot/state/migrations.py, tests/signal/, tests/risk/ |
| 2 | BarAggregator — bar-close detection, HOD/LOD, asyncio bridge | 91f6e73 | bot/signal/bar_aggregator.py, tests/signal/test_bar_aggregator.py |
| 3 | MoomooGateway.get_market_snapshot() | 3bdd216 | bot/gateway/gateway.py, tests/gateway/test_gateway.py |

## Verification Results

- `pytest tests/signal/test_bar_aggregator.py -x -q` → **5 passed** (SIG-02 mid-bar, reconnect dedup, HOD/LOD, malformed row)
- Migration 0003 idempotency command → **migration_0003_ok** (user_version=3, both tables present after double-run)
- `pytest tests/state/test_migrations.py -x -q` → **31 passed** (existing 0001/0002 tests still pass; 0003 tests added)
- `pytest tests/signal/ tests/risk/ --collect-only -q` → **27 tests collected** (all Nyquist stub names present)
- `pytest tests/ -x -q` → **286 passed, 28 skipped** (no regression in Phase 1/2 suites; 28 skips are Wave 0 stubs for 03-02/03-03)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed existing test_migrations.py assertions after CURRENT_VERSION bump**
- **Found during:** Task 1 verification
- **Issue:** Four existing tests in `TestMigration0002FreshDb`, `TestMigration0002Idempotency`, `TestUpgradeFromV1Db`, and `TestMigration0002PartialApplication` had hardcoded `assert version == 2`. Adding migration 0003 bumps CURRENT_VERSION to 3, making these fail.
- **Fix:** Updated all four assertions to use `CURRENT_VERSION` instead of the literal `2`. Test docstrings note the history.
- **Files modified:** tests/state/test_migrations.py
- **Commit:** 5e2d1a9 (included in Task 1 commit)

**2. [Rule 1 - Bug] Redesigned test_no_double_fire_on_reconnect to match actual SDK reconnect semantics**
- **Found during:** Task 2 TDD RED phase
- **Issue:** Initial test scenario (push bar_a, bar_b, reconnect re-push bar_a then bar_b) produced unexpected results. The SDK re-pushes the MOST RECENTLY CACHED bar (bar_b), not the previously closed bar. Pushing bar_a after bar_b had advanced `_last_time_key` to bar_b caused a new close of bar_b (legitimately not yet deduped).
- **Fix:** Redesigned test to correctly simulate is_first_push=True reconnect: the SDK re-pushes the current bar (same time_key as _last_time_key → mid-bar guard fires → no callback). Test now verifies the correct dedup invariant at two reconnect points.
- **Files modified:** tests/signal/test_bar_aggregator.py
- **Commit:** 91f6e73

## Key Decisions Made

1. **SIG-02 bar-close detection via timestamp advance:** BarAggregator fires `on_bar_closed` exclusively when `time_key` advances from the stored `_last_time_key`. Mid-bar ticks (same `time_key`) are silently ignored.

2. **Dual-guard reconnect dedup:** `_seen_time_keys` per-code set persists across SDK reconnects for the entire session lifetime. `reset_session()` clears it; SDK reconnect does NOT. This prevents the is_first_push=True re-push from double-firing a bar already evaluated.

3. **LOD = session running-min from first K_5M bar (Open-Q3 RESOLVED / Pitfall 3):** `_lod[code]` is updated on every push from the very first bar and never reset per-bar. `BarEvent.lod` carries this session running-min, which is what flows into `compute_initial_stop(lod)` in RiskEngine.

4. **asyncio.run_coroutine_threadsafe chosen over call_soon_threadsafe:** The callback is a coroutine; `run_coroutine_threadsafe` is the correct primitive. Fire-and-forget (no `.result()` wait) keeps the SDK thread unblocked (T-03-03).

5. **get_market_snapshot is interpretation-free:** The gateway method returns the raw `(ret, data)` tuple without calling `_check_ret` or reading any fields. D-01/D-03 interpretation (reading `pre_high_price`, excluding zero values, freezing) lives in the 03-02 `fetch_premarket_highs` helper.

6. **Migration 0003 uses callable with executescript:** `CREATE TABLE IF NOT EXISTS` is inherently idempotent, so no PRAGMA table_info guard needed (unlike 0002's ALTER TABLE). The callable is used (not SQL string) to avoid `executescript`'s implicit COMMIT racing the `PRAGMA user_version` bump (WR-03).

## Artifact Verification

All `must_haves.artifacts` verified:

| Artifact | Check | Status |
|----------|-------|--------|
| bot/signal/bar_aggregator.py | class BarAggregator, 239 lines (min 80) | PASS |
| bot/signal/events.py | class BarEvent with lod field | PASS |
| bot/risk/events.py | class OrderIntent with stop_price, quantity, intent_id | PASS |
| bot/state/migrations.py | def _migration_0003, CURRENT_VERSION = 3 | PASS |
| bot/gateway/gateway.py | async def get_market_snapshot | PASS |
| tests/signal/test_bar_aggregator.py | test_no_double_fire_on_reconnect | PASS |

## Self-Check: PASSED

Files exist:
- bot/signal/__init__.py ✓
- bot/signal/events.py ✓
- bot/signal/bar_aggregator.py ✓
- bot/risk/__init__.py ✓
- bot/risk/events.py ✓
- tests/signal/__init__.py ✓
- tests/signal/test_bar_aggregator.py ✓
- tests/signal/test_signal_engine.py ✓
- tests/risk/__init__.py ✓
- tests/risk/test_risk_engine.py ✓

Commits exist:
- 5e2d1a9 (Task 1) ✓
- 91f6e73 (Task 2) ✓
- 3bdd216 (Task 3) ✓
