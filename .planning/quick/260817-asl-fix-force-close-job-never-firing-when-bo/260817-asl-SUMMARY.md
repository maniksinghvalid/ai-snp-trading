---
phase: quick-260817-asl
status: complete
completed: 2026-08-17
commit: 8043243
tests: 962 passed, 1 skipped (was 961)
---

# Quick 260817-asl — force_close armed on mid-session (re)start

## What was wrong
`bot/service/bot.py::_register_jobs` registered `force_close` as a DateTrigger parked at
2099-01-01; only `_job_premarket_scan` (08:30 ET) rescheduled it to
`get_force_close_time_et(today)`. A (re)start after 08:30 ET on a trading day — observed
2026-08-17, killed 14:10Z / restarted 14:17Z (10:17 ET) — never armed today's force close,
so intraday positions would not have been closed at 15:51 ET (no other caller of
`force_close_all`).

## What changed (commit 8043243)
- `TradingBot._reschedule_force_close(today)` — the existing reschedule block factored out
  of `_job_premarket_scan` (same DateTrigger, log events, swallow-and-log).
- `_register_jobs`: right after `add_job(id="force_close")`, if `is_trading_day(today)` and
  `now_et().time() < get_force_close_time_et(today)` → `_reschedule_force_close(today)`.
  `run()` already calls `_register_jobs()` before `scheduler.start()`, so no new wiring.
- Test `tests/service/test_bot.py::test_register_jobs_arms_force_close_when_started_after_premarket_scan`:
  `now_et` patched to 2026-08-17 10:17 ET, real calendar → `force_close.next_run_time == 15:51 ET`.
- Existing half-day test now uses a real 13:00 ET `now_et` (past the 12:51 half-day close) so
  the startup arm is a no-op there and the test still proves the premarket-scan path.

## Deliberate limits
- Restart at/after the force-close time does not arm (spec); a restart at 15:53 with open
  positions relies on the operator. Dropping the `<` guard would let APScheduler's 300 s
  misfire grace fire it, at the cost of a "missed run" warning on every evening restart.
