---
phase: 05-service-orchestration-and-reliability
plan: "02"
subsystem: service/watchdog
tags: [watchdog, reconnect, backoff, entries-guard, telegram-alert, svc-02]

dependency_graph:
  requires:
    - 05-00  # MoomooGateway.get_global_state(), StrategyConfig watchdog_* fields
    - 05-01  # TradingBot._entries_enabled, bot/_watchdog injection point
    - 05-03  # TelegramAlerter.send() fire-and-forget
  provides:
    - OpenDWatchdog (bot/service/watchdog.py)
    - Watchdog wired into TradingBot.run() on shared event loop
  affects:
    - bot/main.py  # constructs OpenDWatchdog, injects into bot
    - bot/service/bot.py  # TradingBot.run() launches + cancels watchdog task

tech_stack:
  added: []
  patterns:
    - reconciliation_loop shape (while True / sleep / CancelledError reraise / log-not-fatal)
    - asyncio.create_task fire-and-forget alert dispatch (D-12)
    - run_in_executor for synchronous gateway.connect() call (D-02)
    - Fixed-template alert text (no credentials / raw exceptions — T-05-02-04)

key_files:
  created:
    - bot/service/watchdog.py
  modified:
    - bot/main.py
    - bot/service/bot.py
    - tests/service/test_watchdog.py

decisions:
  - "_on_disconnect spawns _reconnect_loop as a create_task (non-blocking); _check_once detects the reconnect directly when called again — avoids double-_on_reconnect if both code paths fire simultaneously"
  - "Watchdog injected into TradingBot post-construction via bot._watchdog = watchdog (watchdog constructor needs bot ref); avoids circular dependency at construction time"
  - "xfail markers removed from test_watchdog.py — tests now PASSED (not XPASS)"

metrics:
  duration_minutes: 12
  completed_date: "2026-06-25"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 3
---

# Phase 05 Plan 02: OpenDWatchdog (SVC-02) Summary

**One-liner:** OpenDWatchdog polls get_global_state() every 60s, pauses entries on disconnect (D-09), reconnects with capped exponential backoff (D-10), re-runs startup_reconcile before re-enabling entries (D-11), and fires fixed-template Telegram alerts on both events (D-12).

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | OpenDWatchdog — poll loop, disconnect/reconnect, backoff, alert | 6d60e55 | bot/service/watchdog.py (created), tests/service/test_watchdog.py (xfail removed) |
| 2 | Wire OpenDWatchdog into bot/main.py + TradingBot.run() | d42c6fb | bot/main.py, bot/service/bot.py |

## What Was Built

### Task 1: bot/service/watchdog.py — OpenDWatchdog

`OpenDWatchdog.__init__(gateway, bot, alerter, cfg)` stores injected refs; `self._connected = True` (optimistic — readiness gate already confirmed connection before `run()` starts).

**`run()`** — mirrors the `reconciliation_loop` shape from `bot/gateway/gateway.py`: `while True` / `await asyncio.sleep(poll_interval)` / `try _check_once() except CancelledError: raise except Exception: log`.

**`_check_once()`** — reads `state = await gateway.get_global_state()`; detects `connected → disconnected` and `disconnected → connected` transitions.

**`_on_disconnect()`** — sets `bot._entries_enabled = False` (D-09, exits NOT disabled), dispatches fixed-template alert via `asyncio.create_task(alerter.send(...))` (D-12), starts `_reconnect_loop` via `create_task` (non-blocking).

**`_reconnect_loop()`** — exponential backoff: `delay = cfg.watchdog_reconnect_initial_s`; on failure `delay = min(delay*2, cfg.watchdog_reconnect_cap_s)` (D-10). `gateway.connect()` (sync) offloaded via `run_in_executor(None, gateway.connect)` (D-02). On success calls `_on_reconnect()` and breaks.

**`_on_reconnect()`** — D-11 ordering enforced: (1) `await gateway.startup_reconcile(store, position_manager)`, (2) re-subscribe active watchlist codes, (3) `bot._entries_enabled = True`. Then dispatches reconnect alert via `create_task` (D-12). Both lifecycle events write an audit entry via `append_audit`.

### Task 2: Wiring

**bot/main.py** — imports `OpenDWatchdog`; constructs it after `TradingBot` (watchdog needs bot ref); sets `bot._watchdog = watchdog`.

**bot/service/bot.py TradingBot.run()** — launches `asyncio.create_task(self._watchdog.run())` after D-08 readiness gate and `scheduler.start()` (single loop, Pitfall 3). In `finally` block cancels the watchdog task and awaits it (CancelledError swallowed), then calls `_shutdown()`.

## Verification Results

```
pytest tests/service/test_watchdog.py -q
2 passed in 0.05s

pytest -q (full suite)
379 passed, 1 skipped, 2 xfailed, 10 xpassed in 4.48s
```

No regressions. Both `test_disconnect_disables_entries` and `test_reconnect_reenables_entries` are PASSED (xfail markers removed).

## Threat Mitigations Applied

| Threat | Mitigation |
|--------|-----------|
| T-05-02-01: entries placed during OpenD loss | `_on_disconnect` sets `bot._entries_enabled=False` within one poll cycle before any reconnect attempt |
| T-05-02-02: stale state after reconnect | `_on_reconnect` re-runs `startup_reconcile` (broker truth wins) + re-subscribe BEFORE re-enabling entries |
| T-05-02-03: reconnect storm hammers OpenD | Capped exponential backoff (`initial_s → cap_s` from `rules.json`) — poll cadence unchanged |
| T-05-02-04: alert leaks internals | Alert text is a fixed template — no token, no raw exception, no credentials interpolated |
| T-05-02-05: exit dropped during outage | Watchdog toggles ONLY `_entries_enabled` — exits stay armed |

## Deviations from Plan

None — plan executed exactly as written. The only minor addition was removing the `xfail` markers from `test_watchdog.py` to surface the tests as PASSED (the plan said "turn green" which required removing the stubs' xfail decorators).

## Known Stubs

None. The implementation is fully wired. `gateway.startup_reconcile` and `gateway.subscribe` are pre-existing methods from 05-00.

## Self-Check: PASSED

- `bot/service/watchdog.py` exists and contains `class OpenDWatchdog` (167 lines)
- `bot/main.py` imports and constructs `OpenDWatchdog`
- `bot/service/bot.py` launches watchdog via `create_task` and cancels in finally
- Commits 6d60e55 and d42c6fb verified in git log
- Full pytest suite: 379 passed, no regressions
