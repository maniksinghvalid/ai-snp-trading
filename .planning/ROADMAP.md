# Roadmap: AI S&P Trading Bot (Trend Join Long)

## Overview

The bot is built bottom-up in six phases, each delivering a runnable, testable artifact before
the next layer is added. Phase 1 lays the broker access, durable state, and strategy-logic
foundation that every later phase depends on. Phase 2 adds the premarket scanner as the first
end-to-end data path. Phase 3 wires the 5m bar loop and signal/risk engines. Phase 4
implements the full position lifecycle FSM and order management — the highest-complexity
phase. Phase 5 hardens the long-running service: scheduler, OpenD watchdog, Telegram alerts,
and structured logging. Phase 6 builds the offline backtester that imports the strategy and
position code unchanged from the live bot, validating the strategy on historical data.

Safety features (environment lock, durable state, startup reconciliation, duplicate-order
prevention, kill switch) are wired in at the earliest phase that introduces the risk — never
deferred.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - Gateway, StateStore, StrategyCore, and safety primitives that everything else depends on
- [ ] **Phase 2: Premarket Scanner** - Daily watchlist generation via S&P 500 constituent fetch and D1/D2/D3 filters
- [ ] **Phase 3: Intraday Signal and Risk Engine** - 5m bar loop with bar-close gating, intraday filters, and position sizing
- [ ] **Phase 4: Order and Position Management** - Full position lifecycle FSM, order execution, reconciliation, and EOD force-close
- [ ] **Phase 5: Service Orchestration and Reliability** - Scheduler, OpenD watchdog, Telegram alerts, and structured logging
- [ ] **Phase 6: Backtester** - Offline historical replay through the shared strategy and FSM code

## Phase Details

### Phase 1: Foundation
**Goal**: The broker access layer, durable state store, and pure strategy logic are in place — independently tested — and the environment safety gate is enforced at startup
**Depends on**: Nothing (first phase)
**Requirements**: STATE-01, SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05, SVC-03, SVC-04
**Success Criteria** (what must be TRUE):
  1. `MoomooGateway` connects to OpenD on 127.0.0.1:11111, asserts the SIMULATE environment, and hard-exits if the account is REAL
  2. `StateStore` creates and migrates the SQLite schema; atomic writes (temp-file + `os.replace`) pass a crash-injection test without corruption
  3. `TrendJoinLong` indicator functions (SMA200, RVOL, swing_low_2_2, D1/D2/D3 filters) pass unit tests against synthetic DataFrames with no network calls
  4. Startup reconciliation skeleton (SAFE-02) and the 60–90s reconciliation loop (SAFE-03) are wired into the gateway layer; broker truth overrides in-memory state
  5. Kill switch (file-touch and SIGINT) triggers a clean shutdown with a state flush; the append-only JSONL audit log records every event without overwriting prior entries
**Plans**: TBD

Plans:
- [ ] 01-01: MoomooGateway — persistent contexts, SIMULATE assertion, run_in_executor wrappers, pre-flight connectivity check
- [ ] 01-02: StateStore — SQLite schema, atomic writes, startup load, and reconciliation loop skeleton
- [ ] 01-03: StrategyCore ABC + TrendJoinLong + indicators — pure logic, unit tests, no I/O
- [ ] 01-04: Safety primitives — kill switch, SIGINT handler, state flush, JSONL audit log, structlog rotating logger, zoneinfo ET helpers

### Phase 2: Premarket Scanner
**Goal**: The bot can run a daily premarket scan against the full S&P 500 universe, apply all daily filters, and persist an idempotent watchlist in StateStore
**Depends on**: Phase 1
**Requirements**: SCAN-01, SCAN-02, SCAN-03, SCAN-04, SCAN-05, SIG-01
**Success Criteria** (what must be TRUE):
  1. Running the scanner on a NYSE trading day fetches the current S&P 500 constituent list and applies D1 (above prior-day high), D2 (prior close above SMA200), and D3 (gap ≥ 3% from prior close, price ≥ $3) filters, producing a watchlist written to StateStore
  2. RVOL 14-day baseline is computed using only completed prior trading days (no look-ahead); the date cutoff is verified by replaying a known date
  3. Calling the scanner twice on the same date does not duplicate the watchlist — the second call is a no-op (idempotency confirmed via StateStore record count)
  4. The scanner refuses to run on NYSE holidays and half-days (verified with pandas-market-calendars); a test date known to be a holiday produces no output and no error
  5. After the scan, `MoomooGateway.subscribe()` is called only for watchlist candidates, not for the full S&P 500 universe, keeping K_5M subscription usage within quota

**Research flag**: Light research needed on `get_plate_stock("US.S&P500")` response format and snapshot batch size limits for 500+ codes before task planning.
**Plans**: TBD

Plans:
- [ ] 02-01: S&P 500 constituent fetch and snapshot batch calls (D1/D3 daily filters via snapshot)
- [ ] 02-02: SMA200 via kline history, RVOL baseline computation, idempotent daily_scan persistence
- [ ] 02-03: NYSE market calendar gate (pandas-market-calendars), watchlist-scoped K_5M subscriptions

### Phase 3: Intraday Signal and Risk Engine
**Goal**: The bot evaluates closed 5m bars against intraday filters, sizes trades correctly using live account equity, and emits verified OrderIntent events — without placing any real orders
**Depends on**: Phase 2
**Requirements**: SIG-02, SIG-03, SIG-04, RISK-01, RISK-02, RISK-03, RISK-04
**Success Criteria** (what must be TRUE):
  1. Bar-close detection operates exclusively via timestamp advance in `BarAggregator`; injecting a sequence of synthetic push events confirms that strategy evaluation fires only when the timestamp changes, never mid-bar
  2. An entry signal is generated only when all three intraday conditions hold simultaneously (price above premarket high, above today's HOD, RVOL ≥ 2.0) and the current time is within the 10:05–15:30 ET window — confirmed by a table-driven test across boundary times
  3. No signal is emitted when 5 concurrent positions are already tracked in PositionManager, or when the clock is at or after 15:30 ET
  4. Position sizing reads live account equity from MoomooGateway (not a cached value), risks exactly 1% of equity per trade, and caps notional at 10% of portfolio value; a worked example with known equity produces the expected share count
  5. `RISK-03` initial stop is computed as LOD − 1%; the OrderIntent logged to structlog contains the correct stop price and quantity for each synthetic signal

**Research flag**: Needs research-phase. Bar-close detection during subscription reconnect mid-bar, and snapshot field availability for HOD/premarket-high at candidate list scale, must be confirmed before task planning.
**Plans**: TBD

Plans:
- [ ] 03-01: BarAggregator — CurKlineHandlerBase subclass, timestamp-advance bar-close detection, SDK-thread to asyncio bridge
- [ ] 03-02: SignalEngine — I1/I2/I3 intraday filters, entry window gate, max-positions gate, SignalEvent emission
- [ ] 03-03: RiskEngine — live equity read, 1% risk sizing, 10% notional cap, 5-position concurrent gate, OrderIntent emission

### Phase 4: Order and Position Management
**Goal**: The bot can place entries, manage the full per-position lifecycle (partial, breakeven, trailing stop), force-close at 15:51 ET, and recover correctly from a mid-session restart
**Depends on**: Phase 3
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, POS-01, POS-02, POS-03, POS-04, POS-05
**Success Criteria** (what must be TRUE):
  1. Orders are placed exclusively against the paper (SIMULATE) account; a limit order entry is submitted via MoomooGateway and a FillEvent is recorded in StateStore and the JSONL audit log
  2. The PositionState FSM transitions correctly through all stages: AWAITING_FILL → ACTIVE → PARTIAL_TAKEN (⅓ off at 0.75R) → BREAKEVEN (stop moved to entry at 1.0R) → TRAILING (5m swing-low trail) → CLOSED; each transition is covered by a unit test using synthetic FillEvents and BarEvents
  3. A pending order that is unfilled within the TTL is cancelled and re-submitted as a cancel-replace; verified against the paper account
  4. Duplicate-order prevention is broker-verified: placing a second entry for a symbol that already has an open position is blocked by a `get_positions()` check, not just an in-memory guard
  5. All open positions are force-closed at 15:51 ET (calendar-aware for half-days); a simulated half-day test confirms the correct earlier force-close time
  6. After a simulated restart with positions in StateStore, the bot reconstructs all PositionState objects, re-subscribes bar feeds for those codes, and resumes stop management without re-entering any position

**Research flag**: Needs research-phase. Paper account order flow behavior (push reliability, fill model, stop order support) must be validated empirically against the SIMULATE environment before task planning.
**Plans**: TBD

Plans:
- [ ] 04-01: PositionState FSM — five states, on_bar() transitions, all exit rules; unit-tested with synthetic events
- [ ] 04-02: PositionManager — owns all PositionState objects, processes fills and bar events, persists every transition
- [ ] 04-03: ExecutionEngine — OrderIntent to moomoo API translation, limit orders, TTL with cancel-replace, FillEvent emission
- [ ] 04-04: Startup reconciliation, duplicate-order guard, EOD force-close (calendar-aware), kill switch state flush

### Phase 5: Service Orchestration and Reliability
**Goal**: The bot runs hands-off as a long-running supervised service: the daily schedule fires automatically, Telegram push alerts cover every trade event, OpenD loss is detected and handled gracefully, and structured logs enable post-hoc diagnosis
**Depends on**: Phase 4
**Requirements**: SVC-01, SVC-02, ALERT-01, ALERT-02, ALERT-03, ALERT-04
**Success Criteria** (what must be TRUE):
  1. APScheduler AsyncIOScheduler fires the premarket scan job, market-open subscription job, and EOD force-close job at the correct ET times on a trading day; verified by running the service for one full paper-trading session end-to-end
  2. OpenD watchdog polls `get_global_state()` every 60 seconds; simulating an OpenD disconnect (kill process) causes order placement to pause within one poll cycle and a Telegram alert to fire (or log if Telegram is unconfigured)
  3. A Telegram alert containing ticker, size, entry price, and initial stop fires on every entry; exit alerts (partial, breakeven, trail, stop-out, force-close) fire on every exit event; all alerts are fire-and-forget with failures logged but never propagating to the trade loop
  4. The daily Telegram summary is sent after the 15:51 ET force-close and includes trade count, win/loss split, realized PnL, and open risk
  5. The service starts under supervisor, restarts automatically on crash, and structured rotating log files are written by structlog in JSON-compatible format

**Plans**: TBD

Plans:
- [ ] 05-01: TradingBot orchestrator — APScheduler AsyncIOScheduler, cron jobs (premarket, market-open, force-close, EOD), component wiring
- [ ] 05-02: OpenD connectivity watchdog — `get_global_state()` poll loop, order-pause on failure, reconnect with backoff
- [ ] 05-03: TelegramAlerter — fire-and-forget async push, entry/exit/summary alerts, failure isolation from trade loop
- [ ] 05-04: Supervisor process config, structured rotating log integration, full daily lifecycle integration test

### Phase 6: Backtester
**Goal**: An offline CLI tool replays historical 5m data through the exact same StrategyCore and PositionState FSM used by the live bot and produces a performance report, confirming live/backtest code parity
**Depends on**: Phase 5
**Requirements**: BT-01, BT-02, BT-03
**Success Criteria** (what must be TRUE):
  1. Running `backtester/run.py` against a known historical 5m CSV dataset imports `bot/strategy/` and `bot/position/` unchanged — no modifications to live code are required
  2. Entries occur at bar N+1 open (not bar N close); gap, SMA200, premarket high, and RVOL are computed point-in-time with no look-ahead; a synthetic dataset with a known ahead-only signal confirms no premature entry
  3. The performance report is written to disk and includes win rate, average R-multiple, max drawdown, profit factor, and a per-trade CSV with entry/exit price, quantity, and exit reason

**Research flag**: Needs research on Moomoo API historical 5m data quota and date-range limits for 500+ symbols. Alternative flat-file data sources (CSV/Parquet from a data vendor) may be needed; validate during Phase 6 planning before committing to a data fetch strategy.
**Plans**: TBD

Plans:
- [ ] 06-01: SimulatedBarFeed — loads historical 5m CSV/Parquet; replays bars as BarEvents in chronological order
- [ ] 06-02: SimulatedExecution — fills at bar N+1 open with configurable slippage; replaces MoomooGateway
- [ ] 06-03: Backtester harness — wires feed through SignalEngine + RiskEngine + PositionManager (shared classes); produces trade log
- [ ] 06-04: Performance report — win rate, avg R, max drawdown, profit factor, per-trade CSV output

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 0/4 | Not started | - |
| 2. Premarket Scanner | 0/3 | Not started | - |
| 3. Intraday Signal and Risk Engine | 0/3 | Not started | - |
| 4. Order and Position Management | 0/4 | Not started | - |
| 5. Service Orchestration and Reliability | 0/4 | Not started | - |
| 6. Backtester | 0/4 | Not started | - |
