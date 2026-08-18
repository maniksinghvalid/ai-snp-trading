# Phase 10: External Strategy Research - Pattern Map

**Mapped:** 2026-08-18
**Files analyzed:** 15 new + 2 modified
**Analogs found:** 15/15 (existing repo has a near-exact sibling package for the whole shape: `backtester/options/*` + `backtester/options_run.py`, itself built as a "new engine that reuses `backtester/feed.py`/`report.py` without touching `bot/`" package in Phase 9)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `backtester/experimental/__init__.py` | package init | — | `backtester/options/__init__.py` (empty) | exact |
| `backtester/experimental/indicators.py` | utility (pure math) | transform | `bot/strategy/indicators.py` (`swing_low_2_2`) + `backtester/options/greeks.py` (pure-function module shape) | role-match |
| `backtester/experimental/strategies.py` | service (signal generation) | transform (vectorized, batch) | `backtester/options/data.py::build_rows` (vectorized row-builder over a chain) | role-match |
| `backtester/experimental/engine.py` | service (per-bar replay engine) | event-driven / batch | `backtester/options/engine.py::OptionsBacktestEngine` + `backtester/harness.py::BacktestHarness` | exact |
| `backtester/experimental/run.py` | CLI entrypoint / controller | request-response (CLI) | `backtester/options_run.py` (near-identical composition-root shape) | exact |
| `backtester/experimental/aggregate.py` | service (aggregation/report) | batch/transform | `backtester/report.py::compute_metrics` (metric computation over trade lists) | role-match |
| `backtester/experimental/charts.py` | utility (SVG renderer) | transform | none in-repo (new capability) — closest shape is any pure-function formatter, e.g. `backtester/report.py`'s CSV writers | partial |
| `backtester/experimental/arms.json` | config | — | `rules_options.json` (frozen config-of-record pattern) | role-match |
| `tests/backtester/experimental/__init__.py` | test scaffold | — | `tests/backtester/fixtures.py` package layout | exact |
| `tests/backtester/experimental/test_indicators.py` | test | unit | `tests/backtester/test_execution.py` (assertion style, no framework markers) | exact |
| `tests/backtester/experimental/test_strategies.py` | test | unit | `tests/backtester/test_execution.py` | exact |
| `tests/backtester/experimental/test_engine.py` | test | unit | `tests/backtester/test_execution.py` (`_FakeFeed` pattern) + `tests/backtester/fixtures.py` (`make_ahead_only_5m_dataset`) | exact |
| `tests/backtester/experimental/test_aggregate.py` | test | unit | `tests/backtester/fixtures.py::make_trade_log` (hand-computed expected metrics convention) | exact |
| `docs/research/2026-08-18-external-strategies-hypotheses.md` | docs | — | `docs/research/2026-08-17-options-backtest-hypotheses.md` | exact |
| `docs/research/2026-08-18-external-strategies-results.md` | docs | — | `docs/research/2026-08-17-options-backtest-results.md` | exact |
| `backtester/report.py` (MODIFIED: `_net_pnl`, `write_report`) | service (existing) | transform | itself (surgical patch, not a new file) | exact |
| `backtester/harness.py` (MODIFIED: optional `day_gate` seam) | service (existing) | event-driven | itself (surgical patch, not a new file) | exact |

## Pattern Assignments

### `backtester/experimental/indicators.py` (utility, transform)

**Analog:** `bot/strategy/indicators.py` (docstring/section-header convention) — do NOT import it for exits (see Shared Patterns); DO import `swing_low_2_2` directly per CONTEXT.md.

**Module docstring convention** (`bot/strategy/indicators.py` top, mirrored from `backtester/feed.py`/`report.py` style):
```python
#!/usr/bin/env python3
"""
backtester.experimental.indicators — pure pandas/numpy indicator functions for the
external-strategy research package (ext2/orb/vwap_pb signal inputs + SPY weekly regime).

No bot/ imports except bot.strategy.indicators.swing_low_2_2 (reused, not reimplemented,
per 10-CONTEXT.md). Never look-ahead: every function operates on rows <= t only.

Exports: sma, ema, macd, atr, vwap, opening_range, htf_ema_bias, weekly_regime
"""
```

**Section-header convention** (`bot/strategy/indicators.py:95-97`):
```python
# ============================================================
# swing_low_2_2 — 5-minute Swing Low (2-bar-left / 2-bar-right)
# ============================================================
```

**Reused function signature (import, do not reimplement)**:
```python
# Source: bot/strategy/indicators.py:100 (verified)
def swing_low_2_2(lows: Union[List[float], "pd.Series"]) -> Optional[float]:
    ...
# mirror per CONTEXT.md: swing_high_2_2 = -swing_low_2_2(-highs)
```

---

### `backtester/experimental/strategies.py` (service, transform/batch)

**Analog:** `backtester/options/data.py::build_rows` (vectorized construction over a batch input, returns list of dict rows) — same shape as "vectorized signal functions consume indicator series, return row-shaped signals."

**Core pattern** (`backtester/options/data.py:105`):
```python
def build_rows(chain_rows, underlying_px, day, r, spread_pct, oi_source, min_open_interest):
    """Vectorized row-builder: pure function, no I/O, returns list[dict]."""
```
Apply the same shape: `ext2_signals(bars_df, ...) -> list[dict]` / `orb_signals(...)` / `vwap_pb_signals(...)`, each a pure function over an already-loaded bar frame, never reaching into `SimulatedBarFeed` itself (keeps strategies.py independently unit-testable without a feed).

---

### `backtester/experimental/engine.py` (service, event-driven/batch)

**Analog:** `backtester/options/engine.py::OptionsBacktestEngine` (class shape) + `backtester/harness.py:380-416` (exact trade-row-on-close convention to reproduce with sign support).

**Imports pattern** (`backtester/options_run.py:26-31`, the sibling composition root):
```python
from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.options.data import OptionChainSource, trading_days
from backtester.options.engine import OptionsBacktestEngine
from backtester.options.report import write_options_report
```
For `experimental/engine.py` itself: `from backtester.feed import SimulatedBarFeed` (source="massive" per Pattern 1), `from bot.strategy.indicators import swing_low_2_2`, `from bot.position.manager import get_force_close_time_et`.

**Class/method shape** (`backtester/options/engine.py:145-237`):
```python
class OptionsBacktestEngine:
    def __init__(self, cfg, chains: dict, r: float = 0.045, slippage_usd: float = 0.02, ...):
        ...
    def run(self, days: list) -> None:
        ...
    def run_day(self, day: str) -> None:
        ...
    def _manage_day(self, day: str) -> None:
        ...
    def _check_daily_breaker(self, day: str) -> None:
        ...
    def _entry_scan(self, day: str, today, change_pct: dict) -> None:
        ...
```
Mirror this shape for `Engine`: `__init__(cfg, feed, arms, ...)`, `run(days)`, `run_day(day)`, per-bar loop inside `run_day` (force-close check → position mgmt → entry eval → fill @ next_bar open per CONTEXT.md engine semantics).

**Trade-row-on-close convention to reproduce with SIGN support** (`backtester/harness.py:400-413`, verified — long-only reference):
```python
exit_price = (
    pos.exit_notional / pos.exit_filled_qty if pos.exit_filled_qty > 0
    else pos.entry_price
)
risk = pos.entry_price - pos.initial_stop
r_multiple = (exit_price - pos.entry_price) / risk if risk != 0 else 0.0
self.trade_log.append({
    "code": pos.code, "entry_price": pos.entry_price, "exit_price": exit_price,
    "quantity": pos.full_quantity, "exit_reason": pos.pending_exit_reason,
    "r_multiple": r_multiple, "opened_at": pos.opened_at, "closed_at": pos.updated_at,
})
```
Short-supporting variant (per CONTEXT.md/RESEARCH.md Pitfall 3 — never reuse the long-only-implicit formula as-is):
```python
risk = abs(pos.entry_price - pos.initial_stop)
sign = -1 if pos.side == "short" else 1
r_multiple = sign * (exit_price - pos.entry_price) / risk if risk != 0 else 0.0
```
Plus extra columns per CONTEXT.md: `side, strategy, arm, n_legs, regime`.

**N+1-open fill / slippage-sign convention** (`backtester/execution.py:57-77`, verified):
```python
next_bar = self._feed.next_bar(intent.code, after=intent.source_signal.bar.time_key)
if next_bar is None:
    return None  # no next bar -- unfilled
fill = FillEvent(..., avg_fill_price=next_bar["open"] + self._slippage)  # long entry slips UP
```
For shorts: entry slips DOWN, exit slips UP (mirror image) — per CONTEXT.md `"long: entry+slip/exit−slip; short: entry−slip/exit+slip"`.

**Sizing formula to mirror (parity re-implementation, not import)** (`bot/risk/risk_engine.py:105-141`, verified):
```python
risk_dollars = equity * cfg.max_risk_per_trade_pct / 100.0   # 1% of $100k = $1000
risk_qty = math.floor(risk_dollars / stop_distance)
notional_cap = equity * cfg.max_position_size_pct / 100.0     # 10% of $100k = $10000
notional_cap_qty = math.floor(notional_cap / entry_price)
qty = min(risk_qty, notional_cap_qty)
if qty < 1:
    return None  # skip, D-07 round-down convention
```

**Force-close time to reuse (import, not reimplement)** (`bot/position/manager.py:63-88`, verified):
```python
def get_force_close_time_et(today: date) -> time:
    close_hhmm = get_market_close_et(today)  # "16:00" normal, "13:00" half-day
    # force_close = close - 9 minutes -> 15:51 normal, 12:51 half-day
```

---

### `backtester/experimental/run.py` (CLI entrypoint)

**Analog:** `backtester/options_run.py` (near-identical sibling composition root — same "new backtest package that imports bot/ pure functions without copying them, reuses backtester/massive.py + backtester/report-style writer" pattern from Phase 9).

**Module docstring + imports pattern** (`backtester/options_run.py:1-38`, verified):
```python
#!/usr/bin/env python3
"""
backtester.experimental.run — CLI entry point for the external-strategy research backtester.

Composition root only: never constructs a live broker gateway, the execution/order layer,
or a StateStore anywhere in this module (mirrors backtester/run.py's and
backtester/options_run.py's stated invariant). Cache-only: asserts every required
{sym}_5m_{start}_{end}.csv / {sym}_1d_{start}_{end}.csv exists before running and refuses
to proceed without --allow-fetch (never passed in this phase's run matrix, 10-RESEARCH
Pitfall 1).

Exports: build_arg_parser, main
"""
import argparse
import json
import os
import sys
import uuid

from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.experimental.engine import Engine
from backtester.experimental.strategies import ext2_signals, orb_signals, vwap_pb_signals
from backtester.report import write_report
```

**Argparse convention** (`backtester/options_run.py:41-55`, verified — dash-separated long options, CLAUDE.md convention):
```python
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backtester.experimental.run", description="...")
    parser.add_argument("--symbols", required=True, help="Comma-separated moomoo codes")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="Run directory")
    parser.add_argument("--set", dest="set_args", action="append", default=[], metavar="KEY=VALUE")
    return parser
```
Add `--window {A,B,C,D,E}` resolving from a single-source-of-truth `WINDOWS` dict (per RESEARCH.md Pitfall 1 — never hand-construct padded cache filenames), `--arms arms.json`, `--cost {base,stress,zero}`.

---

### `backtester/experimental/aggregate.py` (service, batch)

**Analog:** `backtester/report.py::compute_metrics` (metric computation shape) — reuse the function directly per-slice rather than reimplementing PF/Sharpe/Sortino.

**Core pattern** (`backtester/report.py:197-292`, verified signature):
```python
def compute_metrics(trades: list, starting_capital: float = 100_000.0,
                     commission_per_share: float = 0.0, start=None, end=None,
                     extra_assumptions: dict = None) -> dict:
    ...
    return {"win_rate": ..., "avg_r_multiple": ..., "profit_factor": ..., ...,
            "per_symbol": per_symbol, "assumptions": assumptions}
```
`aggregate.py` calls this once per (window × regime-slice × arm) trade-list slice, plus adds bootstrap CI / walk-forward / per-side split as NEW functions (not inside `report.py` — report.py stays a generic metrics/writer module per CONTEXT.md's "exactly one small change" boundary).

**tz-naive slicing convention to reuse** (`backtester/report.py:47-55`, verified):
```python
def _to_dt(value) -> datetime:
    """Accept a datetime or a 'YYYY-MM-DD HH:MM:SS[...]' string (trade-log convention)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")

def _day_str(value) -> str:
    return str(value)[:10]
```
Per RESEARCH.md Pitfall 5: `aggregate.py`'s own day-bucket slicing must use this exact `[:10]`/`[:19]` convention, never a full tz-aware `datetime.fromisoformat`.

---

### `docs/research/2026-08-18-external-strategies-hypotheses.md` (docs)

**Analog:** `docs/research/2026-08-17-options-backtest-hypotheses.md` (template to match in shape/rigor — verified header structure).

**Header/pre-registration-statement convention** (`docs/research/2026-08-17-options-backtest-hypotheses.md:1-13`, verified):
```markdown
# [Strategy] backtest hypotheses (Phase [N], pre-registration)

**Pre-registration statement**

Dated [date], before any real-data backtest run of `[entrypoint]` has occurred. Commit
discipline proves it: this file is committed in its own commit, ahead of Plan [X] (engine)
and Plan [Y] (first real run) — check `git log --diff-filter=A --format='%h %ad %s'
--date=short -- '[this file path]'` and confirm no commit under `[results dir]` predates it.
```
Section order to reproduce (verified from the file): `## Data window` → `## IS / OOS windows` → Hypotheses → `## Metric of record` → `## Evidence floor` → `## Verdict rules` → `## Known limitations` → `## What would change [config]`.

---

## Shared Patterns

### Module docstring convention (all new `.py` files)
**Source:** `backtester/feed.py:1-36`, `backtester/report.py:1-24`, `backtester/options_run.py:1-24` (all verified)
**Apply to:** every file in `backtester/experimental/`
```python
#!/usr/bin/env python3
"""
backtester.experimental.<module> — <one-line role>.

<3-6 lines: what it does, what it never does (no bot/ writes, no broker gateway,
no network fetch), what look-ahead/parity guarantee it upholds and why>.

Exports: <comma-separated public names>
"""
```

### "Never construct a live broker gateway" invariant statement
**Source:** `backtester/run.py:15-17`, `backtester/options_run.py:12-14` (verified, stated identically in both)
**Apply to:** `engine.py`, `run.py` docstrings — state explicitly that the experimental engine is provably broker-free, mirroring both existing composition roots.

### Cache-only / no-fetch guard
**Source:** RESEARCH.md Pattern 1, `backtester/massive.py:161-179::cached_bars`
**Apply to:** `run.py` — assert every `{sym}_5m_{start}_{end}.csv`/`{sym}_1d_{start}_{end}.csv` file exists before constructing `SimulatedBarFeed(source="massive", ...)`; refuse without `--allow-fetch`.

### `report.py` extension via `extra_fields`, never mutate `_CSV_FIELDS`
**Source:** `backtester/report.py:296-326` (verified current signature, no `extra_fields` param yet)
**Apply to:** the one approved `report.py` edit
```python
# Current (verified, backtester/report.py:296-298):
_CSV_FIELDS = ["code", "opened_at", "entry_price", "exit_price", "quantity",
               "exit_reason", "r_multiple", "closed_at"]

def write_report(trades: list, output_dir: str, starting_capital: float = 100_000.0,
                 commission_per_share: float = 0.0, start=None, end=None,
                 extra_assumptions: dict = None) -> dict:
    ...
    with open(os.path.join(output_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        ...

# Patch: add extra_fields param, build fieldnames LOCALLY (never mutate _CSV_FIELDS):
def write_report(trades, output_dir, starting_capital=100_000.0, commission_per_share=0.0,
                 start=None, end=None, extra_assumptions=None, extra_fields: list = None):
    ...
    fieldnames = list(_CSV_FIELDS) + list(extra_fields or [])
    writer = csv.DictWriter(f, fieldnames=fieldnames)
```
And `_net_pnl` (`backtester/report.py:41-45`, verified current — long-only-implicit):
```python
def _net_pnl(trade: dict, commission_per_share: float) -> float:
    gross = (trade["exit_price"] - trade["entry_price"]) * trade["quantity"]
    return gross - commission_per_share * trade["quantity"] * 2
# Patch: sign = -1 if trade.get("side") == "short" else 1; gross = sign * (exit-entry) * qty
```

### Test style: plain `def test_*`, `asyncio.run()` inline, no pytest markers
**Source:** `tests/backtester/test_execution.py:1-16` (verified — project-wide convention, no `@pytest.mark.asyncio`)
**Apply to:** all 4 new test files under `tests/backtester/experimental/`

### `_FakeFeed` double pattern (hand-rolled, not the real feed)
**Source:** `tests/backtester/test_execution.py:32-44` (verified)
```python
class _FakeFeed:
    """Minimal next_bar(code, after) double... deliberately hand-rolled (not
    backtester.feed.SimulatedBarFeed) so this test file depends only on fixtures.py."""
    def __init__(self, bars):
        self._bars = bars
    def next_bar(self, code, after):
        candidates = [b for b in self._bars if b["code"] == code and b["time_key"] > after]
        return min(candidates, key=lambda b: b["time_key"]) if candidates else None
```
**Apply to:** `test_engine.py` — reuse this exact pattern for engine fill tests instead of a real `SimulatedBarFeed`.

### Fixture reuse, never reinvent
**Source:** `tests/backtester/fixtures.py:1-24` (verified docstring — pure functions, no network, no `backtester.*` import)
**Apply to:** all 4 new test files — import `make_ahead_only_5m_dataset`, `make_trade_log`, `recent_session_days` directly; do not create parallel fixture helpers.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `backtester/experimental/charts.py` | utility | transform | No SVG/rendering code exists anywhere in this repo yet — genuinely new capability. Follow RESEARCH.md's own spec (hand-rolled `<polyline>` SVG, no matplotlib) and the module-docstring/`Exports:` convention above; no in-repo analog to copy structure from beyond generic pure-function style. |
| `backtester/experimental/arms.json` (content shape) | config | — | No prior "frozen arm list" JSON exists in this repo (closest is `rules_options.json`'s schema-validated shape, but arms.json is a list of strategy-parameter dicts, not a single strategy config) — follow the design doc's own arm schema (CONTEXT.md §Strategy definitions), not an in-repo JSON analog. |

## Metadata

**Analog search scope:** `backtester/`, `backtester/options/`, `bot/strategy/`, `bot/position/`, `bot/risk/`, `tests/backtester/`, `docs/research/`
**Files scanned:** `backtester/report.py`, `backtester/feed.py`, `backtester/harness.py`, `backtester/execution.py`, `backtester/massive.py`, `backtester/run.py`, `backtester/options/{data,engine,greeks,report}.py`, `backtester/options_run.py`, `tests/backtester/fixtures.py`, `tests/backtester/test_execution.py`, `bot/strategy/indicators.py`, `bot/position/manager.py`, `bot/risk/risk_engine.py`, `docs/research/2026-08-17-options-backtest-hypotheses.md`
**Pattern extraction date:** 2026-08-18
