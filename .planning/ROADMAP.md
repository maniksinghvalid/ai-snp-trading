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

**Data/execution split (cross-cutting):** scan and backtest market data come from a free
external source (**yfinance**) to avoid Moomoo snapshot/kline quota; Moomoo/OpenD is reserved
for order execution, account/position truth, and live intraday 5m subscriptions on the (capped)
watchlist. All strategy parameters live in a single **`rules.json`** config (CFG-01) read by
both the live bot and the backtester.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Gateway, StateStore, StrategyCore, and safety primitives that everything else depends on
- [x] **Phase 2: Premarket Scanner** - Daily watchlist generation via S&P 500 constituent fetch and D1/D2/D3 filters (completed 2026-06-23)
- [x] **Phase 3: Intraday Signal and Risk Engine** - 5m bar loop with bar-close gating, intraday filters, and position sizing (completed 2026-06-24)
- [ ] **Phase 4: Order and Position Management** - Full position lifecycle FSM, order execution, reconciliation, and EOD force-close
- [ ] **Phase 5: Service Orchestration and Reliability** - Scheduler, OpenD watchdog, Telegram alerts, and structured logging
- [ ] **Phase 6: Backtester** - Offline historical replay through the shared strategy and FSM code

## Phase Details

### Phase 1: Foundation

**Goal**: The broker access layer, durable state store, and pure strategy logic are in place — independently tested — and the environment safety gate is enforced at startup
**Depends on**: Nothing (first phase)
**Requirements**: CFG-01, STATE-01, SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05, SVC-03, SVC-04
**Success Criteria** (what must be TRUE):

  1. `MoomooGateway` connects to OpenD on 127.0.0.1:11111 and the hard paper guard (SAFE-01) passes only when `PAPER_TRADING=true` AND the selected account's environment asserts SIMULATE; a REAL account or a flag/account mismatch hard-exits before any order path is reachable
  2. `StateStore` creates and migrates the SQLite schema; atomic writes (temp-file + `os.replace`) pass a crash-injection test without corruption
  3. `TrendJoinLong` indicator functions (SMA200, RVOL, swing_low_2_2, D1/D2/D3 filters) pass unit tests against synthetic DataFrames with no network calls
  6. `rules.json` (CFG-01) loads and validates at startup; every strategy constant (filters, time gates, exit rules, risk + `max_trades_per_day`) is read from it — a unit test confirms no strategy literal is hardcoded in `StrategyCore`, and a malformed/missing config fails fast with a clear error
  4. Startup reconciliation skeleton (SAFE-02) and the 60–90s reconciliation loop (SAFE-03) are wired into the gateway layer; broker truth overrides in-memory state
  5. Kill switch (file-touch and SIGINT) triggers a clean shutdown with a state flush; the append-only JSONL audit log records every event without overwriting prior entries

**Plans**: 4 plans
Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Scaffolding + MoomooGateway (persistent contexts, run_in_executor, pre-flight) + hard paper guard (SAFE-01) + reconciliation skeletons (SAFE-02/03) + JSONL audit log (SAFE-05) [Wave 1] *(2026-06-23, 3 tasks, 54 tests, 8 min)*

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — StateStore: SQLite migration 0001 (full v1 schema via PRAGMA user_version) + atomic temp-file/os.replace writes with crash-injection test (STATE-01) [Wave 2] *(2026-06-23, 2 tasks, 49 tests, 4 min)*
- [x] 01-03-PLAN.md — rules.json + jsonschema loader → StrategyConfig + StrategyCore ABC + TrendJoinLong + pure indicators (SMA200/RVOL/swing_low_2_2); config-driven, no I/O (CFG-01) [Wave 2] *(2026-06-23, 3 tasks, 64 tests, 7 min)*
- [x] 01-04-PLAN.md — Safety primitives: zoneinfo ET helpers (SVC-04), structlog rotating logger (SVC-03), kill switch (file-touch + SIGINT) → state flush + audit (SAFE-04/05) [Wave 2] *(2026-06-23, 2 tasks, 39 tests, 3 min)*

### Phase 2: Premarket Scanner

**Goal**: The bot can run a daily premarket scan (and intraday re-scans) against the full S&P 500 universe using yfinance daily-bar data, apply all daily filters, and persist an idempotent, top-20-capped watchlist in StateStore
**Depends on**: Phase 1
**Requirements**: SCAN-01, SCAN-02, SCAN-03, SCAN-04, SCAN-05, SCAN-06, SCAN-07, SCAN-08, SIG-01
**Success Criteria** (what must be TRUE):

  1. Running the scanner on a NYSE trading day fetches the current S&P 500 constituent list and applies D1 (above prior-day high), D2 (prior close above SMA200), and D3 (gap ≥ 3% from prior close, price ≥ $3) filters, producing a watchlist written to StateStore
  2. RVOL 14-day baseline is computed using only completed prior trading days (no look-ahead); the date cutoff is verified by replaying a known date
  3. Calling the scanner twice on the same date does not duplicate the watchlist — the second call is a no-op for already-present candidates; intraday re-scan passes update the watchlist idempotently (idempotency confirmed via StateStore record count)
  4. The scanner refuses to run on NYSE holidays and half-days (verified with pandas-market-calendars); a test date known to be a holiday produces no output and no error
  5. After the scan, `MoomooGateway.subscribe()` is called only for the capped (top-20) watchlist candidates, not for the full S&P 500 universe, keeping K_5M subscription usage within quota
  6. Scan daily-bar data is pulled from yfinance (SCAN-06) with ticker-format normalization (e.g. `BRK.B`→`BRK-B`) and bounded concurrency (threads≈5); a Yahoo-wide / partial-degradation failure is detected and surfaced (alert/log) rather than silently producing an empty or partial watchlist
  7. The persisted watchlist is capped at the top 20 candidates ranked by gap % (SCAN-08); a test with >20 passing candidates confirms exactly 20 are stored and subscribed

**Resolved (was research flag):** yfinance supplies scan data, removing the Moomoo `get_market_snapshot`/kline batch-quota concern for 500+ codes. Remaining light research: S&P 500 constituent source (Wikipedia scrape vs. hardcoded list refreshed every 2–3 months) and yfinance batch-download reliability/rate behavior.
**Plans**: 4 plans

Plans:
**Wave 1**

- [x] 02-00-PLAN.md — Wave 0 foundation: add yfinance + pandas-market-calendars (package-legitimacy checkpoints), create bot/scanner package + tests/scanner Wave 0 test stubs [Wave 1]

**Wave 2** *(blocked on Wave 1)*

- [x] 02-01-PLAN.md — universe.py (Wikipedia scrape + dated cache/fallback), fetcher.py (yfinance batch, threads=5, 10% degradation gate + audit), calendar.py (NYSE holiday/half-day gate) [Wave 2]

**Wave 3** *(blocked on Wave 2)*

- [x] 02-02-PLAN.md — migration 0002 (daily_scan rich columns, D-08), run_daily_scan (config-driven D1/D2/D3 + no-look-ahead SMA200/RVOL), idempotent upsert, top-20 gap cap [Wave 3]

**Wave 4** *(blocked on Wave 3)*

- [x] 02-03-PLAN.md — MoomooGateway.subscribe() K_5M (SIG-01), subscribe top-20 only, run_intraday_rescan (idempotent merge, protect active candidates D-04) [Wave 4]

### Phase 3: Intraday Signal and Risk Engine

**Goal**: The bot evaluates closed 5m bars against intraday filters, sizes trades correctly using live account equity, and emits verified OrderIntent events — without placing any real orders
**Depends on**: Phase 2
**Requirements**: SIG-02, SIG-03, SIG-04, RISK-01, RISK-02, RISK-03, RISK-04, RISK-05
**Success Criteria** (what must be TRUE):

  1. Bar-close detection operates exclusively via timestamp advance in `BarAggregator`; injecting a sequence of synthetic push events confirms that strategy evaluation fires only when the timestamp changes, never mid-bar
  2. An entry signal is generated only when all three intraday conditions hold simultaneously (price above premarket high, above today's HOD, RVOL ≥ 2.0) and the current time is within the 10:05–15:30 ET window — confirmed by a table-driven test across boundary times
  3. No signal is emitted when 5 concurrent positions are already tracked in PositionManager, when the clock is at or after 15:30 ET, or when the day's new-entry count has already reached `max_trades_per_day` (RISK-05) — the daily cap is enforced independently of the concurrent cap and verified by a test that closes positions then confirms no further entries fire that day
  4. Position sizing reads live account equity from MoomooGateway (not a cached value), risks exactly 1% of equity per trade, and caps notional at 10% of portfolio value; a worked example with known equity produces the expected share count
  5. `RISK-03` initial stop is computed as LOD − 1%; the OrderIntent logged to structlog contains the correct stop price and quantity for each synthetic signal

**Resolved (was research flag):** bar-close detection during subscription reconnect is handled by a `time_key`-advance + session-level `_seen_time_keys` dual-guard in `BarAggregator` (never double-fires / replays a partial bar); snapshot field availability confirmed — premarket high = `pre_high_price`, equity = `accinfo_query.total_assets` (03-RESEARCH.md, HIGH confidence).
**Plans**: 3 plans (3 waves)

Plans:
**Wave 1**

- [x] 03-01-PLAN.md — Wave 0 scaffold (bot/signal + bot/risk packages, BarEvent/SignalEvent/OrderIntent dataclasses, migration 0003 daily_trade_count + pending_intents, failing test stubs) + BarAggregator (CurKlineHandlerBase subclass, time_key-advance bar-close detection with reconnect dedup, HOD/LOD running max/min, SDK-thread→asyncio bridge) [SIG-02] [Wave 1] *(2026-06-24, 3 tasks, 14 files, 11 min)*

**Wave 2** *(blocked on 03-01)*

- [x] 03-02-PLAN.md — SignalEngine: I1/I2/I3 feed of passes_intraday_filters() + 10:05–15:30 ET entry-window gate (inclusive/exclusive boundaries) + 5-concurrent-position cap (broker-truth get_positions) + daily new-entry cap (filled+pending gating, D-08/D-09); emits SignalEvent [SIG-03, SIG-04, RISK-04, RISK-05] [Wave 2]

**Wave 3** *(blocked on 03-01, 03-02)*

- [x] 03-03-PLAN.md — MoomooGateway.get_equity() (live accinfo_query total_assets, $100k fallback) + RiskEngine: live-equity 1%-risk sizing, 10%-notional cap (take smaller), LOD−1% stop via compute_initial_stop(), round shares DOWN / <1-share→no-intent, emit OrderIntent logged to structlog AND persisted to pending_intents [RISK-01, RISK-02, RISK-03] [Wave 3]

### Phase 4: Order and Position Management

**Goal**: The bot can place entries, manage the full per-position lifecycle (partial, breakeven, trailing stop), force-close at 15:51 ET, and recover correctly from a mid-session restart
**Depends on**: Phase 3
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-05, POS-01, POS-02, POS-03, POS-04, POS-05
**Success Criteria** (what must be TRUE):

  1. Orders are placed exclusively against the paper (SIMULATE) account; a limit order entry is submitted via MoomooGateway and a FillEvent is recorded in StateStore and the JSONL audit log
  2. The PositionState FSM transitions correctly through all stages: AWAITING_FILL → ACTIVE → PARTIAL_TAKEN (⅓ off at 0.75R) → BREAKEVEN (stop moved to entry at 1.0R) → TRAILING (5m swing-low trail) → CLOSED; each transition is covered by a unit test using synthetic FillEvents and BarEvents
  3. A pending order that is unfilled within the TTL is cancelled and re-submitted as a cancel-replace; verified against the paper account
  4. Duplicate-order prevention is broker-verified: placing a second entry for a symbol that already has an open position is blocked by a `get_positions()` check, not just an in-memory guard
  7. Stop-out and exit-fill detection matches broker fills by `order_id`, never by quantity (EXEC-05); a test that takes a ⅓ partial then checks the remaining stop confirms the partial is not misread as a full stop-out
  5. All open positions are force-closed at 15:51 ET (calendar-aware for half-days); a simulated half-day test confirms the correct earlier force-close time
  6. After a simulated restart with positions in StateStore, the bot reconstructs all PositionState objects, re-subscribes bar feeds for those codes, and resumes stop management without re-entering any position

**Research flag**: Needs research-phase. Paper account order flow behavior (push reliability, fill model, stop order support) must be validated empirically against the SIMULATE environment before task planning.
**Plans**: TBD

Plans:

- [ ] 04-01: PositionState FSM — five states, on_bar() transitions, all exit rules; unit-tested with synthetic events
- [ ] 04-02: PositionManager — owns all PositionState objects, processes fills and bar events, persists every transition
- [ ] 04-03: ExecutionEngine — OrderIntent to moomoo API translation, limit orders, TTL with cancel-replace, order_id-keyed fill reconciliation (EXEC-05), FillEvent emission
- [ ] 04-04: Startup reconciliation, duplicate-order guard, EOD force-close (calendar-aware), kill switch state flush

### Phase 5: Service Orchestration and Reliability

**Goal**: The bot runs hands-off as a long-running supervised service: the daily schedule fires automatically, Telegram push alerts cover every trade event, OpenD loss is detected and handled gracefully, and structured logs enable post-hoc diagnosis
**Depends on**: Phase 4
**Requirements**: SVC-01, SVC-02, SCAN-07, ALERT-01, ALERT-02, ALERT-03, ALERT-04, DASH-01
**Success Criteria** (what must be TRUE):

  1. APScheduler AsyncIOScheduler fires the premarket scan job, the intraday re-scan jobs (~every 30 min, ≈7 passes through midday — SCAN-07), the market-open subscription job, and the EOD force-close job at the correct ET times on a trading day; verified by running the service for one full paper-trading session end-to-end
  2. OpenD watchdog polls `get_global_state()` every 60 seconds; simulating an OpenD disconnect (kill process) causes order placement to pause within one poll cycle and a Telegram alert to fire (or log if Telegram is unconfigured)
  3. A Telegram alert containing ticker, size, entry price, and initial stop fires on every entry; exit alerts (partial, breakeven, trail, stop-out, force-close) fire on every exit event; all alerts are fire-and-forget with failures logged but never propagating to the trade loop
  4. The daily Telegram summary is sent after the 15:51 ET force-close and includes trade count, win/loss split, realized PnL, and open risk
  5. The service starts under supervisor, restarts automatically on crash, and structured rotating log files are written by structlog in JSON-compatible format
  6. A static, offline, no-JS HTML dashboard (DASH-01) is generated alongside the daily summary — R-multiple histogram, open-positions table, and last-20 closed trades — and renders correctly from a file:// open with no server

**Plans**: TBD

Plans:

- [ ] 05-01: TradingBot orchestrator — APScheduler AsyncIOScheduler, cron jobs (premarket scan, intraday re-scans ~30 min, market-open subscribe, force-close, EOD report), component wiring
- [ ] 05-02: OpenD connectivity watchdog — `get_global_state()` poll loop, order-pause on failure, reconnect with backoff
- [ ] 05-03: TelegramAlerter — fire-and-forget async push, entry/exit/summary alerts, failure isolation from trade loop
- [ ] 05-04: Daily P&L report + static HTML dashboard (DASH-01, R-multiple histogram); supervisor process config; structured rotating log integration; full daily lifecycle integration test

### Phase 6: Backtester

**Goal**: An offline CLI tool replays historical 5m data through the exact same StrategyCore and PositionState FSM used by the live bot and produces a performance report, confirming live/backtest code parity
**Depends on**: Phase 5
**Requirements**: BT-01, BT-02, BT-03, BT-04
**Success Criteria** (what must be TRUE):

  1. Running `backtester/run.py` against a known historical 5m dataset imports `bot/strategy/` and `bot/position/` unchanged — no modifications to live code are required — and reads the same `rules.json` config as the live bot
  2. Entries occur at bar N+1 open (not bar N close); gap, SMA200, premarket high, and RVOL are computed point-in-time with no look-ahead; a synthetic dataset with a known ahead-only signal confirms no premature entry
  3. The performance report is written to disk and includes win rate, average R-multiple, max drawdown, profit factor, and a per-trade CSV with entry/exit price, quantity, and exit reason
  4. Historical 5m data is sourced from yfinance (or a flat CSV/Parquet export) rather than Moomoo (BT-04), avoiding broker historical-quota limits; the loader handles yfinance's 5m date-range window (≈60 days) and the strategy's ticker-format normalization

**Resolved (was research flag):** historical data comes from yfinance/flat-file (BT-04), not Moomoo, removing the broker historical-quota concern. Remaining light research at Phase 6 planning: yfinance 5m history window/limits and whether a Parquet cache is needed for repeated multi-symbol backtests.
**Plans**: TBD

Plans:

- [ ] 06-01: SimulatedBarFeed — loads historical 5m data from yfinance/CSV/Parquet (BT-04); replays bars as BarEvents in chronological order
- [ ] 06-02: SimulatedExecution — fills at bar N+1 open with configurable slippage; replaces MoomooGateway
- [ ] 06-03: Backtester harness — wires feed through SignalEngine + RiskEngine + PositionManager (shared classes, shared `rules.json`); produces trade log
- [ ] 06-04: Performance report — win rate, avg R, max drawdown, profit factor, per-trade CSV output

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/4 | In progress | - |
| 2. Premarket Scanner | 4/4 | Complete   | 2026-06-23 |
| 3. Intraday Signal and Risk Engine | 3/3 | Complete   | 2026-06-24 |
| 4. Order and Position Management | 0/4 | Not started | - |
| 5. Service Orchestration and Reliability | 0/4 | Not started | - |
| 6. Backtester | 0/4 | Not started | - |
