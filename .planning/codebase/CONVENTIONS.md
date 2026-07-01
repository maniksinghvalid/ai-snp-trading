# Coding Conventions

**Analysis Date:** 2026-06-23

## Naming Patterns

**Files:**
- Scripts use lowercase with underscores: `get_ticker.py`, `place_order.py`, `common.py`, `check_env.py`
- Organized by category directory: `scripts/quote/`, `scripts/trade/`
- Common shared module always named: `common.py`

**Functions:**
- Public functions use snake_case: `create_quote_context()`, `place_order()`, `parse_market()`
- Private/internal functions prefix with underscore: `_ensure_utf8_io()`, `_check_opend_alive()`, `_env_check_is_cached()`
- Helper functions follow pattern: `get_*()`, `parse_*()`, `create_*()`, `check_*()`, `is_*()`, `safe_*()`, `format_*()`, `infer_*()`

**Variables:**
- Local variables use snake_case: `trd_market`, `acc_id`, `output_json`, `ret`, `data`
- Configuration/constant collections use UPPERCASE_WITH_UNDERSCORES: `KTYPE_MAP`, `MARKET_MAP`, `SORT_MAP`, `ORDER_SESSION_MAP`
- Module-level constants in UPPERCASE: `MIN_SDK_VERSION`, `SKILL_VERSION`, `SDK_MODULE_NAME`, `_ENV_CHECK_TTL`
- Private module-level constants prefix with underscore: `_CODE_PREFIX_TO_MARKET`, `_MARKET_NAMES`
- Temporary files/paths use conventional prefixes: `_CRYPTO_FIRM_CACHE_FILE`, `_ENV_CHECK_CACHE_FILE`, `STAMP_FILE`

**Types/Classes:**
- Dataclasses use PascalCase: `FutuConfig` (defined in `common.py` line 27)
- Enum types imported from moomoo SDK, used as-is: `TrdEnv`, `TrdMarket`, `OrderType`, `SubType`, `Market`, `Session`, etc.

**Parameters:**
- Function parameters use snake_case with common abbreviations: `code`, `qty`, `price`, `trd_env`, `trd_market`, `acc_id`, `security_firm`
- Boolean parameters suffixed with `_json` or use prefix pattern: `output_json`, `refresh_cache`, `fill_outside_rth`, `confirmed`
- String choice parameters often have `_str` suffix: `session_str`, `ktype`, `rehab`

## Code Style

**Formatting:**
- Python 3 shebang on all scripts: `#!/usr/bin/env python3`
- Line length appears reasonable (80-100 character range, no strict enforcement detected)
- Indentation: 4 spaces (Python standard)
- No type hints on most functions, except in `FutuConfig` dataclass which uses annotations

**Linting:**
- No `.pylintrc`, `.flake8`, or linting configuration detected
- No mandatory linting enforced in codebase
- UTF-8 encoding explicitly handled on Windows (see `_ensure_utf8_io()` in `common.py` lines 71-85)

## Import Organization

**Order (from examination of `scripts/quote/get_ticker.py`):**
1. Standard library: `argparse`, `json`, `sys`, `os`
2. Standard library (delayed imports): `math`, `pandas`, `textwrap`, `socket`, `time`, `subprocess`, `tempfile`
3. Local relative imports: `from common import (...)`

**Path Setup Pattern (consistent across all scripts):**
```python
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (...)
```
This pattern ensures `common.py` can be imported from any script in `scripts/` subdirectories.

**Path Aliases:**
- `os` imported as `_os` in scripts to avoid naming conflicts with local `os` module reference
- No other major path aliases or `from __future__` imports used

## Error Handling

**Patterns:**
- API calls check return code via `check_ret(ret, data, ctx, action_str)` function (defined in `common.py`)
- All business functions wrapped in try/except blocks with generic `Exception` catch
- Exit codes used: `sys.exit(0)` for success, `sys.exit(1)` for errors
- Error messages printed to stdout (plain text) or stderr (for warnings) with `[ERROR]` or `[WARN]` prefixes
- No custom exception classes; all errors raised as built-ins or SDK errors

**Example (from `scripts/quote/get_ticker.py`):**
```python
try:
    ctx = create_quote_context()
    ret, msg = ctx.subscribe([code], [SubType.TICKER])
    if ret != RET_OK:
        print(f"Subscription failed: {msg}")
        sys.exit(1)
    ret, data = ctx.get_rt_ticker(code, num=num)
    check_ret(ret, data, ctx, "get ticker data")
except Exception as e:
    if output_json:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
    else:
        print(f"Error: {e}")
    sys.exit(1)
finally:
    safe_close(ctx)
```

**Permission/Account Error Detection:**
- Special error message detection functions: `_is_no_account_error()`, `_is_unlock_needed_error()`, `_is_permission_error()` (in `common.py`)
- Provides contextual hints to users for specific error conditions (e.g., quota exhaustion, unlock required)

## Logging

**Framework:** `print()` for text output, `json.dumps()` for JSON output

**Patterns:**
- All scripts support `--json` flag for structured JSON output
- When `--json` is present, output single JSON object to stdout; otherwise, formatted text
- Warnings printed to stderr with `[WARN]` prefix via `print(..., file=sys.stderr)`
- Errors printed to stderr with `[ERROR]` prefix via `print(..., file=sys.stderr)`
- Success/data printed to stdout

**Examples:**
```python
# Text output
print("=" * 70)
print(f"Tick-by-Tick Trades: {code}")
print("=" * 70)

# JSON output
print(json.dumps({"code": code, "data": records}, ensure_ascii=False))

# Warnings
print(f"[WARN] Version stamp file not found: {STAMP_FILE}", file=sys.stderr)
```

## Comments

**When to Comment:**
- Module-level docstrings on all files (3-6 lines, describe function + usage example)
- Function docstrings on all public and most private functions
- Inline comments for complex logic, version constraints, or non-obvious behavior
- Section headers as comment blocks with `# ============================================================`

**JSDoc/TSDoc:**
- Not used; pure Python docstrings in triple-quoted format
- Docstrings describe function purpose, parameters, and return values inline
- No structured annotation format (e.g., `:param:`, `:return:`)

**Example (from `common.py` lines 43-59):**
```python
def get_config() -> FutuConfig:
    """
    Get Futu OpenAPI configuration

    Reads configuration from environment variables, using defaults for unset values.

    Environment variables:
        - FUTU_LOGIN_ACCOUNT: Futu login account
        - FUTU_LOGIN_PWD: Futu login password
        - FUTU_OPEND_HOST: OpenD host address (default: 127.0.0.1)
        ...

    Returns:
        FutuConfig: Configuration object
    """
```

## Function Design

**Size:** Functions typically 10-50 lines; longer functions (100+ lines) only for complex workflows like `place_order()`, `get_stock_filter()`

**Parameters:**
- Most functions accept 3-7 parameters
- Optional parameters use `None` as default, then resolved via `or` operator: `acc_id = acc_id or get_default_acc_id()`
- Boolean flags typically at end: `output_json=False`, `confirmed=False`, `refresh_cache=False`

**Return Values:**
- Script entry functions return `None` (side effects via print/exit)
- Helper functions return single values or tuples: `(ret, data)` matching SDK pattern
- Use early returns for validation/error cases

**Example (from `scripts/trade/place_order.py`):**
```python
def place_order(code, side, quantity, price=None, order_type="NORMAL",
                acc_id=None, trd_env=None, security_firm=None, output_json=False,
                confirmed=False, ...):
    acc_id = acc_id or get_default_acc_id()
    trd_env = parse_trd_env(trd_env) if trd_env else get_default_trd_env()
    trd_side = parse_trd_side(side)
    
    # Validation with early exit
    if not market:
        if output_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"Error: {msg}")
        sys.exit(1)
```

## Module Design

**Exports:**
- `common.py` is the only shared module; exports 30+ helper/utility functions
- Scripts are standalone (no inter-script imports)
- Each script in `scripts/quote/` or `scripts/trade/` can be executed independently

**Barrel Files:**
- No barrel exports or `__init__.py` files
- Direct imports from `common` using explicit names: `from common import (create_quote_context, ...)`

**Organization (from `scripts/quote/`):**
- 30+ independent quote scripts (one API call per script)
- Scripts follow naming pattern: `get_*.py` for query operations
- Trade scripts in separate `scripts/trade/` directory

## Data Processing

**Dataframe Handling:**
- All dataframe iterations use explicit loop pattern (lines 55-63 in `get_ticker.py`):
```python
for i in range(len(data)):
    row = data.iloc[i] if hasattr(data, "iloc") else data[i]
    records.append({...})
```
- No pandas method chaining or vectorized operations
- Compatibility check `hasattr(data, "iloc")` for DataFrame vs list handling

**Safe Access Functions (in `common.py`):**
- `safe_get(row, *keys, default="")` — handles missing fields with fallbacks
- `safe_float(val, default=0.0)` — parse with default for N/A, empty, None
- `safe_int(val, default=0)` — parse with numpy scalar support for precision
- `to_jsonable(val, default=None)` — convert to JSON-serializable types (line 776)

**JSON Serialization:**
- All JSON output uses `ensure_ascii=False` for Unicode support
- Custom helper `to_jsonable()` handles NaN/Inf/enum values
- `df_to_records(df, limit=None)` converts DataFrame to list of dicts (line 792)

## Configuration & Defaults

**Environment Variables (from `common.py` FutuConfig):**
- `FUTU_LOGIN_ACCOUNT` — Login account
- `FUTU_LOGIN_PWD` — Login password
- `FUTU_OPEND_HOST` — OpenD server (default: 127.0.0.1)
- `FUTU_OPEND_PORT` — OpenD port (default: 11111)
- `FUTU_TRD_ENV` — Trading environment (default: SIMULATE)
- `FUTU_DEFAULT_MARKET` — Default market (default: NONE)
- `FUTU_SECURITY_FIRM` — Default security firm
- `FUTU_ACC_ID` — Default account ID
- Also supports `MOOMOO_*` prefix variants

**Caching Strategy:**
- Environment check cached for 1 hour (`_ENV_CHECK_TTL = 3600`)
- Crypto firm auto-detect cached for 1 hour (`_CRYPTO_FIRM_CACHE_TTL`)
- Temp files in system temp directory: `/tmp/.moomoo_env_ok`, `/tmp/.moomoo_crypto_firm`

## Argument Parsing

**Pattern (all scripts follow this):**
```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("code", help="Stock code, e.g. HK.00700")
    parser.add_argument("--num", type=int, default=20, help="Number of ticks")
    parser.add_argument("--json", action="store_true", dest="output_json", help="JSON output")
    args = parser.parse_args()
    function_name(args.code, args.num, args.output_json)
```

**Conventions:**
- Positional arguments for required inputs (`code`)
- Dash-separated long options (`--market`, `--trd-env`, `--acc-id`)
- `--json` flag for JSON output support
- Boolean flags use `action="store_true"` with descriptive dest names
- All scripts are executable as main entry points

---

*Convention analysis: 2026-06-23*
