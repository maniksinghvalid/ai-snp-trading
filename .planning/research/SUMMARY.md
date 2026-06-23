# Project Research Summary

**Project:** AI S&P Trading Bot (Trend Join Long)
**Domain:** Automated intraday day-trading bot — single-strategy, paper-only, long-running supervised service
**Researched:** 2026-06-23
**Confidence:** HIGH

## Executive Summary

This project is a fully automated paper-trading bot that executes a single, completely specified mechanical strategy (Trend Join Long) on S&P 500 stocks via the Moomoo/Futu OpenAPI. The strategy involves premarket scanning, intraday 5m bar signal evaluation, multi-stage position management (partial exits, breakeven moves, trailing stops), and mandatory EOD force-close. Experts build systems of this type as long-running async services with event-driven internal buses, durable SQLite state, and a per-position finite state machine. The dominant reference architectures (Freqtrade, Lumibot, QuantStart event-driven series) converge on the same pattern: separate strategy logic from execution, share that logic between the live bot and backtester, and never trust push events alone — always reconcile against broker truth.

The recommended approach is a single-process asyncio service composed of 10 clearly bounded components (Scanner, SignalEngine, RiskEngine, PositionManager, ExecutionEngine, BarAggregator, MoomooGateway, StateStore, TelegramAlerter, TradingBot orchestrator) built bottom-up in dependency order. The new stack additions are minimal: APScheduler 3.x (scheduling), python-telegram-bot 22.x (alerts), pandas-market-calendars 5.x (NYSE holidays), structlog (structured logs), SQLite stdlib (state), and backtrader2 (backtesting). All broker access reuses the existing `skills/moomooapi` client without modification. The backtester is in v1 scope and must share the strategy and position-management code with the live bot to be meaningful.

The top risks are correctness risks, not infrastructure risks. The three highest-severity pitfalls — acting on incomplete 5m bars (repainting), silently losing OpenD session state mid-day, and order state divergence between bot memory and broker truth — can each cause the bot to take wrong financial actions on paper, and would be catastrophic on real money. All three have known prevention patterns: bar-close gating via timestamp-advance detection, a watchdog polling `get_global_state()` every 60 seconds, and a broker-reconciliation loop every 60–90 seconds. These must be wired in from the start, not retrofitted.

---

## Key Findings

### Recommended Stack

The existing codebase already provides the broker access layer (`moomoo-api` 10.7.6708, `pandas` 3.0.3, Python 3.9+). New additions are narrow and well-justified. APScheduler 3.11.2 is the correct choice for cron-style intraday scheduling — v4.x is explicitly pre-release and must be avoided. The moomoo SDK delivers bar callbacks on its own background thread; the critical thread-safety pattern is to hand off events to the asyncio event loop via `loop.call_soon_threadsafe()` rather than touching asyncio primitives from the SDK thread. backtrader2 (community fork of the abandoned original) is preferred over vectorbt because its event-driven model mirrors the live bot architecture, enabling strategy code reuse; vectorbt's vectorized model cannot share code with a bar-by-bar live system. A critical compatibility note: backtrader2 may conflict with pandas 3.x — the safest approach is a separate virtualenv pinned to pandas 2.2.x for the backtester.

**Core technologies:**
- APScheduler 3.11.2: cron-triggered premarket/force-close/EOD jobs — v3.x stable; v4.x pre-release (avoid)
- python-telegram-bot 22.8: fire-and-forget async push alerts — de-facto standard, async-native
- pandas-market-calendars 5.4.0: NYSE holiday/half-day detection — `valid_days()` + `schedule()` handle all edge cases
- zoneinfo (stdlib): US Eastern datetime handling — replaces pytz for Python 3.9+; handles DST automatically
- SQLite (stdlib sqlite3): durable state for positions, stops, and scan results — zero infrastructure, ACID writes
- structlog 26.1.0: structured JSON-ready logs — contextvars integration works across asyncio boundary
- backtrader2 1.9.76.123: event-driven 5m bar backtester — strategy code reusable from live bot
- supervisor 4.3.0: process supervision on macOS — simpler than launchd for developer-run service

### Expected Features

The feature surface is defined by the requirements for a safe, correct automated order-placing system. Durable state persistence is the architectural foundation — every safety feature (duplicate prevention, startup reconciliation, position lifecycle) depends on it. The position lifecycle manager is the highest-complexity component: a per-position FSM with five distinct states (AWAITING_FILL, ACTIVE, PARTIAL_TAKEN, BREAKEVEN, TRAILING) that must survive restarts and handle partial fills correctly.

**Must have (table stakes — all required for v1):**
- Durable state persistence (SQLite, atomic writes) — every safety feature depends on this
- Startup position reconciliation against broker — must precede any signal processing
- Duplicate-order prevention (symbol-level position guard) — broker-verified, not just state-verified
- Paper-trading environment lock (assert SIMULATE, hard-exit if REAL) — safety gate
- Position lifecycle manager (stop, partial at 0.75R, breakeven at 1.0R, 5m swing-low trail) — the product
- EOD force-close at 15:51 ET (calendar-aware for half-days) — intraday strategy requirement
- Risk-rule enforcement at order submission time (reads live equity, not cached) — gates every order
- ET timezone correctness throughout — use zoneinfo, never naive datetime or fixed UTC offset
- Market-hours / holiday gate via pandas-market-calendars — run before every scan
- Kill switch (file-touch or SIGINT) + graceful shutdown with state flush
- Trade/order audit log (JSONL) extending existing `~/.futu_trade_audit.jsonl` pattern
- Structured application logging (structlog rotating file)
- Telegram entry/exit alerts (fire-and-forget; alert failure must never block trade loop)
- Daily Telegram summary (aggregated after force-close)
- Backtester with historical 5m data replay (in-scope v1 per PROJECT.md)

**Should have (v1.x after core is stable):**
- Daily max-loss circuit breaker (halt new entries if session PnL < -2%)
- Per-trade R-multiple tracking computed from audit log
- Richer Telegram exit alerts (R achieved, stop level, exit rule triggered)
- Backtest performance report (win rate, profit factor, max drawdown, per-trade CSV)
- Health-check heartbeat log (file-touch every N minutes)

**Defer (v2+):**
- System-health Telegram alerts (OpenD down, scan-missed) — requires separate monitoring loop
- Real-money trading path — only after explicit safety audit; no `TrdEnv.REAL` code in this milestone

### Architecture Approach

The system uses a single asyncio event loop with an event-driven internal bus. Components communicate exclusively via typed dataclass events (`BarEvent`, `SignalEvent`, `OrderIntent`, `FillEvent`, `AlertEvent`) enqueued in asyncio queues — no direct cross-component method calls except through the queue. The moomoo SDK's background callback thread is the only cross-boundary point, bridged via `loop.call_soon_threadsafe()`. The strategy computation (`StrategyCore` ABC + `TrendJoinLong` implementation + pure indicator functions) is kept in `bot/strategy/` with zero I/O dependencies, making it importable by the backtester unchanged. Stop management is implemented in the bot (not delegated to broker-side stop orders) because the 5m swing-low trailing pattern cannot be expressed as a native broker order type, and paper-account stop order support is unreliable.

**Major components:**
1. **MoomooGateway** — persistent OpenQuoteContext + OpenSecTradeContext; thin typed wrapper over existing `skills/moomooapi/scripts/common.py`; all broker I/O runs through `run_in_executor`
2. **StateStore** — SQLite persistence; positions, trades, daily_scan, bar_cache tables; atomic writes; loaded on startup before any signal processing
3. **Scanner** — premarket snapshot-based daily filter (D1/D2/D3); no subscriptions needed; outputs watchlist
4. **BarAggregator** — `CurKlineHandlerBase` subclass; detects bar close via timestamp advance; bridges SDK thread to asyncio queue
5. **SignalEngine** — intraday I1/I2/I3 filter on closed 5m bars; emits `SignalEvent`; gated by entry window and max-positions check
6. **RiskEngine** — sizes each trade (1% risk, 10% max notional, 5 max concurrent); computes initial stop price; reads live equity
7. **PositionManager + PositionState FSM** — per-position state machines; processes fills and bar events; emits `OrderIntent` to ExecutionEngine; persists every transition
8. **ExecutionEngine** — translates `OrderIntent` to moomoo API calls; manages pending-order TTL with cancel-replace
9. **TelegramAlerter** — fire-and-forget async push; failures logged but never propagated to trade loop
10. **TradingBot (Scheduler)** — APScheduler AsyncIOScheduler orchestrating daily lifecycle; reconciliation loop every 60-90 seconds
11. **Backtester** — sibling package importing `bot/strategy/` and `bot/position/` only; SimulatedBarFeed + SimulatedExecution; produces trade log + metrics

### Critical Pitfalls

1. **Signal on incomplete 5m bar (repainting)** — Detect bar close exclusively via timestamp advance in `BarAggregator`; never evaluate entry conditions mid-bar; in the backtester enter at bar N+1 open, not bar N close. This is the most common cause of live vs backtest divergence.

2. **OpenD session expiry kills subscriptions silently** — Run a watchdog polling `get_global_state()` every 60 seconds; on failure pause order placement, send Telegram alert, attempt reconnect with backoff. The SDK does not throw on disconnect — it just stops delivering push data.

3. **Order state divergence / ghost positions** — Run a broker-reconciliation loop every 60-90 seconds calling `get_portfolio()` + `get_orders()` and diffing against in-memory state; broker truth wins. Paper account push is explicitly documented as unreliable.

4. **RVOL look-ahead bias** — RVOL denominator must use strictly prior completed trading days (`end = prior_close_date`). For intraday RVOL, compare cumulative volume at time T (time-of-day normalized), not total daily volume.

5. **Non-atomic state writes** — Write to temp file then `os.replace()` atomically; validate before replacing; on corrupt state at startup, reconstruct from broker via `get_portfolio()` and alert operator.

6. **Paper order type limitations** — Use limit orders at aggressive prices for all exits; never rely on market order fills on paper. Implement pending-order TTL: cancel-replace if not filled within 30 seconds.

7. **Subscription quota exhaustion** — Narrow to 5-30 candidates via snapshot-based premarket filter (no quota cost) before subscribing any K_5M feeds.

---

## Implications for Roadmap

Based on the architecture's explicit build-order guidance and the feature dependency graph, a six-phase bottom-up structure is recommended. Each phase produces a runnable, testable artifact before the next layer is added.

### Phase 1: Foundation — Gateway, State, Strategy Core

**Rationale:** Every other component depends on broker access (MoomooGateway), durable state (StateStore), and strategy logic (StrategyCore + TrendJoinLong + indicators). These have zero inter-dependencies and are independently testable. Building them first eliminates the most blocking dependencies.

**Delivers:** Working broker connection layer (reusing `skills/moomooapi`), SQLite state schema with atomic writes, and pure-logic strategy functions unit-tested against synthetic DataFrames.

**Addresses:** Durable state persistence, ET timezone correctness, paper-trading environment lock, OpenD connectivity check at startup.

**Avoids:** Non-atomic state writes (design right from day one); SIMULATE assertion (hardcoded in gateway from day one).

**Research flag:** Standard patterns — skip research-phase.

### Phase 2: Premarket Scanner

**Rationale:** First end-to-end path through MoomooGateway + StrategyCore daily filters. Validates the market-calendar gate and batch snapshot API usage. No subscriptions needed — operates via snapshot calls only.

**Delivers:** Runnable premarket scan producing a filtered watchlist (D1/D2/D3 filters), stored in StateStore, respecting NYSE holidays/half-days. RVOL baseline precomputed with correct date cutoff.

**Addresses:** Market-hours / holiday gate, idempotent daily scan, RVOL baseline precomputation, subscription quota management.

**Avoids:** Subscription quota exhaustion; RVOL look-ahead bias; market calendar blindness; API rate limit violations.

**Research flag:** Light research needed on `get_plate_stock()` for S&P 500 constituent fetch and snapshot batch limits.

### Phase 3: Intraday Signal Engine + Risk Engine

**Rationale:** With scanner and bar delivery working, wire the 5m bar loop. BarAggregator is the critical thread-boundary component. SignalEngine and RiskEngine emit `OrderIntent` events but no orders are placed yet — this phase is signal production only.

**Delivers:** Bar-by-bar evaluation of intraday entry conditions with correct bar-close gating, trade sizing computation, and `OrderIntent` emission verified by logging.

**Addresses:** ET timezone correctness in entry window gating (10:05-15:30), risk-rule enforcement (reads live equity), RVOL intraday calculation (time-of-day normalized).

**Avoids:** Signal on incomplete bar (repainting) — the defining correctness challenge of this phase.

**Research flag:** Needs research-phase for bar-close detection behavior during subscription reconnect mid-bar, and snapshot field availability for HOD/premarket-high.

### Phase 4: Order & Position Management

**Rationale:** Highest-complexity phase. The PositionState FSM, ExecutionEngine, startup reconciliation, duplicate-order prevention, and partial-fill handling all belong here. Unit-test the FSM against synthetic events before wiring broker. The reconciliation loop must be built here — not deferred.

**Delivers:** Full position lifecycle (initial stop, partial at 0.75R, breakeven at 1.0R, 5m swing-low trail, EOD force-close at 15:51 ET). Startup reconciliation. Duplicate-order prevention. Pending-order TTL with cancel-replace. Per-position audit trail.

**Addresses:** Position lifecycle manager, EOD force-close, duplicate-order prevention, startup position reconciliation, kill switch + graceful shutdown, trade/order audit log.

**Avoids:** Order state divergence / ghost positions (reconciliation loop); partial fill state corruption (all quantities from `filled_qty`); paper order rejection (limit orders + TTL).

**Research flag:** Needs research-phase. Paper account order flow behavior (push reliability, fill model, stop order support) must be validated empirically against the actual SIMULATE environment.

### Phase 5: Service Orchestration & Reliability

**Rationale:** With all functional components proven, harden the service: scheduling, OpenD watchdog, Telegram alerts, structured logging, supervisor, and graceful shutdown. Turns a working loop into an operator-ready tool.

**Delivers:** Fully scheduled long-running service with Telegram push alerts for all trade events, daily Telegram summary, structured rotating log, OpenD connectivity watchdog, and clean signal handling.

**Addresses:** Telegram entry/exit alerts, daily Telegram summary, structured application logging, OpenD watchdog, graceful shutdown, supervisor process management.

**Avoids:** OpenD session expiry silently killing subscriptions; timezone/DST bugs; market calendar blindness on half-days (calendar-aware force-close time).

**Research flag:** Standard patterns — APScheduler AsyncIOScheduler + supervisor are well-documented. Skip research-phase.

### Phase 6: Backtester

**Rationale:** Imports `bot/strategy/` and `bot/position/` unchanged. Building last ensures those modules are stable. Any discrepancy between backtest and live behavior reveals a code-sharing gap, not an architecture flaw.

**Delivers:** Offline CLI backtester replaying historical 5m OHLCV through the exact same StrategyCore and PositionState FSM as the live bot. Performance report: win rate, avg R, max drawdown, profit factor, per-trade CSV.

**Addresses:** Backtester with historical 5m data replay, backtest performance report, point-in-time data loading (no look-ahead bias), entry at bar N+1 open.

**Avoids:** Backtest look-ahead bias on gap/SMA200/premarket high; RVOL look-ahead; survivorship bias (documented limitation, date-stamped constituent list, conservative haircut).

**Research flag:** Needs research on Moomoo API historical 5m data quota and date-range limits for 500+ symbols. Alternative flat-file data sources may be needed.

### Phase Ordering Rationale

- Bottom-up dependency order: Foundation unblocks all others; Scanner validates data path before live subscriptions; Signal/Risk validates bar processing before orders are placed; Position Management is isolated and testable before scheduling is layered on.
- FSM complexity in Phase 4 is contained to a well-isolated package testable with synthetic events before any broker wire-up.
- Backtester is deliberately last to ensure strategy code is stable before depending on it.
- Safety features (environment lock, durable state, reconciliation) appear in the earliest relevant phases — never deferred.

### Research Flags

Phases needing deeper research during planning:
- **Phase 3 (Signal Engine):** Bar-close detection during subscription reconnect mid-bar; snapshot field availability for HOD/premarket-high at candidate list scale.
- **Phase 4 (Order & Position Management):** Highest-risk phase. Paper account order flow behavior requires empirical validation against the SIMULATE environment before detailed task planning.
- **Phase 6 (Backtester):** Historical 5m data availability and quota for Moomoo API at multi-year / 500-symbol scale is unclear. Alternative data sources may be needed.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** SQLite, zoneinfo, and MoomooGateway reuse are all well-documented.
- **Phase 2 (Scanner):** Snapshot batch calls and pandas-market-calendars are fully documented.
- **Phase 5 (Orchestration):** APScheduler AsyncIOScheduler, supervisor, structlog, and python-telegram-bot fire-and-forget are thoroughly documented.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified via PyPI; patterns verified via official docs and Context7. One gap: backtrader2 vs pandas 3.x compatibility requires testing; separate virtualenv recommended. |
| Features | HIGH | Cross-verified against Freqtrade, Lumibot, FIA best-practices guidance, and practitioner literature. Feature dependencies are explicit and internally consistent. |
| Architecture | HIGH | Core patterns (event-driven bus, FSM, StrategyCore ABC) verified against QuantStart series, Freqtrade architecture, and official Moomoo API docs. |
| Pitfalls | HIGH | Critical pitfalls verified against official Moomoo API docs (OpenD FAQ, Quote FAQ, Transaction FAQ), confirmed by codebase CONCERNS.md and INTEGRATIONS.md audits. |

**Overall confidence:** HIGH

### Gaps to Address

- **backtrader2 + pandas 3.x compatibility**: Test before Phase 6. If incompatible, use a separate virtualenv pinned to pandas 2.2.x for the backtester. Default plan is a separate env.
- **Historical 5m data quota and availability**: Moomoo API limits on historical kline data (date range, symbols per call, daily quota) not fully specified. Validate during Phase 6 planning; have flat-file CSV from a data vendor as fallback.
- **Paper account push reliability in practice**: Research confirms push is documented as unreliable for US paper accounts but actual failure rate under normal conditions is unknown. Reconciliation loop (Phase 4) is the mitigation; 60-second polling interval may need tuning.
- **S&P 500 constituent list source for backtesting**: `get_plate_stock("US.S&P500")` returns current members only. Point-in-time historical constituent lists (CRSP, open-source GitHub snapshots) need accessibility validation.

---

## Sources

### Primary (HIGH confidence)
- Moomoo OpenAPI official docs (openapi.moomoo.com) — CurKlineHandlerBase pattern, subscription quota, paper trading limitations, OpenD FAQ, transaction FAQ, authorities and quota tiers
- APScheduler v3 docs (Context7) — BackgroundScheduler, AsyncIOScheduler, CronTrigger, timezone handling
- python-telegram-bot docs (Context7) — standalone Bot usage, async send_message without polling loop
- pandas-market-calendars docs (Context7) — `valid_days()`, `schedule()`, NYSE calendar, early-close handling
- QuantStart event-driven backtesting series — event queue pattern, live/backtest parity architecture
- PyPI JSON APIs — confirmed package versions for all dependencies

### Secondary (MEDIUM confidence)
- Freqtrade architecture (DeepWiki synthesis) — IStrategy unified interface, SQLite trade persistence, startup reconciliation, kill switch
- Lumibot framework docs — paper-to-live architecture, Telegram integration patterns
- TradingView Pine Script repainting docs — incomplete bar signal problem formally described
- FIA Automated Trading Risk Controls whitepaper — kill switch, duplicate prevention, circuit breaker design
- structlog official best practices — contextvars, asyncio boundary, JSON output
- Internal codebase audits (CONCERNS.md, INTEGRATIONS.md) — confirmed existing gaps (no reconciliation, unreliable push, bare exception handlers)

### Tertiary (LOW confidence)
- Backtest survivorship bias for S&P 500 (riazarbi.github.io) — historical constituent list sourcing; accessibility unconfirmed
- WebSearch results on APScheduler vs schedule library, backtrader vs vectorbt comparisons — community consensus; confirmed by primary sources where possible

---
*Research completed: 2026-06-23*
*Ready for roadmap: yes*
