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
- [ ] **SIG-02**: Entry signals are evaluated only on closed 5m bars — never mid-bar (no repainting)
- [ ] **SIG-03**: An entry triggers when price is above the premarket high, above today's HOD, and intraday RVOL ≥ 2.0, within 10:05–15:30 ET
- [ ] **SIG-04**: No new entries when 5 concurrent positions are open or after the 15:30 ET cutoff

### Risk & Sizing

- [ ] **RISK-01**: Each position is sized to risk 1% of current account equity (read live, not cached)
- [ ] **RISK-02**: Position notional is capped at 10% of portfolio value
- [ ] **RISK-03**: Initial stop is computed as low-of-day − 1%
- [ ] **RISK-04**: Maximum 5 concurrent positions is enforced at order-submission time
- [ ] **RISK-05**: A daily new-entry cap (`max_trades_per_day`, default 5) is enforced separately from the concurrent cap — bounds total daily entries even as positions close and free slots

### Execution

- [ ] **EXEC-01**: Orders are placed through the Moomoo API on the paper (SIMULATE) account only
- [ ] **EXEC-02**: Exits use limit orders at aggressive prices (no reliance on paper market-order fills)
- [ ] **EXEC-03**: Pending orders use a TTL with cancel-replace if unfilled
- [ ] **EXEC-04**: Duplicate-order prevention via a broker-verified per-symbol position guard
- [ ] **EXEC-05**: Stop-out and fill reconciliation matches broker fills by `order_id`, never by quantity (quantity matching produces false stop-outs after a partial exit)

### Position Lifecycle

- [ ] **POS-01**: Take ⅓ off the position at 0.75R (partial profit)
- [ ] **POS-02**: Move the stop to breakeven at 1.0R
- [ ] **POS-03**: After breakeven, trail the stop on 5m swing lows (2/2 pattern)
- [ ] **POS-04**: Force-close all open positions at 15:51 ET (calendar-aware for half-days)
- [ ] **POS-05**: Per-position lifecycle state (the FSM) is persisted and survives restarts

### Strategy Configuration

- [x] **CFG-01**: All strategy parameters (universe/daily/intraday filters, time gates, exit rules, risk + `max_trades_per_day`) are externalized to a `rules.json` config loaded at startup as the single source of truth — no strategy constants hardcoded in Python; live bot and backtester read the same file *(01-03: rules.json + jsonschema loader + StrategyConfig + TrendJoinLong config-driven)*

### State & Safety

- [x] **STATE-01**: Durable SQLite state with atomic writes for positions, stops, scans, and trades *(01-02: StateStore + migration 0001 + atomic_write_json crash-injection proven)*
- [x] **SAFE-01**: Hard paper-trading guard at startup — an explicit `PAPER_TRADING=true` config flag is required AND the selected account's environment is asserted to be SIMULATE (via broker account-type check); any mismatch or REAL account hard-exits before any order path is reachable *(01-01: triple fail-closed guard implemented)*
- [ ] **SAFE-02**: Startup reconciliation against broker truth completes before any signal processing
- [ ] **SAFE-03**: A broker-reconciliation loop (every 60–90s) diffs in-memory state vs broker truth; broker wins
- [x] **SAFE-04**: Kill switch (file-touch or SIGINT) triggers graceful shutdown with a state flush *(01-04: KillSwitch implemented — sentinel file + SIGINT + idempotent trigger + state-flush callback)*
- [x] **SAFE-05**: Append-only trade/order audit log (JSONL), extending the existing `~/.futu_trade_audit.jsonl` pattern *(01-01: append_audit() implemented)*

### Service & Orchestration

- [ ] **SVC-01**: Long-running supervised service with an internal scheduler (premarket scan → intraday loop → EOD flatten)
- [ ] **SVC-02**: OpenD connectivity watchdog (poll `get_global_state` ~every 60s); pause order placement on failure
- [x] **SVC-03**: Structured, rotating application logging *(01-04: structlog RotatingFileHandler JSON + ConsoleRenderer stderr)*
- [x] **SVC-04**: All timing uses US Eastern (`zoneinfo`), correct across DST *(01-04: ET = ZoneInfo("America/New_York"), now_et(), to_et(); DST-tested)*

### Alerts

- [ ] **ALERT-01**: Telegram alert on entry (ticker, size, entry price, initial stop)
- [ ] **ALERT-02**: Telegram alert on each exit event (partial, breakeven move, trail-stop, stop-out, force-close)
- [ ] **ALERT-03**: Daily Telegram summary after force-close (trades, win/loss, realized PnL, open risk)
- [ ] **ALERT-04**: Alert delivery failures never block or crash the trade loop

### Reporting

- [ ] **DASH-01** *(optional)*: A static, offline, no-JS HTML performance dashboard is generated (R-multiple histogram, open-positions table, last-20 closed trades) alongside the Telegram daily summary

### Backtesting

- [ ] **BT-01**: Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot
- [ ] **BT-02**: Backtester enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time
- [ ] **BT-03**: Backtester produces a performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV)
- [ ] **BT-04**: Backtest historical data is sourced from yfinance (or flat CSV/Parquet export), not Moomoo, avoiding broker historical-quota limits at 500-symbol scale

## v2 Requirements

Acknowledged but deferred — not in the current roadmap.

### Reliability & Reporting

- **CB-01**: Daily max-loss circuit breaker (halt new entries if session PnL < −2%)
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
| Multiple strategies / strategy framework | One strategy (Trend Join Long) for now; a framework is premature abstraction. |
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
| SIG-02 | Phase 3 | Pending |
| SIG-03 | Phase 3 | Pending |
| SIG-04 | Phase 3 | Pending |
| RISK-01 | Phase 3 | Pending |
| RISK-02 | Phase 3 | Pending |
| RISK-03 | Phase 3 | Pending |
| RISK-04 | Phase 3 | Pending |
| RISK-05 | Phase 3 | Pending |
| EXEC-01 | Phase 4 | Pending |
| EXEC-02 | Phase 4 | Pending |
| EXEC-03 | Phase 4 | Pending |
| EXEC-04 | Phase 4 | Pending |
| EXEC-05 | Phase 4 | Pending |
| POS-01 | Phase 4 | Pending |
| POS-02 | Phase 4 | Pending |
| POS-03 | Phase 4 | Pending |
| POS-04 | Phase 4 | Pending |
| POS-05 | Phase 4 | Pending |
| SVC-01 | Phase 5 | Pending |
| SVC-02 | Phase 5 | Pending |
| ALERT-01 | Phase 5 | Pending |
| ALERT-02 | Phase 5 | Pending |
| ALERT-03 | Phase 5 | Pending |
| ALERT-04 | Phase 5 | Pending |
| DASH-01 | Phase 5 | Pending |
| BT-01 | Phase 6 | Pending |
| BT-02 | Phase 6 | Pending |
| BT-03 | Phase 6 | Pending |
| BT-04 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 47 total (1 CFG + 8 SCAN + 4 SIG + 5 RISK + 5 EXEC + 5 POS + 1 STATE + 5 SAFE + 4 SVC + 4 ALERT + 1 DASH + 4 BT)
- Mapped to phases: 47 ✓
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-23*
*Last updated: 2026-06-23 — refined with humbledtrader.com build-guide inputs (Steps 4–13), adapted IBKR→Moomoo: added CFG-01 (rules.json), SCAN-06/07/08 (yfinance data, intraday re-scan, top-20 cap), RISK-05 (daily entry cap), EXEC-05 (order_id fill matching), DASH-01 (HTML dashboard), BT-04 (yfinance backtest data); strengthened SAFE-01 and SIG-01*
