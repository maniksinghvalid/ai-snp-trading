# Technology Stack

**Analysis Date:** 2026-06-23

## Languages

**Primary:**
- Python 3.x - Market data queries, order placement, portfolio management, and real-time subscription handling in `skills/moomooapi/scripts/` and `skills/install-moomoo-opend/scripts/`

## Runtime

**Environment:**
- Python 3.6+ (tested with modern 3.x)

**Package Manager:**
- pip / pip3
- Package name: `moomoo-api`

## Frameworks

**Core API SDK:**
- moomoo-api >= 10.4.6408 - Python SDK for Futu OpenAPI providing quote and trading contexts
  - Crypto trading: requires moomoo-api >= 10.5.6508 (provides OpenCryptoTradeContext)

**Market Data:**
- OpenQuoteContext - Quote/market data access via `create_quote_context()` in `skills/moomooapi/scripts/common.py`

**Trading:**
- OpenSecTradeContext - Securities trading (stocks, options, futures) via `create_trade_context()` in `skills/moomooapi/scripts/common.py`
- OpenCryptoTradeContext - Cryptocurrency trading (BTC, ETH) via `create_crypto_trade_context()` in `skills/moomooapi/scripts/common.py`

**Data Processing:**
- pandas - DataFrame operations in quote/trading scripts for handling market snapshots, candlesticks, order books

**Utilities:**
- argparse - CLI parameter parsing in all Python scripts
- json - JSON output formatting
- socket - OpenD connectivity checking in `common.py`

## Key Dependencies

**Critical:**
- moomoo-api [version: >=10.4.6408, <11.0] - Why it matters: Futu OpenAPI SDK; all market data, quote subscriptions, and trading operations depend on this
- OpenD (separate binary service) [version: >=10.4.6408] - Why it matters: Required daemon that serves the API; scripts fail if not running on 127.0.0.1:11111

**Infrastructure:**
- pandas - Data frame manipulation for market data responses
- socket library - TCP connectivity validation to OpenD

## Configuration

**Environment Variables:**

Scripts in `skills/moomooapi/scripts/common.py` read the following environment variables (all optional with defaults):

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

**Configuration Storage:**

- Environment check cache: `~/.moomoo_skill_version` - Version stamp file written after `/install-moomoo-opend` runs
- Temp cache: `/tmp/.moomoo_env_ok` - Environment check result (1-hour TTL)
- Crypto firm cache: `/tmp/.moomoo_crypto_firm` - Auto-detected crypto trading firm (1-hour TTL)
- Trade audit log: `~/.futu_trade_audit.jsonl` - Append-only JSONL log of all placed orders

**OpenD Connection:**

- Default address: `127.0.0.1:11111` (configurable via FUTU_OPEND_HOST and FUTU_OPEND_PORT)
- Protocol: WebSocket over localhost
- Port connectivity checked via socket.connect() before API operations

## Platform Requirements

**Development:**
- macOS / Linux / Windows (OpenD GUI version available for all three)
- Python 3.6+
- moomoo account (login required in OpenD GUI)

**Production:**
- OpenD service running (GUI version required, command-line version not supported)
- Python 3.6+
- Network access to OpenD on 127.0.0.1:11111 (or custom host:port)

**Deployment Target:**
- Local desktop/laptop running OpenD GUI
- OpenD can be configured to listen on 0.0.0.0 for LAN access (not Internet-exposed recommended)

## Version Constraints

**SDK Version Compatibility:**

| Feature | Minimum Version | Notes |
|---------|-----------------|-------|
| Basic trading & quotes | 10.4.6408 | Minimum supported version |
| Crypto trading | 10.5.6508 | First version with OpenCryptoTradeContext |
| AI-type parameter | 10.4.6408 | SDK parameter support detection in `common.py` |

**OpenD Compatibility:**
- OpenD version must match or exceed SDK version (both use same X.Y.ZZZZ versioning)
- Version detection: `~/.moomoo_skill_version` stamp file (written by `/install-moomoo-opend` skill)
- Mismatch warning: Scripts warn if detected version != installed version

---

*Stack analysis: 2026-06-23*
