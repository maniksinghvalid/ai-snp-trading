# Phase 6: Backtester - Research

**Researched:** 2026-07-06
**Domain:** Offline strategy replay harness reusing live `bot/strategy`, `bot/signal`, `bot/risk`, `bot/position` code against historical yfinance 5m/1d data
**Confidence:** HIGH (architecture — full read of live `bot/` source); MEDIUM (yfinance interval limits — cross-verified via GitHub issues + docs); LOW (Parquet-vs-CSV performance — no local benchmark run)

## Summary

Phase 6 does not need new business logic — it needs a **replay harness** that feeds
historical 5m bars through the *exact same* `SignalEngine → RiskEngine → PositionManager`
pipeline `bot/service/bot.py::_process_bar` already drives live, with two swapped
leaves: a `SimulatedBarFeed` (yfinance/CSV instead of the moomoo SDK push stream) and a
`SimulatedExecution` (fills at bar N+1 open instead of `ExecutionEngine`'s TTL/poll loop
against a real broker). `StrategyCore`/`TrendJoinLong`, `PositionState` FSM, `SignalEngine`,
and `RiskEngine` are imported unchanged — this is enforced by their existing no-I/O
contracts (`StrategyCore` docstring: "no I/O is permitted inside any hook").

The hard constraint discovered in this research is **yfinance's intraday data window is a
*rolling* 60-day window measured from "now", not an arbitrary historical range** — a
backtest run today (2026-07-06) can fetch 5m bars back to roughly 2026-05-07 and no
further, regardless of what dates are requested. Daily bars have no such limit (`period="1y"`
already used by the live scanner). This means BT-04's "yfinance or flat CSV/Parquet export"
alternative is not a nice-to-have — it is the only way to backtest further back than ~60
days, and the harness should build a local CSV cache from every yfinance 8/60-day fetch so
the effective backtestable history *grows* over the life of the project instead of resetting
every day. No Parquet dependency is justified: `pyarrow` is not installed, the data volumes
here are small (≤20 symbols × ~60 days × 5m bars ≈ 30k rows/symbol max), and CSV is already
the format BT-04 names as an acceptable alternative.

**Primary recommendation:** Build a new top-level `backtester/` package (mirrors `bot/`,
sibling not sub-package) containing `feed.py` (SimulatedBarFeed), `execution.py`
(SimulatedExecution + a minimal `SimulatedGateway` stub satisfying only the async methods
`SignalEngine`/`RiskEngine` actually call), `harness.py` (the replay loop — a directly-ported,
synchronous-driver copy of `bot.service.bot.TradingBot._process_bar`), `report.py` (metrics +
CSV), and `run.py` (CLI entry point). Reuse `StateStore` (a fresh SQLite file per backtest
run, via `BOT_STATE_DB` env override — never the live `data/bot_state.db`) and reuse
`bot.scanner.scanner._evaluate_symbol` / `_compute_tod_baselines` for point-in-time
SMA200/RVOL/RVOL-TOD baseline computation instead of reimplementing that math.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BT-01 | Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot | Architectural Responsibility Map + "Reuse the live per-bar pipeline" pattern below — `SignalEngine`, `RiskEngine`, `PositionManager`, `TrendJoinLong` are constructed identically to `bot/main.py`; only `gateway`/`execution engine` are swapped for simulated leaves. |
| BT-02 | Backtester enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time | "Point-in-Time Correctness" pattern + Pitfall 1 (N+1 fill timing) + Pitfall 2 (SMA/RVOL reuse of `_evaluate_symbol`'s existing `date < scan_date` cutoff) + Pitfall 5 (premarket-high source divergence). |
| BT-03 | Performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV) | "Performance Metrics" Code Examples section — standard formulas + `bot/state/store.py::get_closed_trades`/`get_daily_trade_stats` as the trade-log source (reuse, not reimplement). |
| BT-04 | Historical data sourced from yfinance (or flat CSV/Parquet), not Moomoo; handles yfinance's ~60-day 5m window + ticker normalization | "yfinance Intraday Data Window" finding (rolling window, not arbitrary range) + Environment Availability (pyarrow absent → CSV) + reuse of `bot/scanner/universe.py::yfinance_to_moomoo` for ticker normalization. |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Historical bar sourcing (yfinance/CSV) | Backtester (new) | — | `bot/` has no historical-5m fetch path; `bot/scanner/fetcher.py::download_intraday_5m` fetches 5m but only for the TOD-baseline use case, not as a bar-replay source. New code, but reuses the fetcher module. |
| Bar-close signal evaluation (I1/I2/I3, entry window, caps) | `bot/signal/signal_engine.py` (reused unchanged) | Backtester (drives it) | `SignalEngine.on_bar()` already accepts a plain `BarEvent` — no broker coupling in the gate logic itself, only in the `gateway.get_positions()` concurrent-cap read. |
| Trade sizing (1%-risk, 10%-notional cap, stop calc) | `bot/risk/risk_engine.py` (reused unchanged) | — | `RiskEngine.on_signal()` already supports a fixed `cfg.sizing_equity_usd` (rules.json currently sets this to 100000), which bypasses any live-equity gateway call entirely — zero broker dependency for backtest sizing. |
| Position lifecycle FSM (partial/BE/trail/stop-out) | `bot/position/state.py` + `bot/position/manager.py` (reused unchanged) | — | `PositionState.evaluate_close()` is pure (close price + cfg only); `PositionManager` needs only a `bar_buffer` dict and an `engine` object satisfying `manage_exit()` — no gateway required when `gateway=None`. |
| Order fill simulation (N+1 open, slippage) | Backtester (new `SimulatedExecution`) | — | Live `ExecutionEngine` polls a real broker over `asyncio.sleep` TTL loops — architecturally wrong for bar-driven replay. New code with the SAME public surface (`consume_intent`, `manage_exit`) so `PositionManager`/pipeline code is unchanged. |
| Point-in-time SMA200 / RVOL / RVOL-TOD baseline | `bot/scanner/scanner.py` (`_evaluate_symbol`, `_compute_tod_baselines` — reused) | Backtester (calls per historical day) | These functions already take an explicit `scan_date` cutoff and never look past it — this is the exact point-in-time guarantee BT-02 requires; reimplementing this math is the #1 hand-roll risk in this phase. |
| Premarket-high per session | Backtester (new, from 5m bars) | `bot/signal/signal_engine.py::set_premarket_highs()` (reused sink) | Live source is a broker snapshot field (`pre_high_price`); backtest source is `max(high)` of the same day's 5m bars with ET time < 09:30. Different source, same sink method — no new SignalEngine code needed. |
| Concurrent-position-cap / broker-truth reads | Backtester (new `SimulatedGateway` stub) | — | `SignalEngine.on_bar()` Gate 4 calls `gateway.get_positions()`; a real broker call is meaningless offline — a ~15-line stub answering from the harness's own open-position tracking satisfies the interface. |
| Performance report + per-trade CSV | Backtester (new `report.py`) | `bot/service/report.py` (pattern reference only, not reused code) | `bot/service/report.py` builds a *daily* HTML dashboard from `StateStore`; the backtest report is a full-run summary + CSV — same StateStore read pattern (`get_closed_trades`), different output shape. Do not import `bot/service/report.py` directly — copy the read pattern, write new aggregation. |

## Standard Stack

### Core (no new packages)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| yfinance | 1.4.1 (pinned in `requirements.txt`, confirmed installed) | Historical daily + 5m bar source (BT-04) | Already the project's sole scan/backtest data source (SCAN-06); `bot/scanner/fetcher.py` already has `download_daily_bars`/`download_intraday_5m` battle-tested against this exact SDK version's quirks (Pitfall #1 threads-as-int, Pitfall #2 `shared._ERRORS.clear()`). |
| pandas | 3.0.3 (installed; `requirements.txt` pins `>=2.0,<4.0`) | DataFrame bar manipulation | Already the project's only tabular-data library; `StrategyCore` hooks are typed against `pd.DataFrame`. |
| jsonschema | >=4.0,<5.0 (installed) | rules.json validation | Already used by `bot/config/loader.py::load_strategy_config` — the backtester loads the SAME rules.json via the SAME loader (CFG-01/BT-01 "reads the same rules.json"). |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| csv (stdlib) | — | Per-trade CSV report (BT-03) | Always — no third-party CSV library is justified for a flat trade-log export. |
| sqlite3 (stdlib, via `bot.state.store.StateStore`) | — | Reuse the live trade/position schema for the backtest run | Reuse, not new — see "Don't Hand-Roll" below. Point `BOT_STATE_DB` at a scratch path per run (e.g. `backtester/runs/<run_id>/state.db`) so a backtest run never touches or is touched by `data/bot_state.db`. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| CSV cache of yfinance bars | Parquet (`pyarrow`) | `pyarrow` is not installed in this environment and adds a new dependency + package-legitimacy checkpoint for a problem CSV already solves at this data volume (≤20 symbols × ≤60 trading days × 5m bars). Revisit only if the cache grows to cover the full S&P 500 × multi-year (hundreds of MB, where CSV parse time becomes the bottleneck). |
| A brand-new StateStore-like SQLite schema for backtest trades | Reusing `bot.state.store.StateStore` unchanged, pointed at a scratch DB file | The live schema (`positions`, `trades`, `daily_trade_count`, `pending_intents`, `daily_scan`, `tod_baselines`) already has every table the pipeline writes to; a parallel schema would duplicate all of `bot/state/migrations.py` for no benefit and risk schema drift between live and backtest (violates BT-01's "no modifications to live code"). |
| Re-implementing `ExecutionEngine`'s TTL logic with `asyncio.sleep` fast-forwarded | A synchronous `SimulatedExecution.consume_intent()`/`manage_exit()` that fills immediately at the injected next-bar-open price | `asyncio.sleep`-based TTL replay is needless complexity for a domain with no wall-clock — direct N+1-bar-open fill is both simpler and matches BT-02's literal requirement. |

**Installation:** None — no new packages required for this phase. `yfinance==1.4.1`,
`pandas`, and `jsonschema` are already in `requirements.txt` from Phases 1–2.

**Version verification:**
```
$ pip show yfinance | grep Version   → Version: 1.4.1  (matches requirements.txt pin)
$ pip show pandas | grep Version     → Version: 3.0.3   (within requirements.txt >=2.0,<4.0)
$ python3 -c "import pyarrow"        → ModuleNotFoundError: No module named 'pyarrow'
```
`[VERIFIED: local pip environment]` — confirmed by direct `pip show`/`python3 -c` execution
in this repo's environment, cross-checked against `requirements.txt` pins (`[CITED:
requirements.txt]`).

## Package Legitimacy Audit

**No new external packages are recommended for this phase.** yfinance, pandas, and
jsonschema were already vetted and installed in Phases 1–2 (per `.planning/STATE.md`:
"02-00: yfinance -> github.com/ranaroussi/yfinance ... operator pre-approved before
install"). The Package Legitimacy Gate protocol therefore does not apply — there is nothing
new to run `slopcheck`/registry verification against.

If a future iteration decides Parquet caching is warranted (see "Alternatives Considered"
above), `pyarrow` would need to go through the full gate at that time — it is explicitly
**not** recommended now (`[ASSUMED: not needed at this data volume]`, see Assumptions Log).

**Packages removed due to slopcheck [SLOP] verdict:** none (no packages evaluated).
**Packages flagged as suspicious [SUS]:** none.

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         backtester/run.py (CLI)                          │
│  args: --symbols, --start, --end, --rules-json, --output-dir             │
└───────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. LOAD CONFIG                                                            │
│    load_strategy_config("rules.json")  ← bot/config/loader.py (unchanged) │
└───────────────────────────────┬───────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. PER-TRADING-DAY SETUP  (backtester/feed.py + reused scanner logic)     │
│    for each historical trading day D in [start, end]:                    │
│      a. download_daily_bars()/download_intraday_5m() ← bot/scanner/      │
│         fetcher.py (unchanged) — with a CSV read-through cache layer     │
│      b. bot.scanner.scanner._evaluate_symbol() per symbol ← D1/D2/D3 +   │
│         SMA200 + RVOL baseline, point-in-time (date < D cutoff, reused)  │
│      c. bot.scanner.scanner._compute_tod_baselines() ← RVOL-TOD baseline │
│      d. premarket_high[code] = max(high) of D's 5m bars with ET<09:30   │
│      e. signal_engine.set_premarket_highs(...)  ← reused sink method    │
└───────────────────────────────┬───────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. PER-BAR REPLAY LOOP (backtester/harness.py — ports                    │
│    bot.service.bot.TradingBot._process_bar bar-for-bar)                  │
│                                                                            │
│   for bar in SimulatedBarFeed.replay(symbols, day D):  # chronological   │
│       await position_manager.on_bar(bar)          ← ALWAYS runs first    │
│       signal = await signal_engine.on_bar(bar)     ← reused, unchanged   │
│       if signal:                                                         │
│           intent = await risk_engine.on_signal(signal)  ← reused         │
│           if intent:                                                     │
│               fill = await simulated_execution.consume_intent(intent)    │
│                 (fills at bar[i+1].open + slippage — NOT bar[i].close)   │
│               position_manager.register_position(pos)                    │
│               position_manager.on_fill(fill)                             │
└───────────────────────────────┬───────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. REPORT (backtester/report.py)                                          │
│    reads bot.state.store.StateStore.get_closed_trades() (reused)         │
│    computes win rate / avg R / max drawdown / profit factor              │
│    writes: <output-dir>/summary.json + <output-dir>/trades.csv           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure
```
backtester/
├── __init__.py
├── run.py            # CLI entry point (argparse: --symbols/--start/--end/--rules-json/--output-dir)
├── feed.py            # SimulatedBarFeed: yfinance+CSV-cache loader, chronological BarEvent replay
├── execution.py        # SimulatedExecution (N+1-open fill) + SimulatedGateway (get_positions/get_equity stub)
├── harness.py          # BacktestHarness — the ported _process_bar loop; owns bar_buffer dict for swing-low
├── report.py           # Metrics (win rate/avg R/max DD/profit factor) + CSV writer
└── cache/              # gitignored — CSV cache of yfinance responses, keyed by symbol+interval+date-range

tests/backtester/
├── __init__.py
├── test_feed.py         # yfinance mocked; CSV cache round-trip; ticker normalization
├── test_execution.py     # N+1-open fill timing; no-look-ahead synthetic-dataset test (BT-02 crit 2)
├── test_harness.py       # full-pipeline wiring test (mirrors tests/test_main_wiring.py pattern)
└── test_report.py        # win rate/avg R/max DD/profit factor formulas against known trade sets
```

### Pattern 1: Reuse the live per-bar pipeline unchanged (BT-01)
**What:** The harness constructs `TrendJoinLong`, `SignalEngine`, `RiskEngine`,
`PositionManager` with the exact same constructor signatures `bot/main.py` uses — only the
`gateway`/`engine` arguments differ.
**When to use:** Always — this is the entire point of BT-01 ("imports `bot/strategy/` and
`bot/position/` unchanged").
**Example:**
```python
# Source: bot/main.py (lines 75-112) — construction pattern to mirror, not import directly
cfg = load_strategy_config(args.rules_json)          # bot/config/loader.py — unchanged
store = StateStore(db_path=run_scratch_db_path).open()  # unchanged class, scratch path
strategy = TrendJoinLong(cfg)                          # unchanged
sim_gateway = SimulatedGateway(position_manager_ref=lambda: harness.position_manager)
sim_execution = SimulatedExecution(store=store, cfg=cfg)  # NEW — replaces ExecutionEngine
signal_engine = SignalEngine(cfg=cfg, gateway=sim_gateway, store=store)  # unchanged class
risk_engine = RiskEngine(cfg=cfg, gateway=sim_gateway, store=store, signal_engine=signal_engine)
bar_buffer: Dict[str, deque] = {}   # harness owns this — mirrors BarAggregator._bar_buffer
position_manager = PositionManager(
    store=store, engine=sim_execution, cfg=cfg, strategy=strategy,
    bar_buffer=bar_buffer, gateway=None,   # gateway=None → arm_stop_protection no-ops (correct for bar-only replay)
)
```

### Pattern 2: Point-in-time-safe reuse of the scanner's SMA/RVOL math (BT-02)
**What:** `bot.scanner.scanner._evaluate_symbol(symbol, data, cfg, scan_date, today_price)`
already restricts SMA200 and the RVOL baseline to `frame.index < scan_ts` (strictly prior
sessions) — this is the exact no-look-ahead guarantee BT-02 needs for D1/D2/D3 and the RVOL
denominator.
**When to use:** For every historical trading day being replayed, call this function (or a
thin harness wrapper around it) with `scan_date` = that day and daily-bar data downloaded
via `download_daily_bars` — do not recompute SMA/RVOL independently in `backtester/`.
**Example:**
```python
# Source: bot/scanner/scanner.py lines 115-255 (existing, reused as-is)
candidate = _evaluate_symbol(symbol, daily_data, cfg, scan_date=day, today_price=synthetic_today_price)
# synthetic_today_price is built from the SAME day's first regular-session 5m bar,
# mirroring resolve_today_price's RTH branch (fetcher.py lines 470-483) — NOT a live yfinance call.
```

### Pattern 3: SimulatedGateway — minimal stub, not a broker mock
**What:** `SignalEngine.on_bar()` Gate 4 calls `await gateway.get_positions()` expecting
`(ret, DataFrame-like)`. `RiskEngine.on_signal()` calls `gateway.get_equity()` ONLY when
`cfg.sizing_equity_usd is None` (current `rules.json` sets it to `100000`, so this call path
is never exercised in the current config — but the stub must still exist for config
robustness).
**When to use:** Implement only these two methods; do not attempt to satisfy
`ExecutionEngine`'s broader interface (`place_order`, `get_ask_price`, etc.) on this object —
those calls belong to `SimulatedExecution`, a separate class.
**Example:**
```python
# Source: interface contract read from bot/signal/signal_engine.py:587 and
# bot/risk/risk_engine.py:127 — no official docs exist for this internal interface.
class SimulatedGateway:
    def __init__(self, position_manager):
        self._pm = position_manager
    async def get_positions(self, refresh_cache: bool = True):
        rows = [
            {"code": code} for code, pos in self._pm._positions.items()
            if pos.phase.value not in ("AWAITING_FILL", "CLOSED")
        ]
        return 0, pd.DataFrame(rows)   # ret=0 == RET_OK
    async def get_equity(self) -> float:
        return 100_000.0   # only reached if cfg.sizing_equity_usd is None
```

### Anti-Patterns to Avoid
- **Reimplementing `ExecutionEngine`'s TTL/retry loop with `asyncio.sleep`:** wastes wall-clock
  and adds no fidelity a backtester needs — bar N+1 open fill IS the fill model per BT-02.
- **Recomputing SMA200/RVOL/RVOL-TOD math from scratch in `backtester/`:** the exact
  point-in-time logic already exists in `bot/scanner/scanner.py` (`_evaluate_symbol`,
  `_compute_tod_baselines`) — duplicating it risks the two implementations drifting and
  silently disagreeing, defeating the "confirming live/backtest code parity" phase goal.
- **Calling `SignalEngine.fetch_premarket_highs()` in backtest:** that method calls
  `gateway.get_market_snapshot()`, a broker-shaped call with no offline equivalent. Compute
  premarket highs directly from the historical 5m dataset and call `set_premarket_highs()`
  (the sink method) instead.
- **Passing a real or mocked `MoomooGateway` into `PositionManager`:** the live
  `arm_stop_protection()` path (`use_broker_stop_orders` / quote-tick subscribe) has no
  offline analog since the backtester only has 5m bar granularity, not tick data. Pass
  `gateway=None` so it correctly no-ops and the bar-close FSM stop check (D-03) is the sole
  (and correct, for this granularity) stop mechanism — this matches production's current
  `rules.json` setting (`use_broker_stop_orders: false`, so production *also* runs primarily
  on the D-02 quote-tick / D-03 bar-close paths, not broker-side stops).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SMA200 / RVOL baseline / RVOL-TOD baseline computation | A new point-in-time indicator module in `backtester/` | `bot.scanner.scanner._evaluate_symbol` + `_compute_tod_baselines` (call directly) | Already implements the exact `date < scan_date` no-look-ahead cutoff BT-02 requires; a second implementation is a parity-drift risk, not a savings. |
| Trade/position persistence schema | A new SQLite schema for backtest trades/positions | `bot.state.store.StateStore` pointed at a scratch DB path (`BOT_STATE_DB` env var or explicit `db_path=` constructor arg) | `StateStore` already has `positions`, `trades`, `daily_trade_count`, `pending_intents`, `daily_scan`, `tod_baselines` — every table the reused pipeline writes to. `resolve_db_path()` already supports override via env var for exactly this kind of test/scratch use. |
| Win rate / R-multiple / drawdown / profit-factor math | Ad-hoc pandas one-liners scattered through `report.py` | A small, well-tested `metrics.py` with named formulas (see Code Examples below) — still new code (no existing implementation to reuse), but keep it in one place with one test file | REP-02 (v2, deferred) explicitly anticipates "parameter sweeps" building on this report — a single well-named metrics module is the extension point; scattered one-liners are not. |
| Ticker format normalization (yfinance ↔ moomoo) | A new `AAPL` ↔ `US.AAPL` mapping in `backtester/` | `bot.scanner.universe.yfinance_to_moomoo` (and its inverse, `.removeprefix("US.")` pattern already used in `scanner.py` line 546/558) | Already handles the `BRK.B` → `BRK-B` yfinance-vs-moomoo dot/dash edge case (per STATE.md `02-01`); a second mapping risks silently diverging on exactly that edge case. |

**Key insight:** almost nothing in this phase should be new logic — the phase's entire
purpose (per its own goal statement: "confirming live/backtest code parity") is defeated if
the backtester reimplements strategy math instead of importing it. The only genuinely new
code is the *replay mechanics* (feed, N+1 fill, report) — everything else is composition of
existing, already-tested modules.

## Common Pitfalls

### Pitfall 1: Filling at bar N close instead of bar N+1 open (BT-02 core requirement)
**What goes wrong:** `SignalEngine.on_bar(bar_N)` evaluates the just-closed bar and, if all
gates pass, emits a signal using `bar_N.close` as `entry_price` (see
`RiskEngine.on_signal`: `entry_price = signal.bar.close`). If `SimulatedExecution` fills at
that same `entry_price` immediately, the backtest is trading on the bar-close price it just
used to generate the signal — a classic look-ahead bug (the signal literally could not have
been acted on until the NEXT bar).
**Why it happens:** `RiskEngine.OrderIntent.entry_price` is set to the signal bar's close as
an *indicative* price — the live system's real fill price comes later, from
`ExecutionEngine`'s actual broker fill (`fill.avg_fill_price`), which happens on bar N+1 or
later in wall-clock time. A naive backtest port would fill instantly at `intent.entry_price`
without re-deriving the actual next-bar price.
**How to avoid:** `SimulatedExecution.consume_intent(intent)` must look up bar N+1's `open`
for `intent.code` from the feed (the harness must expose "next bar for this code" to the
execution simulator) and construct the `FillEvent` with `avg_fill_price = bar_{N+1}.open (+
slippage)` — never `intent.entry_price`. Apply the same N+1-open rule to exit fills
(`manage_exit`) for consistency, though POS-01/02/03 exit triggers already fire on bar close
so the "exit at next bar's open" convention should be applied there too, matching entries.
**Warning signs:** Backtest win rate/avg-R implausibly high on the FIRST trading day of any
symbol's inclusion, or entry price in the trade CSV exactly equal to a `signal_emitted` log
line's `close` field.

### Pitfall 2: yfinance's intraday window is a rolling window, not an arbitrary range
**What goes wrong:** Requesting `yf.download(interval="5m", start="2026-01-01",
end="2026-02-01")` today (2026-07-06) returns an empty frame or an error — NOT a 60-day
slice starting from 2026-01-01. The 60-day (5m) / 7-day (1m) limit is measured from *today*,
not from the requested start date.
**Why it happens:** This is a Yahoo Finance server-side restriction that `yfinance` surfaces
as an error or empty result, not something client-side pagination can work around.
**How to avoid:** Do not attempt to backtest date ranges older than "today minus ~58 trading
days" using live yfinance 5m fetches. For anything older, the harness must read from the
local CSV cache (built up incrementally by running backtests/production scans over time) or
an operator-supplied flat-file export (BT-04's explicit fallback). Document the effective
backtest window in the CLI's `--help` and fail with a clear error (not a silent empty
result) when `--start` falls outside both the live-fetch window and the cache's coverage.
**Warning signs:** `download_intraday_5m` returning an empty frame for a requested range with
no exception raised (yfinance often degrades silently to an empty DataFrame here rather than
raising) `[MEDIUM confidence — cross-verified via ranaroussi/yfinance issue #1510 and
multiple independent guides, see Sources]`.

### Pitfall 3: Column-name casing mismatch between daily and raw 5m frames
**What goes wrong:** `bot.scanner.fetcher.get_ticker_frame()` lowercases columns
(`ticker_df.columns.str.lower()`), but `_compute_tod_baselines` in `scanner.py` reads
`"Volume"` (capital V) directly from the RAW `yf.download` result (i.e. NOT passed through
`get_ticker_frame`). A `BarEvent` built directly from a raw 5m frame without going through
`get_ticker_frame` first will KeyError on `"open"`/`"high"`/`"low"`/`"close"`/`"volume"`.
**Why it happens:** `yf.download()`'s native column names are Title Case
(`Open`/`High`/`Low`/`Close`/`Volume`); only code paths that explicitly call
`get_ticker_frame()` see lowercase columns.
**How to avoid:** Always route 5m/daily frames obtained from `_download_batch`
(`download_daily_bars`/`download_intraday_5m`) through `get_ticker_frame(data, symbol)`
before building `BarEvent`s — this is the same discipline `scanner.py`'s
`_evaluate_symbol` already follows for daily bars (`frame = get_ticker_frame(data, symbol)`,
line 151).
**Warning signs:** `KeyError: 'open'` or `KeyError: 'Open'` (whichever casing was NOT used)
when constructing `BarEvent` from a fresh yfinance download.

### Pitfall 4: `StateStore` DB-path collision with the live bot
**What goes wrong:** `StateStore()` with no `db_path` argument defaults to
`resolve_db_path()` → `BOT_STATE_DB` env var or `data/bot_state.db`. Running the backtester
without overriding this will open (and mutate — `daily_trade_count`, `positions`,
`pending_intents`) the SAME database file the live paper-trading bot uses, corrupting live
state or vice versa.
**Why it happens:** `StateStore`'s default is designed for the single-instance live-bot use
case; nothing in its constructor warns against reuse for a second, unrelated process.
**How to avoid:** `backtester/run.py` must ALWAYS pass an explicit `db_path=` (e.g.
`backtester/runs/<run_id>/state.db`, one fresh file per invocation) — never rely on the
default. Add a CLI-level guard that refuses to run if the resolved path equals
`data/bot_state.db` exactly.
**Warning signs:** Backtest run producing `daily_trade_count` entries that show up in the
LIVE `reports/latest.html` dashboard, or a backtest silently gated by a real, currently-tripped
`RISK-CIRCUIT` circuit breaker row.

### Pitfall 5: Premarket-high source divergence (live snapshot field vs. backtest 5m bars)
**What goes wrong:** Live `SignalEngine.fetch_premarket_highs()` reads the broker's
`pre_high_price` snapshot field (a Moomoo-computed premarket high that may include
after-hours/extended data the bot itself never subscribed to). A backtest computing
premarket high as `max(high)` of its OWN 5m bars with ET time < 09:30 is a *different*,
narrower definition (bounded by whatever 5m bars yfinance actually returned in the premarket
window, `prepost=True` on the 1m fetcher but the 5m fetcher (`download_intraday_5m`) is
called with `prepost=False`).
**Why it happens:** BT-02 requires a premarket-high value but no live-parity data source
exists offline — this is an unavoidable divergence, not a bug to "fix", but it must be
called out so backtest results are not over-interpreted as bit-for-bit reproductions of what
the live bot would have done on the same historical day.
**How to avoid:** Either (a) fetch premarket 5m bars with `prepost=True` specifically for the
premarket-high calculation (separate from the RVOL-TOD 5m fetch, which correctly stays
`prepost=False` per its own docstring rationale), or (b) explicitly document in the backtest
report that premarket-high is a same-methodology-different-source approximation. Recommend
(a) for closer parity.
**Warning signs:** Backtest signal counts are visibly lower than a live session on the same
day/symbols, traceable to `signal_skipped_no_premarket_high` in the harness's log output.

### Pitfall 6: Swing-low trail wiring — `bar_buffer` must be harness-owned, not gateway/execution-owned
**What goes wrong:** `PositionManager._compute_swing_low()` reads from `self._bar_buffer`
(a `Dict[str, deque]` passed at construction) — in the live bot this deque is
`BarAggregator._bar_buffer`, populated as a side effect of the SDK push handler. If the
harness forgets to populate an equivalent buffer per code as it replays bars, POS-03
(trailing stop) silently never fires (`_compute_swing_low` returns `None` on an empty/missing
buffer, and `evaluate_close` treats `new_swing_low=None` as "no update this bar" — not an
error, just silently wrong).
**Why it happens:** `bar_buffer` is an optional constructor parameter defaulting to `None`
("no bar_buffer for tests that do not wire a live BarAggregator") — it is easy to forget when
porting the pipeline outside its original live-service context.
**How to avoid:** The harness must append every closed bar's OHLCV dict to a
`Dict[str, deque(maxlen=50)]` (mirroring `BarAggregator._bar_buffer`'s exact shape:
`{"open":..., "high":..., "low":..., "close":..., "volume":...}`) BEFORE calling
`position_manager.on_bar(bar)`, and pass that same dict into `PositionManager(bar_buffer=...)`
at construction.
**Warning signs:** Backtest trades reaching `BREAKEVEN` phase but never transitioning to
`TRAILING`; test coverage gap if `test_harness.py` never asserts on a `TRAIL_UP` FSM action.

## Code Examples

### Performance Metrics (BT-03) — standard formulas
```python
# Source: standard trading-performance definitions (no single canonical library —
# these are the textbook formulas; [CITED: general quant-trading literature, not
# tied to a specific library since no Python package is a clear "standard" for this]).
def compute_metrics(trades: list[dict]) -> dict:
    """trades: rows from StateStore.get_closed_trades() — each has realized_pnl, r_multiple."""
    wins = [t for t in trades if t["realized_pnl"] > 0]
    losses = [t for t in trades if t["realized_pnl"] <= 0]

    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_r = sum(t["r_multiple"] for t in trades) / len(trades) if trades else 0.0

    gross_profit = sum(t["realized_pnl"] for t in wins)
    gross_loss = abs(sum(t["realized_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown: running peak-to-trough on the cumulative-PnL equity curve
    cum_pnl, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:  # trades must be in chronological (exit-time) order
        cum_pnl += t["realized_pnl"]
        peak = max(peak, cum_pnl)
        max_dd = max(max_dd, peak - cum_pnl)

    return {
        "win_rate": win_rate, "avg_r_multiple": avg_r,
        "profit_factor": profit_factor, "max_drawdown_usd": max_dd,
        "total_trades": len(trades),
    }
```

### N+1-open fill (BT-02 core mechanic)
```python
# Source: original — no library implements this; it is the harness's core contract.
class SimulatedExecution:
    def __init__(self, feed, slippage_usd: float = 0.0):
        self._feed = feed          # SimulatedBarFeed — exposes next_bar(code, after_time_key)
        self._slippage = slippage_usd

    async def consume_intent(self, intent) -> Optional[FillEvent]:
        next_bar = self._feed.next_bar(intent.code, after=intent.source_signal.bar.time_key)
        if next_bar is None:
            return None  # no next bar (end of session/data) — intent unfilled, matches D-05 abandon
        fill_price = next_bar["open"] + self._slippage
        return FillEvent(
            order_id=str(uuid.uuid4()), intent_id=intent.intent_id, code=intent.code,
            filled_qty=intent.quantity, avg_fill_price=fill_price,
            is_entry=True, fill_time=next_bar["time_key"],
        )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `deal_list_query` for fill detection | `order_list_query` (dealt_qty) for fill detection | Phase 4 (per PROJECT history: "deal_list_query unsupported on paper") | Not applicable to backtester (no broker calls at all) — noted so the harness's `SimulatedExecution` does not accidentally try to mimic the deprecated path. |
| Broker-side stop orders as primary (`use_broker_stop_orders: true`) | Bar-close FSM stop / quote-tick fallback as primary (`use_broker_stop_orders: false` in current `rules.json`) | Phase 7 (07-03/07-05), current setting confirmed live 2026-07-06 | The backtester should treat bar-close stop evaluation (D-03) as the ONLY simulatable stop mechanism (5m granularity has no ticks) — this happens to match the CURRENT live default, but would still be the right backtest choice even if `use_broker_stop_orders` were `true`, since tick data is not part of BT-01/02's scope. |

**Deprecated/outdated:** None specific to this phase's own history (Phase 6 has not been
built yet — nothing to deprecate). The two rows above are broader project-history context
the backtester implementer should know so as not to "restore" an already-abandoned pattern.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | CSV caching is sufficient and Parquet/pyarrow is not warranted at current data volume (≤20 symbols × ≤60 days × 5m bars) | Standard Stack / Alternatives Considered | If backtests later expand to the full ~500-symbol universe over multi-year history, CSV parse time could become a bottleneck — revisit with a benchmark before assuming CSV stays sufficient at that scale. Low risk for the phase's stated scope (BT-04 names both CSV and Parquet as acceptable). |
| A2 | Applying the same "fill at next bar's open" rule to EXITS (not just entries) is the correct backtest convention, even though BT-02's success criterion only explicitly names entries | Pitfall 1 | If the planner/operator wants exit fills to remain at the FSM-triggering bar's close (a common, simpler backtest convention), the exit-fill-timing decision should be confirmed explicitly rather than defaulting to symmetric N+1 timing — this changes computed R-multiples and drawdown non-trivially. |
| A3 | Premarket-high computed from 5m bars with `prepost=True` (not the RVOL-TOD fetcher's `prepost=False` 5m calls) is close enough to the live broker's `pre_high_price` field for backtest purposes | Pitfall 5 | If yfinance's premarket 5m bar coverage is materially different from the broker's premarket tape (thinner/less liquid symbols may have gaps), backtest signal counts could diverge from what the live bot would have produced on the same day — should be flagged in the report output, not silently assumed equivalent. |

## Open Questions

1. **Should the backtest date range be operator-specified per run, or should the CLI default
   to "maximum available" (today minus ~58 trading days)?**
   - What we know: yfinance 5m data caps the realistic default window; daily-bar D1/D2/D3
     filters could theoretically go back further (yfinance daily bars support `period="1y"` or
     `"max"`) but are gated by 5m-bar availability for the entry-signal side.
   - What's unclear: Whether the operator wants a CLI that silently clips an out-of-range
     `--start` to the available window, or one that errors loudly.
   - Recommendation: Error loudly with a clear message naming the actual available window
     (computed from `datetime.now() - 58 trading days`) — silent clipping risks the operator
     believing they backtested a longer period than they actually did.

2. **Exit-model comparison for EXIT-MODEL (Phase 7, gated on this backtester per `07-06-PLAN.md`)
   — does Phase 6 need to support swapping `exit.model` variants, or is that Phase 7's job?**
   - What we know: `rules.json`'s `exit.model` schema already enumerates
     `partial_be_trail`/`fixed_2r`/`full_to_1p5r_trail`, but the loader fail-closes on
     anything except `partial_be_trail` until "Phase 6 backtester evidence exists."
   - What's unclear: Whether Phase 6's own success criteria require running the OTHER two
     exit models (which have no `PositionState` FSM implementation yet — `fixed_2r` and
     `full_to_1p5r_trail` are schema-only stubs), or whether Phase 6 just needs to prove out
     `partial_be_trail` and Phase 7 (07-06) is responsible for building + comparing the other
     two variants using this phase's harness.
   - Recommendation: Scope Phase 6 to running the CURRENT (`partial_be_trail`) FSM only — BT-01
     through BT-04 as written only require replaying "the exact same PositionState FSM," which
     today means `partial_be_trail`. Building the other two exit-model FSM variants is Phase
     7's explicit responsibility (07-06 plan already names this dependency). Flag this
     boundary explicitly to the planner so Phase 6 scope doesn't silently balloon into
     building unimplemented FSM variants.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| yfinance | Historical bar source (BT-04) | Yes | 1.4.1 | — |
| pandas | DataFrame bar handling | Yes | 3.0.3 | — |
| jsonschema | rules.json loading (reused) | Yes | installed, `requirements.txt` pins `>=4.0,<5.0` | — |
| pyarrow | Parquet cache (considered, not recommended) | No | — | CSV cache (recommended default — see Assumptions A1) |
| Network access to Yahoo Finance | Live yfinance fetches for the ~60-day rolling window | Assumed yes (same network dependency Phase 2's scanner already has) | — | Local CSV cache built from prior successful fetches; operator-supplied flat-file export per BT-04 |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** pyarrow (fallback: CSV, recommended as the default —
not merely a fallback).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (no `pytest.ini`/`pyproject.toml` config found; discovery works via default rootdir conventions — 590 tests currently collected under `tests/`) |
| Config file | none — see Wave 0 (no config needed; existing project convention) |
| Quick run command | `python3 -m pytest tests/backtester/ -x` |
| Full suite command | `python3 -m pytest tests/ -x` |

Async test convention observed project-wide (`[VERIFIED: grep of tests/position/test_manager.py,
tests/signal/, etc.]`): tests call `asyncio.run(coro)` directly inside a plain `def test_*`
function rather than using `@pytest.mark.asyncio` — even though `pytest-asyncio==1.4.0` is
installed. Match this existing convention in `tests/backtester/` for consistency
(`bot/config/loader.py`-adjacent modules note "Python 3.14 removed implicit default event
loop in main thread" as the reason for explicit `asyncio.run()`).

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BT-01 | Harness imports `bot/strategy/`, `bot/position/` unchanged and loads real `rules.json` | integration | `python3 -m pytest tests/backtester/test_harness.py -x` | ❌ Wave 0 |
| BT-02 | Entries fire at bar N+1 open; synthetic ahead-only-signal dataset produces no premature entry | unit | `python3 -m pytest tests/backtester/test_execution.py -x` | ❌ Wave 0 |
| BT-03 | Report metrics (win rate/avg R/max DD/profit factor) match known trade-set fixtures; CSV written with correct columns | unit | `python3 -m pytest tests/backtester/test_report.py -x` | ❌ Wave 0 |
| BT-04 | yfinance fetch + CSV-cache round-trip; ticker normalization; graceful handling of an out-of-window date request | unit | `python3 -m pytest tests/backtester/test_feed.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/backtester/ -x`
- **Per wave merge:** `python3 -m pytest tests/ -x` (full 590+ test suite — must stay green;
  this phase must not touch any file under `bot/`, so a regression here would indicate an
  accidental live-code edit)
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/backtester/__init__.py` — new test package
- [ ] `tests/backtester/test_feed.py` — covers BT-04
- [ ] `tests/backtester/test_execution.py` — covers BT-02
- [ ] `tests/backtester/test_harness.py` — covers BT-01
- [ ] `tests/backtester/test_report.py` — covers BT-03
- [ ] No new test framework/config needed — existing pytest discovery already covers a new
  `tests/backtester/` directory with no changes to any shared conftest (none exists).

## Security Domain

`security_enforcement` is enabled in `.planning/config.json` (`security_asvs_level: 1`,
`security_block_on: "high"`). This phase is an offline CLI tool with no network-facing
surface, no authentication, and no user-facing input beyond local CLI args/files — most ASVS
categories genuinely do not apply.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth surface — offline CLI, no accounts. |
| V3 Session Management | No | No sessions — single-process batch run. |
| V4 Access Control | No | Single-operator local tool. |
| V5 Input Validation | Yes | `rules.json` already validated via `jsonschema` (`bot/config/schema.py`, reused unchanged). CLI args (`--symbols`, `--start`, `--end`) should be validated for sane types/ranges (e.g. `--start` parseable as a date, `--symbols` non-empty) before hitting the yfinance fetch layer — cheap, avoids a confusing downstream stack trace. |
| V6 Cryptography | No | No secrets, no crypto operations in this phase. |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| CSV cache file path traversal (if `--output-dir`/cache-dir is ever taken from an untrusted source) | Tampering | Not a realistic threat for a single-operator local CLI tool with no remote input — noted only for completeness; no mitigation code needed given the trust model (matches the project's existing `CLAUDE.md` "operator running it on their own machine" framing). |
| Accidental live-DB corruption from a backtest run pointed at the wrong SQLite path | Tampering (of the bot's own trusted state, self-inflicted) | See Pitfall 4 above — this is the one real "safety" concern in this phase, and it's an operational/config-path issue, not a classic security vulnerability. Treat the `BOT_STATE_DB` scratch-path guard as the mitigating control. |

## Sources

### Primary (HIGH confidence)
- `bot/strategy/core.py`, `bot/strategy/trend_join_long.py`, `bot/strategy/indicators.py` — StrategyCore ABC contract, TrendJoinLong implementation, pure indicator math (read directly).
- `bot/signal/signal_engine.py`, `bot/signal/bar_aggregator.py`, `bot/signal/events.py` — SignalEngine gate sequence, BarEvent/SignalEvent field semantics.
- `bot/risk/risk_engine.py`, `bot/risk/events.py` — RiskEngine sizing pipeline, OrderIntent fields, `sizing_equity_usd` bypass of live equity read.
- `bot/position/manager.py`, `bot/position/state.py` — PositionManager orchestration, PositionState FSM, `bar_buffer` wiring, `gateway=None` no-op path.
- `bot/execution/engine.py`, `bot/execution/events.py` — ExecutionEngine's broker-polling model (confirms why it must NOT be reused as-is for backtest).
- `bot/gateway/gateway.py` — full async interface surface `SignalEngine`/`RiskEngine`/`PositionManager` call, used to derive the minimal `SimulatedGateway` stub.
- `bot/config/loader.py`, `bot/config/schema.py`, `rules.json` — confirms `sizing_equity_usd: 100000` (fixed basis, no live equity call needed) and `use_broker_stop_orders: false` (current production default).
- `bot/scanner/fetcher.py`, `bot/scanner/scanner.py` — yfinance download kernel (`_download_batch`), `get_ticker_frame` column-casing behavior, `_evaluate_symbol`/`_compute_tod_baselines` point-in-time math to be reused, `resolve_today_price` RTH/premarket phase-selection logic.
- `bot/main.py`, `bot/service/bot.py` (`_process_bar`, lines 238-321) — the exact live per-bar pipeline sequence the backtest harness must port.
- `bot/state/store.py` — `StateStore` constructor/`resolve_db_path()`/`BOT_STATE_DB` env override, confirms reuse is safe with a scratch path.
- `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md` — phase requirements, sketched plans, decision history.
- Local environment: `pip show yfinance/pandas`, `python3 -c "import pyarrow"` — direct verification of installed versions.

### Secondary (MEDIUM confidence)
- [Intraday data cannot extend last 60 days · Issue #1510 · ranaroussi/yfinance](https://github.com/ranaroussi/yfinance/issues/1510) — confirms the rolling-window (not arbitrary-range) nature of the yfinance intraday limit.
- [yfinance Library - A Complete Guide - AlgoTrading101 Blog](https://algotrading101.com/learn/yfinance-guide/) — corroborates 1m=7-day/intraday=60-day limits.
- [Reliably download historical market data from with Python | Ran Aroussi](https://aroussi.com/post/python-yahoo-finance) — yfinance maintainer's own guidance on interval/period constraints.

### Tertiary (LOW confidence)
- [yfinance Library – A Complete Guide | IBKR Campus US](https://www.interactivebrokers.com/campus/ibkr-quant-news/yfinance-library-a-complete-guide/) — general corroboration only, not cited for any specific numeric claim beyond what the primary GitHub issue already confirms.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all versions directly verified in the local environment.
- Architecture: HIGH — every claim about `bot/` internals is from direct source reads of the files that will be reused, not from memory or inference.
- Pitfalls: MEDIUM-HIGH — pitfalls 1, 3, 4, 6 are derived directly from reading the exact reused code; pitfalls 2 and 5 depend partly on external yfinance behavior (MEDIUM, cross-verified via GitHub issue + docs) and an architectural judgment call about premarket-high parity (flagged as Assumption A3).

**Research date:** 2026-07-06
**Valid until:** 30 days for the `bot/` architecture findings (stable, only changes if Phase 7 further modifies `bot/`); 90 days for the yfinance interval-limit finding (a third-party API constraint unlikely to change on a short timescale, but not controlled by this project).
