# Requirements: AI S&P Trading Bot (Trend Join Long)

**Defined:** 2026-06-23
**Core Value:** The bot autonomously executes the Trend Join Long strategy end-to-end on a paper account — scan, enter, manage risk, exit, and report — correctly and unattended.

## v1 Requirements

Requirements for the initial release. Each maps to roadmap phases. Derived from the
fully-specified strategy (PROJECT.md) and the research table-stakes (`.planning/research/`).

### Scanner

- [ ] **SCAN-01**: Bot fetches the current S&P 500 constituent list as the scan universe
- [ ] **SCAN-02**: Premarket scan filters the universe by price ≥ $3 and the daily setup (above prior-day high, prior close > SMA200, gap ≥ 3% from prior close)
- [ ] **SCAN-03**: Scan computes a 14-day RVOL baseline using only prior completed trading days (no look-ahead)
- [ ] **SCAN-04**: Scan runs only on NYSE trading days (holiday/half-day aware) and writes a stored daily watchlist
- [ ] **SCAN-05**: The daily scan is idempotent (re-running the same day does not duplicate the watchlist)

### Signals

- [ ] **SIG-01**: Bot subscribes to 5m bars only for watchlist candidates (snapshot-first to respect subscription quota)
- [ ] **SIG-02**: Entry signals are evaluated only on closed 5m bars — never mid-bar (no repainting)
- [ ] **SIG-03**: An entry triggers when price is above the premarket high, above today's HOD, and intraday RVOL ≥ 2.0, within 10:05–15:30 ET
- [ ] **SIG-04**: No new entries when 5 concurrent positions are open or after the 15:30 ET cutoff

### Risk & Sizing

- [ ] **RISK-01**: Each position is sized to risk 1% of current account equity (read live, not cached)
- [ ] **RISK-02**: Position notional is capped at 10% of portfolio value
- [ ] **RISK-03**: Initial stop is computed as low-of-day − 1%
- [ ] **RISK-04**: Maximum 5 concurrent positions is enforced at order-submission time

### Execution

- [ ] **EXEC-01**: Orders are placed through the Moomoo API on the paper (SIMULATE) account only
- [ ] **EXEC-02**: Exits use limit orders at aggressive prices (no reliance on paper market-order fills)
- [ ] **EXEC-03**: Pending orders use a TTL with cancel-replace if unfilled
- [ ] **EXEC-04**: Duplicate-order prevention via a broker-verified per-symbol position guard

### Position Lifecycle

- [ ] **POS-01**: Take ⅓ off the position at 0.75R (partial profit)
- [ ] **POS-02**: Move the stop to breakeven at 1.0R
- [ ] **POS-03**: After breakeven, trail the stop on 5m swing lows (2/2 pattern)
- [ ] **POS-04**: Force-close all open positions at 15:51 ET (calendar-aware for half-days)
- [ ] **POS-05**: Per-position lifecycle state (the FSM) is persisted and survives restarts

### State & Safety

- [ ] **STATE-01**: Durable SQLite state with atomic writes for positions, stops, scans, and trades
- [ ] **SAFE-01**: Assert the SIMULATE environment at startup; hard-exit if the account is REAL
- [ ] **SAFE-02**: Startup reconciliation against broker truth completes before any signal processing
- [ ] **SAFE-03**: A broker-reconciliation loop (every 60–90s) diffs in-memory state vs broker truth; broker wins
- [ ] **SAFE-04**: Kill switch (file-touch or SIGINT) triggers graceful shutdown with a state flush
- [ ] **SAFE-05**: Append-only trade/order audit log (JSONL), extending the existing `~/.futu_trade_audit.jsonl` pattern

### Service & Orchestration

- [ ] **SVC-01**: Long-running supervised service with an internal scheduler (premarket scan → intraday loop → EOD flatten)
- [ ] **SVC-02**: OpenD connectivity watchdog (poll `get_global_state` ~every 60s); pause order placement on failure
- [ ] **SVC-03**: Structured, rotating application logging
- [ ] **SVC-04**: All timing uses US Eastern (`zoneinfo`), correct across DST

### Alerts

- [ ] **ALERT-01**: Telegram alert on entry (ticker, size, entry price, initial stop)
- [ ] **ALERT-02**: Telegram alert on each exit event (partial, breakeven move, trail-stop, stop-out, force-close)
- [ ] **ALERT-03**: Daily Telegram summary after force-close (trades, win/loss, realized PnL, open risk)
- [ ] **ALERT-04**: Alert delivery failures never block or crash the trade loop

### Backtesting

- [ ] **BT-01**: Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot
- [ ] **BT-02**: Backtester enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time
- [ ] **BT-03**: Backtester produces a performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV)

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

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (to be filled by roadmapper) | — | Pending |

**Coverage:**
- v1 requirements: 35 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 35 ⚠️

---
*Requirements defined: 2026-06-23*
*Last updated: 2026-06-23 after initial definition*
