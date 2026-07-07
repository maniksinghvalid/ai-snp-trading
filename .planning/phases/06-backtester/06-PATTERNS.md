# Phase 6: Backtester - Pattern Map

**Mapped:** 2026-07-06
**Files analyzed:** 10 (5 backtester/ modules + 5 tests/backtester/ modules; run.py CLI and cache/ dir carry no independent pattern beyond composition)
**Analogs found:** 10 / 10 (all role-match or exact; no "no analog" files)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `backtester/__init__.py` | config | — | `bot/__init__.py` | exact (empty package marker) |
| `backtester/run.py` | route/CLI | request-response | `bot/main.py` (composition root) + `bot/__main__.py` (entry dispatch) | role-match |
| `backtester/feed.py` | service | batch/file-I/O | `bot/scanner/fetcher.py` (`download_daily_bars`, `download_intraday_5m`, `get_ticker_frame`) | exact (same yfinance batch-download kernel, new caller) |
| `backtester/execution.py` | service | event-driven (bar-driven fill) | `bot/execution/engine.py` (`ExecutionEngine.consume_intent`) — pattern reference only, NOT reused; `bot/gateway/gateway.py` (interface surface for `SimulatedGateway` stub) | role-match (same public surface, deliberately different internals per Anti-Pattern) |
| `backtester/harness.py` | controller (orchestrator) | event-driven | `bot/service/bot.py::TradingBot._process_bar` (lines 238-321) | exact (direct port target, ported not imported) |
| `backtester/report.py` | service (aggregation) | batch/transform | `bot/service/report.py` (pattern reference only — daily HTML dashboard) + `bot/state/store.py::get_closed_trades`/`get_daily_trade_stats` (reused read pattern) | role-match |
| `tests/backtester/__init__.py` | test | — | `tests/__init__.py` if present, else any `tests/<subpkg>/__init__.py` | exact |
| `tests/backtester/test_feed.py` | test | batch/file-I/O | `tests/scanner/test_fetcher.py` (yfinance mocking conventions) | role-match |
| `tests/backtester/test_execution.py` | test | event-driven | `tests/execution/test_engine.py` (fill/intent test shape) | role-match |
| `tests/backtester/test_harness.py` | test | event-driven | `tests/test_main_wiring.py` (full real-construction wiring test pattern) | exact |
| `tests/backtester/test_report.py` | test | transform | `tests/state/test_store.py` (get_closed_trades fixtures) — pattern reference for trade-row shape | role-match |

## Pattern Assignments

### `backtester/run.py` (CLI entry point, request-response)

**Analog:** `bot/main.py` (composition root) + `bot/__main__.py` (module dispatch)

**Composition-root pattern** (`bot/main.py` lines 42-142, full file read):
```python
def main() -> None:
    configure_logging()
    _logger = get_logger(__name__)
    try:
        cfg = load_strategy_config("rules.json")
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    gateway = MoomooGateway(get_gateway_config())
    store = StateStore().open()
    strategy = TrendJoinLong(cfg)
    engine = ExecutionEngine(gateway=gateway, store=store, cfg=cfg)
    signal_engine = SignalEngine(cfg=cfg, gateway=gateway, store=store)
    risk_engine = RiskEngine(cfg=cfg, gateway=gateway, store=store, signal_engine=signal_engine)
    position_manager = PositionManager(
        store=store, engine=engine, cfg=cfg, strategy=strategy,
        on_entry_alert=..., on_exit_alert=..., gateway=gateway,
    )
    bot = TradingBot(cfg=cfg, gateway=gateway, store=store, ...)
    asyncio.run(bot.run())
```
**What to copy:** the ordered construction sequence (config load → error-to-stderr+exit(1) →
component construction → run) and the `ConfigError` handling block verbatim (same
`load_strategy_config("rules.json")` call — BT-01 requires the backtester read the SAME
rules.json via the SAME loader). Swap step 4 (`MoomooGateway`/`ExecutionEngine`) for
`SimulatedGateway`/`SimulatedExecution`; swap `StateStore().open()` for
`StateStore(db_path=<scratch path from argparse>).open()` (Pitfall 4 — never the default path).

**Entry dispatch pattern** (`bot/__main__.py`, full file, 10 lines):
```python
from bot.main import main
if __name__ == "__main__":
    main()
```
`run.py` differs here because it needs argparse (`--symbols`, `--start`, `--end`,
`--rules-json`, `--output-dir`) — no existing script in this codebase does argparse in
`bot/` (bot has no CLI flags), so use the `skills/moomooapi/scripts/*.py` argparse convention
noted in CLAUDE.md instead: dash-separated long options, `--json` style boolean flags,
`argparse.ArgumentParser()` with positional/required args validated before any I/O
(V5 Input Validation — RESEARCH Security Domain).

---

### `backtester/feed.py` (service, batch/file-I/O)

**Analog:** `bot/scanner/fetcher.py`

**Imports pattern** (top of `fetcher.py`):
```python
import pandas as pd
import yfinance as yf
```

**Batch-download + column-casing pattern** (`fetcher.py` lines 285-315 wrapping the shared
kernel at 136-160, and `get_ticker_frame` lines 334-361):
```python
def download_daily_bars(yf_symbols, threads=5, degradation_threshold=0.10):
    return _download_batch(
        yf_symbols, threads=threads, degradation_threshold=degradation_threshold,
        download_kwargs={"period": "1y", "interval": "1d"},
        abort_event="scan_aborted_data_degradation",
        partial_event="scan_partial_data", ...
    )

def download_intraday_5m(yf_symbols, threads=5, degradation_threshold=0.10):
    return _download_batch(
        yf_symbols, threads=threads, degradation_threshold=degradation_threshold,
        download_kwargs={"period": "30d", "interval": "5m", "prepost": False},
        abort_event="tod_baseline_scan_aborted_data_degradation",
        partial_event="tod_baseline_partial_data", ...
    )

def get_ticker_frame(data, symbol):
    try:
        ticker_df = data[symbol]
    except (KeyError, TypeError):
        return None
    if ticker_df is None or not isinstance(ticker_df, pd.DataFrame):
        return None
    if ticker_df.dropna(how="all").empty:
        return None
    ticker_df = ticker_df.copy()
    ticker_df.columns = ticker_df.columns.str.lower()
    return ticker_df
```
**What to copy:** call `download_daily_bars`/`download_intraday_5m` directly (do not
reimplement the yfinance batch kernel — Don't Hand-Roll table) and ALWAYS route the raw
result through `get_ticker_frame(data, symbol)` before constructing a `BarEvent` (Pitfall 3 —
raw `yf.download()` frames are Title-Case columns; `get_ticker_frame` lowercases them).
`feed.py`'s CSV cache layer is new code (no analog) — write/read-through cache keyed by
`symbol+interval+date-range`, falling back to `download_intraday_5m`/`download_daily_bars`
only on a cache miss, per the RESEARCH recommendation (grows backtestable history over time).

**Ticker normalization** (`bot/scanner/universe.py` lines 63-71):
```python
def yfinance_to_moomoo(yf_symbol: str) -> str:
    return f"US.{yf_symbol}"
```
Reuse this directly; do not write a second mapping (Don't Hand-Roll table — `BRK.B`/`BRK-B`
edge case already handled here).

---

### `backtester/execution.py` (service, event-driven bar-fill)

**Analog:** `bot/execution/engine.py` (public surface only — NOT the internals) +
`bot/gateway/gateway.py` (interface surface for the `SimulatedGateway` stub)

**Public surface to match** (so `PositionManager`/pipeline code is unchanged — confirm exact
method names/signatures by reading `bot/execution/engine.py`'s `consume_intent` and
`manage_exit` signatures before implementing):
```python
class SimulatedExecution:
    def __init__(self, feed, slippage_usd: float = 0.0):
        self._feed = feed
        self._slippage = slippage_usd

    async def consume_intent(self, intent) -> Optional[FillEvent]:
        next_bar = self._feed.next_bar(intent.code, after=intent.source_signal.bar.time_key)
        if next_bar is None:
            return None  # no next bar — intent unfilled (matches D-05 abandon)
        fill_price = next_bar["open"] + self._slippage
        return FillEvent(
            order_id=str(uuid.uuid4()), intent_id=intent.intent_id, code=intent.code,
            filled_qty=intent.quantity, avg_fill_price=fill_price,
            is_entry=True, fill_time=next_bar["time_key"],
        )
```
**What to copy:** the FillEvent construction fields from `bot/execution/engine.py` (read the
real `FillEvent` dataclass there — do not invent field names) and the N+1-open fill rule
(Pitfall 1 — never fill at `intent.entry_price`, always the injected next bar's `open`).
Apply the same N+1-open convention to `manage_exit` (RESEARCH Assumption A2).

**SimulatedGateway stub** (interface contract read directly from
`bot/signal/signal_engine.py:587` `gateway.get_positions()` call site and
`bot/risk/risk_engine.py:127` `gateway.get_equity()` call site — no separate doc):
```python
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
Implement ONLY these two methods — do not attempt broader `MoomooGateway` parity (Pattern 3
in RESEARCH). Pass `gateway=None` into `PositionManager` (not `SimulatedGateway`) so
`arm_stop_protection()` correctly no-ops (Anti-Patterns section).

---

### `backtester/harness.py` (controller/orchestrator, event-driven)

**Analog:** `bot/service/bot.py::TradingBot._process_bar` (lines 238-321, direct port target)

**Core per-bar pipeline to port bar-for-bar**:
```python
async def _process_bar(self, bar_data: dict) -> None:
    from bot.signal.events import BarEvent
    try:
        bar = BarEvent(
            code=bar_data["code"], time_key=bar_data["time_key"],
            open=bar_data["open"], high=bar_data["high"], low=bar_data["low"],
            close=bar_data["close"], volume=bar_data["volume"],
            hod=bar_data["hod"], lod=bar_data["lod"],
            cum_volume=bar_data.get("cum_volume", 0),
        )
    except Exception:
        _logger.warning("on_bar_closed_bar_construction_error", bar_data=bar_data, exc_info=True)
        return

    await self._position_manager.on_bar(bar)          # ALWAYS runs first (D-06)
    if not self._entries_enabled:
        return

    signal = await self._signal_engine.on_bar(bar)
    if signal is None:
        return
    intent = await self._risk_engine.on_signal(signal)
    if intent is None:
        return

    fill = await self._execution_engine.consume_intent(intent)
    if fill is not None:
        pos = PositionState(
            position_id=str(uuid4()), code=intent.code, phase=PositionPhase.AWAITING_FILL,
            entry_price=intent.entry_price, initial_stop=intent.stop_price,
            trail_stop=intent.stop_price, full_quantity=intent.quantity,
            remaining_quantity=intent.quantity, entry_order_id=fill.order_id,
        )
        self._position_manager.register_position(pos)   # DB-first
        self._position_manager.on_fill(fill)             # AWAITING_FILL -> ACTIVE
        await self._position_manager.arm_stop_protection(pos)   # no-ops (gateway=None)
    else:
        self._store.resolve_pending_intent(intent.intent_id, "ABANDONED")
    self._signal_engine.note_intent_resolved()
```
**What to copy:** this exact sequence and ordering (management-always-first, then entries
gate, then signal→risk→execution→register/fill/arm chain). Drop the D-08 circuit-breaker
side-effect call and `_entries_enabled` gate if the harness has no equivalent kill-switch
concept — otherwise keep them for parity. `bar_buffer` wiring (Pitfall 6) MUST append every
closed bar's OHLCV dict to a `Dict[str, deque(maxlen=50)]` BEFORE calling
`position_manager.on_bar(bar)`, mirroring `BarAggregator._bar_buffer`'s exact shape
(`{"open":..., "high":..., "low":..., "close":..., "volume":...}`).

**Construction pattern to mirror** (see Pattern 1 in RESEARCH, sourced from `bot/main.py`
lines 75-112):
```python
cfg = load_strategy_config(args.rules_json)
store = StateStore(db_path=run_scratch_db_path).open()
strategy = TrendJoinLong(cfg)
sim_gateway = SimulatedGateway(position_manager_ref=lambda: harness.position_manager)
sim_execution = SimulatedExecution(store=store, cfg=cfg)
signal_engine = SignalEngine(cfg=cfg, gateway=sim_gateway, store=store)
risk_engine = RiskEngine(cfg=cfg, gateway=sim_gateway, store=store, signal_engine=signal_engine)
bar_buffer: Dict[str, deque] = {}
position_manager = PositionManager(
    store=store, engine=sim_execution, cfg=cfg, strategy=strategy,
    bar_buffer=bar_buffer, gateway=None,
)
```

---

### `backtester/report.py` (service/aggregation, batch/transform)

**Analog (reused read pattern):** `bot/state/store.py::get_closed_trades` /
`get_daily_trade_stats` (lines 343-382). **Analog (pattern reference only, do not import):**
`bot/service/report.py` (daily HTML dashboard).

**Trade read pattern** (`bot/state/store.py` lines 343-356):
```python
def get_closed_trades(self, session_date) -> list:
    with self._lock:
        # Atomically flip row_factory -> fetch -> reset
        ...
    # returns list of dicts, one per closed trade row
```
**What to copy:** the `with self._lock:` row_factory→fetch→reset discipline if `report.py`
queries `StateStore` directly across a date range (loop `get_closed_trades` per session date,
or add a new StateStore method following the same pattern if a single multi-day query is
needed — confirm with planner whether extending StateStore is in-scope before doing so, since
CLAUDE.md/RESEARCH both stress "no modifications to live code" as a constraint; if a new
range-query method is needed, it should be additive-only and not change existing behavior).

**Metrics formulas** (RESEARCH Code Examples, no existing analog — new code, single file):
```python
def compute_metrics(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["realized_pnl"] > 0]
    losses = [t for t in trades if t["realized_pnl"] <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_r = sum(t["r_multiple"] for t in trades) / len(trades) if trades else 0.0
    gross_profit = sum(t["realized_pnl"] for t in wins)
    gross_loss = abs(sum(t["realized_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    cum_pnl, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:  # chronological (exit-time) order required
        cum_pnl += t["realized_pnl"]
        peak = max(peak, cum_pnl)
        max_dd = max(max_dd, peak - cum_pnl)
    return {"win_rate": win_rate, "avg_r_multiple": avg_r, "profit_factor": profit_factor,
            "max_drawdown_usd": max_dd, "total_trades": len(trades)}
```
CSV writer: use stdlib `csv` module (Don't Hand-Roll / Standard Stack — no third-party CSV
library justified).

---

### Test files

**`tests/backtester/test_harness.py`** — Analog: `tests/test_main_wiring.py` (full file read
above). Copy the "drive the REAL construction sequence with only I/O seams patched" pattern:
monkeypatch only `SimulatedGateway`-equivalent I/O boundaries (feed's yfinance calls),
construct the harness for real, and assert on the actual `PositionManager`/`SignalEngine`
instances it builds — do not hand-construct a parallel test-only pipeline (that pattern is
exactly what let the Phase 7 gateway-wiring bug ship, per that file's own docstring).

**All async test files** — project-wide convention (verified via grep,
`tests/position/test_manager.py`, `tests/signal/`): call `asyncio.run(coro)` directly inside a
plain `def test_*` function, NOT `@pytest.mark.asyncio` (even though `pytest-asyncio` is
installed). Match this in every `tests/backtester/*.py` file.

**`tests/backtester/test_feed.py`** — mock `yf.download` the same way scanner fetcher tests
do (search `tests/scanner/test_fetcher.py` if present for the exact mocking fixture shape;
otherwise mock at the `yfinance.download` call boundary, not inside `_download_batch`).

## Shared Patterns

### Config loading (all files that construct the pipeline)
**Source:** `bot/config/loader.py::load_strategy_config` (via `bot/main.py` line 65)
**Apply to:** `backtester/run.py`, `backtester/harness.py`
```python
try:
    cfg = load_strategy_config(args.rules_json)
except ConfigError as exc:
    print(f"[ERROR] {exc}", file=sys.stderr)
    sys.exit(1)
```

### StateStore scratch-path discipline (Pitfall 4)
**Source:** `bot/state/store.py` — `resolve_db_path()` (lines 42-51), `StateStore.__init__`
(lines 162-174)
**Apply to:** `backtester/run.py`, `backtester/harness.py`, all backtester tests
```python
store = StateStore(db_path=run_scratch_db_path).open()   # NEVER StateStore() with no db_path
```
Add a CLI-level guard in `run.py` refusing to run if the resolved path equals
`data/bot_state.db` exactly (Pitfall 4).

### Column-casing discipline (Pitfall 3)
**Source:** `bot/scanner/fetcher.py::get_ticker_frame` (lines 334-361)
**Apply to:** `backtester/feed.py` — every 5m/daily frame from `_download_batch` must pass
through `get_ticker_frame(data, symbol)` before any `BarEvent` construction or SMA/RVOL math.

### Point-in-time SMA/RVOL reuse (Don't Hand-Roll — highest-priority shared pattern)
**Source:** `bot/scanner/scanner.py::_evaluate_symbol` (lines 115-... , signature at 115-121)
**Apply to:** `backtester/feed.py` or `backtester/harness.py` (per-trading-day setup step)
```python
candidate = _evaluate_symbol(symbol, daily_data, cfg, scan_date=day, today_price=synthetic_today_price)
```
Never reimplement SMA200/RVOL/RVOL-TOD math in `backtester/` — call this function directly.

## No Analog Found

None — every file in the phase's file list has at least a role-match analog in the existing
codebase (see table above). `backtester/feed.py`'s CSV cache layer and `backtester/report.py`'s
metrics formulas are new logic with no prior implementation to copy (explicitly called out in
RESEARCH's Don't Hand-Roll / Code Examples sections as the only genuinely new code in this
phase), but they are not "no analog" in the pattern-mapping sense — they compose entirely
around reused analogs (`fetcher.py`'s batch kernel, `StateStore`'s read methods).

## Metadata

**Analog search scope:** `bot/main.py`, `bot/__main__.py`, `bot/service/bot.py`,
`bot/scanner/fetcher.py`, `bot/scanner/scanner.py`, `bot/scanner/universe.py`,
`bot/state/store.py`, `bot/service/report.py`, `bot/execution/engine.py` (surface only),
`bot/gateway/gateway.py` (surface only), `bot/signal/signal_engine.py` (call-site only),
`bot/risk/risk_engine.py` (call-site only), `tests/test_main_wiring.py`.
**Files scanned:** 13 source files directly read; full-file reads for small files
(`bot/main.py` 142 lines, `bot/__main__.py` 10 lines, `tests/test_main_wiring.py` 104 lines),
targeted non-overlapping offset/limit or grep-located reads for large files
(`bot/service/bot.py` 1030 lines, `bot/scanner/fetcher.py` 579 lines, `bot/state/store.py` 886
lines).
**Pattern extraction date:** 2026-07-06
