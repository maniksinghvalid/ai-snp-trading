# Phase 3: Intraday Signal and Risk Engine — Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 12 new/modified files
**Analogs found:** 12 / 12

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bot/signal/bar_aggregator.py` | service (push handler) | event-driven | `skills/moomooapi/scripts/subscribe/push_kline.py` (handler pattern) + `bot/gateway/gateway.py` lines 301–337 (deferred import + run_in_executor) | exact role-match |
| `bot/signal/events.py` | model (dataclasses) | N/A | `bot/gateway/gateway.py` lines 51–101 (`GatewayConfig` dataclass) | role-match |
| `bot/signal/signal_engine.py` | service (orchestrator) | event-driven | `bot/scanner/scanner.py` (reads config + StateStore, evaluates filters, emits results) | exact role-match |
| `bot/risk/events.py` | model (dataclasses) | N/A | `bot/gateway/gateway.py` lines 51–101 (`GatewayConfig` dataclass) | role-match |
| `bot/risk/risk_engine.py` | service (orchestrator) | event-driven | `bot/strategy/trend_join_long.py` (config-parameterized pure math) + `bot/scanner/scanner.py` (StateStore writes) | role-match |
| `bot/signal/__init__.py` | config | N/A | `bot/gateway/__init__.py` (empty package init) | exact |
| `bot/risk/__init__.py` | config | N/A | `bot/gateway/__init__.py` (empty package init) | exact |
| `bot/state/migrations.py` (extend) | migration | batch | `bot/state/migrations.py` lines 103–132 (`_migration_0002` callable pattern) | exact |
| `bot/gateway/gateway.py` (extend) | service | request-response | `bot/gateway/gateway.py` lines 290–299 (`get_positions()` run_in_executor wrapper) | exact |
| `tests/signal/test_bar_aggregator.py` | test | event-driven | `tests/state/test_migrations.py` (class-based pytest, in-memory fixtures) | role-match |
| `tests/signal/test_signal_engine.py` | test | event-driven | `tests/scanner/test_scanner.py` (config-driven, monkeypatch, no broker) | role-match |
| `tests/risk/test_risk_engine.py` | test | event-driven | `tests/scanner/test_scanner.py` (config-driven, monkeypatch, no broker) | role-match |

---

## Pattern Assignments

### `bot/signal/bar_aggregator.py` (service, event-driven)

**Analog 1:** `skills/moomooapi/scripts/subscribe/push_kline.py`
**Analog 2:** `bot/gateway/gateway.py` (deferred import + asyncio bridge)

**CurKlineHandlerBase subclass pattern** (`push_kline.py` lines 54–87):
```python
from moomoo import CurKlineHandlerBase, RET_ERROR

class KlineHandler(CurKlineHandlerBase):
    def __init__(self, output_json=False):
        super().__init__()
        self.output_json = output_json

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)   # ALWAYS call super first
        if ret_code != RET_OK:
            ...
            return RET_ERROR, data

        for i in range(len(data)):
            row = data.iloc[i] if hasattr(data, "iloc") else data[i]
            row.get("code", "")
            row.get("time_key", "")
            row.get("open", 0); row.get("high", 0); row.get("low", 0)
            row.get("close", 0); row.get("volume", 0)

        return RET_OK, data
```

**Deferred SDK import pattern** (`bot/gateway/gateway.py` lines 316–318, 352–353):
```python
async def subscribe(self, codes: list, subtypes: list = None) -> None:
    # Deferred import — avoids top-level import failure when moomoo-api
    # is not installed in the test environment (matches gateway SDK import pattern).
    from moomoo import SubType, Session
```
Apply the same deferred import to `CurKlineHandlerBase` inside `BarAggregator.__init__` or at module level with a `try/except ImportError` guard matching `bot/gateway/gateway.py` lines 23–34.

**asyncio.run_coroutine_threadsafe bridge** — use instead of `call_soon_threadsafe` when firing an async coroutine from the SDK push thread (RESEARCH.md Pattern 2). The loop object must be passed to `BarAggregator.__init__()`, never fetched inside `on_recv_rsp`.

**Module docstring pattern** (`push_kline.py` lines 1–11 / `gateway.py` lines 1–15):
```python
#!/usr/bin/env python3
"""
bot.signal.bar_aggregator — CurKlineHandlerBase subclass for bar-close detection.

Bridges moomoo SDK push thread → asyncio event loop for the signal engine.
Bar-close detected via timestamp-advance + session-level dedup (SIG-02).
"""
```

**Logger pattern** (`bot/gateway/gateway.py` lines 44–44, `bot/scanner/scanner.py` line 31):
```python
from bot.safety.logger import get_logger
_logger = get_logger(__name__)
```

**Error handling pattern** (throughout `push_kline.py` lines 60–67):
```python
ret_code, data = super().on_recv_rsp(rsp_pb)
if ret_code != RET_OK:
    return RET_ERROR, data
if data is None or len(data) == 0:
    return ret_code, data
```

---

### `bot/signal/events.py` and `bot/risk/events.py` (model, dataclasses)

**Analog:** `bot/gateway/gateway.py` lines 51–101 (`GatewayConfig` dataclass)

**Dataclass import and style pattern** (`bot/gateway/gateway.py` lines 19–20):
```python
from dataclasses import dataclass, field
from typing import Optional
```

**Dataclass declaration pattern** (`bot/gateway/gateway.py` lines 51–66):
```python
@dataclass
class GatewayConfig:
    """One-line purpose — mirrors FutuConfig pattern in common.py (D-02).

    Reads the same FUTU_* env vars plus FUTU_ACC_ID and PAPER_TRADING.
    """
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    trd_env: str = "SIMULATE"
    acc_id: int = 0
    paper_trading: bool = False
```

For Phase 3 dataclasses, also import `datetime` and `uuid` at module top. `Optional` from `typing` for nullable fields. No `field(default_factory=...)` needed here — all fields are value types or references.

**Module docstring pattern** for events files:
```python
#!/usr/bin/env python3
"""
bot.signal.events — BarEvent and SignalEvent dataclasses (Phase 3, SIG-02/03).

BarEvent is produced by BarAggregator on every closed 5m bar.
SignalEvent is produced by SignalEngine when all gates pass.
"""
```

---

### `bot/signal/signal_engine.py` (service, event-driven)

**Analog:** `bot/scanner/scanner.py`

**Imports pattern** (`bot/scanner/scanner.py` lines 1–31):
```python
#!/usr/bin/env python3
"""
bot.scanner.scanner — Premarket scan and watchlist persistence.
...
"""
import asyncio
import sqlite3
from datetime import date
from typing import List, Optional, Set

from bot.config.loader import StrategyConfig
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.state.store import StateStore
from bot.strategy.trend_join_long import TrendJoinLong

_logger = get_logger(__name__)
```

**Config-driven threshold pattern** — never hardcode thresholds; read from `cfg`:
```python
# bot/scanner/scanner.py lines 77–79 — exact model to follow
if n_prior < cfg.rvol_lookback_days:
    _logger.warning("symbol_skipped_insufficient_history", ...)
    return None
```

**StateStore read pattern** (`bot/scanner/scanner.py` lines 199–232 `_persist_watchlist`):
```python
conn.execute(
    "SELECT code, prior_day_high, prior_close, sma200, rvol_baseline "
    "FROM daily_scan WHERE scan_date = ? ORDER BY rank",
    (session_date,)
).fetchall()
```
Use `?` placeholders — no f-string SQL (T-02-06).

**Structlog event pattern** (`bot/scanner/scanner.py` lines 233–238):
```python
_logger.info(
    "watchlist_persisted",
    count=len(candidates),
    scan_date=scan_date_str,
    scan_pass=scan_pass,
)
```
All log events use keyword-only structured args. Event name is a `snake_case_string`. No f-strings in log calls.

**Early return on gate failure** (`bot/scanner/scanner.py` lines 68–83):
```python
if frame is None:
    return None
if len(frame) < 2:
    _logger.warning("symbol_skipped_insufficient_rows", ...)
    return None
```
Same pattern for signal gates: check each gate, log the specific reason, return early.

**TrendJoinLong call pattern** (`bot/scanner/scanner.py` lines 156–159):
```python
strategy = TrendJoinLong(cfg)
if not strategy.passes_daily_filters(symbol, daily_2row, sma200_val):
    return None
```
For Phase 3: `strategy = TrendJoinLong(cfg)` instantiated with config; call `strategy.passes_intraday_filters(code, bars_df, premarket_high, hod, rvol_ratio)`.

---

### `bot/risk/risk_engine.py` (service, event-driven)

**Analog:** `bot/strategy/trend_join_long.py` (config-parameterized math) + `bot/scanner/scanner.py` (StateStore writes)

**Config math pattern** (`bot/strategy/trend_join_long.py` lines 42–50):
```python
def __init__(self, cfg: StrategyConfig) -> None:
    self._cfg = cfg
```
All thresholds read as `self._cfg.max_risk_per_trade_pct`, `self._cfg.max_position_size_pct`, `self._cfg.max_concurrent_positions`, `self._cfg.max_trades_per_day`. Zero literals.

**StateStore write pattern** (`bot/scanner/scanner.py` lines 202–231):
```python
conn.execute(
    """
    INSERT INTO pending_intents
        (intent_id, code, status, entry_price, stop_price, quantity, emitted_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    (intent.intent_id, intent.code, "PENDING", ...),
)
conn.commit()
```

**Structlog audit pattern** (`bot/safety/logger.py` — `get_logger(__name__)`) + (`bot/scanner/scanner.py` lines 233–238):
```python
_logger.info(
    "order_intent_emitted",
    intent_id=intent.intent_id,
    code=intent.code,
    entry_price=intent.entry_price,
    stop_price=intent.stop_price,
    quantity=intent.quantity,
    equity_used=intent.equity_used,
    risk_dollars=intent.risk_dollars,
    notional=intent.notional,
)
```

**math.floor for quantity sizing** — import `math` at top; use `math.floor()`, not `int()`, for truncation to whole shares (D-07).

---

### `bot/state/migrations.py` — extend with migration 0003

**Analog:** `bot/state/migrations.py` lines 103–132 (`_migration_0002` callable)

**Callable migration pattern** (lines 112–122):
```python
def _migration_0002(conn: sqlite3.Connection) -> None:
    """Add Phase 2 rich-context columns to daily_scan, idempotently (D-08, WR-03).

    Each ALTER is guarded by a column-existence check so re-running after a
    partial failure (some columns committed, user_version not yet bumped) is a
    no-op rather than a fatal "duplicate column name" error.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_scan)")}
    for col, decl in _DAILY_SCAN_0002_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE daily_scan ADD COLUMN {col} {decl}")
```

**Migration 0003 must be a callable** (not a SQL string) because `CREATE TABLE IF NOT EXISTS` in an `executescript` call would issue an implicit COMMIT before the `PRAGMA user_version` bump — same WR-03 rationale as 0002. Use `conn.executescript()` inside the callable to run multiple CREATE TABLE statements atomically within the open transaction.

**MIGRATIONS list extension pattern** (lines 129–135):
```python
MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,   # adds rich context columns to daily_scan (Phase 2, D-08)
    _migration_0003,   # adds daily_trade_count + pending_intents (Phase 3, D-08/D-12)
]

CURRENT_VERSION = 3
```
Only append to `MIGRATIONS`. Never change index positions of existing entries. Bump `CURRENT_VERSION` by exactly 1.

**Table existence check for callable migration** — for `CREATE TABLE IF NOT EXISTS` (unlike ALTER TABLE ADD COLUMN), SQLite's `IF NOT EXISTS` clause is already idempotent. No need for a PRAGMA table_info guard. The callable simply calls `conn.executescript(...)` with the two CREATE TABLE statements.

---

### `bot/gateway/gateway.py` — new `get_equity()` method

**Analog:** `bot/gateway/gateway.py` lines 290–299 (`get_positions()`)

**Exact pattern to mirror** (lines 290–299):
```python
async def get_positions(self) -> tuple:
    """Async wrapper: fetch open positions from broker (non-blocking).

    Returns (ret, data) from trade_ctx.position_list_query().
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: self._trade_ctx.position_list_query(),
    )
```

**`get_equity()` must follow this signature:** `async def get_equity(self) -> float:` — not a tuple return; applies fallback logic internally and returns a float. Uses `lambda:` to pass `accinfo_query(trd_env=..., acc_id=..., refresh_cache=True)` to `run_in_executor`.

**`_check_ret` helper** (lines 172–182):
```python
def _check_ret(ret: int, data, action: str) -> None:
    if ret != RET_OK:
        raise GatewayError(f"{action} failed: ret={ret}, data={data}")
```
For `get_equity()`, do NOT call `_check_ret` — instead check `ret != RET_OK` and return the `_EQUITY_FALLBACK`, because an equity query failure must degrade gracefully (D-05), not raise.

**`_parse_trd_env` helper** (lines 146–154):
```python
def _parse_trd_env(env_str: str) -> "TrdEnv":
    if env_str and str(env_str).upper() == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE
```
Pass `_parse_trd_env(self.cfg.trd_env)` to `accinfo_query(trd_env=...)`.

**DataFrame row access pattern** (used in `push_kline.py` lines 72–73, `gateway.py` reconcile section):
```python
row = data.iloc[0] if hasattr(data, "iloc") else data[0]
total_assets = float(row.get("total_assets", 0) or 0)
```

---

### `tests/signal/test_bar_aggregator.py` and `tests/signal/test_signal_engine.py` (test, event-driven)

**Analog:** `tests/state/test_migrations.py` (class-based pytest) and `tests/scanner/test_scanner.py` (monkeypatch pattern)

**Test file header pattern** (`tests/state/test_migrations.py` lines 1–17):
```python
#!/usr/bin/env python3
"""
tests/signal/test_bar_aggregator.py — Tests for bot/signal/bar_aggregator.py

Verifies:
  (a) No signal fired mid-bar (time_key unchanged) — SIG-02
  (b) Signal fired exactly once when time_key advances — SIG-02
  (c) Session dedup prevents double-fire on reconnect re-push — SIG-02
  (d) HOD tracking updated correctly across bars — D-02
"""
import pytest
```

**In-memory fixture pattern** (`tests/state/test_migrations.py` lines 24–29):
```python
@pytest.fixture
def in_memory_conn():
    """Return a fresh in-memory SQLite connection, closed after the test."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()
```
For signal/risk tests: use similar fixtures for `StateStore` with in-memory SQLite (`monkeypatch.setenv("BOT_STATE_DB", ":memory:")`) or construct `StrategyConfig` directly from test values.

**Class-based test grouping pattern** (`tests/state/test_migrations.py` lines 36–109):
```python
class TestMigrationFreshDb:
    """Migration 0001 applied to an empty database."""

    def test_all_five_tables_created(self, in_memory_conn):
        ...
```
Group by behavior boundary: `TestBarAggregatorMidBar`, `TestBarAggregatorBarClose`, `TestBarAggregatorReconnectDedup`, `TestSignalEngineGates`, `TestSignalEngineEntryWindow`, etc.

**No broker dependency in unit tests** — BarAggregator tests call `on_recv_rsp` directly with a fake DataFrame. SignalEngine tests mock `gateway.get_positions()` to return fixed data. RiskEngine tests mock `gateway.get_equity()` to return fixed floats. Consistent with how `tests/scanner/test_scanner.py` passes `gateway=None`.

---

### `tests/risk/test_risk_engine.py` (test, event-driven)

**Analog:** `tests/state/test_migrations.py` (class-based pytest) + `tests/scanner/test_scanner.py`

Same header, fixture, and grouping patterns as signal tests. Key test groups:
- `TestRiskEngineSizingMath` — 1% risk qty, 10% notional cap, take-smaller rule
- `TestRiskEngineUnderBudget` — qty < 1 emits no intent and logs reason
- `TestRiskEngineEquityFallback` — failed/implausible equity returns $100k
- `TestRiskEngineIntentFields` — `OrderIntent` carries correct stop_price, quantity, intent_id
- `TestRiskEngineDailyCap` — daily cap gating via pending_count + filled_count

---

## Shared Patterns

### Module-level Logger Initialization
**Source:** `bot/gateway/gateway.py` line 44, `bot/scanner/scanner.py` line 31
**Apply to:** All new `bot/signal/*.py` and `bot/risk/*.py` modules
```python
from bot.safety.logger import get_logger
_logger = get_logger(__name__)
```

### Config-Driven Thresholds (CFG-01)
**Source:** `bot/scanner/scanner.py` lines 77–79, `bot/strategy/trend_join_long.py` lines 42–50
**Apply to:** `signal_engine.py`, `risk_engine.py` — zero numeric literals; all thresholds from `cfg.*`
```python
from bot.config.loader import StrategyConfig

class SignalEngine:
    def __init__(self, cfg: StrategyConfig, gateway, store: StateStore) -> None:
        self._cfg = cfg
        # Access via: self._cfg.max_concurrent_positions, self._cfg.max_trades_per_day, etc.
```

### DataFrame Row Access (Null-Safe)
**Source:** `skills/moomooapi/scripts/subscribe/push_kline.py` lines 72–73
**Apply to:** `bar_aggregator.py` (on_recv_rsp), `gateway.py` (get_equity)
```python
row = data.iloc[i] if hasattr(data, "iloc") else data[i]
value = safe_float(row.get("field_name", 0))   # or float(row.get(..., 0) or 0)
```

### StateStore SQL Binding (No f-string SQL)
**Source:** `bot/scanner/scanner.py` lines 202–230
**Apply to:** `risk_engine.py` (pending_intents insert), `signal_engine.py` (daily_trade_count read)
```python
conn.execute("SELECT ... WHERE session_date = ?", (session_date_str,))
conn.execute("INSERT INTO pending_intents (...) VALUES (?, ?, ...)", (...))
conn.commit()
```

### Structlog Structured Event Pattern
**Source:** `bot/scanner/scanner.py` lines 233–238, `bot/gateway/gateway.py` lines 337, 371
**Apply to:** All log calls in `bar_aggregator.py`, `signal_engine.py`, `risk_engine.py`
```python
_logger.info("event_name_snake_case", key1=value1, key2=value2)
_logger.warning("event_name", code=code, reason="specific_reason")
```

### ET Time Helper
**Source:** `bot/scanner/scanner.py` lines 387, 460 — `from bot.safety.et_helpers import now_et`
**Apply to:** `signal_engine.py` (entry-window gate), `risk_engine.py` (OrderIntent.emitted_at)
```python
from bot.safety.et_helpers import now_et
now = now_et()          # returns datetime in ET timezone
today = now_et().date() # ET date for session_date keys
```

### Deferred moomoo SDK Import
**Source:** `bot/gateway/gateway.py` lines 316–318 (inside `subscribe`)
**Apply to:** `bar_aggregator.py` — import `CurKlineHandlerBase` and `RET_OK` inside `__init__` or at top-level with `try/except ImportError` guard matching `gateway.py` lines 23–34
```python
try:
    from moomoo import CurKlineHandlerBase, RET_OK
except ImportError as exc:  # pragma: no cover
    raise ImportError("moomoo-api is not installed...") from exc
```

---

## No Analog Found

All files have analogs. No entry needed here.

---

## Metadata

**Analog search scope:** `bot/`, `skills/moomooapi/scripts/subscribe/`, `tests/`
**Files read:** 8 existing source files, 2 existing test files
**Pattern extraction date:** 2026-06-24
