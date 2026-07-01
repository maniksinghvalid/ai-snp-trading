# AI S&P Trading Bot (Trend Join Long)

## What This Is

A fully automated paper-trading system that trades a single, well-defined day-trading
strategy ("Trend Join Long") on S&P 500 stocks through the Moomoo/Futu OpenAPI. It scans
premarket for setups, enters and manages intraday positions on a 5-minute timeframe,
enforces risk rules automatically, sends Telegram alerts, and runs hands-off on a schedule —
no manual operation by a human. It also includes a backtester to validate the strategy on
historical data. Built for the operator (the project owner) running it on their own machine
against a Moomoo paper (SIMULATE) account.

## Core Value

The bot autonomously executes the Trend Join Long strategy end-to-end on a paper account —
scan, enter, manage risk, exit, and report — correctly and unattended, exactly as specified.
If everything else is stripped away, a correct and safe automated trade loop is what must work.

## The Strategy — "Trend Join Long"

The strategy is the product. It is fully specified and authoritative:

```json
{
  "strategy_name": "Trend Join Long",
  "direction": "long_only",
  "trade_timeframe": "5m",

  "universe_filters": {
    "index": "S&P 500",
    "min_price_usd": 3.0
  },

  "daily_filters": {
    "D1_above_prior_day_high": true,
    "D2_prior_close_above_sma200": true,
    "D3_min_gap_pct_from_prior_close": 3.0
  },

  "intraday_filters": {
    "I1_above_premarket_high": true,
    "I2_above_today_hod": true,
    "I3_rvol_min": 2.0,
    "I3_rvol_lookback_days": 14
  },

  "time_filter": {
    "earliest_entry_et": "10:05",
    "latest_entry_et": "15:30",
    "force_close_et": "15:51"
  },

  "exit": {
    "initial_stop_rule": "lod_minus_1pct",
    "partial_profit_trigger_R": 0.75,
    "partial_profit_fraction": 0.3333,
    "breakeven_trigger_R": 1.0,
    "post_breakeven_trail": "swing_low_5m_2_2"
  },

  "risk": {
    "max_risk_per_trade_pct": 1.0,
    "max_position_size_pct_of_portfolio": 10,
    "max_concurrent_positions": 5,
    "max_trades_per_day": 5
  }
}
```

> This block is the canonical content of the runtime **`rules.json`** config (CFG-01):
> the bot loads it at startup as the single source of truth for all filters, time gates,
> exit rules, and risk parameters — no strategy constants hardcoded in Python.

**Plain-English summary:**
- **Universe:** S&P 500 constituents, price ≥ $3.
- **Daily setup (all must hold):** trading above prior-day high; prior close above SMA200;
  gap ≥ 3% from prior close.
- **Intraday trigger (all must hold):** above premarket high; above today's high-of-day;
  relative volume ≥ 2.0 over a 14-day lookback.
- **Timing:** entries allowed 10:05–15:30 ET; all positions force-closed at 15:51 ET (flat by close → intraday/day-trading, no overnight risk).
- **Exits:** initial stop = low-of-day − 1%; take ⅓ off at 0.75R; move stop to breakeven at 1.0R;
  thereafter trail on 5-minute swing lows (2/2 pattern).
- **Risk:** risk 1% of account per trade; max 10% of portfolio per position; max 5 concurrent positions; max 5 *new entries per day* (over-trading guard, distinct from the concurrent cap).

## Requirements

### Validated

<!-- Existing infrastructure available in this repo (not yet wired into a bot). -->

- ✓ Moomoo/Futu OpenAPI Python client — quote, trade, and subscribe scripts — existing (`skills/moomooapi/`)
- ✓ OpenD connectivity + paper-trading (SIMULATE) defaults and trade audit log — existing (`skills/moomooapi/scripts/common.py`)
- ✓ OpenD installer/setup helper — existing (`skills/install-moomoo-opend/`)

### Active

<!-- The bot to build. Hypotheses until shipped and validated on the paper account. -->

- [ ] Premarket scanner that screens the S&P 500 against the daily filters (prior-day high, SMA200, ≥3% gap, price ≥ $3)
- [ ] Intraday signal engine on 5m bars (above premarket high, above HOD, RVOL ≥ 2.0) within the entry window
- [ ] Automated order placement through the Moomoo API on the paper account
- [ ] Position lifecycle manager: initial stop (LOD−1%), partial at 0.75R, breakeven at 1.0R, 5m swing-low trailing stop
- [ ] Risk engine: 1% risk/trade sizing, 10% max position, 5 max concurrent positions, $100k assumed equity
- [ ] EOD force-close of all open positions at 15:51 ET
- [ ] Telegram alerts for entries, exits (partial/breakeven/trail/stop/force-close), and a daily summary
- [ ] Long-running scheduled service that orchestrates premarket → intraday loop → EOD unattended
- [ ] Durable state + trade logging so the bot survives restarts and produces an auditable record
- [ ] Backtester to validate Trend Join Long on historical 5m data

### Out of Scope

- Live/real-money trading — paper (SIMULATE) only for this milestone; real capital is a deliberate future decision after validation
- LLM/AI in the trade-decision loop — the brain is a deterministic mechanical screener; the existing `/trade` AI skills remain a separate manual research toolkit
- Multiple strategies / strategy framework — one strategy (Trend Join Long) for now
- Short selling — strategy is long-only by definition
- Non-S&P 500 universes (broad market, crypto, options) — single universe for now
- Interactive web dashboard / server-backed UI — Telegram is the primary interface for v1 (a *static, offline, no-JS* HTML performance report file is in scope as an optional reporting artifact; a live web app/server is not)
- System-health Telegram alerts (OpenD down, scan-didn't-run) — internal logging covers this in v1; promote to alerts later

## Context

- **Brownfield repo.** The directory already contains a mature Moomoo/Futu OpenAPI Python
  client (96 scripts: quote/trade/subscribe) plus a suite of `/trade` AI analysis skills.
  See `.planning/codebase/` for the full map. The bot builds *on top of* this client; it
  does not reinvent broker access.
- **Broker model.** OpenD daemon runs locally on `127.0.0.1:11111`; the API talks to it over
  localhost. Paper trading is the `SIMULATE` environment (already the default). A trade audit
  log already exists at `~/.futu_trade_audit.jsonl`.
- **Data/execution split.** Scanning is data-heavy across all ~500 constituents and would
  strain Moomoo's snapshot/kline quota, so daily-bar scan data (and backtest history) come from
  a free external source (**yfinance**). Moomoo/OpenD is reserved for what only the broker can
  do: order execution, position/account truth, and live intraday 5m bar subscriptions for the
  (small) watchlist. This is a supplementary read-only data source, not a rewrite of broker access.
- **Strategy is stateful and intraday.** Trailing stops, partial fills, and breakeven moves
  require continuous per-position tracking through the session — this drives the long-running
  service architecture decision below.
- **Operator-run.** Runs on the owner's machine where OpenD is logged in; not a multi-tenant
  or cloud-hosted product (for now).

## Constraints

- **Tech stack**: Python 3.6+ — must reuse the existing `moomoo-api` SDK and `skills/moomooapi` client (no rewrite of broker access). `yfinance` is added as a read-only market-data source for scanning and backtest history only (not broker access).
- **Dependency**: Requires OpenD GUI running and logged in on `127.0.0.1:11111`; the bot is non-functional without it.
- **Safety**: Paper trading only (`FUTU_TRD_ENV=SIMULATE`); no real-money order path in this milestone.
- **Timezone**: All strategy timing is US Eastern (ET); the bot must handle ET/market-session correctness regardless of host timezone.
- **Market hours**: Operates only on US market trading days; must respect holidays/half-days.
- **Position sizing basis**: Assume $100,000 starting paper equity for risk math.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Mechanical (non-LLM) trade brain | Fast, cheap, deterministic, fully specified by the strategy; no token cost or latency in the trade loop | — Pending |
| Long-running service (vs cron) | Strategy is stateful intraday (trailing/partial/breakeven); a persistent process owning state + live subscriptions is simpler and more reliable | — Pending |
| Backtester included in scope | Validate Trend Join Long on history before trusting it live; build confidence | — Pending |
| Paper-only this milestone | Prove correctness and safety before risking real capital | — Pending |
| Reuse existing moomoo client | Mature, mapped, already handles OpenD/paper/audit; avoid reinventing broker access | — Pending |
| Telegram as the v1 interface | Lightweight, push-based, no UI to build | — Pending |
| yfinance for scan + backtest data; Moomoo for execution + live 5m | Scanning 500 symbols would exhaust Moomoo snapshot/kline quota; free daily bars from yfinance avoid the bottleneck and resolve the Phase 2/Phase 6 data-quota research flags. Broker still owns all trading + live intraday data. | — Pending |
| `rules.json` externalized strategy config | All filters/time-gates/exit/risk params read from one JSON file (single source of truth); change strategy without code edits; live and backtest read the same config | — Pending |
| Intraday re-scan (every 30 min, ~7 passes) | A single premarket snapshot misses stocks that gap/break out after the open; periodic re-scan through midday catches later setups (free, via yfinance) | — Pending |
| Daily entry cap (max_trades_per_day) | Over-trading guard distinct from the 5-concurrent cap; bounds daily churn even as positions close and free up slots | — Pending |
| HTML performance dashboard (optional) | Offline, no-JS R-multiple histogram + open/closed trade tables for at-a-glance edge validation; complements (does not replace) Telegram summary | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-23 after initialization*
