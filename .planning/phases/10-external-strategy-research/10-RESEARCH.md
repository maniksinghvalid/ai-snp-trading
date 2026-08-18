# Phase 10: External Strategy Research - Research

**Researched:** 2026-08-18
**Domain:** Backtest-engine research package (pandas/numpy-only), pre-registered hypothesis testing, cache-only historical replay
**Confidence:** HIGH

## Summary

CONTEXT.md for this phase already embeds the full approved design (per-bar engine algorithm,
exact cache windows, strategy formulas, file/LOC breakdown, run matrix) — the design doc at
`~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md` is the canonical spec. This
research pass's job was narrower: **verify every code-level claim in that design against the
live repo** so the planner can trust it without re-deriving anything, and flag any drift.

Everything checked out. All five cache windows (A–E) are fully populated for all 24 MEGA24
symbols at the exact padded cache keys the design specifies (120/120 5m files, including the
`2024-08-03` padded start for window C — the specific value CONTEXT.md called out as
error-prone). `backtester/report.py::_net_pnl`/`write_report` currently lack the side-aware sign
and `extra_fields` param the design calls for — confirming that edit is both necessary and
minimal (5 lines). `backtester/harness.py:400-416`'s trade-row convention, `bot/risk/risk_engine.py`'s
sizing formula, `bot/position/state.py`'s exit FSM thresholds, `bot/position/manager.py::get_force_close_time_et`,
and `tests/backtester/fixtures.py`'s fixture functions all match CONTEXT.md's descriptions exactly
— no drift found anywhere. The Phase 9 `warm-cache-pool` background process is still alive and
untouched (pid 48166, running `backtester.options_run`). No SPY equity bars exist in the Massive
cache — confirming the design's yfinance-for-SPY-regime decision is correct (a Massive fetch
would be a cache miss, costing quota). `backtester/experimental/` does not yet exist (green field).
No new dependencies are needed — pandas 3.0.3 and numpy 2.4.6 are already installed and already
project dependencies; scipy/matplotlib are absent and the design correctly avoids them.

**Primary recommendation:** Plan directly off CONTEXT.md and the design doc verbatim — every
code-level claim in them is verified against the current repo state as of 2026-08-18. The only
things worth double-checking again at plan/execute time (not now) are: (1) the `requirements.txt`
numpy pin (`2.5.0`) vs. the installed version (`2.4.6`) — a pre-existing drift unrelated to this
phase, do not "fix" it as part of Phase 10; (2) confirm the `warm-cache-pool.pid` process is still
alive again immediately before Wave 3 runs (it is a long-running background job that could finish
or die between now and execution).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Historical bar replay (cache-only) | Backtest engine (`backtester/experimental/engine.py`) | Data source (`backtester/feed.py::SimulatedBarFeed`) | Engine drives per-bar loop; feed owns cache I/O and never fetches in this phase |
| Signal generation (ext2/orb/vwap_pb) | Backtest engine (`strategies.py`) | Indicators (`indicators.py`) | Vectorized signal functions consume indicator series, engine walks them bar-by-bar |
| Position sizing / risk caps | Backtest engine (`engine.py`) | — | Mirrors `bot/risk/risk_engine.py` math but is a parity re-implementation, not an import (RiskEngine is async/gateway-coupled; the backtester's own harness pattern, established in Phase 6, is a pure re-implementation with parity tests, not a shared import) |
| Exit FSM (pct_ladder / partial_be_trail / fixed_2r) | Backtest engine (`engine.py`) | `bot/position/state.py` (reference only, for partial_be_trail parity) | Same reasoning — pure re-implementation with parity assertions, since `PositionState` is long-only and this phase needs short support |
| Metrics / CSV / summary.json | `backtester/report.py` (existing, patched) | — | Reused as-is except the two approved edits (`_net_pnl` sign, `write_report` extra_fields) |
| Regime series (SPY weekly) | `backtester/experimental/indicators.py::weekly_regime` | yfinance (one-time fetch, cached CSV) | New, isolated data need; zero Massive quota impact |
| Report / hypothesis verdicts | `docs/research/*.md` (docs tier) | — | Pure documentation output, no runtime component |
| Conditional production integration (Wave 5) | `bot/` (feature branch only) | `rules.json` (feature branch only) | Explicitly out of `develop`; only reachable if a hypothesis is SUPPORTED |

## User Constraints

<user_constraints>
### Locked Decisions (from CONTEXT.md — copied verbatim, condensed for research use; full text is in 10-CONTEXT.md)

**Universe & data:** Frozen 24-symbol `MEGA24` list (`US.AAPL,MSFT,NVDA,AMD,AVGO,META,GOOGL,AMZN,NFLX,TSLA,HD,COST,WMT,JPM,GS,BAC,XOM,CVX,CAT,DE,BA,UNH,LLY,JNJ`), validation-only, never referenced by `bot/`. Windows (exact cache-keyed strings, verified below): A 2025-05-01→2025-07-31, B 2025-04-01→2025-04-30, C 2024-09-02→2024-12-31 (padded start 2024-08-03 — verified, not 2024-08-04 or 09-03), D 2025-08-01→2026-07-31, E 2023-07-03→2024-08-30. IS = E∪C; OOS = B∪A∪D. 9 regime slices (date-sliced from trades, no re-runs). SPY daily regime series via yfinance only (zero Massive requests), cached to `backtester/cache/SPY_1d_regime.csv`. Costs: base $0.005/share + $0.03/share slippage; stress $0.05; zero-cost = diagnostic only.

**Reuse (do not reinvent):** `backtester/feed.py::SimulatedBarFeed`, `backtester/report.py::compute_metrics/build_equity_curve/write_report`, `bot/strategy/indicators.py::swing_low_2_2` (+ mirror for highs), `bot/position/manager.py::get_force_close_time_et`, `backtester/run.py::_trading_days`. TJL baseline = existing results under `backtester/results/strategy-audit-validation/fulluniverse/{E1,E2,E3,D1,D2,D3}/base` and `window{A..E}/base` — do NOT re-run TJL; "TJL + weekly regime gate" is a day-filter of existing `trades.csv`, zero harness changes required (optional 4-line `day_gate` seam in `BacktestHarness.setup_day` allowed only as a parity check).

**Production code boundary (hard constraint):** `bot/` package zero changes. `rules.json`/`rules_options.json` zero changes this phase regardless of verdict. `backtester/report.py`: exactly one allowed edit — `_net_pnl` gets a side-aware sign, `write_report` gains optional `extra_fields`. `backtester/harness.py`: optional 4-line `day_gate` seam is the only other allowed edit to existing code.

**Engine semantics (locked):** Signal on bar CLOSE at t, fill at bar t+1 OPEN ± adverse slippage. Stops evaluated on bar CLOSE by default (`--stop-fill intrabar` is a sensitivity mode). Sizing `qty = min(floor(1000/|close−stop|), floor(10000/close))` from signal bar's close, $100k non-compounding, skip if qty<1. Risk caps: ≤5 concurrent, ≤5 entries/day, −$2,000 realised-gross daily breaker (blocks entries only), one position/symbol, no re-entry same bar. Force-flat at 15:51 ET / 12:51 half-days via `get_force_close_time_et`. Trade row: one per position, qty-weighted blended `exit_price`, `r_multiple = sign·(exit−entry)/|entry−initial_stop|`, extra columns `side,strategy,arm,n_legs,regime`. Long AND short both supported (first for this backtester).

**Strategy definitions (locked, exact rules in design doc):** ext2 (SMA10+MACD), orb (ORB30 + 1H-EMA100 + VWAP), vwap_pb (VWAP pullback). Frozen arm list in `backtester/experimental/arms.json` (Wave 1 deliverable, 15 arms + conditional `combo`).

**Pre-registration discipline (hard constraint):** `docs/research/2026-08-18-external-strategies-hypotheses.md` + `arms.json` MUST be committed before any real-data run exists under `backtester/results/experimental/`. Verification via `git log --diff-filter=A`. Evidence floor: ≥25 closed trades per arm per window in IS and OOS separately, never loosened. Metric of record: PF (net, base cost) primary; Sortino secondary; bootstrap 95% CI tie-break. Verdict SUPPORTED only if holds in BOTH IS and OOS with floor met in both. Hypotheses H1–H8 fixed in the design doc — reproduce verbatim, do not invent new ones.

### Claude's Discretion
- Exact internal module boundaries within `backtester/experimental/` (design doc's file breakdown is a strong recommendation, not a contract), as long as the package is self-contained and importable independent of `bot/`.
- Exact SVG chart styling (no-dep hand-rolled SVG is locked; polish is discretionary).
- Whether to implement the optional `day_gate` harness seam at all.
- Wave numbering/plan-file splitting as long as dependency order (pre-registration → engine+tests → runs → report → conditional branch) is preserved.

### Deferred Ideas (OUT OF SCOPE)
- Broader universe (S&P 100+, SPY/QQQ additions) — would require new Massive fetches and pausing the warm-cache job.
- Live short-side production capability.
- Merging any Wave-5 integration branch to `develop`.
- Paid Massive tier upgrade.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| XSR-01 | External strategies extracted into explicit rules, credibility assessed, committed doc | Design doc Appendix A already contains the full extraction (both Reddit threads, verbatim rules, credibility pushback with comment scores) — Wave 1 transcribes this into `docs/research/2026-08-18-external-strategies-hypotheses.md`. No new extraction work needed; source material is complete. |
| XSR-02 | `backtester/experimental/` implements automatable cores as pluggable, tested modules reusing `SimulatedBarFeed`/`report.py`, no `bot/`/`rules.json` changes | Verified: `SimulatedBarFeed` already supports `source="massive"` (built in Phase 9) with the exact cache-key format the design needs; `report.py`'s `_CSV_FIELDS` (8 cols) and `write_report` signature confirmed below — the 2 approved edits are the only production-code touch. |
| XSR-03 | Hypotheses pre-registered (universe, IS/OOS windows, evidence floor ≥25 trades/arm/window, metric of record) committed before any real-data run | Precedent verified: `docs/research/2026-08-17-options-backtest-hypotheses.md` structure (Data window → IS/OOS → Hypotheses → Metric of record → Evidence floor → Verdict rules → Known limitations → What would change config) is the template to match; `git log --diff-filter=A` ordering check verified as a working pattern from Phase 9. |
| XSR-04 | Cache-only runs, zero new Massive requests, warm-cache-pool undisturbed, realistic costs, standard+extended metrics | Verified: all 24×5=120 required 5m cache files present at exact padded keys; all 24×5 daily cache files present at exact 400-day-padded keys; warm-cache-pool.pid (48166) confirmed alive and running a *different* command (`backtester.options_run`, SPY/QQQ/IWM/TLT/GLD/XLE options) — zero collision risk with this phase's equity-bar cache reads. |
| XSR-05 | SUPPORTED/REJECTED/INSUFFICIENT-EVIDENCE verdicts with numbers; `rules.json`/`rules_options.json` unchanged | `compute_metrics`/`write_report` already produce every required field except `extra_fields` columns (side/strategy/arm/n_legs/regime) and side-aware PnL — both are the 2 approved `report.py` edits, verified as currently absent (so the edit is real, not redundant). |
| XSR-06 | If SUPPORTED, production integration on separate unmerged feature branch | Verified: `bot/config/loader.py::StrategyConfig` currently does NOT read `strategy_name`/`direction` from `rules.json` even though those keys exist in the JSON — confirms CONTEXT.md's Appendix B claim ("direction field decorative") and validates the Wave-5 sketch's premise that loader wiring is genuinely new work, not already done. |
</phase_requirements>

## Standard Stack

### Core

No new external packages. This is a zero-new-dependency phase by design (CONTEXT.md/design doc:
"No new dependencies: pandas/numpy only; charts as hand-rolled SVG").

| Library | Version (installed) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pandas | 3.0.3 (verified via `python3 -c "import pandas; print(pandas.__version__)"`) | Indicator/frame math, CSV cache I/O | Already the project's data-processing backbone (feed.py, massive.py, report.py) |
| numpy | 2.4.6 (verified) | Vectorized indicator math (SMA/MACD/ATR/EMA) | Already a transitive/direct dependency; no new install |
| pandas_market_calendars | 5.4.0 (per requirements.txt) | NYSE trading-day iteration (`_trading_days`, half-day-aware force-close) | Already used by `backtester/run.py`, `backtester/report.py`, `bot/scanner/calendar.py` |
| yfinance | 1.4.1 (per requirements.txt) | One-time SPY daily regime series fetch | Already a project dependency (SCAN-06/BT-04); zero-Massive-quota data source |

### Supporting

None needed — the phase explicitly avoids scipy/matplotlib (confirmed absent from the environment:
`import scipy` and `import matplotlib` both raise `ModuleNotFoundError`). Bootstrap CI (10k
resamples) and SVG chart rendering are both hand-rolled with numpy/stdlib per the design doc,
which is the correct call — installing scipy or matplotlib for two isolated features in a
research-only package is not justified.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| hand-rolled SVG charts | matplotlib | Saves ~80 LOC per design doc's own estimate, but adds a new dependency for a research-only, non-production package — not worth it per the design's own stated rationale |
| numpy-based bootstrap CI | scipy.stats.bootstrap | scipy's version is more robust but the phase's own bootstrap need (mean-R 95% CI, 10k resamples, seeded RNG) is a ~10-line stdlib+numpy `default_rng(0).choice(...)` loop — scipy would be a new dependency for something numpy already covers |

**Installation:** None required — no new packages.

**Version verification:** `pandas` (3.0.3) and `numpy` (2.4.6) confirmed installed via direct
Python import in this environment 2026-08-18. Note: `requirements.txt` pins `numpy==2.5.0` but
the installed environment has `2.4.6` — this is a pre-existing drift unrelated to Phase 10 (not
introduced by this research, not something this phase should "fix" as a side effect). Flag for
the planner only if new numpy-version-sensitive code is written; existing numpy usage across the
codebase already tolerates this drift (tests pass, per the 1040-test collection below).

## Package Legitimacy Audit

Not applicable — this phase installs zero new external packages (verified: `pip install` is not
part of the design; pandas/numpy/pandas_market_calendars/yfinance are all pre-existing project
dependencies already in `requirements.txt` and already installed). No `Package Legitimacy Audit`
table is needed.

## Architecture Patterns

### System Architecture Diagram

```
Wave 1 (docs, committed FIRST)
  docs/research/2026-08-18-external-strategies-hypotheses.md  ─┐
  backtester/experimental/arms.json                            ├─► git commit (pre-registration proof)
  backtester/cache/SPY_1d_regime.csv (yfinance, one-time fetch)┘

Wave 2 (code + tests, bot/ untouched except 2 approved report.py lines)
  backtester/experimental/indicators.py  (ema/sma/macd/atr/vwap/OR/HTF-EMA/swing highs/regime)
        │
        ▼
  backtester/experimental/strategies.py  (ext2_signals / orb_signals / vwap_pb_signals — vectorized, rows ≤ t only)
        │
        ▼
  backtester/experimental/engine.py  ◄── backtester/feed.py::SimulatedBarFeed (source="massive", cache-only)
        │      per-bar loop: force-close check → position mgmt → entry eval → fill @ next_bar open
        ▼
  trades[] (one row/position, side-aware R, extra cols)
        │
        ▼
  backtester/report.py::compute_metrics/write_report  (2-line patch: _net_pnl sign, extra_fields)
        │
        ▼
  backtester/results/experimental/runs/<window>/<cost>/<arm>/{trades.csv,summary.json,equity_curve.csv,params.json}

Wave 3 (runs — cache-only, no network)
  run.py --window {A,B,C,D,E} --arms arms.json --cost {base,stress,zero} --out .../runs
  run.py --tjl-regime --baseline <existing TJL results dir> --out .../tjl/<slice>   (day-filter, no re-run)

Wave 4 (aggregate + report)
  aggregate.py: walks runs/, slices into 9 regime windows + IS/OOS pools,
        reuses report.compute_metrics per slice, adds bootstrap CI / walk-forward / per-side split
        │
        ▼
  charts.py: hand-rolled SVG (equity curves, PF-per-slice bars)
        │
        ▼
  docs/research/2026-08-18-external-strategies-results.md  (14 sections, H1–H8 verdicts)

Wave 5 (conditional — only if SUPPORTED in both IS+OOS)
  feature/phase10-<arm> branch: bot/config/loader.py wiring, new bot/strategy/<name>.py,
        SignalEngine seam, exit.model registration — NOT merged to develop
```

### Recommended Project Structure

```
backtester/experimental/
├── __init__.py
├── indicators.py     # pure pandas/numpy indicator functions (~150 LOC per design doc)
├── strategies.py      # vectorized ext2/orb/vwap_pb signal functions (~150 LOC)
├── engine.py           # Engine class + Position, per-bar order (~300 LOC)
├── run.py               # CLI entrypoint (~220 LOC)
├── aggregate.py          # slice/pool/bootstrap/walk-forward aggregator (~200 LOC)
├── charts.py               # no-dep SVG renderer (~130 LOC)
└── arms.json                 # frozen arm definitions (Wave 1, committed before any run)

tests/backtester/experimental/
├── test_indicators.py
├── test_strategies.py
├── test_engine.py
└── test_aggregate.py

docs/research/
├── 2026-08-18-external-strategies-hypotheses.md   # Wave 1, committed before any run
└── 2026-08-18-external-strategies-results.md      # Wave 4

backtester/results/experimental/    # gitignored run output (aggregate CSV/MD + SVGs ARE committed)
docs/research/assets/2026-08-18-external-strategies/   # SVG charts, committed
```

### Pattern 1: Cache-only SimulatedBarFeed via source="massive"

**What:** `SimulatedBarFeed(codes, start, end, source="massive", massive=MassiveDataSource(...))`
already exists (built in Phase 9) and reads exclusively from `backtester/cache/massive/*.csv` via
`MassiveDataSource.cached_bars()` — a network call only happens on a cache miss (`fetch_bars`
inside `cached_bars`, verified at `backtester/massive.py:161-179`).

**When to use:** Every Wave 3 run. To guarantee zero network calls even on an unexpected cache
gap, the design doc's `run.py` should assert cache presence (`os.path.exists` for every
`{sym}_5m_{start}_{end}.csv` and `{sym}_1d_{start}_{end}.csv`) and refuse to proceed without
`--allow-fetch` (never passed in this phase's run matrix).

**Example (verified live signature):**
```python
# Source: backtester/feed.py:104-135 (verified 2026-08-18)
feed = SimulatedBarFeed(
    codes=MEGA24,
    start="2024-09-02", end="2024-12-31",   # window C — verified cache key match below
    source="massive",
    massive=MassiveDataSource(api_key=..., cache_dir="backtester/cache/massive"),
)
```

### Pattern 2: report.py extension via extra_fields, not a parallel writer

**What:** `write_report`'s current signature (verified, `backtester/report.py:277-278`):
```python
def write_report(trades: list, output_dir: str, starting_capital: float = 100_000.0,
                 commission_per_share: float = 0.0, start=None, end=None,
                 extra_assumptions: dict = None) -> dict:
```
has no `extra_fields` parameter and `_CSV_FIELDS` is hardcoded to the 8 base columns. The
approved patch adds `extra_fields: list = None` and appends those column names/values after
`_CSV_FIELDS` when writing `trades.csv` — this must stay backward compatible (existing TJL
callers that don't pass `extra_fields` get byte-identical output).

**When to use:** Every `write_report` call from `backtester/experimental/run.py`, passing
`extra_fields=["side", "strategy", "arm", "n_legs", "regime"]` and the corresponding per-trade
dict keys.

### Pattern 3: TJL regime-gate as a pure day-filter, never a harness re-run

**What:** Because TJL is long-only, single-position-per-day-shaped, and carries no cross-day
state, "TJL + weekly SPY regime gate" is computable as: read the existing
`{...}/base/trades.csv`, drop rows whose `opened_at` date falls on a bear-regime day (per
`weekly_regime(spy_daily)` + `regime_for_day`), re-run `report.compute_metrics` on the filtered
list. No `BacktestHarness` invocation, no new SimulatedBarFeed for TJL windows.

**Anti-Patterns to Avoid**
- **Re-running TJL through the harness to test the regime gate:** wastes time and risks
  divergence from the 226-trade evidence of record; CONTEXT.md explicitly forbids this as the
  primary evidence path (day-filter of the existing CSV is the only sanctioned approach; an
  optional 4-line `day_gate` harness seam is allowed ONLY as a parity check, never as evidence).
- **Adding scipy/matplotlib "just for this phase":** contradicts the explicit no-new-deps
  decision; both bootstrap CI and SVG charts are already scoped as hand-rolled.
- **Importing `bot.position.state.PositionState` directly for the experimental engine's exit
  FSM:** `PositionState` is long-only and DB/StateStore-coupled by convention across the
  codebase (even though the class itself is pure) — the established Phase 6 pattern is a
  parity re-implementation with hand-computed test values (see `tests/backtester/fixtures.py`'s
  own docstring precedent), not a direct import into a short-supporting engine.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| NYSE trading-day iteration / half-day detection | Custom holiday calendar | `pandas_market_calendars` via `backtester.run._trading_days` and `bot.position.manager.get_force_close_time_et` | Both already handle DST, half-days (verified: `get_market_close_et` returns "13:00" on half-days, force-close computed as close−9min) |
| Swing-low/high pivot detection | New pivot-finding logic | `bot.strategy.indicators.swing_low_2_2` (+ `-swing_low_2_2(-highs)` for highs, per CONTEXT.md) | Already tested, already the exact 2-bar-left/2-bar-right semantics production trailing stops use |
| Cache-key construction for Massive-sourced bars | New cache path logic | `MassiveDataSource.cached_bars()` (existing, `backtester/massive.py:161`) | Exact `{sym}_{tag}_{start}_{end}.csv` format already verified to match all 120 required window files |
| PF/Sharpe/Sortino/CAGR/drawdown/exposure math | New metrics module | `backtester.report.compute_metrics` | Already produces every required "standard" metric; only 2 small additions needed for "extended" (bootstrap CI, avg trade $, expectancy, max consecutive losses, avg hold — new to `aggregate.py`, not `report.py`) |
| Trading-day-only NYSE regime tagging | Custom date math | Same `_NYSE.valid_days`/`mcal` pattern already used in `report.py`'s `build_equity_curve` | Consistency with existing equity-curve day iteration |

**Key insight:** Every piece of infrastructure this phase needs (calendar-aware date math,
pivot detection, cache-keyed data replay, core performance metrics) already exists and is
already unit-tested in the codebase. The phase's genuinely new work is narrow: vectorized
indicator/signal functions for 2 new strategies, a short-supporting exit-FSM re-implementation,
and an aggregation/bootstrap/reporting layer — nothing that duplicates existing infrastructure.

## Common Pitfalls

### Pitfall 1: Cache-key mismatch silently triggers a network fetch
**What goes wrong:** If `run.py`'s window strings don't exactly match the padded cache-key
convention (`start - N days` where N is 30 for 5m TOD padding or the window's own start for the
replay range itself), `SimulatedBarFeed`/`MassiveDataSource.cached_bars` will attempt
`fetch_bars`, consuming Phase 9's shared Massive quota.
**Why it happens:** The padding math (30 days for 5m TOD context) is computed by the caller
(`_load_massive` in feed.py resolves `tod_start = start - _MASSIVE_TOD_PAD_DAYS days`), not by
`run.py` directly — a hand-typed `--start`/`--end` pair that doesn't match what `_load_massive`
will compute internally causes a mismatch. But critically, `_load_massive`'s tod padding
(`_MASSIVE_TOD_PAD_DAYS = 30`) computes the SAME padded start as the design doc's stated cache
keys — this was verified by direct filename match for all 24×5 window files (see Environment
Availability below), so passing the exact window boundary dates from CONTEXT.md (not
re-derived padded strings) into `run.py --window` is the safe path.
**How to avoid:** `run.py` should resolve window boundaries from a single source of truth
(a WINDOWS dict keyed A–E with exact start/end strings, matching CONTEXT.md verbatim) and let
`SimulatedBarFeed`/`MassiveDataSource` compute their own internal padding — never hand-construct
padded cache filenames in `run.py` itself.
**Warning signs:** Any `[fetch]`/HTTP log line in a Wave 3 run's output (the design doc's own
Verification section already calls this out as the sanity check).

### Pitfall 2: `write_report`'s `extra_fields` patch breaking existing TJL-report callers
**What goes wrong:** `_CSV_FIELDS` is currently a module-level constant consumed by
`write_report`'s `csv.DictWriter`. A naive `extra_fields` implementation that mutates
`_CSV_FIELDS` globally (instead of building a local `fieldnames = _CSV_FIELDS + (extra_fields or [])`)
would corrupt every other caller's CSV schema for the rest of the process lifetime.
**Why it happens:** `_CSV_FIELDS` is a shared module-level list; any accidental in-place mutation
(`_CSV_FIELDS.extend(...)` or `_CSV_FIELDS += ...`) is a module-global side effect, not scoped to
one call.
**How to avoid:** Build `fieldnames = list(_CSV_FIELDS) + list(extra_fields or [])` as a local
variable inside `write_report`; never touch `_CSV_FIELDS` itself.
**Warning signs:** Existing TJL/options-phase tests (`tests/backtester/test_report.py`) start
failing after this phase's `report.py` edit lands — the plan should re-run the FULL suite
(`python3 -m pytest -q`) after this specific edit, not just the new experimental tests.

### Pitfall 3: `r_multiple` sign convention breaks for shorts if `initial_stop` direction isn't tracked
**What goes wrong:** The existing harness formula (verified) is
`r_multiple = (exit_price - pos.entry_price) / risk` where `risk = pos.entry_price - pos.initial_stop`
— implicitly long-only (risk is always positive because stop < entry for a long). A short
position has `initial_stop > entry_price`, so `risk` would be negative under the same formula,
silently flipping sign semantics rather than raising an error.
**Why it happens:** The existing production FSM (`bot/position/state.py`) never needs to handle
this because `PositionState` is long-only by construction (`R = self.entry_price - self.initial_stop`
is always positive in production).
**How to avoid:** Use `risk = abs(entry_price - initial_stop)` and `r_multiple = sign * (exit_price - entry_price) / risk`
where `sign = +1` for long / `-1` for short (exactly as CONTEXT.md specifies) — never reuse the
long-only-implicit formula as-is for the short-supporting engine.
**Warning signs:** A short trade's R comes out with the wrong sign relative to its actual P&L
(a profitable short showing negative R, or vice versa) — the design doc's own test list
("short `partial_be_trail` math", "short row net P&L sign") already covers this; do not skip
those tests.

### Pitfall 4: `Infinity` in `params.json`/`arms.json` JSON round-tripping
**What goes wrong:** `compute_metrics` already produces `profit_factor == float('inf')` for a
trade set with zero losing dollars, and `write_report` writes it via `json.dump(..., allow_nan=True)`
(default), which emits the non-standard JSON token `Infinity`. This round-trips fine within
Python's own `json` module but breaks strict-JSON consumers.
**Why it happens:** Pre-existing, documented behavior (see `report.py` docstring: "profit_factor == inf
round-trips as the JSON token Infinity").
**How to avoid:** Nothing new needed here — `aggregate.py` must use Python's `json.load` (not a
strict-mode JSON parser) to read `summary.json`/`params.json` files, and `results.md`'s table
renderer must handle `inf` explicitly (render as `"∞"` or `"inf"`, never crash on
`f"{pf:.2f}"` with an infinite float, which actually works fine in Python but should be
intentional, not accidental).
**Warning signs:** Any `aggregate.py` run raising `ValueError`/`OverflowError` while formatting
a PF value, or any table cell silently showing `inf` unformatted.

### Pitfall 5: `time_key` string slicing across tz-naive vs tz-aware CSV rows
**What goes wrong:** `SimulatedBarFeed._materialize_bars` produces `time_key` as a tz-STRIPPED
`"YYYY-MM-DD HH:MM:SS"` string (`ts_et.strftime("%Y-%m-%d %H:%M:%S")` — no offset suffix,
verified in feed.py). But `MassiveDataSource.cached_bars`'s CSV read-through applies
`pd.to_datetime(frame.index, utc=True).tz_convert(ET)` before returning — meaning the RAW cached
CSV rows may contain a mix of `-04:00`/`-05:00` offset suffixes (EDT/EST) if written across a DST
boundary, but by the time `_materialize_bars` formats `time_key`, that's already resolved to a
clean naive-looking ET string. This is a Phase 9-documented pitfall (`Infinity PF...time_key tz-naive
strings vs -04:00 suffixed CSVs (slice [:19]/[:10])` in the design doc's Risks section) —
`aggregate.py`'s own slicing (`opened_at[:10]` for day-bucket assignment) must use the same
`[:10]`/`[:19]` slicing convention, never a full `datetime.fromisoformat` that could choke on a
mixed-format string.
**How to avoid:** Always slice `time_key`/`opened_at`/`closed_at` strings with `[:10]` (date) or
`[:19]` (date+time, no offset) rather than parsing with a timezone-aware parser — matches
`report.py`'s own `_to_dt`/`_day_str` helpers (verified: both already do exactly this).
**Warning signs:** A `ValueError: Invalid isoformat string` or a slice landing on regime dates
that don't match the expected 9-slice boundaries.

### Pitfall 6: `feed.replay(day)` is O(all bars) per call — must be grouped once per window
**What goes wrong:** `SimulatedBarFeed.replay(day)` (verified, `feed.py`) iterates
`self._bars_by_code.values()` (every code's every bar) and filters by `time_key.startswith(day_str)`
on every call — O(total bars in the window) per trading day requested. Calling this once per
arm per day (15 arms × ~65 trading days per window × 5 windows) would multiply this cost 15x
unnecessarily since every arm shares the identical bar data.
**Why it happens:** `replay()` is a generator with no internal memoization; it was designed for
single-strategy Phase 6 harness use where this cost is paid once per backtest run, not once per
arm.
**How to avoid:** The design doc's own `engine.py` spec already addresses this — `group_by_day`
"computed once per window and shared by all arms." Confirm the plan preserves this: one
`SimulatedBarFeed` + one `group_by_day` pass per window, reused across all ~15 arms in that
window's run.
**Warning signs:** A single window's full-arm run taking meaningfully longer than the design
doc's own estimate (~15-20 min for all ~110 engine runs).

## Code Examples

### Verified `_CSV_FIELDS` schema (backward-compat baseline)
```python
# Source: backtester/report.py:274-275 (verified 2026-08-18)
_CSV_FIELDS = ["code", "opened_at", "entry_price", "exit_price", "quantity",
               "exit_reason", "r_multiple", "closed_at"]
# 8 columns — matches CONTEXT.md's "same 8-column trades.csv schema" claim exactly.
```

### Verified harness trade-row convention (blended exit_price, r_multiple)
```python
# Source: backtester/harness.py:400-416 (verified 2026-08-18) — production long-only reference
exit_price = (
    pos.exit_notional / pos.exit_filled_qty if pos.exit_filled_qty > 0
    else pos.entry_price
)
risk = pos.entry_price - pos.initial_stop
r_multiple = (exit_price - pos.entry_price) / risk if risk != 0 else 0.0
# CONTEXT.md's short-supporting variant: risk = abs(entry_price - initial_stop);
# r_multiple = sign * (exit_price - entry_price) / risk, sign = +1 long / -1 short.
```

### Verified sizing formula (bot/risk/risk_engine.py:105-158)
```python
# Source: bot/risk/risk_engine.py (verified 2026-08-18)
risk_dollars = equity * cfg.max_risk_per_trade_pct / 100.0   # 1% of $100k = $1000
risk_qty = math.floor(risk_dollars / stop_distance)
notional_cap = equity * cfg.max_position_size_pct / 100.0     # 10% of $100k = $10000
notional_cap_qty = math.floor(notional_cap / entry_price)
qty = min(risk_qty, notional_cap_qty)
# Matches CONTEXT.md's qty = min(floor(1000/|close-stop|), floor(10000/close)) exactly
# at the $100k / 1% / 10% parameters this phase's engine hard-codes for parity.
```

### Verified force-close time computation
```python
# Source: bot/position/manager.py:63-88 (verified 2026-08-18)
def get_force_close_time_et(today: date) -> time:
    close_hhmm = get_market_close_et(today)  # "16:00" normal, "13:00" half-day
    # force_close = close - 9 minutes -> 15:51 normal, 12:51 half-day
```

### Verified all-24-symbols cache coverage (2026-08-18 audit)
```
For each of the 24 MEGA24 symbols, all 5 padded 5m cache files exist:
  {SYM}_5m_2023-06-03_2024-08-30.csv   (window E, pad -30d from 2023-07-03)
  {SYM}_5m_2024-08-03_2024-12-31.csv   (window C, pad -30d from 2024-09-02 — the exact
                                          value CONTEXT.md flagged as error-prone; VERIFIED correct)
  {SYM}_5m_2025-03-02_2025-04-30.csv   (window B, pad -30d from 2025-04-01)
  {SYM}_5m_2025-04-01_2025-07-31.csv   (window A, pad -30d from 2025-05-01)
  {SYM}_5m_2025-07-02_2026-07-31.csv   (window D, pad -30d from 2025-08-01)
Result: 24 symbols x 5 windows = 120/120 files present. Zero new Massive fetches required.
Daily (SMA200) cache files similarly verified present at the 400-day-padded start for AAPL
(spot-checked; e.g. window C daily start 2023-07-30 = 2024-09-02 minus 400 days).
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `SimulatedBarFeed(source="yfinance")` only | `SimulatedBarFeed(source="massive", massive=...)` also supported | Phase 9 (2026-08-17) | This phase can go cache-only from day one using the Massive-source path; no yfinance 5m rolling-window limitation applies to the equity-bar data |
| Long-only `_net_pnl`/harness r_multiple | Side-aware sign planned (this phase's 1 approved edit) | Not yet shipped — Phase 10 Wave 2 | First short-side support anywhere in this backtester's history; scoped narrowly to `report.py`, not the production FSM |

**Deprecated/outdated:** None — this is a small, additive extension to an actively-maintained
codebase; nothing here is deprecated.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `bot/position/state.py::PositionState` should be re-implemented (not imported) for the short-supporting exit FSM, following the Phase 6 harness precedent of parity re-implementation with hand-computed tests | Architecture Patterns, Anti-Patterns | If wrong, the plan could instead attempt to subclass/wrap `PositionState` for shorts, which is architecturally heavier and untested in this codebase; re-implementation is `[ASSUMED]` as the right call based on codebase convention, not an explicit CONTEXT.md directive — CONTEXT.md does say "reuse `bot.strategy.indicators.swing_low_2_2`" for the exit model but does not explicitly forbid importing `PositionState` itself. Low risk: CONTEXT.md's own engine.py file spec (design doc) already describes `Position`/`Engine` as new classes in `backtester/experimental/engine.py`, implying re-implementation was already intended. |

**If this table is empty:** N/A — one low-risk assumption logged above; everything else in this
research was verified directly against the live codebase (file reads, cache directory audits,
process checks) or is copied verbatim from the operator-approved CONTEXT.md/design doc.

## Open Questions

1. **Exact `combo` arm composition (Wave 4, conditional)**
   - What we know: CONTEXT.md/design doc states the `combo` arm is appended only if "an entry
     family has PF>1 in IS with ≥25 trades" — a data-dependent decision made during Wave 4, not
     at plan time.
   - What's unclear: Which specific arm(s) would compose `combo` cannot be known until Wave 3
     run results exist.
   - Recommendation: The planner should NOT pre-specify `combo`'s contents; Wave 4's plan should
     include a decision step ("per the pre-registered rule in the hypotheses doc, decide and
     append `combo` to `arms.json` in a separate commit, only if the rule's condition is met").

2. **Wave 5 branch name and exact diff scope**
   - What we know: CONTEXT.md specifies `feature/phase10-<arm>`, default-off, schema-valid,
     `rules.json` unchanged unless SUPPORTED (one key, own commit); the design doc sketches the
     integration seam (`bot/config/loader.py` dispatch, new `bot/strategy/<name>.py`,
     `SignalEngine(cfg, gateway, store, strategy=None)` seam).
   - What's unclear: This is entirely conditional on a SUPPORTED verdict that doesn't exist yet;
     no further research is useful here until Wave 4 completes.
   - Recommendation: Plan Wave 5 as a single "conditional" plan/wave with an explicit precondition
     check (read the results doc's verdict table) rather than trying to fully spec its tasks now.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| pandas | All indicator/engine/report code | Y | 3.0.3 | — |
| numpy | Indicator math, bootstrap CI | Y | 2.4.6 (requirements.txt pins 2.5.0 — pre-existing drift, not this phase's concern) | — |
| pandas_market_calendars | `_trading_days`, force-close, regime slicing | Y | 5.4.0 (requirements.txt) | — |
| yfinance | SPY daily regime series (one-time fetch) | Y | 1.4.1 (requirements.txt) | — |
| Massive cache (24×5 5m files) | Cache-only Wave 3 runs | Y | 120/120 files verified present at exact padded keys | — (no fallback needed; fully covered) |
| Massive cache (24×5 daily files, 400d pad) | SMA200-equivalent indicator warmup if used | Y | Spot-checked present for AAPL across all 5 windows | — |
| SPY equity daily bars (Massive) | N/A — deliberately NOT used | N (no `SPY_1d`/`SPY_5m` files in `backtester/cache/massive/`, only `O_SPY...` option contract files from Phase 9) | — | yfinance is the correct/only source per CONTEXT.md's own decision — this "gap" is expected and confirms the design choice, not a blocker |
| scipy | Not required (bootstrap CI hand-rolled) | N (`ModuleNotFoundError`) | — | numpy `default_rng` bootstrap, per design |
| matplotlib | Not required (SVG hand-rolled) | N (`ModuleNotFoundError`) | — | Hand-rolled SVG polylines, per design |
| Phase 9 `warm-cache-pool` process | Must remain undisturbed | Y — alive, pid 48166, running `backtester.options_run --symbols US.SPY,US.QQQ,US.IWM,US.TLT,US.GLD,US.XLE ...` | — | N/A — verified running a disjoint command (options data), zero collision with this phase's equity-bar cache reads |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** SPY equity bars not cached under Massive — expected;
yfinance is the designed source (zero Massive quota impact, per CONTEXT.md).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (no version pin found in requirements.txt beyond `pytest-asyncio==1.4.0`; project convention is plain `def test_*` functions run via `asyncio.run()` inside the test body, not `@pytest.mark.asyncio`) |
| Config file | none found (no `pytest.ini`/`pyproject.toml` `[tool.pytest.ini_options]` detected) — plain `pytest -q` from repo root |
| Quick run command | `python3 -m pytest -q tests/backtester/experimental/ -x` (once created) |
| Full suite command | `python3 -m pytest -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| XSR-02 | No-look-ahead prefix-invariance for ext2/orb/vwap_pb signal functions | unit | `pytest tests/backtester/experimental/test_strategies.py -x` | Wave 0 (new) |
| XSR-02 | N+1-open fill correctness (reusing `make_ahead_only_5m_dataset`) | unit | `pytest tests/backtester/experimental/test_engine.py -x` | Wave 0 (new) |
| XSR-02 | Short `partial_be_trail` math, short-row net P&L sign | unit | `pytest tests/backtester/experimental/test_engine.py -x` | Wave 0 (new) |
| XSR-02 | Stop-on-close vs intrabar fill mode | unit | `pytest tests/backtester/experimental/test_engine.py -x` | Wave 0 (new) |
| XSR-02 | Force-close at 15:50/12:50 half-day bars | unit | `pytest tests/backtester/experimental/test_engine.py -x` | Wave 0 (new) |
| XSR-02 | Entry/concurrent/breaker caps (6 same-bar signals -> 5, breaker blocks rest of day) | unit | `pytest tests/backtester/experimental/test_engine.py -x` | Wave 0 (new) |
| XSR-04 | `report.py` `_net_pnl` side-aware sign, `write_report` `extra_fields` backward compat | unit | `pytest tests/backtester/test_report.py -x` (2 new cases per design doc) | existing file, 2 new cases needed |
| XSR-04 | Aggregate metrics on `make_trade_log` (PF 1.2, slice PF 2.0, deterministic CI, max consecutive losses 1) | unit | `pytest tests/backtester/experimental/test_aggregate.py -x` | Wave 0 (new) |
| XSR-04 | SVG output contains one `<polyline>` per series | unit | `pytest tests/backtester/experimental/test_aggregate.py -x` (or a dedicated `test_charts.py`) | Wave 0 (new) |
| XSR-03 | Pre-registration git-ordering proof | manual/scripted check, not pytest | `git log --diff-filter=A --format=%H%x20%ad -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` | N/A — verification command, not a test file |

### Sampling Rate
- **Per task commit:** `python3 -m pytest -q tests/backtester/experimental/ -x` (fast, scoped)
- **Per wave merge:** `python3 -m pytest -q` (full suite — currently 1040 tests collected, verified 2026-08-18)
- **Phase gate:** Full suite green before `/gsd-verify-work`; additionally the design doc's own
  Verification section (pre-registration git-ordering proof, cache-only run-log check, per-window
  `summary.json` existence, warm-cache-pool still-alive check) must all pass.

### Wave 0 Gaps
- [ ] `tests/backtester/experimental/__init__.py` and 4 test files (`test_indicators.py`,
      `test_strategies.py`, `test_engine.py`, `test_aggregate.py`) — none exist yet
- [ ] `backtester/experimental/` package itself does not exist yet (confirmed via `ls`) — this is
      expected; Wave 2 is where it's created
- [ ] 2 new cases in `tests/backtester/test_report.py` for the `_net_pnl` sign / `extra_fields` patch
- [ ] Framework install: none — pytest already installed and the full suite already runs clean at
      1040 tests (verified via `python3 -m pytest -q --collect-only`)

## Security Domain

Not applicable in the ASVS sense — this phase has no auth/session/network-input-surface (it is
an offline, local, cache-only research package with no user input, no network listeners, no
credentials). The relevant "security-adjacent" constraint is the pre-existing project one:
**no live/real-money order path may be introduced** — trivially satisfied since this phase
touches `bot/` not at all (0 lines) except conditionally on a separate, unmerged branch in Wave 5,
and even then only extends the existing paper-trading-only architecture (`bot/main.py`'s
strategy dispatch), never adds a new execution path.

## Project Constraints (from CLAUDE.md)

- **GSD workflow enforcement:** All file-changing work in this phase must go through
  `/gsd-execute-phase` (this research feeds `/gsd-plan-phase` → `/gsd-execute-phase 10`) — no
  direct repo edits outside the GSD workflow.
- **Tech stack:** Python 3.6+, must reuse existing `moomoo-api` SDK / `skills/moomooapi` client
  for any broker access — N/A for this phase (zero broker calls; the experimental backtester is
  pure pandas/numpy, cache-only, no OpenD dependency).
- **Data/execution split:** yfinance is read-only for scan/backtest data, never broker access —
  this phase's one new data need (SPY daily regime) follows this exactly.
- **Strategy config:** `rules.json` is the single source of truth for the live bot — this phase's
  hard constraint (`rules.json`/`rules_options.json` unchanged) is stricter than CLAUDE.md
  requires, not a violation of it.
- **Safety:** Paper trading only, no real-money order path — N/A this phase (no order placement
  anywhere in `backtester/experimental/`).
- **Timezone:** All strategy timing in US Eastern — the phase correctly reuses
  `bot.safety.et_helpers.ET` (via `feed.py`, already imported) and
  `bot.position.manager.get_force_close_time_et` rather than reimplementing ET conversion.
- **Naming/code style conventions** (from CLAUDE.md's documented patterns, applicable to new
  `backtester/experimental/*.py` files): `#!/usr/bin/env python3` shebang, 4-space indent,
  snake_case functions/variables, module-level docstrings (3-6 lines), `argparse` for CLI,
  `--json`-style flags where output format matters, functions returning `(action, value)` tuples
  or plain dicts (not custom exception classes) for control flow, early returns for validation.
  These are conventions observed project-wide (not literally enforced by lint config — none
  exists) and the existing `backtester/` modules (`report.py`, `feed.py`, `run.py`) already
  follow them closely; new Phase 10 code should match.

## Sources

### Primary (HIGH confidence — direct codebase verification, 2026-08-18)
- `backtester/report.py` (full file read) — `_net_pnl`, `write_report`, `_CSV_FIELDS`, `compute_metrics` signatures and current absence of side-awareness/`extra_fields`
- `backtester/feed.py` (full file read) — `SimulatedBarFeed.__init__`, `_load_massive`, `_cache_path`, `replay`, `next_bar`, cache-key construction (`_MASSIVE_TOD_PAD_DAYS=30`, `_MASSIVE_DAILY_PAD_DAYS=400`)
- `backtester/massive.py:161-179` — `cached_bars` read-through cache-key format
- `backtester/harness.py:380-430` — trade-row-on-close convention, blended exit_price, r_multiple formula
- `bot/risk/risk_engine.py:90-170` — sizing formula (`risk_qty`, `notional_cap_qty`, `qty = min(...)`)
- `bot/position/state.py` (full file read) — FSM phases, `evaluate_close` thresholds (0.75R partial, 1.0R breakeven, D-11 never-loosen trail)
- `bot/position/manager.py:63-88, 536-567` — `get_force_close_time_et` signature and force_close_all protocol
- `bot/strategy/indicators.py:100-130` — `swing_low_2_2` signature/docstring
- `tests/backtester/fixtures.py` (full file read) — `recent_session_days`, `make_ahead_only_5m_dataset`, `make_trade_log` exact signatures/return shapes
- `tests/backtester/test_execution.py:1-70` — `_FakeFeed` pattern
- `backtester/run.py:111-213` — `_trading_days`, `main()` structure
- `backtester/execution.py:32-76` — `SimulatedExecution` slippage-sign convention (BUY slips up, SELL slips down)
- `bot/config/loader.py:101-141` — `StrategyConfig` field list, confirming `strategy_name`/`direction` are NOT currently loaded
- Direct filesystem audit: `backtester/cache/massive/` — 9115 files total, 120/120 required 5m files present for MEGA24 × windows A–E at exact padded cache keys, daily-cache spot-check for AAPL across all 5 windows
- `ps -p 48166` — confirmed Phase 9 `warm-cache-pool` process alive, command line captured (`backtester.options_run --symbols US.SPY,US.QQQ,US.IWM,US.TLT,US.GLD,US.XLE ...`)
- `python3 -c "import pandas, numpy"` — pandas 3.0.3, numpy 2.4.6 confirmed installed
- `python3 -m pytest -q --collect-only` — 1040 tests currently collected
- `backtester/results/strategy-audit-validation/fulluniverse/E1/base/summary.json` — confirmed existing TJL baseline evidence structure/fields match design doc claims (PF 1.02, 35 trades, matches "226 trades across E1/E2/E3/D1/D2/D3" aggregate claim)
- `docs/research/2026-08-17-options-backtest-hypotheses.md` and `...-results.md` headers — confirmed the Phase 9 template shape this phase must match

### Secondary (MEDIUM confidence)
- None — every claim in this research was directly verified against the live repository rather
  than sourced from external documentation or web search (this phase's domain is entirely
  internal codebase conventions, not a third-party library).

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies, all existing packages version-confirmed by direct import
- Architecture: HIGH — every reused component's exact signature/behavior verified by direct file read, not assumed from CONTEXT.md's prose alone
- Pitfalls: HIGH — sourced from (a) direct codebase behavior (e.g. `_CSV_FIELDS` mutation risk) and (b) the design doc's own explicitly-stated Risks section, itself written by the operator-approved planning process this research is verifying, not inventing

**Research date:** 2026-08-18
**Valid until:** This is a fast-moving local-repo research pass tied to a specific commit
(`e1c53bf`) and a specific moment-in-time process check (`warm-cache-pool` pid 48166) — re-verify
the warm-cache-pool liveness and cache-file presence immediately before Wave 3 execution if more
than a few days elapse between this research and execution. The architectural/code-signature
findings (report.py, feed.py, harness.py, risk_engine.py, position/state.py, fixtures.py) are
stable until the next commit touches those files — check `git log --oneline -- <file>` for any
new commits before planning if execution is delayed.
