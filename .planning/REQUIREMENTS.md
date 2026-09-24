# Requirements: AI S&P Trading Bot (Trend Join Long)

**Defined:** 2026-06-23
**Core Value:** The bot autonomously executes the Trend Join Long strategy end-to-end on a paper account — scan, enter, manage risk, exit, and report — correctly and unattended.

## v1 Requirements

Requirements for the initial release. Each maps to roadmap phases. Derived from the
fully-specified strategy (PROJECT.md) and the research table-stakes (`.planning/research/`).

### Scanner

- [x] **SCAN-01**: Bot fetches the current S&P 500 constituent list as the scan universe
- [x] **SCAN-02**: Premarket scan filters the universe by price ≥ $3 and the daily setup (above prior-day high, prior close > SMA200, gap ≥ 3% from prior close)
- [x] **SCAN-03**: Scan computes a 14-day RVOL baseline using only prior completed trading days (no look-ahead)
- [x] **SCAN-04**: Scan runs only on NYSE trading days (holiday/half-day aware) and writes a stored daily watchlist
- [x] **SCAN-05**: The daily scan is idempotent (re-running the same day does not duplicate the watchlist)
- [x] **SCAN-06**: Scan market data (daily bars across the ~500-symbol universe) is sourced from yfinance (free external source), not Moomoo snapshots/klines, to avoid broker quota; Moomoo is reserved for execution + live 5m subscriptions
- [x] **SCAN-07**: Scan re-runs intraday on a schedule (~every 30 min, ≈7 passes 09:55–12:55 ET) to catch post-open gappers/breakouts; each pass updates the watchlist idempotently
- [x] **SCAN-08**: The persisted watchlist is capped at the top 20 candidates by gap %, bounding downstream 5m subscriptions

### Signals

- [x] **SIG-01**: Bot subscribes to live 5m bars only for the (capped, top-20) watchlist candidates — never the full universe — to respect Moomoo subscription quota
- [x] **SIG-02**: Entry signals are evaluated only on closed 5m bars — never mid-bar (no repainting)
- [x] **SIG-03**: An entry triggers when price is above the premarket high, above today's HOD, and intraday RVOL ≥ 2.0, within 10:05–15:30 ET
- [x] **SIG-04**: No new entries when 5 concurrent positions are open or after the 15:30 ET cutoff

### Risk & Sizing

- [x] **RISK-01**: Each position is sized to risk 1% of current account equity (read live, not cached)
- [x] **RISK-02**: Position notional is capped at 10% of portfolio value
- [x] **RISK-03**: Initial stop is computed as low-of-day − 1%
- [x] **RISK-04**: Maximum 5 concurrent positions is enforced at order-submission time
- [x] **RISK-05**: A daily new-entry cap (`max_trades_per_day`, default 5) is enforced separately from the concurrent cap — bounds total daily entries even as positions close and free slots

### Execution

- [x] **EXEC-01**: Orders are placed through the Moomoo API on the paper (SIMULATE) account only *(04-03: ExecutionEngine.consume_intent; live round-trip UAT-verified 2026-07-02)*
- [x] **EXEC-02**: Exits use limit orders at aggressive prices (no reliance on paper market-order fills) *(04-03: test_no_market_orders — 0 MARKET refs, gateway.place_order hardcodes NORMAL)*
- [x] **EXEC-03**: Pending orders use a TTL with cancel-replace if unfilled *(04-03: test_ttl_cancel_replace, 3 scenarios incl. config-swap)*
- [x] **EXEC-04**: Duplicate-order prevention via a broker-verified per-symbol position guard *(04-04: test_duplicate_guard, 3 paths — broker positions + open orders + clear-to-proceed)*
- [x] **EXEC-05**: Stop-out and fill reconciliation matches broker fills by `order_id`, never by quantity (quantity matching produces false stop-outs after a partial exit) *(04-03: test_fill_by_order_id + source grep gate)*

### Position Lifecycle

- [x] **POS-01**: Take ⅓ off the position at 0.75R (partial profit) *(04-01: test_partial_profit_trigger, config-driven via cfg.partial_profit_trigger_r)*
- [x] **POS-02**: Move the stop to breakeven at 1.0R *(04-01: test_breakeven_trigger, incl. config-swap to 1.5R)*
- [x] **POS-03**: After breakeven, trail the stop on 5m swing lows (2/2 pattern) *(04-01: test_trail_never_loosens, incl. restart simulation)*
- [x] **POS-04**: Force-close all open positions at 15:51 ET (calendar-aware for half-days) *(04-04: test_force_close_half_day; force_close_stuck audit at manager.py:350,378)*
- [x] **POS-05**: Per-position lifecycle state (the FSM) is persisted and survives restarts

### Strategy Configuration

- [x] **CFG-01**: All strategy parameters (universe/daily/intraday filters, time gates, exit rules, risk + `max_trades_per_day`) are externalized to a `rules.json` config loaded at startup as the single source of truth — no strategy constants hardcoded in Python; live bot and backtester read the same file *(01-03: rules.json + jsonschema loader + StrategyConfig + TrendJoinLong config-driven)*

### State & Safety

- [x] **STATE-01**: Durable SQLite state with atomic writes for positions, stops, scans, and trades *(01-02: StateStore + migration 0001 + atomic_write_json crash-injection proven)*
- [x] **SAFE-01**: Hard paper-trading guard at startup — an explicit `PAPER_TRADING=true` config flag is required AND the selected account's environment is asserted to be SIMULATE (via broker account-type check); any mismatch or REAL account hard-exits before any order path is reachable *(01-01: triple fail-closed guard implemented)*
- [x] **SAFE-02**: Startup reconciliation against broker truth completes before any signal processing
- [x] **SAFE-03**: A broker-reconciliation loop (every 60–90s) diffs in-memory state vs broker truth; broker wins
- [x] **SAFE-04**: Kill switch (file-touch or SIGINT) triggers graceful shutdown with a state flush *(01-04: KillSwitch implemented — sentinel file + SIGINT + idempotent trigger + state-flush callback)*
- [x] **SAFE-05**: Append-only trade/order audit log (JSONL), extending the existing `~/.futu_trade_audit.jsonl` pattern *(01-01: append_audit() implemented)*

### Service & Orchestration

- [x] **SVC-01**: Long-running supervised service with an internal scheduler (premarket scan → intraday loop → EOD flatten) *(05-01: TradingBot orchestrator, bot/service/bot.py, AsyncIOScheduler + 5 jobs; composed end-to-end in bot/main.py per 06.1)*
- [x] **SVC-02**: OpenD connectivity watchdog (poll `get_global_state` ~every 60s); pause order placement on failure *(05-02: OpenDWatchdog, bot/service/watchdog.py)*
- [x] **SVC-03**: Structured, rotating application logging *(01-04: structlog RotatingFileHandler JSON + ConsoleRenderer stderr)*
- [x] **SVC-04**: All timing uses US Eastern (`zoneinfo`), correct across DST *(01-04: ET = ZoneInfo("America/New_York"), now_et(), to_et(); DST-tested)*

### Alerts

- [x] **ALERT-01**: Telegram alert on entry (ticker, size, entry price, initial stop) *(05-03: format_entry_alert; live delivery UAT-verified 2026-07-02)*
- [x] **ALERT-02**: Telegram alert on each exit event (partial, breakeven move, trail-stop, stop-out, force-close)
- [x] **ALERT-03**: Daily Telegram summary after force-close (trades, win/loss, realized PnL, open risk)
- [x] **ALERT-04**: Alert delivery failures never block or crash the trade loop *(05-03: TelegramAlerter.send() swallows all exceptions; no-op without secrets)*

### Reporting

- [x] **DASH-01** *(optional)*: A static, offline, no-JS HTML performance dashboard is generated (R-multiple histogram, open-positions table, last-20 closed trades) alongside the Telegram daily summary *(05-04: ReportBuilder; histogram overflow KeyError fixed commit be194e5; renders offline UAT-verified)*

### Backtesting

- [x] **BT-01**: Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot
- [x] **BT-02**: Backtester enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time
- [x] **BT-03**: Backtester produces a performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV)
- [x] **BT-04**: Backtest historical data is sourced from yfinance (or flat CSV/Parquet export), not Moomoo, avoiding broker historical-quota limits at 500-symbol scale

### Strategy Optimization (Phase 7)

Formalized 2026-07-03 from the quant-feedback phase. All four are config-driven (CFG-01). RISK-CIRCUIT promotes and supersedes the v2 CB-01 sketch (realized-only −2R halt rather than a −2% session-PnL rule).

- [x] **SIG-RVOL-TOD**: Intraday RVOL compares cumulative session volume at time T against the 14-day average of cumulative volume at the same time-of-day bucket (no full-day-average denominator before the close); falls back to the legacy ratio only when no TOD baseline exists *(07-01/02/05: get_tod_baseline in signal_engine Gate 2; _compute_tod_baselines in scanner)*
- [x] **RISK-TICK-STOP**: A stop violation is acted on at tick/quote granularity — a broker-side Stop-Market protective order (EXEC-02 amended to allow protective stop-market orders, D-01) or, on accounts that do not honor stop orders, a bot-side quote-tick monitor (D-02) — not at the next 5m bar close; the bar-close stop check is retained as a redundant backstop (D-03) *(07-01/03: place_stop_order/arm_stop_protection/_on_quote; wiring wave 07.1-01 fixed bot/main.py's PositionManager(gateway=...) so this reaches the live path instead of no-op'ing)*
- [x] **RISK-CIRCUIT**: When cumulative daily realized loss reaches −2R (realized-only, from the trades table; −$2,000 at the fixed $100k basis), all new entries are halted for the rest of the session; the trip is persisted (survives restart), auto-resets next trading day with no intraday re-arm, cancels working entry intents (D-08), and fires a Telegram alert + structured log event (D-05/D-06/D-07) *(07-01/05: _is_circuit_breaker_tripped Gate 7; _handle_circuit_breaker_side_effects; _breaker_handled startup init)*
- [x] **EXIT-MODEL**: The exit model shipped in rules.json is chosen from a backtest comparison (current partial/BE/trail vs no-scale/fixed-2R vs full-size-to-1.5R+trail) using the Phase 6 backtester — not by default; Phase 7 ships the config-driven, fail-closed `exit.model` selector, and the evidence-based selection is gated on Phase 6 (plan 07-06) *(07-04: exit.model config seam + fail-closed _IMPLEMENTED_EXIT_MODELS; backtest-driven selection itself intentionally deferred — see traceability note)*

### Options Backtesting (Phase 9)

Added 2026-08-17. Offline evidence for the Phase 8 `tasty_credit_spreads` strategy before any `rules_options.json` change.

- [x] **OBT-01**: The options backtester imports `bot/options/strategy.py` (`pick_expiry`/`passes_entry_gate`/`pick_strikes`/`size_position`/`manage_decision`) and reads `rules_options.json` unchanged — same import-not-copy pattern as Phase 6 vs `bot/strategy/`
- [x] **OBT-02**: Massive data layer for option contracts reference (incl. `expired=true`) + `O:…` daily aggregates with an on-disk cache (`backtester/massive.py` pattern) and no look-ahead — entry/strike decisions use only bars ≤ the decision date
- [x] **OBT-03**: Black-Scholes IV and delta derived from option close + underlying close + DTE + risk-free rate (no chain-snapshot dependency — that endpoint is 403 on the current entitlement)
- [x] **OBT-04**: IVR computed from the backtester's own rolling ATM-IV series (252-trading-day window, matching the live `ivr` semantics)
- [x] **OBT-05**: Fill model = mid ± configurable slippage per leg, per-leg commissions, expiry settlement at intrinsic value
- [x] **OBT-06**: Hypotheses (IVR 20 vs 30, 16Δ vs 20Δ, IC vs PCS) pre-registered in a committed doc BEFORE the first real-data run, with an evidence floor (min trades) and an out-of-sample window
- [x] **OBT-07**: Output = per-trade log + summary metrics (win rate, PF, avg credit captured, max DD, Sortino) reusing `backtester/report.py` conventions

### External Strategy Research (Phase 10)

Added 2026-08-18. Backtest-only research comparing two externally-sourced (Reddit) day-trading strategies against Trend Join Long (TJL, validated no-edge 2026-08-13) and candidate improvements, using the Phase 6 backtester conventions. Does not lift the "multiple strategies" / "short selling" out-of-scope items for v1 production — those stay excluded unless a specific hypothesis is SUPPORTED and a separate follow-up phase is approved.

- [x] **XSR-01**: External strategies are extracted into explicit rules (entry/exit/stops/sizing/timeframe), separated from assumptions/interpretation, with credibility assessed (anecdotal evidence, no verified P&L, survivorship, sample size) — captured in a committed doc
- [x] **XSR-02**: A backtest-only research package (`backtester/experimental/`) implements the automatable cores of both external strategies as pluggable, tested modules reusing `SimulatedBarFeed`/`report.py` conventions (cache-keyed data, N+1-open fills, no look-ahead), without modifying `bot/` or `rules.json`
- [x] **XSR-03**: Hypotheses (external strategies vs TJL vs candidate improvements) are pre-registered in a committed doc — universe, IS/OOS windows, evidence floor (≥25 closed trades/arm per window), metric of record — before any real-data run (git history proves ordering), matching the Phase 9 OBT-06 pattern
- [x] **XSR-04**: Backtests run cache-only against already-cached windows/universe (zero new Massive requests; the Phase 9 warm-cache run is undisturbed) with realistic costs (commission + slippage) and both standard and extended metrics (CAGR, Sharpe, Sortino, max DD, PF, win rate, avg trade $, expectancy, exposure, bootstrap CI)
- [x] **XSR-05**: Each hypothesis is reported as SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with numbers; `rules.json`/`rules_options.json` are unchanged as part of this phase regardless of verdict
- [x] **XSR-06**: If a hypothesis is SUPPORTED in both IS and OOS, the exact production integration (default-off, schema-valid) is implemented and tested on a separate feature branch, not merged to `develop` as part of this phase

### Multi-Strategy Options (Phase 11)

Added 2026-09-24. Operator-approved scope lift of the "Multiple strategies / strategy framework" out-of-scope item **for the options bot only** (the equity bot stays single-strategy Trend Join Long). Design: `docs/superpowers/specs/2026-09-24-multi-strategy-options-design.md`.

- [ ] **MSO-01**: `rules_options.json` supports a `strategies` array (unique `name`; per-strategy `universe` XOR `universe_source`, `entry`, `structure`, `sizing`, `manage`) plus shared `risk`/`execution`/`service` blocks; `load_options_book(path)` returns every strategy as a flat `OptionsConfig` with the shared values flattened in; the legacy flat file shape still loads
- [ ] **MSO-02**: `load_options_config(path, strategy=None)` keeps its name and return type (flat per-strategy view, default = first strategy) so the Phase 9 backtester, UAT probe and existing tests keep working unmodified; `backtester.options_run` gains `--strategy` and projects the chosen strategy to the legacy flat shape via `legacy_view(raw, name)` before `--set` overrides, rejecting debit structures
- [ ] **MSO-03**: Config fails closed (`ConfigError`) on duplicate names, both/neither universe keys, unknown `universe_source`, an unimplemented `structure.type`, or IV-gate keys missing for a credit structure
- [ ] **MSO-04**: `bull_call_spread` structure in the pure strategy core: long call at |Δ| closest to `long_delta`, short call = listed strike closest to long + width strictly above, both legs liquid, `0 < debit ≤ max_debit_to_width × width`, BUY leg first; sized via `size_position` with per-spread risk `debit × 100`
- [ ] **MSO-05**: `manage_decision_debit` exits in order assignment guard → profit target (`profit_target_pct_of_max` of `width − debit`) → optional DTE exit; no stop loss; the debit sign convention is handled in exactly one place
- [ ] **MSO-06**: The `equity_watchlist` universe source reads today's `daily_scan` codes from the equity bot's DB (`data/bot_state.db`) through a read-only SQLite URI, capped at 20 by `rank ASC`; missing/locked/empty → zero entries for that strategy that day; no write path exists
- [ ] **MSO-07**: `option_positions.strategy_name` (idempotent guarded migration; legacy rows default `tasty_credit_spreads`); debit positions store negative `credit_per_spread` so the existing close math yields correct realized P&L for both credit and debit structures
- [ ] **MSO-08**: The ONE options process runs every strategy: per-strategy entry-scan jobs, one manage job dispatching on each position's `strategy_name`, per-strategy caps (entries/day, concurrent), global daily-loss breaker + global BP headroom + global one-position-per-underlying; alerts and the EOD report show the strategy name
- [ ] **MSO-09**: Shipped `rules_options.json` converted to the `strategies` shape with `tasty_credit_spreads` (behavior identical, proven field-for-field) and `super_bull_call`; strategy provenance doc (transcript-distilled rules, source URL, deviations) committed under `docs/research/`; all Phase 8 safety invariants unchanged

## v2 Requirements

Acknowledged but deferred — not in the current roadmap.

### Reliability & Reporting

- **CB-01**: Daily max-loss circuit breaker (halt new entries if session PnL < −2%) — *superseded by RISK-CIRCUIT (Phase 7), reframed as realized-only −2R*
- **REP-01**: Per-trade R-multiple tracking computed from the audit log
- **ALERT-05**: Richer Telegram exit alerts (R achieved, stop level, which exit rule fired)
- **REP-02**: Backtest performance report extras / parameter sweeps
- **SVC-05**: Health-check heartbeat (file-touch every N minutes)
- **ALERT-06**: System-health Telegram alerts (OpenD down, scan missed) — requires a separate monitoring loop

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Live / real-money trading | Paper (SIMULATE) only this milestone; real capital is a deliberate future decision after validation. No `TrdEnv.REAL` code path. |
| LLM/AI in the trade-decision loop | The brain is a deterministic mechanical screener; cost/latency/non-determinism unacceptable in the trade loop. Existing `/trade` AI skills stay a separate manual toolkit. |
| Multiple strategies / strategy framework | One strategy (Trend Join Long) for now; a framework is premature abstraction. **Lifted for the options bot only by operator decision 2026-09-24 (Phase 11, MSO-01..09); the equity bot stays single-strategy.** |
| Short selling | Strategy is long-only by definition. |
| Non-S&P 500 universes (broad market, crypto, options) | Single universe keeps scope and quota manageable. |
| Web dashboard / UI | Telegram is the v1 interface; no UI to build. |
| Interactive Telegram commands | Alerts are fire-and-forget one-way; command handling is out of scope for v1. |

## Traceability

Which phases cover which requirements.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CFG-01 | Phase 1 | Implemented (01-03) |
| STATE-01 | Phase 1 | Implemented (01-02) |
| SAFE-01 | Phase 1 | Implemented (01-01) |
| SAFE-02 | Phase 1 | Skeleton (01-01); full logic Phase 4 |
| SAFE-03 | Phase 1 | Skeleton (01-01); full logic Phase 4 |
| SAFE-04 | Phase 1 | Implemented (01-04) |
| SAFE-05 | Phase 1 | Implemented (01-01) |
| SVC-03 | Phase 1 | Implemented (01-04) |
| SVC-04 | Phase 1 | Implemented (01-04) |
| SCAN-01 | Phase 2 | Complete |
| SCAN-02 | Phase 2 | Complete |
| SCAN-03 | Phase 2 | Complete |
| SCAN-04 | Phase 2 | Complete |
| SCAN-05 | Phase 2 | Complete |
| SCAN-06 | Phase 2 | Complete |
| SCAN-07 | Phase 2 + Phase 5 (scheduler) | Complete |
| SCAN-08 | Phase 2 | Complete |
| SIG-01 | Phase 2 | Complete |
| SIG-02 | Phase 3 | Complete |
| SIG-03 | Phase 3 | Complete |
| SIG-04 | Phase 3 | Complete |
| RISK-01 | Phase 3 | Complete |
| RISK-02 | Phase 3 | Complete |
| RISK-03 | Phase 3 | Complete |
| RISK-04 | Phase 3 | Complete |
| RISK-05 | Phase 3 | Complete |
| EXEC-01 | Phase 4 | Complete (04-03; live round-trip UAT-verified 2026-07-02) |
| EXEC-02 | Phase 4 | Complete (04-03) |
| EXEC-03 | Phase 4 | Complete (04-03) |
| EXEC-04 | Phase 4 | Complete (04-04) |
| EXEC-05 | Phase 4 | Complete (04-03) |
| POS-01 | Phase 4 | Complete (04-01) |
| POS-02 | Phase 4 | Complete (04-01) |
| POS-03 | Phase 4 | Complete (04-01) |
| POS-04 | Phase 4 | Complete (04-04) |
| POS-05 | Phase 4 | Complete |
| SVC-01 | Phase 5 | Complete (05-01; composed end-to-end in bot/main.py per Phase 6.1) |
| SVC-02 | Phase 5 | Complete (05-02) |
| ALERT-01 | Phase 5 | Complete (05-03; live delivery UAT-verified 2026-07-02) |
| ALERT-02 | Phase 5 | Complete |
| ALERT-03 | Phase 5 | Complete |
| ALERT-04 | Phase 5 | Complete (05-03) |
| DASH-01 | Phase 5 | Complete (05-04; UAT-verified renders offline) |
| BT-01 | Phase 6 | Complete |
| BT-02 | Phase 6 | Complete |
| BT-03 | Phase 6 | Complete |
| BT-04 | Phase 6 | Complete |
| SIG-RVOL-TOD | Phase 7 | Complete (07-01, 07-02, 07-05) |
| RISK-TICK-STOP | Phase 7 + Phase 07.1 | Complete — built 07-01/07-03; wiring gap (bot/main.py not passing `gateway=`) closed by Phase 07.1 |
| RISK-CIRCUIT | Phase 7 | Complete (07-01, 07-05) |
| EXIT-MODEL | Phase 7 | Config seam complete (07-04) and fail-closed; the evidence-based backtest selection (criterion 2) remains intentionally deferred — no phase has run it |
| OBT-01 | Phase 9 | Complete (infrastructure) — see note |
| OBT-02 | Phase 9 | Complete (infrastructure) — see note |
| OBT-03 | Phase 9 | Complete (infrastructure) — see note |
| OBT-04 | Phase 9 | Complete (infrastructure) — see note |
| OBT-05 | Phase 9 | Complete (infrastructure) — see note |
| OBT-06 | Phase 9 | Complete — hypotheses pre-registered before any run, per design |
| OBT-07 | Phase 9 | Complete (infrastructure) — see note |

> **Note (Phase 9):** All 7 OBT requirements are satisfied as code/infrastructure — the data layer, Black-Scholes greeks, replay engine, CLI, and report format all exist, are tested, and the full suite is green. But `09-VERIFICATION.md` records `status: gaps_found`: (1) a critical unfixed code-review finding (CR-01 — the engine silently drops positions still open at `--end` from every reported metric, biasing results optimistically) and (2) the data layer's per-contract-bar fetch strategy against Massive's free-tier rate limit projects to ~183 hours for a single hypothesis-arm run (~90x the phase's own budget), so all 3 pre-registered hypotheses report INSUFFICIENT-EVIDENCE — zero real evidence exists for `rules_options.json`. This is a phase-goal gap, not a requirement-level one; see the v1.0 milestone audit for the open recommendation.
| XSR-01 | Phase 10 | Complete |
| XSR-02 | Phase 10 | Complete |
| XSR-03 | Phase 10 | Complete — pre-registration commit `f8439aa` precedes all run artifacts |
| XSR-04 | Phase 10 | Complete — cache-only held throughout, zero new equity fetches |
| XSR-05 | Phase 10 | Complete — H1-H8 verdicted; only H2 SUPPORTED (not a profitable arm — both exit variants PF&lt;1) |
| XSR-06 | Phase 10 | Complete (conditional) — gate mechanically TRIGGERED via H2; operator selected `defer` at the Task 2 checkpoint given both exit variants are losing configurations. No feature branch created, `bot/`/`rules.json`/`rules_options.json` untouched — this is the gate correctly closing on informed judgment, not the "nothing SUPPORTED" case |
| MSO-01 | Phase 11 | Pending |
| MSO-02 | Phase 11 | Pending |
| MSO-03 | Phase 11 | Pending |
| MSO-04 | Phase 11 | Pending |
| MSO-05 | Phase 11 | Pending |
| MSO-06 | Phase 11 | Pending |
| MSO-07 | Phase 11 | Pending |
| MSO-08 | Phase 11 | Pending |
| MSO-09 | Phase 11 | Pending |
| CB-01 | — (v2) | Deferred — superseded by RISK-CIRCUIT (Phase 7), reframed as realized-only −2R |
| REP-01 | — (v2) | Deferred — not in current roadmap |
| ALERT-05 | — (v2) | Deferred — not in current roadmap |
| REP-02 | — (v2) | Deferred — not in current roadmap |
| SVC-05 | — (v2) | Deferred — not in current roadmap |
| ALERT-06 | — (v2) | Deferred — not in current roadmap |

**Coverage:**

- v1 requirements: 47 total (1 CFG + 8 SCAN + 4 SIG + 5 RISK + 5 EXEC + 5 POS + 1 STATE + 5 SAFE + 4 SVC + 4 ALERT + 1 DASH + 4 BT)
- Mapped to phases: 47 ✓ — all 47 confirmed Complete (audited 2026-08-18; see v1.0-MILESTONE-AUDIT.md)
- Unmapped: 0 ✓
- Phase 7 strategy-optimization requirements (added 2026-07-03): SIG-RVOL-TOD, RISK-TICK-STOP, RISK-CIRCUIT, EXIT-MODEL — 4 total, all mapped to Phase 7 ✓ — all Complete (EXIT-MODEL's evidence-based selection sub-criterion remains deferred, see row above)
- Phase 9 options-backtesting requirements (added 2026-08-17): OBT-01..OBT-07 — 7 total, all mapped to Phase 9 ✓ — infrastructure Complete; phase goal (real evidence) not yet achieved, see note above
- Phase 10 external-strategy-research requirements (added 2026-08-18): XSR-01..XSR-06 — 6 total, all mapped to Phase 10 ✓ — all Complete
- Phase 11 multi-strategy-options requirements (added 2026-09-24): MSO-01..MSO-09 — 9 total, all mapped to Phase 11 ✓ — Pending
- v2 requirements: 6 total, all deferred (not in current roadmap) — listed above for traceability completeness, not phase-mapped

---
*Requirements defined: 2026-06-23*
*Last updated: 2026-08-18 — v1.0 milestone audit: corrected 19 stale checkboxes/traceability rows that had never been updated since 2026-06-23 despite Phases 4-7 shipping and passing verification (EXEC-01..05, POS-01..04, SVC-01/02, ALERT-01/04, DASH-01, SIG-RVOL-TOD, RISK-TICK-STOP, RISK-CIRCUIT, EXIT-MODEL — all confirmed SATISFIED against each phase's own VERIFICATION.md, cross-checked live against current code via the milestone integration checker); marked Phase 10's XSR-01..06 Complete; added the 6 orphaned v2 REQ-IDs to the traceability table; annotated Phase 9's OBT-01..07 with its still-open goal-level gap*
