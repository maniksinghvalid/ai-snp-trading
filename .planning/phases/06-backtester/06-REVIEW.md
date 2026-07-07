---
phase: 06-backtester
reviewed: 2026-07-07T03:13:57Z
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
  critical: 7
  warning: 8
  info: 7
  total: 22
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-07-07T03:13:57Z
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The backtester package (feed, execution, harness, report, run CLI) was reviewed against the
live pipeline it reuses (`bot/signal`, `bot/risk`, `bot/position`, `bot/scanner`, `bot/state`),
with focus on look-ahead bias, N+1-open fill correctness, ET timezone handling, and
point-in-time data correctness. All 28 backtester tests pass, and the single-day, recent-date
happy path they exercise is genuinely correct: the N+1-open fill rule, the strictly-prior TOD
baseline cutoff, and the replay-clock rebind are implemented and proven by tests.

However, the review found seven Critical defects, all invisible to the single-day test
fixtures. The multi-day path is broken in three independent ways (premarket highs from the
LAST day applied to every replayed day; fills and stop-outs rolling across session boundaries
into the next morning's open; no end-of-day force close so positions carry overnight and
open positions at backtest end silently vanish from the metrics). The advertised ~58-trading-day
window is a fiction — the reused fetcher only pulls 30 calendar days of 5m data and 5 days of
premarket data, so backtests older than roughly one week silently produce zero entries or zero
bars, defeating the loud-failure guarantee `BacktestWindowError` was built for. Finally, the
daily scan layer (D1/D2/D3, SMA200, top-20 cap) computes and persists a watchlist that nothing
reads — every `--symbols` code can trade regardless of daily filters — and the synthetic
today-price used by that (currently dead) scan path is built from the day's full session
(EOD close, full-day high), a look-ahead the live 08:30 premarket scan cannot have.

Any strategy conclusion drawn from a multi-day run of this backtester as-is would be invalid.
The single-day plumbing is sound; the session/window semantics need the fixes below before
Phase 7 optimization consumes its output.

## Critical Issues

### CR-01: Multi-day replay uses only the LAST day's premarket highs (look-ahead + wrong gate values)

**File:** `backtester/harness.py:173`, `backtester/run.py:147-150`
**Issue:** `run.py` calls `harness.setup_day(day, symbols)` for every trading day in the range,
then calls `asyncio.run(harness.run())` once. Each `setup_day` ends with
`self.signal_engine.set_premarket_highs(self._feed.premarket_highs(day))`, and
`SignalEngine.set_premarket_highs` **replaces** the entire dict (signal_engine.py:93-107).
So when `run()` finally replays day 1, Gate 1 (I1: `close > premarket_high`) evaluates every
bar of every day against the FINAL day's premarket highs. That is simultaneously (a) look-ahead
(day 1 entries gated by day N's future premarket data) and (b) simply wrong values for N-1 of N
days. The per-day baselines are correctly keyed by date in the StateStore; premarket highs are
the one piece of per-day setup state held in a global, and the setup-all-then-replay-all
ordering silently clobbers it. Only single-day tests exist, so this is never caught.
**Fix:** Move the premarket-high freeze into the replay, keyed per day. E.g. store
`self._premarket_highs_by_day[day] = self._feed.premarket_highs(day)` in `setup_day`, and in
`replay_day(day)` call `self.signal_engine.set_premarket_highs(self._premarket_highs_by_day[day])`
before the bar loop. Add a two-day regression test where day 1's signal only passes I1 with
day 1's (not day 2's) premarket high.

### CR-02: `premarket_highs` fetches only `period="5d"` — replay days older than ~5 calendar days get zero premarket highs, silently producing a zero-trade backtest

**File:** `backtester/feed.py:258-266`
**Issue:** `premarket_highs(day)` calls `_download_batch(..., download_kwargs={"period": "5d", "interval": "5m", "prepost": True}, ...)`.
yfinance's `period="5d"` is measured back from **now** (fetch time), not from `day`. For any
replay `day` older than ~3 trading days, the returned frame contains no bars dated `day`, so
`highs` comes back empty for those days. SignalEngine Gate 1 (D-03) then blocks **every**
entry for every code (`signal_skipped_no_premarket_high`, signal_engine.py:485-493). The
module advertises an ~58-trading-day backtestable window (feed.py:55), but entries can only
ever fire in the trailing ~1 week — the rest of the range replays "cleanly" with zero trades
and no error. This is the exact silent-empty failure mode the module's own docstring promises
never to allow, and it reproduces the "premarket highs never seeded" class of live bug already
seen in Phase 06.1.
**Fix:** Request a window that actually covers the replay range, e.g.
`{"period": "60d", "interval": "5m", "prepost": True}` (yfinance's 5m prepost limit), or
compute the period from `self.start`. Additionally, fail loudly (or at minimum log a warning
per day) when `premarket_highs(day)` returns an empty dict for a requested replay day —
otherwise data gaps still silently zero the backtest.

### CR-03: Window guard promises 58 trading days but the reused fetcher only downloads 30 calendar days — starts between ~21 and ~58 trading days ago replay silently empty

**File:** `backtester/feed.py:55-56, 107-134` (vs `bot/scanner/fetcher.py:571-579`)
**Issue:** `INTRADAY_5M_WINDOW_TRADING_DAYS = 58` drives `_enforce_window`, which only raises
`BacktestWindowError` when `start` precedes ~58 trading days ago. But `_load_5m` downloads via
`download_intraday_5m`, whose hardcoded kwargs are `{"period": "30d", "interval": "5m", "prepost": False}`
— roughly **21 trading days**. For a `--start` between ~21 and ~58 trading days ago (no cache),
the guard passes, the download succeeds, the CSV cache is written, and `replay(day)` simply
yields zero bars for every day before the 30-day horizon. No error, no warning — exactly the
silent clipping `BacktestWindowError` exists to prevent (06-RESEARCH Open-Q1). Worse, the
undersized frame is now cached under the requested `{start}_{end}` filename, so the gap is
permanent for that range (see WR-08).
**Fix:** Either (a) set `INTRADAY_5M_WINDOW_TRADING_DAYS` to match the real fetch (~21), or
(b) have the feed request the true yfinance 5m maximum (`period="60d"`) via its own
`_download_batch` call instead of the 30d scanner wrapper. In both cases, after loading,
verify per requested trading day that at least one bar exists, and raise `BacktestWindowError`
(or log loudly) for uncovered days.

### CR-04: `next_bar` crosses session boundaries — entry fills and stop-out exits roll into the NEXT morning's open, violating D-05 and the fill model

**File:** `backtester/feed.py:202-207`, `backtester/execution.py:57-59, 89-92`
**Issue:** `next_bar(code, after)` returns the first bar with `time_key > after` across the
feed's **entire multi-day** bar list — there is no same-session constraint. Consequences:
- A signal on the last bar of day D (`consume_intent`) fills at day D+1's 09:30 open — an
  overnight-gap entry the live bot could never take (entry orders have a 20s TTL; D-05 says
  abandon). `consume_intent`'s docstring claims "None if no next bar (end of session/data)"
  but the code only returns None at end of **data**. The D-05 test
  (`test_consume_intent_returns_none_when_signal_is_on_the_last_bar`) only covers the
  end-of-dataset case, so the cross-session case is untested.
- A stop-out triggered on the last bar of day D (`manage_exit`) "fills" at day D+1's open,
  crediting the position with the overnight gap in either direction.
- Ripple effect: the entry fill's `fill_time` is day D+1, so `_increment_daily_filled_count`
  keys the fill to the wrong session date for the Gate 6 daily cap.
**Fix:** Constrain the lookup to the same ET session date:
```python
def next_bar(self, code: str, after: str) -> Optional[dict]:
    day = after[:10]
    for b in self._bars_by_code.get(code, []):
        if b["time_key"] > after:
            return b if b["time_key"][:10] == day else None
    return None
```
Add tests: signal on the last bar of day 1 of a two-day dataset must yield `fill is None`.

### CR-05: No end-of-day force close — positions carry overnight across replay days, and positions still open at backtest end silently vanish from the metrics

**File:** `backtester/harness.py` (replay loop, lines 181-199)
**Issue:** Live, the main loop calls `PositionManager.force_close_all()` at
`force_close_et` (15:51, rules.json) so the day-trading strategy is always flat overnight.
The harness ports `_process_bar` but never ports the force-close schedule, and nothing in
`replay_day`/`run` flattens positions at session end. Two concrete corruptions:
1. A position still open at day D's last bar is carried into day D+1 and managed against
   D+1's bars — overnight holds a strategy defined as intraday-only would never have. Combined
   with CR-04, its eventual stop-out even fills across the gap.
2. A position open when the replay ends never reaches CLOSED, so `_capture_closed_trades`
   never records it — its P&L (realized partials included, since only CLOSED positions are
   captured) is silently dropped from `trades.csv` and every metric in `summary.json`.
**Fix:** At the end of `replay_day(day)`, invoke the force-close path — e.g. call
`await self.position_manager.force_close_all(day)` with the replay clock set to the day's
final bar time (its `now_et()` guard reads the wall clock, so either patch
`bot.position.manager.now_et` alongside the signal-engine rebind, or replicate the
manage_exit loop with the harness clock). Then assert at end of `run()` that no non-CLOSED
positions remain, and capture any force-closed trades into `trade_log`.

### CR-06: The daily filter layer never gates replay entries — the persisted watchlist is dead state, so codes failing D1/D2/D3/SMA200/top-20 trade anyway

**File:** `backtester/harness.py:125-175`
**Issue:** `setup_day` faithfully reuses `_evaluate_symbol`, ranks candidates by gap, and
persists them via `persist_watchlist(day, ranked, "backtest")` — and then **nothing ever
reads that table during replay**. SignalEngine has no watchlist gate (its Gates 1-7 never
query `daily_scan` except for the legacy `rvol_baseline` fallback, which the TOD-baseline
primary path bypasses). Live, the watchlist is what gets subscribed — non-watchlist codes
never produce bars. In the harness, `feed.replay(day)` yields bars for ALL `--symbols`,
premarket highs are seeded for ALL codes (`self._feed.premarket_highs(day)` iterates
`self.codes`), and TOD baselines are upserted for ALL symbols (the `for code in symbols`
loop writes baselines even when `candidate is None`). Net effect: a symbol that fails every
daily filter — below SMA200, no gap, penny stock — passes Gates 1-7 and trades. The entire
daily-scan layer of the strategy (D1/D2/D3, the SCAN-08 top-20 cap) is a no-op in the
backtest, so results do not reflect the strategy being validated.
**Fix:** Filter the replay to the day's candidates. E.g. in `setup_day`, keep
`self._watchlist_by_day[day] = {c["code"] for c in ranked[:20]}` and in `_process_bar`
skip the entry branch (Gates included) for codes not in that day's set — or, closer to live
semantics, only seed premarket highs and TOD baselines for watchlist codes so Gate 1/Gate 2
block the rest. Add a test: a symbol whose daily data fails D2 must produce zero trades even
when its intraday bars would clear I1/I2/I3.

### CR-07: `synthetic_today_price` is built from the day's FULL session (EOD close, full-day high) — look-ahead into the scan decision

**File:** `backtester/feed.py:227-244`
**Issue:** `synthetic_today_price(code, day)` returns
`today_price = day_bars[-1]["close"]` (the day's ~16:00 close) and
`today_high = max(high of ALL the day's RTH bars)`. This feeds `_evaluate_symbol`, where
`today_price` is used as "today close" in D1 (`today close > prior high`) and the universe
price filter (`today close >= min_price_usd`). Live, the scan runs at 08:30 ET premarket
(rules.json `premarket_scan_et`) and `resolve_today_price` returns the price **as of scan
time** — it structurally cannot see the day's close or full-day high. In the backtest, a
stock that opened flat but rallied all day passes D1 with information from hours after the
scan decision. The docstring's "Mirrors resolve_today_price's RTH branch" is only true for a
scan run at end of day, which is not when the strategy scans. (Today this bias is masked by
CR-06 — the watchlist gates nothing — but once CR-06 is fixed this becomes the binding
selection bias, and it already contaminates the persisted `rvol_baseline`/gap-rank rows.)
**Fix:** Build the TodayPrice point-in-time for the scan moment: from the day's premarket
prepost bars (mirroring `resolve_today_price`'s premarket branch — `today_open = today_price
= latest premarket close`, `today_high = max premarket high`), reusing the same prepost frame
`premarket_highs()` already fetches. If a premarket approximation is unavailable, at most use
the day's FIRST RTH bar (open) — never `day_bars[-1]` or a full-day max.

## Warnings

### WR-01: `manage_exit` reports a full fill even when NO next bar exists — PositionManager closes the position and the harness fabricates a break-even trade

**File:** `backtester/execution.py:89-97`, `backtester/harness.py:318-325`
**Issue:** When `next_bar` returns None (stop-out on the dataset's final bar),
`manage_exit` still appends an exit record with `exit_price=None` and returns `int(qty)`.
`_trigger_stop_out` (manager.py:899-904) treats `filled_qty >= qty` as a FULL fill and marks
the position CLOSED — the manager's deliberately not-fail-open zero-fill branch is defeated
by the simulator lying about the fill. `_capture_closed_trades` then finds `total_qty == 0`
among priced fills and falls back to `exit_price = pos.entry_price`, recording a fictitious
0-PnL trade that dilutes win rate, profit factor, and drawdown. Live semantics
(`_place_exit_order` docstring: "manage_exit returning 0 is valid — no fill occurred") say
return the actually-filled quantity.
**Fix:** In `manage_exit`, return `0` when `next_bar is None` (and don't append a fill row),
letting the manager's `stop_out_incomplete` path keep the position open; pair with CR-05 so
end-of-data open positions are handled explicitly rather than laundered into fake trades.

### WR-02: The daily -2R circuit breaker can never trip in a backtest — harness docstring claims otherwise

**File:** `backtester/harness.py:40-43` (docstring), dependency: `bot/signal/signal_engine.py:434`, `bot/state/store.py:389-400`
**Issue:** Gate 7 computes realized P&L via `get_daily_trade_stats`, which SUMs over the
`trades` table — and, as `backtester/report.py`'s own docstring establishes, **nothing ever
writes `trades` in a backtest**. `realized` is therefore always 0.0 and the breaker never
trips. The harness docstring says "SignalEngine's own Gate 7 circuit-breaker check still runs
and can still block entries" — it runs, but it can never block. Live, a -2R day halts all
further entries; the backtest keeps trading through it, overstating results on losing days.
**Fix:** Either write closed backtest trades into the scratch DB's `trades` table (the
harness already has every field at `_capture_closed_trades` time), or correct the docstring
and 06 documentation to state the breaker is inert offline. Writing the rows is preferred —
it restores a real risk rule at negligible cost and makes Gate 7's replay behavior honest.

### WR-03: `closed_at` in the trade log is the wall-clock run time, not the replay bar time

**File:** `backtester/harness.py:337`
**Issue:** `_capture_closed_trades` records `"closed_at": pos.updated_at`, and every FSM
handler sets `pos.updated_at = now_et()` — the **unpatched** `bot.position.manager` clock,
i.e. the real time the backtest process happened to run. Every trade in `trades.csv` carries
essentially the same meaningless timestamp. Metric math survives only because append order is
chronological, but any downstream consumer (Phase 7 per-day stats, drawdown-by-date, WR-02's
fix writing `trades` rows keyed by `DATE(closed_at)`) gets garbage dates.
**Fix:** Record the closing bar's time instead — capture `bar.time_key` (or the parsed ET
datetime) at `_process_bar` time and pass it into `_capture_closed_trades`, e.g.
`"closed_at": self._replay_clock`.

### WR-04: Entry-window gate evaluated at the bar's START label — window shifted one bar earlier than live at both boundaries

**File:** `backtester/harness.py:239` (replay clock), `bot/signal/signal_engine.py:343-365`
**Issue:** The replay clock is set to `bar.time_key`, which for 5m bars is the bar's start
label. Live, a closed bar is processed at its close time (label + 5m wall clock). So the
backtest evaluates `[10:05, 15:30)` against start labels: the bar labeled 15:25 (closing at
15:30) passes offline but its live processing at ~15:30:00 fails the exclusive upper bound;
symmetrically the bar labeled 10:00 (closing 10:05, live-eligible) fails offline. The
effective window is displaced one bar early relative to production for both entry-window and
`session_date`/TOD-bucket keys near midnight-adjacent edge cases.
**Fix:** Set the replay clock to the bar's close time:
`self._replay_clock = self._parse_time_key_et(bar.time_key) + timedelta(minutes=5)` (keeping
the TOD bucket lookup on the label, which SignalEngine derives from `event.time_key`, not the
clock). Document the convention next to the rebind.

### WR-05: CSV cache round-trip breaks on DST-spanning data — mixed UTC offsets come back as strings and crash `_materialize_bars`

**File:** `backtester/feed.py:115, 163`
**Issue:** Fresh frames have a tz-aware `America/New_York` index; `to_csv` serializes each
timestamp with its offset (`-04:00`/`-05:00`). `pd.read_csv(..., parse_dates=True)` on a file
spanning an EST/EDT transition cannot parse mixed offsets into a DatetimeIndex and leaves the
index as object-dtype **strings**; `_materialize_bars` then evaluates `ts.tzinfo` on a `str`
→ `AttributeError`, aborting the whole feed load. The cache is precisely the mechanism meant
to extend history across months, so crossing a DST boundary is its expected steady state. The
cache test only covers a single-day (single-offset) file.
**Fix:** Normalize on write/read: `frame.tz_convert("UTC").to_csv(...)` and on read
`pd.read_csv(...); frame.index = pd.to_datetime(frame.index, utc=True)` — `_materialize_bars`
already converts UTC→ET correctly. Add a cache round-trip test spanning a November DST
transition.

### WR-06: `setup_day` performs 3 network batch downloads per replay day of largely identical data — rate-limit degradation aborts the run with an uncaught traceback

**File:** `backtester/feed.py:213-225, 246-266`, `backtester/run.py:146-152`
**Issue:** For an N-day range, `run.py` calls `setup_day` N times, each triggering
`download_daily_bars` (1y daily), `download_intraday_5m` (30d 5m), and the prepost 5d
premarket fetch — 3N yfinance batch calls returning the same rolling windows every time
(none of these accessors use the CSV cache). Beyond wasted time, hammering yfinance N times
invites rate limiting, and `_download_batch` responds by raising `ScanDegradationError`
(daily/5m thresholds are 0.10) — which `main()` does not catch (only `ConfigError` and
`BacktestWindowError`), so the operator gets a raw traceback mid-run after some days were
already replayed. This is a correctness/robustness issue, not a performance nit.
**Fix:** Fetch once per feed instance (memoize `daily_bars`/`intraday_5m_for_tod`/the prepost
frame in `__init__` or on first call — the per-day point-in-time cutoffs are applied
downstream anyway), and wrap the setup/replay in `try/except ScanDegradationError` in
`main()` returning exit code 1 with an `[ERROR]` message.

### WR-07: In-window download failures silently drop a symbol — zero bars, zero log lines

**File:** `backtester/feed.py:122-127, 129-134`
**Issue:** After a cache-miss download, `if frame is None or frame.empty: continue` skips the
symbol with no logging; the second loop's identical guard then leaves it out of
`_bars_by_code`. A typo'd ticker or transient per-symbol failure inside the window (below the
1.0-style degradation threshold `download_intraday_5m` uses at 0.10... which raises for >10%
failures, but a 1-of-many failure passes) replays as "no trades for that symbol" with no
operator-visible signal — inconsistent with the module's fail-loud philosophy for the window
case.
**Fix:** Log a warning per skipped symbol (`backtest_symbol_no_5m_data`, include symbol and
requested range), and consider raising when ALL requested symbols end up empty.

### WR-08: Cache files are trusted on filename match alone — a stale/partial cache silently replays empty days and permanently masks CR-02/CR-03 gaps

**File:** `backtester/feed.py:100-101, 113-115`
**Issue:** `_cache_path` keys on `{symbol}_{interval}_{start}_{end}`, but the file's contents
are whatever the download returned at write time (the full rolling window — unfiltered and
possibly not covering `[start, end]`, per CR-03). On a later run with the same range, the
cache hit bypasses both `_enforce_window` and any coverage check, so days missing from the
cached frame replay silently empty forever. There is no validation that the cached frame
actually contains bars for the requested range.
**Fix:** After loading (cache or network), assert coverage: for each requested NYSE trading
day in `[start, end]`, require at least one bar for at least one symbol, else raise
`BacktestWindowError` naming the uncovered days. This one check also converts CR-02/CR-03's
silent failures into loud ones.

## Info

### IN-01: `resolve_pending_intent(intent_id, "ABANDONED")` writes the literal string "ABANDONED" into the `resolved_at` timestamp column

**File:** `backtester/harness.py:290`
**Issue:** `StateStore.resolve_pending_intent(intent_id, resolved_at)`'s second parameter is
an ISO timestamp; the status is unconditionally set to `'RESOLVED'`, never "ABANDONED". The
harness copied this misuse verbatim from `bot/service/bot.py:315` (a pre-existing live
defect), so gate behavior matches production, but the scratch DB's `resolved_at` column holds
`"ABANDONED"`.
**Fix:** Pass `self._replay_clock.isoformat()`; file the same fix against `bot/service/bot.py`.

### IN-02: `SimulatedExecution.fills` mixes FillEvent objects (entries) and plain dicts (exits)

**File:** `backtester/execution.py:70, 96`
**Issue:** Entries append `FillEvent` dataclasses; exits append dicts into the same
`self.fills` list. Nothing currently iterates `fills` heterogeneously (the harness reads
`exit_fills` only), but any future consumer doing `f.avg_fill_price` will crash on the dict
entries.
**Fix:** Either drop the duplicate append into `fills` or normalize exits into FillEvents.

### IN-03: Slippage model is wired but unreachable — always 0.0

**File:** `backtester/harness.py:84`, `backtester/run.py`
**Issue:** `SimulatedExecution` accepts `slippage_usd` but the harness constructs
`SimulatedExecution(feed)` with no way to override, and the CLI exposes no flag. Dead
parameter outside tests.
**Fix:** Thread a `--slippage-usd` CLI flag through `BacktestHarness.__init__` (or remove the
parameter until needed).

### IN-04: `main()` never closes the StateStore, never validates `start <= end`, and scratch run dirs accumulate unboundedly

**File:** `backtester/run.py:135-153`
**Issue:** (a) `store = StateStore(...).open()` has no matching `close()`/try-finally — an
exception mid-run leaves the SQLite handle to GC. (b) `--start` after `--end` yields zero
trading days and a "successful" empty report (exit 0) instead of a validation error.
(c) Every invocation creates `backtester/runs/<uuid>/state.db` that is never cleaned
(gitignored, but grows on disk).
**Fix:** Add `if args.start > args.end: return 1` to the V5 block; wrap replay+report in
`try/finally: store.close()`; optionally print the run dir so the operator can prune.

### IN-05: `_process_bar` silently swallows malformed-bar construction errors

**File:** `backtester/harness.py:236-237`
**Issue:** `except Exception: return` with no logging. The live `_process_bar` it ports logs
`on_bar_closed_bar_construction_error` with `exc_info`. In a backtest a malformed bar means a
feed bug — the one context where you most want the loud log.
**Fix:** Mirror the live warning log before returning.

### IN-06: Dead `get_logger` call in `main()`

**File:** `backtester/run.py:118`
**Issue:** `get_logger(__name__)` return value is discarded; the module never logs.
**Fix:** Delete the line (keep `configure_logging()`).

### IN-07: `_enforce_window` compares against naive local `datetime.now()` instead of ET

**File:** `backtester/feed.py:138`
**Issue:** The window cutoff uses the host-local naive clock. On an ~81-calendar-day window
the few-hour skew is immaterial, but the project convention (CLAUDE.md: "must handle ET
regardless of host timezone") is `now_et()`.
**Fix:** `cutoff = now_et().replace(tzinfo=None) - timedelta(...)` or compare dates only.

---

_Reviewed: 2026-07-07T03:13:57Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
