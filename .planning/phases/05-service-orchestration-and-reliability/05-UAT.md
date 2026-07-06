---
status: partial
phase: 05-service-orchestration-and-reliability
source:
  - 05-00-SUMMARY.md
  - 05-01-SUMMARY.md
  - 05-02-SUMMARY.md
  - 05-03-SUMMARY.md
  - 05-04-SUMMARY.md
started: 2026-06-24T19:00:00Z
updated: 2026-06-24T19:00:00Z
---

## Current Test

[testing complete — 2 pass, 1 issue, 3 blocked]

## Tests

### 1. Cold Start Smoke Test (python -m bot)
expected: With OpenD running + paper-safety env set, `python3 -m bot` boots with no traceback — structured JSON logs, paper guard passes, readiness gate connects + startup_reconcile, scheduler registers the 5 jobs, bot idles for the next ET time. Ctrl-C triggers graceful shutdown (flush + audit + gateway close + bot-stopped alert/log).
result: pass
note: Offline pre-check verified config load + composition + 5-job registration (coalesce/misfire/ET triggers) + entry-gate default + alerter no-op; user confirmed live boot/idle/graceful-shutdown with OpenD.

### 2. Static HTML dashboard renders offline (DASH-01)
expected: Open `reports/latest.html` (or a dated `reports/YYYY-MM-DD.html`) in a browser via file:// with no internet. It renders fully — an inline-SVG R-multiple histogram, an Open Positions table, and a Last-20 Closed Trades table — with no broken images, no spinner, no console errors, and no network requests.
result: pass
note: Generated a sample report (5 closed trades incl. R=3.5, 2 open positions) into reports/; automated checks confirmed 1 <svg> + <rect> bars, 0 <script>, 0 external http refs (only svg xmlns), all 3 sections present; user confirmed visual render from file://.

### 3. Telegram alerts deliver (ALERT-01/02/03) or no-op cleanly (ALERT-04)
expected: With TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID exported, a trade entry/exit and the post-force-close daily summary arrive as Telegram messages (entry shows ticker/size/entry/stop; exits cover partial/breakeven/trail/stop-out/force-close; summary shows trade count, win/loss, realized PnL, open risk). With the secrets UNSET, the bot runs normally and only logs the alert (no crash, no blocking).
result: issue
live_delivery: "CONFIRMED — 7/7 messages (entry + 5 exit reasons + daily summary) delivered via the real urllib transport to Telegram channel S&P-AutoUpdates (chat_id -1003955814504). Token @snpautobot valid; transport + builders + delivery all working end-to-end. NOTE: the originally-configured chat_id -5293756055 was a deleted group (403 'group chat was deleted'); user created a new channel. trail_stop rendered raw in-channel, confirming the missing-label limb of the issue below."
reported: "Live exit-alert wiring does not satisfy ALERT-02. PositionManager.apply_exit_fill (manager.py:525-529) invokes on_exit_alert with a HARDCODED reason 'exit_fill' for every exit, and only when remaining_quantity==0. Consequences: (a) exit alerts never distinguish the 5 spec reasons (partial/breakeven/trail-stop/stop-out/force-close) — all show 'Exit Fill'; (b) partial scale-outs fire NO alert (gated on full close); (c) the reason the manager actually emits for trailing exits is 'trail_stop', which is absent from _EXIT_REASON_LABELS (has 'trail'/'trail_up'). format_exit_alert + the no-op path (ALERT-04) and entry alert (ALERT-01) and daily summary builder (ALERT-03) are all correct in isolation; the gap is the manager→alerter integration. Live delivery itself not exercised — .env Telegram secrets are blank."
severity: major

### 4. OpenD disconnect watchdog (SVC-02)
expected: While the bot is running, kill OpenD. Within ~one poll cycle (~60s) the bot detects the loss — logs it and (if Telegram configured) fires a disconnect alert — and PAUSES new entries while bar-close exit checks keep running. Restart OpenD: the watchdog reconnects with backoff, re-runs startup_reconcile + re-subscribes, fires a reconnect alert, and only then re-enables entries.
result: blocked
blocked_by: server
reason: "Requires live OpenD disconnect/reconnect exercise; not available this session. Watchdog logic covered by tests/service/test_watchdog.py (green)."

### 5. launchd supervision + auto-restart (SVC-01/SAFE-04)
expected: Install deploy/com.bot.trading.plist (with your env values) via `launchctl load`. The bot runs supervised; rotating JSON logs appear under logs/. Kill the bot process — launchd auto-restarts it (respecting ThrottleInterval). `launchctl unload` stops it cleanly. (The documented shell `run_forever.sh` fallback is an alternative if you prefer no launchd.)
result: blocked
blocked_by: release-build
reason: "Requires installing the launchd LaunchAgent and exercising kill/auto-restart; not run this session. Plist + run_forever.sh + README delivered and inspected in verification."

### 6. Scheduler fires daily jobs at correct ET times (SVC-01/SCAN-07)
expected: Run the service across one full paper trading day. The premarket scan fires ~08:30 ET, market-open subscribe ~09:30 ET, intraday re-scans ~every 30 min for ~7 passes across 09:55–12:55 ET, force-close ~15:51 ET, and the EOD report + daily summary ~15:55 ET — each at the correct ET time, on a trading day only (weekends/holidays no-op).
result: blocked
blocked_by: server
reason: "Requires an unattended live session across market hours; not run this session. Job registration + ET cron/interval triggers + misfire grace verified offline in Test 1 pre-check and in 05-VERIFICATION.md (AC-01)."

## Summary

total: 6
passed: 2
issues: 1
pending: 0
blocked: 3
skipped: 0

## Gaps

- truth: "On each exit, a Telegram alert fires covering all five exit reasons — partial, breakeven, trail-stop, stop-out, force-close (ALERT-02)"
  status: failed
  reason: "User reported: live exit-alert wiring uses a hardcoded 'exit_fill' reason and only fires on full close — exit alerts never distinguish the 5 reasons, partial exits fire no alert, and the manager's actual 'trail_stop' reason is missing from _EXIT_REASON_LABELS."
  severity: major
  test: 3
  artifacts:
    - "bot/position/manager.py:517-531 (apply_exit_fill — on_exit_alert called with constant 'exit_fill', gated on remaining_quantity==0)"
    - "bot/position/manager.py:682,931 (emits exit_reason='trail_stop')"
    - "bot/service/alerter.py:30-38 (_EXIT_REASON_LABELS missing 'trail_stop' key)"
  missing:
    - "Pass the real exit reason (stop_out/trail_stop/breakeven/force_close) to on_exit_alert instead of constant 'exit_fill'"
    - "Fire an exit alert on partial scale-outs (not only on full close)"
    - "Add 'trail_stop' to _EXIT_REASON_LABELS (and reconcile reason vocabulary between manager and alerter)"
