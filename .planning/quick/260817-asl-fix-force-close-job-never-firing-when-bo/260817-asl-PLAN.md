---
phase: quick-260817-asl
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - bot/service/bot.py
  - tests/service/test_bot.py
autonomous: true
requirements: [SVC-01, POS-04]

must_haves:
  truths:
    - "A TradingBot (re)started on a trading day after premarket_scan_et but before today's force-close time has its force_close job scheduled at today's calendar-aware force-close time (get_force_close_time_et), not the 2099-01-01 placeholder"
    - "_job_premarket_scan still reschedules force_close (existing half-day test stays green)"
  artifacts:
    - path: "bot/service/bot.py"
      provides: "_reschedule_force_close(today) helper shared by _job_premarket_scan and startup arm in _register_jobs"
      contains: "def _reschedule_force_close"
    - path: "tests/service/test_bot.py"
      provides: "regression test: _register_jobs at 10:17 ET on a trading day → force_close next_run_time == today 15:51 ET"
      contains: "test_register_jobs_arms_force_close_when_started_after_premarket_scan"
---

<objective>
Bug (observed 2026-08-17: bot killed 14:10Z, restarted 14:17Z = 10:17 ET): `force_close`
is registered as a DateTrigger parked at 2099-01-01 and only `_job_premarket_scan`
(08:30 ET) reschedules it to `get_force_close_time_et(today)`. A restart after 08:30 ET
therefore never arms today's force close; intraday positions would not be closed at
15:51 ET (no other caller of force_close_all).
</objective>

<tasks>

## Task 1 — startup arm + helper (bot/service/bot.py)
- Factor the reschedule block in `_job_premarket_scan` into `_reschedule_force_close(today)`
  (same DateTrigger/logging/try-except).
- In `_register_jobs`, right after `add_job(... id="force_close")`: if `is_trading_day(today)`
  and `now_et().time() < get_force_close_time_et(today)` → `_reschedule_force_close(today)`.
  `_register_jobs` is already called by `run()` before `scheduler.start()`, so no new wiring.
- Verify: `python3 -m pytest tests/service -q`

## Task 2 — regression test (tests/service/test_bot.py)
- `test_register_jobs_arms_force_close_when_started_after_premarket_scan`: patch `now_et`
  to 2026-08-17 10:17 ET (real calendar: trading day, normal close), call `_register_jobs()`,
  assert `get_job("force_close").next_run_time` == 2026-08-17 15:51 ET.
- Existing half-day test: replace MagicMock `now_et` with a real 13:00 ET datetime so the
  startup arm is a no-op there and the test still proves the premarket-scan path.
- Verify: `python3 -m pytest tests/service/test_bot.py -q`

</tasks>
