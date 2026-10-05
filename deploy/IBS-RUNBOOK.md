# IBS bot runbook (Phase 12) — cutover and operations

Every step here is **operator-run**. Agents never execute these commands.

The IBS bot (`ibs_etf_mean_reversion`) replaces Trend Join Long on the shared paper account 1727266.
The options bot keeps running. Sentinels are separate: `.bot_kill` (equity), `.bot_kill_options`, `.bot_kill_ibs`.

## 0. Preconditions

- The Phase 12 branch is merged to `develop` **in the main checkout** (worktree changes are inert until merged).
- `python3 -m pytest -q` is green there.
- RTH read-only probe reviewed between 15:30 and 15:55 ET:
  `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/uat_ibs_probe.py`
  Every row fresh, IBS values plausible; use the `age_s` and `ask-bid` columns to confirm
  `signal.max_snapshot_age_s` and the limit buffers in `rules_ibs.json` (tune the JSON, never code).
- One-share round trip clean (`--live-1lot --confirm`):
  `python3 scripts/uat_ibs_probe.py --live-1lot --confirm --symbol US.XLU` (same env vars)
  (placed BUY fills, SELL fills; if it prints "1 share still held", sell it manually in moomoo).

## 1. Stop Trend Join Long

It runs as a terminal process, not under launchd.

```
cd <directory it was started from>
touch .bot_kill          # or Ctrl-C in its terminal
pgrep -fl "python3 -m bot"   # only the options process (--rules rules_options.json) should remain
rm .bot_kill             # a leftover sentinel kills the next equity start
```

Only if a `com.bot.trading` LaunchAgent was installed later:
`launchctl unload ~/Library/LaunchAgents/com.bot.trading.plist`.

## 2. Leave the options bot running

Do not touch `.bot_kill_options`.

## 3. Start IBS (ONE instance only)

Terminal:

```
PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_ibs.json
```

Or launchd: copy `deploy/com.bot.ibs.plist` to `~/Library/LaunchAgents/`, fill the OPERATOR values
(paths, Telegram token/chat id), `chmod 600` it, never commit the filled copy, then
`launchctl load ~/Library/LaunchAgents/com.bot.ibs.plist` (status: `launchctl list | grep com.bot.ibs`).
KeepAlive restarts crashes only; a `.bot_kill_ibs` shutdown (exit 0) stays stopped.

## 4. Verify startup

- `logs/ibs.log` shows `ibs_readiness_gate_passed` and the armed jobs: 15:50 / 15:59 / 16:05 ET on a
  normal day, 12:50 / 12:59 / 13:05 on a half-day.
- Telegram start message received; `data/ibs_state.db` exists.

## 5. Stop / restart IBS

```
touch .bot_kill_ibs      # positions stay held by design (D-06); working orders are cancelled
rm .bot_kill_ibs         # before restarting
```

Under launchd, `launchctl unload ~/Library/LaunchAgents/com.bot.ibs.plist` sends SIGTERM, which takes
the same graceful path (decision cancelled, working orders swept, "stopped" alert). `kill -9` does not:
after one, check moomoo for working orders and expect NEEDS_ATTENTION alerts on the next start.

## 6. First week — every trading day after 16:05 ET

- `reports/ibs/latest.html` written, and the Telegram EOD names `ibs_etf_mean_reversion`.
- DB matches moomoo share positions:
  `sqlite3 data/ibs_state.db "select code,qty,entry_date,status,exit_pending from ibs_positions where status not in ('CLOSED','ABORTED')"`
- No working orders left in moomoo after 16:00.
- `grep '"event": "ibs_' ~/.futu_trade_audit.jsonl | tail`
- First Monday: confirm the 15:50 `ibs_decide` job ran (look in `logs/ibs.log`) and its Telegram summary arrived.
- Any NEEDS_ATTENTION alert: inspect moomoo, fix by hand, then update the row, e.g.
  `update ibs_positions set status='CLOSED', close_reason='manual' where position_id=...`.
  The bot never trades to fix drift (D-11).
- Confirm the kill-file stop works without an auto-restart (`touch .bot_kill_ibs`, watch `pgrep`, then `rm` it).

## 7. Rollback

`touch .bot_kill_ibs`. Held ETFs remain until sold manually. Restart Trend Join Long only by explicit operator choice.
