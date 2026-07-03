# Phase 07: Strategy Optimization - Context

**Gathered:** 2026-07-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Close the four structural strategy weaknesses identified by quant feedback (2026-07-03) on the Trend Join Long system:

1. **RVOL-TOD** — replace the full-day-average RVOL denominator with a time-of-day-normalized baseline (14-day average of cumulative volume *at the same time bucket*), restoring realistic signal frequency without loosening the institutional-interest filter
2. **Exit restructure** — select the shipped exit model from backtest evidence (current 0.75R-partial/1R-breakeven vs no-scale/fixed-2R vs full-size-to-1.5R + trail variants) instead of by default
3. **Tick-level stop invalidation** — move stop-outs from 5m bar-close evaluation to tick granularity via broker-side protective stop orders
4. **−2R daily circuit breaker** — halt new entries for the session once cumulative daily realized loss reaches −2R

All new thresholds live in `rules.json` (CFG-01 — locked project constraint). Sizing basis is fixed $100k (`risk.sizing_equity_usd`), so 1R = $1,000 and the breaker trips at −$2,000 realized.

</domain>

<decisions>
## Implementation Decisions

### Stop mechanism (tick-level invalidation)
- **D-01 (Mechanism):** Broker-side **Stop-Market** protective orders. **EXEC-02 is amended** from "never place a market order" to "never place a market order EXCEPT broker-side protective stop orders." Rationale: guaranteed exit at the tick and — decisive — protection survives bot/OpenD crashes (the June incident class). Slippage unbounded but acceptable on S&P large caps.
- **D-02 (SIMULATE fallback):** If research finds the SIMULATE paper account does not support conditional/stop orders, ship a **bot-side tick/quote monitor** as the paper fallback that fires the existing limit-escalation exit immediately on stop violation. The broker-side stop path is still built and tested behind a config flag; account type selects the path. Research MUST verify Moomoo/Futu conditional-order support in SIMULATE.
- **D-03 (Coexistence — split duties):** The tick/broker-side stop owns **invalidation only**. The bar-close FSM keeps owning partial-profit, breakeven, and trail-ratchet decisions. The existing bar-close stop check is retained as a redundant backstop for broker-order rejection/cancellation. Clear single authority per concern, defense in depth.
- **D-04 (Trail sync):** On every trail ratchet (breakeven at 1R, swing-low updates), **cancel-replace the broker-side stop** to mirror `trail_stop`. Use the finding-1.4 pattern (re-query post-cancel `dealt_qty` before placing the replacement). The brief no-stop window during replace is an accepted cost; never loosen (D-11 invariant applies to the broker order too).

### Circuit breaker (−2R daily halt)
- **D-05 (Trigger):** **Realized-only** — sum of closed-trade P&L for the session ≤ −2.0R (−$2,000 at the fixed basis) trips the breaker. Computed from the trades table; no mark-to-market. Threshold is a `rules.json` key (e.g., `risk.daily_circuit_breaker_r: 2.0`).
- **D-06 (Scope):** Trip halts **new entries only** — no new OrderIntents emitted or consumed for the rest of the session. Existing positions continue under full management (stops, partials, trail, force-close untouched).
- **D-07 (Reset/override):** **Auto-reset at next trading day's session start. No intraday re-arm/override** — the breaker exists to remove discretion on tilt days. Trip fires a Telegram alert and a structured log event. Breaker state is **persisted** so a mid-day bot restart does NOT clear it.
- **D-08 (In-flight entries):** On trip, **cancel any working entry orders** and mark their intents ABANDONED via the existing cancel + post-cancel dealt_qty machinery. Partial fills that already landed are kept and managed normally. No new exposure after −2R.

### Claude's Discretion
- **RVOL-TOD construction:** data source for 14-day intraday cumulative-volume curves (yfinance 5m ~60-day window is the known candidate), time-bucket granularity, caching strategy, and whether the 2.0 threshold needs retuning after the redefinition. Researcher investigates; planner decides.
- **Sequencing & backtest gating:** which items ship to the live bot immediately (items 3+4 are pure risk reducers and plausibly don't need backtest validation) vs gated behind Phase 6 backtester evidence (item 2 explicitly requires it; item 1's ~3x signal-frequency change may warrant validation). Planner decides wave structure; Phase 6 dependency applies at minimum to the exit-model selection (roadmap success criterion 2).
- Alerting copy/details, log event names, config key naming — follow existing conventions.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy & feedback
- `docs/2026-07-03-strategy-analysis-trend-join-long.md` — trader's analysis that independently identified the same gaps (bar-close stops, no kill switch, untested exit params); aligns with and motivates this phase
- `rules.json` — single source of truth for all strategy thresholds; every new parameter (RVOL-TOD min, circuit-breaker R, exit-model params, stop-mode flag) is added here
- `.planning/ROADMAP.md` §Phase 7 — goal and success criteria for this phase; §Phase 6 — backtester scope this phase depends on for exit-model selection

### Code touched by these changes
- `bot/strategy/indicators.py` — `rvol()` (current full-day-denominator implementation to be replaced/extended with TOD baseline)
- `bot/signal/signal_engine.py` — intraday gates (I3 RVOL check; circuit-breaker gate joins the existing gate stack)
- `bot/position/manager.py` — bar-close FSM (partial/breakeven/trail ownership per D-03; trail ratchet triggers D-04 stop replace)
- `bot/execution/engine.py` — cancel + post-cancel dealt_qty pattern (finding 1.4) reused for D-04 and D-08
- `bot/gateway/gateway.py` — order placement surface; broker-side stop order support lands here
- `bot/state/store.py` — trades table (D-05 realized P&L source); breaker state persistence (D-07)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Finding-1.4 cancel/re-query machinery (`bot/execution/engine.py`): exact pattern for D-04 trail-sync replaces and D-08 in-flight cancellation
- Signal-engine gate stack (`bot/signal/signal_engine.py`): the circuit breaker is a new gate alongside entry-window/concurrent-cap/daily-cap gates — same shape, same tests pattern
- `get_force_close_time_et` / calendar-awareness: breaker daily reset keys off the same trading-day concept
- Telegram alerter + structured log conventions: trip alert follows existing ALERT-0x patterns

### Established Patterns
- CFG-01: no strategy literals in Python — all four changes are config-driven
- TDD regression-first commits (`test(...)` RED → `fix/feat(...)` GREEN) — Phase 06.2 discipline continues
- D-11 never-loosen trail invariant — extends to the broker-side stop order level

### Integration Points
- Stop orders enter at fill time (`bot/service/bot.py::_process_bar` fill branch registers the position — the broker stop placement hooks in here or in `PositionManager.on_fill`)
- Breaker check gates `SignalEngine.on_bar` before intent emission; trip-time cancellation reaches into `ExecutionEngine`
- RVOL-TOD baseline is computed at scan time (scanner) and consumed at signal time (I3 gate)

</code_context>

<specifics>
## Specific Ideas

- Feedback's exit-model candidates to backtest: "No-Scale / Fixed 2R" and "Full size to 1.5R, then trail by 15m EMA"; guidance: "avoid taking profits before 1.5R"
- Feedback's RVOL-TOD form: cumulative volume at time T vs 14-day average of cumulative volume at the same clock time
- Feedback's breaker rule verbatim: `if cumulative_daily_realized_loss <= -2.0R: halt_all_new_orders = true`

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (Sequencing of live-shippable items vs backtest-gated items is Claude's discretion within this phase, not deferred.)

</deferred>

---

*Phase: 07-strategy-optimization*
*Context gathered: 2026-07-03*
