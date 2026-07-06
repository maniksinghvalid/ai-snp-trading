# Phase 5: Service Orchestration and Reliability - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Wrap the completed scan → signal → risk → execution → position stack (Phases 1–4) in a **long-running supervised service** that runs the full daily trade lifecycle hands-off, with reliability and observability. No new strategy logic — this is the integration/glue + reliability phase. In scope:

- **TradingBot orchestrator + scheduler** — an always-on async process driven by an **APScheduler `AsyncIOScheduler`**; cron jobs for premarket scan, market-open subscribe, intraday re-scans (~every 30 min, ≈7 passes 09:55–12:55 ET — SCAN-07), 15:51-ET force-close (calendar-aware, already built in Phase 4), and the EOD report. Wires all Phase 1–4 components together. (SVC-01)
- **OpenD connectivity watchdog** — poll `get_global_state()` ~every 60s; on loss, pause new entries (keep exits queued), reconnect with backoff, re-reconcile on recovery, and alert. (SVC-02)
- **TelegramAlerter** — fire-and-forget push alerts on entry (ALERT-01), every exit event (partial/breakeven/trail/stop-out/force-close — ALERT-02), and a daily summary after force-close (ALERT-03); delivery failures never touch the trade loop (ALERT-04).
- **Daily P&L report + static HTML dashboard** — daily summary (trade count, win/loss, realized PnL, open risk) and an offline, no-JS HTML dashboard (R-multiple histogram, open-positions table, last-20 closed trades — DASH-01).
- **Process supervision + structured logging integration** — launchd LaunchAgent for crash auto-restart; service-wide integration of the existing structlog rotating JSON logger (SVC-03); **wire the deferred kill-switch flush (R-04-01 / T-04-21)**.

Requirements in scope: **SVC-01, SVC-02, SCAN-07 (scheduling half), ALERT-01, ALERT-02, ALERT-03, ALERT-04, DASH-01**.

**Out of scope:** the backtester (Phase 6); any new strategy/filter/exit logic (Phases 2–4 own it, unchanged); live/real-money trading (paper SIMULATE only); an interactive/server-backed web UI (the dashboard is a static file). Phase 5 is the **first phase that runs the whole bot end-to-end as one unattended service**.

**Carry-forward obligations from Phase 4 (must be honored this phase):**
- **R-04-01 / T-04-21:** `main.py` MUST call `KillSwitch.register_flush(...)` wiring `position_manager.flush_all` before the trade loop starts — folded into the graceful-shutdown decision (D-07) below.
- Phase 4 already provides calendar-aware 15:51 force-close (`force_close_all`), `startup_reconcile` (broker-truth reconciliation), and `flush_all`. Phase 5 **schedules and supervises** these — it does not reimplement them.
- Live SIMULATE order-flow validation remains a manual UAT against a running OpenD paper account (Phase-4 carry-over) — relevant to the end-to-end session test (success criterion #1).
</domain>

<decisions>
## Implementation Decisions

### Service lifecycle & scheduler (SVC-01, SCAN-07)
- **D-01: Always-on 24/7 process.** One long-lived process; `AsyncIOScheduler` owns all daily timing. Survives across days; weekends/holidays no-op via the existing NYSE calendar gate (`bot/scanner/calendar.py`). Matches PROJECT.md's "persistent process owns state + live subscriptions" decision — simplest fit for stateful intraday positions.
- **D-02: Single AsyncIO event loop.** `AsyncIOScheduler` and the continuous 5m bar-push / position async tasks share **one** event loop. `BarAggregator` already bridges the SDK push thread → asyncio (Phase 3). Scheduled jobs and bar-close handlers are all coroutines on one loop — no locking, no second thread. (Keep every job coroutine non-blocking; offload any blocking call via `run_in_executor`.)
- **D-03: Smart per-job misfire handling.** `coalesce=True` + per-job `misfire_grace_time`. On a late boot, jobs still valid late (market-open subscribe, intraday re-scan, force-close) fire once; stale one-shots (e.g. premarket scan after the open) skip. Phase-4 `startup_reconcile` establishes position truth regardless of which jobs fired. Misfire-grace / coalesce values → `rules.json` (CFG-01), no hardcoded literals.
- **D-04: Lazy session-date keying for daily rollover.** No reset job. All session-scoped state (`daily_trade_count`, etc.) is keyed by today's **ET `session_date`** (already the schema design — `daily_trade_count.session_date` PK). A new day is a new key; yesterday's row is untouched. Robust to restarts and missed jobs.

### Process supervision & crash recovery (SVC-01, SAFE-04, R-04-01)
- **D-05: launchd LaunchAgent supervisor.** Native macOS. Ship a `.plist` with `KeepAlive=true` (restart on crash), `RunAtLoad=true` (start at login), `ThrottleInterval` (crash-loop guard), and `StandardOutPath`/`StandardErrorPath` to log files. Env vars (incl. paper-safety + Telegram secrets) provided via the plist `EnvironmentVariables` dict and/or a sourced `.env` (the bot does NOT auto-load `.env` — no python-dotenv; see `.env.example`). A shell `while`-loop wrapper is documented as a portable fallback / quick-run path; systemd documented only as a Linux note.
- **D-06: Throttle + alert on crash-loop, keep trying.** `ThrottleInterval` (e.g. 30–60s, → config) between restarts; on repeated rapid crashes fire a Telegram alert (if configured) so the operator knows it's flapping, but **keep attempting** — a transient OpenD restart should self-heal unattended (don't give up while away).
- **D-07: Full graceful shutdown handler (closes R-04-01 / T-04-21).** `main.py` registers `KillSwitch.register_flush(handler)` **before** the trade loop starts. The handler: (1) `position_manager.flush_all()` — persist all `PositionState` to StateStore; (2) write a shutdown audit entry; (3) unsubscribe feeds + `gateway.close()` cleanly; (4) fire a final "bot stopped" Telegram alert. Maximizes restart-readiness + operator visibility. (Literal R-04-01 minimum was flush-only; we go beyond it.)
- **D-08: Hard startup readiness gate before entries.** On every (re)start, **block the entry path** until: (1) paper-guard passes (SAFE-01), (2) OpenD reachable via `get_global_state()`, (3) Phase-4 `startup_reconcile` completes. Only then enable entry-placement jobs. Exits, force-close, and reconciliation run regardless. Prevents trading on a half-initialized restart. This same gate is reused on OpenD reconnect (D-11).

### OpenD watchdog disconnect policy (SVC-02)
- **D-09: Pause entries, keep exits queued.** On detected OpenD loss, suspend new-entry placement immediately (don't fire orders doomed to fail). Bar-close stop/partial/breakeven checks keep running; any exit they trigger is **queued and fired the instant OpenD reconnects** — a needed stop-out is never dropped (upholds Phase-4 D-07 "exits must complete"). Force-close stays armed.
- **D-10: Exponential backoff, capped, on reconnect.** Keep the ~60s health poll, but reconnection attempts back off (e.g. 5s→10s→30s→cap 60s, → `rules.json`) so a brief blip recovers fast without hammering and a long outage settles to steady retries. Reconnect = re-create the gateway quote/trade contexts (`connect()`).
- **D-11: Re-run startup reconciliation on recovery.** After reconnect, re-run Phase-4 `startup_reconcile` (broker truth wins) + re-subscribe 5m feeds before re-enabling entries (reuses the D-08 hard gate). The broker is authoritative; positions/fills may have moved during the outage.
- **D-12: Telegram-alert OpenD disconnect AND reconnect** — a **narrow, intentional exception** to PROJECT.md's "system-health alerts are log-only / out-of-scope for v1." Fire a Telegram alert on disconnect and on recovery (fire-and-forget; log if Telegram unconfigured). Rationale: SVC-02 success criterion #2 explicitly calls for a disconnect alert, and an unattended bot going blind is exactly when the operator needs to know. *Scope nuance flagged for the PROJECT.md Out-of-Scope line: this one health event is promoted to an alert; broader system-health alerting stays deferred.*

### Reporting: Telegram + HTML dashboard (ALERT-01..04, DASH-01)
- **D-13: Telegram secrets via env vars + `.env.example`.** `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` read from environment (consistent with the existing `FUTU_*` pattern). Documented in the repo's `.env.example`; `.env` stays gitignored and is sourced into the shell or supplied via the launchd plist (the bot does not auto-load `.env`). `rules.json` holds only non-secret toggles (e.g. `alerts_enabled`, poll/schedule intervals) — never secrets.
- **D-14: Zero-dependency Telegram transport.** POST to the Telegram Bot API HTTPS endpoint via **stdlib `urllib`**, wrapped in `run_in_executor` + `asyncio.create_task` so it is **truly fire-and-forget and never blocks the loop** (ALERT-04). No new dependency — mirrors the existing gateway `run_in_executor` pattern. Failures are caught + logged, never propagated.
- **D-15: No-JS dashboard via inline SVG + CSS, zero-dep.** Hand-generate the R-multiple histogram as inline `<svg>` bars; render open-positions and last-20-closed tables as HTML/CSS in **one self-contained `.html` file**. No matplotlib, no image files, no external/CDN assets — fully offline, opens from `file://`, tiny, version-control-friendly. Use pandas (already a dep) for aggregation.
- **D-16: Dated report files + stable `latest.html`.** Write `reports/YYYY-MM-DD.html` each day (kept — tiny, builds a history) plus a stable `reports/latest.html` copy for a one-click `file://` bookmark. `reports/` is gitignored. The Telegram daily summary (ALERT-03) references that day's file. Report/dashboard generation is triggered after the 15:51 force-close completes.

### Claude's Discretion
Left to research/planner at standard defaults (no user-locked preference):
- **Numeric tunables → `rules.json` (CFG-01), no hardcoded literals:** exact cron/schedule ET times, intraday re-scan cadence (~30 min) + window (09:55–12:55), watchdog poll interval (~60s), reconnect backoff steps + cap (D-10), launchd `ThrottleInterval` (D-06), misfire/coalesce grace (D-03), crash-loop alert threshold (D-06).
- **Module/dataclass decomposition** — the 4 planned slices (05-01 orchestrator/scheduler, 05-02 watchdog, 05-03 TelegramAlerter, 05-04 report+dashboard+supervisor+log integration); exact class names (`TradingBot`, `OpenDWatchdog`, `TelegramAlerter`, report builder), where `main.py` / `__main__.py` lives, and the alert event-fan-out wiring (how PositionManager/ExecutionEngine emit alert-worthy events to the alerter — in-process callback vs event bus).
- **Alert message format/content layout** — beyond the required fields (ALERT-01 ticker/size/entry/stop; ALERT-03 trades/win-loss/PnL/open-risk); emoji/markdown styling, message batching/rate-limiting.
- **`get_global_state()` gateway method** — does NOT exist yet on `MoomooGateway` (only `connect`/`close`/`subscribe`/`reconcile_once`/`startup_reconcile` + Phase-4 order methods). The watchdog (05-02) adds it, mirroring the deferred-SDK-import + `run_in_executor` pattern.
- **APScheduler dependency** — `apscheduler` (AsyncIOScheduler) is a new runtime dependency to add to `requirements.txt` with a package-legitimacy checkpoint (per the Phase-2 yfinance/pandas-market-calendars precedent).
- **Integration-test design** — the "one full paper-trading session end-to-end" verification (success criterion #1) and the OpenD-disconnect simulation (criterion #2): how to drive APScheduler/clock + a fake/killed OpenD deterministically in tests.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy, Config & Requirements (source of truth)
- `.planning/PROJECT.md` §"The Strategy — Trend Join Long" — canonical `rules.json` content; §"Out of Scope" (note the system-health-alerts line that D-12 narrowly excepts); §"Key Decisions" (Long-running service vs cron; Telegram as v1 interface; HTML dashboard optional).
- `rules.json` (repo root) — loaded runtime config (CFG-01); Phase-5 tunables (schedule times, poll/backoff intervals, misfire grace, throttle, `alerts_enabled`) are added here via the Phase-1 `StrategyConfig` loader. No Phase-5 literal hardcoded in Python.
- `.planning/REQUIREMENTS.md` §Service/Orchestration + §Alerts + §Dashboard — **SVC-01, SVC-02, SCAN-07, ALERT-01..04, DASH-01** acceptance text (this phase's requirement IDs).
- `.planning/ROADMAP.md` §"Phase 5: Service Orchestration and Reliability" — goal, 6 success criteria, and the 4 planned plan-slices (05-01..05-04).
- `.env.example` (repo root) — env-var conventions (paper-safety + `FUTU_*`); Phase 5 **adds `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`** here (D-13). Note: the bot does NOT auto-load `.env`.

### Architecture & Research
- `.planning/research/SUMMARY.md` — service/orchestration component boundary and bottom-up build-order rationale (Phase 5 = service hardening on top of stable Phase 1–4 stack).
- `.planning/research/STACK.md` — pinned deps; moomoo SDK global-state / connectivity surface for the watchdog.
- `.planning/research/PITFALLS.md` — ET/session correctness, non-atomic writes, push reliability (relevant to scheduler timing + queued-exit recovery D-09).

### Phase 1–4 artifacts (Phase 5 schedules/supervises/wires these — does NOT reimplement)
- `bot/gateway/gateway.py` — `MoomooGateway`: `connect()`/`close()` (reconnect D-10), `subscribe([SubType.K_5M])` (market-open job + reconnect re-subscribe), `reconcile_once()` (60–90s loop SAFE-03), `startup_reconcile(store, manager)` (boot + reconnect reconciliation D-08/D-11), Phase-4 order methods. **`get_global_state()` does NOT exist — watchdog 05-02 adds it.**
- `bot/position/manager.py` — `PositionManager`: `flush_all()` (kill-switch flush D-07), `force_close_all(today)` (calendar-aware 15:51 EOD job — Phase 4 built it; Phase 5 schedules it), restart reconstruction. Emits the alert-worthy events Phase 5 fans out to Telegram (ALERT-01/02).
- `bot/execution/engine.py` + `bot/execution/events.py` — `ExecutionEngine`, `FillEvent`; entry/exit fills are the source of ALERT-01/02 events and the daily-summary trade record (ALERT-03).
- `bot/safety/kill_switch.py` — `KillSwitch.register_flush(callback)` (line 97) — the exact API for wiring R-04-01 / D-07; `trigger()` (file-touch + SIGINT, SAFE-04).
- `bot/safety/logger.py` — existing structlog rotating JSON logger (SVC-03); Phase 5 integrates it service-wide (success criterion #5).
- `bot/safety/audit_log.py` — append-only JSONL audit (SAFE-05); shutdown + lifecycle events mirror this.
- `bot/safety/et_helpers.py` — ET / market-session helpers (SVC-04); all scheduler cron times + the watchdog "is the market open" checks compose with these.
- `bot/scanner/calendar.py` — NYSE holiday/half-day schedule; weekend/holiday no-op (D-01) and calendar-aware force-close timing source.
- `bot/scanner/scanner.py` — `run_daily_scan` / `run_intraday_rescan` (the premarket-scan + ~30-min re-scan jobs SCAN-07 call these).
- `bot/state/store.py` + `bot/state/migrations.py` — `StateStore`: `positions`, `trades` (closed-trade record incl. `exit_reason`, `r_multiple` — the dashboard/summary data source), `daily_trade_count` (`session_date` PK — D-04 lazy rollover). Any Phase-5 schema need is a new ordered migration (0005), never edit a shipped one.
- `.planning/phases/04-order-and-position-management/04-CONTEXT.md` — Phase-4 decisions Phase 5 depends on: D-01 synthetic bot-monitored stop (why exits-queued-on-disconnect D-09 matters), D-07 exits-must-complete, D-08 force-close escalate+alert (Phase-5 owns the Telegram delivery), D-09/D-10/D-11 reconciliation policy (reused on boot + reconnect).

### Existing Codebase (reuse-by-reference / patterns, NOT imported — D-02 wrap-not-import)
- `skills/moomooapi/scripts/quote/` + the global-state/connectivity scripts — `get_global_state` field shapes for the watchdog (05-02).
- `skills/moomooapi/scripts/common.py` — `_check_opend_alive()` / socket connectivity precedent for the watchdog health check.
- `CLAUDE.md` — project constraints (Python 3.6+, ET timezone, market-hours awareness, **paper-only / SIMULATE**, env-var config conventions, lean-deps + package-legitimacy gate for new deps).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`MoomooGateway`** — `connect`/`close` (reconnect D-10), `subscribe` (market-open + re-subscribe), `startup_reconcile` (boot + reconnect D-08/D-11), `reconcile_once` (SAFE-03 loop), async `run_in_executor` template (mirror for the new `get_global_state` watchdog method + the Telegram urllib POST D-14).
- **`PositionManager.flush_all()`** — ready for the kill-switch graceful-shutdown handler (D-07, R-04-01). **`force_close_all(today)`** — Phase 4 built the calendar-aware 15:51 EOD logic; Phase 5 just schedules it as a cron job.
- **`KillSwitch.register_flush()`** (kill_switch.py:97) — the exact wiring point for R-04-01.
- **structlog logger (`bot/safety/logger.py`)** + **JSONL audit (`audit_log.py`)** — service-wide log integration (SVC-03, criterion #5) and lifecycle/shutdown audit entries; no new logging stack.
- **`scanner.py` run_daily_scan / run_intraday_rescan**, **`calendar.py`**, **`et_helpers.py`** — the scheduler jobs are thin cron wrappers over these existing functions.
- **`StateStore` `trades` table** (`exit_reason`, `r_multiple`) + **`positions`** — the complete data source for the daily summary (ALERT-03) and the HTML dashboard (DASH-01); pandas (existing dep) does the aggregation for the SVG histogram.

### Established Patterns
- **Config-driven, behaviorally proven** (Phase 1 D-12 / CFG-01): Phase-5 tests should swap `rules.json` schedule/poll/backoff values and assert behavior changes; no schedule/interval literal hardcoded.
- **New ordered migration keyed by `PRAGMA user_version`** (Phase 1/2/3/4) — only if Phase 5 needs new columns (likely none); never edit a shipped migration.
- **Deferred SDK imports inside methods** + `run_in_executor` — the new `get_global_state` watchdog method follows this so the test env imports without `moomoo-api` (and so the urllib Telegram POST stays off the event loop).
- **New runtime dependency → package-legitimacy checkpoint** (Phase-2 yfinance/pandas-market-calendars precedent) — applies to adding `apscheduler` to `requirements.txt`.
- **Env-var config via `FUTU_*` + `.env.example`** (bot does not auto-load `.env`) — Telegram secrets follow the same convention (D-13).
- snake_case files, PascalCase classes, UPPER_SNAKE constants; pytest tree mirrors `bot/` (new `tests/service/` or per-component dirs per planner).

### Integration Points
- **The orchestrator (`TradingBot`/`main.py`) is the top-level wiring** — first place all of Phases 1–4 are composed into one running process: gateway + scanner + signal/risk + execution + position manager + scheduler + watchdog + alerter + reporter, under one AsyncIO loop (D-02).
- **Alert fan-out:** PositionManager/ExecutionEngine fill/exit events → TelegramAlerter (ALERT-01/02); the watchdog → TelegramAlerter on OpenD up/down (D-12); the report builder → daily summary (ALERT-03) — all fire-and-forget (D-14, ALERT-04).
- **Kill-switch handler (D-07)** registered in `main.py` before the loop; ties `flush_all` + clean gateway teardown + shutdown alert.
- **Watchdog ↔ entry path:** the watchdog toggles an entry-enabled flag the scheduler's entry jobs check (D-09); reconnect re-runs `startup_reconcile` (D-11) behind the same readiness gate as boot (D-08).
- **launchd plist** is the only non-Python deliverable — references `python -m bot...`, log paths, and `EnvironmentVariables` (paper-safety + Telegram).

</code_context>

<specifics>
## Specific Ideas

- Operator runs on **macOS** — supervision is **launchd** (native), not systemd; systemd is documented only as a Linux note (D-05).
- **Lean-dependency ethos drove every reporting choice:** zero-dep Telegram via stdlib urllib (D-14) and a zero-dep inline-SVG/CSS dashboard (D-15) — no httpx, no python-telegram-bot, no matplotlib. Only one new dep total: `apscheduler`.
- **One narrow, deliberate scope exception:** OpenD disconnect/reconnect IS alerted over Telegram (D-12) even though general system-health alerts are deferred in PROJECT.md — because SVC-02 demands it and an unattended bot going blind is precisely when the operator must be told.
- **Unattended-first reliability stance:** crash-loop guard keeps retrying + alerts rather than giving up (D-06); a needed exit is queued and never dropped during an OpenD outage (D-09); a (re)start never trades until paper-guard + OpenD + reconciliation all pass (D-08).
- **Dashboard is a daily-readable artifact, not a toy** — dated history files + a stable `latest.html` bookmark (D-16), real SVG charts, opens straight from `file://`.

</specifics>

<deferred>
## Deferred Ideas

- **Broader system-health alerting** (scan-didn't-run, scheduler-job-failed, degraded-data) — PROJECT.md keeps these log-only for v1; only the OpenD up/down event is promoted to a Telegram alert this phase (D-12). Promote the rest in a later milestone if desired.
- **Interactive / two-way Telegram** (commands to query status, pause, flatten) — v1 is one-way push only; would need a receiving bot loop (out of scope, future).
- **Server-backed / live web dashboard** — explicitly out of scope (PROJECT.md); the static no-JS file is the v1 ceiling.
- **Backtester (Phase 6)** — unchanged; remains the next phase.

None of the above were in-scope this phase; discussion stayed within the Phase-5 boundary.

</deferred>

---

*Phase: 05-service-orchestration-and-reliability*
*Context gathered: 2026-06-24*
