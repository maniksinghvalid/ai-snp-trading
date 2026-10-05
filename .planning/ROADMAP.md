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
- [x] **Phase 6: Backtester** - Offline historical replay through the shared strategy and FSM code (9/9 plans executed 2026-07-07; gap-closure 06-07..06-09 closed all 7 original multi-day BLOCKERs, but re-verification gaps_found — 2 NEW BLOCKERs: NaN union-index bars crash multi-ticker replay; Gate 7 -2R circuit breaker can never trip during replay) (completed 2026-07-07)
- [ ] **Phase 7: Strategy Optimization** - Four structural strategy changes from quant feedback: RVOL-TOD gate, exit restructure (backtest-gated), tick-level stop invalidation, -2R daily circuit breaker
- [x] **Phase 8: Options Premium Selling (tasty_credit_spreads)** - Successor strategy after Trend Join Long was validated as no-edge (2026-08-13): tastylive-derived defined-risk iron condors / put credit spreads on liquid ETFs (45 DTE, IVR≥30 gate, 20Δ shorts, 50% profit target, 21-DTE exit), self-contained `bot/options/` package + `python -m bot --rules rules_options.json` dispatch (built 2026-08-17 via quick tasks 260817-0ph/155/1ie + UAT batch ad41cd5; 961 tests; live paper UAT `--live-1lot` pending operator run)
- [x] **Phase 10: External Strategy Research** - Two Reddit-sourced day-trading strategies critically extracted and compared against Trend Join Long; backtest-only research package (`backtester/experimental/`) implementing their automatable cores, pre-registered hypotheses, cache-only cost-realistic backtests across 5 windows/9 regime slices, and a 14-section report — production code changed only on a separate branch if a hypothesis is SUPPORTED (started 2026-08-18) (completed 2026-08-18)
- [x] **Phase 11: Multi-strategy options bot (bull_call_spread)** - `strategies` array in `rules_options.json`; one options process runs `tasty_credit_spreads` (unchanged) + new `super_bull_call` bull call debit spread on the equity premarket watchlist; per-strategy sizing, global breaker/BP cap; loader keeps the flat `load_options_config` contract for the backtester/probe (planned 2026-09-24) (completed 2026-09-26)
- [ ] **Phase 12: IBS ETF mean-reversion bot (ibs_etf_mean_reversion)** - Successor strategy after Trend Join Long was validated no-edge: separate `bot/ibs/` process trading the IBS < 0.20 / > 0.80 mean-reversion rule on 17 liquid ETFs near the close, overnight holds up to 10 trading days, unlevered, LIMIT-only, own DB/kill file/reports; selected by a two-round pre-registered search 2026-10-04 (OOS CAGR 16.4%, Sharpe 1.30, maxDD −11%) (added 2026-10-04)

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
**Plans**: 12 plans (4 build waves + 3 gap-closure waves + 2 post-review gap-closure waves)

Plans:
**Wave 1**

- [x] 06-01-PLAN.md — Wave-0 foundation: backtester/ + tests/backtester/ packages, 4 importorskip-guarded failing test stubs, synthetic ahead-only 5m fixture (BT-02 look-ahead proof) + trade-log fixture (BT-03), gitignore cache/runs [Wave 1]

**Wave 2** *(blocked on 06-01)*

- [x] 06-02-PLAN.md — SimulatedBarFeed (BT-04): yfinance+CSV read-through cache, get_ticker_frame casing, yfinance_to_moomoo normalization, chronological replay with point-in-time hod/lod/cum_volume, next_bar(N+1), out-of-window BacktestWindowError; + point-in-time setup accessors (daily bars, synthetic TodayPrice, 5m-for-TOD, prepost=True premarket highs) [Wave 2]
- [x] 06-03-PLAN.md — SimulatedExecution (BT-02): N+1-open entry/exit fills + slippage (never intent.entry_price), fill capture for the trade log, D-05 abandon on no-next-bar; + SimulatedGateway stub (get_positions/get_equity only) [Wave 2]
- [x] 06-04-PLAN.md — Performance report (BT-03): compute_metrics (win rate, avg R, profit factor, max drawdown; realized_pnl DERIVED — trades table never written by bot/), write_report per-trade CSV + summary.json [Wave 2]

**Wave 3** *(blocked on 06-02/03/04)*

- [x] 06-05-PLAN.md — BacktestHarness (BT-01): ports TradingBot._process_bar; reused SignalEngine/RiskEngine/PositionManager/TrendJoinLong (gateway=None); per-day point-in-time baselines via _evaluate_symbol/_compute_tod_baselines; per-bar CLOCK CONTROL (patch signal_engine.now_et to bar time — entry-window + baseline keys point-in-time); harness-owned bar_buffer (swing-low trail); closed-position trade-log capture [Wave 3]

**Wave 4** *(blocked on 06-05)*

- [x] 06-06-PLAN.md — run.py CLI (BT-01/03/04): argparse (--symbols/--start/--end/--rules-json/--output-dir), shared rules.json loader + ConfigError→exit(1), V5 input validation, live-DB collision guard (refuse data/bot_state.db), wire feed→harness→write_report, out-of-window loud failure [Wave 4]

**Gap Closure** *(2026-07-07 — verification gaps_found, 7 confirmed multi-day BLOCKERs; do NOT replan 06-01..06-06)*

- [x] 06-07-PLAN.md — feed.py yfinance window + point-in-time (BT-02/BT-04): real 60-calendar-day 5m fetch matching the guard (CR-03), per-trading-day coverage check → BacktestWindowError naming uncovered days (CR-03), same-session next_bar (CR-04), one-time prepost=True premarket load → point-in-time premarket_highs (CR-02) + premarket-only synthetic_today_price (CR-07) [Gap Wave 1]
- [x] 06-08-PLAN.md — execution.py exit-fill semantics (BT-01/BT-03): no-next-bar exit returns 0 with no phantom fill (WR-01), force-close mode fills at last observed bar close + returns full qty so force_close_all reaches CLOSED (CR-05 mechanic) [Gap Wave 2, blocked on 06-07]
- [x] 06-09-PLAN.md — harness.py multi-day correctness (BT-01/BT-02/BT-03): per-day premarket-high freeze applied in replay_day (CR-01), _WATCHLIST_CAP + entry gate on persisted watchlist (CR-06), EOD/end-of-run force_close_all with manager now_et rebind (CR-05), + 2+-trading-day regression test proving CR-01/CR-04/CR-05/CR-06 closed [Gap Wave 3, blocked on 06-07/06-08]

**Post-Review Gap Closure** *(2026-07-07 — re-verification gaps_found 1/4, 2 new BLOCKERs from code review 06d8b5b; do NOT replan 06-01..06-09)*

- [x] 06-10-PLAN.md — feed.py NaN-row hygiene (BT-04/BT-02): dropna(open/high/low/close/volume) in _materialize_bars and _load_premarket (both network + CSV-cache paths) so a realistic multi-ticker yf.download union-index NaN-padded frame neither crashes int(NaN) nor poisons premarket_highs; + union-index regression test [Post-Review Gap Wave 1]
- [x] 06-11-PLAN.md — Gate-7 trades persistence + exit-slippage sign (BT-01/BT-03): new parameterized StateStore.record_trade; _capture_closed_trades writes each closed trade to the scratch trades table so the reused SignalEngine Gate-7 -2R circuit breaker reads real realized P&L and can block entries; adverse exit slippage (SELL exits subtract); truthful harness docstring; + Gate-7-trip and slippage-sign regression tests [Post-Review Gap Wave 1]
- [x] 06-12-PLAN.md — test-suite time-bomb hygiene (non-blocking, test-only): recent_session_days() helper + retrofit hardcoded 2026-06-01/02 fixture dates across the backtester suite so the rolling 60-day window guard never turns the suite red on a future calendar date [Post-Review Gap Wave 2, blocked on 06-10/06-11]

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

**Plans**: 9 plans (4 build waves + 3 gap-closure waves)

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
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/4 | In progress | - |
| 2. Premarket Scanner | 4/4 | Complete   | 2026-06-23 |
| 3. Intraday Signal and Risk Engine | 3/3 | Complete   | 2026-06-24 |
| 4. Order and Position Management | 4/4 | Complete   | 2026-06-24 |
| 5. Service Orchestration and Reliability | 6/6 | Complete (3 UAT items blocked on live session) | 2026-06-24 |
| 6. Backtester | 12/12 | Complete    | 2026-07-07 |
| 7. Strategy Optimization | 5/6 | In Progress|  |
| 8. Options Premium Selling (tasty_credit_spreads) | 5/5 waves | Built; live paper UAT pending | 2026-08-17 |
| 9. Options Backtester | 5/5 | Built + gap-closure done; evidence run in progress (free-tier cache warm, ~1–2 days) | - |
| 10. External Strategy Research | 6/6 | Complete    | 2026-08-18 |
| 11. Multi-strategy options bot (bull_call_spread) | 9/9 | Complete    | 2026-09-26 |

### Phase 8: Options Premium Selling (tasty_credit_spreads)

**Goal:** The bot can trade a research-derived options premium-selling strategy on the Moomoo paper account, end-to-end and unattended: screen liquid ETFs for IV Rank, build a defined-risk iron condor / put credit spread at ~45 DTE, size by dollar risk, manage to 50% profit or 21 DTE, reconcile against the broker, alert, and report — without touching the equity bot's code path or the human's positions on the shared account.
**Requirements**: OPT-01 strategy core is pure/config-driven (rules_options.json, CFG-01); OPT-02 defined-risk only, one position per underlying, BP/concurrency/per-day caps, daily-loss breaker; OPT-03 legs placed long-wing-first, unwind on failure, shorts bought back first on close, LIMIT orders only; OPT-04 the options bot only ever manages legs recorded in its own DB (SAFE-OG-01 for options); OPT-05 separate DB/kill-file/report-dir so both bots can coexist; OPT-06 research provenance (docs/research/2026-08-17-tastylive-options-research.md)
**Depends on:** Phase 5 (gateway/watchdog/alerter/kill-switch/scheduler patterns), Phase 1 (StateStore/migrations)
**Design:** ~/.claude/plans/scrape-highly-rated-options-velvety-naur.md (approved 2026-08-17)
**Success Criteria** (what must be TRUE):

  1. `python -m bot --rules rules_options.json` dispatches on `strategy_name` to `bot/options/service.py:main`; `python -m bot` (equity) is byte-for-byte unchanged in behaviour
  2. `pick_expiry/pick_strikes/size_position/manage_decision` are pure functions with tests covering the priority table, geometry, sizing floors and BP cap
  3. Migration 0006 adds `option_positions`/`option_legs`; migrations 0001-0005 untouched
  4. `LegExecutor` never places a MARKET order, opens long wings before shorts, persists order_id before the next leg, and unwinds on any leg failure
  5. `OptionsBot.reconcile()` flags NEEDS_ATTENTION on any DB/broker leg mismatch and never adopts or closes broker option codes it did not record
  6. Full test suite green (961 as of ad41cd5)
  7. Live UAT: `scripts/uat_options_probe.py` read-only run during RTH shows sane liquidity/credit decisions; `--live-1lot --confirm` opens+closes 1 SPY put credit spread on paper with clean reconcile and recorded realized P&L (OPERATOR-RUN, pending)

**Plans:** delivered as GSD quick tasks (not phase plans):

- [x] 260817-0ph — Wave 1: migration 0006, bot/options/{schema,config,strategy}.py, rules_options.json (14d9ab5, b5f1c85, 10aef4e, 47a47dd)
- [x] 260817-155 — Wave 2-3: gateway get_stock_ids/screen_options/get_option_positions, OptionsStore, LegExecutor (f5c6612, ac614eb, 7624ea9)
- [x] 260817-1ie — Wave 4: OptionsBot service (entry/manage/eod jobs, reconcile, alerts, report), main dispatch + --rules (ce3edb7, bb059f1, 96ad6ac, e34e252, 35194ed)
- [x] Wave 5: research doc, scripts/uat_options_probe.py, live read-only UAT fixes (ad41cd5), ROADMAP/README/CLAUDE.md
- [ ] Live paper UAT (`--live-1lot`) + first RTH probe review of liquidity thresholds — operator
- [ ] Phase 9 (see block below): options backtester built (5/5 plans, 1038 tests); hypothesis evidence pending the background cache-warming run started 2026-08-17 (Massive free tier: 5 req/min hard cap)

### Phase 07.1: Close gap: RISK-TICK-STOP — wire gateway into PositionManager (INSERTED)

**Goal**: `bot/main.py`'s `PositionManager(...)` construction actually receives `gateway=`, so `arm_stop_protection()` runs live instead of silently no-op'ing — RISK-TICK-STOP (broker Stop-Market + quote-tick fallback) becomes real on the live/paper path instead of dead code, with a regression test that fails if the wiring is ever dropped again
**Requirements**: RISK-TICK-STOP (completes the wiring gap left by 07-03; v1.0-MILESTONE-AUDIT.md Gap 1, discovered 2026-07-06)
**Depends on:** Phase 7 (07-03-PLAN.md built `arm_stop_protection`/`gateway.place_stop_order`; this phase wires it into production construction)
**Success Criteria** (what must be TRUE):

  1. `bot/main.py`'s `PositionManager(...)` call passes `gateway=gateway` (or an equivalent late-bind in `bot/service/bot.py::_do_startup_wiring`, mirroring the existing `_bar_buffer` pattern), so `position_manager._gateway is not None` after the real construction sequence — not just in a manually-injected test
  2. A regression test drives the actual `bot/main.py` (or `TradingBot`) construction path — not a hand-built `PositionManager(gateway=...)` — and asserts `arm_stop_protection()` calls `gateway.place_stop_order`/`subscribe_quote` rather than no-op'ing
  3. Full test suite stays green; no behavior change to the bar-close stop backstop (D-03) which remains active regardless

**Plans:** 12/12 plans complete

Plans:
**Wave 1**

- [x] 07.1-01-PLAN.md — RED regression test driving bot/main.py's real construction path (asserts position_manager._gateway wired + arm_stop_protection dispatches to gateway.subscribe_quote) + one-line gateway=gateway fix; full suite green [Wave 1] ✅ 2026-07-06 (5cc61eb, a8b2418)

### Phase 06.2: Code review remediation (INSERTED)

**Goal:** Close all 16 confirmed findings from the 2026-07-02 multi-agent code review (develop vs main) so the bot is safe for an unattended live paper run (Tier 1 blockers), behaviorally correct (Tier 2), and maintainable (Tier 3). Spec: `.planning/phases/06.2-code-review-remediation/06.2-SPEC.md`
**Requirements**: Regression-test-first (TDD) per finding; full suite green per tier; live premarket UAT gates Tier 1 sign-off
**Depends on:** Phase 06.1 (wire core trade loop) — NOT Phase 6 (backtester, unbuilt)
**Plans:** 3/3 plans complete
Plans:
**Wave 1**

- [x] 06.2-01-PLAN.md — Tier 1 live-run blockers (findings 1.1–1.5): wire FillEvent→PositionManager, reconcile guard, bar-handler re-register, post-cancel dealt_qty, calendar-aware force-close; full suite + live premarket UAT gate [Wave 1]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 06.2-02-PLAN.md — Tier 2 correctness (findings 2.1–2.8): bot-owned gate count, rescan active-codes, trades writer, bid/ask raise, orphan dedup, scanner staleness, exit-proxy R, config-driven orphan stop [Wave 2]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 06.2-03-PLAN.md — Tier 3 hygiene (findings 3.1–3.2): shared reconcile core (after 1.2), trading-day-keyed daily-bar cache [Wave 3]

### Phase 9: Options backtester

**Goal:** Produce offline evidence for/against the `tasty_credit_spreads` strategy before it earns real capital: replay `bot/options/strategy.py`'s pure functions (`pick_expiry`/`passes_entry_gate`/`pick_strikes`/`size_position`/`manage_decision`) unchanged over Massive historical option data (contracts reference incl. `expired=true` + `O:…` daily aggregates), with Black-Scholes IV/delta derived from closes and IVR from an own daily ATM-IV series, and answer a pre-registered hypothesis set (IVR 20 vs 30, 16Δ vs 20Δ, IC vs PCS) so any change to `rules_options.json` is data-driven rather than default.
**Requirements**: OBT-01, OBT-02, OBT-03, OBT-04, OBT-05, OBT-06, OBT-07 (see REQUIREMENTS.md § Options Backtesting)
**Depends on:** Phase 8 (strategy pure functions, `rules_options.json` schema), Phase 6 (backtester harness/report/massive patterns)
**Design:** ~/.claude/plans/scrape-highly-rated-options-velvety-naur.md §Follow-ups (Massive entitlement: contracts reference + option daily aggregates = 200; chain snapshot = 403, verified 2026-08-17)
**Success Criteria** (what must be TRUE):

  1. `python3 -m backtester.options_run --rules rules_options.json --start … --end …` runs end-to-end on ≥1 underlying (SPY) with no modification to `bot/options/strategy.py`, and a test proves the strategy module is imported, not copied
  2. A look-ahead test (ahead-only fixture, Phase 6 BT-02 pattern) proves entry/strike decisions never see bars after the decision date
  3. Black-Scholes IV/delta round-trip test (price→IV→price within tolerance) and delta monotonicity test pass; IVR series test matches hand-computed rank on a fixture
  4. `docs/research/2026-MM-DD-options-backtest-hypotheses.md` exists with the pre-registered hypotheses, evidence floor and OOS window, committed BEFORE the first real-data run (git history proves ordering)
  5. Result doc reports each hypothesis as SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with the numbers; `rules_options.json` is changed only if a hypothesis is SUPPORTED with OOS confirmation
  6. Full test suite green

**Plans:** 5/5 plans complete

Plans:
**Wave 1**

- [x] 09-01-PLAN.md — Massive options data layer: entitlement re-probe, `cached_contracts`/`cached_option_bars`, `OptionChainSource` with structural no-look-ahead (OBT-01, OBT-02) [Wave 1]

**Wave 2** *(blocked on Wave 1)*

- [x] 09-02-PLAN.md — Black-Scholes IV/delta (stdlib only), min-max IVR series, and the pre-registered hypotheses doc committed before any real-data run (OBT-03, OBT-04, OBT-06) [Wave 2]

**Wave 3** *(blocked on Wave 2)*

- [x] 09-03-PLAN.md — Daily replay engine (imported strategy, live cap order, mid±slippage fills, intrinsic settlement) + options report glue (OBT-01, OBT-05, OBT-07) [Wave 3]

**Wave 4** *(blocked on Wave 3)*

- [x] 09-04-PLAN.md — `backtester.options_run` CLI with `--set` overrides, real-data runs for every hypothesis arm, results doc + conditional `rules_options.json` change (OBT-01, OBT-06, OBT-07) [Wave 4] — arms could NOT complete: Massive free tier = 5 req then 429 (0.23 s latency) + eager fetch design → all H1/H2/H3 INSUFFICIENT-EVIDENCE

**Wave 5 — gap closure** *(09-VERIFICATION.md gaps_found 6/9; 09-REVIEW.md 1 critical / 5 warnings)*

- [x] 09-05-PLAN.md — Lazy per-expiry/OTM-band fetch (≈7.5k SPY contracts vs 58k+), expired=true/false union (WR-03), per-ticker negative cache (WR-04), `--workers`, CR-01 end_of_window settlement, WR-01/02/05, results-doc root-cause correction (OBT-02/03/05/07) [Wave 5] ✅ 2026-08-17 (ae0270c…ded8f80, d3c956a)
- [ ] Evidence: background run `warm-cache-pool` (SPY,QQQ,IWM,TLT,GLD,XLE 2024-11-18→2026-06-15) started 2026-08-17 (pid file `backtester/results/options/warm-cache-pool.pid`, log `.log`); then run the 12 arm commands in `docs/research/2026-08-17-options-backtest-results.md` from cache and fill in verdicts. Alternative: upgrade Massive to a paid options tier → `--workers 8`, minutes instead of days.

### Phase 10: External Strategy Research

**Goal**: Two Reddit-sourced day-trading strategies ("day trading strategies that actually work", u/El1teM1ndset 2025-02-27; "Consistent trading strategy… netted $300K+", u/Logical_Argument_216 2025-01-26) are critically extracted, their credibility assessed, and their automatable cores backtested cache-only against Trend Join Long (TJL, no-edge since 2026-08-13) and candidate improvements, with a pre-registered hypothesis protocol, so any recommendation to change `rules.json` is evidence-gated rather than default — matching the Phase 9 pre-registration discipline. Research-only: no production code or config changes as part of this phase.
**Requirements**: XSR-01, XSR-02, XSR-03, XSR-04, XSR-05, XSR-06
**Depends on:** Phase 6 (backtester conventions — `SimulatedBarFeed`, `report.py` metrics/CSV schema), Phase 7 (RVOL-TOD / exit-model precedent this research extends)
**Design:** `~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md` (approved 2026-08-18)
**Success Criteria** (what must be TRUE):

  1. Both Reddit threads are extracted into explicit entry/exit/stop/sizing/timeframe rules, separated from assumptions, with credibility assessed (anecdotal, unverified P&L, survivorship, sample size) — captured in the results doc
  2. `backtester/experimental/` implements the automatable strategy cores (SMA10+MACD; ORB30+1H-EMA100+VWAP; VWAP pullback) as tested, pluggable modules reusing `SimulatedBarFeed`/`report.py` conventions (cache-keyed data, N+1-open fills, no look-ahead); `bot/` and `rules.json`/`rules_options.json` are untouched
  3. `docs/research/2026-08-18-external-strategies-hypotheses.md` (universe, IS/OOS windows, evidence floor ≥25 trades/arm/window, metric of record) is committed BEFORE any real-data run — git history proves ordering
  4. All backtests run cache-only (zero new Massive requests; the Phase 9 warm-cache process is undisturbed) at realistic cost (commission + slippage), reporting standard + extended metrics (CAGR, Sharpe, Sortino, max DD, PF, win rate, avg trade $, expectancy, exposure, bootstrap CI) across the 5 windows and 9 regime slices
  5. `docs/research/2026-08-18-external-strategies-results.md` reports every hypothesis (H1–H8) as SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with numbers, plus the 14 requested report sections; `rules.json`/`rules_options.json` remain unchanged regardless of verdict
  6. Full test suite green (`python3 -m pytest -q`)
  7. If any hypothesis is SUPPORTED in both IS and OOS, the exact production integration exists on a separate, unmerged feature branch (default-off, schema-valid, tests green)

**Plans**: 6 plans across 5 waves (Wave 1 pre-registration → Wave 2 engine+strategies+CLI+tests → Wave 3 runs → Wave 4 aggregate+charts+report → Wave 5 conditional productionization branch)

Plans:
**Wave 1**

- [x] 10-01-PLAN.md — Pre-registration: hypotheses doc (H1–H8, universe, windows, evidence floor, metric of record), frozen 15-arm `backtester/experimental/arms.json`, SPY daily regime series via yfinance (cached, provenance recorded), committed before any run [Wave 1]

**Wave 2** *(blocked on Wave 1)*

- [x] 10-02-PLAN.md — `backtester/experimental/{indicators,strategies,exits,engine}.py` + `backtester/report.py` side-aware `_net_pnl`/`extra_fields` patch + look-ahead/prefix-invariance/short-sign/caps/force-close tests [Wave 2]
- [x] 10-03-PLAN.md — `backtester/experimental/run.py` CLI: locked `WINDOWS` table, cache-presence guard + fail-closed data source, per-window arm loop, `--tjl-regime` day-filter mode + CLI tests [Wave 2]

**Wave 3** *(blocked on Wave 2)*

- [x] 10-04-PLAN.md — Pre-flight (cache/warm-pool/pre-registration/scope) → cache-only run matrix (15 arms × 5 windows × base/stress/zero + intrabar) → 11 TJL day-filter re-reports + post-run no-fetch proof [Wave 3]

**Wave 4** *(blocked on Wave 3)*

- [x] 10-05-PLAN.md — `aggregate.py` (9 slices, IS/OOS pools, bootstrap CI, walk-forward) + `charts.py` (hand-rolled SVG) + committed evidence assets + `docs/research/2026-08-18-external-strategies-results.md` (14 sections, H1–H8 verdict table) [Wave 4]

**Wave 5** *(conditional — only if a hypothesis is SUPPORTED in both IS and OOS)*

- [x] 10-06-PLAN.md — Gate task reads the Wave-4 verdict table; if triggered: operator scope checkpoint, then `feature/phase10-<arm>` integration (`strategy_name`/`direction` loaded, `SignalEngine(strategy=...)` seam, new `StrategyCore` subclass, default-off, suite green) — not merged [Wave 5]

### Phase 11: Multi-strategy options bot (bull_call_spread)

**Goal:** `rules_options.json` defines multiple option strategies in a `strategies` array and the ONE options-bot process runs all of them concurrently: the existing `tasty_credit_spreads` credit book (behavior unchanged) plus a new `super_bull_call` debit book — a bull call spread (~30Δ long call, short call one width higher, ≤30% of width debit, no stop, full close at a % of max profit) sourced from "Super Bull Call Spread" (Options With Ravish, youtube VZ1MbM3UQ5Q), whose daily bullish universe is the equity bot's Trend Join Long premarket watchlist (read-only). Per-strategy sizing; global daily-loss breaker and BP cap.
**Requirements**: MSO-01, MSO-02, MSO-03, MSO-04, MSO-05, MSO-06, MSO-07, MSO-08, MSO-09 (see REQUIREMENTS.md § Multi-Strategy Options)
**Depends on:** Phase 8 (options bot, `rules_options.json` schema, pure strategy core), Phase 9 (options backtester — a consumer of `load_options_config` that must keep working unchanged), Phase 2 (premarket scanner watchlist in `daily_scan`)
**Design:** `docs/superpowers/specs/2026-09-24-multi-strategy-options-design.md` (operator-approved 2026-09-24; §12a amendment = planning-time findings after rebase onto develop)
**Success Criteria** (what must be TRUE):

  1. `rules_options.json` ships in the `strategies` shape with both `tasty_credit_spreads` and `super_bull_call`; `python3 -m bot --rules rules_options.json` registers one entry-scan job per strategy plus one shared manage job and one EOD job
  2. `load_options_config(path)` still returns a flat `OptionsConfig` (default strategy = first); the `tasty_credit_spreads` view equals the pre-change config field-for-field; the Phase 9 backtester, UAT probe and all pre-existing tests pass without modification to their call sites
  3. `backtester.options_run` accepts `--strategy NAME` and every documented `--set` arm command keeps working verbatim; a debit structure is rejected with a clear error
  4. Pure-function tests prove the bull-call strike selection, the ≤`max_debit_to_width` gate, `debit × 100` sizing, and `manage_decision_debit` sign math and check ordering (assignment guard → profit target → optional DTE exit; no stop)
  5. The equity watchlist is read via a read-only SQLite URI, capped at 20 codes by rank; missing/locked/empty → zero bull-call entries that day; no write path to the equity DB exists
  6. Positions carry `strategy_name` (idempotent migration, legacy rows default `tasty_credit_spreads`); debit positions store negative `credit_per_spread` and the existing close math yields correct realized P&L for both kinds
  7. Per-strategy caps (entries/day, concurrent) are counted per strategy while the daily-loss breaker, BP headroom and one-position-per-underlying are global — proven by service tests
  8. Safety invariants unchanged (LIMIT only; longs-first open / shorts-first close; SAFE-OG-01 reconcile scope; own DB/kill file/report dir; one instance; SIMULATE only) and the full suite is green (`python3 -m pytest -q`)

**Plans:** 9/9 plans complete

Plans:
**Wave 1**

- [x] 11-01-PLAN.md — Config book: STRATEGIES_SCHEMA, OptionsConfig +6 fields, load_options_book, load_options_config(path, strategy=None), legacy_view, fail-closed D-11 checks (MSO-01/02/03) [Wave 1]
- [x] 11-02-PLAN.md — Pure core: bull_call_spread pick_strikes branch + 1/4-rule gate, size_debit_position, manage_decision_debit; provenance doc (MSO-04/05, D-27) [Wave 1]
- [x] 11-03-PLAN.md — Data layer: _migration_0007 strategy_name + store pass-through/per-strategy count; read-only equity watchlist reader bot/options/universe.py (MSO-06/07) [Wave 1]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 11-04-PLAN.md — options_run --strategy + legacy_view before --set; convert shipped rules_options.json + widen bot/main.py dispatch; D-26 field-for-field no-drift test (MSO-02/09) [Wave 2]
- [x] 11-05-PLAN.md — Service entry side: per-strategy jobs, watchlist universe, call-only screen, debit entry, per-strategy caps vs global breaker/BP/underlying (MSO-08/06) [Wave 2]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 11-06-PLAN.md — Service manage side: per-strategy manage dispatch, debit close/%-of-max, D-29 unknown-strategy guard, strategy name in exit/EOD, book-based main(); phase gate (MSO-08/07/05/09) [Wave 3]

**Gap closure** *(11-VERIFICATION.md CR-01 + 11-REVIEW.md WR-01/WR-05 + approved EX-01/Q-01)*

- [x] 11-07-PLAN.md — _quote_ok gate + near-expiry unquotable escalation + close-exception→NEEDS_ATTENTION in the shared manage path (CR-01, Q-01), close_legs never sells a long once a short failed (EX-01), structure-kind mismatch fail-closed (WR-01), BP headroom/concurrent cap over every ACTIVE row (WR-05) (MSO-05/07/08) [Wave 1, gap_closure]

**Gap closure 2** *(11-REVIEW.md CR-02 (EX-02) + WR-06 + WR-07 — operator decision 2026-09-24)*

- [x] 11-08-PLAN.md — failed entry unwind → NEEDS_ATTENTION + truthful alert, counted in BP/busy/caps (CR-02); near-expiry escalation after 3 consecutive misses or the expiry session's final cycle, snapshot outages never count (WR-06); _quote_markable width gate for every decision outside the guard window (WR-07) (MSO-05/07/08) [Wave 1, gap_closure]

**Gap closure 3** *(11-REVIEW.md third review CR-03 + WR-10 + WR-11 + IN-08 — operator decision 2026-09-25)*

- [x] 11-09-PLAN.md — fill_leg cancels its working order on any exception/cancellation then re-raises, hand-off alerts warn to cancel working orders (CR-03); one-time expiry-day warning on the first unmanageable cycle (WR-10); process-level snapshot-outage alert once per episode (WR-11); miss streak scoped to the ET session (IN-08) (MSO-05/07/08) [Wave 1, gap_closure]

### Phase 12: IBS ETF mean-reversion bot (ibs_etf_mean_reversion)

**Goal:** A new, separate paper-trading bot process (`python3 -m bot --rules rules_ibs.json`, self-contained `bot/ibs/` mirroring `bot/options/`) that trades the IBS mean-reversion rule on 17 liquid US ETFs — buy when IBS = (last−low)/(high−low) < 0.20 at 10 minutes before the close, sell when IBS > 0.80 or after 10 trading days, 10 equal-weight slots of 10% of $100k paper equity, LIMIT orders only, positions held overnight by design — with the full safety stack (paper guard, readiness gate, own-DB-only reconcile, kill switch, watchdog, Telegram, EOD report) and operator cutover tooling (read-only UAT probe, `--live-1lot`, launchd plist, runbook to stop Trend Join Long). Selected 2026-10-04 by a two-round pre-registered search (16 hypotheses): OOS 2019–2026 net CAGR 16.4% (close fill) / 12.7% (next-open), Sharpe 1.30 / 1.07, maxDD −11% / −22% vs SPY 17.3% / 0.93 / −34%. Operator decisions (final): unlevered, separate bot, near-close fills, Trend Join Long stopped at cutover. Context: `12-CONTEXT.md`.
**Requirements**: IBS-01..IBS-10 (defined in `12-CONTEXT.md`)
**Depends on:** Phase 11
**Plans:** 9 plans (5 waves)

Plans:

- [ ] 12-01-PLAN.md — Wave 1: research provenance — six scripts to backtester/experimental/ibs_search/ (path constants only) + results doc (IBS-10)
- [ ] 12-02-PLAN.md — Wave 1: rules_ibs.json + IBS_SCHEMA + fail-closed IbsConfig loader + tests/ibs scaffolding (IBS-01)
- [ ] 12-03-PLAN.md — Wave 1: additive shared helpers — configure_logging log_name/force, trading_days_between, migration 0008 ibs_* tables, audit-log test isolation (IBS-06, IBS-08)
- [ ] 12-04-PLAN.md — Wave 2: pure strategy core + research parity test with documented same-day re-entry divergence (IBS-03)
- [ ] 12-05-PLAN.md — Wave 2: IbsStore + IbsExecutor (LegExecutor reuse, deadline-bounded) (IBS-05, IBS-06)
- [ ] 12-06-PLAN.md — Wave 3: IbsBot readiness gate, own-rows reconcile, guarded decision job, exit batch with persisted retry (IBS-04, IBS-05, IBS-07)
- [ ] 12-07-PLAN.md — Wave 3: operator tooling — uat_ibs_probe.py (read-only + --live-1lot --confirm), launchd plist, IBS-RUNBOOK.md, CLAUDE.md section (IBS-09)
- [ ] 12-08-PLAN.md — Wave 4: entry batch (external-holding guard, same-session exclusion, slots, sizing) + close − 1 min hard-cancel sweep (IBS-04, IBS-05, IBS-07)
- [ ] 12-09-PLAN.md — Wave 5: calendar-aware job arming, EOD report, shutdown order, main(), bot/main.py dispatch, static hygiene, phase gate + VALIDATION sign-off (IBS-02, IBS-04, IBS-05, IBS-08)
