<!-- refreshed: 2026-06-23 -->
# Architecture

**Analysis Date:** 2026-06-23

## System Overview

This codebase comprises two interconnected skill modules for the moomoo OpenAPI trading platform:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                    moomooapi Skill (Market Data & Trading)              │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│   Quote Scripts      │   Trade Scripts      │   Subscribe Scripts      │
│ `scripts/quote/`     │ `scripts/trade/`     │ `scripts/subscribe/`     │
│                      │                      │                          │
│ 55+ market data      │ 20+ trading/account  │ 6 subscription handlers  │
│ and research queries │ operations           │ for real-time pushes     │
└──────────────────────┴──────────────────────┴──────────────────────────┘
         │                      │                      │
         └──────────────────────┴──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Common Utilities & Configuration                            │
│           `scripts/common.py` - Shared infrastructure                   │
│  - FutuConfig: Environment variable configuration                       │
│  - OpenD connectivity & context management                              │
│  - Dependency checking (SDK version, OpenD availability)                │
│  - Error handling & response parsing                                    │
│  - Enum conversion utilities                                            │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   OpenD Service (External)                               │
│                    127.0.0.1:11111 (default)                            │
│  - Market data (snapshots, candlesticks, order books)                   │
│  - Trading operations (place/modify/cancel orders)                      │
│  - Position & account queries                                           │
│  - Real-time push subscriptions                                         │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              install-moomoo-opend Skill (Installation)                  │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│  Platform Detection  │  Download & Extract  │  SDK Installation        │
│  `detect_version.md` │  `install_*.md`      │  `verify_version.md`     │
│                      │                      │                          │
│  OS auto-detection   │  Platform-specific   │  SDK version check &     │
│  Version comparison  │  installation flows  │  dependency setup        │
└──────────────────────┴──────────────────────┴──────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Quote Scripts | Query market data (snapshots, candlesticks, order books, tickers, time-sharing) | `scripts/quote/*.py` |
| Market Research Scripts | Financial statements, analyst ratings, valuations, corporate actions, shareholder data | `scripts/quote/*.py` (27 scripts) |
| Trade Scripts | Place/modify/cancel orders, query positions, funds, accounts, crypto trading | `scripts/trade/*.py` |
| Subscription Scripts | Subscribe/unsubscribe to real-time pushes; receive market data updates | `scripts/subscribe/*.py` |
| Common Utilities | Configuration, context creation, dependency checking, error handling, enums | `scripts/common.py` |
| Installer Assistant | OS detection, OpenD download, SDK installation, version management | `scripts/install_*.md`, `scripts/detect_version.md`, `scripts/verify_version.md` |

## Pattern Overview

**Overall:** Script-based CLI pattern with shared utility layer

**Key Characteristics:**
- **Modular scripts**: Each market operation or trade action is a standalone Python script in `quote/`, `trade/`, or `subscribe/` subdirectories
- **Shared utilities**: `common.py` provides configuration, context management, and reusable helpers
- **OpenD broker pattern**: All operations delegate to a locally-running OpenD service (WebSocket-based, default `127.0.0.1:11111`)
- **Environment-driven configuration**: FutuConfig reads from environment variables (FUTU_OPEND_HOST, FUTU_TRD_ENV, etc.)
- **Error handling and version checks**: Built-in dependency detection and automatic environment validation

## Layers

**Presentation Layer (Scripts):**
- Purpose: User-facing CLI entry points that parse arguments and format output (table or JSON)
- Location: `scripts/quote/*.py`, `scripts/trade/*.py`, `scripts/subscribe/*.py`
- Contains: Argument parsing, output formatting, business logic orchestration
- Depends on: `common.py` for context creation and utilities
- Used by: End users via command-line or AI agents

**Application Layer (Common Utilities):**
- Purpose: Centralized configuration, context management, and reusable utilities
- Location: `scripts/common.py`
- Contains: 
  - `FutuConfig`: Dataclass for environment-based configuration
  - `create_quote_context()` / `create_trade_context()`: Factory functions for OpenD contexts
  - Dependency checkers (`_check_sdk`, `_check_opend_reachable`, `_check_version_stamp`)
  - Environment validation with caching (1-hour TTL)
  - Parsing helpers for enums (market, trading environment, security firm)
  - Safe accessors for DataFrame/dict fields (`safe_get`, `safe_float`, `safe_int`)
  - Error checking and context cleanup (`check_ret`, `safe_close`)
- Depends on: moomoo SDK, environment variables
- Used by: All quote, trade, and subscribe scripts

**Infrastructure Layer (OpenD Service):**
- Purpose: Broker service handling all protocol communication with moomoo API
- Location: External service running on `127.0.0.1:11111` (configurable)
- Contains: WebSocket/protobuf protocol handling, market data subscriptions, order execution
- Depends on: moomoo server backend
- Used by: All scripts via SDK context

**Installation Layer:**
- Purpose: Download, extract, and install OpenD and Python SDK
- Location: `scripts/install_*.md` (platform-specific), `scripts/detect_version.md`, `scripts/verify_version.md`
- Contains: OS detection, version comparison, download fallback logic, installation workflows
- Depends on: curl/PowerShell, Python package manager (pip)
- Used by: AI agent during initial setup

## Data Flow

### Primary Request Path (Market Data Query)

1. User invokes: `python scripts/quote/get_snapshot.py US.AAPL HK.00700` (entry point)
2. Script imports and calls `create_quote_context()` from `common.py` (context creation, dependency check)
3. Context manager validates:
   - SDK installed and version >= 10.4.6408
   - OpenD reachable on configured host:port
   - Environment check cached or first-time full validation
4. Script calls OpenD method: `ctx.get_market_snapshot([codes])` (remote API call via WebSocket)
5. OpenD returns DataFrame with snapshot data (OHLC, volume, bid/ask, etc.)
6. Script parses rows, formats output (table or JSON), prints result
7. Context closes: `safe_close(ctx)` (cleanup)

Example: `get_snapshot.py`
- Entry: `scripts/quote/get_snapshot.py` (line 26-100)
- Context creation: `create_quote_context()` from `common.py` (line 63-66)
- API call: `ctx.get_market_snapshot(batch)` (line 72)
- Response parsing: `_parse_snapshot_row(row)` (line 45-60)
- Output: JSON or formatted table (line 86-96)

### Trading Order Path

1. User invokes: `python scripts/trade/place_order.py --code US.AAPL --side BUY --quantity 100 --price 150 --confirmed` (entry point)
2. Script imports and calls `create_trade_context()` from `common.py`
3. Context validates:
   - Same SDK/OpenD checks as quote path
   - Trading environment is SIMULATE by default (paper trading) or REAL if explicitly requested
4. Script parses order parameters (code, side, quantity, price, order type, etc.)
5. Script performs market inference from code prefix (e.g., `US.` → US market)
6. Script calls OpenD: `trd_ctx.place_order(order_dict)` (remote execution)
7. OpenD returns order confirmation with order ID, filled quantity, etc.
8. Script formats response and displays to user
9. Trade audit log appended to `~/.futu_trade_audit.jsonl` for compliance tracking

Example: `place_order.py`
- Entry: `scripts/trade/place_order.py` (line 30-52)
- Context creation: `create_trade_context()` from `common.py`
- Market inference: `infer_market_from_code(code)` (line 42)
- Order placement: `trd_ctx.place_order(...)` (line 120+)
- Audit logging: `_audit_log(entry)` (line 64-73)

### Subscription Path (Real-Time Pushes)

1. User invokes: `python scripts/subscribe/subscribe.py US.AAPL --push-quote` (entry point)
2. Script creates quote context and subscribes: `ctx.subscribe([codes], [push_types])`
3. OpenD establishes push channel (WebSocket) back to script
4. Script spawns push handler (e.g., `push_quote.py`) in separate process/thread
5. Handler calls: `ctx.start()` to begin receiving asynchronous pushes
6. For each push event, handler's callback method is invoked with new data
7. Handler formats and prints received data in real-time
8. User terminates with Ctrl+C; script calls `unsubscribe()` to clean up

Example: `push_quote.py`
- Entry: `scripts/subscribe/push_quote.py`
- Handler class: `QuotePushHandler` (extends `QuoteHandlerBase`)
- Subscription loop: `ctx.start()` (blocking until termination)
- Callback: `on_push_quote(quote_data)` (invoked by OpenD for each push)

**State Management:**
- **Configuration**: Environment variables read once at script startup via `get_config()` and stored in `FutuConfig` dataclass
- **Contexts**: Mutable OpenD contexts (quote/trade) created per script invocation, scoped to that execution
- **Caching**: Environment check status cached to temp file with 1-hour TTL to avoid repeated validation
- **Audit trail**: Trade orders logged to `~/.futu_trade_audit.jsonl` for compliance and debugging

## Key Abstractions

**OpenD Context:**
- Purpose: Manages WebSocket connection to OpenD service and provides typed SDK API
- Examples: `OpenQuoteContext`, `OpenSecTradeContext`, `OpenCryptoTradeContext`
- Pattern: Factory function pattern (`create_quote_context()`, `create_trade_context()`) returning context-manager objects

**FutuConfig:**
- Purpose: Centralized configuration dataclass mapping environment variables to trading parameters
- Examples: `opend_host`, `opend_port`, `trd_env`, `default_market`, `security_firm`
- Pattern: Environment variable reader with typed defaults (dataclass)

**Response Parsing:**
- Purpose: Convert OpenD DataFrames/dicts to structured output
- Examples: `_parse_snapshot_row()`, `_parse_order_row()`, safe accessors (`safe_get`, `safe_float`, `safe_int`)
- Pattern: Row-by-row parsing with null-safety and type coercion

**Enum Conversion:**
- Purpose: Map string inputs to SDK enum values
- Examples: `parse_trd_env()`, `parse_trd_side()`, `parse_security_firm()`, `infer_market_from_code()`
- Pattern: String-to-enum mapping utilities

**Dependency Checking:**
- Purpose: Validate SDK version, OpenD connectivity, installation status
- Examples: `_check_sdk()`, `_check_opend_reachable()`, `_check_version_stamp()`
- Pattern: Standalone checker functions with fallback/skip mechanisms

## Entry Points

**Quote Script Entry:**
- Location: `scripts/quote/get_snapshot.py` (and 54 others)
- Triggers: User command-line invocation, typically for market data queries
- Responsibilities: Parse stock codes, create quote context, fetch data from OpenD, format output

**Trade Script Entry:**
- Location: `scripts/trade/place_order.py` (and 19 others)
- Triggers: User command-line invocation for trading operations
- Responsibilities: Parse order parameters, validate account/market, execute via OpenD, log audit trail

**Subscription Entry:**
- Location: `scripts/subscribe/subscribe.py`
- Triggers: User starts real-time subscription service
- Responsibilities: Subscribe to market data streams, launch push handlers, manage lifecycle

**Installer Entry:**
- Location: Invoked via `/install-moomoo-opend` command by AI agent
- Triggers: User requests OpenD/SDK installation
- Responsibilities: Detect OS, download installer, run platform-specific installation flow, upgrade SDK

## Architectural Constraints

- **Threading:** All scripts are single-threaded except subscription handlers which may spawn background processes. No explicit multi-threading primitives in the codebase; OpenD handles concurrent connections via WebSocket.
- **Global state:** Configuration is immutable per script (read once from environment variables via `get_config()`). No module-level mutable singletons. Environment check result cached to temp file but read-only per process.
- **Circular imports:** None detected. Import structure is linear: scripts → common.py → moomoo SDK.
- **Market Inference:** Hard constraint at code level — market is always inferred from stock code prefix (e.g., `US.`, `HK.`, `CC.`), regardless of `--market` parameter. Conflicting input generates warning but does not block execution.
- **Code Format Validation:** Hard constraint — stock codes must contain `.` separator and prefix from `{US, HK, SH, SZ, SG, CC}`. Invalid format exits with error.
- **Default Environment:** Paper trading (`TrdEnv.SIMULATE`) is the default for safety. Live trading (`TrdEnv.REAL`) requires explicit `--trd-env REAL` parameter.
- **Trade Password Unlock:** Security constraint — trade unlock is **forbidden via SDK code**. Must be done manually in OpenD GUI. Any attempt to call `unlock_trade()` is rejected.

## Anti-Patterns

### Hardcoded Credentials in Scripts

**What happens:** Some older scripts may contain inline credentials or environment variable names as literals instead of using the `get_config()` factory.

**Why it's wrong:** Credentials may leak if script is committed with defaults; environment variable names become inflexible if OpenD configuration changes.

**Do this instead:** Always use `get_config()` from `common.py` (`scripts/common.py` line 43-68) to read configuration once at startup. Use the returned `FutuConfig` object to access host, port, trading environment, and other settings.

### Duplicated Context Creation Logic

**What happens:** Some scripts may create OpenD context directly with hardcoded parameters instead of using factory functions.

**Why it's wrong:** Changes to connection logic (e.g., new authentication scheme, different default parameters) require updating all scripts; easier to introduce bugs.

**Do this instead:** Use `create_quote_context()` and `create_trade_context()` from `common.py` (lines 200+). These factories handle dependency checks, version validation, and connection setup centrally.

### Missing Error Handling for OpenD Failures

**What happens:** Script calls `ctx.method()` and assumes success without checking the return code.

**Why it's wrong:** Network timeouts, API errors, or permission issues go unhandled; user sees confusing stack traces instead of actionable error messages.

**Do this instead:** Always call `check_ret(ret, data, ctx, "operation name")` from `common.py` after each API call (example: `scripts/quote/get_snapshot.py` line 72-73). This function prints formatted error messages and exits cleanly on failure.

## Error Handling

**Strategy:** Fail-fast with informative messages

**Patterns:**
- Environment validation: Run dependency checks on first script invocation. Cache result for 1 hour to avoid repeated checks. Prompt user to run `/install-moomoo-opend` if checks fail.
- API errors: Call `check_ret(ret, data, ctx, description)` after each OpenD API call. Prints error code, error message, and context before exiting.
- JSON output: When `--json` flag is used, output structured error object with `{"error": "message"}` instead of printing to stderr.
- Audit trail: Trade operations logged to `~/.futu_trade_audit.jsonl` for compliance. Logging failures are silently ignored (don't block the trade).

## Cross-Cutting Concerns

**Logging:** 
- stderr for warnings and environment checks (marked with `[WARN]`)
- stdout for normal output (table or JSON)
- Trade audit log written to `~/.futu_trade_audit.jsonl` (JSON lines format, one entry per order)

**Validation:**
- Code format: Must contain `.` and start with market prefix from `{US, HK, SH, SZ, SG, CC}`
- Environment: SDK version >= 10.4.6408 required; crypto operations require >= 10.5.6508
- Market inference: Automatic from code prefix; conflicts logged as warning but not blocking

**Authentication:**
- Trade unlock manually in OpenD GUI (security constraint, no SDK-based unlock)
- Login credentials optional; default account selected automatically if present
- Paper trading (default) requires no credentials; live trading requires unlock in GUI

---

*Architecture analysis: 2026-06-23*
