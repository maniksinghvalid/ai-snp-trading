---
phase: 05-service-orchestration-and-reliability
verified: 2026-06-24T18:15:00Z
remediated: 2026-06-24T18:40:00Z
status: passed
score: 12/12 must-haves verified
overrides_applied: 0
gaps: []
remediation:
  - truth: "Static no-JS HTML dashboard renders from file:// — R-multiple histogram, open-positions table, and last-20 closed-trades; SVG bucket counts match data"
    original_status: partial
    final_status: verified
    fix_commit: "be194e5"
    reason: "_compute_histogram overflow branch wrote counts['3+'] but the dict is keyed by _LABELS ('-3'..'3'), raising KeyError on any R>=3 trade (and a related underflow miscount for R<-3). Routed overflow (v>=3) to _LABELS[-1] and underflow (v<-3) to _LABELS[0]; added regression test test_r_histogram_overflow_and_underflow_buckets (R=3.5, R=-5.0). Functionally re-confirmed: build_daily_html with an R=3.5 trade renders ~2800 bytes with <svg>/<rect>, no <script>, and the only http ref is the SVG xmlns namespace (not a network fetch) — opens offline from file://. Full suite 393 passed, 0 failed."
---

# Phase 5: Service Orchestration and Reliability Verification Report

**Phase Goal:** The bot runs hands-off as a long-running supervised service: the daily schedule fires automatically, Telegram push alerts cover every trade event, OpenD loss is detected and handled gracefully, and structured logs enable post-hoc diagnosis

**Verified:** 2026-06-24T18:15:00Z
**Status:** passed — 12/12 AC verified (the 1 partial, histogram overflow bug, was remediated in commit be194e5; see frontmatter `remediation`)
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (Acceptance Criteria)

| # | AC | Status | Evidence |
|---|-----|--------|----------|
| AC-01 | AsyncIOScheduler fires all 5 jobs at correct ET times; `_register_jobs()` registers expected job IDs; coalesce+misfire_grace per job | VERIFIED | `bot/service/bot.py` `_register_jobs()` adds exactly `['premarket_scan', 'market_open_subscribe', 'intraday_rescan', 'force_close', 'eod_report']` with `coalesce=True` and config-sourced `misfire_grace_time` per job (lines 143-207). Confirmed by running bot construction in isolation — all 5 IDs present. |
| AC-02 | Startup readiness gate blocks entries until paper-guard + OpenD reachable + startup_reconcile all pass; exits/force-close/reconcile run regardless | VERIFIED | `_readiness_gate()` (bot.py lines 218-251): sets `_entries_enabled=True` only after gateway.connect() + startup_reconcile() + reconstruct_from_store() all succeed. `_entries_enabled` starts False. Test `test_readiness_gate_blocks_entries` passes. |
| AC-03 | `KillSwitch.register_flush(position_manager.flush_all)` is wired before the trade loop starts (R-04-01) | VERIFIED | Wired at TWO places: (1) `_readiness_gate()` line 246; (2) `_register_jobs()` line 139 — both before `self._scheduler.start()` and the main loop. Test `test_kill_switch_flush_registered` verifies `register_flush` called with `flush_all`. |
| AC-04 | `MoomooGateway.get_global_state()` exists and the watchdog polls it ~every 60s | VERIFIED | `gateway.py` lines 495-536: async method returning `{connected, qot_logined, trd_logined, ...}`, runs in thread executor. Watchdog (`watchdog.py` line 77-85) reads `self._cfg.watchdog_poll_interval_s` (60 from rules.json) as poll interval. |
| AC-05 | Simulated OpenD disconnect pauses entries within one poll cycle; exits never disabled; reconnect re-runs startup_reconcile before re-enabling entries | VERIFIED | `_on_disconnect()` (watchdog.py lines 114-145): sets `bot._entries_enabled = False` immediately; never touches exit logic. `_on_reconnect()` (lines 187-238): calls `startup_reconcile()` THEN sets `bot._entries_enabled = True`. Tests `test_disconnect_disables_entries` and `test_reconnect_reenables_entries` pass. |
| AC-06 | Entry alert contains ticker, size, entry price, and initial stop | VERIFIED | `format_entry_alert()` (alerter.py lines 126-150): produces `"<b>Entry</b> US.AAPL\nSize: 100 shares\nEntry: $150.00\nStop: $148.50"`. All 4 required fields present. Verified by direct invocation. |
| AC-07 | Exit alerts fire for all five exit reasons (partial, breakeven, trail, stop-out, force-close) | VERIFIED | `_EXIT_REASON_LABELS` dict covers all 5 SPEC-required keys: `partial`, `breakeven`, `trail`, `stop_out`, `force_close`. `format_exit_alert()` returns labelled message for each. Direct invocation confirmed. Test `test_exit_alert_for_all_reasons` passes for all 6 label variants. |
| AC-08 | Daily summary after force-close includes trade count, win/loss, realized PnL, and open risk | VERIFIED | `format_daily_summary()` (alerter.py lines 178-216): computes all 4 required fields. Direct invocation with 2 trades produced `"<b>Daily Summary</b>\nTrades: 2 (1W / 1L)\nRealized PnL: +$42.50\nOpen Risk: $100.00"`. `_job_eod_report()` dispatches via `asyncio.create_task`. |
| AC-09 | Simulated alert-delivery failure does not raise or propagate into the trade loop; alerter no-ops when secrets are unset | VERIFIED | `send()` (alerter.py lines 91-120): wraps `run_in_executor` in `try/except Exception` — confirmed via `patch('urllib.request.urlopen', side_effect=OSError(...))` — exception is caught, warning logged, not re-raised. `_enabled=False` when token/chat_id empty — confirmed no-op with empty constructor. |
| AC-10 | Static no-JS HTML dashboard renders from `file://` — R-multiple histogram + open-positions + last-20 closed-trades; SVG bucket counts match data | VERIFIED (remediated) | HTML generation (`bot/service/report.py`) produces `<svg>` R-multiple histogram, "Last 20 Closed Trades" table, and "Open Positions" table with no `<script>` tags. The `xmlns="http://www.w3.org/2000/svg"` attribute is not an external CDN dependency. The original PARTIAL was a `KeyError` overflow-bucket bug; **fixed in commit be194e5** (overflow→`_LABELS[-1]`, underflow→`_LABELS[0]`) with regression test `test_r_histogram_overflow_and_underflow_buckets`. Re-confirmed functionally: an R=3.5 trade renders offline (`<svg>`/`<rect>`, no `<script>`). |
| AC-11 | Service starts under launchd, auto-restarts on crash, writes structured rotating JSON logs | VERIFIED | `deploy/com.bot.trading.plist`: has `KeepAlive=true`, `RunAtLoad=true`, `ThrottleInterval=30`, `StandardOutPath`, `StandardErrorPath`, `EnvironmentVariables` (PAPER_TRADING, FUTU_TRD_ENV, TELEGRAM secrets). `deploy/run_forever.sh`: documented shell fallback. `bot/safety/logger.py` line 97: `RotatingFileHandler` with JSON output. `main.py` calls `configure_logging()` first. |
| AC-12 | All schedule/poll/backoff/throttle/misfire values from `rules.json` `service.*`; no Telegram secret in `rules.json` | VERIFIED | All 13 service tunables loaded from `rules.json service` block via `StrategyConfig` (loader.py lines 215-229). Bot/watchdog code reads exclusively from `cfg.*` fields — no literal timing values found except `asyncio.sleep(1)` (the kill-switch 1-second heartbeat, not a tunable). `rules.json` contains no `TELEGRAM_*` keys. |

**Score:** 12/12 AC verified (the histogram overflow bug was remediated in commit be194e5)

---

### Gaps

#### Gap 1 (AC-10): R-Multiple Histogram Overflow Bucket — KeyError on R >= 3.0

**Root cause:** In `bot/service/report.py`, `_compute_histogram()` initializes the counts dict using `_LABELS = ["-3", "-2", "-1", "0", "1", "2", "3"]` (the "3" key represents the [3, ∞) overflow bucket). However the overflow branch at line 178 writes `counts["3+"] += 1` — a key that does not exist in the dict. This raises `KeyError: '3+'` whenever a trade closes at R >= 3.0.

**Impact:** The EOD report job (`_job_eod_report`) calls `build_daily_html` → `_build_histogram_svg` → `_compute_histogram`. Any session with at least one 3R+ trade crashes the report generation entirely. The daily HTML file is not written and the Telegram summary is not dispatched for that session. For all sessions where trades close at R < 3.0 (the typical case), the dashboard functions correctly.

**Fix:** Change `bot/service/report.py` line 178 from `counts["3+"] += 1` to `counts["3"] += 1`. Add a test with `r_multiple=3.5` to `test_r_histogram_buckets`.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/service/bot.py` | TradingBot orchestrator (SVC-01) | VERIFIED | 530 lines; AsyncIOScheduler + readiness gate + 5 jobs + graceful shutdown |
| `bot/service/watchdog.py` | OpenDWatchdog (SVC-02) | VERIFIED | 239 lines; poll loop, disconnect/reconnect handlers, backoff |
| `bot/service/alerter.py` | TelegramAlerter (ALERT-01..04) | VERIFIED | 329 lines; urllib fire-and-forget, 3 format methods, failure isolation |
| `bot/service/report.py` | ReportBuilder + module-level helpers (DASH-01) | VERIFIED | overflow/underflow bucket bug fixed (commit be194e5) + regression test added |
| `bot/main.py` | Entry point composing all components | VERIFIED | Wires all Phase 1-5 components; asyncio.run(bot.run()) |
| `bot/__main__.py` | `python -m bot` dispatch | VERIFIED | Calls `main()` from `bot.main` |
| `bot/gateway/gateway.py:get_global_state` | New async method (SVC-02) | VERIFIED | Lines 495-536; run_in_executor; never raises; returns connected/qot_logined/trd_logined |
| `bot/config/loader.py` | StrategyConfig service.* fields (CFG-01) | VERIFIED | 14 service fields; all loaded from rules.json |
| `bot/config/schema.py` | JSON schema service block | VERIFIED | service block with 14 required fields |
| `rules.json` | service.* block | VERIFIED | All 14 tunables present; no TELEGRAM keys |
| `deploy/com.bot.trading.plist` | launchd LaunchAgent plist | VERIFIED | KeepAlive, RunAtLoad, ThrottleInterval=30, StandardOut/Err, EnvironmentVariables |
| `deploy/run_forever.sh` | Shell fallback supervisor | VERIFIED | while-true loop, ThrottleInterval=30, sources .env |
| `.env.example` | Environment variables documentation | VERIFIED | PAPER_TRADING, FUTU_TRD_ENV, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID documented |
| `tests/service/` | Unit coverage (4 test files, 15 tests) | VERIFIED | All 15 pass; covers SVC-01/SVC-02/ALERT-01..04/DASH-01 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `TradingBot.__init__` | `KillSwitch.register_flush` | `_register_jobs()` line 139 AND `_readiness_gate()` line 246 | WIRED | Called before scheduler.start() |
| `TradingBot.run()` | `OpenDWatchdog.run()` | `asyncio.create_task(self._watchdog.run())` line 506 | WIRED | Launched after readiness gate |
| `PositionManager` | `TelegramAlerter.send_entry_alert` | `on_entry_alert` lambda in `main.py` line 91 via `asyncio.create_task` | WIRED | Fire-and-forget |
| `PositionManager` | `TelegramAlerter.send_exit_alert` | `on_exit_alert` lambda in `main.py` line 94 via `asyncio.create_task` | WIRED | Fire-and-forget |
| `_job_eod_report` | `TelegramAlerter.format_daily_summary` + `send` | `bot.py` lines 419-420 via `asyncio.create_task` | WIRED | Fire-and-forget after HTML write |
| `_job_eod_report` | `build_daily_html` + `write_reports` | `bot.py` line 409-410 via `run_in_executor` | WIRED | File I/O off event loop |
| `OpenDWatchdog._check_once` | `gateway.get_global_state()` | `watchdog.py` line 100 | WIRED | Every poll cycle |
| `OpenDWatchdog._on_reconnect` | `gateway.startup_reconcile()` | `watchdog.py` line 203 | WIRED | Before re-enabling entries |
| `bot/main.py` | `bot/service/` components | explicit imports and constructor injection | WIRED | All components composed in `main()` |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|-------------------|--------|
| `_job_eod_report` | `trades_rows` | `store.get_closed_trades(today)` | Yes — queries StateStore SQLite | FLOWING |
| `_job_eod_report` | `positions_rows` | `store.get_open_positions()` | Yes — queries StateStore SQLite | FLOWING |
| `format_daily_summary` | `n_trades, wins, realized_pnl, open_risk` | `trades_rows` / `open_positions` (passed in from store) | Yes — computed from store rows | FLOWING |
| `build_daily_html` | `trades_rows, positions_rows` | module-level function args (passed from job) | Yes — passed from store query | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 5 job IDs registered | `TradingBot._register_jobs()` invoked with mock cfg | `['premarket_scan', 'market_open_subscribe', 'intraday_rescan', 'force_close', 'eod_report']` | PASS |
| Entry alert fields | `format_entry_alert('US.AAPL', 100, 150.00, 148.50)` | `<b>Entry</b> US.AAPL\nSize: 100 shares\nEntry: $150.00\nStop: $148.50` | PASS |
| Exit alert (all 5 spec reasons) | `format_exit_alert(code, reason, 1.5)` for each reason | Correct label for partial/breakeven/trail/stop_out/force_close | PASS |
| Alert failure isolation | `send()` with `urlopen` raising OSError | Exception caught, warning logged, not re-raised | PASS |
| Alerter no-op without secrets | `TelegramAlerter('', '')._enabled` | False; send() returns after debug log | PASS |
| HTML dashboard — no script | `build_daily_html(trades, positions, date)` | `<script>` absent; `<svg>` present; histogram + 2 tables | PASS |
| HTML dashboard — R>=3 overflow | `_compute_histogram([3.5])` | KeyError: '3+' — crash | FAIL |
| SVG bucket counts match data | `_compute_histogram([1.5, -0.5])` | `{'-1': 1, '1': 1, ...}` sum=2 | PASS (R<3 only) |
| Watchdog disconnect disables entries | `_check_once()` with `connected=False` | `bot._entries_enabled = False` | PASS |
| Watchdog reconnect re-runs reconcile | `_check_once()` after disconnect, `connected=True` | `startup_reconcile` called, `_entries_enabled=True` | PASS |
| Full test suite | `python3 -m pytest -q` | 392 passed, 1 skipped, 0 failed | PASS |

---

### Probe Execution

Step 7c: SKIPPED — no `scripts/*/tests/probe-*.sh` files; phase deliverable is library code and deploy configs, not a standalone probe script.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SVC-01 | 05-01-PLAN | AsyncIOScheduler-driven orchestrator | SATISFIED | TradingBot + _register_jobs() + readiness gate |
| SVC-02 | 05-02-PLAN | OpenD watchdog with connect/disconnect handling | SATISFIED | OpenDWatchdog with full lifecycle |
| SCAN-07 | 05-01-PLAN | Intraday re-scan scheduling (≈7 passes 09:55-12:55) | SATISFIED | `intraday_rescan` IntervalTrigger(minutes=30) + window check |
| ALERT-01 | 05-03-PLAN | Entry alert with ticker/size/entry/stop | SATISFIED | format_entry_alert() verified |
| ALERT-02 | 05-03-PLAN | Exit alert for all 5 exit reasons | SATISFIED | All 5 reasons produce labelled output |
| ALERT-03 | 05-03-PLAN | Daily summary with trade count/win-loss/PnL/open-risk | SATISFIED | format_daily_summary() verified |
| ALERT-04 | 05-03-PLAN | Alert delivery failure isolation | SATISFIED | send() swallows all exceptions; no-op without secrets |
| DASH-01 | 05-04-PLAN | Static no-JS HTML dashboard | VERIFIED | overflow/underflow KeyError fixed (commit be194e5); renders for all R |
| R-04-01 | 05-01-PLAN | KillSwitch.register_flush carry-forward from Phase 4 | SATISFIED | Wired in both _readiness_gate() and _register_jobs() |
| CFG-01 (service) | 05-00-PLAN | All service tunables from rules.json | SATISFIED | 14 service fields in StrategyConfig; all referenced from cfg.* |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/service/report.py` | 178 | `counts["3+"] += 1` but key is `"3"` in dict | Blocker (latent) | EOD report crashes on any session with a 3R+ trade; daily summary not dispatched |

No debt markers (TBD/FIXME/XXX) found in Phase 5 files.

---

### Human Verification Required

None identified — all acceptance criteria are verifiable programmatically. The launchd plist is a template (requires operator-specific paths), which is the intended design, not a gap.

---

## Gaps Summary

One gap blocks full goal achievement:

**Histogram overflow KeyError (AC-10/DASH-01):** `bot/service/report.py` line 178 writes to `counts["3+"]` but the counts dict has key `"3"` (matching `_LABELS`). This is a single-line off-by-one in the key name. The bug makes the EOD report generation crash — and suppresses the daily Telegram summary — on any trading session where at least one position closes at 3R or above. The fix is trivial but untested and must be verified.

All other 11 acceptance criteria are fully implemented, substantively correct, and properly wired end-to-end.

---

_Verified: 2026-06-24T18:15:00Z_
_Verifier: Claude (gsd-verifier)_
