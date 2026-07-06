# Phase 05: Service Orchestration and Reliability — Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 10 new files
**Analogs found:** 10 / 10

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bot/service/bot.py` (TradingBot) | orchestrator | event-driven | `bot/gateway/gateway.py` (MoomooGateway lifecycle) + `bot/safety/kill_switch.py` (shutdown loop) | role-match |
| `bot/__main__.py` / `bot/main.py` | entry-point | request-response | `bot/safety/kill_switch.py` (trigger + register_flush) | partial-match |
| `bot/service/watchdog.py` (OpenDWatchdog) | service | event-driven | `bot/gateway/gateway.py` (`reconciliation_loop` + `run_in_executor` pattern) | role-match |
| `bot/gateway/gateway.py` — add `get_global_state()` | service method | request-response | `bot/gateway/gateway.py` `subscribe()` / `get_equity()` (deferred import + run_in_executor) | exact |
| `bot/service/alerter.py` (TelegramAlerter) | service | request-response | `bot/gateway/gateway.py` `place_order()` (run_in_executor blocking-in-executor pattern) | role-match |
| `bot/service/report.py` (ReportBuilder) | utility | batch | `bot/safety/audit_log.py` (file write + `bot/state/store.py` data access) | partial-match |
| `deploy/com.bot.trading.plist` | config | — | none — new artifact type | no-analog |
| `bot/config/schema.py` — add `service` block | config | — | `bot/config/schema.py` (existing `execution` block pattern) | exact |
| `bot/config/loader.py` — extend StrategyConfig | config | — | `bot/config/loader.py` (existing `execution` section mapping pattern) | exact |
| `tests/service/test_bot.py`, `test_watchdog.py`, `test_alerter.py`, `test_report.py` | test | — | `tests/gateway/test_gateway.py`, `tests/safety/test_kill_switch.py`, `tests/position/test_manager.py` | role-match |

---

## Pattern Assignments

### `bot/service/bot.py` — TradingBot (orchestrator, event-driven)

**Analog:** `bot/gateway/gateway.py` (MoomooGateway) for the class + async lifecycle shape; `bot/safety/kill_switch.py` for the shutdown loop pattern.

**Imports pattern** (`bot/gateway/gateway.py` lines 17–38):
```python
import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from bot.safety.logger import get_logger
from bot.safety.paper_guard import assert_paper_account
```
New file adds:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo
```

**Module logger pattern** (`bot/gateway/gateway.py` line 44):
```python
_logger = get_logger(__name__)
```

**Class constructor with injected dependencies** — mirror `MoomooGateway.__init__` (lines 238–241) using Optional dataclass config + explicit injected component refs:
```python
class TradingBot:
    def __init__(self, cfg, gateway, store, scanner, position_manager,
                 execution_engine, watchdog, alerter, kill_switch):
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/New_York"))
        self._cfg = cfg
        self._gateway = gateway
        # ... store all injected components
        self._entries_enabled: bool = False
```

**Async lifecycle loop** — mirror `reconciliation_loop` (lines 954–978 in `gateway.py`): `while True`, `await asyncio.sleep`, `try/except asyncio.CancelledError` re-raise, log on other errors, never silently die:
```python
# bot/gateway/gateway.py lines 968–978 — the exact loop shape to copy:
async def reconciliation_loop(self, interval_s: float = 75.0) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await self.reconcile_once()
        except asyncio.CancelledError:
            raise  # propagate cancellation for clean shutdown
        except Exception:
            _logger.error("reconcile_failed", exc_info=True)
```

**Kill-switch shutdown pattern** (`bot/safety/kill_switch.py` lines 130–138):
```python
# Orchestration loop polls .triggered:
while not ks.triggered:
    if ks.check_file():
        ks.trigger("sentinel_file")
    await asyncio.sleep(1)
```
`TradingBot.run()` wraps this with `try/finally` to call `scheduler.shutdown(wait=False)`.

**register_flush wiring before loop** (`bot/safety/kill_switch.py` line 97 API):
```python
# Exact API — must be called before loop starts (R-04-01):
self._kill_switch.register_flush(self._position_manager.flush_all)
self._kill_switch.install()   # installs SIGINT handler
```

---

### `bot/__main__.py` / `bot/main.py` — Entry point (entry-point, request-response)

**Analog:** `bot/safety/kill_switch.py` + `bot/config/loader.py` (load config → construct → run pattern).

**Config load + error exit pattern** (`bot/config/loader.py` lines 118–196):
```python
# Load → raise ConfigError → caller (main.py) handles sys.exit:
try:
    cfg = load_strategy_config("rules.json")
except ConfigError as exc:
    print(f"[ERROR] {exc}", file=sys.stderr)
    sys.exit(1)
```

**Module docstring convention** (`bot/gateway/gateway.py` lines 1–15) — all bot files use 3–6-line module docstrings with `Exports:` line.

**asyncio.run entry pattern** — single `asyncio.run(bot.run())` in `if __name__ == "__main__":` block; no `get_event_loop().run_forever()`.

---

### `bot/service/watchdog.py` — OpenDWatchdog (service, event-driven)

**Analog:** `bot/gateway/gateway.py` `reconciliation_loop` (lines 954–978) for the polling-loop shape; `get_equity()` (lines 331–387) for the try/except-returns-fallback error swallow pattern.

**Polling loop shape** (`bot/gateway/gateway.py` lines 968–978 — copy exactly):
```python
async def reconciliation_loop(self, interval_s: float = 75.0) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await self.reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("reconcile_failed", exc_info=True)
```
Watchdog replaces `reconcile_once()` call with `await self._check_once()`.

**Error-swallowing fallback return** (`bot/gateway/gateway.py` `get_equity` lines 347–387):
```python
try:
    loop = asyncio.get_running_loop()
    ret, data = await loop.run_in_executor(None, lambda: ...)
    if ret != RET_OK or data is None or len(data) == 0:
        _logger.warning("equity_query_failed", ret=ret, fallback=_EQUITY_FALLBACK)
        return _EQUITY_FALLBACK
    ...
except Exception:
    _logger.warning("equity_query_exception", exc_info=True, fallback=_EQUITY_FALLBACK)
    return _EQUITY_FALLBACK
```
`get_global_state()` returns `{"connected": False}` instead of a numeric fallback, but the same try/except-warning-return-fallback structure applies.

**fire-and-forget alert dispatch** — mirror `place_order` audit_log call pattern; use `asyncio.create_task(alerter.send(...))` not `await alerter.send(...)`.

---

### `bot/gateway/gateway.py` — add `get_global_state()` method (service method, request-response)

**Analog:** `bot/gateway/gateway.py` `subscribe()` (lines 423–459) — the canonical deferred-import + run_in_executor + inner `_blocking` function pattern.

**Deferred import + run_in_executor pattern** (`bot/gateway/gateway.py` lines 439–458):
```python
async def subscribe(self, codes: list, subtypes: list = None) -> None:
    # Deferred import — avoids top-level import failure when moomoo-api
    # is not installed in the test environment.
    from moomoo import SubType, Session

    if subtypes is None:
        subtypes = [SubType.K_5M]

    loop = asyncio.get_running_loop()

    def _subscribe_blocking():
        ret, msg = self._quote_ctx.subscribe(
            codes, subtypes, is_first_push=True, subscribe_push=True,
            extended_time=False, session=Session.NONE,
        )
        _check_ret(ret, msg, "subscribe")

    await loop.run_in_executor(None, _subscribe_blocking)
    _logger.info("subscribed_k5m", codes=codes, count=len(codes))
```
`get_global_state()` uses the same shape: `loop = asyncio.get_running_loop()`, inner `def _blocking():`, `ret, data = await loop.run_in_executor(None, _blocking)`, returns fallback dict on any exception (never raises).

**Return-fallback-never-raises error convention** (`get_equity` lines 381–387):
```python
except Exception:
    _logger.warning("equity_query_exception", exc_info=True, fallback=_EQUITY_FALLBACK)
    return _EQUITY_FALLBACK
```
New method returns `{"connected": False, "qot_logined": False, "trd_logined": False}` on any exception — same intent, dict instead of float.

**Placement in file:** add as a new method in the `# Async Wrappers (run_in_executor)` section (after line 493), before the `# Order Methods` section.

---

### `bot/service/alerter.py` — TelegramAlerter (service, request-response)

**Analog:** `bot/gateway/gateway.py` `place_order()` (lines 499–548) — the most complete example of the deferred-import + run_in_executor + inner `_blocking` + audit-log-never-blocks pattern. Also `bot/safety/audit_log.py` `append_audit()` (lines 31–50) for the try/except-pass-never-propagates error convention.

**run_in_executor blocking-in-executor pattern** (`gateway.py` lines 522–547):
```python
loop = asyncio.get_running_loop()

def _place_blocking():
    ret, data = self._trade_ctx.place_order(...)
    _check_ret(ret, data, "place_order")
    row = data.iloc[0] if hasattr(data, "iloc") else data[0]
    order_id = str(row.get("order_id", "") or row.get("orderID", ""))
    from bot.safety.audit_log import append_audit
    append_audit({...})
    return order_id

order_id = await loop.run_in_executor(None, _place_blocking)
_logger.info("order_placed", code=code, qty=qty, price=price, order_id=order_id)
return order_id
```
TelegramAlerter.send() replaces `_place_blocking` with `_post_blocking` (urllib POST), and wraps the whole `await loop.run_in_executor(...)` in try/except that logs + swallows (never re-raises — ALERT-04).

**Silent-failure-never-propagates convention** (`bot/safety/audit_log.py` lines 43–50):
```python
def append_audit(entry: dict) -> None:
    try:
        ...
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass   # logging must not block shutdown
```
`TelegramAlerter.send()` uses the same `except Exception: pass` (or log instead of pass) — never re-raises.

**Enabled guard** — mirror `assert_paper_account` pattern: if token/chat_id are empty, return early with a debug log. Never attempt the POST when unconfigured.

---

### `bot/service/report.py` — ReportBuilder (utility, batch)

**Analog:** `bot/safety/audit_log.py` (file write with path resolution + try/except); `bot/state/store.py` `get_open_positions()` for the data-access pattern; `bot/safety/et_helpers.py` `now_et()` for session-date computation.

**Path resolution + file write pattern** (`bot/safety/audit_log.py` lines 20–50):
```python
AUDIT_LOG_PATH = os.path.join(os.path.expanduser("~"), ".futu_trade_audit.jsonl")

def append_audit(entry: dict) -> None:
    try:
        ...
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
```
ReportBuilder uses `pathlib.Path("reports") / f"{session_date}.html"` and `Path.write_text(html, encoding="utf-8")` — same try/except around I/O.

**StateStore data access** (`bot/gateway/gateway.py` `startup_reconcile` lines 776–777):
```python
# Use store.get_open_positions() which returns a list of plain dicts:
state_rows = store.get_open_positions()
state_codes = {r["code"]: r for r in state_rows}
```
ReportBuilder queries `store.conn` for `trades` (last 20 closed, sorted by `closed_at DESC`) and `positions` (ACTIVE phase) using the same `store.conn.row_factory = sqlite3.Row` → `fetchall()` pattern established across `startup_reconcile`.

**et_helpers session date** (`bot/safety/et_helpers.py` `now_et()` line 28):
```python
def now_et() -> datetime:
    ...
```
Report builder calls `now_et().date()` for the `session_date` used in the filename and report title.

---

### `bot/config/schema.py` — add `service` block (config, extension)

**Analog:** `bot/config/schema.py` `execution` block (lines 1–60 excerpt) — the existing pattern for adding a new top-level optional config group.

**Existing `execution` block pattern in SCHEMA** (`bot/config/schema.py` — read lines 27–30):
```python
SCHEMA = {
    "type": "object",
    "required": [..., "execution"],
    "additionalProperties": True,
    "properties": {
        ...
        "execution": {
            "type": "object",
            "required": ["entry_limit_buffer_usd", ...],
            "properties": {
                "entry_limit_buffer_usd": {"type": "number"},
                ...
            }
        },
    }
}
```
New `service` block follows the same structure. `"service"` is added to `"required"` and its `"properties"` define all Phase-5 tunables as `{"type": "number"}` or `{"type": "boolean"}`. `"additionalProperties": True` already allows the new key without a schema migration.

---

### `bot/config/loader.py` — extend StrategyConfig (config, extension)

**Analog:** `bot/config/loader.py` `execution` section (lines 101–196) — the exact pattern for adding a new config group to the StrategyConfig dataclass and the `load_strategy_config` mapping.

**StrategyConfig dataclass extension pattern** (`bot/config/loader.py` lines 101–111):
```python
# ---- execution (Phase 4 tunables — CFG-01, D-05/D-07/D-08) ----
entry_limit_buffer_usd: float           # execution.entry_limit_buffer_usd
entry_ttl_seconds: float                # execution.entry_ttl_seconds
entry_max_retries: int                  # execution.entry_max_retries
...
```
New fields follow the same pattern:
```python
# ---- service (Phase 5 tunables — CFG-01, D-01/D-03/D-06/D-10) ----
premarket_scan_et: str                  # service.premarket_scan_et  ("HH:MM")
market_open_et: str                     # service.market_open_et
intraday_rescan_interval_min: int       # service.intraday_rescan_interval_min
watchdog_poll_interval_s: float         # service.watchdog_poll_interval_s
watchdog_reconnect_initial_s: float     # service.watchdog_reconnect_initial_s
watchdog_reconnect_cap_s: float         # service.watchdog_reconnect_cap_s
alerts_enabled: bool                    # service.alerts_enabled
misfire_grace_scan_s: int               # service.misfire_grace_scan_s
misfire_grace_rescan_s: int             # service.misfire_grace_rescan_s
force_close_misfire_grace_s: int        # service.force_close_misfire_grace_s
```

**load_strategy_config mapping pattern** (`bot/config/loader.py` lines 160–196):
```python
ex_cfg = data.get("execution", {})

return StrategyConfig(
    ...
    entry_limit_buffer_usd=float(ex_cfg["entry_limit_buffer_usd"]),
    ...
)
```
New `service` block:
```python
svc_cfg = data.get("service", {})

return StrategyConfig(
    ...
    premarket_scan_et=str(svc_cfg["premarket_scan_et"]),
    watchdog_poll_interval_s=float(svc_cfg["watchdog_poll_interval_s"]),
    alerts_enabled=bool(svc_cfg.get("alerts_enabled", True)),
    ...
)
```

---

### `tests/service/test_bot.py`, `test_watchdog.py`, `test_alerter.py`, `test_report.py`

**Analog:** `tests/gateway/test_gateway.py` (lines 1–80) for mock-injection pattern + async test structure; `tests/safety/test_kill_switch.py` for trigger/callback testing; `tests/position/test_manager.py` for `AsyncMock` usage; `tests/conftest.py` for fixture conventions.

**Test file header pattern** (`tests/gateway/test_gateway.py` lines 1–33):
```python
#!/usr/bin/env python3
"""
tests/gateway/test_gateway.py — Tests for bot.gateway.gateway (MoomooGateway).

Verifies (no network — all SDK contexts are mocked): ...
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import pandas as pd

from bot.gateway.gateway import MoomooGateway, ...
```

**Mock-injection helper** (`tests/gateway/test_gateway.py` lines 41–65):
```python
def _make_gateway_with_mocks(cfg=None, ...) -> MoomooGateway:
    """Return a MoomooGateway with pre-injected mock contexts (no connect() needed)."""
    gw = MoomooGateway(cfg)
    gw._quote_ctx = MagicMock()
    gw._trade_ctx = _make_mock_ctx(...)
    return gw
```
Service tests: `_make_bot_with_mocks()` injects mock gateway, mock store, mock position_manager, mock alerter — never calls `asyncio.run()` or real APScheduler in unit tests.

**Fixture pattern** (`tests/conftest.py` lines 26–35):
```python
@pytest.fixture
def tmp_state_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bot_state_test.db")
    monkeypatch.setenv("BOT_STATE_DB", db_path)
    return db_path
```
New `tests/service/conftest.py` adds `monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")` and `monkeypatch.setenv("TELEGRAM_CHAT_ID", "")` fixtures to ensure alerter is disabled in tests.

**Async test pattern** (`tests/position/test_manager.py` lines 1–31):
```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Tests using async functions:
@pytest.mark.asyncio
async def test_something():
    mock_gw = MagicMock()
    mock_gw.get_global_state = AsyncMock(return_value={"connected": True, ...})
    ...
```

**Audit log inspection pattern** (`tests/safety/test_kill_switch.py` lines 75–80 implied):
```python
def test_trigger_runs_registered_callback(ks):
    mock_cb = MagicMock()
    ks.register_flush(mock_cb)
    ks.trigger("test_flush")
    mock_cb.assert_called_once()
```
alerter tests assert `urllib.request.urlopen` was called (or not called when disabled) using `patch("urllib.request.urlopen")`.

---

### `deploy/com.bot.trading.plist` (config, no-analog)

No existing analog — first plist in the project. RESEARCH.md Pattern 4 is the canonical reference. Key constraint from `CLAUDE.md`: use full absolute path to venv Python in `ProgramArguments`; launchd does not source shell profiles.

---

## Shared Patterns

### run_in_executor for blocking SDK/network calls
**Source:** `bot/gateway/gateway.py` lines 304–310, 417–421, 439–458
**Apply to:** `get_global_state()` (gateway addition), `TelegramAlerter._post_blocking`
```python
loop = asyncio.get_running_loop()

def _blocking():
    # ... blocking call here ...
    pass

result = await loop.run_in_executor(None, _blocking)
```
Key: always `asyncio.get_running_loop()` (not deprecated `get_event_loop()`). Inner function is a plain `def`, not `async def`.

### Fire-and-forget task dispatch
**Source:** `bot/gateway/gateway.py` `place_order` + `cancel_order` combined with `audit_log.py` silent-failure pattern
**Apply to:** All `TelegramAlerter.send()` call sites in TradingBot/watchdog
```python
# Correct dispatch — never `await alerter.send()` from a callback:
asyncio.create_task(alerter.send(f"message text"))
```

### Error-swallowing with warning log (never re-raise)
**Source:** `bot/gateway/gateway.py` `get_equity()` lines 381–387; `bot/safety/audit_log.py` lines 43–50
**Apply to:** `TelegramAlerter.send()`, `ReportBuilder` file writes, `get_global_state()`
```python
except Exception:
    _logger.warning("action_failed", exc_info=True)
    return <safe_fallback>   # or just: pass
```

### Structured log with get_logger(__name__)
**Source:** `bot/gateway/gateway.py` line 44; `bot/safety/logger.py` lines 161–170
**Apply to:** All new `bot/service/*.py` files
```python
from bot.safety.logger import get_logger
_logger = get_logger(__name__)
```
Service-wide `configure_logging()` is called once in `main.py` before constructing any component.

### Deferred SDK import inside method
**Source:** `bot/gateway/gateway.py` `subscribe()` lines 439–441, `place_order()` lines 518–520
**Apply to:** `get_global_state()` new method — if it requires any moomoo sub-import beyond what is at module top
```python
# Only if needed — subscribe() already defers SubType:
from moomoo import SubType, Session
```
`get_global_state()` only needs `self._quote_ctx.get_global_state()` which uses the already-imported `OpenQuoteContext` — no additional deferred import needed unless testing requires it.

### Append-only JSONL audit for lifecycle events
**Source:** `bot/safety/audit_log.py` `append_audit()` lines 31–50
**Apply to:** `main.py` shutdown handler, watchdog reconnect/disconnect events
```python
from bot.safety.audit_log import append_audit
append_audit({
    "event": "bot_shutdown",
    "reason": "SIGINT",
    # ... other fields
})
```

### Config group extension (rules.json + StrategyConfig)
**Source:** `bot/config/loader.py` lines 101–111 (dataclass fields) + lines 160–196 (mapping)
**Apply to:** `service` block in `StrategyConfig` and `load_strategy_config`
All new tunables go in `rules.json["service"]`, mapped to flat `cfg.service_*` fields (or a nested `ServiceConfig` dataclass — planner's discretion). `"additionalProperties": True` in SCHEMA means no schema-breaking change.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `deploy/com.bot.trading.plist` | config/deploy | — | First plist; no macOS launchd precedent in the codebase. RESEARCH.md Pattern 4 is the sole reference. |

---

## Metadata

**Analog search scope:** `bot/` (gateway, safety, config, position, state, scanner, signal, execution), `tests/` (all subdirs), `bot/config/schema.py`
**Files read:** 12 source files
**Pattern extraction date:** 2026-06-24
