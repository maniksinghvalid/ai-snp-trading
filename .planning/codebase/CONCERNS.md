# Codebase Concerns

**Analysis Date:** 2026-06-23

## Tech Debt

**Bare Exception Handlers (Silent Failures)**

- **Issue:** Multiple exception handlers catch `Exception` without specific handling, silently masking errors
- **Files:**
  - `skills/moomooapi/scripts/common.py` (lines 77, 81, 374, 626, 737): UTF-8 reconfiguration, TLS setup, crypto probing, context closing, JSON output flag detection
  - `skills/moomooapi/scripts/trade/place_order.py` (line 72): Audit log write failures
  - `skills/moomooapi/scripts/trade/place_crypto_order.py` (line 56): Trade operation failures
  - `skills/moomooapi/scripts/quote/get_daily_short_volume.py` (line 53): Data processing failures
  - `skills/moomooapi/scripts/trade/get_accounts.py` (line 120): Account iteration failures
  - Multiple other trade/quote scripts with catch-all `except Exception: pass` patterns
- **Impact:** When errors occur, no logging or indication to user; harder to debug failures
- **Fix approach:** Replace bare `except Exception:` with specific exception types, at minimum log errors to stderr when appropriate, or raise/re-raise with context

**Global Mutable State for SDK Feature Detection**

- **Issue:** Module-level variable `_sdk_supports_ai_type` is declared `global` and mutated in `ensure_futu_api()` and `_detect_ai_type_support()` within `common.py`
- **Files:** `skills/moomooapi/scripts/common.py` (lines 104, 168, 189)
- **Impact:** State is shared across multiple script invocations in same process; if a script modifies the flag, it affects subsequent operations; testing and concurrency are problematic
- **Fix approach:** Use a configuration class or module singleton pattern with explicit initialization rather than mutable globals; consider returning feature support status from functions instead

**Soft Version Stamp Warnings (Non-blocking)**

- **Issue:** Version mismatches between installed OpenD/SDK and expected SKILL_VERSION are detected but only warn; scripts proceed anyway
- **Files:** `skills/moomooapi/scripts/common.py` (lines 143-146)
- **Impact:** User may be using outdated SDK/OpenD without realizing compatibility issues; features silently degrade (e.g., `ai_type` parameter skipped for old SDK)
- **Fix approach:** Consider making certain version checks blocking if they impact critical functionality (e.g., crypto requires MIN_CRYPTO_SDK_VERSION 10.5.6508); add explicit feature capability detection on script startup

**Inconsistent Connection Reachability Checks**

- **Issue:** Two separate functions check OpenD connectivity: `_check_opend_reachable()` (called during `ensure_futu_api()`) and `_check_opend_alive()` (called per context creation)
- **Files:** `skills/moomooapi/scripts/common.py` (lines 149-163, 262-276)
- **Impact:** First check may pass but connection fails by the time context is created; redundant checks waste time; code duplication
- **Fix approach:** Consolidate into single reachability check, cache result with reasonable TTL (similar to env check cache at line 107-108)

**Error Handling Divergence in Crypto Account Detection**

- **Issue:** `_detect_crypto_firm()` probes accounts by attempting context creation and catches all exceptions silently; if user has misconfigured environment, errors are swallowed
- **Files:** `skills/moomooapi/scripts/common.py` (lines 367-400)
- **Impact:** If account probing fails due to permissions, network, or SDK issues, user gets "no crypto account found" error instead of actual root cause
- **Fix approach:** Differentiate between "account not found" and "probe failed"; log probe failures or return more specific status

**Cache File TTL Race Condition**

- **Issue:** Environment check cache and crypto firm cache use file modification time checks, but window exists where file becomes stale (TTL elapses) between check and use
- **Files:** `skills/moomooapi/scripts/common.py` (lines 119-125, 331)
- **Impact:** Cache hit/miss logic can race; if file is deleted by another process, checks re-run unnecessarily
- **Fix approach:** Use more robust caching (e.g., atomic writes with lock files, or in-memory cache with shorter TTL)

## Known Bugs

**Quote Permission Error Detection Incomplete**

- **Issue:** Permission error keywords checked against lowercase error message, but not all API error formats may be covered
- **Files:** `skills/moomooapi/scripts/common.py` (lines 653-667)
- **Impact:** Some permission errors may not be recognized, showing generic error instead of helpful permission hint
- **Fix approach:** Add regression tests with real API error messages from Futu; expand keyword list based on actual observed errors

**Market Inference Bug (Not Tested for Crypto)**

- **Issue:** `infer_market_from_code()` returns market string (e.g., "CRYPTO"), but not all API calls accept this; trading context creation expects `TrdMarket` enum
- **Files:** `skills/moomooapi/scripts/common.py` (lines 517-523); used in `skills/moomooapi/scripts/trade/place_order.py` (line 105)
- **Impact:** If user passes crypto code (CC.BTC), market is inferred as "CRYPTO", but then market comparison/validation may fail
- **Fix approach:** Ensure market inference always returns enum or has conversion layer; add validation that inferred market matches expected types for operation

## Security Considerations

**Login Credentials Stored in Environment Variables Without Validation**

- **Issue:** `FUTU_LOGIN_ACCOUNT` and `FUTU_LOGIN_PWD` are read from environment and stored in `FutuConfig` dataclass with no encryption or masking
- **Files:** `skills/moomooapi/scripts/common.py` (lines 29-31, 60-62)
- **Current mitigation:** Credentials are read but not used directly in scripts (actual auth is delegated to OpenD which handles it)
- **Recommendations:**
  - Never log or print FutuConfig containing credentials
  - Add explicit handling to mask credentials if config is dumped/logged
  - Consider storing credentials in a more secure location (e.g., system keychain) rather than env vars
  - Add warning in SKILL.md about credential security

**Trade Audit Log Stored in User Home Directory**

- **Issue:** Trade operations are logged to `~/.futu_trade_audit.jsonl`, which may contain sensitive trade details
- **Files:** `skills/moomooapi/scripts/trade/place_order.py` (lines 64-73)
- **Current mitigation:** File is created with default permissions (may be readable by other users depending on OS)
- **Recommendations:**
  - Ensure audit log file is created with restrictive permissions (0600)
  - Document that this file is created and may contain sensitive data
  - Add option to disable audit logging for security-sensitive environments

**Insufficient Input Validation on Order Parameters**

- **Issue:** Order price, quantity, and other parameters are passed to API with minimal pre-validation
- **Files:** `skills/moomooapi/scripts/trade/place_order.py` (lines 119-131), `skills/moomooapi/scripts/common.py` (lines 496-514)
- **Current mitigation:** Some validation (positive qty, integer check) but not all edge cases handled
- **Recommendations:**
  - Add max value checks (prevent overflow orders)
  - Add precision validation per market type (e.g., US stocks <=\$1 allow 4 decimals)
  - Sanitize string inputs to prevent injection (though moomoo SDK should handle this)

**Real Trading Not Fully Protected from Accidental Execution**

- **Issue:** `--confirmed` flag is required for REAL trades, but only checked as string comparison
- **Files:** `skills/moomooapi/scripts/trade/place_order.py` (lines 134, 153)
- **Current mitigation:** Code checks for `--confirmed` flag in CLI args and prevents order execution without it
- **Recommendations:**
  - Consider second-factor confirmation (e.g., confirmation code generated at time of order)
  - Add explicit user prompt to re-confirm real trading orders before execution
  - Implement rate limiting on real orders (already in API: 15 req/30 sec, 0.02 sec between orders)

## Performance Bottlenecks

**Stock Filter Fallback Uses Hardcoded Top-50 Snapshot List**

- **Issue:** When `get_stock_filter()` returns unreliable data, code falls back to fetching snapshots of 40-50 hardcoded stocks per market
- **Files:** `skills/moomooapi/scripts/quote/get_stock_filter.py` (lines 121-173)
- **Impact:** Fallback may be slow (50 stocks = 1 batch of API calls); if API is slow, user experiences delays; fallback doesn't scale to large result sets
- **Fix approach:** Cache snapshot results more aggressively; allow configurable fallback stock list; consider timeout-based switching to fallback earlier

**Crypto Account Detection Probes Multiple Firms Sequentially**

- **Issue:** `_detect_crypto_firm()` probes up to 3 supported firms sequentially; each probe attempts context creation + account list query
- **Files:** `skills/moomooapi/scripts/common.py` (lines 380-400)
- **Impact:** If user doesn't have crypto enabled, probing takes multiple API roundtrips; noticeable delay before error message
- **Fix approach:** Parallelize probing (if async support available); implement early timeout detection; cache results more aggressively (already 1-hour TTL at line 326)

**Repeated Snapshot Fetches in Filter Enrichment**

- **Issue:** `_enrich_with_snapshot()` batches code list into 50-stock chunks, then falls back to single-stock queries on batch failure
- **Files:** `skills/moomooapi/scripts/quote/get_stock_filter.py` (lines 97-118)
- **Impact:** Worst-case is N+1 queries if batch fails (1 batch query + N individual queries); inefficient for large result sets
- **Fix approach:** Implement exponential backoff on batch failures; consider pre-filtering codes to smaller batches if known to have high failure rate

## Fragile Areas

**Environment Check Cache Implementation (Race Condition Risk)**

- **Files:** `skills/moomooapi/scripts/common.py` (lines 106-134)
- **Why fragile:**
  - Cache file is checked by multiple scripts in parallel; no locking
  - File could be deleted between existence check and use
  - TTL is checked against mtime; if system clock changes, TTL logic breaks
- **Safe modification:**
  - Always use `os.path.exists()` before reading; handle FileNotFoundError gracefully
  - Use atomic operations for cache writes
  - Consider in-process cache with fallback to file cache
- **Test coverage:** No unit tests for cache behavior; edge cases (clock skew, concurrent access) untested

**Crypto Feature Detection via ImportError (Version Misalignment Risk)**

- **Files:** `skills/moomooapi/scripts/common.py` (lines 234-244, 242-249)
- **Why fragile:**
  - Code catches ImportError for `OpenCryptoTradeContext` and `TimeInForce`, but doesn't guarantee they're in same SDK version
  - If SDK version has one but not the other, code may fail later
  - Version checks are done separately (MIN_CRYPTO_SDK_VERSION) but features are gated by import availability
- **Safe modification:**
  - Implement a comprehensive feature detection function that checks all crypto-related imports together
  - Validate SDK version number explicitly before attempting imports
- **Test coverage:** No integration tests with older SDK versions

**Global Exception Handler in `safe_close()` (Silent Failures)**

- **Files:** `skills/moomooapi/scripts/common.py` (lines 621-627)
- **Why fragile:**
  - Catches all exceptions without logging; if context close fails, user doesn't know
  - Used 193 times across scripts; any close failure is silently ignored
- **Safe modification:**
  - Log close failures (at least to stderr)
  - Consider adding optional `strict` parameter to fail hard on close errors
  - Use context managers (`with` statements) wherever possible instead of manual close
- **Test coverage:** No tests for context close behavior

## Scaling Limits

**Cache Storage (File System)**

- **Current capacity:** Cache files are small (single version string, timestamp); temporary directory is assumed unlimited
- **Limit:** If temporary directory is on low-disk volume, or if process creates many cache files, could exhaust space
- **Scaling path:** Implement memory cache with file fallback; add cache size limits; periodically clean stale cache files

**Hardcoded Stock Lists (Snapshot Fallback)**

- **Current capacity:** 40-50 hardcoded stocks per market
- **Limit:** If more stocks are needed for fallback, list must be manually updated; doesn't scale to dynamic watchlists
- **Scaling path:** Fetch stock list dynamically instead of hardcoding; cache results; allow user-provided lists via env var or config file

**Concurrent Script Execution (Shared Temp Files)**

- **Current capacity:** Environment and crypto caches use process-agnostic temp files; multiple scripts can race
- **Limit:** Under high concurrency (many scripts running in parallel), cache contention and file locks could cause slowdowns
- **Scaling path:** Implement file locking; consider per-process in-memory cache; implement cleanup for orphaned cache files

## Dependencies at Risk

**moomoo-api SDK Version Pinning**

- **Risk:** Codebase targets `moomoo-api >= 10.4.6408` with optional features at 10.5.6508+
- **Impact:** 
  - If moomoo-api breaks compatibility in minor version, scripts fail
  - No upper bound specified; breaking changes in newer versions could silently degrade functionality
  - Crypto features require explicit version check but `ai_type` parameter is gated by flags (see line 104)
- **Migration plan:**
  - Add explicit upper version bound if breaking changes are known
  - Document which SDK versions are tested/supported
  - Implement more granular feature detection (not just version numbers)

**Undeclared Pandas Dependency**

- **Risk:** Code uses pandas DataFrame operations (`.iloc[]`, `.iterrows()`) but pandas not explicitly imported at top level
- **Impact:** 
  - Scripts that call `df_to_records()` or `print_display_df()` will fail if pandas is not installed
  - No error message indicates missing pandas; SDK import error comes first
  - Some scripts use pandas implicitly through API return values
- **Migration plan:**
  - Add explicit pandas import guard in `common.py`
  - Document pandas as required dependency
  - Add optional flag to disable pandas-dependent features

**OpenD Client Dependency (Hard Runtime Requirement)**

- **Risk:** All scripts require OpenD client running locally; no graceful degradation if unavailable
- **Impact:**
  - If OpenD crashes or fails to start, all scripts fail immediately
  - Network-based OpenD access (remote host) not supported by default
  - No fallback to REST API or other transport
- **Migration plan:**
  - Support configurable OpenD host (already done via FUTU_OPEND_HOST)
  - Implement retry/reconnection logic
  - Add health check endpoint to OpenD

## Missing Critical Features

**No Transaction Rollback/Undo Support**

- **Problem:** Once an order is placed, it cannot be "undone" via this skill; user must manually cancel in OpenD GUI or use `cancel_order.py`
- **Blocks:** Atomic multi-order operations (e.g., buy hedge + short position) cannot be rolled back as a unit

**No Detailed Order Status Polling**

- **Problem:** Order execution status is returned by API but not continuously polled; if order fails to fill, user may not know
- **Blocks:** Automated trading strategies that depend on real-time order status updates

**No Risk Management Constraints**

- **Problem:** No built-in position limits, loss limits, or account balance checks before order placement
- **Blocks:** Automated trading systems that need to enforce risk limits

**Limited Error Recovery**

- **Problem:** Most scripts fail hard on first error; no retry logic, backoff, or automatic recovery
- **Blocks:** Reliable trading in unstable network conditions

## Test Coverage Gaps

**No Unit Tests for Common Utilities**

- **What's not tested:**
  - Version parsing: `_parse_version()` corner cases (empty strings, non-numeric versions)
  - Enum conversion: `format_enum()`, `parse_market()`, `parse_trd_side()`
  - Data conversion: `safe_float()`, `safe_int()`, `to_jsonable()` with NaN/Inf values
  - Error detection: `_is_permission_error()`, `_is_no_account_error()` with all known error message formats
- **Files:** `skills/moomooapi/scripts/common.py` (lines 111-615)
- **Risk:** Utility functions are used throughout codebase; bugs here cascade to all scripts
- **Priority:** High

**No Integration Tests with Real OpenD**

- **What's not tested:**
  - End-to-end order placement (even in SIMULATE mode)
  - Crypto account detection with multiple firms
  - Quote permission error scenarios
  - All market types (HK, US, SH, SZ, SG, CRYPTO)
- **Files:** All scripts in `skills/moomooapi/scripts/`
- **Risk:** Regressions in OpenD connectivity or API changes not caught until user tries to execute
- **Priority:** High

**No Version Compatibility Tests**

- **What's not tested:**
  - Scripts with SDK versions 10.4.x vs 10.5.x (crypto features)
  - Behavior when `ai_type` parameter is not supported
  - Fallback code paths when newer enums (e.g., TimeInForce) are unavailable
- **Files:** `skills/moomooapi/scripts/common.py`, scripts using conditional imports
- **Risk:** Old SDK users hit untested code paths
- **Priority:** Medium

**No Concurrent Execution Tests**

- **What's not tested:**
  - Multiple scripts accessing cache files simultaneously
  - Race conditions in `_env_check_is_cached()`
  - Lock contention on temp files
- **Files:** All scripts using cache mechanisms
- **Risk:** Fails under load or in parallel execution
- **Priority:** Medium

---

*Concerns audit: 2026-06-23*
