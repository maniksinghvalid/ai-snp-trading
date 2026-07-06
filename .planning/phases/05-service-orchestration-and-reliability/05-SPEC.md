# Phase 5: Service Orchestration and Reliability — Specification

**Created:** 2026-06-24
**Ambiguity score:** 0.08 (gate: ≤ 0.20)
**Requirements:** 9 locked

> **Note — retroactive spec.** This SPEC.md was written *after* discuss-phase (05-CONTEXT.md) and plan-phase (05-00..05-04 PLAN.md committed) had already completed. It reconstructs the locked WHAT/WHY from existing source-of-truth artifacts (ROADMAP success criteria, REQUIREMENTS.md acceptance text, 05-CONTEXT.md decisions) for documentation/audit completeness. It does not supersede those artifacts; the committed plans remain the execution contract.

## Goal

Wrap the completed Phase 1–4 scan→signal→risk→execution→position stack in a single long-running, supervised async service that runs the full daily trade lifecycle hands-off — scheduler fires the daily jobs at the correct ET times, Telegram push alerts cover every trade event, OpenD connectivity loss is detected and handled gracefully, and structured logs enable post-hoc diagnosis — with no new strategy logic.

## Background

Phases 1–4 delivered all trading primitives — `MoomooGateway` (connect/close/subscribe/reconcile/startup_reconcile + order methods), `PositionManager` (flush_all, force_close_all), `ExecutionEngine`/`FillEvent`, `KillSwitch.register_flush`, the scanner (run_daily_scan / run_intraday_rescan), the NYSE calendar, ET helpers, `StateStore` (positions/trades/daily_trade_count), the structlog rotating JSON logger (SVC-03, Phase 1), and the JSONL audit log — but **nothing composes them into one running process**. There is no orchestrator, no scheduler, no entry point that runs the bot end-to-end, no connectivity watchdog, no alerting, and no reporting. `MoomooGateway.get_global_state()` does not exist yet. The carry-forward obligation R-04-01 / T-04-21 (wire `KillSwitch.register_flush(position_manager.flush_all)` before the trade loop) is still open from Phase 4. This phase is the first that runs the whole bot as one unattended, supervised service.

## Requirements

1. **Supervised long-running service + scheduler (SVC-01)**: An always-on async process drives the full daily lifecycle via an APScheduler `AsyncIOScheduler` on a single shared event loop.
   - Current: No orchestrator, scheduler, or service entry point exists; Phase 1–4 components are never composed into a running process.
   - Target: A `TradingBot` orchestrator + `python -m bot` entry point wires all components under one asyncio loop; `AsyncIOScheduler` registers cron jobs for premarket scan, market-open subscribe, intraday re-scans, 15:51-ET force-close, and EOD report, with `coalesce` + per-job `misfire_grace_time`; a hard startup readiness gate (paper-guard + OpenD reachable + startup_reconcile) blocks entries until ready; a full graceful-shutdown handler wires `KillSwitch.register_flush` before the loop (closes R-04-01).
   - Acceptance: Running the service for one full paper-trading session end-to-end fires the premarket scan, intraday re-scan, market-open subscribe, and EOD force-close jobs at the correct ET times on a trading day; `_register_jobs()` registers the expected job IDs; entries are blocked until the readiness gate passes.

2. **Intraday re-scan scheduling (SCAN-07, scheduling half)**: The scheduler re-runs the scan intraday on a fixed cadence within a bounded midday window.
   - Current: `run_intraday_rescan` exists (Phase 2) but nothing schedules it.
   - Target: An `AsyncIOScheduler` job calls `run_intraday_rescan` ~every 30 min for ≈7 passes across the 09:55–12:55 ET window; cadence/window come from `rules.json` `service.*` (no hardcoded literals).
   - Acceptance: The intraday re-scan job is registered and, on a trading day within the window, fires ≈7 times; the job is a thin wrapper over the existing `run_intraday_rescan` (not reimplemented).

3. **OpenD connectivity watchdog (SVC-02)**: A watchdog polls OpenD global state and degrades the service safely on connectivity loss.
   - Current: No watchdog; `MoomooGateway.get_global_state()` does not exist.
   - Target: A new `MoomooGateway.get_global_state()` (deferred-SDK-import + `run_in_executor`, returns a dict with `qot_logined`/`trd_logined`) plus an `OpenDWatchdog` that polls ~every 60s; on loss it pauses new-entry placement while keeping bar-close exit checks running and queuing any triggered exit; reconnect uses capped exponential backoff; on recovery it re-runs `startup_reconcile` + re-subscribes behind the same readiness gate before re-enabling entries.
   - Acceptance: Simulating an OpenD disconnect (kill process) pauses order placement within one poll cycle and fires a Telegram alert (or logs if Telegram unconfigured); exits are never disabled during the outage; on reconnect, `startup_reconcile` runs before entries are re-enabled.

4. **Entry alert (ALERT-01)**: A Telegram alert fires on every position entry with required fields.
   - Current: No alerting exists.
   - Target: On entry, an alert containing ticker, size, entry price, and initial stop is dispatched fire-and-forget.
   - Acceptance: `format_entry_alert` output contains ticker, size, entry price, and initial stop; an entry event triggers exactly one alert dispatch.

5. **Exit alerts (ALERT-02)**: A Telegram alert fires on every exit event, for each exit reason.
   - Current: No alerting exists.
   - Target: On each exit, an alert fires covering all five exit reasons — partial, breakeven move, trail-stop, stop-out, force-close.
   - Acceptance: `format_exit_alert` produces a correct alert for each of the five `exit_reason` values; each exit event triggers a dispatch.

6. **Daily summary (ALERT-03)**: A daily Telegram summary is sent after force-close.
   - Current: No reporting exists.
   - Target: After the 15:51-ET force-close completes, a summary including trade count, win/loss split, realized PnL, and open risk is sent.
   - Acceptance: `format_daily_summary` includes trade count, win/loss split, realized PnL, and open risk; the EOD job dispatches it after force-close.

7. **Alert delivery isolation (ALERT-04)**: Alert delivery failures never block or crash the trade loop.
   - Current: No alerting; no isolation guarantee.
   - Target: Telegram transport is stdlib `urllib` POST to `api.telegram.org/sendMessage`, wrapped in `run_in_executor` + `asyncio.create_task` (truly fire-and-forget); all exceptions are caught and logged, never propagated; the alerter no-ops (logs only) when `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are unset.
   - Acceptance: A simulated `urllib` exception inside `send()` does not raise or propagate; with secrets unset, `send()` is a logged no-op; no `await` on the alerter blocks the trade loop.

8. **Static HTML dashboard (DASH-01)**: A static, offline, no-JS HTML performance dashboard is generated alongside the daily summary.
   - Current: No dashboard or report file is produced.
   - Target: A zero-dependency `.html` file with an inline-SVG R-multiple histogram, an open-positions table, and a last-20 closed-trades table, aggregated from the `StateStore` `trades`/`positions` tables via pandas; written as dated `reports/YYYY-MM-DD.html` plus a stable `reports/latest.html`; `reports/` is gitignored.
   - Acceptance: The generated HTML renders correctly from a `file://` open with no server and no JavaScript/CDN assets; it contains the R-multiple histogram, open-positions table, and last-20 closed-trades sections; the SVG histogram bucket counts match the underlying trade data.

9. **Process supervision + service-wide structured logging (SVC-01 / SVC-03 integration / SAFE-04)**: The service runs under a crash-restarting supervisor with structured rotating logs and graceful shutdown.
   - Current: The structlog rotating JSON logger (SVC-03) and `KillSwitch` (SAFE-04) exist but are not integrated into a supervised service; no supervisor config exists.
   - Target: A macOS launchd LaunchAgent `.plist` (`KeepAlive`, `RunAtLoad`, `ThrottleInterval`, `StandardOutPath`/`StandardErrorPath`, `EnvironmentVariables` carrying paper-safety + Telegram secrets) supervises `python -m bot`, with a documented shell `while`-loop fallback; the existing structlog logger is configured once at service startup; the graceful-shutdown handler flushes positions, writes a shutdown audit entry, closes the gateway cleanly, and sends a final alert.
   - Acceptance: The service starts under the supervisor and auto-restarts on crash; rotating JSON log files are written by structlog; killing the process triggers the graceful-shutdown sequence (flush + audit + gateway close + bot-stopped alert).

## Boundaries

**In scope:**
- `TradingBot` orchestrator + `python -m bot` / `bot/main.py` entry point composing Phases 1–4 under one asyncio loop
- `AsyncIOScheduler` cron jobs (premarket scan, market-open subscribe, intraday re-scans, 15:51 force-close, EOD report) with coalesce + misfire grace
- Hard startup readiness gate (paper-guard + OpenD reachable + startup_reconcile) and full graceful-shutdown handler (closes R-04-01 / T-04-21)
- New `MoomooGateway.get_global_state()` method + `OpenDWatchdog` (poll, pause-entries/queue-exits, capped backoff reconnect, re-reconcile on recovery, disconnect/reconnect alert)
- `TelegramAlerter` (zero-dep urllib fire-and-forget; entry/exit/daily-summary alerts; failure isolation; no-op when unconfigured)
- Daily P&L report + static no-JS HTML dashboard (inline SVG + CSS tables; dated + `latest.html`)
- launchd LaunchAgent `.plist` supervisor (+ documented shell-loop fallback; systemd as a Linux note) and service-wide structlog integration
- New `service.*` block in `rules.json` + `StrategyConfig` loader extension for all schedule/poll/backoff/throttle/misfire tunables (CFG-01)
- `apscheduler` added to `requirements.txt` with a package-legitimacy checkpoint
- `tests/service/` unit coverage + a full daily-lifecycle integration test

**Out of scope:**
- The backtester — that is Phase 6.
- Any new strategy/filter/signal/risk/exit logic — Phases 2–4 own it, unchanged; this phase only schedules and supervises existing behavior.
- Live/real-money trading — paper SIMULATE only; no real-money order path in this milestone.
- An interactive or server-backed web UI — the dashboard is a static `file://` artifact only.
- Two-way/interactive Telegram (commands to query/pause/flatten) — v1 is one-way push only.
- Broader system-health alerting (scan-didn't-run, job-failed, degraded-data) — remains log-only for v1; only the OpenD up/down event is promoted to a Telegram alert (a deliberate narrow exception per CONTEXT D-12).
- Reimplementing Phase-4 force-close / reconciliation / flush — Phase 5 schedules and supervises the existing implementations.

## Constraints

- **Paper-only:** the new orchestrator entry path must keep the SAFE-01 paper-safety triple-guard intact (enforced inside the readiness gate); `FUTU_TRD_ENV=SIMULATE`.
- **Single asyncio event loop (CONTEXT D-02):** `AsyncIOScheduler` and the continuous 5m bar-push/position tasks share one loop; every job coroutine must be non-blocking and offload blocking SDK/network calls via `run_in_executor`.
- **Timezone:** all scheduler timing is US Eastern; weekends/holidays/half-days no-op via the existing NYSE calendar gate.
- **Lean dependencies:** exactly one new runtime dependency (`apscheduler`, pinned, with a package-legitimacy checkpoint); Telegram transport and the dashboard are zero-dependency (stdlib `urllib`; inline SVG/CSS — no httpx, python-telegram-bot, or matplotlib).
- **No hardcoded tunables (CFG-01):** all schedule times, re-scan cadence/window, watchdog poll interval, reconnect backoff steps/cap, launchd throttle, and misfire/coalesce grace live in `rules.json` `service.*` via the `StrategyConfig` loader.
- **Secrets via env only (CONTEXT D-13):** `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` read from the environment and documented in `.env.example`; never stored in `rules.json` and never logged; the bot does not auto-load `.env`.
- **Wrap, don't import the broker scripts:** reuse `skills/moomooapi` patterns by reference only; broker access stays through `MoomooGateway`.
- **Python 3.6+ project**, but the pinned APScheduler (3.11.2) requires a modern Python 3.x runtime.

## Acceptance Criteria

- [ ] `AsyncIOScheduler` fires premarket scan, intraday re-scan (≈7 passes 09:55–12:55 ET), market-open subscribe, and 15:51 force-close jobs at correct ET times across one full paper session
- [ ] Startup readiness gate blocks entries until paper-guard + OpenD reachable + startup_reconcile all pass; exits/force-close/reconcile run regardless
- [ ] `KillSwitch.register_flush(position_manager.flush_all)` is wired before the trade loop starts (R-04-01 / T-04-21)
- [ ] `MoomooGateway.get_global_state()` exists and the watchdog polls it ~every 60s
- [ ] Simulated OpenD disconnect pauses order placement within one poll cycle and fires a Telegram alert (or logs if unconfigured); exits are never disabled; reconnect re-runs `startup_reconcile` before re-enabling entries
- [ ] Entry alert contains ticker, size, entry price, and initial stop
- [ ] Exit alerts fire for all five exit reasons (partial, breakeven, trail, stop-out, force-close)
- [ ] Daily summary after force-close includes trade count, win/loss, realized PnL, and open risk
- [ ] A simulated alert-delivery failure does not raise or propagate into the trade loop; alerter no-ops when secrets are unset
- [ ] Static no-JS HTML dashboard renders from `file://` with no server — R-multiple histogram + open-positions table + last-20 closed-trades; SVG bucket counts match data
- [ ] Service starts under launchd, auto-restarts on crash, and writes structured rotating JSON logs
- [ ] All schedule/poll/backoff/throttle/misfire values are read from `rules.json` `service.*` (no hardcoded literals); no Telegram secret appears in `rules.json`

## Ambiguity Report

| Dimension          | Score | Min  | Status | Notes                                                        |
|--------------------|-------|------|--------|--------------------------------------------------------------|
| Goal Clarity       | 0.92  | 0.75 | ✓      | Precise goal + 6 measurable ROADMAP success criteria         |
| Boundary Clarity   | 0.95  | 0.70 | ✓      | Explicit in/out-of-scope reconstructed from 05-CONTEXT.md     |
| Constraint Clarity | 0.88  | 0.65 | ✓      | Paper-only, single loop, ET, lean-deps, env-secrets, CFG-01  |
| Acceptance Criteria| 0.90  | 0.70 | ✓      | Per-requirement acceptance + 12 pass/fail checkboxes         |
| **Ambiguity**      | 0.08  | ≤0.20| ✓      | Phase already discussed + planned; WHAT fully locked         |

Status: ✓ = met minimum, ⚠ = below minimum (planner treats as assumption)

## Interview Log

This SPEC.md was generated retroactively after discuss-phase and plan-phase completed; no live Socratic interview was run. Requirements were reconstructed from committed source-of-truth artifacts. The gate passed on the first assessment (ambiguity 0.08).

| Round | Perspective     | Source of truth                                  | Decision locked                                            |
|-------|-----------------|--------------------------------------------------|-----------------------------------------------------------|
| —     | Researcher      | REQUIREMENTS.md (SVC/ALERT/DASH IDs), STATE.md   | 8 phase req IDs + SVC-03 integration + R-04-01 carry-forward |
| —     | Simplifier      | ROADMAP "Phase 5" goal + 6 success criteria      | Irreducible core = supervised service running the daily loop end-to-end |
| —     | Boundary Keeper | 05-CONTEXT.md domain + deferred sections         | Out-of-scope: backtester, new strategy logic, live trading, web UI, two-way Telegram, broad health alerts |
| —     | Failure Analyst | 05-CONTEXT.md D-08/D-09/D-12, ALERT-04           | Readiness gate, queued exits on disconnect, alert isolation are the reject-conditions |
| —     | Seed Closer     | 05-CONTEXT.md D-01..D-16, CFG-01                 | Constraints: single loop, paper-only, env-secrets, no hardcoded tunables, one new dep |

---

*Phase: 05-service-orchestration-and-reliability*
*Spec created: 2026-06-24 (retroactive)*
*Next step: Phase 5 is already planned — proceed to /gsd-execute-phase 5. (discuss-phase and plan-phase already completed; re-running them is only needed if you intend to replan.)*
