# Stack Research

**Domain:** Automated intraday Python trading bot (Moomoo/Futu OpenAPI, paper-trading)
**Researched:** 2026-06-23
**Confidence:** HIGH (all versions verified via PyPI; library docs verified via Context7 and official sources)

---

## Existing Stack (Do Not Change)

The repo already has a mature broker-access layer. This research covers only what to ADD around it.

| Component | What Exists | Version |
|-----------|-------------|---------|
| Broker SDK | `moomoo-api` | 10.7.6708 (latest on PyPI) |
| Data frames | `pandas` | 3.0.3 |
| Runtime | Python 3.6+ (3.9+ recommended for zoneinfo) | — |
| Trade audit | `~/.futu_trade_audit.jsonl` | — |

---

## Recommended Stack — New Components to Add

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| APScheduler | 3.11.2 | Scheduled jobs: premarket scan trigger (08:00 ET), intraday 5m polling, 15:51 EOD force-close | v3.x is stable production release; v4.x is still pre-release per official docs (avoid). `BackgroundScheduler` with `CronTrigger` maps directly to the "fire at HH:MM ET" requirement; handles missed fires gracefully and does not block the main thread. The `schedule` library is simpler but single-threaded and loses state on restart. |
| python-telegram-bot | 22.8 | One-way push alerts: entries, exits, daily summary | The standard Python Telegram library; v22.x is current stable. For one-way fire-and-forget alerts (no user commands needed), use `Bot` directly without the full `Application` polling loop — confirmed by official docs. Async-native with `asyncio`. |
| pandas-market-calendars | 5.4.0 | NYSE trading-day validation, holiday detection, market open/close times in ET | `nyse.valid_days()` and `nyse.schedule()` give accurate holiday/half-day schedules including observed holidays (e.g. Christmas observed). Wraps exchange_calendars under the hood. Essential for "don't run on market holidays" logic. HIGH confidence via Context7 docs. |
| zoneinfo (stdlib) | Python 3.9+ built-in | All US Eastern datetime handling | Native stdlib since 3.9; replaces pytz for new code. APScheduler v3.11 accepts zoneinfo timezones. No external dependency. Use `ZoneInfo("America/New_York")` throughout — handles EST/EDT transitions automatically. |
| SQLite via stdlib `sqlite3` | built-in | Durable state: open positions, trailing stops, partial fill state, daily trade log | The bot must survive restarts (trailing stop levels, breakeven state, position IDs). SQLite requires zero infrastructure on a local operator machine; stdlib `sqlite3` is sufficient for single-process access. No network dependency, atomic writes, human-readable with any SQLite browser. For a single-user bot this beats Redis, Postgres, or TinyDB. |
| structlog | 26.1.0 | Structured, machine-parseable log output for the long-running service | stdlib `logging` produces unstructured text; structlog emits JSON-ready key-value lines that are trivially grepped by `jq`. Its `contextvars` integration works correctly across the asyncio boundary used by python-telegram-bot. Production standard for financial services bots per 2025/2026 community guidance. |
| backtrader2 | 1.9.76.123 | Intraday 5m bar backtesting against OHLCV CSV/DataFrame | backtrader (original) is unmaintained (creator calls it "complete"). `backtrader2` is the community-maintained fork with active bug fixes. Event-driven architecture exactly mirrors how the live bot processes bars one-at-a-time, making strategy code re-use between backtest and live straightforward. Accepts pandas DataFrames directly (`bt.feeds.PandasData`) and handles 5m bar timeframe natively (`timeframe=bt.TimeFrame.Minutes, compression=5`). |
| supervisor | 4.3.0 | Process supervision: restart the bot on crash, manage stdout/stderr logs | Operator runs on macOS; macOS `launchd` XML is painful for developer-run services. `supervisord` is a single `pip install`, uses `.ini` config, gives `supervisorctl start/stop/status` commands, auto-restarts on crash with configurable backoff. Simpler than a systemd service for a single-developer local machine. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | 2.5.0 | RVOL calculation, SMA200, swing-low detection on bar arrays | All indicator math on 5m bar arrays; already a transitive dep of pandas but worth pinning |
| python-dotenv | 1.2.2 | Load `.env` file for `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` without polluting shell env | Config management for the Telegram credentials alongside existing `FUTU_*` env vars |
| httpx | 0.28.1 | Optional: HTTP health-check endpoint or webhook verification | Only needed if adding a `/health` check; python-telegram-bot already bundles aiohttp |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pytest 8.x | Unit-test signal logic, risk math, position sizing, stop calculations | Do NOT use backtrader for unit tests — instantiate strategy functions directly |
| pytest-asyncio | Test async Telegram notification code | Required because python-telegram-bot v22 is fully async |
| ruff | Linting and formatting | Replaces flake8+black; zero config by default |

---

## Installation

```bash
# Core additions (all new — broker SDK already installed)
pip install \
  "APScheduler==3.11.2" \
  "python-telegram-bot==22.8" \
  "pandas-market-calendars==5.4.0" \
  "structlog==26.1.0" \
  "backtrader2==1.9.76.123" \
  "supervisor==4.3.0" \
  "python-dotenv==1.2.2" \
  "numpy==2.5.0"

# Dev dependencies
pip install \
  "pytest>=8.0" \
  "pytest-asyncio>=0.24" \
  "ruff>=0.5"
```

---

## Alternatives Considered

| Recommended | Alternative | Why Not |
|-------------|-------------|---------|
| APScheduler 3.x | `schedule` library | Single-threaded, no persistence, blocks if a job runs long; inadequate for a trading service that must fire jobs while handling active trades |
| APScheduler 3.x | APScheduler 4.x | Official docs explicitly flag v4 as "pre-release, not intended for production use" as of 2025 |
| APScheduler 3.x | `asyncio` naked task loop | Requires hand-rolling cron logic, holiday skip, misfire handling — APScheduler solves all three |
| python-telegram-bot | `telebot` (pyTelegramBotAPI) | python-telegram-bot v22 is async-native and the de-facto standard; telebot has sync/async split and smaller ecosystem |
| python-telegram-bot | Telegram HTTP API directly | python-telegram-bot wraps retry, rate-limit, and parse_mode — no reason to hand-roll |
| pandas-market-calendars | exchange_calendars | exchange_calendars is the upstream; pandas-market-calendars adds the `valid_days()` and pandas date range integration that is more convenient for this use case |
| backtrader2 | vectorbt | vectorbt is faster for parameter sweeps but uses a vectorized array model that is architecturally different from bar-by-bar event-driven live execution. Strategy code cannot be shared between backtest and live bot. For a single-strategy validation the realism advantage of backtrader2 outweighs vectorbt's speed. |
| backtrader2 | vectorbt PRO | Commercial, requires subscription; out of scope for paper-trading validation |
| backtrader2 | NautilusTrader | Production-grade but complex C++/Rust core; significant overkill for a single 5m strategy |
| SQLite stdlib | TinyDB | TinyDB is document-oriented JSON; less efficient for position queries, no ACID transactions, not worth the dependency |
| SQLite stdlib | Redis | Requires a separate running service; unnecessary on a local operator machine |
| structlog | loguru | loguru is simpler but structlog's contextvars integration is cleaner for the asyncio/thread boundary in this bot |
| supervisor | launchd (macOS plist) | launchd requires XML plist configuration and `launchctl` commands; supervisord's `supervisorctl` is simpler for development use |
| supervisor | systemd user service | macOS does not have systemd |
| zoneinfo | pytz | pytz is a legacy compatibility shim; deprecated for new code in Python 3.9+; APScheduler 4.x has already dropped it |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| APScheduler 4.x | Officially pre-release and not production-ready as of 2025; breaking API changes from 3.x | APScheduler 3.11.2 |
| `schedule` library | Single-threaded, no misfire handling, cannot skip market holidays without manual wrapping | APScheduler 3.x with BackgroundScheduler |
| backtrader (original PyPI package) | Creator-declared "complete", no bug fixes; the PyPI `backtrader` 1.9.78.123 has known bugs | backtrader2 community fork |
| vectorbt OSS for strategy validation | Vectorized array model is architecturally incompatible with the event-driven live bot; code cannot be shared; lookahead bias risk when implementing partial exit logic | backtrader2 |
| pytz | Deprecated for Python 3.9+; use stdlib zoneinfo | `from zoneinfo import ZoneInfo` |
| Thread-based asyncio bridging (`asyncio.run_coroutine_threadsafe`) | The moomoo SDK's push callbacks fire on a SDK-internal thread; naively bridging to asyncio can deadlock. Use a queue (`queue.Queue`) to hand off bar events to the main thread | `queue.Queue` between push handler and main loop |

---

## Stack Patterns by Variant

**Scheduling (APScheduler BackgroundScheduler):**
```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
scheduler = BackgroundScheduler(timezone=ET)
scheduler.add_job(run_premarket_scan, CronTrigger(hour=8, minute=0, timezone=ET))
scheduler.add_job(force_close_all, CronTrigger(hour=15, minute=51, timezone=ET))
scheduler.start()
```

**Moomoo 5m bar callback — thread-safe hand-off pattern:**
```python
import queue
from moomoo import CurKlineHandlerBase, SubType

bar_queue = queue.Queue()

class KlineHandler(CurKlineHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret == 0:
            bar_queue.put(data)  # hand off to main loop; never block here

quote_ctx.set_handler(KlineHandler())
quote_ctx.subscribe(symbols, [SubType.K_5M])
```

**Telegram one-way alert (no polling):**
```python
import asyncio
from telegram import Bot

async def send_alert(token: str, chat_id: str, text: str) -> None:
    async with Bot(token) as bot:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

# From sync code:
asyncio.run(send_alert(token, chat_id, msg))
```

**Market-day guard:**
```python
import pandas_market_calendars as mcal
from datetime import date

nyse = mcal.get_calendar("NYSE")

def is_trading_day(dt: date) -> bool:
    valid = nyse.valid_days(start_date=dt, end_date=dt)
    return len(valid) > 0
```

---

## Integration with Existing moomoo Client

The existing `skills/moomooapi/scripts/common.py` provides `create_quote_context()` and `create_trade_context()` factory functions. The new bot layer MUST:

1. **Reuse these factories** — do not create new context creation logic; the factories handle version checks, env-var config, and SIMULATE default.
2. **Hold contexts open** — unlike the CLI scripts which create and destroy a context per invocation, the bot must keep `OpenQuoteContext` alive for the duration of the session to maintain 5m bar subscriptions. Use the `with` context manager across the full session, not per-call.
3. **Inherit env-var config** — all `FUTU_*` env vars already defined; add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env` loaded via python-dotenv.
4. **SIMULATE default is safe** — `FUTU_TRD_ENV=SIMULATE` is the existing default; the bot must never set `TrdEnv.REAL` in any code path.

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| APScheduler 3.11.2 | Python 3.6–3.13 | v3.x works with both pytz and zoneinfo (pass `ZoneInfo` object directly) |
| python-telegram-bot 22.8 | Python 3.9+ | v22 requires Python 3.9+; confirms the project should target 3.9+ minimum |
| pandas-market-calendars 5.4.0 | pandas 2.x and 3.x | Tested against pandas 3.0.x; no known conflicts |
| backtrader2 1.9.76.123 | Python 3.8+, pandas ≤ 2.x | backtrader2 may have pandas 3.x compatibility issues — test with pandas 2.2.x in the backtest environment or pin pandas for backtest use only |
| moomoo-api 10.7.6708 | Python 3.6+ | Existing constraint; no changes |
| structlog 26.1.0 | Python 3.9+ | Fully compatible with asyncio contextvars |
| supervisor 4.3.0 | Python 3.4+, macOS/Linux | Does not run natively on Windows; macOS is fine |

**Critical compatibility note:** backtrader2 and pandas 3.x may conflict. The safest approach is to run the backtester in a separate virtualenv (or conda env) pinned to pandas 2.2.x, while the live bot runs the main env with pandas 3.0.3. This avoids a dependency footgun.

---

## Sources

- `/agronholm/apscheduler` (Context7) — APScheduler v3 vs v4 pre-release status, migration docs, BackgroundScheduler patterns
- `/python-telegram-bot/python-telegram-bot` (Context7) — standalone Bot usage, send_message without polling
- `/rsheftel/pandas_market_calendars` (Context7) — valid_days(), schedule(), NYSE calendar
- `https://pypi.org/pypi/APScheduler/json` — confirmed latest stable: 3.11.2
- `https://pypi.org/pypi/python-telegram-bot/json` — confirmed latest: 22.8
- `https://pypi.org/pypi/pandas-market-calendars/json` — confirmed latest: 5.4.0
- `https://pypi.org/pypi/vectorbt/json` — confirmed latest: 1.0.0
- `https://pypi.org/pypi/backtrader2/json` — confirmed latest: 1.9.76.123
- `https://pypi.org/pypi/supervisor/json` — confirmed latest: 4.3.0
- `https://pypi.org/pypi/structlog/json` — confirmed latest: 26.1.0
- `https://pypi.org/pypi/moomoo-api/json` — confirmed latest: 10.7.6708
- `https://openapi.moomoo.com/moomoo-api-doc/en/quote/update-kl.html` — CurKlineHandlerBase callback pattern, SubType.K_5M subscription
- `https://community.backtrader.com/topic/3702/is-backtrader-dead` — backtrader maintenance status; confirmed creator-declared "complete"
- `https://snyk.io/advisor/python/backtrader` — MEDIUM confidence; backtrader classified as inactive on Snyk
- WebSearch: "APScheduler vs schedule library Python long-running service" — MEDIUM confidence, confirmed by APScheduler docs
- WebSearch: "Python backtrader vs vectorbt 5-minute intraday backtesting" — MEDIUM confidence, multiple sources agree
- WebSearch: "Python structlog vs logging trading bot 2025" — MEDIUM confidence, structlog recommended for production services
- `https://www.structlog.org/en/stable/logging-best-practices.html` — structlog official best practices

---
*Stack research for: AI S&P Trading Bot — automated intraday Python service on Moomoo/Futu OpenAPI*
*Researched: 2026-06-23*
