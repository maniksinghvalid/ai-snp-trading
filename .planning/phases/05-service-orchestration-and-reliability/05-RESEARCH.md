# Phase 5: Service Orchestration and Reliability — Research

**Researched:** 2026-06-24
**Domain:** AsyncIO service orchestration, process supervision (launchd), Telegram Bot API (stdlib),
static HTML report generation, APScheduler AsyncIOScheduler, OpenD watchdog integration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Always-on 24/7 process. One long-lived process; `AsyncIOScheduler` owns all daily timing.
- **D-02:** Single AsyncIO event loop. `AsyncIOScheduler` and the continuous 5m bar-push / position async tasks share **one** event loop. Every job coroutine non-blocking; blocking calls via `run_in_executor`.
- **D-03:** Smart per-job misfire handling. `coalesce=True` + per-job `misfire_grace_time`. Misfire-grace / coalesce values → `rules.json` (CFG-01).
- **D-04:** Lazy session-date keying for daily rollover. No reset job. All session-scoped state keyed by today's ET `session_date`.
- **D-05:** launchd LaunchAgent supervisor. macOS-native. `.plist` with `KeepAlive=true`, `RunAtLoad=true`, `ThrottleInterval`, `StandardOutPath`/`StandardErrorPath`. Portable shell `while`-loop fallback documented.
- **D-06:** Throttle + alert on crash-loop, keep trying. `ThrottleInterval` (e.g. 30–60s, → config); fire Telegram alert on rapid crashes but keep attempting.
- **D-07:** Full graceful shutdown handler (closes R-04-01 / T-04-21). `main.py` registers `KillSwitch.register_flush(handler)` before the trade loop starts. Handler: (1) `position_manager.flush_all()`, (2) shutdown audit entry, (3) unsubscribe feeds + `gateway.close()`, (4) fire "bot stopped" Telegram alert.
- **D-08:** Hard startup readiness gate before entries. Block entry path until: paper guard passes (SAFE-01), OpenD reachable via `get_global_state()`, Phase-4 `startup_reconcile` completes. Exits/force-close/reconciliation run regardless.
- **D-09:** Pause entries, keep exits queued. On OpenD loss, suspend new-entry placement immediately. Bar-close stop/partial/breakeven checks keep running; exit orders queued and fired on reconnect.
- **D-10:** Exponential backoff, capped, on reconnect. Reconnect attempts back off (e.g. 5s→10s→30s→cap 60s, → `rules.json`).
- **D-11:** Re-run startup reconciliation on recovery. After reconnect, re-run `startup_reconcile` + re-subscribe 5m feeds before re-enabling entries (reuses D-08 gate).
- **D-12:** Telegram-alert OpenD disconnect AND reconnect. Fire on disconnect and recovery. Narrow exception to PROJECT.md "system-health alerts log-only" policy — explicitly in scope for SVC-02.
- **D-13:** Telegram secrets via env vars + `.env.example`. `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` read from environment. `rules.json` holds only non-secret toggles (`alerts_enabled`, poll/schedule intervals).
- **D-14:** Zero-dependency Telegram transport. POST via **stdlib `urllib`**, wrapped in `run_in_executor` + `asyncio.create_task` for truly fire-and-forget. No new dependency.
- **D-15:** No-JS dashboard via inline SVG + CSS. R-multiple histogram as inline `<svg>` bars; tables as HTML/CSS. One self-contained `.html` file. No matplotlib, no image files, no external assets.
- **D-16:** Dated report files + stable `latest.html`. Write `reports/YYYY-MM-DD.html` + stable `reports/latest.html` copy after 15:51 force-close.

### Claude's Discretion

- Numeric tunables → `rules.json` (CFG-01): exact cron/schedule ET times, intraday re-scan cadence, watchdog poll interval, reconnect backoff steps + cap (D-10), launchd `ThrottleInterval` (D-06), misfire/coalesce grace (D-03), crash-loop alert threshold (D-06).
- Module/dataclass decomposition — 4 planned slices (05-01 orchestrator/scheduler, 05-02 watchdog, 05-03 TelegramAlerter, 05-04 report+dashboard+supervisor+log integration); exact class names (`TradingBot`, `OpenDWatchdog`, `TelegramAlerter`, report builder), where `main.py` / `__main__.py` lives, alert event-fan-out wiring.
- Alert message format/content layout — beyond required fields.
- `get_global_state()` gateway method — does NOT exist yet on `MoomooGateway`. Watchdog (05-02) adds it, mirroring deferred-SDK-import + `run_in_executor` pattern.
- APScheduler dependency — `apscheduler` is the new runtime dependency (package-legitimacy checkpoint required per Phase-2 precedent).
- Integration-test design — end-to-end session test (criterion #1) and OpenD-disconnect simulation (criterion #2).

### Deferred Ideas (OUT OF SCOPE)

- Broader system-health alerting (scan-didn't-run, scheduler-job-failed, degraded-data) — log-only for v1.
- Interactive / two-way Telegram (commands to query status, pause, flatten) — one-way push only.
- Server-backed / live web dashboard — static file is v1 ceiling.
- Backtester (Phase 6) — next phase.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SVC-01 | Long-running supervised service with internal scheduler (premarket scan → intraday loop → EOD flatten) | APScheduler AsyncIOScheduler cron/interval jobs + launchd LaunchAgent plist |
| SVC-02 | OpenD connectivity watchdog (poll `get_global_state` ~every 60s); pause order placement on failure | `get_global_state()` SDK surface verified; watchdog adds new MoomooGateway method |
| SCAN-07 | Scan re-runs intraday on schedule (~every 30 min, ≈7 passes 09:55–12:55 ET) | AsyncIOScheduler interval trigger + `run_intraday_rescan()` wiring |
| ALERT-01 | Telegram alert on entry (ticker, size, entry price, initial stop) | stdlib urllib POST pattern confirmed; TelegramAlerter fire-and-forget design |
| ALERT-02 | Telegram alert on each exit event (partial, breakeven move, trail-stop, stop-out, force-close) | Alert fan-out from PositionManager/ExecutionEngine event callbacks |
| ALERT-03 | Daily Telegram summary after force-close (trades, win/loss, realized PnL, open risk) | `trades` table schema confirmed (`exit_reason`, `r_multiple`); pandas aggregation |
| ALERT-04 | Alert delivery failures never block or crash the trade loop | `asyncio.create_task` fire-and-forget + try/except swallow in TelegramAlerter |
| DASH-01 | Static offline no-JS HTML dashboard: R-multiple histogram, open-positions table, last-20 closed trades | Inline SVG + CSS confirmed; `trades` and `positions` tables provide all data |
</phase_requirements>

---

## Summary

Phase 5 is the **integration and hardening phase** that wraps the already-built Phases 1–4 stack
(scan → signal → risk → execution → position) in a long-running supervised async service. No new
strategy logic is required. The primary work is:

1. **Orchestration** — composing all Phase 1–4 components under a single `asyncio` event loop
   controlled by an `APScheduler AsyncIOScheduler`, with cron jobs for each lifecycle milestone.
2. **Reliability** — an OpenD connectivity watchdog that detects broker disconnects, queues exits,
   reconnects with backoff, and fires Telegram alerts.
3. **Observability** — fire-and-forget Telegram alerts (stdlib `urllib`, no new dep) and a static
   HTML dashboard generated from `StateStore.trades` + `StateStore.positions`.
4. **Supervision** — a launchd LaunchAgent plist for crash auto-restart on macOS.

The only new runtime dependency is `apscheduler`. All other new functionality uses Python stdlib
(`urllib`, `asyncio`, `html`, `string`) or existing project deps (`pandas` for aggregation,
`structlog` already wired). The core integration complexity is the component-wiring in `main.py` /
`TradingBot` and the stateful flag (`_entries_enabled`) the watchdog toggles.

**Primary recommendation:** Add `apscheduler==3.11.2` to `requirements.txt`, implement `main.py`
as `TradingBot` with an `AsyncIOScheduler` on the single existing event loop, and wire all
Phase 1–4 components as injected dependencies. All tunables go to `rules.json`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Daily scheduling (cron jobs) | Service / orchestrator (`TradingBot`) | `APScheduler AsyncIOScheduler` | Owns all timing; wraps existing Phase 1–4 functions as cron job callbacks |
| OpenD connectivity | Watchdog (`OpenDWatchdog`) | `MoomooGateway.get_global_state()` | Polls broker health; enables/disables entry path via shared flag |
| Alert delivery | `TelegramAlerter` (new module) | stdlib `urllib` via `run_in_executor` | Fire-and-forget; never blocks or raises to caller (ALERT-04) |
| Entry/exit event fan-out | `TradingBot` wiring / callback injection | `PositionManager`, `ExecutionEngine` | Callback pattern preferred over event bus for single-process simplicity |
| HTML report generation | `ReportBuilder` (new module) | `StateStore` tables, `pandas`, inline SVG | `trades` + `positions` are the canonical data source; no server required |
| Process supervision | launchd LaunchAgent (`com.yourname.bot.plist`) | Shell `while`-loop fallback | macOS-native; no Python dep needed for crash-restart |
| Graceful shutdown | `main.py` / `KillSwitch` wiring | `PositionManager.flush_all()`, `gateway.close()` | R-04-01 carry-forward: `KillSwitch.register_flush()` registered before loop starts |
| Structured logging | `bot/safety/logger.py` (already built) | `structlog` (already wired, Phase 1) | Phase 5 integrates service-wide; no new logging stack |

---

## Standard Stack

### Core (Phase 5 only adds one new runtime dep)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `apscheduler` | 3.11.2 | `AsyncIOScheduler` + `CronTrigger` + `IntervalTrigger` for all bot timing | Industry-standard Python scheduler; v3.11.2 is current stable (v4 remains pre-release/dev). 36.3M downloads/month (PyPI). Supports async native coroutines directly. [VERIFIED: pypi.org] |
| `urllib` (stdlib) | built-in | Telegram Bot API HTTPS POST (`sendMessage`) | Zero-dep delivery; POST with `urllib.request.urlopen`; `run_in_executor` keeps it off the event loop (D-14) [VERIFIED: Telegram Bot API official docs] |
| `asyncio` (stdlib) | built-in | Single event loop; `create_task` for fire-and-forget alert dispatch | All Phase 1–4 code already async-native (D-02) [ASSUMED] |
| `pandas` (already in `requirements.txt`) | ≥2.0,<4.0 | Aggregation of `trades` table for daily summary and dashboard SVG | Already a dependency; `groupby`, `tail(20)`, simple column aggregation [ASSUMED] |
| `structlog` (already in `requirements.txt`) | 26.1.0 | Structured rotating JSON logs (SVC-03 — already implemented Phase 1) | Service-wide integration is config at startup; no new logger instances [ASSUMED] |

### Supporting (no new installs)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `zoneinfo` (stdlib) | Python 3.9+ built-in | ET timezone for all cron triggers | All APScheduler `CronTrigger` timezone params take `ZoneInfo("America/New_York")` [VERIFIED: APScheduler 3.x docs] |
| `html` (stdlib) | built-in | HTML entity escaping for dashboard output | `html.escape()` prevents XSS in trade ticker/reason strings in the HTML report [ASSUMED] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `apscheduler` AsyncIOScheduler | Naked `asyncio` tasks with manual `datetime` comparison | APScheduler handles DST-aware cron, misfire/coalesce, job persistence, DRY callbacks; raw tasks require hand-rolling all of this |
| stdlib `urllib` for Telegram | `python-telegram-bot`, `httpx`, `requests` | Zero dep vs. one-more dep with retry wrappers; `urllib` is fully sufficient for one-way push-only delivery at low volume |
| Inline SVG string generation | `matplotlib`, `pygal`, `svg.charts` | Those require new dependencies; hand-generated SVG is 30–50 lines of Python string ops and renders from `file://` identically |
| launchd LaunchAgent | `supervisor` (supervisord) | `supervisord` is simpler in configuration but adds a Python dep. For macOS, launchd is OS-native, zero install, logs via OS, and survives reboots without any additional service management |

**Installation (only the new dep):**
```bash
pip install "apscheduler==3.11.2"
```

**Version verification:**
```bash
# APScheduler confirmed on PyPI:
# version: 3.11.2, uploaded: 2025-12-22, license: MIT, python_requires: >=3.8
# 36.3M downloads/month, 8.9M last week (pypistats.org 2026-06-24)
```

---

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| `apscheduler` | PyPI | 14+ yrs | 36.3M/month | github.com/agronholm/apscheduler | N/A (slopcheck unavailable) | Approved — [VERIFIED: pypi.org] official, MIT license, 7.5k GitHub stars, pushed 2026-06-22, 768 forks, actively maintained |

**Packages removed due to slopcheck [SLOP] verdict:** none

**Packages flagged as suspicious [SUS]:** none

*slopcheck was unavailable at research time. The package above is tagged `[VERIFIED: pypi.org]` based on manual cross-verification: PyPI registry existence + official GitHub source repo confirmed + high download volume + active maintenance history. This exceeds the standard slopcheck equivalent for a package of this age and provenance.*

---

## Architecture Patterns

### System Architecture Diagram

```
                        ┌─────────────────────────────────────────┐
                        │         launchd LaunchAgent             │
                        │  crash-restart + ThrottleInterval       │
                        └────────────────┬────────────────────────┘
                                         │ spawn
                                         ▼
                        ┌─────────────────────────────────────────┐
                        │          main.py / TradingBot           │
                        │  - KillSwitch.register_flush() wired    │
                        │  - D-08 startup readiness gate          │
                        │  - asyncio.run(bot.run())               │
                        └───┬────────────┬─────────────┬──────────┘
                            │            │             │
         ┌──────────────────▼──┐  ┌──────▼──────┐  ┌──▼─────────────────┐
         │  AsyncIOScheduler   │  │  OpenDWatch │  │  TelegramAlerter   │
         │  (APScheduler 3.x)  │  │  dog 05-02  │  │  (stdlib urllib)   │
         │                     │  │             │  │  fire-and-forget   │
         │ CronTrigger jobs:   │  │ poll 60s    │  │  create_task       │
         │  premarket_scan     │  │ get_global  │  └──────────┬─────────┘
         │  market_open_sub    │  │ _state()    │             │ alert fan-out
         │  intraday_rescan×7  │  │             │             │
         │  force_close        │  │ ─entries_   │  ┌──────────▼─────────┐
         │  eod_report         │  │  enabled ──►├─►│ ReportBuilder 05-04│
         └──────────┬──────────┘  │ flag        │  │ trades + positions │
                    │             │             │  │ → dated HTML +     │
                    │ schedule    │ reconnect   │  │   latest.html      │
                    │ callbacks   │ backoff     │  └────────────────────┘
                    ▼             │ (D-10)      │
         ┌──────────────────────────────────────▼──────────────────────┐
         │                  Phase 1–4 Components                       │
         │                                                             │
         │  MoomooGateway ──► BarAggregator ──► SignalEngine          │
         │       │                                    │               │
         │       │ subscribe/unsubscribe         RiskEngine           │
         │       │                                    │               │
         │  StateStore ◄── PositionManager ◄── ExecutionEngine        │
         │       │             │                                      │
         │       │         flush_all() ◄── KillSwitch                 │
         │       │         force_close_all()                          │
         │  migrations                                                 │
         └─────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
bot/
├── service/
│   ├── __init__.py
│   ├── bot.py              # TradingBot class (05-01): AsyncIOScheduler + component wiring
│   ├── watchdog.py         # OpenDWatchdog class (05-02): get_global_state poll loop
│   ├── alerter.py          # TelegramAlerter class (05-03): urllib fire-and-forget
│   └── report.py           # ReportBuilder class (05-04): HTML + SVG dashboard
├── main.py                 # Entry point: constructs all components, wires KillSwitch, asyncio.run
└── ... (existing bot/ subdirs unchanged)

reports/                    # gitignored; generated per-day
├── 2026-06-24.html
└── latest.html

deploy/                     # new: launchd + shell supervisor artifacts
└── com.bot.trading.plist   # LaunchAgent plist template

tests/
└── service/                # new tests directory (05-01 through 05-04)
    ├── __init__.py
    ├── test_bot.py
    ├── test_watchdog.py
    ├── test_alerter.py
    └── test_report.py
```

---

### Pattern 1: AsyncIOScheduler with CronTrigger (ET-timezone, async coroutine jobs)

**What:** APScheduler `AsyncIOScheduler` natively schedules `async def` coroutines. When the
scheduled function is a native coroutine, it is scheduled to run directly in the event loop
(not in the thread pool). The scheduler is started with `scheduler.start()` from within the
running asyncio context.

**When to use:** All daily lifecycle jobs (premarket scan, market-open subscribe, intraday
re-scans, force-close, EOD report). Each job is an `async def` method so it runs on the
shared event loop (D-02) without spawning threads.

```python
# Source: APScheduler 3.x docs (apscheduler.readthedocs.io/en/3.x)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

class TradingBot:
    def __init__(self, cfg, gateway, store, scanner, position_manager,
                 execution_engine, watchdog, alerter, kill_switch):
        self._scheduler = AsyncIOScheduler(timezone=ET)
        # ... store all injected components ...

    def _register_jobs(self) -> None:
        """Register all cron/interval jobs with the scheduler."""
        # Premarket scan — runs once, ~08:30 ET (config-driven)
        self._scheduler.add_job(
            self._job_premarket_scan,
            CronTrigger(hour=8, minute=30, timezone=ET),
            id="premarket_scan",
            coalesce=True,
            misfire_grace_time=3600,    # → rules.json: scanner.misfire_grace_scan_s
        )
        # Market-open subscribe — 09:30 ET
        self._scheduler.add_job(
            self._job_market_open_subscribe,
            CronTrigger(hour=9, minute=30, timezone=ET),
            id="market_open_subscribe",
            coalesce=True,
            misfire_grace_time=3600,
        )
        # Intraday re-scan — every ~30 min, 09:55–12:55 ET (SCAN-07)
        # Use IntervalTrigger with start_date/end_date expressed in ET
        self._scheduler.add_job(
            self._job_intraday_rescan,
            IntervalTrigger(minutes=30, timezone=ET),  # ~7 passes
            id="intraday_rescan",
            coalesce=True,
            misfire_grace_time=600,     # → rules.json: scanner.misfire_grace_rescan_s
        )
        # Force-close — calendar-aware (computed from get_market_close_et - 9 min)
        # Schedule as a cron at the nominal 15:51 ET; the Phase-4 force_close_all()
        # itself checks actual market close time internally (half-day aware).
        self._scheduler.add_job(
            self._job_force_close,
            CronTrigger(hour=15, minute=51, timezone=ET),
            id="force_close",
            coalesce=True,
            misfire_grace_time=300,     # → rules.json: force_close.misfire_grace_s
        )
        # EOD report — triggered after force-close completes (15:55 ET)
        self._scheduler.add_job(
            self._job_eod_report,
            CronTrigger(hour=15, minute=55, timezone=ET),
            id="eod_report",
            coalesce=True,
            misfire_grace_time=3600,
        )

    async def _job_premarket_scan(self) -> None:
        """Premarket scan job — thin wrapper over run_daily_scan (SCAN-07)."""
        from bot.scanner.scanner import run_daily_scan
        if not self._calendar_is_trading_day():
            return
        run_daily_scan(self._store, self._gateway, self._cfg)

    async def run(self) -> None:
        """Main bot lifecycle: gate → scheduler start → loop until kill-switch."""
        # D-08: startup readiness gate
        await self._readiness_gate()
        self._register_jobs()
        self._scheduler.start()
        try:
            while not self._kill_switch.triggered:
                self._kill_switch.check_file() and self._kill_switch.trigger("sentinel_file")
                await asyncio.sleep(1)
        finally:
            self._scheduler.shutdown(wait=False)
            # D-07 graceful shutdown runs via KillSwitch callbacks
```

**Key details (verified from APScheduler 3.x docs):**

- `coalesce=True` — if the scheduler fires after multiple missed executions of the same job, it
  runs it only once (not N times in a burst). Correct for cron jobs where running stale passes
  would be wrong.
- `misfire_grace_time` (seconds) — how late a job may fire and still be considered valid.
  For a premarket scan: if the bot boots at 08:45 ET and the 08:30 job was missed, `grace=3600`
  lets it still run (it fires immediately). For force-close: `grace=300` means if boot is at
  15:53 ET, force-close fires immediately — this is exactly D-03's "still valid late" behavior.
- `AsyncIOScheduler` is the correct scheduler class when the application is already on an asyncio
  event loop. `BackgroundScheduler` would add a separate thread — violates D-02.
- `timezone=ET` on the scheduler sets the default. Pass `timezone=ET` on each trigger also for
  clarity and DST correctness.
- `scheduler.start()` may be called from within a running event loop (`asyncio.run`); no
  `asyncio.get_event_loop().run_forever()` call is needed.

---

### Pattern 2: OpenD get_global_state() — new MoomooGateway method (05-02)

**What:** The moomoo SDK's `OpenQuoteContext.get_global_state()` returns a dict (not a
DataFrame) with keys including `qot_logined` (bool), `trd_logined` (bool),
`market_us`, `market_hk`, `server_ver`. A return of `RET_OK` with `qot_logined=True` and
`trd_logined=True` confirms OpenD is alive and authenticated. Any non-`RET_OK` return or
an exception means OpenD is disconnected.

**Pattern verified from:** `skills/moomooapi/scripts/quote/get_global_state.py` (reference
implementation) and `PITFALLS.md §Pitfall 2`.

```python
# Source: skills/moomooapi/scripts/quote/get_global_state.py (project codebase)
# New method to add to MoomooGateway in bot/gateway/gateway.py (05-02)

async def get_global_state(self) -> dict:
    """Poll OpenD global state for watchdog health check (SVC-02, D-09/D-11).

    Returns a dict with at minimum:
      - 'connected': bool — True if ret==RET_OK AND qot_logined==True
      - 'qot_logined': bool
      - 'trd_logined': bool
      - 'server_ver': str
      - 'market_us': market-state enum string

    Returns {'connected': False} on any error (never raises — watchdog treats
    any exception as a disconnect event and starts the reconnect sequence).

    Mirrors the deferred-SDK-import + run_in_executor pattern from subscribe().
    """
    try:
        loop = asyncio.get_running_loop()

        def _blocking():
            ret, data = self._quote_ctx.get_global_state()
            return ret, data

        ret, data = await loop.run_in_executor(None, _blocking)

        if ret != RET_OK or not data:
            return {"connected": False, "qot_logined": False, "trd_logined": False}

        # data is a dict (not a DataFrame) per skills/quote/get_global_state.py
        connected = bool(data.get("qot_logined", False)) and bool(data.get("trd_logined", False))
        return {
            "connected": connected,
            "qot_logined": bool(data.get("qot_logined", False)),
            "trd_logined": bool(data.get("trd_logined", False)),
            "server_ver": str(data.get("server_ver", "")),
            "market_us": str(data.get("market_us", "")),
        }
    except Exception:
        _logger.warning("get_global_state_exception", exc_info=True)
        return {"connected": False, "qot_logined": False, "trd_logined": False}
```

**Watchdog design (OpenDWatchdog class, 05-02):**

```python
# bot/service/watchdog.py — OpenDWatchdog
import asyncio

class OpenDWatchdog:
    """Poll get_global_state() ~every 60s; manage _entries_enabled flag (D-09/D-10/D-11)."""

    def __init__(self, gateway, bot, alerter, cfg):
        self._gateway = gateway
        self._bot = bot           # TradingBot reference to toggle _entries_enabled
        self._alerter = alerter
        self._cfg = cfg
        self._connected = True    # optimistic initial state
        self._backoff_s = 5.0     # initial reconnect interval (→ rules.json: watchdog.reconnect_initial_s)

    async def run(self) -> None:
        """Main watchdog coroutine — runs for the lifetime of the bot."""
        poll_interval = self._cfg.service.watchdog_poll_interval_s   # ~60s → rules.json
        while True:
            await asyncio.sleep(poll_interval)
            await self._check_once()

    async def _check_once(self) -> None:
        state = await self._gateway.get_global_state()
        if state["connected"] and not self._connected:
            # Reconnected: re-run D-11 reconciliation, re-enable entries
            await self._on_reconnect()
        elif not state["connected"] and self._connected:
            # Disconnected: pause entries, alert
            await self._on_disconnect()

    async def _on_disconnect(self) -> None:
        self._connected = False
        self._bot._entries_enabled = False    # D-09
        asyncio.create_task(
            self._alerter.send("⚠️ OpenD DISCONNECTED — new entries paused. Reconnecting...")
        )
        # Start reconnect task with exponential backoff (D-10)
        asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Exponential backoff reconnect (D-10). Cap from rules.json."""
        cap_s = self._cfg.service.watchdog_reconnect_cap_s    # e.g. 60 → rules.json
        delay = self._cfg.service.watchdog_reconnect_initial_s  # e.g. 5 → rules.json
        while not self._connected:
            await asyncio.sleep(delay)
            try:
                self._gateway.connect()       # re-create quote/trade contexts
                await self._on_reconnect()
                break
            except Exception:
                delay = min(delay * 2, cap_s)   # exponential backoff

    async def _on_reconnect(self) -> None:
        self._connected = True
        # D-11: re-run startup_reconcile + re-subscribe before enabling entries
        await self._gateway.startup_reconcile(self._bot._store, self._bot._position_manager)
        self._bot._entries_enabled = True
        asyncio.create_task(
            self._alerter.send("✅ OpenD RECONNECTED — entries re-enabled.")
        )
        self._backoff_s = 5.0    # reset backoff
```

---

### Pattern 3: TelegramAlerter — zero-dep stdlib urllib fire-and-forget (D-14, ALERT-04)

**What:** HTTP POST to `https://api.telegram.org/bot{TOKEN}/sendMessage` with
`application/x-www-form-urlencoded` payload containing `chat_id` and `text`. Wraps the
blocking `urllib.request.urlopen` in `loop.run_in_executor(None, ...)` so it never blocks
the event loop. Dispatches from `asyncio.create_task` so failures are isolated.

**Endpoint verified from:** [Telegram Bot API official docs](https://core.telegram.org/bots/api#sendmessage)

```python
# bot/service/alerter.py — TelegramAlerter
import asyncio
import urllib.parse
import urllib.request

class TelegramAlerter:
    """Fire-and-forget Telegram push alerts via stdlib urllib (D-14, ALERT-04).

    - Never raises to the caller (all exceptions caught + logged internally).
    - Non-blocking: urllib.urlopen runs in run_in_executor; caller uses create_task.
    - Failures are logged; they never reach the trade loop.
    """

    _API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    _TIMEOUT_S = 10   # HTTP timeout; not worth blocking longer (ALERT-04)

    def __init__(self, token: str, chat_id: str, logger=None):
        self._token = token
        self._chat_id = chat_id
        self._logger = logger
        self._enabled = bool(token and chat_id)

    async def send(self, text: str) -> None:
        """Send text to the configured Telegram chat. Fire-and-forget.

        Catches all exceptions; never propagates (ALERT-04).
        """
        if not self._enabled:
            if self._logger:
                self._logger.debug("telegram_not_configured", text_preview=text[:80])
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._post_blocking, text)
        except Exception:
            if self._logger:
                self._logger.warning("telegram_send_failed", exc_info=True)

    def _post_blocking(self, text: str) -> None:
        """Blocking HTTP POST — called in executor thread (not on the event loop)."""
        url = self._API_URL.format(token=self._token)
        payload = urllib.parse.urlencode({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=self._TIMEOUT_S) as resp:
            resp.read()   # consume response; don't parse (we only care it sent)
```

**Fire-and-forget dispatch pattern:**
```python
# From TradingBot or PositionManager callback wiring:
asyncio.create_task(alerter.send(f"🟢 ENTRY {code} | {qty} @ {entry_price:.2f} | stop {stop:.2f}"))
```

**Alert fan-out wiring:** At `TradingBot` construction time, inject `alerter` callbacks into
`PositionManager` and `ExecutionEngine` via optional callback parameters (or simple lambda
injection). The alerter is NOT imported directly from those modules — it is passed in at
wiring time to preserve separation of concerns and keep Phases 1–4 classes dependency-free
from the alerter.

**Key constraints:**
- Telegram `sendMessage` rate limit: 30 messages/second per bot, 20 messages/minute per chat.
  This bot sends < 10 alerts/day in normal operation — no batching needed.
- `parse_mode: "HTML"` allows `<b>` and `<code>` tags in alert text for readability.
- `text` must be 1–4096 characters. Daily summary may be long; truncate if necessary.

---

### Pattern 4: launchd LaunchAgent plist (D-05)

**What:** A `.plist` XML file dropped in `~/Library/LaunchAgents/` that tells launchd to
start `python -m bot` at login (`RunAtLoad=true`), keep it alive after crash (`KeepAlive=true`),
and impose a minimum restart interval (`ThrottleInterval`) to avoid tight crash loops (D-06).

**Verified from:** [launchd.info](https://launchd.info/) official launchd documentation.

```xml
<!-- deploy/com.bot.trading.plist — TEMPLATE, paths must be filled in -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bot.trading</string>

    <key>ProgramArguments</key>
    <array>
        <!-- Use the full path to the venv python to avoid PATH issues in launchd -->
        <string>/Users/OPERATOR/path/to/venv/bin/python</string>
        <string>-m</string>
        <string>bot</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/OPERATOR/path/to/project</string>

    <!-- KeepAlive: restart on crash (D-05) -->
    <key>KeepAlive</key>
    <true/>

    <!-- RunAtLoad: start when the LaunchAgent is loaded / at login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- ThrottleInterval: minimum seconds between restarts (D-06 crash-loop guard)  -->
    <!-- Tunable; 30–60s keeps fast self-healing without CPU burn on repeated crash  -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <!-- Env vars: paper-safety + Telegram secrets (D-05/D-13)                      -->
    <!-- The bot does NOT auto-load .env; env vars must be supplied here             -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PAPER_TRADING</key>
        <string>true</string>
        <key>FUTU_TRD_ENV</key>
        <string>SIMULATE</string>
        <key>FUTU_OPEND_HOST</key>
        <string>127.0.0.1</string>
        <key>FUTU_OPEND_PORT</key>
        <string>11111</string>
        <key>TELEGRAM_BOT_TOKEN</key>
        <string>YOUR_TOKEN_HERE</string>
        <key>TELEGRAM_CHAT_ID</key>
        <string>YOUR_CHAT_ID_HERE</string>
        <!-- PATH must be explicit; launchd does not source shell profiles (D-05 note) -->
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <!-- Logs: redirect stdout/stderr to dated files (SVC-03) -->
    <key>StandardOutPath</key>
    <string>/Users/OPERATOR/path/to/project/logs/bot.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/OPERATOR/path/to/project/logs/bot.stderr.log</string>
</dict>
</plist>
```

**launchctl commands:**
```bash
# Load and start the agent (run once after placing the plist):
launchctl load ~/Library/LaunchAgents/com.bot.trading.plist

# Check status:
launchctl list | grep com.bot.trading

# Stop and unload:
launchctl unload ~/Library/LaunchAgents/com.bot.trading.plist
```

**Shell `while`-loop fallback (portable, documented for quick-run / Linux):**
```bash
#!/usr/bin/env bash
# deploy/run_forever.sh — portable fallback supervisor
set -a; source .env; set +a
while true; do
    python -m bot
    EXIT=$?
    echo "[$(date)] bot exited with code $EXIT — restarting in 30s..."
    sleep 30
done
```

**ThrottleInterval guidance:**
`ThrottleInterval=30` means launchd waits at least 30 seconds between crash-restarts. A value
of 30–60s balances fast self-healing (transient OpenD restart) against CPU burn on persistent
bugs (D-06). If the bot crashes repeatedly faster than `ThrottleInterval`, fire a Telegram alert
after N rapid consecutive crashes. The crash-loop counter can be tracked in the bot itself by
reading the launchd restart cadence or simply counting how soon after start the `run()` method
exits without completing a full session.

---

### Pattern 5: Static HTML + inline SVG R-multiple histogram (DASH-01, D-15)

**What:** Generate a self-contained `.html` file from Python string operations. No external
library, no JS, no CSS CDN. The R-multiple histogram is an inline `<svg>` element with
`<rect>` bars computed from pandas-aggregated `trades` table rows. Tables use plain `<table>`
elements with CSS classes defined in a `<style>` block in the same file.

```python
# bot/service/report.py — ReportBuilder (partial)
import html
from datetime import date

def build_daily_html(trades_rows: list, positions_rows: list, session_date: date) -> str:
    """Build a self-contained daily HTML report (DASH-01, D-15/D-16).

    trades_rows:    list of dicts from StateStore trades table (exit_reason, r_multiple, etc.)
    positions_rows: list of dicts from StateStore positions table (open positions)
    session_date:   The trading session date (ET-correct)

    Returns a complete HTML string. Call from _job_eod_report() after force_close_all().
    """
    # --- Aggregate for summary ---
    n_trades = len(trades_rows)
    wins = [t for t in trades_rows if (t.get("r_multiple") or 0) > 0]
    losses = [t for t in trades_rows if (t.get("r_multiple") or 0) <= 0]
    total_pnl = sum(t.get("exit_price", 0) * t.get("quantity", 0) -
                    t.get("entry_price", 0) * t.get("quantity", 0)
                    for t in trades_rows)

    # --- SVG Histogram ---
    svg = _build_r_histogram(trades_rows)

    # --- Last 20 closed trades table ---
    last20 = trades_rows[-20:]  # already sorted by closed_at via query

    rows_html = "".join(
        f"<tr>"
        f"<td>{html.escape(str(t.get('code','')))} </td>"
        f"<td>{html.escape(str(t.get('exit_reason','')))}</td>"
        f"<td class='num'>{t.get('r_multiple',0):.2f}R</td>"
        f"</tr>"
        for t in last20
    )

    # --- Open positions table ---
    open_html = "".join(
        f"<tr><td>{html.escape(str(p.get('code','')))}</td>"
        f"<td>{html.escape(str(p.get('phase','')))}</td>"
        f"<td class='num'>{p.get('remaining_quantity',0)}</td></tr>"
        for p in positions_rows
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<title>Trading Report {session_date}</title>
<style>
  body {{ font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }}
  h1 {{ color: #58a6ff; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th {{ background: #161b22; text-align: left; padding: 6px 12px; }}
  td {{ padding: 4px 12px; border-bottom: 1px solid #21262d; }}
  .num {{ text-align: right; }}
  .win {{ color: #3fb950; }}
  .loss {{ color: #f85149; }}
</style>
</head>
<body>
<h1>Daily Report — {session_date}</h1>
<p><b>Trades:</b> {n_trades} &nbsp; <b>Wins:</b> {len(wins)} &nbsp;
   <b>Losses:</b> {len(losses)} &nbsp; <b>P&amp;L:</b> ${total_pnl:.2f}</p>
{svg}
<h2>Last 20 Closed Trades</h2>
<table><tr><th>Code</th><th>Exit Reason</th><th>R-Multiple</th></tr>
{rows_html}</table>
<h2>Open Positions</h2>
<table><tr><th>Code</th><th>Phase</th><th>Qty</th></tr>
{open_html}</table>
</body></html>"""


def _build_r_histogram(trades: list) -> str:
    """Generate inline SVG bar histogram of R-multiples (D-15)."""
    if not trades:
        return "<p>No trades today.</p>"

    rs = [t.get("r_multiple") or 0.0 for t in trades]
    # Bucket into bins: [-3..-2, -2..-1, -1..0, 0..1, 1..2, 2..3, 3+]
    bins = [-3, -2, -1, 0, 1, 2, 3, 4]
    labels = ["<-3", "-2", "-1", "0", "1", "2", "3+"]
    counts = [0] * len(labels)
    for r in rs:
        for i in range(len(bins) - 1):
            if bins[i] <= r < bins[i + 1]:
                counts[i] += 1
                break
        else:
            counts[-1] += 1

    max_count = max(counts) or 1
    bar_h = 100
    bar_w = 30
    gap = 8
    total_w = len(labels) * (bar_w + gap)

    bars = ""
    for i, (label, count) in enumerate(zip(labels, counts)):
        h = int(bar_h * count / max_count)
        x = i * (bar_w + gap)
        color = "#3fb950" if i >= 3 else "#f85149"   # green = positive R
        bars += (
            f'<rect x="{x}" y="{bar_h - h}" width="{bar_w}" height="{h}" fill="{color}"/>'
            f'<text x="{x + bar_w//2}" y="{bar_h + 12}" text-anchor="middle" '
            f'font-size="10" fill="#c9d1d9">{html.escape(label)}</text>'
            f'<text x="{x + bar_w//2}" y="{bar_h - h - 3}" text-anchor="middle" '
            f'font-size="9" fill="#c9d1d9">{count}</text>'
        )

    return (
        f'<h2>R-Multiple Histogram</h2>'
        f'<svg width="{total_w + 10}" height="{bar_h + 30}" '
        f'xmlns="http://www.w3.org/2000/svg">{bars}</svg>'
    )
```

**Writing reports (D-16):**
```python
import shutil
from pathlib import Path

def write_reports(html_content: str, session_date: date) -> None:
    """Write dated + latest.html reports (D-16)."""
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    dated = reports_dir / f"{session_date}.html"
    dated.write_text(html_content, encoding="utf-8")
    shutil.copy2(dated, reports_dir / "latest.html")
```

---

### Pattern 6: D-08 Startup Readiness Gate

```python
# In TradingBot.run() — D-08 hard gate before any entry-placement job runs
async def _readiness_gate(self) -> None:
    """Block until: paper guard, OpenD reachable, startup_reconcile complete (D-08)."""
    # 1. Paper guard — asserted in gateway.connect() already; re-check on reconnect
    #    (connect() raises PaperGuardError or ConnectionError if gate fails)
    self._gateway.connect()

    # 2. startup_reconcile — must complete before scheduler registers entry jobs
    await self._gateway.startup_reconcile(self._store, self._position_manager)

    # 3. reconstruct in-memory positions from DB-reconciled StateStore
    self._position_manager.reconstruct_from_store()

    # 4. Wire kill-switch flush NOW (R-04-01) — before loop starts
    self._kill_switch.register_flush(self._position_manager.flush_all)
    self._kill_switch.install()   # SIGINT handler

    # 5. Set entries enabled (watchdog may disable later)
    self._entries_enabled = True
    _logger.info("readiness_gate_passed")
```

---

### Anti-Patterns to Avoid

- **Using `BackgroundScheduler` instead of `AsyncIOScheduler`:** `BackgroundScheduler` runs jobs in a separate thread pool, violating D-02 (single event loop). All jobs must be coroutines on the shared loop.
- **Calling `asyncio.run()` inside a scheduled job:** Already inside an asyncio event loop. Calling `asyncio.run()` from within a running loop raises `RuntimeError`. Use `await` or `loop.run_in_executor` for blocking calls.
- **Importing `TELEGRAM_BOT_TOKEN` from `rules.json`:** Secrets must come from env vars only (D-13). `rules.json` holds only toggles like `alerts_enabled`.
- **Raising in `TelegramAlerter.send()`:** ALERT-04: delivery failures must never propagate. Wrap the entire `send()` coroutine in `try/except Exception` and log, never re-raise.
- **Calling `gateway.connect()` on reconnect without re-running `startup_reconcile`:** D-11 — reconnection always re-runs reconciliation before re-enabling entries.
- **Hardcoding `15:51` in the scheduler cron trigger:** The 15:51 trigger is a nominal time. The actual force-close logic inside `PositionManager.force_close_all()` already reads `get_market_close_et()` dynamically (Phase 4 built this). The scheduler triggers the job; the job itself is half-day safe.
- **Editing an existing StateStore migration:** If Phase 5 needs new `rules.json` tunables (service config keys), add them to `rules.json` and handle them in `StrategyConfig`. Only add a new migration (0005) if Phase 5 needs new DB columns (currently assessed as unlikely).
- **Skipping `asyncio.create_task` for alert dispatch:** Calling `await alerter.send(...)` directly inside a position manager callback blocks that callback until the Telegram HTTP request completes (up to 10s). Always dispatch via `asyncio.create_task(alerter.send(...))`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cron job scheduling with DST-aware ET times | Custom `asyncio.sleep` + datetime comparison loop | `APScheduler AsyncIOScheduler` + `CronTrigger(timezone=ET)` | DST transitions, misfire handling, coalesce, job management — all solved |
| Crash auto-restart with throttle | Shell `while` loop as only option | launchd LaunchAgent with `KeepAlive=true` + `ThrottleInterval` | OS-native on macOS; survives process group kill; no Python dep |
| Telegram HTTP client | Custom requests/aiohttp wrapper | stdlib `urllib.request.urlopen` + `run_in_executor` | Zero dep; one-way fire-and-forget; urllib is proven and stdlib |
| SVG chart library | matplotlib/pygal | Hand-generate 30-line `<rect>` SVG string | For a histogram of ≤7 buckets, a library adds 0 value; inline SVG renders from `file://` with no server |

**Key insight:** Phase 5 is an integration phase. Every non-trivial problem (scheduling, reconnect,
charting) is either solved by APScheduler or is small enough to hand-generate with stdlib. The goal
is maximum reliability with minimal new surface area.

---

## Common Pitfalls

### Pitfall 1: `AsyncIOScheduler` started before `asyncio.run()` has created the event loop

**What goes wrong:** If `scheduler.start()` is called before the asyncio event loop is running
(e.g. in module-level code or in `__init__`), the scheduler cannot attach to the event loop and
raises or fails silently.

**How to avoid:** Only call `scheduler.start()` from within a coroutine that is already running
under `asyncio.run()`. Pattern: start the scheduler in `TradingBot.run()` or `_readiness_gate()`
after `asyncio.run()` has been entered.

**Warning signs:** `SchedulerAlreadyRunningError` or jobs never fire.

---

### Pitfall 2: `run_in_executor` inside an APScheduler cron job that is itself a coroutine

**What goes wrong:** `loop.run_in_executor(None, blocking_fn)` requires a running event loop.
Inside an `async def` APScheduler job, `asyncio.get_running_loop()` works correctly. The pitfall
is using `asyncio.get_event_loop()` (deprecated in 3.10+) or forgetting `await` on the
executor call.

**How to avoid:** Always use `asyncio.get_running_loop()` (not `get_event_loop()`) inside
coroutines. The existing `MoomooGateway` pattern (verified in `gateway.py`) uses this correctly.

---

### Pitfall 3: `_entries_enabled` flag is not async-safe

**What goes wrong:** The watchdog sets `bot._entries_enabled = False` from within the watchdog
coroutine while a scheduled entry job may be concurrently reading the flag. On CPython, simple
boolean assignment is GIL-safe for single-assignment, but the read-check-act pattern is not atomic.

**How to avoid:** Keep all flag mutations and reads on the **same** event loop coroutine. Since
D-02 mandates one event loop and all code is coroutines (not threads), concurrent mutations are
serialized by the event loop. No `asyncio.Lock` is needed for a simple boolean flag if all
coroutines run on one loop. If blocking SDK calls run in `run_in_executor` threads, those threads
must not read/write `_entries_enabled` directly — only the coroutine side does.

---

### Pitfall 4: Telegram alert content includes raw credentials or sensitive values

**What goes wrong:** A catch-all `f"Exception: {exc}"` in error-path alert formatting may
interpolate strings that contain `TELEGRAM_BOT_TOKEN`, `FUTU_LOGIN_PWD`, or position P&L
computed from internal price data.

**How to avoid:** Alert message templates are defined as constants with explicit `{field}`
interpolation. Exceptions logged via structlog (masked) but never forwarded to Telegram.
Consistent with PITFALLS §Security Mistakes (never log FutuConfig / mask token in logs).

---

### Pitfall 5: `intraday_rescan` job fires outside market hours

**What goes wrong:** An `IntervalTrigger` set to 30 minutes will fire on weekends and holidays,
and before/after market hours, causing yfinance download attempts and pointless compute.

**How to avoid:** Each job coroutine wraps its body in an `if not is_trading_day(today) or
not _in_rescan_window():` guard. The calendar guard (`is_trading_day`) is already in
`bot/scanner/calendar.py`. The rescan window check compares `now_et().time()` against configured
start/end (e.g. 09:55–12:55 ET from rules.json). The scheduler fires the coroutine; the
coroutine no-ops gracefully outside the intended window.

---

### Pitfall 6: launchd PATH does not include the virtualenv Python

**What goes wrong:** launchd LaunchAgents do not source `.bashrc` or `.zshrc`. The default `PATH`
in a launchd process is `/usr/bin:/bin:/usr/sbin:/sbin`. A `python` command resolves to the system
Python (or fails), not the virtualenv Python. The bot fails to import `apscheduler`, `yfinance`,
etc.

**How to avoid:** In `ProgramArguments`, use the **full absolute path** to the virtualenv Python
binary (e.g. `/Users/acdc/path/to/venv/bin/python`). Also set `PATH` in `EnvironmentVariables`
to include `/opt/homebrew/bin:/usr/local/bin` for any subprocesses. Verified from launchd
community guidance.

---

### Pitfall 7: `coalesce=True` on force-close job with `misfire_grace_time=None`

**What goes wrong:** If `misfire_grace_time=None` (APScheduler default means "always run no
matter how late"), a misfired force-close job will fire even if the bot boots hours after
15:51 ET — triggering a spurious force-close next morning.

**How to avoid:** Set explicit `misfire_grace_time` for each job (see Pattern 1). For
force-close, a grace of 300–600 seconds (5–10 minutes) is right: boots at 15:53 ET should
still force-close; boots at 18:00 should not. `PositionManager.force_close_all()` has its own
internal time check (`if now_time < force_close_time: return`), providing a second safety layer.

---

## Code Examples

### Integration: KillSwitch.register_flush wiring (R-04-01 / D-07)

```python
# bot/main.py — the ONLY place all components are composed
# Source: bot/safety/kill_switch.py line 97 (register_flush signature verified)

from bot.safety.kill_switch import KillSwitch
from bot.position.manager import PositionManager
from bot.gateway.gateway import MoomooGateway, get_gateway_config
from bot.service.bot import TradingBot
from bot.service.alerter import TelegramAlerter
import asyncio
import os

def main() -> None:
    gateway = MoomooGateway(get_gateway_config())
    # ... build store, cfg, scanner, engine, position_manager, watchdog ...
    kill_switch = KillSwitch()
    alerter = TelegramAlerter(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        logger=get_logger(__name__),
    )
    bot = TradingBot(
        cfg=cfg,
        gateway=gateway,
        store=store,
        position_manager=position_manager,
        execution_engine=engine,
        kill_switch=kill_switch,
        alerter=alerter,
    )
    # KillSwitch.register_flush is wired inside bot._readiness_gate():
    #   self._kill_switch.register_flush(self._position_manager.flush_all)
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()
```

### Confirmed signatures from codebase (read-verified)

```python
# bot/safety/kill_switch.py — line 97
def register_flush(self, callback: Callable) -> None: ...
# callback: zero-argument callable

# bot/position/manager.py — line 246
def flush_all(self) -> None: ...
# synchronous; safe to call from KillSwitch (not async)

# bot/position/manager.py — line 272
async def force_close_all(self, today: _dt.date = None) -> None: ...
# async; scheduled as a cron job coroutine

# bot/scanner/scanner.py — line 356
def run_daily_scan(store, gateway, cfg, scan_date=None, scan_pass="premarket") -> List[str]: ...
# synchronous! Wrap in run_in_executor if called from async context, OR call from sync job.
# Note: scanner.py already has _run_coro() for internal async gateway.subscribe() bridge.

# bot/scanner/scanner.py — line 429
def run_intraday_rescan(store, gateway, cfg, active_codes, scan_date=None, scan_pass="intraday") -> List[str]: ...
# synchronous! Same pattern as run_daily_scan.

# bot/gateway/gateway.py — async methods confirmed:
async def connect() -> None  # NOTE: connect() is synchronous in codebase! See below.
# CORRECTION: connect() IS synchronous in the current codebase (line 272).
# Must call via run_in_executor from async context, or call sync from startup code.
```

**Important note on `run_daily_scan` / `run_intraday_rescan`:** Both scanner functions are
**synchronous** (`def`, not `async def`). Scheduled APScheduler cron jobs calling these must
either: (a) wrap them in `loop.run_in_executor(None, run_daily_scan, ...)` to avoid blocking
the event loop, or (b) define the job as a sync function (APScheduler runs sync functions in
a thread pool automatically). Option (b) is simpler and matches the existing pattern in
`scanner.py` which already has its own `_run_coro()` helper for the internal `gateway.subscribe`
bridge.

Similarly, `gateway.connect()` is **synchronous** and should be called from startup code
(before `asyncio.run`), or wrapped in `run_in_executor` on reconnect from within a coroutine.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `BackgroundScheduler` + `asyncio.run_coroutine_threadsafe` bridge | `AsyncIOScheduler` runs coroutines natively on the event loop | APScheduler 3.7+ | Eliminates thread-asyncio boundary friction; no need for `asyncio.run_coroutine_threadsafe` |
| `pytz` for APScheduler timezone | `ZoneInfo("America/New_York")` from stdlib | APScheduler 3.9+, Python 3.9 | `pytz` deprecated for new code; `zoneinfo` is DST-correct and stdlib |
| APScheduler v4 (async-first, new API) | APScheduler v3 (3.11.2 stable) | v4 still pre-release/dev as of 2026-06-24 | v4 has breaking API changes from v3 and is not production-ready; use v3 |
| `python-telegram-bot` library for one-way alerts | stdlib `urllib.request.urlopen` | n/a (project-specific decision D-14) | Eliminates one dependency for a use case that needs exactly one HTTP call |
| `supervisor` (supervisord) for macOS | launchd LaunchAgent | n/a (project-specific decision D-05) | OS-native, zero install, boot-persistent, standard for macOS services |

**Deprecated/outdated:**

- `asyncio.get_event_loop()` — deprecated in Python 3.10; removed implicit default in Python 3.12+.
  Use `asyncio.get_running_loop()` inside coroutines; `asyncio.run()` at the top level.
  Codebase already uses `asyncio.run()` in tests (verified in conftest decision log).
- APScheduler 4.x — do not use; pre-release, breaking changes from v3 API.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `TelegramAlerter` fire-and-forget dispatch via `asyncio.create_task` is sufficient for ALERT-04 isolation | Pattern 3 | If `create_task` errors are not suppressed, alert failures could log uncaught task exceptions; mitigated by wrapping `send()` in try/except |
| A2 | The `trades` table `r_multiple` column may be NULL for force-close exits (not all exits compute R); dashboard must guard `or 0.0` on the value | Pattern 5 | Dashboard histogram would error on NULL; guard is cheap to add |
| A3 | `run_daily_scan` / `run_intraday_rescan` are synchronous (verified by reading scanner.py); APScheduler treats sync job functions as thread-pool jobs | Code Examples | If this changes to async, the scheduler wiring changes (beneficial, not harmful) |
| A4 | `rules.json` new `service` top-level section (for poll intervals, backoff caps, alert throttles) will be added by the planner; `StrategyConfig` loader extended per CFG-01 pattern | Code Examples | If the planner doesn't add a service config block, numeric literals must not appear in code — fallback to constants read from separate env vars |
| A5 | The crash-loop alert threshold (D-06) is a simple in-process counter (restarts cannot be detected from `main.py` itself after a crash); launchd throttle is the OS-level guard | Pitfall section | If the bot needs to detect its own crash loop, it needs a filesystem timestamp on startup vs. `ThrottleInterval`; document as operator-visible via launchd log |

---

## Open Questions

1. **Should the EOD report generation query include trades from _prior_ sessions or only today?**
   - What we know: `trades` table has `closed_at` (ISO timestamp). Filtering by `session_date`
     requires a date comparison on `closed_at` in ET.
   - What's unclear: Whether a `session_date` column should be added (new migration 0005) or
     whether filtering by `closed_at DATE` in ET is sufficient.
   - Recommendation: Filter by `DATE(closed_at)` compared to `session_date.isoformat()` in
     SQLite (`WHERE DATE(closed_at) = ?`). Avoids a new migration. The `closed_at` timestamp is
     stored as ISO string (ET-aware per codebase convention).

2. **Alert fan-out wiring: callback injection vs event bus?**
   - What we know: Phase 1–4 classes (`PositionManager`, `ExecutionEngine`) don't currently emit
     any alerts. Adding alert callbacks at construction time is the simplest approach.
   - Recommendation: Add optional `on_entry_alert: Callable` and `on_exit_alert: Callable`
     parameters to `PositionManager.__init__`. These are `None` by default (backward compatible).
     `TradingBot` injects `asyncio.create_task(alerter.send(...))` lambda at construction time.
     No event bus needed for a single-process single-alerter setup.

3. **`main.py` vs `bot/__main__.py` — entry point location?**
   - What we know: `python -m bot` invokes `bot/__main__.py` if it exists.
   - Recommendation: Put the logic in `bot/main.py` and have `bot/__main__.py` call
     `from bot.main import main; main()`. This makes `main.py` importable/testable without
     triggering `asyncio.run`.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.x | All bot code | ✓ | 3.14.5 | — |
| `apscheduler` | TradingBot scheduler | ✗ (not installed) | — | Must install: `pip install apscheduler==3.11.2` |
| `moomoo-api` | MoomooGateway | ✓ (in requirements.txt) | ≥10.4.6408 | — |
| `pandas` | ReportBuilder aggregation | ✓ (in requirements.txt) | ≥2.0,<4.0 | — |
| `structlog` | Logging | ✓ (in requirements.txt) | 26.1.0 | — |
| `pytest-asyncio` | Service tests | ✓ (in requirements-dev.txt) | ≥0.24 | — |
| launchd | Process supervision | ✓ (macOS built-in) | macOS launchd | Shell `while`-loop script |
| Telegram Bot API | Alert delivery | ✓ (HTTPS, stdlib urllib) | stdlib | Log-only if `TELEGRAM_BOT_TOKEN` not set |
| OpenD | Broker connectivity | Operator-managed | ≥10.4.6408 | Bot does not start without OpenD; startup gate enforces this |

**Missing dependencies with no fallback:**
- `apscheduler==3.11.2` — must be added to `requirements.txt` and installed before bot can start.

**Missing dependencies with fallback:**
- Telegram Bot API (no `TELEGRAM_BOT_TOKEN`) — `TelegramAlerter` gracefully no-ops (logs instead of sending). Verified in TelegramAlerter design (D-12 notes: "fire-and-forget; log if Telegram unconfigured").

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest ≥8.0 + pytest-asyncio ≥0.24 |
| Config file | `pyproject.toml` or `pytest.ini` (check existing) |
| Quick run command | `pytest tests/service/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SVC-01 | Scheduler fires premarket scan job | unit | `pytest tests/service/test_bot.py::test_premarket_scan_job_called -x` | ❌ Wave 0 |
| SVC-01 | Scheduler fires intraday rescan 7× in window | unit | `pytest tests/service/test_bot.py::test_intraday_rescan_job_called -x` | ❌ Wave 0 |
| SVC-01 | Scheduler fires force-close job | unit | `pytest tests/service/test_bot.py::test_force_close_job_called -x` | ❌ Wave 0 |
| SVC-01 | D-08 readiness gate blocks entries before reconcile | unit | `pytest tests/service/test_bot.py::test_readiness_gate_blocks_entries -x` | ❌ Wave 0 |
| SVC-02 | Watchdog detects OpenD disconnect → entries disabled | unit | `pytest tests/service/test_watchdog.py::test_disconnect_disables_entries -x` | ❌ Wave 0 |
| SVC-02 | Watchdog reconnect → startup_reconcile called → entries re-enabled | unit | `pytest tests/service/test_watchdog.py::test_reconnect_reenables_entries -x` | ❌ Wave 0 |
| ALERT-01 | Entry alert sent with ticker/size/entry/stop | unit | `pytest tests/service/test_alerter.py::test_entry_alert_content -x` | ❌ Wave 0 |
| ALERT-02 | Exit alerts for each exit_reason type | unit | `pytest tests/service/test_alerter.py::test_exit_alert_for_all_reasons -x` | ❌ Wave 0 |
| ALERT-03 | Daily summary includes trades/wins/PnL | unit | `pytest tests/service/test_alerter.py::test_daily_summary_content -x` | ❌ Wave 0 |
| ALERT-04 | urllib exception → send() does not raise | unit | `pytest tests/service/test_alerter.py::test_send_failure_does_not_raise -x` | ❌ Wave 0 |
| DASH-01 | HTML report renders all required sections | unit | `pytest tests/service/test_report.py::test_html_report_sections -x` | ❌ Wave 0 |
| DASH-01 | SVG histogram generated with correct bucket counts | unit | `pytest tests/service/test_report.py::test_r_histogram_buckets -x` | ❌ Wave 0 |
| R-04-01 | KillSwitch.register_flush wired before loop | unit | `pytest tests/service/test_bot.py::test_kill_switch_flush_registered -x` | ❌ Wave 0 |

**Integration / manual tests:**

| Req ID | Behavior | Test Type | How |
|--------|----------|-----------|-----|
| SVC-01 (crit. #1) | One full paper-trading session end-to-end | manual UAT | Run bot with OpenD paper account; observe jobs fire at correct ET times |
| SVC-02 (crit. #2) | OpenD-disconnect simulation | manual integration | `kill` the OpenD process; observe entries pause + Telegram alert within 60s |

**Testing approach for APScheduler jobs (without real time delays):**

The recommended pattern (verified from APScheduler community and test suite patterns) is to
test the **job function directly** without the scheduler:

```python
# tests/service/test_bot.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_premarket_scan_job_called():
    """Job coroutine calls run_daily_scan when is_trading_day is True."""
    mock_scanner = MagicMock()
    mock_scanner.run_daily_scan = MagicMock(return_value=["US.AAPL"])
    bot = TradingBot(..., scanner=mock_scanner, ...)
    # Call the job directly — skip the scheduler entirely
    await bot._job_premarket_scan()
    mock_scanner.run_daily_scan.assert_called_once()
```

For the scheduler integration test (crit. #1), run the bot for a real session. For the
scheduler wiring test, assert the job IDs are registered:

```python
def test_scheduler_has_required_jobs():
    bot._register_jobs()
    job_ids = {j.id for j in bot._scheduler.get_jobs()}
    assert "premarket_scan" in job_ids
    assert "force_close" in job_ids
    assert "intraday_rescan" in job_ids
```

### Sampling Rate

- **Per task commit:** `pytest tests/service/ -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/service/__init__.py` — new test package
- [ ] `tests/service/test_bot.py` — TradingBot unit tests (Wave 0 stubs)
- [ ] `tests/service/test_watchdog.py` — OpenDWatchdog unit tests
- [ ] `tests/service/test_alerter.py` — TelegramAlerter unit tests
- [ ] `tests/service/test_report.py` — ReportBuilder unit tests
- [ ] `bot/service/__init__.py` — new service package
- [ ] Framework install: `pip install apscheduler==3.11.2` — add to `requirements.txt`

---

## Security Domain

`security_enforcement: true` is confirmed in `.planning/config.json`. ASVS Level 1 applies.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Not applicable (no user login; bot is a local service) |
| V3 Session Management | no | Not applicable (no user sessions) |
| V4 Access Control | partially | Paper-guard (SAFE-01) already enforces SIMULATE-only; no external access |
| V5 Input Validation | yes | Telegram alert text uses `html.escape()` to prevent injection in HTML output; `rules.json` values validated by jsonschema (Phase 1 CFG-01) |
| V6 Cryptography | no | No cryptographic operations; secrets via env vars only |
| V7 Error Handling & Logging | yes | structlog JSON (Phase 1 SVC-03); ALERT-04 failures logged not propagated; Telegram token masked in logs |
| V9 Communications | yes | Telegram transport uses HTTPS (`urllib.request.urlopen` defaults to HTTPS) |

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Telegram token leaked in logs | Information Disclosure | Never log `TELEGRAM_BOT_TOKEN`; mask to last 4 chars if logged at all |
| Credential injection via plist `EnvironmentVariables` | Information Disclosure | plist file must have `chmod 600`; secrets not committed to git |
| Runaway crash loop consuming CPU | Denial of Service | launchd `ThrottleInterval` limits restart rate; D-06 Telegram alert after N rapid crashes |
| Force-close fires after market due to `misfire_grace_time=None` | Tampering | Per-job `misfire_grace_time` limits (D-03); `force_close_all()` has internal time check |
| Alert injection via trade ticker/code string | Spoofing | `html.escape()` on all ticker strings in HTML report; Telegram HTML mode only permits `<b>`, `<i>`, `<code>` tags |
| StateStore read during report generation races with position writes | Tampering | SQLite WAL mode (already enabled by Phase 1 StateStore.open()); read-only query for reporting; no schema mutations in Phase 5 |

---

## Sources

### Primary (HIGH confidence)

- `bot/gateway/gateway.py` — verified: `connect()` is synchronous, `get_global_state()` does not exist yet, `subscribe()` / `run_in_executor` pattern confirmed [VERIFIED: codebase grep]
- `bot/safety/kill_switch.py` — verified: `register_flush(callback: Callable)` at line 97; `flush_all` is synchronous [VERIFIED: codebase read]
- `bot/position/manager.py` — verified: `flush_all()` signature line 246 (sync); `force_close_all(today)` line 272 (async) [VERIFIED: codebase read]
- `bot/scanner/scanner.py` — verified: `run_daily_scan()` line 356 and `run_intraday_rescan()` line 429 are both **synchronous** [VERIFIED: codebase read]
- `skills/moomooapi/scripts/quote/get_global_state.py` — verified: `ctx.get_global_state()` returns `(ret, data)` where `data` is a **dict** (not DataFrame), with `qot_logined`, `trd_logined`, `server_ver`, `market_us` keys [VERIFIED: codebase read]
- `https://pypi.org/pypi/apscheduler/json` — APScheduler 3.11.2, MIT, Python ≥3.8, uploaded 2025-12-22, 36.3M downloads/month [VERIFIED: pypi.org]
- `https://api.github.com/repos/agronholm/apscheduler` — 7,548 stars, 768 forks, pushed 2026-06-22, MIT license, actively maintained [VERIFIED: GitHub API]
- `https://apscheduler.readthedocs.io/en/3.x/` — AsyncIOScheduler runs `async def` coroutines natively on the event loop; `misfire_grace_time` and `coalesce` semantics; `ZoneInfo` replaces `pytz` in 3.x [CITED: apscheduler.readthedocs.io]
- `https://core.telegram.org/bots/api#sendmessage` — `POST https://api.telegram.org/bot{TOKEN}/sendMessage`, params: `chat_id`, `text`, `parse_mode`, response: `{"ok": true, "result": {...}}` [CITED: Telegram official docs]
- `https://launchd.info/` — launchd plist structure: `KeepAlive`, `RunAtLoad`, `ThrottleInterval`, `EnvironmentVariables`, `StandardOutPath`/`StandardErrorPath`; `KeepAlive=true` vs `StartCalendarInterval` choice [CITED: launchd.info]
- `.planning/phases/05-service-orchestration-and-reliability/05-CONTEXT.md` — 16 locked decisions D-01..D-16 [VERIFIED: project docs]
- `bot/state/migrations.py` — `trades` table schema: `exit_reason TEXT`, `r_multiple REAL`, `closed_at TEXT` confirmed [VERIFIED: codebase read]
- `.env.example` — confirmed: bot does not auto-load `.env`; `FUTU_*` env var convention; Phase 5 adds `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` [VERIFIED: codebase read]

### Secondary (MEDIUM confidence)

- `https://pypistats.org/api/packages/apscheduler/recent` — 36.3M downloads/month, 8.9M last week (retrieved 2026-06-24) [CITED: pypistats.org]
- APScheduler GitHub issue #293 discussion pattern: test job functions directly by calling them, skip the scheduler for unit tests [CITED: github.com/agronholm/apscheduler]
- launchd PATH issue: launchd processes do not source shell profiles; must provide full Python path in `ProgramArguments` [CITED: launchd.info + community guidance]

### Tertiary (LOW confidence — use with caution)

- APScheduler `AsyncIOScheduler` `event_loop` constructor parameter — present in docs but version-specific; do not pass it explicitly; let the scheduler auto-detect the running loop [LOW: partial docs only]

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — APScheduler version and provenance verified via PyPI; existing project deps verified via requirements.txt; Telegram API verified via official docs
- Architecture patterns: HIGH — all Phase 1–4 method signatures verified by reading source files; `get_global_state()` field shape verified from skills/ reference implementation
- APScheduler integration: HIGH — async coroutine job execution confirmed from official docs + community sources
- launchd plist: HIGH — verified from official launchd.info docs
- Pitfalls: HIGH — derived from code-verified method signatures and established project PITFALLS.md
- Testing strategy: MEDIUM — direct job function call pattern is standard; APScheduler time-control is not well-supported, so end-to-end session test is manual

**Research date:** 2026-06-24
**Valid until:** 2026-09-24 (90 days — APScheduler 3.x is stable; Telegram API endpoint is stable)
