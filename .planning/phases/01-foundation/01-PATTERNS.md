# Phase 1: Foundation - Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 18 new/modified files
**Analogs found:** 15 / 18 (3 have no close analog — pure-strategy and asyncio code)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `bot/__init__.py` | config | — | `skills/moomooapi/scripts/common.py` | structural-ref |
| `bot/gateway/__init__.py` | config | — | `skills/moomooapi/scripts/common.py` | structural-ref |
| `bot/gateway/gateway.py` | service | request-response | `skills/moomooapi/scripts/common.py` | role-match |
| `bot/config/__init__.py` | config | — | `skills/moomooapi/scripts/common.py` | structural-ref |
| `bot/config/loader.py` | utility | transform | `skills/moomooapi/scripts/common.py` (`get_config`) | role-match |
| `bot/state/__init__.py` | config | — | none | no-analog |
| `bot/state/store.py` | service | CRUD | `skills/moomooapi/scripts/common.py` (env-check caching) | partial-match |
| `bot/strategy/__init__.py` | config | — | none | no-analog |
| `bot/strategy/core.py` | utility | transform | none | no-analog |
| `bot/strategy/trend_join_long.py` | utility | transform | `skills/moomooapi/scripts/common.py` (enum/parse pattern) | partial-match |
| `bot/strategy/indicators.py` | utility | transform | none | no-analog |
| `bot/safety/__init__.py` | config | — | none | no-analog |
| `bot/safety/paper_guard.py` | middleware | request-response | `skills/moomooapi/scripts/trade/get_accounts.py` | role-match |
| `bot/safety/audit_log.py` | utility | file-I/O | `skills/moomooapi/scripts/trade/place_order.py` (`_audit_log`) | exact |
| `bot/safety/kill_switch.py` | utility | event-driven | none | no-analog |
| `bot/safety/logger.py` | utility | file-I/O | `skills/moomooapi/scripts/common.py` (stderr/stdout pattern) | partial-match |
| `bot/safety/et_helpers.py` | utility | transform | none | no-analog |
| `tests/conftest.py` | test | — | none | no-analog |
| `rules.json` | config | — | `skills/moomooapi/scripts/common.py` (`FutuConfig`) | partial-match |
| `requirements.txt` / `requirements-dev.txt` | config | — | none | no-analog |

---

## Pattern Assignments

### `bot/gateway/gateway.py` (service, request-response)

**Analog:** `skills/moomooapi/scripts/common.py`
**Divergence note (D-02):** `MoomooGateway` imports the moomoo SDK directly — it does NOT import from `skills/moomooapi/scripts/`. The patterns below are references to mirror, not to `import`.

**Config dataclass pattern** (common.py lines 26-68):
```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class FutuConfig:
    """Futu OpenAPI configuration class"""
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    trd_env: str = "SIMULATE"
    default_market: str = "NONE"
    security_firm: Optional[str] = None

def get_config() -> FutuConfig:
    return FutuConfig(
        opend_host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
        opend_port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        trd_env=os.getenv("FUTU_TRD_ENV", "SIMULATE"),
        default_market=os.getenv("FUTU_DEFAULT_MARKET", "NONE"),
        security_firm=os.getenv("FUTU_SECURITY_FIRM", "") or None,
    )
```
Mirror this as `GatewayConfig` dataclass in `bot/gateway/gateway.py`, reading the same env vars plus `FUTU_ACC_ID` and `PAPER_TRADING`.

**Socket connectivity check pattern** (common.py lines 262-276):
```python
def _check_opend_alive(host, port):
    """Quick check if OpenD port is reachable, exit with error if not"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        print(f"Error: Cannot connect to OpenD ({host}:{port}), connection refused.")
        sys.exit(1)
    except OSError as e:
        print(f"Error: Cannot connect to OpenD ({host}:{port}): {e}.")
        sys.exit(1)
    finally:
        sock.close()
```
Mirror as `_check_opend_alive(host, port) -> None` raising `ConnectionError` instead of `sys.exit` (the gateway raises; the bot main catches and logs+exits).

**Context factory pattern** (common.py lines 279-318):
```python
def create_quote_context():
    host, port = get_opend_config()
    _check_opend_alive(host, port)
    return OpenQuoteContext(host=host, port=port)

def create_trade_context(market=None, security_firm=None):
    host, port = get_opend_config()
    _check_opend_alive(host, port)
    trd_market = parse_market(market) if market else get_default_market()
    kwargs = dict(host=host, port=port, filter_trdmarket=trd_market)
    if security_firm is not None:
        kwargs["security_firm"] = security_firm
    else:
        kwargs["security_firm"] = SecurityFirm.NONE
    return OpenSecTradeContext(**kwargs)
```
Mirror as `MoomooGateway._make_quote_ctx()` and `MoomooGateway._make_trade_ctx()` — persistent contexts held as instance attributes, re-created on reconnect. Wrap blocking SDK calls with `asyncio.get_event_loop().run_in_executor(None, ...)`.

**SDK return-code check pattern** (common.py lines 731-762):
```python
def check_ret(ret, data, ctx=None, action="operation", output_json=None):
    """Check API return value, print error and exit on failure"""
    if ret != RET_OK:
        # ... error categorisation and hint building ...
        safe_close(ctx)
        sys.exit(1)
```
Mirror as `_check_ret(ret, data, action: str) -> None` raising `GatewayError(action, data)` instead of exiting. The gateway never calls `sys.exit`.

**safe_close pattern** (common.py lines 621-627):
```python
def safe_close(ctx):
    """Safely close a context"""
    try:
        if ctx:
            ctx.close()
    except Exception:
        pass
```
Copy verbatim as `_safe_close(ctx)` in gateway.

**Enum/market parsing pattern** (common.py lines 453-475):
```python
def parse_trd_env(env_str):
    if env_str and str(env_str).upper() == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE

def parse_market(market_str):
    mapping = {
        "NONE": TrdMarket.NONE,
        "US": TrdMarket.US,
        ...
    }
    return mapping.get(str(market_str).upper(), TrdMarket.US)
```
Mirror as module-level helpers in `bot/gateway/gateway.py`; these convert the `GatewayConfig` string fields to SDK enums at context-creation time.

---

### `bot/config/loader.py` (utility, transform)

**Analog:** `skills/moomooapi/scripts/common.py` `get_config()` pattern (lines 43-68)

**Pattern to mirror** — env-var reader with typed defaults:
```python
@dataclass
class FutuConfig:
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    trd_env: str = "SIMULATE"

def get_config() -> FutuConfig:
    return FutuConfig(
        opend_host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
        opend_port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        ...
    )
```
Mirror as `load_strategy_config(path: str = "rules.json") -> StrategyConfig`:
- Read JSON file; raise `ConfigError` with a human-readable message on `FileNotFoundError` or `json.JSONDecodeError`.
- Validate with `jsonschema.validate(data, SCHEMA)`.
- Map validated dict to a `StrategyConfig` dataclass (field names match `rules.json` top-level keys).
- Analogous to `get_config()` but source is a file, not env vars; same "read once at startup, fail fast" philosophy.

**Fail-fast early-exit style** (common.py lines 196-205):
```python
try:
    import moomoo
    current = getattr(moomoo, "__version__", "0")
    ...
except ImportError:
    print("[ERROR] moomoo-api not installed.")
    sys.exit(1)
```
Mirror in `load_strategy_config`: wrap file-read + validate in `try/except`, re-raise as a project-specific `ConfigError` with a clear message. The bot's `main()` catches and logs+exits.

---

### `bot/safety/paper_guard.py` (middleware, request-response)

**Analog:** `skills/moomooapi/scripts/trade/get_accounts.py` + `skills/moomooapi/scripts/common.py`

**`get_acc_list` access pattern** (get_accounts.py lines 101-123):
```python
def get_accounts(output_json=False, show_disabled=False):
    for firm in _ALL_SECURITY_FIRMS:
        ctx = None
        try:
            ctx = create_trade_context(market="NONE", security_firm=firm)
            ret, data = ctx.get_acc_list()
            if ret != 0 or is_empty(data):
                continue
            for i in range(len(data)):
                row = data.iloc[i] if hasattr(data, "iloc") else data[i]
                acc = _parse_account_row(row)
                ...
        except Exception:
            pass
        finally:
            safe_close(ctx)
```
`assert_paper_account()` calls `trade_ctx.get_acc_list()` on the already-open trade context, filters to the configured `acc_id`, and reads `trd_env` field from the matching row.

**Account row field access pattern** (get_accounts.py lines 86-98):
```python
return {
    "acc_id": safe_int(safe_get(row, "acc_id", default=0)),
    "trd_env": format_enum(safe_get(row, "trd_env", default="")),
    "security_firm": format_enum(safe_get(row, "security_firm", default="")),
    ...
}
```
Use `safe_int` / `safe_get` / `format_enum` equivalents to read `acc_id` and `trd_env` from the DataFrame row returned by `get_acc_list()`.

**Triple-guard structure** (D-03/D-04/D-05/D-06) — no existing analog; compose from above:
```python
def assert_paper_account(cfg: GatewayConfig, trade_ctx) -> None:
    """Triple independent assertion — fail-closed. Called at startup AND before every place_order."""
    # Guard 1: env flag
    if not cfg.paper_trading:
        _fail("PAPER_TRADING env var is not 'true'")
    # Guard 2: FUTU_TRD_ENV
    if cfg.trd_env.upper() != "SIMULATE":
        _fail("FUTU_TRD_ENV is not SIMULATE")
    # Guard 3: broker-reported trd_env for configured acc_id
    ret, data = trade_ctx.get_acc_list()
    # ... iterate rows, find cfg.acc_id, check trd_env == SIMULATE ...
    # Any failure calls _fail() which: logs structured error, appends audit log entry, sys.exit(1)
```

**Fail pattern** (mirrors common.py lines 756-762):
```python
safe_close(ctx)
sys.exit(1)
```
`_fail(reason)` in paper_guard: write structured log line + audit log entry recording the refusal, then `sys.exit(1)`.

---

### `bot/safety/audit_log.py` (utility, file-I/O)

**Analog:** `skills/moomooapi/scripts/trade/place_order.py` lines 64-73

**Exact pattern to extend** (place_order.py lines 64-73):
```python
def _audit_log(entry):
    """Append trade audit log to ~/.futu_trade_audit.jsonl"""
    import datetime
    try:
        log_path = _os.path.join(_os.path.expanduser("~"), ".futu_trade_audit.jsonl")
        entry["timestamp"] = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
```
This is the canonical pattern. `bot/safety/audit_log.py` wraps this into a public `append_audit(entry: dict) -> None`:
- Same path `~/.futu_trade_audit.jsonl` (SAFE-05: extend, don't replace).
- Same `"a"` open mode (append-only, never overwrites).
- Same `ensure_ascii=False` JSON serialization.
- Same silent-on-failure behavior (`except Exception: pass`) — audit failures never block the trade path.
- Add `"bot_version"` field alongside `"timestamp"` for traceability.
- Call `datetime.datetime.utcnow().isoformat() + "Z"` for UTC timestamps (consistent with structlog).

---

### `bot/state/store.py` (service, CRUD)

**Analog:** `skills/moomooapi/scripts/common.py` env-check caching pattern (lines 107-134) — closest match for "write to a file atomically, cache result, read back".

**Atomic write pattern to mirror** (common.py lines 128-134):
```python
def _env_check_mark_ok():
    """Mark environment check as passed"""
    try:
        with open(_ENV_CHECK_CACHE_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass
```
For SQLite state, atomic writes via temp-file + `os.replace()` (D-10) follow this same "write to temp, then atomically swap" principle:
```python
# Pattern for any non-DB state snapshot flush (D-10):
import os, tempfile, json

def _atomic_write_json(path: str, data: dict) -> None:
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

**Migration runner structure** — no existing analog; use Python stdlib `sqlite3` with `PRAGMA user_version`:
```python
import sqlite3

CURRENT_VERSION = 1

_MIGRATIONS = [
    # 0001 — full v1 schema
    """
    CREATE TABLE IF NOT EXISTS positions (...);
    CREATE TABLE IF NOT EXISTS trades (...);
    CREATE TABLE IF NOT EXISTS daily_scan (...);
    CREATE TABLE IF NOT EXISTS bar_cache (...);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """,
]

def _run_migrations(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {i}")
```

**DB path from config/env** (mirrors common.py `get_config()` env-var defaults pattern):
```python
DB_PATH = os.getenv("BOT_STATE_DB", os.path.join("data", "bot_state.db"))
```

---

### `bot/strategy/core.py` (utility, transform)

**No close analog.** The existing codebase has no ABC or strategy pattern. Use research defaults:
- Define `StrategyCore` as an `abc.ABC` with `@abstractmethod` hooks for `on_daily_bar`, `on_intraday_bar`, `check_entry`, `check_exit`.
- No I/O inside the ABC or its implementations — pure DataFrame-in, signal-out.
- All strategy parameters come from a `StrategyConfig` instance passed at construction, never from module-level constants or hardcoded literals (D-12).

---

### `bot/strategy/trend_join_long.py` (utility, transform)

**No close analog** for strategy logic. Follow the `parse_*` helper pattern from common.py for any enum/type coercion within the strategy.

**Config-driven parameter access pattern** (mirrors common.py `get_config()` field access):
```python
# common.py style — resolve param from config, never hardcode
sma_period = self._cfg.sma_period          # from rules.json, not a literal
rvol_threshold = self._cfg.rvol_threshold  # same
```
Each filter (D1/D2/D3) is a private method `_check_d1(bar: pd.Series) -> bool` following the `_is_*` / `_check_*` helper naming convention from common.py.

---

### `bot/strategy/indicators.py` (utility, transform)

**No close analog.** Pure pandas computations — SMA200, RVOL, swing_low_2_2. No I/O.

**safe_float / safe_int pattern** (common.py lines 583-607) — use project-standard null-safe helpers when reading DataFrame values:
```python
def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default
```
Copy these helpers verbatim into `bot/gateway/gateway.py` (or a shared `bot/_utils.py`) rather than re-importing from the skills dir.

---

### `bot/safety/logger.py` (utility, file-I/O)

**Analog:** `skills/moomooapi/scripts/common.py` stderr/stdout print pattern — partial match only (the bot uses structlog, not print).

**Stderr warning prefix convention to preserve** (common.py lines 143-146):
```python
print(f"[WARN] Version stamp file not found: {STAMP_FILE}.", file=sys.stderr)
```
structlog configuration should emit `level=warning` events that include the same `[WARN]` / `[ERROR]` semantic distinction. Configure a `structlog.PrintLogger` or `logging.FileHandler` rotating handler with JSON renderer for file output and a plain ConsoleRenderer for stderr during development.

**Section header comment style** (common.py lines 88-90 and throughout):
```python
# ============================================================
# Section Name
# ============================================================
```
Use this same visual section separator in `logger.py` and all other new bot modules.

---

### `bot/safety/et_helpers.py` (utility, transform)

**No close analog.** Pure `zoneinfo` helpers:
```python
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
```
Never use fixed UTC offsets or `pytz`. All `datetime.now()` calls pass `tz=ET`.

---

### `bot/safety/kill_switch.py` (utility, event-driven)

**No close analog.** File-sentinel check + `signal.signal(signal.SIGINT, handler)` pattern. The sentinel path should be configurable (env var or config field). On trigger: set a `threading.Event`, log structured shutdown message, flush state.

---

### `tests/conftest.py` (test)

**No close analog** (no existing test infrastructure). Follow D-11:
- `pytest` fixtures for `tmp_path`-based SQLite DB (overrides `BOT_STATE_DB` env var).
- Fixture providing a minimal valid `rules.json` dict.
- Fixture providing a `MoomooGateway` with a mocked `OpenSecTradeContext` / `OpenQuoteContext`.

---

## Shared Patterns

### Module-level docstrings
**Source:** `skills/moomooapi/scripts/common.py` lines 1-11
**Apply to:** Every new `bot/` Python file
```python
#!/usr/bin/env python3
"""
<module purpose — 1 line>

<What it contains / key exports — 2-4 lines>
"""
```

### Section header separators
**Source:** `skills/moomooapi/scripts/common.py` (throughout)
**Apply to:** Every new module with multiple logical sections
```python
# ============================================================
# Section Name
# ============================================================
```

### Private helper prefix
**Source:** `skills/moomooapi/scripts/common.py` (e.g., `_check_opend_alive`, `_is_no_account_error`)
**Apply to:** All internal helpers across `bot/` — prefix with `_`

### Null-safe accessors
**Source:** `skills/moomooapi/scripts/common.py` lines 574-607
**Apply to:** Any code reading moomoo SDK DataFrame rows (`bot/gateway/gateway.py`, `bot/safety/paper_guard.py`)
```python
def safe_get(row, *keys, default=""):
    for key in keys:
        val = row.get(key) if hasattr(row, 'get') else getattr(row, key, None)
        if val is not None:
            return val
    return default

def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    if val is None:
        return default
    if hasattr(val, 'item'):
        val = val.item()
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default
```
Copy these into `bot/gateway/gateway.py` (or a shared `bot/_utils.py`). Do not import from `skills/`.

### JSONL audit log append
**Source:** `skills/moomooapi/scripts/trade/place_order.py` lines 64-73
**Apply to:** `bot/safety/paper_guard.py` (guard-refusal entry), all Phase 4 order placement code
```python
def _audit_log(entry):
    import datetime
    try:
        log_path = os.path.join(os.path.expanduser("~"), ".futu_trade_audit.jsonl")
        entry["timestamp"] = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
```

### Raise, don't sys.exit, inside library code
**Source:** `skills/moomooapi/scripts/common.py` — the scripts call `sys.exit`; the bot's library modules must NOT.
**Apply to:** `bot/gateway/gateway.py`, `bot/config/loader.py`, `bot/safety/paper_guard.py`
- Raise project exceptions (`GatewayError`, `ConfigError`, `PaperGuardError`) instead.
- Only `bot/main.py` (Phase 5) calls `sys.exit`.

### Paper-trading default (SIMULATE)
**Source:** `skills/moomooapi/scripts/common.py` line 65, `get_config()` default
**Apply to:** `bot/gateway/gateway.py` `GatewayConfig` defaults
```python
trd_env: str = "SIMULATE"  # always default; never TrdEnv.REAL as default
```

### `format_enum` for SDK enum → string
**Source:** `skills/moomooapi/scripts/common.py` lines 610-614
**Apply to:** `bot/safety/paper_guard.py` when reading `trd_env` field from `get_acc_list()` DataFrame
```python
def format_enum(val):
    if hasattr(val, "name"):
        return val.name
    return str(val)
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `bot/strategy/core.py` | utility | transform | No ABC or strategy pattern exists in the codebase |
| `bot/strategy/trend_join_long.py` | utility | transform | No signal/indicator logic exists; pure-Python finance logic is new |
| `bot/strategy/indicators.py` | utility | transform | No pandas SMA/RVOL/swing-low computation in codebase |
| `bot/safety/kill_switch.py` | utility | event-driven | No signal-handler or sentinel-file pattern exists |
| `bot/safety/et_helpers.py` | utility | transform | No timezone helpers exist; new stdlib `zoneinfo` usage |
| `tests/conftest.py` | test | — | No test infrastructure exists at all |

For these files, use patterns from `.planning/research/ARCHITECTURE.md` and `.planning/research/STACK.md`.

---

## Metadata

**Analog search scope:** `skills/moomooapi/scripts/` (common.py, trade/place_order.py, trade/get_accounts.py)
**Files scanned:** 4 source files read in full
**Pattern extraction date:** 2026-06-23
