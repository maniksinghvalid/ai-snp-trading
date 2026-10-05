---
status: testing
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
source: [12-VERIFICATION.md]
started: 2026-10-04T20:45:00-07:00
updated: 2026-10-04T20:45:00-07:00
---

## Current Test

number: 1
name: RTH read-only probe
expected: |
  Run between 15:30 and 15:55 ET on a trading day, from the repo root:
  `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/uat_ibs_probe.py`
  All 17 rows fresh (age_s well under signal.max_snapshot_age_s = 900), IBS values plausible against a chart,
  ask-bid within the 0.05 limit buffers; the printed WOULD EXIT / WOULD ENTER list matches expectations; nothing is placed.
awaiting: user response

## Tests

### 1. RTH read-only probe
expected: All 17 rows fresh (age_s ≪ 900), IBS values plausible against a chart, ask-bid within the 0.05 limit buffers; the would-do list matches expectations; no order is placed. Also calibrates `max_snapshot_age_s` and the limit buffers (RESEARCH: `update_time` freshness during RTH is untested offline).
result: [pending]

### 2. One-share paper round trip
expected: `python3 scripts/uat_ibs_probe.py --live-1lot --confirm --symbol US.XLU` (same env vars) during RTH on a flat universe ETF: BUY 1 share fills, SELL 1 share fills through the full TTL/re-price/cancel path; audit log + scratch `ibs_orders` rows consistent; no "1 share still held" message. The probe refuses (exit 3, no order) if the symbol is held, has an active row, is outside the universe, or it is inside the 15:43–16:00 decision window.
result: [pending]

### 3. launchd supervision and kill-file stop
expected: Copy `deploy/com.bot.ibs.plist` to `~/Library/LaunchAgents`, fill the OPERATOR values, `launchctl load`, confirm the process starts and `logs/ibs.log` shows `ibs_readiness_gate_passed`; then `touch .bot_kill_ibs`: process exits cleanly and is NOT restarted (`KeepAlive SuccessfulExit=false`); positions stay held; any working orders cancelled; "IBS bot stopped" Telegram alert received.
result: [pending]

### 4. Cutover per deploy/IBS-RUNBOOK.md
expected: Stop Trend Join Long (`touch .bot_kill` in the main checkout, confirm with `pgrep`, then `rm .bot_kill`); leave the options bot running; start IBS. `pgrep -fl 'python3 -m bot'` shows only `--rules rules_options.json` and `--rules rules_ibs.json`; `ibs.log` shows the readiness gate passed and jobs armed at 15:50 / 15:59 / 16:05 ET (12:50 / 12:59 / 13:05 on a half-day). First-week checks in runbook section 6 pass (Telegram daily summary, `reports/ibs/latest.html`, no unexpected NEEDS_ATTENTION rows).
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
