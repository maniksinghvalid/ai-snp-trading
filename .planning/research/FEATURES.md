# Feature Research

**Domain:** Automated intraday day-trading bot — single-strategy, paper-trading, scheduled, with Telegram alerts and a backtester
**Researched:** 2026-06-23
**Confidence:** HIGH (cross-verified against FIA best-practices guidance, open-source reference bots Freqtrade/Lumibot, and practitioner literature)

---

## Context and Scope Anchor

This file covers the **operational and feature surface** surrounding the Trend Join Long strategy, not the strategy internals (which are fully specified in PROJECT.md). The "user" here is a single operator running the bot on their own machine. Safety and correctness for an order-placing system are weighted above convenience.

---

## Feature Landscape

### Table Stakes — Must-Have or the Bot Is Unsafe/Unusable

These are non-negotiable. An automated order-placing system missing any of these is either unsafe or operationally broken.

| Feature | Why It Is Non-Negotiable | Complexity | Dependencies / Notes |
|---------|--------------------------|------------|----------------------|
| **Duplicate-order prevention** | A network timeout, restart, or re-scan can cause the same setup to trigger twice. Two entries into the same symbol on paper money are bad practice and on real money would be a serious error. | MEDIUM | Requires durable per-symbol "position open" state checked before every order submission. State must survive restarts. |
| **Startup position reconciliation** | On restart, the bot must query the broker for live open positions and live pending orders and reconcile them against persisted state before processing any new signals. Without this, the bot may enter duplicate positions or ignore existing ones (ghost positions). | MEDIUM | Depends on durable state persistence. Moomoo API provides `get_portfolio.py` and `get_orders.py` — these must be called at init. |
| **EOD force-close at 15:51 ET** | The strategy is explicitly intraday — no overnight risk. A bot that misses the force-close window leaves positions open with real overnight gap risk (even on paper, this is a correctness failure). | LOW | Requires reliable ET timezone handling and market-calendar awareness. |
| **Market-hours / holiday gate** | The bot must not attempt to scan, enter, or manage positions when the US market is closed or on a half-day. Calling APIs during off-hours wastes quota and can produce stale data. | LOW | Moomoo `get_trading_days.py` / `get_market_state.py` provide this. Must fire before the daily scan. |
| **Kill switch (hard stop)** | A single command or file-touch that immediately halts all new order submission and triggers a graceful shutdown (exit all open positions or hold with no further action, operator's choice). Paper-only scope reduces urgency but the wiring must exist before any real-money path is considered. | LOW | Implemented as a state flag persisted to disk; checked at the top of every loop iteration. |
| **Durable state persistence** | Open positions, entry prices, stop levels, partial-fill counts, and breakeven flags must survive a process restart. The trailing stop and breakeven logic are multi-step state machines — losing that state mid-trade produces wrong exit behavior. | MEDIUM | JSON file (or SQLite) written atomically after every state change. Loaded on startup before reconciliation. |
| **Trade / order audit log** | Every order submitted, every fill received, every stop modification, and every forced exit must be recorded with timestamps, symbol, quantity, price, reason, and bot-generated order ID. This is the primary debugging surface and the foundation for PnL analysis. | LOW | Extend the existing `~/.futu_trade_audit.jsonl` pattern. Structured JSONL is sufficient. |
| **Idempotent daily scan** | The premarket scan must be safe to run multiple times (e.g., after restart during premarket). Stocks already on the watchlist for today must not be re-added or double-scanned. | LOW | Depends on durable state that tracks "scanned today" and "candidates for today." |
| **Risk-rule enforcement before order submission** | The three risk rules — 1% risk per trade, 10% max position, 5 max concurrent positions — must be checked immediately before every order call, not just at signal time. Market moves between signal and order submission can change the math. | MEDIUM | Read live account equity from broker at check time (not cached from session start) to get accurate position-size calculation. |
| **Position lifecycle manager (stop / partial / breakeven / trail)** | The bot must correctly execute: initial stop (LOD−1%), partial exit at 0.75R, stop move to breakeven at 1.0R, and 5m swing-low trailing stop thereafter. Each is a distinct state transition that must persist across restarts. Missing any of these means exits do not follow the strategy. | HIGH | This is the most complex single component. Requires 5m bar subscription, swing-low detection, and per-position state machine. |
| **Paper-trading environment lock** | `FUTU_TRD_ENV=SIMULATE` must be asserted and validated at startup. The bot must refuse to run if the environment resolves to `TrdEnv.REAL`. No real-money order path may exist in this milestone. | LOW | Single env-var check at init. Hard exit with a clear error message if misconfigured. |
| **ET timezone correctness** | All strategy time gates (entry window 10:05–15:30, force-close 15:51) must use US Eastern time, not the host machine's local timezone. Many bugs in trading systems originate from timezone assumptions. | LOW | Use `pytz` or `zoneinfo` with explicit `America/New_York` zone. Never use `datetime.now()` without timezone. |
| **OpenD connectivity check at startup** | The bot must verify the OpenD daemon is reachable before starting the trading loop. Failing silently and missing a session is worse than a clear startup error. | LOW | Socket check already in `common.py` — wire it as a hard precondition. |
| **Graceful shutdown on signal (SIGTERM/SIGINT)** | The process must handle CTRL-C and system shutdown signals by persisting state and (optionally) placing EOD closes before exiting. Abrupt termination risks orphaned orders. | LOW | Python `signal` module. Flush state file, optionally trigger EOD close routine. |
| **Telegram entry and exit alerts** | Operator must receive a push notification for every order event: entry fill, partial exit, breakeven stop move, trailing stop adjustment, force-close. Without these, the operator cannot monitor the bot hands-off. | LOW | Requires `python-telegram-bot` or `requests`-based Telegram Bot API calls. Fire-and-forget with error logging on send failure — alert failure must not crash the trade loop. |
| **Daily Telegram summary** | At EOD (after force-close), send a structured summary: trades taken, wins/losses, gross PnL, average R, and any errors encountered. This is the operator's primary performance signal. | LOW | Aggregate from the audit log after the force-close routine completes. |
| **Structured application logging** | All bot decisions, API calls, scan results, and errors must be written to a structured log file (not just stdout). Required for debugging after-the-fact without relying on terminal history. | LOW | Python `logging` with rotating file handler. JSON-structured preferred for grep-ability. |

---

### Differentiators — Valuable but Not Required for Correct Operation

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Backtester (historical 5m data replay)** | Validates strategy logic against history before trusting the live loop. Catches parameter errors and strategy-logic bugs before real money is considered. In-scope for v1 per PROJECT.md. | HIGH | Needs historical 5m OHLCV data for all S&P 500 constituents. Moomoo `get_history_kl_quota.py` and candlestick API are the source. Must faithfully reproduce all signal and exit conditions including RVOL, swing-low detection, and partial fills. Lookahead bias prevention is critical. |
| **Backtest performance report** | Win rate, average R, profit factor, max drawdown, Sharpe ratio, and trade-by-trade CSV export. Makes backtest results actionable. | MEDIUM | Built on top of the backtester. Output as a structured text/CSV report rather than a chart to avoid UI dependency. |
| **Daily scan result log (watchlist snapshot)** | Record which stocks passed each filter stage during premarket scan. Enables debugging of why a stock was or was not traded. | LOW | Append to audit log or a separate daily-scan JSONL file. No UI needed. |
| **R-multiple tracking per trade** | Record actual R achieved at each exit relative to initial risk. Enables operator to evaluate whether the strategy is capturing the expected edge. | LOW | Computed from audit log entries — entry price, stop price, partial exit prices, final exit price. |
| **Per-trade Telegram detail** | Include R-multiple, stop level, position size, and which exit rule triggered in each exit alert. Richer than a bare fill notification. | LOW | Depends on per-position state being well-structured. |
| **Daily max-loss circuit breaker** | Halt new entries for the remainder of the session if cumulative realized losses exceed a configured threshold (e.g., −2% of equity). Prevents a bad day from compounding. | LOW | One additional risk check in the entry gate. Tracks session PnL in bot state. |
| **RVOL caching** | Cache the 14-day average volume used for RVOL calculation at scan time rather than re-fetching for every 5m bar check. Reduces API calls. | LOW | Store in daily state file; invalidate at start of each new trading day. |
| **Health-check heartbeat log** | Write a timestamp to a known file every N minutes during the live loop. Operator can check this file to verify the bot is alive without requiring a separate monitoring service. | LOW | Single file write. Operator checks it manually or via a simple cron alert. |

---

### Anti-Features — Deliberately Not Built

| Feature | Why It Seems Appealing | Why It Is Problematic | What to Do Instead |
|---------|------------------------|-----------------------|--------------------|
| **Real-money trading path** | "Just a flag flip" — feels like easy future-proofing | A single misconfigured env var could route paper logic to live orders. The code must have no `TrdEnv.REAL` path in this milestone — presence of that code creates risk. | Hard-assert SIMULATE at startup. Add `TrdEnv.REAL` only in a future milestone after explicit safety review. |
| **Multi-strategy framework** | More strategies = more value | Adds abstraction layers that obscure the simple signal → order → manage loop. The strategy is fully specified; a framework would be premature generalization that complicates debugging. | Single concrete implementation. Refactor to a framework only when a second strategy is actually needed. |
| **Web dashboard / UI** | "Nicer" than Telegram | Significant build surface (Flask/FastAPI, frontend, auth, HTTPS) for a single-operator tool. Telegram already provides push-based notifications — the operator does not need to pull a dashboard. | Telegram for all operator communication in v1. |
| **Interactive Telegram commands (buy/sell/cancel from chat)** | Operator feels "in control" | Order-placing via chat introduces latency, typo risk, and complex state management for a bot that is supposed to be autonomous. For paper trading, there is no urgency requiring interactive control. | Kill switch via file-touch or SIGINT. Manual inspection via audit log. Add Telegram commands only if operational experience shows a genuine need. |
| **Parameter optimization / hyperopt** | Maximize backtest metrics | Optimization without out-of-sample validation leads to overfitting. The strategy parameters are already specified — the backtester validates them, it does not tune them. | Fix strategy parameters as specified. Use the backtester for validation only. |
| **Live strategy performance dashboard with charts** | Visual appeal | Matplotlib/charting adds dependencies and generates artifacts that are not useful without a UI to display them. | Text-based backtest report. Telegram daily summary. |
| **Auto-reconnect / auto-restart wrapper** | Resilience feels necessary | OpenD requires a logged-in GUI session — if OpenD goes down, the bot cannot trade regardless of auto-restart. Adding a restart wrapper creates false confidence and masks real connectivity issues. | Log the error clearly. Operator restarts the bot after verifying OpenD is up. |
| **System-health Telegram alerts (OpenD down, scan-missed)** | Seems like table stakes | As noted in PROJECT.md Out of Scope: internal logging covers this in v1. Adding Telegram alerts for infrastructure failures requires a separate monitoring loop that adds complexity. | Log to file. Promote to Telegram alerts in a future milestone. |
| **Short selling** | Strategy generalization | Strategy is long-only by specification. Short-selling requires different order types, different risk math, and different exit logic. Adding it now pollutes the codebase. | Out of scope explicitly. |
| **Crypto / options / non-US universe support** | API already supports it | The Moomoo API supports many markets but the strategy targets S&P 500 US stocks only. Generic universe support adds complexity without value in v1. | Hard-code US market and S&P 500 constituent list. |

---

## Feature Dependencies

```
[Durable State Persistence]
    └──required by──> [Duplicate-Order Prevention]
    └──required by──> [Startup Position Reconciliation]
    └──required by──> [Position Lifecycle Manager]
    └──required by──> [Idempotent Daily Scan]
    └──required by──> [Graceful Shutdown]

[Startup Position Reconciliation]
    └──must precede──> [Any Signal Processing or Order Submission]

[Position Lifecycle Manager]
    └──required by──> [EOD Force-Close]
    └──required by──> [Telegram Entry/Exit Alerts]
    └──required by──> [Daily Telegram Summary]
    └──required by──> [R-Multiple Tracking] (differentiator)

[Risk-Rule Enforcement]
    └──required by──> [Order Submission] (gates every order call)

[Market-Hours / Holiday Gate]
    └──must precede──> [Daily Scan]
    └──must precede──> [Intraday Signal Processing]

[Paper-Trading Environment Lock]
    └──must succeed at init before──> [Any API Connection]

[OpenD Connectivity Check]
    └──must succeed at init before──> [Any API Connection]

[Trade / Order Audit Log]
    └──feeds──> [Daily Telegram Summary]
    └──feeds──> [R-Multiple Tracking] (differentiator)
    └──feeds──> [Backtest Performance Report] (differentiator)

[Backtester]
    └──required by──> [Backtest Performance Report] (differentiator)
    └──requires──> [Historical 5m data from Moomoo API]
    └──shares logic with──> [Position Lifecycle Manager] (exit logic must match exactly)
```

### Dependency Notes

- **Durable state persistence is the foundation.** Every safety feature — duplicate prevention, reconciliation, position lifecycle — depends on it. It must be the first component designed and the last thing written on shutdown.
- **Position reconciliation must precede signal processing.** On every startup, the bot must be in a known, broker-verified state before it evaluates any new signals. The order is: check env → check OpenD → load state → reconcile with broker → start scan loop.
- **Backtester exit logic must be byte-for-byte equivalent to the live loop exit logic.** If the backtester uses a simplified exit model, backtest results will not predict live behavior. Share the exit logic code, do not reimplement it.
- **Telegram send failures must not block the trade loop.** Alerts are observability, not execution. A Telegram API timeout must log an error and continue — it must never propagate to halt an order submission.
- **Risk enforcement reads live equity.** Do not cache account equity from session start. Re-read from broker at position-size calculation time to account for intraday P&L changes.

---

## MVP Definition

### Launch With (v1) — All Table Stakes Features

The v1 is complete when every table-stakes feature is implemented and verified on paper. The backtester is also in v1 scope per PROJECT.md.

- [ ] Paper-trading environment lock — assert SIMULATE, hard-exit if REAL
- [ ] OpenD connectivity check at startup
- [ ] ET timezone correctness throughout
- [ ] Market-hours / holiday gate
- [ ] Durable state persistence (JSON, atomic writes)
- [ ] Startup position reconciliation against broker
- [ ] Duplicate-order prevention (symbol-level position guard)
- [ ] Idempotent daily scan
- [ ] Risk-rule enforcement at order submission time
- [ ] Position lifecycle manager (stop, partial at 0.75R, breakeven at 1.0R, 5m swing-low trail)
- [ ] EOD force-close at 15:51 ET
- [ ] Kill switch (file-touch or SIGINT-driven)
- [ ] Graceful shutdown with state flush
- [ ] Trade / order audit log (JSONL)
- [ ] Structured application logging (rotating file)
- [ ] Telegram entry and exit alerts
- [ ] Daily Telegram summary
- [ ] Backtester with historical 5m data replay

### Add After Validation (v1.x) — Differentiators Worth Adding Once Core Is Stable

- [ ] Daily max-loss circuit breaker — add when paper results show value in preventing compounding losses
- [ ] Per-trade R-multiple tracking — add when audit log is mature enough to compute reliably
- [ ] Richer Telegram exit alerts (R achieved, stop level, exit rule) — add once basic alerts are proven reliable
- [ ] Backtest performance report (win rate, profit factor, max drawdown) — extend backtester output
- [ ] Health-check heartbeat log — add if operator finds manual monitoring tedious

### Future Consideration (v2+) — After Paper Validation

- [ ] System-health Telegram alerts (OpenD down, scan-missed) — requires separate monitoring loop
- [ ] Daily max-loss circuit breaker (promote to table stakes if real money is added)
- [ ] Real-money path — only after explicit safety audit and deliberate decision; requires full re-review of every table-stakes safety feature

---

## Feature Prioritization Matrix

| Feature | Operator Value | Implementation Cost | Priority |
|---------|---------------|---------------------|----------|
| Durable state persistence | HIGH | MEDIUM | P1 |
| Startup position reconciliation | HIGH | MEDIUM | P1 |
| Duplicate-order prevention | HIGH | LOW | P1 |
| Paper-trading env lock | HIGH | LOW | P1 |
| Position lifecycle manager | HIGH | HIGH | P1 |
| EOD force-close | HIGH | LOW | P1 |
| Risk-rule enforcement | HIGH | MEDIUM | P1 |
| ET timezone correctness | HIGH | LOW | P1 |
| Trade / order audit log | HIGH | LOW | P1 |
| Market-hours / holiday gate | HIGH | LOW | P1 |
| Kill switch | HIGH | LOW | P1 |
| Telegram alerts (entry/exit) | HIGH | LOW | P1 |
| Daily Telegram summary | MEDIUM | LOW | P1 |
| Structured application logging | MEDIUM | LOW | P1 |
| Backtester | HIGH | HIGH | P1 (in-scope v1) |
| Backtest performance report | MEDIUM | MEDIUM | P2 |
| Daily max-loss circuit breaker | MEDIUM | LOW | P2 |
| R-multiple tracking | MEDIUM | LOW | P2 |
| RVOL caching | LOW | LOW | P2 |
| Health-check heartbeat log | LOW | LOW | P3 |
| System-health Telegram alerts | LOW | MEDIUM | P3 |

**Priority key:**
- P1: Must have for launch — required for safe, correct paper operation
- P2: Should have — add after core is verified working
- P3: Nice to have — defer until operational experience reveals genuine need

---

## Safety and Correctness Emphasis

For an automated order-placing system, even on paper, these features carry disproportionate weight:

**Startup sequence must be:** env check → OpenD check → state load → broker reconciliation → market-hours check → scan. Any failure in the first four steps must hard-exit with a clear error message. Silent failures that let the bot proceed with bad state are worse than crashes.

**The position lifecycle manager is the highest-complexity, highest-correctness-risk component.** Its state machine has five distinct states per position (initial, partial-taken, breakeven-set, trailing, closed) and must handle partial fills, stop modifications, and the distinction between broker-side stops and bot-managed stops. The Moomoo API supports trailing stop orders natively (`TRAILING_PERCENT` and `TRAILING_AMOUNT` order types), but the 5m swing-low trail described in the strategy is custom logic that must be implemented in the bot, not delegated to the broker's trailing stop order type.

**Duplicate prevention must be broker-verified, not just state-verified.** On startup reconciliation, query actual open positions from the API — do not trust only the local state file, which could be stale if the process was killed abnormally.

---

## Competitor Feature Analysis

These open-source bots inform what a production-quality feature surface looks like for comparable systems:

| Feature | Freqtrade | Lumibot | This Bot |
|---------|-----------|---------|----------|
| Paper trading isolation | Yes (dry-run mode) | Yes (backtester reuses live code) | Yes (SIMULATE env lock) |
| State persistence across restarts | Yes (trades.json) | Partial | Yes (required) |
| Startup broker reconciliation | Yes | Yes | Yes (required) |
| Kill switch | Yes (Telegram /stop) | Yes | Yes (file/signal) |
| Telegram alerts | Yes (full Telegram bot) | Yes (send_telegram_message tool) | Yes (fire-and-forget alerts) |
| Daily summary | Yes | Partial | Yes |
| Backtester | Yes (full framework) | Yes (key differentiator) | Yes (custom, single strategy) |
| Circuit breaker / daily loss limit | Yes | Yes | Differentiator (v1.x) |
| Multi-strategy | Yes | Yes | Anti-feature (no) |
| Web UI | Yes | No | Anti-feature (no) |
| Interactive trading commands | Yes (Telegram commands) | No | Anti-feature (no) |

---

## Sources

- [FIA Best Practices for Automated Trading Risk Controls](https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf) — Industry standard for kill switches, duplicate prevention, circuit breakers
- [Building Production-Grade Algorithmic Trading Bots (Medium)](https://medium.com/@writeronepagecode/building-production-grade-algorithmic-trading-bots-e91e7ff6c6de) — State persistence, process isolation, lifecycle patterns
- [Architecting a Robust Trading Bot (Substack)](https://onepagecode.substack.com/p/architecting-a-robust-trading-bot) — Startup reconciliation, market-hours gate, trade manager patterns
- [Lumibot framework](https://lumibot.lumiwealth.com/) — Open-source reference for paper-to-live architecture, Telegram integration
- [Freqtrade](https://github.com/freqtrade-crypto-trading-bot) — Open-source reference for operational feature completeness
- [Binance Risk Management with Daily Loss Limit and Circuit Breaker](https://www.binance.com/en/square/post/32732737991226) — Circuit breaker design patterns
- [Backtesting Trading Strategies (Blockchain Council)](https://www.blockchain-council.org/cryptocurrency/backtesting-ai-crypto-trading-strategies-avoiding-overfitting-lookahead-bias-data-leakage/) — Lookahead bias prevention, walk-forward validation
- [Freqtrade state persistence issue discussion](https://github.com/freqtrade/freqtrade/issues/5227) — Real-world patterns for trade state recovery

---

*Feature research for: Automated S&P 500 intraday day-trading bot (Trend Join Long), paper-trading on Moomoo/Futu OpenAPI*
*Researched: 2026-06-23*
