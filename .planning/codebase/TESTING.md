# Testing Patterns

**Analysis Date:** 2026-06-23

## Test Framework

**Runner:**
- No test runner detected (no `pytest.ini`, `conftest.py`, `tox.ini`, `setup.cfg`)
- No unit testing framework present (no imports of `pytest`, `unittest`, `nose`, etc.)

**Assertion Library:**
- Not applicable — no formal testing framework configured

**Run Commands:**
- Not applicable — no automated test suite defined
- Scripts are executed directly via Python: `python scripts/quote/get_ticker.py HK.00700 --num 20`
- Manual testing via command-line execution

## Test File Organization

**Location:**
- **No test files found** in the codebase
- No `test_*.py`, `*_test.py`, or `/tests/` directory structure
- Scripts themselves are executable tools, not testable modules

**Naming:**
- Not applicable — no test naming convention established

**Structure:**
- Not applicable — no test organization pattern

## Testing Strategy

**Current Approach: Manual Integration Testing**

All testing is performed through direct script execution. The codebase is designed as a collection of self-contained, executable scripts rather than a testable library. Each script serves as an integration point with the moomoo OpenAPI.

**Test Coverage Areas (via direct execution):**

1. **Environment Setup** (`scripts/check_env.py`)
   - Validates SDK installation and version
   - Checks OpenD connectivity
   - Used as pre-flight check before running other scripts
   - Output: Plain text or JSON result
   - Example:
     ```bash
     python scripts/check_env.py
     python scripts/check_env.py --json
     ```

2. **API Calls** (all quote and trade scripts)
   - Each script independently tests one API endpoint
   - Example test patterns:
     ```bash
     # Quote API
     python scripts/quote/get_ticker.py HK.00700 --num 20
     python scripts/quote/get_ticker.py HK.00700 --json
     
     # Trade API
     python scripts/trade/place_order.py US.AAPL BUY 100 --price 150 --json
     python scripts/trade/get_orders.py --market HK --trd-env SIMULATE
     ```

3. **Error Handling** (via try/except blocks)
   - Connection errors (OpenD unreachable)
   - Permission errors (insufficient quotes)
   - Account errors (no trading account available)
   - Version mismatches (SDK version too old)
   - Each script handles errors with `check_ret()` validation

## Mocking

**Framework:** Not used

**Patterns:**
- No mocking framework imported or configured
- Scripts directly invoke moomoo SDK methods
- Scripts directly connect to live OpenD process
- No mock contexts or test doubles

**What to Mock (if testing were formalized):**
- `create_quote_context()` → Return mock context with `subscribe()` and `get_rt_ticker()` methods
- `create_trade_context()` → Return mock context with trading methods
- OpenD connectivity check `_check_opend_alive()` → Could be stubbed
- Environment variable reads `get_config()` → Could be overridden via env vars

**What NOT to Mock:**
- DataFrame operations (these are SDK-specific return types)
- Enum conversions (TrdMarket, TrdSide, etc. — SDK enums)
- JSON serialization (output formatting is critical to test)

## Fixtures and Factories

**Test Data:**
- No fixtures defined
- Mapping dictionaries serve as configuration data: `KTYPE_MAP`, `MARKET_MAP`, `SORT_MAP` (in scripts)
- Dataclass `FutuConfig` in `common.py` lines 26-41 serves as configuration factory

**Location:**
- Not applicable — no fixture directory
- Constants defined inline in scripts where needed

**Example (from `scripts/quote/get_kline.py`):**
```python
KTYPE_MAP = {
    "1m": KLType.K_1M,
    "3m": KLType.K_3M,
    "5m": KLType.K_5M,
    # ... more mappings
}

REHAB_MAP = {
    "none": AuType.NONE,
    "forward": AuType.QFQ,
    "backward": AuType.HFQ,
}
```

**Configuration Factory (from `common.py`):**
```python
@dataclass
class FutuConfig:
    """Futu OpenAPI configuration class"""
    login_account: Optional[str] = None
    login_pwd: Optional[str] = None
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    trd_env: str = "SIMULATE"
    default_market: str = "NONE"
    security_firm: Optional[str] = None

def get_config() -> FutuConfig:
    return FutuConfig(...)
```

## Coverage

**Requirements:** No coverage requirements enforced

**View Coverage:** Not applicable (no test suite to measure)

**Implications:**
- All code paths are only exercised through live API calls
- Manual testing is the only validation mechanism
- Changes to core functions (e.g., `common.py` parsing/validation) require manual re-testing of multiple scripts

## Test Types

**Unit Tests:**
- **Not implemented**
- Would target: Parsing functions (`parse_market()`, `parse_qty()`, `_parse_version()`), safe accessors (`safe_get()`, `safe_float()`), enum mapping (`format_enum()`)
- Currently validated inline or implicitly through API call success/failure

**Integration Tests:**
- **De facto only test type** — each script is an integration test
- Tests: SDK connection, API call success, response parsing, output formatting
- Example: `place_order.py` tests the entire order placement workflow
- Status verification requires manual inspection or live account state check

**E2E Tests:**
- **Not formalized**
- Manual E2E: Execute workflow like: check_env → get_accounts → place_order → get_orders → cancel_order
- No automated E2E suite

## Environment Checks

**Pre-flight Validation (in `common.py`):**

All scripts automatically run `ensure_futu_api()` on import (line 207), which:
1. Checks for cached environment result (skip if < 1 hour old)
2. Validates version stamp file (`~/.moomoo_skill_version`)
3. Imports moomoo SDK and checks version >= `MIN_SDK_VERSION` (10.4.6408)
4. Tests OpenD connectivity on `FUTU_OPEND_HOST:FUTU_OPEND_PORT` (default 127.0.0.1:11111)
5. Marks environment check passed in temp file
6. Exits with error if any check fails

**Example:**
```python
def ensure_futu_api():
    """Environment check with cache: SDK version + stamp + OpenD connectivity."""
    if _env_check_is_cached():
        _detect_ai_type_support()
        return True

    _check_version_stamp()
    
    try:
        import moomoo
        current = getattr(moomoo, "__version__", "0")
        if _parse_version(current) < _parse_version(MIN_SDK_VERSION):
            _sdk_supports_ai_type = False
            print(f"[WARN] moomoo-api version too low: {current} < {MIN_SDK_VERSION}...", file=sys.stderr)
    except ImportError:
        print("[ERROR] moomoo-api not installed. Please run /install-moomoo-opend")
        sys.exit(1)

    _check_opend_reachable()
    _env_check_mark_ok()
    return True
```

## Testing Best Practices (for future implementation)

**If formalizing tests, follow these patterns:**

### 1. Mock Context Pattern
```python
# If testing were to be added
class MockQuoteContext:
    def subscribe(self, codes, subtypes):
        return RET_OK, "subscribed"
    
    def get_rt_ticker(self, code, num=20):
        # Return mock DataFrame with expected columns
        return RET_OK, pd.DataFrame({
            "time": ["2026-06-23 10:00:00"],
            "price": [150.0],
            "volume": [1000],
            "ticker_direction": ["UP"]
        })
```

### 2. Test Script Execution
```bash
# Isolated test execution
cd scripts/quote
python -c "
import sys
sys.path.insert(0, '..')
from common import parse_market, TrdMarket
assert parse_market('HK') == TrdMarket.HK
assert parse_market('US') == TrdMarket.US
print('✓ parse_market tests pass')
"
```

### 3. Error Case Validation
```bash
# Test error handling
python scripts/quote/get_ticker.py INVALID.CODE --json 2>&1 | grep -q error && echo "✓ Error handling works"
```

### 4. Output Format Validation
```bash
# Verify JSON output validity
python scripts/quote/get_ticker.py HK.00700 --num 1 --json | python -m json.tool > /dev/null && echo "✓ JSON valid"
```

## Known Testing Gaps

**Untested Areas:**

1. **Parsing Functions** (`scripts/common.py`):
   - `parse_qty()` (line 496) — Crypto float vs integer validation
   - `_parse_version()` (line 111) — Version string parsing and comparison
   - `parse_trd_side()` (line 526) — Buy/Sell parsing
   - No unit tests for edge cases (e.g., empty input, invalid market)

2. **Error Detection** (`scripts/common.py`):
   - `_is_no_account_error()` (line 630) — Multilingual error detection
   - `_is_permission_error()` (line 653) — Permission message detection
   - Only tested implicitly when API returns actual errors

3. **Cache Management** (`scripts/common.py`):
   - `_crypto_firm_cache_read()` (line 329) — Cache TTL and validity
   - `_env_check_is_cached()` (line 119) — Environment check caching
   - Relies on file system timing; difficult to test deterministically

4. **Data Transformation** (`scripts/common.py`):
   - `df_to_records()` (line 792) — DataFrame iteration and JSON conversion
   - `scale_int()` (line 848) — Decimal precision restoration
   - `format_big_number()` (line 875) — Number scaling and suffixing
   - Only tested through integration with API responses

5. **Trade Workflows** (`scripts/trade/*.py`):
   - Multi-step workflows (place → get → cancel)
   - Real vs simulated trading environment switching
   - Account and firm selection logic

**Risk/Priority:**
- **High:** Parsing functions and validation (used by all scripts, affects multiple workflows)
- **Medium:** Error detection (affects user experience and error guidance)
- **Medium:** Trade workflows (trading is safety-critical)
- **Low:** Data transformation (tested indirectly through API use)
- **Low:** Cache management (non-critical, graceful fallback if cache fails)

## Manual Testing Checklist

**Environment Setup:**
- [ ] Run `python scripts/check_env.py` — Should pass if SDK and OpenD are ready
- [ ] Run `python scripts/check_env.py --json` — Should output valid JSON

**Quote APIs:**
- [ ] `python scripts/quote/get_ticker.py HK.00700` — Should display recent ticks
- [ ] `python scripts/quote/get_ticker.py HK.00700 --json` — Should output JSON
- [ ] `python scripts/quote/get_kline.py US.AAPL` — Should display candlestick data
- [ ] `python scripts/quote/get_stock_filter.py --market HK --min-price 10` — Should list filtered stocks

**Trade APIs (Simulated):**
- [ ] `python scripts/trade/get_accounts.py` — Should list accounts
- [ ] `python scripts/trade/place_order.py US.AAPL BUY 100 --price 150` — Should show preview
- [ ] `python scripts/trade/place_order.py US.AAPL BUY 100 --price 150 --json` — Should output JSON preview

**Trade APIs (Real - with caution):**
- [ ] Unlock trade password in OpenD GUI first
- [ ] `python scripts/trade/place_order.py US.AAPL BUY 100 --price 150 --trd-env REAL --confirmed` — Places real order
- [ ] `python scripts/trade/get_orders.py --trd-env REAL` — Verify order appears

---

*Testing analysis: 2026-06-23*
