---
phase: 05-service-orchestration-and-reliability
plan: 03
subsystem: service/alerter
tags: [telegram, alerts, stdlib, urllib, run_in_executor, fire-and-forget, ALERT-01, ALERT-02, ALERT-03, ALERT-04]
dependency_graph:
  requires:
    - 05-00  # RED stubs for test_alerter.py
  provides:
    - bot.service.alerter.TelegramAlerter
  affects:
    - bot/service/bot.py  # 05-01 wires alerter for entry/exit fan-out
    - bot/service/watchdog.py  # 05-02 dispatches disconnect/reconnect alerts
tech_stack:
  added: []
  patterns:
    - run_in_executor blocking-in-executor (mirrors gateway.py place_order pattern)
    - silent-failure-never-propagates (mirrors audit_log.py try/except pass)
    - enabled guard early-return (mirrors paper_guard.py assert_paper_account style)
key_files:
  created:
    - bot/service/alerter.py
  modified:
    - tests/service/test_alerter.py  # xfail -> xpassed (turned green)
decisions:
  - "send_daily_summary accepts pre-computed aggregates (trades, wins, pnl_usd, open_risk_usd) in addition to format_daily_summary which accepts raw rows — simplifies caller code in 05-01 TradingBot"
  - "html.escape() applied to code and exit_reason in all builders — mitigates T-05-03-03 alert injection"
  - "_EXIT_REASON_LABELS dict maps all test-required reason strings (stop_out, partial, breakeven, trail_up, force_close, exit_fill) to human labels"
metrics:
  duration: "~5 minutes"
  completed: "2026-06-25"
  tasks_completed: 2
  files_created: 1
---

# Phase 05 Plan 03: TelegramAlerter Summary

**One-liner:** Zero-dep stdlib urllib fire-and-forget Telegram alerter with entry/exit/daily-summary builders and strict exception isolation (ALERT-01..04).

## What Was Built

`bot/service/alerter.py` — `TelegramAlerter` class implementing:

- **Transport:** `_post_blocking()` POSTs urlencoded payload to `https://api.telegram.org/bot{token}/sendMessage` via `urllib.request.urlopen` with 10s timeout. Token is used only in the URL; never logged or included in alert text (T-05-03-01 mitigated).
- **Async send:** `send(text)` runs `_post_blocking` in a thread executor via `loop.run_in_executor(None, ...)`. All exceptions are caught, warning-logged (without token), and swallowed — NEVER re-raised (ALERT-04 / T-05-03-04 mitigated).
- **Enabled guard:** `_enabled = bool(token and chat_id)`. When disabled, `send()` debug-logs a preview and returns immediately — no network call attempted (D-12/D-13).
- **Entry builder:** `format_entry_alert(code, qty, entry_price, stop)` — ticker/size/entry/stop fields, code HTML-escaped (ALERT-01 / T-05-03-03 mitigated).
- **Exit builder:** `format_exit_alert(code, exit_reason, r_multiple)` — maps all 6 exit reason strings to human labels via `_EXIT_REASON_LABELS` dict; unknown reasons fall back to the escaped raw string (ALERT-02).
- **Daily summary:** `format_daily_summary(trades_rows, open_positions)` — computes n_trades, wins, losses, realized PnL, open risk from raw rows; `send_daily_summary(trades, wins, pnl_usd, open_risk_usd)` accepts pre-aggregated values for simpler caller wiring (ALERT-03).
- **Convenience coroutines:** `alert_entry()`, `alert_exit()`, `alert_summary()` build and await send (callers use `asyncio.create_task` for fire-and-forget dispatch). Named `send_*` variants for test compatibility.

## Tests

All 4 `tests/service/test_alerter.py` xfail stubs turned green (xpassed):
- `test_send_failure_does_not_raise` (ALERT-04) — urlopen patched to raise OSError; send() returns normally
- `test_entry_alert_content` (ALERT-01) — ticker, entry price, stop in POST body
- `test_exit_alert_for_all_reasons` (ALERT-02) — 6 exit reasons each produce exactly one urlopen call
- `test_daily_summary_content` (ALERT-03) — trades/wins/PnL in POST body

Full suite: 377 passed, 1 skipped, 10 xfailed (other wave stubs unchanged), 4 xpassed.

## Deviations from Plan

None — plan executed exactly as written.

The plan specified both `format_*` builders and `async def alert_*` / `send_*` wrappers. Both were implemented. The tests use the `send_entry_alert` / `send_exit_alert` / `send_daily_summary` naming for the concrete send methods; `alert_*` aliases call through to the same builders.

## Known Stubs

None. All public methods are fully implemented with real logic. No placeholder values or TODO comments.

## Threat Surface Scan

No new threat surface beyond what is documented in the plan's threat model. All T-05-03-* items are mitigated:
- T-05-03-01 (token in logs): token only used in `_API_URL.format(token=...)` inside `_post_blocking`; no log call includes token
- T-05-03-02 (blocks event loop): `run_in_executor` keeps POST off the loop; `_TIMEOUT_S=10` bounds the call
- T-05-03-03 (alert injection): `html.escape()` on `code` and `exit_reason` in all builders
- T-05-03-04 (failure propagation): `send()` wraps everything in `try/except Exception` and swallows

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 21ffc37 | feat(05-03) | implement TelegramAlerter — urllib fire-and-forget transport + alert builders |

## Self-Check: PASSED

- [x] `bot/service/alerter.py` created (328 lines, contains `class TelegramAlerter`)
- [x] commit 21ffc37 exists in git log
- [x] `python3 -m pytest tests/service/test_alerter.py -q` — 4 xpassed
- [x] `python3 -m pytest tests/ -q` — 377 passed, 1 skipped, 10 xfailed, 4 xpassed
