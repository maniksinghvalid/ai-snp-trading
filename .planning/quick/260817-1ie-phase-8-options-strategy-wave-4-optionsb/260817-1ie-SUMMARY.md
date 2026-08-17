---
task: 260817-1ie-phase-8-options-strategy-wave-4-optionsb
plan: 01
status: complete
branch: develop
completed: 2026-08-17
tests_before: "920 passed, 1 skipped"
tests_after: "952 passed, 1 skipped"
---

# Phase 8 Options — Wave 4 (OptionsBot service + dispatch) Summary

The options bot now exists as a process: it connects, reconciles against the broker,
enables entries, runs three scheduled jobs, and shuts down on the kill switch.
`python -m bot --rules rules_options.json` launches it; `python -m bot` still launches
the equity bot through its unchanged construction sequence.

## Commits

| Commit | Task | Files |
|--------|------|-------|
| `ce3edb7` | 1 — lifecycle, reconcile, formatters | `bot/options/service.py`, `tests/options/test_service.py` |
| `bb059f1` | 2 — entry-scan / manage / EOD jobs | `bot/options/service.py`, `tests/options/test_service.py` |
| `96ad6ac` | 3 — `main(rules_path)` + dispatch + `--rules` | `bot/options/service.py`, `bot/main.py`, `bot/__main__.py`, `tests/options/test_dispatch.py` |
| `e34e252` | +guards | `tests/options/test_service.py` (kill-switch / readiness-gate entry guards) |

## Files

Created: `bot/options/service.py` (977 lines), `tests/options/test_service.py`,
`tests/options/test_dispatch.py`.
Modified: `bot/main.py` (+`import json`, strategy_name peek/dispatch, `rules_path`
parameter), `bot/__main__.py` (argparse `--rules`).

Explicitly unchanged (verified `git diff --stat` over the whole task):
`bot/service/bot.py`, `bot/service/watchdog.py`, `bot/service/alerter.py`,
`bot/service/report.py`, `bot/gateway/gateway.py`, `bot/options/strategy.py`,
`bot/options/execution.py`, `bot/options/store.py`, `bot/options/config.py`,
`bot/state/**`, `tests/test_main_wiring.py`.

## Tests

920 passed / 1 skipped → **952 passed / 1 skipped** (32 new: 26 service, 6 dispatch).
Full suite fully green. `grep -c "OrderType.MARKET" bot/options/service.py` = 0.
Smoke: `python3 -c "import bot.options.service, bot.main"` and `python3 -m bot --help`
(prints usage with `--rules`) both clean.

Safety invariants covered by tests: no order for a code outside the bot's own
`option_legs` (entry inserts leg rows before the executor is called; manage closes only
DB leg rows), reconcile never touches broker codes absent from the DB, entries blocked
by the breaker meta / kill switch / readiness gate / non-trading day.

## Deviations

None material — the plan was executed as written. Two judgement calls inside its latitude:

1. **`os.makedirs(cfg.report_dir, exist_ok=True)` before `write_reports`.** The shared
   `_write_reports` helper does `Path(report_dir).mkdir(exist_ok=True)` — not recursive —
   so the nested default `reports/options` would have failed (silently, as a warning log).
   Creating the directory in `_job_eod` fixes it without touching `bot/service/report.py`.
2. **`_job_entry_scan` / `_job_manage` split their locked bodies into helpers**
   (`_scan_and_open` / `_try_open`, `_manage_once` / `_manage_position` /
   `_check_daily_breaker`). Same control flow the plan specifies, but each per-underlying
   and per-position `try/except … continue` reads as one call instead of a 90-line inline
   block. Also added `_parse_hhmm` as a module helper, reused by `_is_rth_now`.

## How to run (operator)

```bash
# OpenD GUI must be running and logged in on 127.0.0.1:11111
export FUTU_TRD_ENV=SIMULATE          # paper only
export FUTU_ACC_ID=1727266            # the shared paper account
export PAPER_TRADING=true
export TELEGRAM_BOT_TOKEN=...         # optional; without it alerts no-op (logged once)
export TELEGRAM_CHAT_ID=...

python3 -m bot --rules rules_options.json     # options bot
python3 -m bot                                # equity bot, unchanged
```

Its own DB (`data/options_state.db`), kill file (`.bot_kill_options`) and report dir
(`reports/options`) come from `rules_options.json` `service.*` — the equity bot's files
are untouched, so both may run at once. Stop it by creating the kill file:
`touch .bot_kill_options`.

Run ONE instance of each bot only (shared-account hazard, see MEMORY.md).

## Only a live UAT can verify these

1. **`u_change_ratio` units.** Assumed to already be a percent and fed straight into
   `passes_entry_gate` as `change_pct`. If the SDK actually returns a fraction, the
   fear-drop gate (`change_pct <= -2.0`) never fires. The `x100` belongs next to the IVR
   conversion in `_try_open` if the probe says fraction. (Comment marks the spot.)
2. **SIMULATE fill behaviour for option limit orders.** The escalation-to-natural loop in
   `LegExecutor` exists precisely because the paper fill model is unproven; whether a
   mid-anchored option limit ever fills on SIMULATE is unknown until a 1-lot live probe.
3. **`position_side` sign in `get_option_positions`.** Reconcile compares `-qty` for SELL
   legs against the broker dict. If a live SIMULATE payload reports shorts as positive
   without a `position_side` the gateway recognises, every open spread would be flagged
   NEEDS_ATTENTION on the first manage tick. Check one real short leg before trusting the
   unattended loop.

---

# What Wave 5 (docs / UAT tooling) must know

## Job ids and triggers (all `coalesce=True, max_instances=1, misfire_grace_time=300`)

| id | trigger | source config |
|----|---------|---------------|
| `options_entry_scan` | cron 10:00 ET | `entry.entry_scan_et` |
| `options_entry_scan_2` | cron 14:30 ET — registered ONLY when set | `entry.second_entry_scan_et` |
| `options_manage` | interval every 5 min | `manage.manage_interval_min` |
| `options_eod` | cron 16:10 ET | `service.eod_report_et` |

The manage job additionally self-gates on `_is_rth_now()`: trading day, at/after 09:35 ET,
and strictly before (calendar close − 5 min). Both are module constants
(`_MANAGE_START_ET`, `_MANAGE_CLOSE_BUFFER_MIN`), deliberately not rules keys.

## Service surface

```python
OptionsBot(cfg, gateway, store, kill_switch, alerter, watchdog=None)
  .run()                       # readiness gate → jobs → kill-switch loop → shutdown
  .reconcile(startup=False)    # broker truth; startup=True also flags OPENING/CLOSING
  ._register_jobs() / ._job_entry_scan() / ._job_manage() / ._job_eod()
  ._entries_enabled / ._store / ._position_manager=None / ._bar_agg=None   # watchdog duck-type
main(rules_path)               # process entry point
```

Module-level pure helpers a UAT probe can reuse without a broker: `_ivr_pct`, `_ivp_pct`
(the ONE fraction→percent conversion point), `_group_rows_by_underlying`, `_rows`,
`_chunks`, `_parse_hhmm`, `_fmt_entry`, `_fmt_exit`, `_fmt_summary`, `_options_html`.

## Judgement calls carried forward

- The entry screen uses fixed delta breadth (`-0.35..-0.03` puts, `0.03..0.35` calls) —
  screen breadth only; `cfg.short_delta` is what actually picks the strike.
- Realized P&L sign: `(credit − net_exit) × 100 × qty`, where `net_exit` = Σ SELL-leg exit
  prices − Σ BUY-leg exit prices. A profitable spread is positive.
- The daily-loss breaker is keyed on the `options_breaker_date` meta row (restart-safe),
  and is only evaluated when at least one OPEN position exists — the manage job returns
  early on an empty book. A day that ends flat-but-losing (all positions already closed)
  will not arm the breaker until the next open position is marked.
- EOD "closed today" is a Python-side prefix filter over all CLOSED rows
  (`ponytail:` comment names the ceiling — add a SQL helper past a few thousand rows).

## Self-Check: PASSED

- `bot/options/service.py`, `bot/main.py`, `bot/__main__.py`,
  `tests/options/test_service.py`, `tests/options/test_dispatch.py` — all present.
- Commits `ce3edb7`, `bb059f1`, `96ad6ac`, `e34e252` — all in `git log`.
