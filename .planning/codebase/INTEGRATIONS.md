# External Integrations

**Analysis Date:** 2026-06-23

## APIs & External Services

**Futu OpenAPI (Primary):**
- Service: Futu Financial Cloud OpenAPI - Market data, trading, real-time subscriptions
  - SDK/Client: `moomoo-api` Python SDK (imported via `from moomoo import *` in `skills/moomooapi/scripts/common.py`)
  - Transport: WebSocket protocol via OpenD daemon
  - Connection: `OpenQuoteContext`, `OpenSecTradeContext`, `OpenCryptoTradeContext` classes from SDK
  - Location: Created in `skills/moomooapi/scripts/common.py` via `create_quote_context()`, `create_trade_context()`, `create_crypto_trade_context()`

**moomoo OpenD (Local Service Daemon):**
- Service: Local desktop application that bridges API calls to Futu backend
  - Role: Required intermediary between Python scripts and Futu's trading/quote servers
  - Version requirement: >= 10.4.6408
  - Connection: TCP on 127.0.0.1:11111 (configurable via FUTU_OPEND_HOST, FUTU_OPEND_PORT)
  - Connectivity check: Socket connection test in `common.py` line 154-163
  - Platform support: GUI version for Windows, macOS, Linux (command-line version not supported)

## Data Storage

**No Backend Database:**
- Scripts do not connect to persistent databases
- All data is transient: quote snapshots, order state, and positions are queried via API calls
- Trade audit log: Appended to `~/.futu_trade_audit.jsonl` locally on execution host (line 68 in `place_order.py`)

**File Storage:**
- Local audit trail: `~/.futu_trade_audit.jsonl` (JSONL format, written when orders are placed)
- Configuration storage: Environment variable files (user-managed)
- Cache files: Temporary files in system temp directory for version/environment checks (TTL-based cleanup)

**Caching:**
- Environment check cache: `.moomoo_env_ok` in temp directory (1-hour TTL, prevents repeated version checks)
- Crypto firm cache: `.moomoo_crypto_firm` in temp directory (1-hour TTL, caches auto-detected crypto trading firm)

## Authentication & Identity

**Auth Provider:**
- Custom/Futu account system - User authenticates via OpenD GUI

**Implementation Details:**
- Login credentials: `FUTU_LOGIN_ACCOUNT` and `FUTU_LOGIN_PWD` environment variables (used by OpenD, not by Python SDK)
- Trade password: Separate password for live trading, unlocked manually in OpenD GUI (not via SDK; see security rules in `SKILL.md`)
- Trade unlock: Manual GUI unlock only; `unlock_trade()` API call is explicitly forbidden by security policy (lines 210-214 in `SKILL.md`)
- Security firm routing: `FUTU_SECURITY_FIRM` environment variable or auto-detected via `_detect_crypto_firm()` in `common.py`

## Market Data Feeds

**Real-Time Subscription:**
- WebSocket push subscriptions via `OpenQuoteContext`
- Scripts: `skills/moomooapi/scripts/subscribe/` directory contains:
  - `subscribe.py` - Subscribe to quote/candlestick/broker/orderbook/ticker/time-series data
  - `push_quote.py` - Receive real-time quote pushes
  - `push_kline.py` - Receive candlestick updates
  - `push_broker.py` - Receive broker queue updates
  - `push_orderbook.py` - Receive order book depth updates
  - `push_ticker.py` - Receive tick-by-tick trade updates
  - `push_rt_data.py` - Receive intraday time-series updates

**Quote Data Types:**
- Snapshots: Last price, OHLC, volume, turnover (no subscription needed)
- Candlesticks: 1m, 3m, 5m, 15m, 30m, 60m, 1d, 1w, 1M, 1Q, 1Y intervals
- Order book: Bid/ask depth (Level 2 data)
- Tick-by-tick: Individual trade records
- Time-sharing: Intraday price data

## Trading Operations

**Trading Venues:**
- US stocks (market: `US`, code prefix: `US.` e.g. `US.AAPL`)
- HK stocks (market: `HK`, code prefix: `HK.` e.g. `HK.00700`)
- A-shares (market: `CN`, code prefixes: `SH.` Shanghai, `SZ.` Shenzhen)
- Singapore futures/stocks (market: `SG`, code prefix: `SG.`)
- Cryptocurrency (market: `CRYPTO`, code prefix: `CC.` e.g. `CC.BTC`, `CC.BTCUSD`)
- Options (supports calls/puts)
- Futures (SG futures including A50, Nikkei, etc.)

**Trading Environments:**
- Paper trading: `TrdEnv.SIMULATE` (default, uses virtual funds, no password required)
- Live trading: `TrdEnv.REAL` (real funds, requires manual GUI unlock, trades password protected)

**Order Types:**
- Limit orders
- Market orders
- Stop-loss / take-profit orders
- Trailing stop orders
- Auction orders (HK pre-market)

**Order Scripts:**
- `place_order.py` - Place buy/sell orders
- `modify_order.py` - Modify pending orders
- `cancel_order.py` - Cancel orders
- `get_orders.py` - Query today's orders
- `get_history_orders.py` - Query historical orders
- Crypto variants: `place_crypto_order.py`, `cancel_crypto_order.py`, `get_crypto_orders.py`

## Portfolio & Account Data

**Portfolio Queries:**
- `get_accounts.py` - List all accounts, account types, market permissions
- `get_portfolio.py` - Positions and funds for single account
- `get_all_portfolios.py` - Positions and funds across all accounts
- `get_crypto_portfolio.py` - Cryptocurrency positions and balance
- `get_margin_ratio.py` - Margin ratio (leverage information)

**Order History & Fills:**
- `get_order_fill_list.py` - Today's executed fills
- `get_history_order_fill_list.py` - Historical fills
- `get_order_fee.py` - Order commission and fees

**Cash Flow:**
- `get_acc_cash_flow.py` - Account cash flow / deposit/withdrawal history
- `get_crypto_cash_flow.py` - Crypto account cash flow

## Fundamental & Research Data

**Company Information:**
- `get_company_profile.py` - Company profile, sector, industry
- `get_company_executives.py` - Directors and executive management
- `get_company_executive_background.py` - Executive background details
- `get_company_operational_efficiency.py` - Operational efficiency metrics

**Financial Statements:**
- `get_financials_statements.py` - Income statement, balance sheet, cash flow
- `get_financials_revenue_breakdown.py` - Revenue by business segment
- `get_financials_earnings_price_move.py` - Historical earnings day price moves
- `get_financials_earnings_price_history.py` - Earnings announcement history

**Valuation & Metrics:**
- `get_valuation_detail.py` - PE, PB, dividend yield, valuation history
- `get_valuation_plate_stock_list.py` - Plate/sector valuation comparison

**Corporate Actions:**
- `get_corporate_actions_dividends.py` - Dividend history and distribution records
- `get_corporate_actions_buybacks.py` - Share buyback history
- `get_corporate_actions_stock_splits.py` - Stock splits and mergers

**Research & Analyst Data:**
- `get_research_analyst_consensus.py` - Analyst consensus ratings (buy/hold/sell)
- `get_research_rating_summary.py` - Rating breakdown by analyst/institution
- `get_research_morningstar_report.py` - Morningstar research reports

**Shareholder Data:**
- `get_shareholders_overview.py` - Top shareholders and insider holdings
- `get_shareholders_holding_changes.py` - Shareholder holding changes history
- `get_shareholders_holder_detail.py` - Detailed shareholder list
- `get_shareholders_institutional.py` - Institutional investor holdings
- `get_insider_holder_list.py` - Company insiders
- `get_insider_trade_list.py` - Insider trading activity
- `get_top_ten_buy_sell_brokers.py` - Top 10 brokers by buy/sell volume (HK)

**Short Selling Data:**
- `get_daily_short_volume.py` - Daily short volume (US/HK)
- `get_short_interest.py` - Short interest ratio (US/HK)

## Options & Derivatives

**Option Queries:**
- `resolve_option_code.py` - Decode option shorthand codes (e.g., "AAPL 150C" -> full code)
- `get_option_expiration_date.py` - Option contract expiration dates
- `get_option_chain.py` - Full option chain with all strikes and expirations
- `get_option_screen.py` - Option screener with filters
- `get_option_volatility.py` - Implied volatility analysis
- `get_option_exercise_probability.py` - Probability of exercise estimates

**Warrant Data:**
- `get_warrant.py` - Warrant list
- `get_warrant_screen.py` - Warrant screener (43 columns, HK/SG/MY support)
- `get_referencestock_list.py` - Related warrants/futures for an underlying stock

**Futures Data:**
- `get_future_info.py` - Futures contract specifications
- SG futures codes: `SG.CNmain` (A50 Futures), `SG.NKmain` (Nikkei Futures)

## Screening & Filtering

**Stock Screener:**
- `get_stock_filter.py` - V1 screener (legacy)
- `get_stock_screen.py` - V2 screener with builder API and broader factors

**Plate/Sector Data:**
- `get_plate_list.py` - List all plates/sectors
- `get_plate_stock.py` - Constituents of a plate/sector
- `get_owner_plate.py` - Which plates/sectors a stock belongs to

**Watchlist Management:**
- `get_user_security.py` - User's watchlist stocks
- `get_user_security_group.py` - Watchlist groups
- `modify_user_security.py` - Add/remove watchlist items

**Price Alerts:**
- `get_price_reminder.py` - List price reminders
- `set_price_reminder.py` - Set/update price alerts

## System & Market State

**Market Status:**
- `get_global_state.py` - OpenD global state, OpenAPI status
- `get_market_state.py` - Trading market open/closed status for specific stocks
- `get_trading_days.py` - List of trading days for a market

**User Permissions:**
- `get_user_info.py` - Quote permissions for markets, real-time data package subscriptions

**Data Availability:**
- `get_history_kl_quota.py` - Historical candlestick data quota remaining

**IPO Information:**
- `get_ipo_list.py` - Upcoming and recent IPOs

## Webhooks & Callbacks

**Real-Time Push Handling:**
- Subscription handlers in `subscribe/push_*.py` scripts implement callback-based push receiving
- WebSocket events fire `TradeOrderHandlerBase` and `TradeDealHandlerBase` for order/trade updates
- Note: Push notifications for US paper trading accounts may be temporarily unavailable (SDK documentation)

**No Outgoing Webhooks:**
- No callback URLs or webhook registration to external services
- All communication is client-initiated (polling or subscription-based)

## Environment Configuration

**Required Environment Variables for Operation:**
- `FUTU_OPEND_HOST` - OpenD connection (default: 127.0.0.1)
- `FUTU_OPEND_PORT` - OpenD port (default: 11111)
- `FUTU_TRD_ENV` - Paper vs. live trading (default: SIMULATE)

**Optional for Convenience:**
- `FUTU_LOGIN_ACCOUNT` - Pre-filled account for OpenD login
- `FUTU_LOGIN_PWD` - Pre-filled password (security risk; not recommended)
- `FUTU_DEFAULT_MARKET` - Default market when not specified
- `FUTU_ACC_ID` - Default account ID
- `FUTU_SECURITY_FIRM` - Security firm for trading

**Secrets Storage:**
- Trade password: Unlocked manually in OpenD GUI (no storage in codebase)
- Login credentials: Environment variables (user responsibility to set securely)

---

*Integration audit: 2026-06-23*
