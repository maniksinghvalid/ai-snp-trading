# Phase 2: Premarket Scanner - Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 10 new/modified files
**Analogs found:** 10 / 10

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bot/scanner/__init__.py` | config | — | `bot/strategy/__init__.py` | role-match (empty package init) |
| `bot/scanner/universe.py` | utility | file-I/O | `bot/config/loader.py` | role-match (file read + fallback error) |
| `bot/scanner/fetcher.py` | service | batch + transform | `bot/strategy/indicators.py` | role-match (pure compute, pandas) |
| `bot/scanner/calendar.py` | utility | request-response | `bot/safety/et_helpers.py` | role-match (thin wrapper, ET-correct) |
| `bot/scanner/scanner.py` | service | CRUD + orchestration | `bot/gateway/gateway.py` | role-match (orchestrator, structlog, error raises) |
| `bot/state/migrations.py` | migration | CRUD | `bot/state/migrations.py` (self) | exact — migration 0002 appended |
| `bot/gateway/gateway.py` | service | request-response | `bot/gateway/gateway.py` (self) | exact — `subscribe()` method added |
| `tests/scanner/__init__.py` | test | — | `tests/strategy/__init__.py` | exact |
| `tests/scanner/test_universe.py` | test | — | `tests/state/test_migrations.py` | role-match (mock + in-memory fixtures) |
| `tests/scanner/test_fetcher.py` | test | — | `tests/strategy/test_indicators.py` | role-match (synthetic DataFrames, pure-function tests) |
| `tests/scanner/test_scanner.py` | test | — | `tests/gateway/test_gateway.py` | role-match (mock injection, class-based test groups) |
| `tests/scanner/test_calendar.py` | test | — | `tests/safety/test_et_helpers.py` | role-match (deterministic assertions, known dates) |

---

## Pattern Assignments

### `bot/scanner/__init__.py` (package init)

**Analog:** Any existing `bot/*/.__init__.py` — all are empty files with no exports at the package level.

**Pattern:** Create an empty `__init__.py` (zero bytes). The subpackage is a sibling of `bot/gateway/`, `bot/state/`, `bot/strategy/`.

---

### `bot/scanner/universe.py` (utility, file-I/O)

**Analog:** `bot/config/loader.py`

**Imports pattern** (`bot/config/loader.py` lines 1-17):
```python
#!/usr/bin/env python3
"""
bot.config.loader — ...
"""
import json
from dataclasses import dataclass

import jsonschema

from bot.config.schema import SCHEMA
```

**For `universe.py`, mirror this import block:**
```python
#!/usr/bin/env python3
"""
bot.scanner.universe — S&P 500 constituent list fetcher with dated-cache fallback.

Exports: fetch_sp500_symbols
"""
import glob
import os
from datetime import date

import pandas as pd

from bot.safety.logger import get_logger

_logger = get_logger(__name__)
```

**Error handling pattern** (`bot/config/loader.py` lines 106-132):
```python
# Raise, don't exit — loader raises ConfigError; caller handles exit.
try:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
except FileNotFoundError:
    raise ConfigError(f"rules.json not found: {path}")

try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    raise ConfigError(f"rules.json is not valid JSON: {exc}") from exc
```

**Apply same raise-don't-exit pattern:** `universe.py` raises `RuntimeError` when scrape fails AND no cache exists. On scrape failure with cache present, log a warning via `_logger.warning(...)` and return cached symbols — never `sys.exit()`.

**Dated cache convention (D-03):** filename `data/sp500_YYYY-MM-DD.csv`; newest file in `glob.glob("data/sp500_*.csv")` sorted reverse is the fallback. Follow the `os.makedirs(parent, exist_ok=True)` pattern from `bot/state/store.py` line 193 when writing the cache.

---

### `bot/scanner/fetcher.py` (service, batch + transform)

**Analog:** `bot/strategy/indicators.py`

**Module header pattern** (`bot/strategy/indicators.py` lines 1-17):
```python
#!/usr/bin/env python3
"""
bot.strategy.indicators — Pure indicator functions for the Trend Join Long strategy.

All functions are pure (no I/O, no network, no broker calls) and operate on
pandas Series/DataFrames or plain Python lists.

Exports: sma, rvol, swing_low_2_2
"""
import math
from typing import List, Optional, Union

import pandas as pd

from bot._utils import safe_float
```

**For `fetcher.py`:**
```python
#!/usr/bin/env python3
"""
bot.scanner.fetcher — yfinance daily-bar batch downloader for the premarket scanner.

Downloads 1-year daily OHLCV bars for ~500 S&P 500 symbols in a bounded-
concurrency batch call. Pure data-fetch layer: no filter logic, no persistence.

Exports: download_daily_bars, ScanDegradationError
"""
from typing import Optional, Set, Tuple

import pandas as pd
import yfinance as yf
import yfinance.shared as shared

from bot.safety.logger import get_logger

_logger = get_logger(__name__)
```

**No-I/O, raises-not-exits pattern:** `fetcher.py` raises `ScanDegradationError` (custom exception subclassing `Exception`) when failure_rate >= 0.10. Never calls `sys.exit()`. Mirrors `GatewayError` and `ConfigError` raise-not-exit patterns.

**Column normalisation:** After `yf.download()`, normalise column names to lowercase (`ticker_df.columns = ticker_df.columns.str.lower()`) before passing to indicators — per RESEARCH.md Open Question #1.

---

### `bot/scanner/calendar.py` (utility, request-response)

**Analog:** `bot/safety/et_helpers.py`

**Module header + thin-wrapper pattern** (`bot/safety/et_helpers.py` lines 1-22):
```python
#!/usr/bin/env python3
"""
bot.safety.et_helpers — DST-correct US Eastern time helpers via zoneinfo.

Exports: ET, now_et, to_et
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

def now_et() -> datetime:
    """Return the current time as a timezone-aware datetime in US Eastern."""
    return datetime.now(tz=ET)
```

**For `calendar.py`:**
```python
#!/usr/bin/env python3
"""
bot.scanner.calendar — NYSE trading-day and market-close helpers.

Thin wrapper around pandas_market_calendars for holiday / half-day awareness.
All returned times are in US Eastern.

Exports: is_trading_day, get_market_close_et, get_prior_n_trading_days
"""
from datetime import date, datetime
from typing import List

import pandas as pd
import pandas_market_calendars as mcal

from bot.safety.et_helpers import ET

_nyse = mcal.get_calendar("NYSE")
```

**Pattern:** Module-level singleton `_nyse = mcal.get_calendar("NYSE")` (created once, analogous to `ET = ZoneInfo("America/New_York")`). Functions are pure, no I/O. Docstrings describe parameter type and return value inline (no `:param:` format — matches project style).

---

### `bot/scanner/scanner.py` (service, CRUD + orchestration)

**Analog:** `bot/gateway/gateway.py`

**Imports and logger pattern** (`bot/gateway/gateway.py` lines 1-44):
```python
#!/usr/bin/env python3
"""
bot.gateway.gateway — MoomooGateway broker access layer.
...
Exports: MoomooGateway, GatewayConfig, GatewayError, get_gateway_config
"""
import asyncio
import os
import socket
from dataclasses import dataclass, field
from typing import Optional

from bot.safety.logger import get_logger
from bot.safety.paper_guard import assert_paper_account

_logger = get_logger(__name__)
```

**For `scanner.py`:**
```python
#!/usr/bin/env python3
"""
bot.scanner.scanner — Premarket scan and intraday re-scan entrypoints.

Orchestrates: universe fetch → bar download → D1/D2/D3 filter → top-20 persist → K_5M subscribe.

Exports: run_daily_scan, run_intraday_rescan, ScanDegradationError
"""
import asyncio
from datetime import date
from typing import List, Optional, Set

from bot.config.loader import StrategyConfig
from bot.gateway.gateway import MoomooGateway
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import is_trading_day
from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
from bot.scanner.universe import fetch_sp500_symbols
from bot.state.store import StateStore
from bot.strategy.indicators import sma, rvol
from bot.strategy.trend_join_long import TrendJoinLong

_logger = get_logger(__name__)
```

**structlog event naming pattern** (from `bot/gateway/gateway.py` line 357 and throughout):
```python
_logger.error("reconcile_failed", exc_info=True)
_logger.info("subscribed_k5m", codes=codes, count=len(codes))
```
Use `snake_case` event name as first positional arg; structured key=value pairs as keyword args. Mirrors RESEARCH.md examples: `"scan_aborted_data_degradation"`, `"scan_partial_data"`.

**Raise-not-exit pattern:** `run_daily_scan()` raises `ScanDegradationError` on >= 10% data failure (D-06). Caller (Phase 5 scheduler or CLI `__main__`) handles the exception. Never calls `sys.exit()` inside the scanner.

**asyncio.run() bridge for sync callers** (per RESEARCH.md §Module Decomposition):
```python
# run_daily_scan is sync; gateway.subscribe() is async.
# Bridge: asyncio.run(gateway.subscribe(codes)) within the sync function body.
# Do NOT make run_daily_scan async unless the planner explicitly decides to.
```

---

### `bot/state/migrations.py` (migration — append migration 0002)

**Analog:** `bot/state/migrations.py` itself (the existing `_MIGRATION_0001` block)

**Existing structure to follow** (lines 15-98):
```python
# ============================================================
# Migration Version
# ============================================================

CURRENT_VERSION = 1   # → change to 2

# ============================================================
# Migration 0001 — Full v1 Schema
# ============================================================

_MIGRATION_0001 = """..."""

# ============================================================
# Migration List (index N corresponds to migration step N+1)
# ============================================================

MIGRATIONS = [
    _MIGRATION_0001,
]
```

**New block to append after `_MIGRATION_0001`:**
```python
# ============================================================
# Migration 0002 — Extend daily_scan with Phase 2 rich context
# ============================================================
#
# Adds per-candidate context columns that Phase 3 reads from StateStore
# rather than recomputing (D-08). All new columns are nullable (no NOT NULL)
# because SQLite ALTER TABLE ADD COLUMN does not allow non-null defaults
# without a DEFAULT expression. gap_pct is NOT re-added (already in 0001).
#
# scan_pass examples: "premarket" | "intraday_1" | "intraday_2"

_MIGRATION_0002 = """
ALTER TABLE daily_scan ADD COLUMN prior_day_high   REAL;
ALTER TABLE daily_scan ADD COLUMN prior_close      REAL;
ALTER TABLE daily_scan ADD COLUMN sma200           REAL;
ALTER TABLE daily_scan ADD COLUMN rvol_baseline    REAL;
ALTER TABLE daily_scan ADD COLUMN scan_pass        TEXT;
"""
```

**Updated list and version:**
```python
MIGRATIONS = [
    _MIGRATION_0001,
    _MIGRATION_0002,   # adds rich context columns to daily_scan (Phase 2, D-08)
]

CURRENT_VERSION = 2
```

**Migration runner stays unchanged** (lines 105-124) — it already handles the ordered list with `PRAGMA user_version`. No changes to `run_migrations()`.

---

### `bot/gateway/gateway.py` — add `subscribe()` method

**Analog:** Existing `get_positions()` and `get_acc_list()` methods in `bot/gateway/gateway.py`

**Exact run_in_executor pattern to mirror** (lines 282-299):
```python
async def get_acc_list(self) -> tuple:
    """Async wrapper: fetch account list from broker (non-blocking).

    Returns (ret, data) tuple from trade_ctx.get_acc_list().
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, self._trade_ctx.get_acc_list)

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

**`_check_ret` helper to reuse** (lines 172-182):
```python
def _check_ret(ret: int, data, action: str) -> None:
    """Check SDK return code; raise GatewayError on failure."""
    if ret != RET_OK:
        raise GatewayError(f"{action} failed: ret={ret}, data={data}")
```

**New `subscribe()` method to insert under "Async Wrappers" section** (after line 299):
```python
async def subscribe(self, codes: list, subtypes: list = None) -> None:
    """Subscribe to real-time K_5M candlestick pushes for the given codes.

    Only the capped top-20 watchlist should be passed (SIG-01) — never the
    full ~500-symbol universe. Each code+subtype pair consumes 1 quota slot;
    top-20 cap keeps usage at 20 of the 100-slot minimum tier.

    Parameters:
        codes: List of Moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
               Must be <= 20 items (SIG-01).
        subtypes: List of SubType values. Defaults to [SubType.K_5M].

    Raises:
        GatewayError: if subscribe() returns non-RET_OK.
    """
    from moomoo import SubType, Session
    if subtypes is None:
        subtypes = [SubType.K_5M]

    loop = asyncio.get_event_loop()

    def _subscribe_blocking():
        ret, msg = self._quote_ctx.subscribe(
            codes,
            subtypes,
            is_first_push=True,
            subscribe_push=True,
            extended_time=False,
            session=Session.NONE,
        )
        _check_ret(ret, msg, "subscribe")

    await loop.run_in_executor(None, _subscribe_blocking)
    _logger.info("subscribed_k5m", codes=codes, count=len(codes))
```

Note: `SubType` and `Session` are imported inside the method body (deferred import) — matches the moomoo SDK import pattern used elsewhere in the gateway to avoid top-level import failures when SDK is not installed during tests.

---

### `tests/scanner/__init__.py`

**Pattern:** Empty file. Mirror `tests/strategy/__init__.py` (0 bytes).

---

### `tests/scanner/test_universe.py` (test, file-I/O)

**Analog:** `tests/state/test_migrations.py`

**Test class structure** (`tests/state/test_migrations.py` lines 36-55):
```python
class TestMigrationFreshDb:
    """Migration 0001 applied to an empty database."""

    def test_all_five_tables_created(self, in_memory_conn):
        """After run_migrations, all v1 tables must exist in sqlite_master."""
        run_migrations(in_memory_conn)
        ...

class TestMigrationIdempotency:
    """Calling run_migrations twice on the same DB must be a no-op."""
    ...
```

**Fixture pattern** (`tests/state/test_migrations.py` lines 24-30):
```python
@pytest.fixture
def in_memory_conn():
    """Return a fresh in-memory SQLite connection, closed after the test."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()
```

**For `test_universe.py`:** Use `unittest.mock.patch("pandas.read_html", ...)` to mock the HTTP call. Use `tmp_path` fixture (pytest built-in) for the cache directory. One class per scenario: `TestWikipediaScrape`, `TestCacheFallback`, `TestTickerNormalization`.

---

### `tests/scanner/test_fetcher.py` (test, batch)

**Analog:** `tests/strategy/test_indicators.py`

**Synthetic DataFrame pattern** (`tests/strategy/test_indicators.py` lines 25-48):
```python
class TestSma:
    def test_sma_correct_value(self):
        """SMA of 200 values should equal their mean."""
        vals = [float(i) for i in range(1, 201)]
        series = pd.Series(vals)
        result = sma(series, 200)
        expected = sum(vals) / len(vals)
        assert abs(result - expected) < 1e-9
```

**For `test_fetcher.py`:** Mock `yf.download` and `yfinance.shared._ERRORS` with `unittest.mock.patch`. Build a synthetic MultiIndex DataFrame (or a dict of DataFrames keyed by ticker symbol) to return from the mock. Test classes: `TestDownloadDailyBars`, `TestDegradationGate`, `TestTickerNormalization`.

**Degradation test pattern:**
```python
def test_10pct_abort_raises(self, monkeypatch):
    # Arrange: mock shared._ERRORS to contain >= 50 failed tickers
    # Act: call download_daily_bars(symbols)
    # Assert: raises ScanDegradationError
```

---

### `tests/scanner/test_scanner.py` (test, orchestration)

**Analog:** `tests/gateway/test_gateway.py`

**Mock injection pattern** (`tests/gateway/test_gateway.py` lines 41-60):
```python
def _make_mock_ctx(acc_id: int = 123456789, broker_trd_env: str = "SIMULATE"):
    """Create a mock SDK trade context with get_acc_list() wired."""
    mock = MagicMock()
    df = pd.DataFrame([{"acc_id": acc_id, "trd_env": broker_trd_env}])
    mock.get_acc_list.return_value = (0, df)
    mock.position_list_query.return_value = (0, pd.DataFrame())
    return mock

def _make_gateway_with_mocks(...) -> MoomooGateway:
    """Return a MoomooGateway with pre-injected mock contexts."""
```

**For `test_scanner.py`:** Use factory helpers `_make_mock_store()`, `_make_mock_gateway()` to inject pre-built mocks. Use `tmp_state_db` fixture from `tests/conftest.py` for persistence tests. Use `minimal_rules` fixture from `tests/conftest.py` for `StrategyConfig`.

**`conftest.py` fixture reuse** (`tests/conftest.py` lines 20-29, 36-81):
```python
@pytest.fixture
def tmp_state_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bot_state_test.db")
    monkeypatch.setenv("BOT_STATE_DB", db_path)
    return db_path

@pytest.fixture
def minimal_rules():
    return { ... }  # canonical rules.json content
```

Both fixtures are available to `tests/scanner/` automatically (conftest.py at `tests/` level is auto-loaded by pytest).

---

### `tests/scanner/test_calendar.py` (test, deterministic)

**Analog:** `tests/safety/test_et_helpers.py` (thin-wrapper, deterministic date assertions)

**Pattern:** Use known fixed NYSE holidays and half-days (no mocking of pandas-market-calendars — use the real library with real known dates). Known dates confirmed in RESEARCH.md:
- `date(2024, 1, 1)` — New Year's Day holiday (not a trading day)
- `date(2023, 11, 24)` — Black Friday half-day (close at 13:00)

---

## Shared Patterns

### Structlog Logger
**Source:** `bot/safety/logger.py` lines 161-170 + `bot/gateway/gateway.py` line 44
**Apply to:** `bot/scanner/universe.py`, `bot/scanner/fetcher.py`, `bot/scanner/scanner.py`
```python
from bot.safety.logger import get_logger
_logger = get_logger(__name__)
# Usage:
_logger.warning("wikipedia_scrape_failed_using_cache", cache=cache_files[0])
_logger.error("scan_aborted_data_degradation", failed_count=len(failed), total=len(yf_symbols))
_logger.info("watchlist_persisted", codes=codes, count=len(codes), scan_date=str(scan_date))
```

### Raise-Not-Exit Error Handling
**Source:** `bot/config/loader.py` lines 24-55, `bot/gateway/gateway.py` lines 108-112
**Apply to:** All `bot/scanner/` modules
```python
# Custom exception per module — never sys.exit() in library code
class ScanDegradationError(Exception):
    """Raised when yfinance failure rate >= 10% of the universe (D-06)."""

# Raise at the point of failure; caller (CLI __main__ or Phase 5 scheduler) handles exit
raise ScanDegradationError(f"Data degradation: {len(failed)}/{len(yf_symbols)} symbols failed")
```

### ET Timezone
**Source:** `bot/safety/et_helpers.py` lines 21-35
**Apply to:** `bot/scanner/scanner.py`, `bot/scanner/calendar.py`
```python
from bot.safety.et_helpers import ET, now_et, to_et

scan_date = now_et().date()   # today in ET — never datetime.today()
```

### Config-Driven Parameters
**Source:** `bot/strategy/trend_join_long.py` lines 43-110, `bot/config/loader.py`
**Apply to:** `bot/scanner/scanner.py`
```python
# All filter thresholds from cfg — never literals
from bot.config.loader import StrategyConfig

def run_daily_scan(store: StateStore, gateway: MoomooGateway, cfg: StrategyConfig, ...) -> list:
    # Use: cfg.d3_min_gap_pct, cfg.min_price_usd, cfg.rvol_lookback_days
    # Never: gap_pct >= 3.0 (hardcoded)
```

### StateStore Usage
**Source:** `bot/state/store.py` lines 127-227
**Apply to:** `bot/scanner/scanner.py`
```python
# Upsert pattern (scanner.py):
store.conn.execute("""
    INSERT INTO daily_scan
        (scan_date, code, gap_pct, rank, created_at,
         prior_day_high, prior_close, sma200, rvol_baseline, scan_pass)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(scan_date, code) DO UPDATE SET
        gap_pct        = excluded.gap_pct,
        rank           = excluded.rank,
        prior_day_high = excluded.prior_day_high,
        prior_close    = excluded.prior_close,
        sma200         = excluded.sma200,
        rvol_baseline  = excluded.rvol_baseline,
        scan_pass      = excluded.scan_pass
""", (scan_date, code, gap_pct, rank, created_at,
      prior_day_high, prior_close, sma200, rvol_baseline, scan_pass))
store.conn.commit()
```

### Test Fixtures (conftest.py)
**Source:** `tests/conftest.py` lines 20-103
**Apply to:** All `tests/scanner/` test files
```python
# Available without import — pytest loads tests/conftest.py automatically:
# tmp_state_db(tmp_path, monkeypatch) — isolated SQLite for each test
# minimal_rules()                     — canonical rules.json dict
# mock_trade_ctx()                    — mock OpenSecTradeContext
```

### Module Docstring Format
**Source:** Any `bot/` file header, e.g. `bot/gateway/gateway.py` lines 1-15
**Apply to:** All new `bot/scanner/` files
```python
#!/usr/bin/env python3
"""
bot.scanner.<module> — One-line description.

Paragraph describing what the module provides and how it is used.

Exports: function_a, ClassB, CONSTANT_C
"""
```

---

## No Analog Found

All files have close analogs in the existing codebase. No files require falling back to RESEARCH.md patterns only.

---

## Metadata

**Analog search scope:** `bot/`, `tests/`, `skills/moomooapi/scripts/`
**Files scanned:** 11 source files read directly
**Pattern extraction date:** 2026-06-23
