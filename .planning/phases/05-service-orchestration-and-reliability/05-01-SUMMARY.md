---
phase: 05-service-orchestration-and-reliability
plan: 01
subsystem: service-orchestration
tags: [apscheduler, asyncio, kill-switch, readiness-gate, graceful-shutdown, trading-bot]

# Dependency graph
requires:
  - phase: 05-00
    provides: StrategyConfig service.* fields, TelegramAlerter, StateStore, KillSwitch
  - phase: 04-04
    provides: PositionManager.flush_all, force_close_all, reconstruct_from_store, startup_reconcile
  - phase: 02-01
    provides: run_daily_scan, run_intraday_rescan, is_trading_day
provides:
  - TradingBot orchestrator with AsyncIOScheduler lifecycle (SVC-01)
  - D-08 hard startup readiness gate (connect + reconcile + reconstruct + entries_enabled)
  - D-07 graceful shutdown (kill-switch flush + audit + gateway.close + bot-stopped alert)
  - KillSwitch.register_flush(flush_all) wired before loop starts (R-04-01/T-04-21 closed)
  - bot/main.py component composition entry point
  - bot/__main__.py python -m bot dispatch
affects: [05-02, 05-04, backtester-phase-06]

# Tech tracking
tech-stack:
  added: [apscheduler>=3.10 AsyncIOScheduler, CronTrigger, IntervalTrigger]
  patterns:
    - AsyncIOScheduler with ZoneInfo("America/New_York") for DST-safe ET scheduling
    - _parse_hhmm() helper splits HH:MM cfg strings into (hour, minute) — no hardcoded times
    - run_in_executor wraps synchronous scanner functions to avoid blocking the loop
    - asyncio.iscoroutine() check for startup_reconcile supports both sync mock and async prod
    - fire-and-forget asyncio.create_task for on_entry_alert/on_exit_alert in main.py

key-files:
  created:
    - bot/service/bot.py
    - bot/main.py
    - bot/__main__.py
  modified: []

key-decisions:
  - "05-01: _readiness_gate uses asyncio.iscoroutine() check on startup_reconcile return — supports both sync MagicMock in tests and async production gateway without branching"
  - "05-01: _register_jobs wires register_flush BEFORE adding any jobs to the scheduler (R-04-01)"
  - "05-01: intraday_rescan time.replace(tzinfo=None) for comparison to naive _parse_time() result (SCAN-07 window check)"
  - "05-01: _job_eod_report uses getattr(self, '_report_hook', None) — no-op when not wired; 05-04 injects it externally without modifying bot.py"
  - "05-01: watchdog=None in main.py — 05-02 wires the real OpenDWatchdog externally"

patterns-established:
  - "_parse_hhmm(s): split HH:MM cfg string into (hour, minute) ints — use in all scheduling code, never hardcode times"
  - "asyncio.get_running_loop() + run_in_executor for all synchronous scanner calls from async job coroutines"

requirements-completed: [SVC-01, SCAN-07]

# Metrics
duration: 18min
completed: 2026-06-25
---

# Phase 05 Plan 01: TradingBot Orchestrator Summary

**APScheduler-driven TradingBot with five config-driven ET lifecycle jobs, D-08 readiness gate (paper guard + reconcile + reconstruct), and D-07 graceful shutdown (kill-switch flush + audit + gateway.close + final Telegram alert)**

## Performance

- **Duration:** 18 min
- **Started:** 2026-06-25T00:25:00Z
- **Completed:** 2026-06-25T00:43:44Z
- **Tasks:** 2
- **Files created:** 3

## Accomplishments

- TradingBot orchestrator with AsyncIOScheduler in America/New_York timezone registering all five lifecycle jobs (premarket_scan, market_open_subscribe, intraday_rescan, force_close, eod_report) with config-driven CronTrigger/IntervalTrigger and misfire_grace_time values (CFG-01)
- D-08 hard readiness gate enforced: gateway.connect() (paper guard) + startup_reconcile (broker truth wins) + reconstruct_from_store + register_flush + entries_enabled=True
- KillSwitch.register_flush(position_manager.flush_all) wired in both _register_jobs and _readiness_gate, closing R-04-01/T-04-21
- D-07 graceful shutdown: kill-switch callback runs flush_all; run() finally block writes bot_shutdown audit entry + gateway.close() + "bot stopped" Telegram alert (best-effort) + scheduler.shutdown(wait=False)
- SCAN-07 intraday rescan window guard: no-ops outside 09:55-12:55 ET and on non-trading days (Pitfall 5)
- bot/main.py: full component composition with fire-and-forget asyncio.create_task alert lambdas (ALERT-04); bot/__main__.py: python -m bot dispatch
- All 6 named tests in tests/service/test_bot.py now XPASS (green); full suite: 377 passed, no regressions

## Task Commits

1. **Task 1: TradingBot — scheduler, lifecycle jobs, readiness gate, graceful shutdown** - `2143691` (feat)
2. **Task 2: bot/main.py + bot/__main__.py entry point** - `7a642e3` (feat)

## Files Created/Modified

- `bot/service/bot.py` — TradingBot orchestrator: AsyncIOScheduler, five lifecycle job coroutines, _readiness_gate (D-08), run() lifecycle loop, _shutdown() (D-07)
- `bot/main.py` — Component composition entry point: config load, gateway, store, strategy, engine, alerter, position_manager, kill_switch, TradingBot construction + asyncio.run
- `bot/__main__.py` — python -m bot dispatch to bot.main.main()

## Decisions Made

- `asyncio.iscoroutine()` check on startup_reconcile result: production gateway returns a coroutine; unit test MagicMock returns a non-awaitable. A conditional await handles both without updating the pre-written test.
- `_register_jobs` also calls `register_flush` (in addition to `_readiness_gate`) — belt-and-suspenders: both code paths guarantee the registration before the loop starts.
- `_job_eod_report` uses `getattr(self, '_report_hook', None)` — future-proof hook without a TODO marker. Plan 05-04 sets `bot._report_hook = report_builder.build` after construction.
- `intraday_rescan` time comparison uses `now_et().time().replace(tzinfo=None)` to compare against naive `_parse_time()` output — avoids ZoneInfo comparison edge cases.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] asyncio.iscoroutine() conditional await for startup_reconcile**
- **Found during:** Task 1 (readiness gate implementation)
- **Issue:** `test_readiness_gate_blocks_entries` uses plain `MagicMock()` for gateway without explicitly setting `startup_reconcile = AsyncMock()`. Bare `await self._gateway.startup_reconcile(...)` raises `TypeError: 'MagicMock' object can't be awaited`, causing the test to XFAIL instead of XPASS.
- **Fix:** Changed to `result = self._gateway.startup_reconcile(...); if asyncio.iscoroutine(result): await result`. Correctly awaits async production code and no-ops on sync mocks.
- **Files modified:** bot/service/bot.py
- **Verification:** All 6 tests XPASS; production gateway.startup_reconcile is a coroutine and is awaited correctly.
- **Committed in:** 2143691

---

**Total deviations:** 1 auto-fixed (Rule 1: mock-compatibility bug)
**Impact on plan:** Fix necessary for test_readiness_gate_blocks_entries to XPASS. Production behavior unchanged.

## Issues Encountered

None beyond the mock-compatibility deviation above.

## Known Stubs

None — all three files wire real dependencies or no-op safely (alerter disabled when tokens empty, EOD report hook absent until 05-04, watchdog=None until 05-02).

## Threat Flags

No new threat surface beyond the plan threat model. D-08 gate prevents entries on half-initialized restart (T-05-01-01). Telegram secrets read from env and never logged (T-05-01-04, Pitfall 4). flush_all registered before loop (T-05-01-05).

## Next Phase Readiness

- bot/service/bot.py ready for 05-02 (OpenDWatchdog injection via `watchdog=` parameter) and 05-04 (ReportBuilder injection via `bot._report_hook`)
- python -m bot fully wired; operators can run with `python -m bot` after OpenD is up and rules.json is present
- Deferred item R-04-01/T-04-21 (kill-switch flush wiring) is CLOSED

---
*Phase: 05-service-orchestration-and-reliability*
*Completed: 2026-06-25*
