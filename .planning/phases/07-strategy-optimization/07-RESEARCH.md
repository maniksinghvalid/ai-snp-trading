# Phase 07: Strategy Optimization — Research

**Researched:** 2026-07-03
**Domain:** Quantitative trading strategy improvements — RVOL normalization, exit model selection, broker-side stop orders, daily circuit breaker
**Confidence:** HIGH (codebase verified), MEDIUM (SIMULATE stop-order support — unverified empirically)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 (Mechanism):** Broker-side **Stop-Market** protective orders (`OrderType.STOP`). EXEC-02 is amended: never place a market order EXCEPT broker-side protective stop orders. Rationale: guaranteed exit at the tick and protection survives bot/OpenD crashes. Slippage unbounded but acceptable on S&P large caps.
- **D-02 (SIMULATE fallback):** If research finds SIMULATE paper account does not support conditional/stop orders, ship a **bot-side tick/quote monitor** as the paper fallback that fires the existing limit-escalation exit immediately on stop violation. The broker-side stop path is still built and tested behind a config flag; account type selects the path. Research MUST verify Moomoo/Futu conditional-order support in SIMULATE.
- **D-03 (Coexistence — split duties):** The tick/broker-side stop owns **invalidation only**. The bar-close FSM keeps owning partial-profit, breakeven, and trail-ratchet decisions. The existing bar-close stop check is retained as a redundant backstop for broker-order rejection/cancellation.
- **D-04 (Trail sync):** On every trail ratchet (breakeven at 1R, swing-low updates), **cancel-replace the broker-side stop** to mirror `trail_stop`. Use the finding-1.4 pattern (re-query post-cancel `dealt_qty` before placing the replacement). The brief no-stop window during replace is an accepted cost; never loosen (D-11 invariant applies to the broker order too).
- **D-05 (Trigger):** **Realized-only** — sum of closed-trade P&L for the session ≤ −2.0R (−$2,000 at the fixed basis) trips the breaker. Computed from the trades table; no mark-to-market. Threshold is a `rules.json` key (`risk.daily_circuit_breaker_r: 2.0`).
- **D-06 (Scope):** Trip halts **new entries only** — no new OrderIntents emitted or consumed for the rest of the session. Existing positions continue under full management (stops, partials, trail, force-close untouched).
- **D-07 (Reset/override):** **Auto-reset at next trading day's session start. No intraday re-arm/override.** Trip fires a Telegram alert and a structured log event. Breaker state is **persisted** so a mid-day bot restart does NOT clear it.
- **D-08 (In-flight entries):** On trip, **cancel any working entry orders** and mark their intents ABANDONED via the existing cancel + post-cancel dealt_qty machinery. Partial fills that already landed are kept and managed normally.
- **CFG-01 (locked project constraint):** All thresholds in `rules.json` — no strategy literals in Python.
- **EXEC-02 amendment:** Market orders are forbidden EXCEPT broker-side protective stop orders (D-01).

### Claude's Discretion

- **RVOL-TOD construction:** data source for 14-day intraday cumulative-volume curves (yfinance 5m ~60-day window is the known candidate), time-bucket granularity, caching strategy, and whether the 2.0 threshold needs retuning after the redefinition. Researcher investigates; planner decides.
- **Sequencing & backtest gating:** which items ship to the live bot immediately (items 3+4 are pure risk reducers and plausibly don't need backtest validation) vs gated behind Phase 6 backtester evidence (item 2 explicitly requires it; item 1's ~3x signal-frequency change may warrant validation). Planner decides wave structure; Phase 6 dependency applies at minimum to the exit-model selection.
- Alerting copy/details, log event names, config key naming — follow existing conventions.

### Deferred Ideas (OUT OF SCOPE)

None.

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SIG-RVOL-TOD | Intraday RVOL compares cumulative volume at time T against the 14-day average of cumulative volume at the same time-of-day bucket (no full-day-average denominator before close) | §RVOL-TOD section: yfinance 5m data availability, new `tod_baselines` table design, BarAggregator `_session_volume` tracking, schema migration plan |
| EXIT-MODEL | The exit model shipped in rules.json is chosen from a backtest comparison using the Phase 6 backtester | §Exit Model section: backtester does NOT exist yet (critical finding); gating strategy documented |
| RISK-TICK-STOP | A stop violation is acted on at tick/quote granularity (broker-side trigger order or quote-driven exit), not at the next 5m bar close | §Broker-Side Stop section: `OrderType.STOP` + `aux_price` verified in SDK; SIMULATE support unverified (critical gap); fallback design documented |
| RISK-CIRCUIT | When cumulative daily realized loss reaches -2R, all new entries are halted for the rest of the session; halt persists through restart; resets next trading day | §Circuit Breaker section: `trades` table P&L source verified, `meta` table persistence pattern documented, gate integration point identified |

</phase_requirements>

---

## Summary

Phase 7 closes four structural strategy weaknesses identified by quant review on 2026-07-03. Two of the four changes (RISK-TICK-STOP and RISK-CIRCUIT) are pure risk reducers that can ship independently of any backtesting infrastructure. One (SIG-RVOL-TOD) restructures the intraday volume gate and is technically self-contained but may benefit from backtest validation of signal frequency. The fourth (EXIT-MODEL) is blocked by a hard dependency: the Phase 6 backtester does not exist at all in the repository — not a single line of backtester code has been written.

The moomoo SDK confirms `OrderType.STOP` is available with `aux_price` as the trigger price parameter. However, whether SIMULATE paper accounts honor stop orders is empirically unverified. An alternative quote-subscription fallback path must be built and selected via config flag (D-02). The circuit breaker reuses the existing `trades` table `realized_pnl` aggregation (`StateStore.get_daily_trade_stats`) and persists its tripped state in the already-present `meta` key-value table. RVOL-TOD requires a new SQLite table (`tod_baselines`) added via migration 0005, yfinance 5m history downloaded at scan time, and a cumulative session volume tracking field added to `BarAggregator`.

**Primary recommendation:** Phase 7 should sequence in two gated waves: Wave A (RISK-TICK-STOP + RISK-CIRCUIT — shippable now), Wave B (SIG-RVOL-TOD — after scan validation), Wave C (EXIT-MODEL — after Phase 6 backtester exists). The plan must explicitly document the Phase 6 dependency for EXIT-MODEL and not schedule that work until Phase 6 is complete.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| RVOL-TOD baseline computation | Scanner (premarket + rescan) | StateStore (migration + tod_baselines table) | Data is available at scan time; computing at signal time is too late and too expensive |
| RVOL-TOD signal evaluation | Signal engine (on_bar gate I3) | BarAggregator (cumulative volume tracking) | Signal gate owns threshold check; aggregator owns per-bar state |
| Broker-side stop placement | Gateway (new place_stop_order method) | ExecutionEngine / PositionManager (wiring) | All SDK order calls go through MoomooGateway |
| Stop order cancel-replace (trail sync) | PositionManager (_trigger_trail_up, _trigger_breakeven) | Gateway (cancel_order + place_stop_order) | FSM transitions own the when-to-replace decision |
| Fallback tick/quote monitoring | BarAggregator (new QuoteTickHandlerBase for SubType.QUOTE pushes) | PositionManager (on_quote callback) | Quote pushes arrive via the subscription path already in place |
| Circuit breaker evaluation | SignalEngine (new Gate 7 in on_bar) | StateStore (realized P&L read + meta key write) | Gate belongs in signal engine alongside concurrent-cap and daily-cap gates |
| Circuit breaker persistence | StateStore (meta table: get/set circuit_breaker_date) | — | meta table is the existing key-value store for session-scoped flags |
| In-flight cancellation on trip | ExecutionEngine (D-08 cancel working entries) | StateStore (mark intents ABANDONED) | Reuses the existing cancel + post-cancel-dealt_qty pattern (finding 1.4) |
| Exit model selection | Backtester (Phase 6) | rules.json (config update after analysis) | A live code change to exit logic requires backtest evidence first |

---

## Standard Stack

### Core (no new packages — all reuse existing stack)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| moomoo-api | >=10.4.6408,<11.0 | Broker orders including stop orders (`OrderType.STOP`, `aux_price`) | Already installed; `OrderType.STOP` confirmed present [VERIFIED: local inspection] |
| yfinance | installed | 5m intraday historical bars for TOD baseline computation | Already installed for daily scan data |
| pandas | installed | DataFrame operations on 5m bar history | Already installed project-wide |
| sqlite3 (stdlib) | stdlib | New `tod_baselines` table and `meta` circuit-breaker state | Already the project state layer |
| pytest | installed | Regression-first TDD (RED → GREEN pattern from Phase 06.2) | Existing test infrastructure |

### No new packages to install

This phase adds no external package dependencies. All capabilities use the existing stack. The Package Legitimacy Audit section is skipped (no installations).

---

## Critical Findings

### Finding C-1: Phase 6 backtester does not exist [VERIFIED: repo grep]

`find /repo -name "*.py" | grep -i backtest` returns zero results. No `backtester/` directory exists. The ROADMAP.md marks Phase 6 as "Not started." EXIT-MODEL (success criterion 2 — backtest-selected exit model) is therefore **hard-blocked** until Phase 6 is built. The planner must NOT schedule EXIT-MODEL work within Phase 7 unless Phase 6 is completed first, which contradicts the roadmap ordering (Phase 6 → Phase 7). The practical sequencing decision: plan for EXIT-MODEL as a final Phase 7 wave, gated on Phase 6 completion.

### Finding C-2: moomoo SDK has OrderType.STOP with aux_price parameter [VERIFIED: local inspection]

Confirmed on the installed moomoo-api:
```python
from moomoo import OrderType
# OrderType.STOP confirmed present
# place_order signature includes aux_price parameter
```

`OpenSecTradeContext.place_order()` signature:
```python
def place_order(self, price, qty, code, trd_side,
                order_type=OrderType.NORMAL,
                aux_price=None, ...)
```

For a stop-market order: `price=0.0, order_type=OrderType.STOP, aux_price=<stop_trigger_price>`. When the market price touches `aux_price`, the order converts to a market order and executes. [VERIFIED: local inspection of installed SDK source]

`modify_order()` also accepts `aux_price` for updating a stop's trigger price. [VERIFIED: local inspection]

### Finding C-3: SIMULATE stop order support is UNVERIFIED [ASSUMED]

The codebase has multiple comments about SIMULATE behavioral differences (e.g., `deal_list_query` is unsupported, returning "Paper trading does not support deal data."). Whether `OrderType.STOP` orders are honored in SIMULATE is not documented anywhere in the codebase or in Phase 4 research notes. CONTEXT.md D-02 explicitly flags this: "Research MUST verify." Without live SIMULATE testing, the answer is unknown. The fallback path (D-02 quote-driven bot-side monitor) must be built regardless.

### Finding C-4: Current RVOL implementation uses single-bar volume vs full-day average [VERIFIED: code]

`signal_engine.py`: `rvol = event.volume / rvol_baseline`

- `event.volume` = volume of the closed 5m bar (from moomoo K_5M push, single-bar volume) [VERIFIED: bar_aggregator.py `_cur_bar[code]["volume"]`]
- `rvol_baseline` = `daily_scan.rvol_baseline` = mean of prior 14 full-day daily volumes [VERIFIED: scanner.py line ~138: `float(prior_sorted["volume"].mean())` where `prior_frame` is from yfinance daily OHLCV]

This means: RVOL = single_5m_bar_volume / full_day_average_volume. At 10am, this gate only fires on stocks where a single 5m bar carries 2× the entire daily average volume — an extremely rare event. This is exactly the "stricter than it looks" analysis in the quant feedback. The TOD fix replaces the denominator with a time-of-day-bucketed baseline, making the comparison fair at any time of day.

### Finding C-5: Daily realized P&L is already aggregated in StateStore [VERIFIED: code]

`StateStore.get_daily_trade_stats(session_date)` returns `realized_pnl = SUM((exit_price - entry_price) * quantity)` from the `trades` table for all closes on that session date. The circuit breaker check can call this method directly. No new query is needed.

### Finding C-6: meta table already exists for persistent flags [VERIFIED: migrations.py]

Migration 0001 creates:
```sql
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
```

The circuit breaker tripped state can be stored as:
- `meta.key = "circuit_breaker_tripped_date"`, `meta.value = "2026-07-03"` (ET date string)
- On session start: if the stored date < today's ET date → auto-reset (D-07)
- On trip: write today's date
- On reset: delete the row (or set to NULL)

No schema migration needed for the circuit breaker persistence.

### Finding C-7: Gate stack for circuit breaker [VERIFIED: code]

`SignalEngine.on_bar()` currently has 6 sequential gates. Gate 7 for the circuit breaker inserts after Gate 6 (daily cap) — or more defensively, after Gate 3 (entry window) but before the broker call in Gate 4 (position cap). Since the breaker halts new entries entirely, it should check EARLY (after time-window gate, before broker calls) to avoid unnecessary SDK calls on tripped days. The signal engine already owns `_store` and `_cfg`, so the check is straightforward.

### Finding C-8: yfinance 5m data window sufficient for TOD baseline [VERIFIED: local inspection]

`yf.download(interval="5m")` documentation: "Intraday data cannot extend last 60 days." At 60 calendar days ≈ 42 trading days, this provides 3× the 14-session TOD baseline needed. Time-range form (`start=`, `end=`) can be used for precision. [VERIFIED: local yfinance inspection]

---

## Architecture Patterns

### System Architecture Diagram (Phase 7 changes only)

```
                      SCAN TIME (8:30 ET)
                      ┌──────────────────────────────────────────┐
yfinance 5m history   │  scanner.py                              │
(60d, per candidate)  │  ├── download_intraday_5m(symbol, 14d)   │
──────────────────▶   │  ├── compute_tod_baselines()             │
                      │  └── store.upsert_tod_baselines()         │──▶ tod_baselines table
                      └──────────────────────────────────────────┘

                      SESSION START
                      ┌───────────────────────────────────────────┐
                      │  PositionManager / ExecutionEngine         │
                      │  on_fill() → place_stop_order(code, qty,  │
MoomooGateway         │             trail_stop, SELL)             │
.place_stop_order()──▶│  (OrderType.STOP, aux_price=trail_stop)   │──▶ broker_stop_order_id
(or fallback:         └───────────────────────────────────────────┘
 quote subscription
 on_quote() check)

                      EVERY CLOSED 5m BAR
  K_5M push           ┌──────────────────────────────────────────────────┐
  ──────────────────▶ │ BarAggregator._handle_row()                      │
                      │  ├── update _session_volume[code] (new: +=vol)   │
                      │  └── emit BarEvent(cum_volume=_session_volume)    │
                      └──────────────┬───────────────────────────────────┘
                                     │ BarEvent (with cum_volume)
                                     ▼
                      ┌──────────────────────────────────────────────────┐
                      │ SignalEngine.on_bar()                            │
                      │  Gate 1: premarket high                          │
                      │  Gate 2: passes_intraday_filters (I1/I2/I3-TOD)  │
                      │    └── I3-TOD: tod_baseline = store.get_tod(...)  │◀─ tod_baselines
                      │         rvol = event.cum_volume / tod_baseline   │
                      │  Gate 3: entry window                            │
                      │  Gate 4: concurrent cap                          │
                      │  Gate 5: re-entry gate                           │
                      │  Gate 6: daily cap                               │
                      │  Gate 7 (NEW): circuit breaker check             │◀─ meta + trades
                      │    realized_pnl = store.get_daily_trade_stats()  │
                      │    if breaker_tripped → return None              │
                      └──────────────────────────────────────────────────┘

                      FSM TRANSITION: BREAKEVEN or TRAIL_UP
                      ┌──────────────────────────────────────────────────┐
                      │ PositionManager._trigger_breakeven /             │
                      │                _trigger_trail_up                 │
                      │  ├── cancel existing broker stop order           │
                      │  ├── re-query dealt_qty (finding-1.4 pattern)    │
                      │  └── place_stop_order(new trail_stop)            │
                      └──────────────────────────────────────────────────┘

                      CIRCUIT BREAKER TRIP
                      ┌──────────────────────────────────────────────────┐
                      │ SignalEngine (Gate 7) or ExecutionEngine          │
                      │  ├── store.set_circuit_breaker_date(today)       │──▶ meta table
                      │  ├── cancel all PENDING entry orders (D-08)      │
                      │  ├── mark intents ABANDONED                      │
                      │  └── fire Telegram alert                         │
                      └──────────────────────────────────────────────────┘
```

### Recommended Project Structure Changes

```
bot/
├── gateway/
│   └── gateway.py          # +place_stop_order(code, qty, stop_price, side)
│                           # +update_stop_order(order_id, new_stop_price)
│                           # +_quote_tick_handler (fallback path)
├── signal/
│   ├── bar_aggregator.py   # +_session_volume[code] tracking, BarEvent.cum_volume
│   └── events.py           # +cum_volume field on BarEvent
│   └── signal_engine.py    # +Gate 7 circuit breaker
├── scanner/
│   └── scanner.py          # +_compute_tod_baselines(), download_intraday_5m_history()
│   └── fetcher.py          # +download_intraday_5m(symbols, days=14)
├── position/
│   └── manager.py          # +broker_stop_order_id tracking per position
│                           # +_trigger_breakeven / _trigger_trail_up: cancel-replace stop
│                           # +_on_tick_stop (fallback path)
├── state/
│   ├── migrations.py       # +migration 0005: tod_baselines table
│   └── store.py            # +upsert_tod_baselines(), get_tod_baseline(date, code, time_bucket)
│                           # +get_circuit_breaker_date(), set_circuit_breaker_date()
│                           # +get_daily_realized_pnl() (thin wrapper on get_daily_trade_stats)
├── config/
│   ├── loader.py           # +daily_circuit_breaker_r, +use_broker_stop_orders, +rvol_tod_enabled
│   └── schema.py           # +new fields in risk and execution sections
tests/
├── strategy/
│   └── test_indicators.py  # +test_tod_baseline_computation (rvol_tod fixture)
├── signal/
│   └── test_signal_engine.py # +test_circuit_breaker_gate, test_rvol_tod_gate
│   └── test_bar_aggregator.py # +test_session_volume_accumulation
├── scanner/
│   └── test_scanner.py     # +test_tod_baseline_store_on_scan
├── position/
│   └── test_manager.py     # +test_stop_order_placed_on_fill
│                           # +test_stop_cancel_replace_on_trail
├── gateway/
│   └── test_gateway.py     # +test_place_stop_order_with_aux_price
├── state/
│   └── test_store.py       # +test_tod_baselines_crud, test_circuit_breaker_state
```

---

## Change-by-Change Deep Dive

### RVOL-TOD (SIG-RVOL-TOD)

**Root cause (verified):** `scanner.py` line ~138 stores `rvol_baseline = mean(prior 14 daily volumes)`. `signal_engine.py` then computes `rvol = event.volume / rvol_baseline` where `event.volume` is the volume of a single closed 5m bar. Comparing a single bar's volume against the full-day average means RVOL effectively never reaches 2.0 except on extreme volume events. This is the "stricter than it looks" gate from the quant analysis.

**The fix:** [ASSUMED based on quant analysis + CONTEXT.md description]

1. **Scanner change (download 5m history):** Add `download_intraday_5m_history(symbols, days=16)` to `fetcher.py`. Uses `yf.download(tickers, period="30d", interval="5m")` — a 30-calendar-day window gives ~20 trading days, well beyond 14.

2. **TOD baseline computation:** For each scan candidate, for each prior trading day in the 5m history, compute cumulative volume at each 5m time bucket (running sum from first bar after 09:30 ET). Average those cumulative volumes across 14 sessions per bucket. The bucket key is the bar's `time_key` truncated to HH:MM.

3. **New DB table (migration 0005):**
```sql
CREATE TABLE IF NOT EXISTS tod_baselines (
    scan_date    TEXT NOT NULL,
    code         TEXT NOT NULL,
    time_bucket  TEXT NOT NULL,    -- "HH:MM" (e.g., "10:05", "10:10")
    cum_vol_mean REAL NOT NULL,    -- 14-day avg cumulative volume at this bucket
    PRIMARY KEY (scan_date, code, time_bucket)
);
```

4. **BarAggregator: add cumulative volume tracking:**
```python
# New field in __init__
self._session_volume: Dict[str, int] = {}

# In _handle_row, when time_key advances (bar A closes):
# Add bar A's final volume to the session running total
closed_vol = closed_ohlcv["volume"]
self._session_volume[code] = self._session_volume.get(code, 0) + closed_vol

# In BarEvent: add cum_volume field
bar_data["cum_volume"] = self._session_volume[code]

# reset_session(): also clear _session_volume
```

5. **BarEvent: add cum_volume field:**
```python
@dataclass
class BarEvent:
    ...
    cum_volume: int = 0   # cumulative session volume up to and including this bar
```

6. **Signal engine I3 gate update:**
```python
# NEW: look up TOD baseline for this bar's time bucket
time_bucket = event.time_key[-8:-3]  # extract "HH:MM" from "YYYY-MM-DD HH:MM:00"
tod_baseline = self._store.get_tod_baseline(session_date_str, code, time_bucket)
if tod_baseline <= 0.0:
    # Fall back to legacy full-day baseline if TOD not available
    rvol_baseline = self._store.get_rvol_baseline(session_date_str, code)
    rvol = event.volume / rvol_baseline if rvol_baseline > 0 else 0.0
else:
    rvol = event.cum_volume / tod_baseline
```

7. **Config:** The 2.0 RVOL threshold (`I3_rvol_min`) stays in `rules.json`. No new config key needed for the normalization switch — the signal engine uses TOD baseline when available, falls back to legacy otherwise. Optionally add `intraday_filters.I3_rvol_mode: "tod" | "legacy"` to make the switch explicit (CFG-01).

**Time-bucket granularity decision (Claude's Discretion):** 5-minute buckets (matching the bar close interval) give maximum precision. 30-minute buckets would require more data aggregation. 5-minute is recommended.

**RVOL threshold retuning:** The quant mentions "signal frequency increases materially." With TOD normalization, a stock running at 2× the historical pace at 10am will now register RVOL ≈ 2.0. The 2.0 threshold is likely appropriate — it means institutional-interest volume (2× historical pace at this time of day). Threshold validation belongs in the backtester (Phase 6), not in this phase.

---

### Exit Model (EXIT-MODEL) — BLOCKED

**Critical dependency status:** The Phase 6 backtester does NOT exist. Zero backtester code in the repository. ROADMAP.md marks Phase 6 as "Not started." [VERIFIED: `find /repo -type d -name backtester` → no results]

**What this means for planning:**
- EXIT-MODEL **cannot be implemented in Phase 7** until Phase 6 is complete
- The planner must create a Phase 7 wave structure that defers EXIT-MODEL to after Phase 6
- Phases execute in roadmap order (6 → 7), so EXIT-MODEL belongs in a final Phase 7 wave that cannot start until Phase 6 merges

**Exit model candidates (from CONTEXT.md specifics section):**
1. Current (baseline): partial ⅓ at 0.75R, BE at 1.0R, 5m swing-low trail
2. No-scale / fixed-2R: hold full size, fixed 2R target
3. Full-size to 1.5R, then trail by 15m EMA

**Implementation when Phase 6 exists:**
- Add `exit.model: "partial_be_trail" | "fixed_2r" | "full_to_1.5r_trail"` to `rules.json`
- `PositionState.evaluate_close()` dispatches on this config key
- StrategyConfig picks up the new field; schema.py validates the enum
- No structural change to `PositionManager` — just a new branch in the FSM

**Planner action:** Schedule EXIT-MODEL in the last wave of Phase 7, with an explicit checkpoint requiring Phase 6 completion before that wave can start.

---

### Broker-Side Stop Orders (RISK-TICK-STOP)

**SDK capability (verified):**

`OrderType.STOP` is present in the installed moomoo-api. [VERIFIED: local inspection]

The `place_order()` call for a stop-market protective sell:
```python
# In gateway.py — new method to add
async def place_stop_order(self, code: str, qty: int, stop_price: float, trd_side) -> str:
    """Place a broker-side Stop-Market protective order.
    
    Uses OrderType.STOP with aux_price as the trigger price.
    When market price touches aux_price, the order converts to a market order.
    
    AMENDED EXEC-02 exception: Stop-market orders are the ONLY market-order-type
    exception. All other orders remain OrderType.NORMAL.
    
    Returns broker-assigned order_id.
    """
    from moomoo import OrderType
    loop = asyncio.get_running_loop()
    
    def _place_stop_blocking():
        ret, data = self._trade_ctx.place_order(
            price=0.0,                     # market price when triggered
            qty=int(qty),
            code=code,
            trd_side=trd_side,
            order_type=OrderType.STOP,     # stop-market (EXEC-02 amendment)
            aux_price=float(stop_price),   # trigger price = trail_stop
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "place_stop_order")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        return str(row.get("order_id", "") or "")
    
    order_id = await loop.run_in_executor(None, _place_stop_blocking)
    append_audit({"event": "stop_order_placed", "code": code, "stop_price": stop_price, "order_id": order_id})
    return order_id
```

**SIMULATE verification status — CRITICAL UNKNOWN:**

The codebase documents several SIMULATE limitations (e.g., `deal_list_query` returns error "Paper trading does not support deal data."). Whether `OrderType.STOP` is honored on SIMULATE is not verified. [ASSUMED: likely unsupported — paper accounts typically simulate fill/position mechanics but not complex order types]

**Decision D-02 implementation — fallback path:**

If SIMULATE does not support stop orders (verified empirically after plan execution), the fallback is a bot-side quote monitor:

1. Subscribe `SubType.QUOTE` (real-time bid/ask pushes) for codes with active positions. The moomoo SDK has `SubType` with multiple values; `SubType.ORDER_BOOK` or `SubType.QUOTE` push bid/ask at tick level.
2. Create a `QuoteTickHandler(QuoteHandlerBase)` that fires a callback on each quote push.
3. In the callback: if `bid_price <= pos.trail_stop`, call `_place_exit_order(code, qty)` immediately (the existing limit-escalation exit from `ExecutionEngine.manage_exit()`).

**Config flag for path selection:**
```json
"execution": {
  "use_broker_stop_orders": true
}
```
When `false` (SIMULATE fallback detected or configured), the bot-side quote monitor path activates.

**D-03 coexistence (bar-close FSM + broker stop):**

The existing `PositionState.evaluate_close()` stop check on bar close remains untouched as a redundant backstop. This is defense-in-depth: if the broker stop order is cancelled, rejected, or lost, the bar-close check will still catch the stop violation within the next 5m. No change to `state.py` evaluate_close logic.

**D-04 trail sync — cancel-replace pattern:**

On each trail ratchet (breakeven at 1R or swing-low update), `PositionManager`:
1. Cancel existing broker stop order via `gateway.cancel_order(pos.broker_stop_order_id)`
2. Re-query `dealt_qty` post-cancel (finding-1.4 pattern — already implemented in `execution/engine.py`)
3. Place new stop order at the updated `trail_stop` via `gateway.place_stop_order()`
4. Update `pos.broker_stop_order_id` with the new order_id
5. Persist `pos` to StateStore DB-first

**New PositionState field:** `broker_stop_order_id: Optional[str] = None`
- Persisted to DB (requires migration 0005 column on `positions` table, or stored in a separate `broker_stop_orders` table)
- Reconstructed on restart; if not None, the existing stop order is still live at the broker
- On startup reconciliation: optionally query `get_order_status(broker_stop_order_id)` to verify the stop is still open

---

### Circuit Breaker (RISK-CIRCUIT)

**P&L source (verified):** `StateStore.get_daily_trade_stats(session_date)` returns:
```python
{"trade_count": int, "wins": int, "losses": int, "realized_pnl": float}
```
where `realized_pnl = SUM((exit_price - entry_price) * quantity)` from the `trades` table. This is the correct realized-only metric (D-05). [VERIFIED: store.py lines ~387-406]

**1R computation:**
```python
one_r = (cfg.max_risk_per_trade_pct / 100.0) * cfg.sizing_equity_usd
# = (1.0 / 100) * 100_000 = $1,000
breaker_threshold = -cfg.daily_circuit_breaker_r * one_r
# = -2.0 * $1,000 = -$2,000
```

**Persistence (meta table, verified present since migration 0001):**
```python
# New StateStore methods:
def get_circuit_breaker_date(self) -> Optional[str]:
    """Return the ET date string when the breaker last tripped, or None."""
    with self._lock:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='circuit_breaker_tripped_date'"
        ).fetchone()
    return row[0] if row else None

def set_circuit_breaker_date(self, date_str: str) -> None:
    """Persist the breaker trip date (upsert)."""
    with self._lock:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('circuit_breaker_tripped_date', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (date_str,),
        )
        self._conn.commit()

def clear_circuit_breaker(self) -> None:
    """Remove the breaker state (next-day auto-reset)."""
    with self._lock:
        self._conn.execute(
            "DELETE FROM meta WHERE key='circuit_breaker_tripped_date'"
        )
        self._conn.commit()
```

**Gate 7 integration in SignalEngine.on_bar():**

The circuit breaker check belongs AFTER Gate 3 (entry window) and BEFORE Gate 4 (broker get_positions call) to avoid an SDK round-trip on tripped days. It is checked on every bar call:

```python
# Gate 7: circuit breaker (D-05/D-06/D-07)
# Reads meta table for persisted trip state; checks P&L only if not already tripped.
if self._is_circuit_breaker_tripped(session_date_str):
    _logger.info("signal_skipped_circuit_breaker", code=code,
                 reason="daily -2R circuit breaker tripped — no new entries this session")
    return None
```

Where `_is_circuit_breaker_tripped()` implements:
1. Check `meta.circuit_breaker_tripped_date` — if today's ET date, return True (already tripped this session)
2. If stored date < today: auto-reset (clear meta row), return False
3. If no stored date: query `get_daily_trade_stats(today)` to see if we've just now crossed -2R
4. If `realized_pnl <= breaker_threshold`: trip (write meta, cancel pending intents, alert), return True
5. Otherwise return False

**D-08 — cancel working entries on trip:**

On trip, the breaker must cancel all PENDING intent orders. Pattern reuses the existing cancel + post-cancel dealt_qty machinery from `execution/engine.py`:
```python
async def _trip_circuit_breaker(self, session_date_str: str) -> None:
    """Trip the daily loss circuit breaker: persist, alert, cancel entries."""
    self._store.set_circuit_breaker_date(session_date_str)
    
    # Cancel all PENDING entry orders (D-08)
    pending = self._store.get_pending_intent_codes("PENDING")
    for row in pending:
        # Cancel via ExecutionEngine / Gateway (injected)
        # Mark intent ABANDONED in pending_intents table
        ...
    
    # Telegram alert
    _logger.warning("circuit_breaker_tripped", session_date=session_date_str, ...)
    if self._alerter is not None:
        await self._alerter.send("CIRCUIT BREAKER: -2R daily loss reached. No new entries today.")
```

`SignalEngine` will need an `alerter` and `engine` injected for the trip path. These are already available in `bot.service.bot` (the orchestrator that wires all components).

**Alternative (cleaner) approach:** Let the circuit breaker trip and alert happen in `bot.service.bot` (the main orchestrator) rather than inside `SignalEngine`. SignalEngine's gate just reads the persisted flag and blocks. The trip detection and D-08 cancellation live in the bot orchestrator's P&L monitoring loop. This avoids injecting `alerter` and `engine` into `SignalEngine`.

**Recommended approach:** Circuit breaker detection in the SignalEngine (Gate 7 reads meta flag) + trip handling in `bot.service.bot` via a periodic check or in-gate trip. Planner decides. Either approach is valid.

**New rules.json key:**
```json
"risk": {
  "daily_circuit_breaker_r": 2.0
}
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Stop order placement | Custom stop-loss polling loop | `OrderType.STOP` + `aux_price` via `place_order()` | SDK provides native stop order type; broker handles trigger |
| Cumulative session volume | Sum volumes from StateStore queries | `_session_volume` counter in BarAggregator (in-memory, resets at session start) | Already on the hot path; O(1) per bar |
| TOD baseline for unseen time buckets | Interpolation between buckets | Fall back to full-day `rvol_baseline` (legacy) for missing buckets | Simple degraded-gracefully fallback; avoids extrapolation errors |
| P&L aggregation for circuit breaker | In-memory running total | `StateStore.get_daily_trade_stats()` SQL aggregate | Already implemented; survives restarts |
| Broker stop order cancel-replace | Custom retry-until-cancelled loop | Existing `gateway.cancel_order()` + finding-1.4 re-query pattern | Pattern already implemented in `execution/engine.py`; reuse exactly |
| Circuit breaker persistence | Custom file or in-memory flag | `meta` table (already in schema since migration 0001) | Matches existing pattern; survives restarts (D-07 requirement) |

**Key insight:** The four Phase 7 changes each have a natural "already exists" hook in the codebase. RVOL-TOD extends the scanner's existing per-candidate computation. The circuit breaker extends the signal engine's existing gate stack. The broker stop extends the gateway's existing `place_order` path. EXIT-MODEL extends `PositionState.evaluate_close()` with a config-dispatched branch. Build on, don't replace.

---

## Common Pitfalls

### Pitfall 1: Forgetting to reset _session_volume on session start
**What goes wrong:** Cumulative volume carries over from prior trading day → RVOL-TOD denominator comparison is wrong for the new session.
**Why it happens:** `BarAggregator.reset_session()` is called once at session start; if `_session_volume` is not cleared there, it persists.
**How to avoid:** Add `self._session_volume.clear()` to `reset_session()`. Test: call `reset_session()` midway through a synthetic session and verify cumulative resets to 0.
**Warning signs:** RVOL values that look unusually high at session open.

### Pitfall 2: Stop order placed before position fill confirmation
**What goes wrong:** Entry order is submitted but fill not yet confirmed; if stop order is placed based on the pending intent, it may trigger before the entry actually fills.
**Why it happens:** Race condition between fill detection polling and stop placement.
**How to avoid:** Place the broker stop order ONLY in `PositionManager._on_entry_fill()` (after AWAITING_FILL → ACTIVE), not at intent creation time. The bar-close FSM is the backstop during the fill-pending window.
**Warning signs:** Stop orders firing on symbols with no confirmed broker position.

### Pitfall 3: Circuit breaker trip using mark-to-market instead of realized-only
**What goes wrong:** Circuit breaker trips on unrealized losses (e.g., a position that is down -$2,000 but still open), halting entries even though no loss is locked in.
**Why it happens:** Using `accinfo_query.total_assets` delta instead of the trades table aggregate.
**How to avoid:** `get_daily_trade_stats(session_date)` queries `WHERE DATE(closed_at) = ?` — only closed trades. Positions still open do NOT contribute. D-05 requires realized-only.
**Warning signs:** Breaker trips immediately on a stop-out that was later partially recovered.

### Pitfall 4: TOD baseline lookup uses UTC date instead of ET date
**What goes wrong:** TOD baselines are stored with scan_date as the ET calendar date, but signal engine queries with UTC date → no match found → RVOL falls back to legacy (or no signal fires).
**Why it happens:** The ET/UTC distinction already caused one bug (fixed in 03-01/03-02).
**How to avoid:** All `scan_date` lookups in signal_engine use `now_et().date().isoformat()` — same convention as `get_rvol_baseline()`. Copy exactly.
**Warning signs:** Signal engine always falls back to legacy RVOL despite TOD baselines being stored.

### Pitfall 5: Stop order cancel-replace creates a brief exposure gap
**What goes wrong:** After cancelling the broker stop (D-04 trail sync), a flash crash executes before the replacement stop is placed → position exits without any protection at the new stop level.
**Why it happens:** The cancel → re-query → re-place sequence takes several hundred milliseconds.
**How to avoid:** This is an accepted cost (CONTEXT.md D-04: "The brief no-stop window during replace is an accepted cost"). The bar-close FSM's stop check is the redundant backstop (D-03). Document as known; don't try to make cancel-replace instantaneous.
**Warning signs:** Positions closing below the stop level during a trail ratchet event.

### Pitfall 6: Circuit breaker does not persist through bot restart
**What goes wrong:** Bot restarts mid-day (e.g., OpenD reconnect restart), reads no breaker state in meta, proceeds to trade even though -2R was already reached.
**Why it happens:** In-memory only implementation without meta persistence.
**How to avoid:** Write breaker state to `meta` table on trip (D-07). On startup, read `meta.circuit_breaker_tripped_date` before `SignalEngine` is initialized. If it matches today's ET date, initialize `SignalEngine._circuit_breaker_tripped = True`.
**Warning signs:** Post-restart trade activity on a session where -2R was already reached.

### Pitfall 7: Broker stop for short position not applicable (long-only)
**What goes wrong:** `place_stop_order` accidentally called with `TrdSide.BUY` for a long-stop, which is a protective BUY stop (not valid for a long position close).
**Why it happens:** Confusion about which TrdSide to use for a protective stop on a long.
**How to avoid:** Long position stop = TrdSide.SELL (sell to close). Always use `TrdSide.SELL` in `place_stop_order`. The strategy is long-only (REQUIREMENTS.md).
**Warning signs:** SDK error "invalid trd_side for stop order" or position not closing when stop is triggered.

---

## Code Examples

### Placing a broker-side stop order (OrderType.STOP + aux_price)

```python
# Source: local inspection of moomoo-api OpenSecTradeContext.place_order
# In gateway.py

async def place_stop_order(self, code: str, qty: int, stop_price: float, trd_side) -> str:
    """Place a Stop-Market protective sell order (EXEC-02 amendment D-01)."""
    from moomoo import OrderType
    loop = asyncio.get_running_loop()

    def _blocking():
        ret, data = self._trade_ctx.place_order(
            price=0.0,
            qty=int(qty),
            code=code,
            trd_side=trd_side,             # TrdSide.SELL for long position protection
            order_type=OrderType.STOP,     # stop-market: triggers as market when aux_price hit
            aux_price=float(stop_price),   # the stop trigger price = trail_stop
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "place_stop_order")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        return str(row.get("order_id", "") or "")

    order_id = await loop.run_in_executor(None, _blocking)
    _logger.info("stop_order_placed", code=code, stop_price=stop_price, order_id=order_id)
    return order_id
```

### TOD baseline storage (migration 0005)

```python
# Source: derived from existing _migration_0002 / _migration_0004 patterns
# In state/migrations.py

def _migration_0005(conn: sqlite3.Connection) -> None:
    """Add Phase 7 tables: tod_baselines + broker_stop_order_id column."""
    # TOD RVOL baseline table
    conn.execute("""CREATE TABLE IF NOT EXISTS tod_baselines (
        scan_date    TEXT NOT NULL,
        code         TEXT NOT NULL,
        time_bucket  TEXT NOT NULL,
        cum_vol_mean REAL NOT NULL,
        PRIMARY KEY (scan_date, code, time_bucket)
    )""")
    # Add broker_stop_order_id to positions (nullable — null before stop is placed)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    if "broker_stop_order_id" not in existing:
        conn.execute("ALTER TABLE positions ADD COLUMN broker_stop_order_id TEXT")
```

### Circuit breaker gate in SignalEngine (pattern)

```python
# Source: derived from existing gate stack in bot/signal/signal_engine.py
# Gate 7 pattern (insert before Gate 4 broker call to save SDK round-trips)

def _is_circuit_breaker_tripped(self, session_date_str: str) -> bool:
    """Return True if the -2R daily circuit breaker is active this session."""
    # Check persisted trip state first (survives restart, D-07)
    stored_date = self._store.get_circuit_breaker_date()
    if stored_date == session_date_str:
        return True   # already tripped today
    if stored_date is not None and stored_date < session_date_str:
        # Stale trip from a prior session — auto-reset (D-07)
        self._store.clear_circuit_breaker()

    # Not yet tripped: check today's realized P&L
    stats = self._store.get_daily_trade_stats(session_date_str)
    realized = stats.get("realized_pnl", 0.0)
    one_r = (self._cfg.max_risk_per_trade_pct / 100.0) * (self._cfg.sizing_equity_usd or 100_000.0)
    threshold = -getattr(self._cfg, "daily_circuit_breaker_r", 2.0) * one_r

    if realized <= threshold:
        # Trip: persist and trigger D-08 cancellations externally
        self._store.set_circuit_breaker_date(session_date_str)
        _logger.warning("circuit_breaker_tripped",
                        realized_pnl=realized, threshold=threshold,
                        session_date=session_date_str)
        return True

    return False
```

### RVOL-TOD cumulative volume tracking (BarAggregator)

```python
# Source: derived from existing BarAggregator._handle_row pattern
# Addition to bar_aggregator.py

# In __init__:
self._session_volume: Dict[str, int] = {}

# In reset_session():
self._session_volume.clear()

# In _handle_row, where bar A closes (time_key advances):
closed_vol = closed_ohlcv["volume"]
self._session_volume[code] = self._session_volume.get(code, 0) + closed_vol

bar_data = {
    ...existing fields...
    "cum_volume": self._session_volume[code],   # NEW: cumulative session volume
}
```

---

## State of the Art

| Old Approach | Current Approach | Change | Impact |
|--------------|------------------|--------|--------|
| Full-day avg RVOL denominator | TOD-bucketed cumulative avg | Phase 7 | Removes early-day bias; RVOL now comparable across times of day |
| Bar-close stop check only | Broker-side stop + bar-close backstop | Phase 7 | Closes the fat-tail window (up to 5m gap between stop hit and exit) |
| No daily loss cap | -2R circuit breaker | Phase 7 | Adds professional kill-switch standard to risk framework |
| Hand-picked exit model (0.75R/1R) | Backtester-selected exit model | Phase 7 (after Phase 6) | Parameters backed by evidence, not assumption |

**Deprecated/outdated post-Phase 7:**
- `daily_scan.rvol_baseline` as the ONLY RVOL denominator: still valid as fallback but not primary
- `signal_engine.py` gate count = 6: increases to 7 with circuit breaker

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | SIMULATE paper accounts do NOT support `OrderType.STOP` orders (quote from codebase: paper account known to not support `deal_list_query`) | RISK-TICK-STOP | If SIMULATE does support stop orders, the fallback implementation is unnecessary but harmless; the config flag just stays `true`. Low consequence. |
| A2 | moomoo K_5M bar push `volume` field contains the volume for that specific 5m period (not cumulative session volume) | RVOL-TOD | If it IS cumulative, the `_session_volume` accumulator would double-count. Must verify empirically on first live run. The fallback to single-bar volume for the I3 gate still works — just with different denominator. |
| A3 | 15-minute EMA trail (exit model candidate 3) can be computed from the existing 5m bar buffer without a separate 15m subscription | EXIT-MODEL | If 15m bars need a separate subscription, complexity increases. yfinance backtester can simulate from 5m bars regardless. |
| A4 | The `meta` table's key-value interface is sufficient for circuit breaker state (no high-frequency writes required) | RISK-CIRCUIT | Low risk — circuit breaker trips at most once per session. |

---

## Open Questions

1. **Does SIMULATE honor OrderType.STOP orders?**
   - What we know: SIMULATE rejects `deal_list_query` ("Paper trading does not support deal data"). Other complex order types may similarly be unsupported.
   - What's unclear: No empirical test or Futu documentation to confirm stop order support on SIMULATE.
   - Recommendation: Build both the broker-stop path and the quote-monitor fallback. Test empirically on first live run: place a small test stop order and verify it appears in `order_list_query`. The config flag (`use_broker_stop_orders`) selects the path.

2. **Should the circuit breaker trip detection happen in SignalEngine or in the bot orchestrator?**
   - What we know: SignalEngine already reads realized P&L state; adding a Store dependency for the circuit breaker is minimal.
   - What's unclear: Whether injecting `alerter` + `engine` into SignalEngine (for the D-08 cancellation) violates the separation of concerns or creates circular dependencies.
   - Recommendation: SignalEngine owns the GATE (read meta flag → block signals). The trip DETECTION + D-08 cancellation lives in `bot.service.bot` (a periodic P&L check or a post-fill hook). This avoids injecting alerter/engine into SignalEngine.

3. **What time-bucket granularity for TOD baselines?**
   - What we know: Signal fires on 5m bar closes, so 5m buckets match exactly.
   - What's unclear: Whether fine-grained 5m buckets have enough history (14 sessions = 14 data points per bucket) for a stable mean.
   - Recommendation: 5m buckets are sufficient. 14 data points per bucket is standard for RVOL lookbacks (the existing daily RVOL uses 14 days). No smoothing needed.

4. **Does the TOD baseline need to be computed at each intraday rescan or only at premarket?**
   - What we know: New intraday-rescan candidates are added to `daily_scan` throughout the day. These candidates need TOD baselines too.
   - What's unclear: Whether downloading 5m history per rescan candidate (at ~30-min intervals) creates yfinance rate-limit pressure.
   - Recommendation: Download 5m history only at premarket scan. Intraday rescan candidates use legacy full-day `rvol_baseline` (already stored in `daily_scan`). This is a graceful degradation; premarket candidates (the primary path) get TOD normalization; rescan candidates get the legacy gate.

---

## Package Legitimacy Audit

No new packages are installed in this phase. All capabilities use the existing stack (moomoo-api, yfinance, pandas, sqlite3, pytest). This section is not applicable.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| moomoo-api | Broker stop orders | ✓ | >=10.4.6408 | — |
| yfinance | 5m intraday history for TOD baselines | ✓ | installed | — |
| OpenD service (127.0.0.1:11111) | Live stop order placement/testing | Assumed ✓ (operator manages) | — | Cannot test stop orders without OpenD running |
| SIMULATE paper account | Stop order empirical verification | Assumed ✓ | — | Build fallback path regardless |
| pytest | Test suite | ✓ | installed | — |

**Missing dependencies with no fallback:** None — all required capabilities are available.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (installed, existing test suite) |
| Config file | pytest.ini or pyproject.toml (check repo root) |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIG-RVOL-TOD | `_session_volume` accumulates correctly across bars | unit | `pytest tests/signal/test_bar_aggregator.py -x -k session_volume` | ❌ Wave 0 |
| SIG-RVOL-TOD | `tod_baselines` upserted correctly at scan time | unit | `pytest tests/scanner/test_scanner.py -x -k tod_baseline` | ❌ Wave 0 |
| SIG-RVOL-TOD | I3 gate uses TOD baseline when available, falls back to legacy | unit | `pytest tests/signal/test_signal_engine.py -x -k rvol_tod` | ❌ Wave 0 |
| RISK-TICK-STOP | `place_stop_order` called with `OrderType.STOP` + `aux_price` | unit | `pytest tests/gateway/test_gateway.py -x -k stop_order` | ❌ Wave 0 |
| RISK-TICK-STOP | Trail sync: existing stop cancelled and replaced on trail ratchet | unit | `pytest tests/position/test_manager.py -x -k trail_sync_stop` | ❌ Wave 0 |
| RISK-TICK-STOP | Config flag `use_broker_stop_orders=false` activates quote-monitor path | unit | `pytest tests/position/test_manager.py -x -k quote_monitor_fallback` | ❌ Wave 0 |
| RISK-CIRCUIT | `_is_circuit_breaker_tripped` returns True when realized_pnl <= -2R | unit | `pytest tests/signal/test_signal_engine.py -x -k circuit_breaker` | ❌ Wave 0 |
| RISK-CIRCUIT | Breaker state written to meta table on trip | unit | `pytest tests/state/test_store.py -x -k circuit_breaker_persist` | ❌ Wave 0 |
| RISK-CIRCUIT | Breaker auto-resets when stored date < today | unit | `pytest tests/signal/test_signal_engine.py -x -k circuit_breaker_reset` | ❌ Wave 0 |
| RISK-CIRCUIT | Pending entries cancelled on trip (D-08) | unit | `pytest tests/signal/test_signal_engine.py -x -k circuit_breaker_cancels` | ❌ Wave 0 |
| EXIT-MODEL | exit.model config key dispatches to correct FSM branch | unit | `pytest tests/position/test_fsm.py -x -k exit_model_dispatch` | ❌ After Phase 6 |

### Sampling Rate

- **Per task commit:** `pytest tests/ -x -q --tb=short`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/signal/test_bar_aggregator.py` — extend: add `test_session_volume_*` tests
- [ ] `tests/scanner/test_scanner.py` — extend: add `test_tod_baseline_*` tests
- [ ] `tests/signal/test_signal_engine.py` — extend: add `test_circuit_breaker_*`, `test_rvol_tod_*` tests
- [ ] `tests/gateway/test_gateway.py` — extend: add `test_place_stop_order_*` tests
- [ ] `tests/position/test_manager.py` — extend: add `test_broker_stop_placed_on_fill`, `test_trail_sync_cancel_replace` tests
- [ ] `tests/state/test_store.py` — extend: add `test_tod_baselines_upsert`, `test_circuit_breaker_state_*` tests
- [ ] New migration test stubs for migration 0005 in `tests/state/test_migrations.py`

---

## Security Domain

`security_enforcement: true` (from `.planning/config.json`). ASVS Level 1 required.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth surface in Phase 7 |
| V3 Session Management | No | Not applicable |
| V4 Access Control | No | Paper-only trading; no multi-user surface |
| V5 Input Validation | Yes | New config fields validated via jsonschema (CFG-01 + schema.py) |
| V6 Cryptography | No | No new cryptographic operations |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed `daily_circuit_breaker_r` config | Tampering | jsonschema validates `type: number`, min enforced in loader |
| Stale circuit breaker state from wrong session | Spoofing | Date comparison in `_is_circuit_breaker_tripped()` auto-resets stale entries |
| Stop order placed for wrong symbol | Elevation | `place_stop_order` validates `code` format matches existing position (callers are PositionManager with broker-verified positions) |
| TOD baseline SQL injection | Tampering | All store queries use parameterized statements (`?` placeholders) — existing pattern |

---

## Project Constraints (from CLAUDE.md)

- **Tech stack:** Python 3.6+; reuse existing moomoo-api SDK; no new broker access libraries.
- **Strategy config:** All parameters in `rules.json` (CFG-01). Every new threshold (`daily_circuit_breaker_r`, `use_broker_stop_orders`, `I3_rvol_mode`) added to `rules.json` + `schema.py` + `loader.py` + `StrategyConfig`.
- **Safety:** `FUTU_TRD_ENV=SIMULATE` at all times; the EXEC-02 amendment (stop-market allowed) does not open any real-money path.
- **Timezone:** All date/time logic uses `now_et()` from `bot.safety.et_helpers`. TOD bucket keys are ET clock times.
- **Naming:** New store methods follow `get_*/set_*` convention; new gateway method follows `place_*_order` convention.
- **Error handling:** No new exception classes; `GatewayError` for broker failures; `ConfigError` for schema violations.
- **Coding style:** 4-space indent, snake_case, docstrings on all public functions, no type hints (except dataclasses).
- **CFG-01 enforcement:** No numeric strategy literals in Python. Every threshold loaded from `cfg.*`.

---

## Sources

### Primary (HIGH confidence)

- [VERIFIED: local code inspection] `bot/strategy/indicators.py` — current `rvol()` implementation; denominator = full-day mean
- [VERIFIED: local code inspection] `bot/scanner/scanner.py` — `rvol_baseline_val = float(prior_sorted["volume"].mean())` (daily volumes)
- [VERIFIED: local code inspection] `bot/signal/signal_engine.py` — gate stack; `rvol = event.volume / rvol_baseline`
- [VERIFIED: local code inspection] `bot/signal/bar_aggregator.py` — `_cur_bar[code]["volume"]` = single 5m bar volume
- [VERIFIED: local code inspection] `bot/signal/events.py` — `BarEvent.volume` docs confirm single-bar field
- [VERIFIED: local code inspection] `bot/gateway/gateway.py` — `place_order()` uses `OrderType.NORMAL` exclusively
- [VERIFIED: local code inspection] `bot/position/manager.py` — FSM, trail ratchet trigger points
- [VERIFIED: local code inspection] `bot/execution/engine.py` — cancel + post-cancel dealt_qty pattern (finding 1.4)
- [VERIFIED: local code inspection] `bot/state/store.py` — `get_daily_trade_stats()` returns realized P&L; `meta` table confirmed
- [VERIFIED: local code inspection] `bot/state/migrations.py` — migration 0001 `meta` table; current schema at version 4
- [VERIFIED: local inspection] `moomoo` SDK: `OrderType.STOP`, `OrderType.STOP_LIMIT`, `OrderType.TRAILING_STOP` confirmed; `place_order()` `aux_price` parameter confirmed; `modify_order()` `aux_price` parameter confirmed
- [VERIFIED: local inspection] yfinance `interval="5m"` supported; `period="60d"` available (~42 trading days)
- [VERIFIED: local code inspection] `rules.json` and `bot/config/loader.py` — current StrategyConfig fields; schema extension pattern

### Secondary (MEDIUM confidence)

- [CITED: .planning/phases/07-strategy-optimization/07-CONTEXT.md] — All locked decisions D-01 through D-11
- [CITED: docs/2026-07-03-strategy-analysis-trend-join-long.md] — Quant analysis motivating all four changes

### Tertiary (LOW confidence)

- [ASSUMED] SIMULATE does not support `OrderType.STOP` (based on pattern of SIMULATE limitations in codebase; not empirically tested)

---

## Metadata

**Confidence breakdown:**

- RVOL-TOD: HIGH (current implementation verified; yfinance 5m capability verified; schema pattern clear)
- Broker stop SDK capability: HIGH (OrderType.STOP + aux_price verified in installed SDK)
- Broker stop SIMULATE support: LOW (empirically unverified; fallback required)
- Circuit breaker: HIGH (P&L source verified; meta table confirmed; gate pattern established)
- Exit model: HIGH (blocked status confirmed; no code to research — backtester not built)

**Research date:** 2026-07-03
**Valid until:** 2026-08-03 (30 days — stable moomoo SDK; verify if SDK version changes)
