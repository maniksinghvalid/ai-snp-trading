<!-- GSD:project-start source:PROJECT.md -->

## Project

**AI S&P Trading Bot (Trend Join Long)**

A fully automated paper-trading system that trades a single, well-defined day-trading
strategy ("Trend Join Long") on S&P 500 stocks through the Moomoo/Futu OpenAPI. It scans
premarket for setups, enters and manages intraday positions on a 5-minute timeframe,
enforces risk rules automatically, sends Telegram alerts, and runs hands-off on a schedule —
no manual operation by a human. It also includes a backtester to validate the strategy on
historical data. Built for the operator (the project owner) running it on their own machine
against a Moomoo paper (SIMULATE) account.

**Core Value:** The bot autonomously executes the Trend Join Long strategy end-to-end on a paper account —
scan, enter, manage risk, exit, and report — correctly and unattended, exactly as specified.
If everything else is stripped away, a correct and safe automated trade loop is what must work.

### Constraints

- **Tech stack**: Python 3.6+ — must reuse the existing `moomoo-api` SDK and `skills/moomooapi` client (no rewrite of broker access). `yfinance` is added as a read-only market-data source for scanning and backtest history only (not broker access).
- **Data/execution split**: yfinance supplies scan + backtest daily-bar data (avoids broker quota); Moomoo/OpenD owns order execution, account/position truth, and live intraday 5m subscriptions on the (top-20-capped) watchlist.
- **Strategy config**: All strategy parameters live in a single `rules.json` (the source of truth) read by both the live bot and the backtester.
- **Dependency**: Requires OpenD GUI running and logged in on `127.0.0.1:11111`; the bot is non-functional without it.
- **Safety**: Paper trading only (`FUTU_TRD_ENV=SIMULATE`); no real-money order path in this milestone.
- **Timezone**: All strategy timing is US Eastern (ET); the bot must handle ET/market-session correctness regardless of host timezone.
- **Market hours**: Operates only on US market trading days; must respect holidays/half-days.
- **Position sizing basis**: Assume $100,000 starting paper equity for risk math.

<!-- GSD:project-end -->

## Phase 8 — Options bot (`tasty_credit_spreads`)

- Run: `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json` (equity bot: `python3 -m bot`, unchanged). ONE options-bot instance at a time; may run alongside the equity bot.
- Config: `rules_options.json` (single source of truth; schema in `bot/options/schema.py`). Code: `bot/options/{strategy,execution,store,service}.py`; gateway option reads in `bot/gateway/gateway.py` (`get_stock_ids`, `screen_options`, `get_option_positions`).
- Live UAT probe: `python3 scripts/uat_options_probe.py` (read-only; run during RTH) and `--live-1lot --confirm` (operator-run, places one paper spread).
- Research provenance: `docs/research/2026-08-17-tastylive-options-research.md`; design: `~/.claude/plans/scrape-highly-rated-options-velvety-naur.md`; phase summary: `.planning/phases/08-options-premium-selling/08-SUMMARY.md`.
- Invariants: LIMIT orders only; long wings before shorts on open, shorts first on close; the options bot never touches broker option codes not in its own `option_legs`; separate DB/kill-file/report-dir from the equity bot.

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.x - Market data queries, order placement, portfolio management, and real-time subscription handling in `skills/moomooapi/scripts/` and `skills/install-moomoo-opend/scripts/`

## Runtime

- Python 3.6+ (tested with modern 3.x)
- pip / pip3
- Package name: `moomoo-api`

## Frameworks

- moomoo-api >= 10.4.6408 - Python SDK for Futu OpenAPI providing quote and trading contexts
- OpenQuoteContext - Quote/market data access via `create_quote_context()` in `skills/moomooapi/scripts/common.py`
- OpenSecTradeContext - Securities trading (stocks, options, futures) via `create_trade_context()` in `skills/moomooapi/scripts/common.py`
- OpenCryptoTradeContext - Cryptocurrency trading (BTC, ETH) via `create_crypto_trade_context()` in `skills/moomooapi/scripts/common.py`
- pandas - DataFrame operations in quote/trading scripts for handling market snapshots, candlesticks, order books
- argparse - CLI parameter parsing in all Python scripts
- json - JSON output formatting
- socket - OpenD connectivity checking in `common.py`

## Key Dependencies

- moomoo-api [version: >=10.4.6408, <11.0] - Why it matters: Futu OpenAPI SDK; all market data, quote subscriptions, and trading operations depend on this
- OpenD (separate binary service) [version: >=10.4.6408] - Why it matters: Required daemon that serves the API; scripts fail if not running on 127.0.0.1:11111
- pandas - Data frame manipulation for market data responses
- socket library - TCP connectivity validation to OpenD

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `FUTU_LOGIN_ACCOUNT` | Login account/email | "" (empty) |
| `FUTU_LOGIN_PWD` | Login password | "" (empty) |
| `FUTU_OPEND_HOST` | OpenD server host | "127.0.0.1" |
| `FUTU_OPEND_PORT` | OpenD server port | "11111" |
| `FUTU_TRD_ENV` | Trading environment | "SIMULATE" (paper trading by default) |
| `FUTU_DEFAULT_MARKET` | Default market | "NONE" |
| `FUTU_SECURITY_FIRM` | Security firm (FUTUSECURITIES, FUTUINC, FUTUSG, etc.) | "" (auto-detect) |
| `FUTU_ACC_ID` | Default account ID | "0" |

- Environment check cache: `~/.moomoo_skill_version` - Version stamp file written after `/install-moomoo-opend` runs
- Temp cache: `/tmp/.moomoo_env_ok` - Environment check result (1-hour TTL)
- Crypto firm cache: `/tmp/.moomoo_crypto_firm` - Auto-detected crypto trading firm (1-hour TTL)
- Trade audit log: `~/.futu_trade_audit.jsonl` - Append-only JSONL log of all placed orders
- Default address: `127.0.0.1:11111` (configurable via FUTU_OPEND_HOST and FUTU_OPEND_PORT)
- Protocol: WebSocket over localhost
- Port connectivity checked via socket.connect() before API operations

## Platform Requirements

- macOS / Linux / Windows (OpenD GUI version available for all three)
- Python 3.6+
- moomoo account (login required in OpenD GUI)
- OpenD service running (GUI version required, command-line version not supported)
- Python 3.6+
- Network access to OpenD on 127.0.0.1:11111 (or custom host:port)
- Local desktop/laptop running OpenD GUI
- OpenD can be configured to listen on 0.0.0.0 for LAN access (not Internet-exposed recommended)

## Version Constraints

| Feature | Minimum Version | Notes |
|---------|-----------------|-------|
| Basic trading & quotes | 10.4.6408 | Minimum supported version |
| Crypto trading | 10.5.6508 | First version with OpenCryptoTradeContext |
| AI-type parameter | 10.4.6408 | SDK parameter support detection in `common.py` |

- OpenD version must match or exceed SDK version (both use same X.Y.ZZZZ versioning)
- Version detection: `~/.moomoo_skill_version` stamp file (written by `/install-moomoo-opend` skill)
- Mismatch warning: Scripts warn if detected version != installed version

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- Scripts use lowercase with underscores: `get_ticker.py`, `place_order.py`, `common.py`, `check_env.py`
- Organized by category directory: `scripts/quote/`, `scripts/trade/`
- Common shared module always named: `common.py`
- Public functions use snake_case: `create_quote_context()`, `place_order()`, `parse_market()`
- Private/internal functions prefix with underscore: `_ensure_utf8_io()`, `_check_opend_alive()`, `_env_check_is_cached()`
- Helper functions follow pattern: `get_*()`, `parse_*()`, `create_*()`, `check_*()`, `is_*()`, `safe_*()`, `format_*()`, `infer_*()`
- Local variables use snake_case: `trd_market`, `acc_id`, `output_json`, `ret`, `data`
- Configuration/constant collections use UPPERCASE_WITH_UNDERSCORES: `KTYPE_MAP`, `MARKET_MAP`, `SORT_MAP`, `ORDER_SESSION_MAP`
- Module-level constants in UPPERCASE: `MIN_SDK_VERSION`, `SKILL_VERSION`, `SDK_MODULE_NAME`, `_ENV_CHECK_TTL`
- Private module-level constants prefix with underscore: `_CODE_PREFIX_TO_MARKET`, `_MARKET_NAMES`
- Temporary files/paths use conventional prefixes: `_CRYPTO_FIRM_CACHE_FILE`, `_ENV_CHECK_CACHE_FILE`, `STAMP_FILE`
- Dataclasses use PascalCase: `FutuConfig` (defined in `common.py` line 27)
- Enum types imported from moomoo SDK, used as-is: `TrdEnv`, `TrdMarket`, `OrderType`, `SubType`, `Market`, `Session`, etc.
- Function parameters use snake_case with common abbreviations: `code`, `qty`, `price`, `trd_env`, `trd_market`, `acc_id`, `security_firm`
- Boolean parameters suffixed with `_json` or use prefix pattern: `output_json`, `refresh_cache`, `fill_outside_rth`, `confirmed`
- String choice parameters often have `_str` suffix: `session_str`, `ktype`, `rehab`

## Code Style

- Python 3 shebang on all scripts: `#!/usr/bin/env python3`
- Line length appears reasonable (80-100 character range, no strict enforcement detected)
- Indentation: 4 spaces (Python standard)
- No type hints on most functions, except in `FutuConfig` dataclass which uses annotations
- No `.pylintrc`, `.flake8`, or linting configuration detected
- No mandatory linting enforced in codebase
- UTF-8 encoding explicitly handled on Windows (see `_ensure_utf8_io()` in `common.py` lines 71-85)

## Import Organization

- `os` imported as `_os` in scripts to avoid naming conflicts with local `os` module reference
- No other major path aliases or `from __future__` imports used

## Error Handling

- API calls check return code via `check_ret(ret, data, ctx, action_str)` function (defined in `common.py`)
- All business functions wrapped in try/except blocks with generic `Exception` catch
- Exit codes used: `sys.exit(0)` for success, `sys.exit(1)` for errors
- Error messages printed to stdout (plain text) or stderr (for warnings) with `[ERROR]` or `[WARN]` prefixes
- No custom exception classes; all errors raised as built-ins or SDK errors
- Special error message detection functions: `_is_no_account_error()`, `_is_unlock_needed_error()`, `_is_permission_error()` (in `common.py`)
- Provides contextual hints to users for specific error conditions (e.g., quota exhaustion, unlock required)

## Logging

- All scripts support `--json` flag for structured JSON output
- When `--json` is present, output single JSON object to stdout; otherwise, formatted text
- Warnings printed to stderr with `[WARN]` prefix via `print(..., file=sys.stderr)`
- Errors printed to stderr with `[ERROR]` prefix via `print(..., file=sys.stderr)`
- Success/data printed to stdout

## Comments

- Module-level docstrings on all files (3-6 lines, describe function + usage example)
- Function docstrings on all public and most private functions
- Inline comments for complex logic, version constraints, or non-obvious behavior
- Section headers as comment blocks with `# ============================================================`
- Not used; pure Python docstrings in triple-quoted format
- Docstrings describe function purpose, parameters, and return values inline
- No structured annotation format (e.g., `:param:`, `:return:`)

## Function Design

- Most functions accept 3-7 parameters
- Optional parameters use `None` as default, then resolved via `or` operator: `acc_id = acc_id or get_default_acc_id()`
- Boolean flags typically at end: `output_json=False`, `confirmed=False`, `refresh_cache=False`
- Script entry functions return `None` (side effects via print/exit)
- Helper functions return single values or tuples: `(ret, data)` matching SDK pattern
- Use early returns for validation/error cases

## Module Design

- `common.py` is the only shared module; exports 30+ helper/utility functions
- Scripts are standalone (no inter-script imports)
- Each script in `scripts/quote/` or `scripts/trade/` can be executed independently
- No barrel exports or `__init__.py` files
- Direct imports from `common` using explicit names: `from common import (create_quote_context, ...)`
- 30+ independent quote scripts (one API call per script)
- Scripts follow naming pattern: `get_*.py` for query operations
- Trade scripts in separate `scripts/trade/` directory

## Data Processing

- All dataframe iterations use explicit loop pattern (lines 55-63 in `get_ticker.py`):
- No pandas method chaining or vectorized operations
- Compatibility check `hasattr(data, "iloc")` for DataFrame vs list handling
- `safe_get(row, *keys, default="")` — handles missing fields with fallbacks
- `safe_float(val, default=0.0)` — parse with default for N/A, empty, None
- `safe_int(val, default=0)` — parse with numpy scalar support for precision
- `to_jsonable(val, default=None)` — convert to JSON-serializable types (line 776)
- All JSON output uses `ensure_ascii=False` for Unicode support
- Custom helper `to_jsonable()` handles NaN/Inf/enum values
- `df_to_records(df, limit=None)` converts DataFrame to list of dicts (line 792)

## Configuration & Defaults

- `FUTU_LOGIN_ACCOUNT` — Login account
- `FUTU_LOGIN_PWD` — Login password
- `FUTU_OPEND_HOST` — OpenD server (default: 127.0.0.1)
- `FUTU_OPEND_PORT` — OpenD port (default: 11111)
- `FUTU_TRD_ENV` — Trading environment (default: SIMULATE)
- `FUTU_DEFAULT_MARKET` — Default market (default: NONE)
- `FUTU_SECURITY_FIRM` — Default security firm
- `FUTU_ACC_ID` — Default account ID
- Also supports `MOOMOO_*` prefix variants
- Environment check cached for 1 hour (`_ENV_CHECK_TTL = 3600`)
- Crypto firm auto-detect cached for 1 hour (`_CRYPTO_FIRM_CACHE_TTL`)
- Temp files in system temp directory: `/tmp/.moomoo_env_ok`, `/tmp/.moomoo_crypto_firm`

## Argument Parsing

- Positional arguments for required inputs (`code`)
- Dash-separated long options (`--market`, `--trd-env`, `--acc-id`)
- `--json` flag for JSON output support
- Boolean flags use `action="store_true"` with descriptive dest names
- All scripts are executable as main entry points

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

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

- **Modular scripts**: Each market operation or trade action is a standalone Python script in `quote/`, `trade/`, or `subscribe/` subdirectories
- **Shared utilities**: `common.py` provides configuration, context management, and reusable helpers
- **OpenD broker pattern**: All operations delegate to a locally-running OpenD service (WebSocket-based, default `127.0.0.1:11111`)
- **Environment-driven configuration**: FutuConfig reads from environment variables (FUTU_OPEND_HOST, FUTU_TRD_ENV, etc.)
- **Error handling and version checks**: Built-in dependency detection and automatic environment validation

## Layers

- Purpose: User-facing CLI entry points that parse arguments and format output (table or JSON)
- Location: `scripts/quote/*.py`, `scripts/trade/*.py`, `scripts/subscribe/*.py`
- Contains: Argument parsing, output formatting, business logic orchestration
- Depends on: `common.py` for context creation and utilities
- Used by: End users via command-line or AI agents
- Purpose: Centralized configuration, context management, and reusable utilities
- Location: `scripts/common.py`
- Contains: 
- Depends on: moomoo SDK, environment variables
- Used by: All quote, trade, and subscribe scripts
- Purpose: Broker service handling all protocol communication with moomoo API
- Location: External service running on `127.0.0.1:11111` (configurable)
- Contains: WebSocket/protobuf protocol handling, market data subscriptions, order execution
- Depends on: moomoo server backend
- Used by: All scripts via SDK context
- Purpose: Download, extract, and install OpenD and Python SDK
- Location: `scripts/install_*.md` (platform-specific), `scripts/detect_version.md`, `scripts/verify_version.md`
- Contains: OS detection, version comparison, download fallback logic, installation workflows
- Depends on: curl/PowerShell, Python package manager (pip)
- Used by: AI agent during initial setup

## Data Flow

### Primary Request Path (Market Data Query)

- Entry: `scripts/quote/get_snapshot.py` (line 26-100)
- Context creation: `create_quote_context()` from `common.py` (line 63-66)
- API call: `ctx.get_market_snapshot(batch)` (line 72)
- Response parsing: `_parse_snapshot_row(row)` (line 45-60)
- Output: JSON or formatted table (line 86-96)

### Trading Order Path

- Entry: `scripts/trade/place_order.py` (line 30-52)
- Context creation: `create_trade_context()` from `common.py`
- Market inference: `infer_market_from_code(code)` (line 42)
- Order placement: `trd_ctx.place_order(...)` (line 120+)
- Audit logging: `_audit_log(entry)` (line 64-73)

### Subscription Path (Real-Time Pushes)

- Entry: `scripts/subscribe/push_quote.py`
- Handler class: `QuotePushHandler` (extends `QuoteHandlerBase`)
- Subscription loop: `ctx.start()` (blocking until termination)
- Callback: `on_push_quote(quote_data)` (invoked by OpenD for each push)
- **Configuration**: Environment variables read once at script startup via `get_config()` and stored in `FutuConfig` dataclass
- **Contexts**: Mutable OpenD contexts (quote/trade) created per script invocation, scoped to that execution
- **Caching**: Environment check status cached to temp file with 1-hour TTL to avoid repeated validation
- **Audit trail**: Trade orders logged to `~/.futu_trade_audit.jsonl` for compliance and debugging

## Key Abstractions

- Purpose: Manages WebSocket connection to OpenD service and provides typed SDK API
- Examples: `OpenQuoteContext`, `OpenSecTradeContext`, `OpenCryptoTradeContext`
- Pattern: Factory function pattern (`create_quote_context()`, `create_trade_context()`) returning context-manager objects
- Purpose: Centralized configuration dataclass mapping environment variables to trading parameters
- Examples: `opend_host`, `opend_port`, `trd_env`, `default_market`, `security_firm`
- Pattern: Environment variable reader with typed defaults (dataclass)
- Purpose: Convert OpenD DataFrames/dicts to structured output
- Examples: `_parse_snapshot_row()`, `_parse_order_row()`, safe accessors (`safe_get`, `safe_float`, `safe_int`)
- Pattern: Row-by-row parsing with null-safety and type coercion
- Purpose: Map string inputs to SDK enum values
- Examples: `parse_trd_env()`, `parse_trd_side()`, `parse_security_firm()`, `infer_market_from_code()`
- Pattern: String-to-enum mapping utilities
- Purpose: Validate SDK version, OpenD connectivity, installation status
- Examples: `_check_sdk()`, `_check_opend_reachable()`, `_check_version_stamp()`
- Pattern: Standalone checker functions with fallback/skip mechanisms

## Entry Points

- Location: `scripts/quote/get_snapshot.py` (and 54 others)
- Triggers: User command-line invocation, typically for market data queries
- Responsibilities: Parse stock codes, create quote context, fetch data from OpenD, format output
- Location: `scripts/trade/place_order.py` (and 19 others)
- Triggers: User command-line invocation for trading operations
- Responsibilities: Parse order parameters, validate account/market, execute via OpenD, log audit trail
- Location: `scripts/subscribe/subscribe.py`
- Triggers: User starts real-time subscription service
- Responsibilities: Subscribe to market data streams, launch push handlers, manage lifecycle
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

### Duplicated Context Creation Logic

### Missing Error Handling for OpenD Failures

## Error Handling

- Environment validation: Run dependency checks on first script invocation. Cache result for 1 hour to avoid repeated checks. Prompt user to run `/install-moomoo-opend` if checks fail.
- API errors: Call `check_ret(ret, data, ctx, description)` after each OpenD API call. Prints error code, error message, and context before exiting.
- JSON output: When `--json` flag is used, output structured error object with `{"error": "message"}` instead of printing to stderr.
- Audit trail: Trade operations logged to `~/.futu_trade_audit.jsonl` for compliance. Logging failures are silently ignored (don't block the trade).

## Cross-Cutting Concerns

- stderr for warnings and environment checks (marked with `[WARN]`)
- stdout for normal output (table or JSON)
- Trade audit log written to `~/.futu_trade_audit.jsonl` (JSON lines format, one entry per order)
- Code format: Must contain `.` and start with market prefix from `{US, HK, SH, SZ, SG, CC}`
- Environment: SDK version >= 10.4.6408 required; crypto operations require >= 10.5.6508
- Market inference: Automatic from code prefix; conflicts logged as warning but not blocking
- Trade unlock manually in OpenD GUI (security constraint, no SDK-based unlock)
- Login credentials optional; default account selected automatically if present
- Paper trading (default) requires no credentials; live trading requires unlock in GUI

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
