---
phase: 06-backtester
plan: 02
subsystem: backtester-feed
tags: [backtester, feed, yfinance, csv-cache, wave-2]
dependency-graph:
  requires:
    - backtester (importable empty package, 06-01)
    - tests.backtester.test_feed.py (importorskip stub, 06-01)
  provides:
    - backtester.feed.SimulatedBarFeed
    - backtester.feed.BacktestWindowError
    - backtester.feed.CACHE_DIR
    - backtester.feed.INTRADAY_5M_WINDOW_TRADING_DAYS
  affects:
    - backtester/execution.py (06-03, consumes feed.next_bar)
    - backtester/harness.py (06-05, consumes feed.replay/daily_bars/synthetic_today_price/intraday_5m_for_tod/premarket_highs)
tech-stack:
  added: []
  patterns:
    - "CSV read-through cache keyed by {symbol}_{interval}_{start}_{end}.csv (write-through on miss)"
    - "Reuse of bot.scanner.fetcher batch kernel (download_intraday_5m/download_daily_bars/get_ticker_frame/_download_batch) — no re-implemented yfinance batch loop"
key-files:
  created:
    - backtester/feed.py
  modified:
    - tests/backtester/test_feed.py
decisions:
  - "06-02: next_bar(code, after) parameter named `after` (not `after_time_key`) — matches the actual call-site convention in 06-PATTERNS.md's SimulatedExecution example and the Wave-0 test stub, which take precedence over the plan objective's prose naming"
  - "06-02: premarket_highs() reuses the shared (underscore-private) bot.scanner.fetcher._download_batch kernel directly rather than re-implementing a second yf.download call site — no public fetcher wrapper exists for 5m+prepost=True, and duplicating the retry/degradation logic (Pitfall #1/#2) would be strictly worse per the Don't Hand-Roll table"
  - "06-02: daily_bars()/intraday_5m_for_tod() return the RAW yf.download-shaped dict (NOT routed through get_ticker_frame) — _evaluate_symbol and _compute_tod_baselines both expect the raw per-ticker frame themselves (confirmed by reading their call sites in bot/scanner/scanner.py) and apply their own column-casing handling"
  - "06-02: BacktestWindowError raised BEFORE any network call when start precedes the window and no cache exists (not after an empty-frame response) — avoids an unnecessary yfinance call and matches Pitfall 2's 'never silently proceed' intent"
  - "06-02: window cutoff computed as a calendar-day approximation (INTRADAY_5M_WINDOW_TRADING_DAYS * 7/5) since a live fetch attempt has no trading-day concept until it succeeds or fails; documented inline as an approximation"
metrics:
  duration_minutes: 25
  completed: 2026-07-06
---

# Phase 06 Plan 02: SimulatedBarFeed Summary

Built `backtester/feed.py`'s `SimulatedBarFeed` — the historical 5m bar source that replaces
the live moomoo SDK push stream (BT-04). It loads 5m bars via the reused
`bot.scanner.fetcher` batch kernel, write-through CSV caches them, replays them
chronologically as `BarEvent`-shaped dicts with point-in-time `hod`/`lod`/`cum_volume`, and
exposes the point-in-time setup-data accessors the harness (06-05) will need per historical
session date.

## What Was Built

**Task 1 — 5m replay engine (load, CSV cache, normalize, hod/lod/cum_volume, next_bar, window guard):**
- `SimulatedBarFeed(codes, start, end, cache_dir=CACHE_DIR)`: for each requested moomoo code,
  checks a CSV cache file (`{cache_dir}/{symbol}_5m_{start}_{end}.csv`) first; on a miss, calls
  the reused `download_intraday_5m` (not a re-implemented yfinance batch loop), routes the
  result through `get_ticker_frame` (Pitfall 3 column-casing), and writes the normalized frame
  to the cache (write-through).
- `_materialize_bars`: builds chronological bar dicts with the exact `BarEvent` field set,
  computing session-running `hod`/`lod`/`cum_volume` as running max-high/min-low/summed-volume,
  resetting the accumulators at each new session date (mirrors `BarAggregator`).
- `replay(day)`: chronological generator across codes for one session date, sorted by
  `(time_key, code)`.
- `next_bar(code, after)`: returns the strictly-next bar for `code` with `time_key > after`, or
  `None` at the end of data.
- `BacktestWindowError`: raised when `start` precedes the available yfinance 5m window
  (`INTRADAY_5M_WINDOW_TRADING_DAYS = 58`, converted to an approximate calendar-day cutoff) AND
  no CSV cache file covers it — raised BEFORE any network call, never a silent empty bar list.
- Ticker codes normalized via the reused `yfinance_to_moomoo` (BRK-B edge case handled).

**Task 2 — Point-in-time setup-data accessors:**
- `daily_bars(codes)`: delegates to `download_daily_bars`, returns the raw result unchanged
  for `_evaluate_symbol` to consume directly.
- `intraday_5m_for_tod(codes)`: delegates to `download_intraday_5m` (prepost=False), returns
  the raw result unchanged for `_compute_tod_baselines` to consume directly.
- `synthetic_today_price(code, day)`: builds a `TodayPrice` from `day`'s already-loaded first
  regular-session 5m bar (today_open = first bar's open, today_price = latest bar's close,
  today_high = max high) — mirrors `resolve_today_price`'s RTH branch without a live yfinance
  call.
- `premarket_highs(day)`: a SEPARATE `prepost=True` 5m fetch (the RVOL-TOD path stays
  `prepost=False`), reusing the shared `_download_batch` kernel; returns
  `{moomoo_code: max(high)}` restricted to bars with ET time < 09:30. Documented in the module
  docstring as a same-methodology/different-source approximation of the live broker's
  `pre_high_price` field (06-RESEARCH Pitfall 5 / Assumption A3), not a bit-for-bit
  reproduction.

## Verification

```
python3 -m pytest tests/backtester/test_feed.py -q   → 7 passed
python3 -m pytest tests/ -q                          → 596 passed, 4 skipped
python3 -c "import backtester.feed"                  → exits 0
grep -n 'from bot.scanner.fetcher import' backtester/feed.py   → shows the reused import block
grep -n 'prepost=True' backtester/feed.py                       → shows premarket_highs' separate fetch
grep -L 'MoomooGateway\|bot.gateway' backtester/feed.py         → lists feed.py (no broker import)
```

No file under `bot/` was touched. Full suite stability confirms no regression (596 = prior
589 + 7 new feed tests; 4 skipped = 3 remaining Wave-0 stubs (execution/harness/report) + 1
pre-existing skip).

## Deviations from Plan

**[Rule 1 - Bug] Module docstring literal string broke its own no-broker-import grep check**

- **Found during:** Task 1 verification (`grep -L 'MoomooGateway\|bot.gateway' backtester/feed.py`
  returned empty instead of listing the file)
- **Issue:** The module docstring explained the import boundary using the literal text
  "bot.gateway/MoomooGateway" (to say the module does NOT import it), which itself satisfied
  the grep pattern the acceptance criterion checks against — the same class of false positive
  documented in 06-01-SUMMARY.md for a different file.
- **Fix:** Reworded the docstring to describe the boundary without embedding the literal
  marker string ("never the broker gateway layer").
- **Files modified:** backtester/feed.py
- **Commit:** 31ef9ef (caught before commit, included in Task 1's commit)

**Test additions beyond the Wave-0 stub (expected per plan, not scope creep):**

The Wave-0 stub (06-01) shipped 3 `test_feed.py` tests (chronological replay, next_bar, window
error). Per the plan's own acceptance criteria and action items, this plan added 4 more tests
to reach full coverage: CSV-cache round-trip (0 network calls on second load), moomoo-code
normalization via `yfinance_to_moomoo`, `synthetic_today_price`, and `premarket_highs`
(pre-09:30 exclusion). This was explicitly instructed by the plan text ("Fill the corresponding
assertions in test_feed.py" / "Add tests for synthetic_today_price ... and premarket_highs
... to test_feed.py"), not an unplanned addition.

No other deviations — plan executed as written.

## Known Stubs

None. All accessors are fully wired against the reused `bot.scanner.fetcher`/`bot.scanner.universe`
functions; no placeholder/mock data paths remain in `backtester/feed.py`.

## Self-Check: PASSED

- FOUND: backtester/feed.py
- FOUND: tests/backtester/test_feed.py (modified, 7 tests, all passing)
- FOUND commit 31ef9ef (Task 1: SimulatedBarFeed replay engine)
- FOUND commit 35c2194 (Task 2: point-in-time setup-data accessors)
