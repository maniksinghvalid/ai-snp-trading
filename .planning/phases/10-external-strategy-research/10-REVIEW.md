---
phase: 10-external-strategy-research
reviewed: 2026-08-18T00:00:00Z
depth: standard
files_reviewed: 26
files_reviewed_list:
  - backtester/cache/SPY_1d_regime.csv
  - backtester/experimental/__init__.py
  - backtester/experimental/aggregate.py
  - backtester/experimental/arms.json
  - backtester/experimental/charts.py
  - backtester/experimental/engine.py
  - backtester/experimental/exits.py
  - backtester/experimental/indicators.py
  - backtester/experimental/run.py
  - backtester/experimental/strategies.py
  - backtester/report.py
  - docs/research/2026-08-18-external-strategies-hypotheses.md
  - docs/research/2026-08-18-external-strategies-results.md
  - docs/research/assets/2026-08-18-external-strategies/equity_curves.svg
  - docs/research/assets/2026-08-18-external-strategies/pf_by_slice.svg
  - docs/research/assets/2026-08-18-external-strategies/results-intrabar.csv
  - docs/research/assets/2026-08-18-external-strategies/results.csv
  - docs/research/assets/2026-08-18-external-strategies/results.md
  - tests/backtester/experimental/__init__.py
  - tests/backtester/experimental/test_aggregate.py
  - tests/backtester/experimental/test_charts.py
  - tests/backtester/experimental/test_engine.py
  - tests/backtester/experimental/test_exits.py
  - tests/backtester/experimental/test_indicators.py
  - tests/backtester/experimental/test_run.py
  - tests/backtester/experimental/test_strategies.py
  - tests/backtester/test_report.py
findings:
  critical: 0
  warning: 6
  info: 3
  total: 9
status: issues_found
---

# Phase 10: Code Review Report

**Reviewed:** 2026-08-18
**Depth:** standard
**Files Reviewed:** 26
**Status:** issues_found

## Summary

This phase adds a self-contained, cache-only research backtester
(`backtester/experimental/{engine,exits,indicators,strategies,run,aggregate,charts}.py`)
plus its committed evidence artifacts. The core replay/fill logic (N+1-open fills,
force-close, caps/breaker, short-side R math, exit-model FSMs) is careful and
mostly well tested; I traced the entry/fill/exit path end to end, hand-verified
several arithmetic invariants (sizing parity with `bot/risk/risk_engine.py`,
gap-through risk-denominator handling, blended exit-price math), and cross-checked
`results.csv` against the published verdict table in
`docs/research/2026-08-18-external-strategies-results.md` — every spot-checked
number matches exactly, so the aggregation pipeline is internally consistent.

I found no BLOCKER-level defect that corrupts the published numbers. I initially
suspected a temporal-inversion bug (a same-day-force-close position filled with a
next-day price), but ruled it out after confirming `SimulatedBarFeed.next_bar`
enforces a same-session guard (CR-04) that the reviewed code relies on implicitly.
I did find and reproduce a genuine (currently dormant) cross-session data-leak bug
in `vwap_pb_signals`, plus several design/robustness gaps that should be fixed
before this replay engine is reused for anything beyond this one-off research
pass. See Warnings below.

## Warnings

### WR-01: `vwap_pb_signals` leaks the prior session's post-10am high/low into the first bar of the next session

**File:** `backtester/experimental/strategies.py:205-212`
**Issue:** `high_since_10`/`low_since_10` are correctly reset per session via
`.groupby(session).cummax()`/`.cummin()`, but `confirmed_up`/`confirmed_down` then
apply a **plain** `.shift(1)` to that already-grouped series:

```python
high_since_10 = frame["high"].where(since_10am, other=float("-inf")).groupby(session).cummax()
low_since_10 = frame["low"].where(since_10am, other=float("inf")).groupby(session).cummin()
confirmed_up = high_since_10.shift(1) > or_high
confirmed_down = low_since_10.shift(1) < or_low
```

`.shift(1)` shifts by row *position*, not per-group, so at the first bar of every
new session the shifted value is the **previous session's** final post-10am
cummax/cummin, not `-inf`/`inf` as intended. I reproduced this directly:

```
high_since_10 (session 1, post-10am bar): 150.0
high_since_10 (session 2, first bar 09:30): -inf
high_since_10.shift(1) (session 2, first bar 09:30): 150.0   <- leaked from session 1
```

This is currently masked in every production run because (a) `or_high`/`or_low`
are still `NaN` at a session's first bar for any `or_bars >= 2` (vwap_pb's
`DEFAULTS` never sets `or_bars`, so it is always the inherited default of 6), and
`NaN` comparisons are `False`; and (b) `entry_start=10:00` excludes the first
bars of every session from `time_ok` regardless. So none of the committed
`vwap_pb_base` results are affected. But this is a real, reproducible defect, not
a hypothetical one — a future `--set or_bars=1` run, or any change to
`entry_start`, would silently start producing signals seeded by stale data from a
different trading day.

**Fix:** Use a session-aware shift, e.g.:
```python
confirmed_up = high_since_10.groupby(session).shift(1) > or_high
confirmed_down = low_since_10.groupby(session).shift(1) < or_low
```

### WR-02: Entry evaluation is not gated by the force-close bar; relies entirely on an implicit feed-level guard

**File:** `backtester/experimental/engine.py:212-337`
**Issue:** `_run_day`'s per-bar loop force-closes *existing* positions once
`is_force_close_bar` is `True` (step 1), but step 3 ("entry evaluation") and step
4 ("fill") never check `is_force_close_bar` — a fresh signal at or after the
force-close bar is still evaluated and can still be filled. `entry_end` is a
fixed wall-clock parameter (`ext2`: `15:30`, `vwap_pb`: `15:00`) that is **not**
half-day aware, while `force_close_floor` correctly shortens on half days (per
`test_force_close_half_day_2025_07_03_at_1250_bar`). On a half day, the actual
last bar of the session is simultaneously (a) still inside `entry_end`'s
wall-clock window and (b) the force-close bar.

The only thing preventing a corrupted trade (an entry filled at a *later*
session's open, immediately followed by an `eod_no_bars` close using *today's*
close, i.e. `closed_at < opened_at`) is `SimulatedBarFeed.next_bar`'s CR-04
same-session guard in `backtester/feed.py:430-443`, which this module never
references or asserts on. That guard is correct today, but `Engine` has no
defense-in-depth of its own — a future change to `next_bar` (or a different feed
implementation passed to `Engine`) would silently reintroduce the corruption with
no test to catch it. Notably, `tests/backtester/experimental/test_engine.py`'s
own `_FakeFeed.next_bar` does **not** implement the same-session guard the real
feed enforces (it returns any future bar irrespective of day), so this test
suite would not detect a regression of that invariant either.

**Fix:** Skip entry evaluation entirely once `is_force_close_bar` is `True`
(cheapest fix — move the `is_force_close_bar` check to guard step 3), and add a
regression test with a `_FakeFeed` that *does* return a next-day bar, asserting
the resulting candidate is never filled/produces no trade.

### WR-03: `equity_svg`'s shared x-axis is index-based, not date-based — per-arm curves can silently misalign

**File:** `backtester/experimental/charts.py:184-197`
**Issue:** `main()` builds each core arm's equity curve independently:
```python
series_by_label = {
    arm: build_equity_curve(trades, 100_000.0, 0.005)
    for arm, trades in trades_by_arm.items() if trades
}
```
No `start`/`end` is passed, so each arm's curve spans **that arm's own**
first/last trade date (`build_equity_curve`'s documented default). `equity_svg`
then plots every series on a shared x-axis using `_x(i, n)`, where `n` is that
series' own point count — position `i` on arm A's polyline and position `i` on
arm B's polyline are not guaranteed to represent the same calendar date if the
two arms' first/last trades differ.

In the committed `equity_curves.svg` all three core arms happen to produce 773
points each (verified), so today's chart is not visibly misleading, but nothing
in the code guarantees that — it is coincidental alignment, not a property the
implementation enforces. A future arm with a materially different trading
frequency or date range would silently misalign against the others.

**Fix:** Compute one shared `(start, end)` — e.g. the union of every core arm's
first/last trade date — and pass it explicitly to every `build_equity_curve`
call so `n` (and therefore each x position) is identical across series.

### WR-04: `exits.py`'s exit models accept but never use `params`, hiding that ladder/R levels are not configurable

**File:** `backtester/experimental/exits.py:55,86,146`
**Issue:** `pct_ladder`, `partial_be_trail`, and `fixed_2r` all declare
`params: dict = None` and are called by `Engine._manage_position` with
`self.params` (the arm's fully-resolved config), but none of the three functions
ever reads `params`. The ladder percentages (`_LADDER_LEG_PCTS = [0.01, 0.02,
0.03]`) and the R-multiple thresholds (0.75R/1.0R for `partial_be_trail`,
1.0R/2.0R for `fixed_2r`) are hardcoded module constants. This isn't incorrect —
none of `arms.json`'s arms need per-arm ladder/R tuning today — but the
parameter-threading strongly implies these values are configurable when they are
not, which is a trap for whoever next tries `--set ladder_pcts=...` or similar
and finds it silently ignored.

**Fix:** Either drop the unused `params` argument (simplify the signature) or
add a one-line comment at each function noting the constants are intentionally
not `params`-driven yet.

### WR-05: Duplicate CSV-trade-coercion logic between `run.py` and `aggregate.py`

**File:** `backtester/experimental/run.py:321-331`, `backtester/experimental/aggregate.py:79-89`
**Issue:** `run._load_baseline_trades` and `aggregate._read_trades` are
byte-for-byte identical implementations (same four-field float/int coercion over
a `csv.DictReader`). `aggregate.py`'s own docstring acknowledges this ("mirrors
run.py's own `_load_baseline_trades` convention") as a deliberate tradeoff to
preserve the function-local-import/parallelism contract, so this is not an
oversight — but it is still a DRY violation: if a future change adds a coerced
column to one copy (e.g. a new numeric field written by `write_report`), it is
easy to forget the other, and nothing would catch the drift.

**Fix:** Low priority given the documented tradeoff; if this package grows a
third consumer of trades.csv, extract the shared coercion into
`backtester/experimental/__init__.py` (currently empty) rather than a third copy.

### WR-06: `regime_gate == "weekly_spy"` silently no-ops instead of failing fast when `regime_fn` is not supplied

**File:** `backtester/experimental/engine.py:203-206, 290-297`
**Issue:**
```python
gate_active = self.params.get("regime_gate") == "weekly_spy"
regime = "none"
if gate_active and self.regime_fn is not None:
    regime = self.regime_fn(day)
```
If `gate_active` is `True` but `self.regime_fn` is `None` (a caller requests the
regime gate but forgets to wire a regime function), `regime` stays `"none"` for
the whole day. Further down, the fill loop's `if gate_active: ... if regime ==
"bear" ...` branches never match `"none"`, so the gate becomes a silent no-op —
`gate_active=True` is recorded on every emitted trade row's `regime` field as
`"none"` (via `regime=regime if gate_active else "none"`, which resolves to
`"none"` either way here) with no warning that the requested gate was never
actually applied. `run.py` always wires `regime_fn` correctly today whenever any
selected arm needs it, so this is dormant, but `Engine` is a reusable class and
this is a footgun for the next caller.

**Fix:** Raise (`ValueError`) in `Engine.__init__` if
`params.get("regime_gate") == "weekly_spy"` and `regime_fn is None`, rather than
silently downgrading to "no gate."

## Info

### IN-01: `build_frame`'s `in_window` column is computed but never consumed

**File:** `backtester/experimental/engine.py:85`
**Issue:** `frame["in_window"] = (day_str >= feed.start) & (day_str <= feed.end)`
is set on every frame `build_frame` produces, but no code in `engine.py`,
`strategies.py`, or `run.py` reads the `in_window` column — the actual
in-window/out-of-window (padding) distinction is enforced upstream by
`group_by_day`/`_trading_days` only ever handing the engine bars for real
trading days. Harmless dead column; either wire it into a defensive assertion or
drop it.

### IN-02: Magic numbers in `Engine._size` are not self-documenting at the point of use

**File:** `backtester/experimental/engine.py:453-455`
**Issue:** `math.floor(1_000.0 / stop_distance)` (1% of the project's fixed
$100,000 paper-equity risk basis) and `math.floor(10_000.0 / close)` (10%
notional cap) are correct and match `bot/risk/risk_engine.py`'s formula
(verified), but the $100k basis they derive from is only stated in the
surrounding docstring/CLAUDE.md, not at the literal usage site. A one-line
inline comment (`# 1% of $100k`) would make this self-contained.

### IN-03: `expectancy_r` (aggregate.py) and `avg_r_multiple` (report.py) are the same formula reported under two column names

**File:** `backtester/experimental/aggregate.py:165`
**Issue:** `expectancy_r = sum(t["r_multiple"] for t in trades) / len(trades)` is
arithmetically identical to `report.compute_metrics`'s `avg_r_multiple`. Not a
bug (the pre-registered results table explicitly calls for both column names),
but worth a short comment noting they are intentionally the same metric under
two names, to save a future reader the trip to prove it.

---

_Reviewed: 2026-08-18_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
