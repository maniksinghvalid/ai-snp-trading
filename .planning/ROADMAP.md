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
- [ ] **Phase 3: Intraday Signal and Risk Engine** - 5m bar loop with bar-close gating, intraday filters, and position sizing (all plans executed 2026-06-24; pending live SIMULATE UAT)
- [x] **Phase 4: Order and Position Management** - Full position lifecycle FSM, order execution, reconciliation, and EOD force-close (completed 2026-06-24; docs recovered 2026-07-06 after ec826eb stripped them from develop)
- [x] **Phase 5: Service Orchestration and Reliability** - Scheduler, OpenD watchdog, Telegram alerts, and structured logging (completed 2026-06-24; docs recovered 2026-07-06 after ec826eb stripped them from develop; 3/6 UAT tests blocked pending live-session exercise: watchdog disconnect, launchd supervision, full-day scheduler timing)
- [ ] **Phase 6: Backtester** - Offline historical replay through the shared strategy and FSM code
- [ ] **Phase 7: Strategy Optimization** - Four structural strategy changes from quant feedback: RVOL-TOD gate, exit restructure (backtest-gated), tick-level stop invalidation, -2R daily circuit breaker

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
**Plans**: 4/4 plans executed

Plans:

- [x] 04-01: PositionState FSM — five states, on_bar() transitions, all exit rules; unit-tested with synthetic events ✅ 2026-06-24
- [x] 04-02: PositionManager — owns all PositionState objects, processes fills and bar events, persists every transition ✅ 2026-06-24
- [x] 04-03: ExecutionEngine — OrderIntent to moomoo API translation, limit orders, TTL with cancel-replace, order_id-keyed fill reconciliation (EXEC-05), FillEvent emission ✅ 2026-06-24
- [x] 04-04: Startup reconciliation, duplicate-order guard, EOD force-close (calendar-aware), kill switch state flush ✅ 2026-06-24

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

**Plans**: 6/6 plans executed (docs recovered 2026-07-06 after ec826eb stripped them from develop)

Plans:

- [x] 05-00: Wave-0 foundation — MoomooGateway.get_global_state(), PositionManager alert callbacks, config/test-stub scaffolding ✅ 2026-06-24
- [x] 05-01: TradingBot orchestrator — APScheduler AsyncIOScheduler, 5 cron/interval jobs (premarket scan, intraday re-scans ~30 min, market-open subscribe, force-close, EOD report), kill-switch + readiness-gate wiring ✅ 2026-06-24
- [x] 05-02: OpenD connectivity watchdog (SVC-02) — `get_global_state()` poll loop, order-pause on failure, reconnect with backoff + Telegram alert ✅ 2026-06-24
- [x] 05-03: TelegramAlerter — stdlib urllib fire-and-forget push, entry/exit/summary alerts (ALERT-01..04), failure isolation from trade loop ✅ 2026-06-24
- [x] 05-04: Daily P&L report + static inline-SVG HTML dashboard (DASH-01); launchd/shell supervisor config; structured rotating log integration ✅ 2026-06-24
- [x] 05-05: ALERT-02 gap closure — thread real exit reason (pending_exit_reason) from FSM trigger to TelegramAlerter, fire alert on partial scale-outs, add trail_stop label; same-day UAT gap-closure plan ✅ 2026-06-24

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
**Plans**: 6 plans (4 waves)

Plans:
**Wave 1**

- [ ] 06-01-PLAN.md — Wave-0 foundation: backtester/ + tests/backtester/ packages, 4 importorskip-guarded failing test stubs, synthetic ahead-only 5m fixture (BT-02 look-ahead proof) + trade-log fixture (BT-03), gitignore cache/runs [Wave 1]

**Wave 2** *(blocked on 06-01)*

- [ ] 06-02-PLAN.md — SimulatedBarFeed (BT-04): yfinance+CSV read-through cache, get_ticker_frame casing, yfinance_to_moomoo normalization, chronological replay with point-in-time hod/lod/cum_volume, next_bar(N+1), out-of-window BacktestWindowError; + point-in-time setup accessors (daily bars, synthetic TodayPrice, 5m-for-TOD, prepost=True premarket highs) [Wave 2]
- [ ] 06-03-PLAN.md — SimulatedExecution (BT-02): N+1-open entry/exit fills + slippage (never intent.entry_price), fill capture for the trade log, D-05 abandon on no-next-bar; + SimulatedGateway stub (get_positions/get_equity only) [Wave 2]
- [ ] 06-04-PLAN.md — Performance report (BT-03): compute_metrics (win rate, avg R, profit factor, max drawdown; realized_pnl DERIVED — trades table never written by bot/), write_report per-trade CSV + summary.json [Wave 2]

**Wave 3** *(blocked on 06-02/03/04)*

- [ ] 06-05-PLAN.md — BacktestHarness (BT-01): ports TradingBot._process_bar; reused SignalEngine/RiskEngine/PositionManager/TrendJoinLong (gateway=None); per-day point-in-time baselines via _evaluate_symbol/_compute_tod_baselines; per-bar CLOCK CONTROL (patch signal_engine.now_et to bar time — entry-window + baseline keys point-in-time); harness-owned bar_buffer (swing-low trail); closed-position trade-log capture [Wave 3]

**Wave 4** *(blocked on 06-05)*

- [ ] 06-06-PLAN.md — run.py CLI (BT-01/03/04): argparse (--symbols/--start/--end/--rules-json/--output-dir), shared rules.json loader + ConfigError→exit(1), V5 input validation, live-DB collision guard (refuse data/bot_state.db), wire feed→harness→write_report, out-of-window loud failure [Wave 4]

### Phase 7: Strategy Optimization

**Goal**: The live strategy's four structural weaknesses identified by quant feedback (2026-07-03) are closed: the RVOL gate is time-of-day normalized (restoring realistic signal frequency), exits stop feeding the left tail (model selected by backtest evidence), stop invalidation moves from 5m bar-close to tick/broker-side (eliminating fat-tail losses past 1R), and a -2R daily circuit breaker halts new entries on adverse days
**Depends on**: Phase 6 (Backtester — required to validate exit-model change and RVOL-TOD threshold), Phase 06.2 (code-review remediation)
**Requirements**: SIG-RVOL-TOD, EXIT-MODEL, RISK-TICK-STOP, RISK-CIRCUIT
**Success Criteria** (what must be TRUE):

  1. Intraday RVOL compares cumulative volume at time T against the 14-day average of cumulative volume at the same time-of-day bucket (no full-day-average denominator before the close); signal frequency increases materially without loosening the institutional-interest intent
  2. The exit model shipped in rules.json is chosen from a backtest comparison (current partial/BE model vs no-scale/fixed-2R vs full-size-to-1.5R + trail variants) using the Phase 6 backtester, not by default
  3. A stop violation is acted on at tick/quote granularity (broker-side trigger order or quote-driven exit), not at the next 5m bar close; realized losses on stop-outs converge to ~1R
  4. When cumulative daily realized loss reaches -2R, all new entries are halted for the rest of the session (existing positions continue to be managed); the halt is logged, alerted, and resets next trading day
  5. All thresholds (RVOL-TOD min, circuit-breaker R, exit-model parameters) live in rules.json — no hardcoded strategy literals

**Plans**: 6 plans (4 waves)

Plans:
**Wave 1**

- [x] 07-01-PLAN.md — Foundation: migration 0005 (tod_baselines + broker_stop_order_id) + StateStore TOD/circuit-breaker methods + rules.json/schema/loader config keys [Wave 1]

**Wave 2** *(blocked on 07-01)*

- [x] 07-02-PLAN.md — RVOL-TOD data path: BarEvent.cum_volume + BarAggregator session-volume accumulator, fetcher.download_intraday_5m, scanner TOD baseline compute+persist [Wave 2]
- [x] 07-03-PLAN.md — Broker-side tick stop (RISK-TICK-STOP): gateway.place_stop_order (D-01), arm-on-fill + trail-sync cancel-replace (D-04), D-02 quote-tick fallback behind use_broker_stop_orders [Wave 2]
- [ ] 07-04-PLAN.md — Exit-model config seam (EXIT-MODEL crit 5): rules.json exit.model enum + schema + fail-closed loader (only partial_be_trail implemented) [Wave 2]

**Wave 3** *(blocked on 07-01, 07-02)*

- [x] 07-05-PLAN.md — Signal-engine gates: TOD-normalized I3 RVOL gate + -2R circuit-breaker gate (persist/auto-reset) + bot-orchestrator trip side-effects (D-08 abandon + Telegram alert) [Wave 3]

**Wave 4** *(blocked on Phase 6 backtester — unbuilt)*

- [x] 07-06-PLAN.md — Exit-model SELECTION gate (EXIT-MODEL crit 2): blocking decision — backtest comparison requires the Phase 6 backtester; defer vs proceed-if-ready [Wave 4]

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/4 | In progress | - |
| 2. Premarket Scanner | 4/4 | Complete   | 2026-06-23 |
| 3. Intraday Signal and Risk Engine | 3/3 | Complete   | 2026-06-24 |
| 4. Order and Position Management | 4/4 | Complete   | 2026-06-24 |
| 5. Service Orchestration and Reliability | 6/6 | Complete (3 UAT items blocked on live session) | 2026-06-24 |
| 6. Backtester | 0/6 | Not started | - |
| 7. Strategy Optimization | 5/6 | In Progress|  |

### Phase 07.1: Close gap: RISK-TICK-STOP — wire gateway into PositionManager (INSERTED)

**Goal**: `bot/main.py`'s `PositionManager(...)` construction actually receives `gateway=`, so `arm_stop_protection()` runs live instead of silently no-op'ing — RISK-TICK-STOP (broker Stop-Market + quote-tick fallback) becomes real on the live/paper path instead of dead code, with a regression test that fails if the wiring is ever dropped again
**Requirements**: RISK-TICK-STOP (completes the wiring gap left by 07-03; v1.0-MILESTONE-AUDIT.md Gap 1, discovered 2026-07-06)
**Depends on:** Phase 7 (07-03-PLAN.md built `arm_stop_protection`/`gateway.place_stop_order`; this phase wires it into production construction)
**Success Criteria** (what must be TRUE):

  1. `bot/main.py`'s `PositionManager(...)` call passes `gateway=gateway` (or an equivalent late-bind in `bot/service/bot.py::_do_startup_wiring`, mirroring the existing `_bar_buffer` pattern), so `position_manager._gateway is not None` after the real construction sequence — not just in a manually-injected test
  2. A regression test drives the actual `bot/main.py` (or `TradingBot`) construction path — not a hand-built `PositionManager(gateway=...)` — and asserts `arm_stop_protection()` calls `gateway.place_stop_order`/`subscribe_quote` rather than no-op'ing
  3. Full test suite stays green; no behavior change to the bar-close stop backstop (D-03) which remains active regardless

**Plans:** 1 plan (1 wave)

Plans:
**Wave 1**

- [x] 07.1-01-PLAN.md — RED regression test driving bot/main.py's real construction path (asserts position_manager._gateway wired + arm_stop_protection dispatches to gateway.subscribe_quote) + one-line gateway=gateway fix; full suite green [Wave 1] ✅ 2026-07-06 (5cc61eb, a8b2418)

### Phase 06.2: Code review remediation (INSERTED)

**Goal:** Close all 16 confirmed findings from the 2026-07-02 multi-agent code review (develop vs main) so the bot is safe for an unattended live paper run (Tier 1 blockers), behaviorally correct (Tier 2), and maintainable (Tier 3). Spec: `.planning/phases/06.2-code-review-remediation/06.2-SPEC.md`
**Requirements**: Regression-test-first (TDD) per finding; full suite green per tier; live premarket UAT gates Tier 1 sign-off
**Depends on:** Phase 06.1 (wire core trade loop) — NOT Phase 6 (backtester, unbuilt)
**Plans:** 5/6 plans executed
Plans:
**Wave 1**

- [ ] 06.2-01-PLAN.md — Tier 1 live-run blockers (findings 1.1–1.5): wire FillEvent→PositionManager, reconcile guard, bar-handler re-register, post-cancel dealt_qty, calendar-aware force-close; full suite + live premarket UAT gate [Wave 1]

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 06.2-02-PLAN.md — Tier 2 correctness (findings 2.1–2.8): bot-owned gate count, rescan active-codes, trades writer, bid/ask raise, orphan dedup, scanner staleness, exit-proxy R, config-driven orphan stop [Wave 2]

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 06.2-03-PLAN.md — Tier 3 hygiene (findings 3.1–3.2): shared reconcile core (after 1.2), trading-day-keyed daily-bar cache [Wave 3]
