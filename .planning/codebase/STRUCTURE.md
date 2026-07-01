# Codebase Structure

**Analysis Date:** 2026-06-23

## Directory Layout

```
skills/
├── moomooapi/                          # moomoo OpenAPI trading & market data skill
│   ├── SKILL.md                        # Skill definition, API reference, usage rules
│   ├── docs/                           # Documentation
│   │   ├── API_LIMITS.md               # Rate limits and constraints
│   │   ├── API_REFERENCE.md            # API method reference
│   │   ├── FIELD_MAPPING.md            # Field names and meanings
│   │   ├── FUTURES_TRADING.md          # Futures-specific guidance
│   │   └── TROUBLESHOOTING.md          # Common issues and solutions
│   └── scripts/                        # Python scripts (96 files)
│       ├── common.py                   # Shared utilities & configuration
│       ├── check_env.py                # Environment pre-check script
│       ├── quote/                      # Market data & research scripts (55 files)
│       │   ├── get_snapshot.py         # Market snapshot (OHLC, volume, bid/ask)
│       │   ├── get_kline.py            # Candlestick data (real-time/historical)
│       │   ├── get_stock_quote.py      # Real-time quotes for subscribed stocks
│       │   ├── get_orderbook.py        # Order book / depth
│       │   ├── get_ticker.py           # Tick-by-tick trades
│       │   ├── get_broker_queue.py     # Broker bid/ask queue
│       │   ├── get_rt_data.py          # Time-sharing data (intraday)
│       │   ├── get_plate_list.py       # Plate/sector list
│       │   ├── get_plate_stock.py      # Plate constituents & index constituents
│       │   ├── get_stock_filter.py     # Stock screener V1 (legacy)
│       │   ├── get_stock_screen.py     # Stock screener V2 (advanced factors)
│       │   ├── get_warrant_screen.py   # Warrant/CBBC screener
│       │   ├── get_option_*.py         # Option data (chain, expiry, volatility, etc.)
│       │   ├── resolve_option_code.py  # Parse option shorthand → moomoo code
│       │   ├── get_financials_*.py     # Financials (earnings, statements, revenue)
│       │   ├── get_research_*.py       # Analyst ratings, Morningstar reports
│       │   ├── get_valuation_*.py      # Valuation metrics & history
│       │   ├── get_corporate_*.py      # Corporate actions (dividends, splits, buybacks)
│       │   ├── get_shareholders_*.py   # Shareholder data & holdings
│       │   ├── get_insider_*.py        # Insider trading & holder list
│       │   ├── get_company_*.py        # Company profile, executives, background
│       │   ├── get_daily_short_volume.py # Short volume analysis
│       │   ├── get_short_interest.py   # Short interest data
│       │   ├── get_capital_*.py        # Capital flow & distribution
│       │   ├── get_ipo_list.py         # IPO list
│       │   ├── modify_user_security.py # Watchlist management
│       │   ├── get_user_*.py           # User permission & watchlist data
│       │   └── [30+ more research scripts]
│       ├── trade/                      # Trading & account scripts (20 files)
│       │   ├── get_accounts.py         # Account list & types
│       │   ├── get_portfolio.py        # Positions & funds for one account
│       │   ├── get_all_portfolios.py   # Positions & funds across all accounts
│       │   ├── place_order.py          # Place buy/sell order
│       │   ├── modify_order.py         # Modify pending order
│       │   ├── cancel_order.py         # Cancel order
│       │   ├── get_orders.py           # Today's orders
│       │   ├── get_history_orders.py   # Historical orders
│       │   ├── get_order_fill_list.py  # Today's fills/executions
│       │   ├── get_history_order_fill_list.py # Historical fills
│       │   ├── get_acc_cash_flow.py    # Cash flow records
│       │   ├── get_order_fee.py        # Order commission/fees
│       │   ├── get_margin_ratio.py     # Margin ratio
│       │   ├── get_max_trd_qtys.py     # Max tradeable quantities
│       │   ├── get_crypto_*.py         # Crypto trading (6 files)
│       │   │   ├── get_crypto_accounts.py
│       │   │   ├── get_crypto_portfolio.py
│       │   │   ├── place_crypto_order.py
│       │   │   ├── cancel_crypto_order.py
│       │   │   ├── get_crypto_orders.py
│       │   │   ├── get_crypto_cash_flow.py
│       │   │   └── [2 more]
│       │   └── [4 more standard trading scripts]
│       └── subscribe/                  # Real-time push handlers (6 files)
│           ├── subscribe.py            # Subscribe to market data types
│           ├── unsubscribe.py          # Unsubscribe from data types
│           ├── unsubscribe_all.py      # Unsubscribe all
│           ├── query_subscription.py   # Check subscription status
│           ├── push_quote.py           # Receive & display quote pushes
│           ├── push_kline.py           # Receive candlestick pushes
│           ├── push_broker.py          # Receive broker queue pushes
│           ├── push_orderbook.py       # Receive order book pushes
│           ├── push_ticker.py          # Receive tick-by-tick pushes
│           └── push_rt_data.py         # Receive time-sharing data pushes
│
└── install-moomoo-opend/               # OpenD installation assistant skill
    ├── SKILL.md                        # Installation workflow & OS detection logic
    └── scripts/                        # Platform-specific installation guides
        ├── detect_version.md           # Version detection & comparison logic
        ├── verify_version.md           # Post-extraction version validation
        ├── install_win.md              # Windows installation steps (PowerShell)
        ├── install_mac.md              # macOS installation steps (DMG mount)
        └── install_linux.md            # Linux installation steps (dpkg/rpm)
```

## Directory Purposes

**moomooapi/SKILL.md:**
- Purpose: Skill entry point and comprehensive documentation
- Contains: Skill metadata, language rules, API reference, security rules, stock code formats, script directory listing, market data commands, trading commands, research commands
- Key sections: Prerequisites, OpenD launch logic, paper vs live trading, stock code formats, script path lookup rules

**moomooapi/docs/:**
- Purpose: Supplementary technical documentation
- `API_LIMITS.md`: Rate limits (60 requests/30s, max 400 codes/request), per-account throttling, quote subscription limits
- `API_REFERENCE.md`: API method reference with parameters and return types
- `FIELD_MAPPING.md`: Meaning of DataFrame columns (bid_ask_ratio, turnover_rate, pe_ratio, etc.)
- `FUTURES_TRADING.md`: Futures-specific guidance (contract codes, continuous contracts, margin rules)
- `TROUBLESHOOTING.md`: Common errors and solutions

**moomooapi/scripts/common.py:**
- Purpose: Shared utilities module imported by all scripts
- Contains: 
  - `FutuConfig` dataclass: Configuration mapping from environment variables
  - `get_config()`: Factory to create config from environment
  - `create_quote_context()` / `create_trade_context()`: OpenD context factories
  - Dependency checking: `_check_sdk()`, `_check_opend_reachable()`, `_check_version_stamp()`
  - Safe accessors: `safe_get()`, `safe_float()`, `safe_int()`, `is_empty()`
  - Error handling: `check_ret()`, `safe_close()`, `format_enum()`
  - Enum parsing: `parse_trd_env()`, `parse_trd_side()`, `parse_security_firm()`, `infer_market_from_code()`
  - Version parsing and caching logic

**moomooapi/scripts/check_env.py:**
- Purpose: Standalone environment pre-check script
- Contains: SDK installation check, OpenD connectivity test
- Output: JSON or human-readable status report

**moomooapi/scripts/quote/ (55 scripts):**
- Purpose: Market data and financial research queries
- Categories:
  - **Real-time quotes**: `get_snapshot.py`, `get_stock_quote.py`, `get_orderbook.py`, `get_ticker.py`, `get_rt_data.py`, `get_broker_queue.py`
  - **Historical data**: `get_kline.py` (candlesticks), `get_history_kl_quota.py`
  - **Screening**: `get_stock_filter.py` (V1), `get_stock_screen.py` (V2), `get_warrant_screen.py`, `get_option_screen.py`
  - **Options**: `get_option_expiration_date.py`, `get_option_chain.py`, `resolve_option_code.py`, `get_option_volatility.py`, `get_option_exercise_probability.py`
  - **Fundamentals**: `get_financials_*.py` (5 scripts) — earnings, statements, revenue
  - **Research**: `get_research_*.py` (3 scripts) — analyst consensus, ratings, Morningstar
  - **Valuation**: `get_valuation_*.py` (2 scripts) — valuation detail and plate stock list
  - **Corporate actions**: `get_corporate_actions_*.py` (3 scripts) — dividends, buybacks, splits
  - **Shareholders**: `get_shareholders_*.py` (3 scripts) — overview, holdings, changes
  - **Insiders**: `get_insider_*.py` (2 scripts) — holder list, trade list
  - **Company info**: `get_company_*.py` (3 scripts) — profile, executives, background, efficiency
  - **Other**: `get_plate_*.py` (2 scripts), `get_owner_plate.py`, `get_referencestock_list.py`, `get_warrant.py`, `get_stock_info.py`, `get_ipo_list.py`, `get_market_state.py`, `get_global_state.py`, `get_capital_*.py` (2 scripts), `get_daily_short_volume.py`, `get_short_interest.py`, `get_user_*.py` (3 scripts), `modify_user_security.py`, `get_price_reminder.py`, `set_price_reminder.py`

**moomooapi/scripts/trade/ (20 scripts):**
- Purpose: Trading operations and account management
- Categories:
  - **Account & portfolio**: `get_accounts.py`, `get_portfolio.py`, `get_all_portfolios.py`
  - **Order management**: `place_order.py`, `modify_order.py`, `cancel_order.py`
  - **Order queries**: `get_orders.py` (today), `get_history_orders.py`, `get_order_fill_list.py` (today), `get_history_order_fill_list.py`
  - **Account data**: `get_acc_cash_flow.py`, `get_order_fee.py`, `get_margin_ratio.py`, `get_max_trd_qtys.py`
  - **Crypto trading** (6 scripts): `get_crypto_accounts.py`, `get_crypto_portfolio.py`, `place_crypto_order.py`, `cancel_crypto_order.py`, `get_crypto_orders.py`, `get_crypto_cash_flow.py`, `get_crypto_max_trd_qtys.py`, `get_crypto_order_fee.py`

**moomooapi/scripts/subscribe/ (6 scripts):**
- Purpose: Real-time data subscription and push handlers
- `subscribe.py`: Subscribe to market data types (quote, kline, broker, orderbook, ticker, rt_data)
- `unsubscribe.py`: Unsubscribe from specific types
- `unsubscribe_all.py`: Cancel all subscriptions
- `query_subscription.py`: Query current subscription status
- `push_*.py` (6 handlers): Receive and display real-time pushes for each data type

**install-moomoo-opend/SKILL.md:**
- Purpose: Installation workflow documentation
- Contains: OS auto-detection, download URLs, fallback methods, GUI vs CLI version distinction, version detection/comparison logic, post-download validation, SDK installation, common dependency setup, verification code
- Key sections: Platform detection, OpenD version checking, installer verification, SDK upgrade, dependency installation (backtrader, matplotlib, pandas, numpy)

**install-moomoo-opend/scripts/:**
- `detect_version.md`: Version number extraction from download URLs, local version detection (Windows registry, macOS Info.plist, Linux process), version comparison logic
- `verify_version.md`: Post-extraction verification that GUI installer with expected version number exists
- `install_win.md`: Windows-specific: PowerShell download, 7z extraction, GUI installer launch
- `install_mac.md`: macOS-specific: curl download, tar.gz extraction, DMG mount, copy to /Applications
- `install_linux.md`: Linux-specific: curl download, tar.gz extraction, dpkg/rpm install

## Key File Locations

**Entry Points:**
- `scripts/quote/get_snapshot.py`: Market snapshot query (most common starting point)
- `scripts/trade/place_order.py`: Order placement (most common trading operation)
- `scripts/subscribe/subscribe.py`: Start real-time subscription service
- `check_env.py`: Environment validation

**Configuration:**
- `scripts/common.py` (line 26-68): FutuConfig dataclass and `get_config()` factory
- Environment variables: `FUTU_LOGIN_ACCOUNT`, `FUTU_LOGIN_PWD`, `FUTU_OPEND_HOST`, `FUTU_OPEND_PORT`, `FUTU_TRD_ENV`, `FUTU_DEFAULT_MARKET`, `FUTU_SECURITY_FIRM`

**Core Logic:**
- `scripts/common.py` (line 149-210): OpenD connectivity, context creation, dependency checking
- `scripts/common.py` (line 250-300): Enum parsing and market inference
- `scripts/quote/get_snapshot.py` (line 63-100): Typical market data query pattern
- `scripts/trade/place_order.py` (line 86-150): Typical trading operation pattern

**Testing & Validation:**
- `check_env.py`: Pre-flight environment checks (SDK, OpenD connectivity)
- `scripts/common.py` (line 119-147): Environment check caching and version validation

## Naming Conventions

**Files:**
- Snake_case for Python files: `get_snapshot.py`, `place_order.py`, `push_quote.py`
- Prefix by operation: `get_*` (query), `place_*` (create), `modify_*` (update), `cancel_*` (delete), `push_*` (subscribe), `resolve_*` (parse)
- Category directory: `quote/`, `trade/`, `subscribe/`

**Directories:**
- Lowercase: `quote/`, `trade/`, `subscribe/`, `docs/`, `scripts/`
- Skill names: `moomooapi/`, `install-moomoo-opend/`

**Functions:**
- Snake_case: `create_quote_context()`, `get_config()`, `check_ret()`, `safe_close()`
- Private helpers: Leading underscore: `_parse_snapshot_row()`, `_audit_log()`, `_check_sdk()`

**Classes:**
- PascalCase: `FutuConfig`, `QuotePushHandler`, `TradePushHandler`

**Constants:**
- UPPER_SNAKE_CASE: `SKILL_VERSION`, `MIN_SDK_VERSION`, `STAMP_FILE`, `_SNAPSHOT_BATCH_SIZE`

## Where to Add New Code

**New Market Data Query:**
- Primary code: `scripts/quote/get_<topic>.py` (follow pattern of `get_snapshot.py`)
- Pattern: Import `common.py` utilities, create context, call OpenD API, parse response, format output (table or JSON)
- Tests: Add to `scripts/` directory if test infrastructure is set up

**New Trading Operation:**
- Primary code: `scripts/trade/<operation>.py` (follow pattern of `place_order.py`)
- Pattern: Import `common.py` utilities, validate parameters, parse enums, create trade context, execute operation, log audit trail
- Audit logging: Append to `~/.futu_trade_audit.jsonl` for compliance

**New Push Handler:**
- Primary code: `scripts/subscribe/push_<datatype>.py` (follow pattern of `push_quote.py`)
- Pattern: Define handler class extending appropriate base class, implement callback methods, manage subscription lifecycle

**New Utility Function:**
- Location: `scripts/common.py`
- Pattern: Add to appropriate section (config, context creation, dependency checking, parsing, error handling)
- Reusability: Design for use across multiple scripts; document with docstring

**New Platform-Specific Installation:**
- Location: `scripts/install_<platform>.md` (if new OS added)
- Pattern: Follow structure of existing install steps (download URL, extraction, installer launch, verification)

**Documentation Updates:**
- Quick reference: Update `moomooapi/SKILL.md` (stock code formats, API limits, command examples)
- Technical details: Add to `moomooapi/docs/` (.md files for API limits, field mappings, troubleshooting)
- Installation guide: Update `install-moomoo-opend/SKILL.md` (workflow steps, common issues)

## Special Directories

**scripts/__pycache__/:**
- Purpose: Python bytecode cache
- Generated: Yes (automatically by Python)
- Committed: No (in .gitignore if present)

**scripts/quote/ & scripts/trade/ subdirectories:**
- Purpose: Organize 55+ market data and 20+ trading scripts by category
- Generated: No (manually authored)
- Committed: Yes

**moomooapi/docs/:**
- Purpose: Supplementary documentation for API developers
- Generated: No (manually maintained)
- Committed: Yes

**install-moomoo-opend/scripts/:**
- Purpose: Platform-specific installation instructions and logic (markdown guides)
- Generated: No (manually maintained)
- Committed: Yes

---

*Structure analysis: 2026-06-23*
