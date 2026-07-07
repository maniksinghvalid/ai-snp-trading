---
phase: 06-backtester
reviewed: 2026-07-07T07:08:55Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - backtester/__init__.py
  - backtester/execution.py
  - backtester/feed.py
  - backtester/harness.py
  - backtester/report.py
  - backtester/run.py
  - tests/backtester/__init__.py
  - tests/backtester/fixtures.py
  - tests/backtester/test_execution.py
  - tests/backtester/test_feed.py
  - tests/backtester/test_harness.py
  - tests/backtester/test_report.py
  - tests/backtester/test_run.py
findings:
  critical: 2
  warning: 7
  info: 6
  total: 15
status: issues_found
---

# Phase 6: Code Review Report (re-review after gap-closure 06-07..06-09)

**Reviewed:** 2026-07-07T07:08:55Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

Re-review of the backtester after the multi-day gap-closure plans landed. The prior
review's multi-day BLOCKERs (CR-01..CR-07, WR-01: premarket-high clobber, session-boundary
fills, phantom exit fills, EOD force-close, watchlist cap, coverage guard, PIT premarket
TodayPrice) are **verified closed** — I traced each fix through `feed.next_bar`'s
same-session guard, `SimulatedExecution`'s force-close/zero-fill modes,
`replay_day`'s per-day premarket freeze + clock-pinned `force_close_all`, and the
multi-day regression test, and confirmed they behave as claimed against the real
`bot/position/manager.py` and `bot/signal/signal_engine.py` call chains.

Two new Critical findings remain, both found by tracing the reused `bot/` pipeline rather
than the backtester files in isolation: (1) NaN rows — the normal shape of multi-ticker
`yf.download` results — crash `_materialize_bars` via `int(NaN)` and silently poison the
premarket-high path; (2) the daily -2R circuit breaker (Gate 7) can never trip in a
backtest because it reads the `trades` table, which nothing writes during a replay — the
harness docstring's claim that Gate 7 "can still block entries" is false in practice, so
backtest statistics omit a live risk rule. Seven warnings follow, including a wrong-sign
slippage on exits, a silent TOD-baseline methodology fallback for replay days older than
~30 days, and a test-suite time bomb (~2026-08-01).

## Critical Issues

### CR-01: NaN bars from multi-ticker yfinance downloads crash `_materialize_bars` and poison premarket highs

**File:** `backtester/feed.py:225-227` (crash), `backtester/feed.py:295-301` (silent NaN)
**Issue:** `yf.download(group_by="ticker")` with multiple tickers returns per-ticker
sub-frames indexed on the **union** of all tickers' timestamps, padded with all-NaN rows
wherever one ticker lacked a bar (halts, illiquid names, staggered premarket coverage).
`get_ticker_frame` (bot/scanner/fetcher.py:334) only rejects **all**-NaN frames — partial
NaN rows pass straight through, and are also preserved by the CSV cache round-trip. Then:

- `_materialize_bars` line 227: `cum_volume += int(row["volume"])` raises
  `ValueError: cannot convert float NaN to integer` → the entire backtest run crashes for
  any realistic multi-symbol run where the symbols' 5m timestamps don't align exactly.
- `_load_premarket` lines 295-301: `float(row["high"])` happily produces NaN bars, so
  `premarket_highs()`'s `max(...)` can return NaN and `synthetic_today_price` can carry a
  NaN close. SignalEngine Gate 1 (`close > premarket_high`) evaluates `x > NaN → False`
  for every bar — the symbol silently never trades, indistinguishable from a quiet day
  (exactly the silent-failure class the CR-03 coverage guard was built to eliminate).

The multi-day regression test doesn't catch this because its mock returns per-symbol
frames as a `{sym: frame}` dict with disjoint per-symbol indexes, never the union-index
NaN padding real yfinance produces.
**Fix:**
```python
# in _materialize_bars, before iterating:
frame = frame.sort_index()
frame = frame.dropna(subset=["open", "high", "low", "close", "volume"])

# in _load_premarket (or equivalently dropna the frame before the per-row loop):
frame = frame.dropna(subset=["open", "high", "low", "close"])
```
(Column names are already lowercased by `get_ticker_frame`; the dropna also covers the
CSV cache-hit path, since cached files preserve NaN rows.)

### CR-02: Daily -2R circuit breaker (Gate 7) can never trip in a backtest — risk rule silently absent from results

**File:** `backtester/harness.py:40-43` (false claim), `backtester/harness.py:367-407` (fix site); root cause `bot/signal/signal_engine.py:434` + `bot/state/store.py:390-399`
**Issue:** The harness docstring states "SignalEngine's own Gate 7 circuit-breaker check
still runs and can still block entries — only the TradingBot-specific abandon+alert side
effect is out of scope." That is false in effect. `_is_circuit_breaker_tripped` computes
realized P&L via `store.get_daily_trade_stats(session_date_str)`, which is
`SELECT ... FROM trades WHERE DATE(closed_at) = ?` — and, as `backtester/report.py:6-10`
itself documents, **nothing writes the `trades` table during a backtest** (verified:
`grep "INTO trades"` across `bot/` + `backtester/` matches only that docstring).
`realized` is therefore always `0.0`, `0.0 <= -(daily_circuit_breaker_r * 1R)` is never
true, and the breaker never trips on any replay day, no matter how badly the day goes.

Consequence: on losing days the live bot halts new entries at -2R; the backtest keeps
entering. Every reported metric (win rate, profit factor, max drawdown, trade count) is
computed against a strategy that does not enforce a live risk rule — and the backtester's
core purpose is validating the strategy *including* its risk rules. Results are
systematically wrong on exactly the days that dominate max drawdown.
**Fix:** In `_capture_closed_trades`, insert each captured trade into the scratch store's
`trades` table (schema in `bot/state/migrations.py`) so Gate 7's query sees real per-day
realized P&L:
```python
self._store.conn.execute(
    "INSERT INTO trades (code, entry_price, exit_price, quantity, r_multiple, closed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (code, pos.entry_price, exit_price, pos.full_quantity, r_multiple,
     pos.updated_at.isoformat()),
)
self._store.conn.commit()
```
(Match exact column names from the migration; prefer a store method over raw SQL if one
exists.) Alternatively, remove the false docstring claim AND flag loudly in summary.json
that the -2R breaker is not simulated — but simulating it is the correct fix for a
strategy-validation tool. Add a regression test: a replay day engineered to lose > 2R
must stop producing new entries after the breach.

## Warnings

### WR-01: Slippage applied in the wrong direction on exits — makes sell fills *better*

**File:** `backtester/execution.py:111`, `backtester/execution.py:127`
**Issue:** Entries fill at `next_bar["open"] + self._slippage` (correct: a buy slips
upward, against you). But exits also fill at `next_bar["open"] + self._slippage` (line
127) and force-closes at `last["close"] + self._slippage` (line 111). For a SELL exit,
adverse slippage is *downward*; adding it inflates every exit price and overstates
performance whenever `slippage_usd > 0`. Latent today (the harness constructs
`SimulatedExecution(feed)` with the 0.0 default and no CLI flag exposes it), but the knob
exists on the public constructor and is a silent optimism bug the moment anyone uses it.
**Fix:**
```python
"exit_price": next_bar["open"] - self._slippage,   # line 127
"exit_price": last["close"] - self._slippage,      # line 111
```

### WR-02: TOD baselines silently unavailable for replay days older than ~30 calendar days — RVOL methodology varies by replay-day age

**File:** `backtester/feed.py:345-350`, `backtester/harness.py:147-148,160-177`
**Issue:** `intraday_5m_for_tod()` delegates to `download_intraday_5m`, which hardcodes
`period="30d"` (bot/scanner/fetcher.py:553) and is fetched fresh from the network on
**every** `setup_day` call — never CSV-cached. For any replay day older than ~30 calendar
days (and for *all* days of a cached-history replay past the 60-day window — the exact
use-case the CSV cache exists for), `_prior_sessions_only(frame, day)` yields an empty or
truncated frame → no `tod_baselines` rows → SignalEngine silently falls back to the
legacy `event.volume / rvol_baseline` path (signal_engine.py:512-529). The RVOL gate's
methodology therefore differs between "recent" and "old" replay days within the same run,
with zero indication in the output. Same class of silent divergence CR-03 fixed for bar
coverage: the 60-day replay window is advertised, but the TOD path quietly dies at ~30
days (and, with the 14-session lookback, full TOD baselines exist only for roughly the
most recent ~2 weeks of replay days). Secondary: 2 network downloads per replay day
(daily + TOD) of the same data, N times per run, is also non-deterministic mid-run.
**Fix:** Load the TOD 5m source once per feed construction with `period="60d"` via
`_download_batch` + the same CSV read-through cache as `_load_5m` — or simply reuse the
already-loaded prepost=False 60d frames from `_load_5m` (it is the same data). At
minimum, log a loud named warning in `setup_day` when `_prior_sessions_only` returns an
empty frame for a symbol/day, so the operator knows that day ran on the legacy RVOL path.

### WR-03: Per-symbol load failures are silent — a failed/misspelled ticker never trades and never warns

**File:** `backtester/feed.py:144-158` (also 264-279, 338-350)
**Issue:** `_download_batch` returns `(data, failed)` but every call site discards
`_failed`, and the per-symbol loop does a bare `continue` when `get_ticker_frame` returns
None/empty. A typo'd `--symbols US.APPL` (or a ticker yfinance genuinely fails on) loads
zero bars, contributes zero trades, and produces zero output — as long as any *other*
requested symbol covers each trading day, `_enforce_coverage` passes and the run reports
success. Same silent-failure class the CR-03 guard fixed per-day, unfixed per-symbol.
**Fix:** After `_load_5m`, fail loudly for every requested code with no bars across the
whole range (matching the coverage guard's philosophy):
```python
empty = [c for c in self.codes if c not in self._bars_by_code]
if empty:
    raise BacktestWindowError(
        f"No 5m bars loaded for requested symbol(s): {', '.join(empty)}"
    )
```

### WR-04: CSV cache round-trip breaks on DST-spanning windows (mixed UTC offsets → AttributeError)

**File:** `backtester/feed.py:135`, `backtester/feed.py:260`, `backtester/feed.py:218,289-292`
**Issue:** Cached frames are written from a tz-aware America/New_York index; `to_csv`
serialises per-row offsets (`-04:00` in EDT, `-05:00` in EST). When a 60-day window spans
a DST transition (next: 2026-11-01), `pd.read_csv(..., parse_dates=True)` cannot coerce
the mixed-offset column into a single tz-aware DatetimeIndex and falls back to **object
dtype (strings)**. `_materialize_bars` line 218 then evaluates `ts.tzinfo` /
`ts.tz_localize` on a `str` → `AttributeError`, crashing every cache-hit run over that
range (same in `_load_premarket`'s loop). Invisible until the first cached window
crosses March/November.
**Fix:**
```python
frame = pd.read_csv(path, index_col=0)
frame.index = pd.to_datetime(frame.index, utc=True)  # tz_convert(ET) happens downstream
```
Apply to both `_load_5m` (line 135) and `_load_premarket` (line 260); `utc=True` handles
mixed offsets deterministically.

### WR-05: `run.py` accepts `--start` > `--end` and exits 0 with an empty report

**File:** `backtester/run.py:109-115`, `backtester/run.py:147-153`
**Issue:** Both dates are individually validated, but there is no ordering check. With
`--start 2026-06-10 --end 2026-06-01`: `_NYSE.valid_days` returns empty →
`_enforce_coverage` has zero trading days to check (vacuously passes) → zero `setup_day`
calls → `run()` replays nothing → `write_report([])` writes a zeroed summary → **exit
code 0**. A reversed-dates typo silently reports "0 trades, clean run", violating the
module's own V5 loud-validation discipline.
**Fix:** In the V5 block:
```python
start_dt = _parse_date("start", args.start)
end_dt = _parse_date("end", args.end)
if start_dt > end_dt:
    raise ValueError(f"--start {args.start} is after --end {args.end}")
```

### WR-06: Test-suite time bomb — hardcoded 2026-06-01 fixtures hit the rolling window guard ~2026-08-01

**File:** `tests/backtester/test_harness.py:57`, `tests/backtester/test_feed.py:37` (all fixed-date tests), `tests/backtester/test_run.py:16`
**Issue:** `SimulatedBarFeed.__init__` calls `_enforce_window()` on every 5m cache miss,
and tests always start with an empty `tmp_path` cache. The guard compares
`datetime.now() - 60 days` against `start`. Every test pinned to `_DAY = "2026-06-01"`
(the entire harness suite, the e2e run test, and most feed tests) will begin raising
`BacktestWindowError` at feed construction once `now() - 60d > 2026-06-01`, i.e. around
**2026-08-01** — the whole backtester suite goes red on a calendar date with no code
change. Flaky-by-calendar is a test-reliability defect, not style.
**Fix:** Derive `_DAY` dynamically (a recent NYSE trading day, shifting the fixture
timestamps to match), or pin the window guard's clock in the shared builders — e.g.
extract `_enforce_window`'s `datetime.now()` behind a module-level hook that
`monkeypatch` can freeze near `_DAY`.

### WR-07: `_process_bar` silently swallows BarEvent construction errors (live logs them)

**File:** `backtester/harness.py:281-295`
**Issue:** The harness ports `TradingBot._process_bar`, but the live version logs
`on_bar_closed_bar_construction_error` with `exc_info` before returning
(bot/service/bot.py:265-270); the harness's `except Exception: return` drops the bar with
zero trace. Combined with CR-01 (NaN bars), a malformed bar simply vanishes from the
replay — an entire symbol-day could be skipped bar-by-bar with no signal that the data
was bad, while the feed's hod/lod accumulators silently diverge from what the pipeline
saw.
**Fix:** Mirror live: log a warning with the offending `bar_data` and `exc_info=True`
before returning.

## Info

### IN-01: `SimulatedExecution.fills` mixes FillEvent objects and plain dicts; gateway reads a private attribute

**File:** `backtester/execution.py:77,116,131`, `backtester/execution.py:153-158`
**Issue:** Entry fills are `FillEvent` dataclasses; exit fills appended to the *same*
`fills` list are plain dicts — consumers must duck-type (test_harness.py:496-498 already
ships a `_fill_code` shim). Separately, `SimulatedGateway.get_positions` reaches into
`manager._positions` (private attribute of a reused live class).
**Fix:** Record one shape in `fills` (e.g. exits as FillEvents with `is_entry=False`), or
drop the combined list and keep the two typed lists; expose an open-codes accessor on
PositionManager instead of touching `_positions`.

### IN-02: Unused import and shadowed local re-imports in harness

**File:** `backtester/harness.py:49`, `backtester/harness.py:418,424`
**Issue:** `Optional` is imported at module level but never used. `_parse_time_key_et`
and `_parse_day` re-import `datetime` locally, shadowing the module-level import from
line 48.
**Fix:** Drop `Optional`; delete the local imports and add `date` to the top-level
`datetime` import.

### IN-03: `run.py` housekeeping — discarded logger, store never closed, unbounded scratch-run accumulation

**File:** `backtester/run.py:118`, `backtester/run.py:135-152`, `backtester/run.py:81-86`
**Issue:** `get_logger(__name__)`'s return value is discarded (dead call); the opened
`StateStore` is never closed (no try/finally around the replay); every invocation creates
a new `backtester/runs/<uuid>/state.db` that nothing ever deletes.
**Fix:** Bind or remove the logger call; wrap the replay in
`try: ... finally: store.close()`; document (or clean up) the runs-dir growth.

### IN-04: Cache filename embeds `[start, end]` but content is always "60d from fetch time"

**File:** `backtester/feed.py:120-121`, `backtester/feed.py:144-148`
**Issue:** The CSV is keyed `{sym}_{interval}_{start}_{end}.csv` yet the fetch is
`period="60d"` relative to *now*, so a file's actual coverage depends on fetch date, not
its name; any run with a different start/end misses the cache entirely and refetches. The
"backtestable history grows past the 60-day window over time" claim only holds for
byte-identical `(symbol, start, end)` tuples.
**Fix:** Key the cache by symbol+interval and merge new fetches into it, or document that
history accrual requires repeating identical date ranges.

### IN-05: `replay_day`/`_process_bar` silently use the wall clock when called outside `run()`

**File:** `backtester/harness.py:205-237`, `backtester/harness.py:239-257`
**Issue:** The `now_et` module rebinds live only inside `run()`. Calling `replay_day`
directly (as `test_setup_day_reuses_scanner_point_in_time_functions:251` does) evaluates
the entry-window gate, baseline date keys, and `force_close_all`'s time guard against the
real wall clock — time-of-day-dependent behavior with no error. That test's assertions
happen not to depend on it, but the API is a footgun.
**Fix:** Move the rebind/restore into `replay_day` (idempotent), or document that `run()`
is the only supported driver and assert the clock is bound.

### IN-06: `summary.json` can contain bare `Infinity` (non-RFC8259)

**File:** `backtester/report.py:55`, `backtester/report.py:105`
**Issue:** `profit_factor = float("inf")` on a lossless run serialises as the literal
token `Infinity` (documented in the docstring). Python's `json.load` round-trips it, but
any non-Python consumer (jq, JS `JSON.parse`, spreadsheets) fails to parse the file.
**Fix:** Serialise infinite profit factor as `null` (with a note) or a numeric sentinel;
or keep as-is only if the file is guaranteed Python-only consumption.

---

_Reviewed: 2026-07-07T07:08:55Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
