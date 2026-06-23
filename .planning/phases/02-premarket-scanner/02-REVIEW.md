---
phase: 02-premarket-scanner
reviewed: 2026-06-23T23:00:02Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - bot/scanner/__init__.py
  - bot/scanner/universe.py
  - bot/scanner/fetcher.py
  - bot/scanner/calendar.py
  - bot/scanner/scanner.py
  - bot/state/migrations.py
  - bot/gateway/gateway.py
  - requirements.txt
  - tests/scanner/test_universe.py
  - tests/scanner/test_fetcher.py
  - tests/scanner/test_calendar.py
  - tests/scanner/test_scanner.py
  - tests/state/test_migrations.py
  - tests/gateway/test_gateway.py
findings:
  critical: 2
  warning: 6
  info: 4
  total: 12
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-06-23T23:00:02Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

Reviewed the premarket scanner (universe / fetcher / calendar / scanner orchestrator),
the v2 migration, and the gateway subscribe path. The data-degradation gate, top-20 cap,
idempotent upsert, and SIG-01 "subscribe only the top-20" wiring are implemented correctly
and well-tested. However, two correctness defects in `_evaluate_symbol` undermine the core
strategy contract and are masked by the test fixtures because the tests always present a
frame whose last row equals `scan_date` and always has >= 200 prior sessions — neither of
which holds in the real premarket path:

1. **D2 (trend filter) is silently bypassed** for any symbol with fewer than 200 days of
   history, because a `None` SMA200 is coerced to `0.0` before the filter.
2. **The "today" row used for gap / D1 / D3 diverges from the no-look-ahead cutoff** whenever
   yfinance returns bars only through the prior session (the normal premarket case), so the
   scanner evaluates the wrong day with no guard.

Both are weighted heavily because they bear directly on no-look-ahead correctness and the
"Trend Join" premise. Six warnings cover a cross-rescan subscription-quota leak (SIG-01),
non-atomic multi-statement migrations, and duplicate logging. Performance is out of v1 scope
and was not assessed.

## Critical Issues

### CR-01: D2 trend filter silently bypassed when SMA200 is unavailable (< 200 days history)

**File:** `bot/scanner/scanner.py:119-121` (with `bot/strategy/trend_join_long.py:61-64`)
**Issue:** When a symbol has >= `rvol_lookback_days` (14) prior sessions but fewer than 200,
`sma(prior_closes, 200)` returns NaN, so `sma200_val` becomes `None` (line 91-92). At line 119
this `None` is coerced to `0.0` and passed into `passes_daily_filters`. `_check_d2` then evaluates
`prior_close > 0.0`, which is **always True** for any real price. D2 ("prior close > SMA200" — the
trend-join premise of the entire strategy) is therefore silently skipped for any symbol that lacks
a full 200-session history. Such a symbol joins the watchlist as if it were in an established uptrend,
with no trend verification at all. The history gate only requires 14 prior sessions (line 77), so the
gap between 14 and 200 is wide. This is a strategy-correctness defect, not a style issue, and it is
invisible to the current tests because `_make_daily_frame` always builds 220 rows.

**Fix:** Treat an unavailable SMA200 as exclusion, not as a passing D2. Either require >= 200 prior
sessions in the history gate, or fail closed when `sma200_val is None`:
```python
# After computing sma200_val (line 92)
if sma200_val is None:
    _logger.warning("symbol_skipped_no_sma200", symbol=symbol, prior_sessions=int(n_prior))
    return None
# ...then pass the real value, never 0.0:
if not strategy.passes_daily_filters(symbol, frame, sma200_val):
    return None
```

### CR-02: gap/D1/D3 use `iloc[-1]` as "today" but the no-look-ahead cutoff uses `scan_date` — they diverge in the real premarket path

**File:** `bot/scanner/scanner.py:73-115`
**Issue:** The RVOL/SMA baseline is split with `prior_mask = frame.index < scan_ts` (date < scan_date),
which is correct. But D1/D3 and the ranked `gap_pct` are computed from positional rows:
`prior_row = frame.iloc[-2]`, `today_row = frame.iloc[-1]` (lines 106-110), and
`passes_daily_filters` likewise uses `daily_data.iloc[-1]`/`iloc[-2]`. These two definitions of
"today" only coincide when the frame's last row date equals `scan_date`. During a real premarket
scan (before ~09:30 ET, the actual use case), yfinance has **not yet produced today's daily bar**,
so `frame.index[-1]` is the *prior* trading day. In that state:
- `prior_mask` includes every row (nothing is `>= scan_date`), so SMA200/RVOL silently fold in the
  "today" position;
- `gap_pct`, D1, and D3 are computed against yesterday-vs-day-before instead of today-vs-yesterday;
- the persisted `prior_day_high`/`prior_close` (consumed by Phase 3 via D-08) are off by one day.
There is no assertion anywhere that `frame.index[-1].date() == scan_date`, so the mismatch is silent.
The tests never catch this because `pd.date_range(end=scan_date, freq="B")` always lands the last row
exactly on `scan_date` (verified). The result is a scan that evaluates the wrong session's gap with
no error, which is exactly the look-ahead / day-alignment hazard this phase is meant to prevent.

**Fix:** Make the "today" row explicit and validate it against `scan_date` instead of relying on
positional `iloc`. For example, locate today's row by date, and skip (or log+abort) if it is absent:
```python
today_rows = frame.index[frame.index.normalize() == pd.Timestamp(scan_date)]
if len(today_rows) == 0:
    _logger.warning("symbol_skipped_no_today_bar", symbol=symbol, scan_date=str(scan_date))
    return None
today_row = frame.loc[today_rows[-1]]
prior_frame = frame.loc[frame.index < pd.Timestamp(scan_date)]
prior_row = prior_frame.iloc[-1]
# derive gap/D1/D3 from today_row + prior_row, and pass a 2-row frame
# (prior_row, today_row) into passes_daily_filters so iloc[-1]/[-2] align.
```
At minimum, add an explicit guard that `frame.index[-1].date() == scan_date` and exclude the symbol
otherwise, so the scanner never silently ranks the wrong day.

## Warnings

### WR-01: Evicted active codes are never unsubscribed — cumulative subscriptions can exceed the top-20 quota cap across rescans (SIG-01)

**File:** `bot/scanner/scanner.py:251-263, 430-431`
**Issue:** `run_intraday_rescan` subscribes only `result - active_codes` (new codes) via
`_subscribe_new_codes`, but nothing ever *unsubscribes* codes that fall out of the protected top-20.
There is no unsubscribe path anywhere in `bot/` (confirmed by grep). Across successive rescans, the
caller's `active_codes` plus each rescan's newly-added codes accumulate at the broker while dropped
codes keep their live K_5M feeds. The single-rescan cap holds, but the *cumulative* subscribed set is
unbounded and can exceed 20, directly violating the SIG-01 quota-safety intent the subscribe path is
built around. The test only exercises the additive case (`test_rescan_subscribes_only_new`).
**Fix:** Compute the eviction set (`active_codes - set(result)`) and unsubscribe it (add a
`gateway.unsubscribe()` and call it before/after subscribing new codes), or document that the caller
is responsible for unsubscription and assert `len(active_codes | set(result)) <= _WATCHLIST_CAP`.

### WR-02: D-04 protection silently drops an actively-traded code that fails the intraday re-filter

**File:** `bot/scanner/scanner.py:392-395`
**Issue:** "Active candidate protection" only re-includes active codes that still appear in `passing`
(i.e., still pass D1/D2/D3 on the re-scan). An active code whose gap collapses intraday is simply
absent from `passing`, so it is dropped from the returned watchlist and from any further handling —
even though it may correspond to an open position or live feed the bot is actively managing. Combined
with WR-01 (no unsubscribe), such a code also keeps its feed silently. Whether eviction is intended
should be explicit, not an emergent side effect of "active code happens to still pass."
**Fix:** Decide and encode the policy: either always carry forward `active_codes` regardless of
re-filter outcome (true D-04 protection), or explicitly log an `active_code_evicted` event so the
drop is auditable rather than silent.

### WR-03: Migration 0002 applies multiple `ALTER TABLE` statements non-atomically; a mid-script failure wedges future runs

**File:** `bot/state/migrations.py:96-102, 138-141`
**Issue:** `conn.executescript(sql)` issues an implicit COMMIT before running and does not wrap the
five `ALTER TABLE ADD COLUMN` statements in a single transaction. If the process dies (or one ALTER
fails) after some columns are added but before `PRAGMA user_version = i` runs, the partial columns are
already committed while `user_version` stays at the prior value. The next `run_migrations` re-applies
the whole 0002 script and fails with "duplicate column name", permanently blocking startup. SQLite
also cannot DDL-rollback inside `executescript` reliably. The tests only cover the happy path and a
clean v1→v2 upgrade.
**Fix:** Bump `user_version` inside the same transaction as the DDL, or guard each ALTER with a
column-existence check (`PRAGMA table_info(daily_scan)`) so re-application is idempotent:
```python
existing = {r[1] for r in conn.execute("PRAGMA table_info(daily_scan)")}
for col, decl in (("prior_day_high","REAL"), ...):
    if col not in existing:
        conn.execute(f"ALTER TABLE daily_scan ADD COLUMN {col} {decl}")
```

### WR-04: `scan_partial_data` is logged twice for every partial-failure scan

**File:** `bot/scanner/scanner.py:230-235` and `bot/scanner/fetcher.py:107-112`
**Issue:** `download_daily_bars` already emits `scan_partial_data` when `failed` is non-empty
(fetcher.py:107-112). `_compute_candidates` then re-checks `if failed:` and logs the identical event
again (scanner.py:230-235). Every degraded-but-under-threshold scan produces two duplicate warnings,
inflating audit/log noise and risking double-counting in any downstream alerting.
**Fix:** Remove the redundant block in `_compute_candidates` (lines 230-235); the fetcher is the
single source of truth for that event.

### WR-05: `fetch_sp500_symbols` blindly trusts `tables[0]` from Wikipedia and returns whatever is cached without validating symbol content

**File:** `bot/scanner/universe.py:73-99`
**Issue:** `pd.read_html` returns all tables on the page; `tables[0]` assumes the constituents table is
always first. If Wikipedia reorders tables or the page layout changes, `tables[0]` may be a different
table that still happens to contain a "Symbol" column (or the code silently falls through to a stale
cache). The cache-read branch (line 98-99) also returns `cached_df["symbol"].tolist()` with no
validation that the list is non-empty or plausibly ~500 entries, so a truncated/garbage cache file is
returned as authoritative and silently shrinks the universe (which can in turn skew the 10% degradation
denominator downstream).
**Fix:** Select the constituents table by matching expected columns (e.g. require both "Symbol" and
"Security"/"GICS Sector"), and sanity-check the result length (e.g. `if len(yf_symbols) < 400: raise`)
before writing the cache or returning a cached list.

### WR-06: `_subscribe_new_codes` and the daily-scan subscribe call swallow no errors but block the event loop via `asyncio.run` per call

**File:** `bot/scanner/scanner.py:262, 335`
**Issue:** Both subscribe paths call `asyncio.run(gateway.subscribe(...))` from synchronous scan code.
If this scanner is ever invoked from within an already-running event loop (e.g., a future async
scheduler), `asyncio.run` raises `RuntimeError: asyncio.run() cannot be called from a running event
loop`, aborting the scan after persistence has already happened — leaving a persisted watchlist with no
subscriptions. This is a latent integration hazard given Phase 4/5 will add async loops.
**Fix:** Accept an optional event loop / make the entrypoints async, or guard with
`asyncio.get_event_loop().is_running()` and use `run_coroutine_threadsafe`/`await` appropriately.

## Info

### IN-01: Dead defensive branch in `_evaluate_symbol`

**File:** `bot/scanner/scanner.py:100-102`
**Issue:** `if len(prior_sorted) < cfg.rvol_lookback_days: return None` can never be true at this point —
`n_prior < cfg.rvol_lookback_days` was already rejected at lines 77-84, and `prior_sorted` is the head
of the same `prior_mask` rows. The comment ("Already checked above, but guard here for clarity")
acknowledges it is dead. Harmless but misleading.
**Fix:** Remove the branch, or convert it to an `assert` to document the invariant without implying a
live code path.

### IN-02: `requirements.txt` pins `numpy==2.5.0` but the environment runs numpy 2.4.6

**File:** `requirements.txt:5`
**Issue:** The pin `numpy==2.5.0` is a real published version, but the developer environment has 2.4.6
installed, so the declared and actual environments diverge. A hard `==` pin on a fast-moving transitive
math dependency (used by pandas/yfinance) is also fragile across platforms.
**Fix:** Confirm the project is actually tested against 2.5.0, or relax to a compatible range
(e.g. `numpy>=2.4,<3.0`) consistent with how pandas is pinned.

### IN-03: `lxml` is unpinned while every other dependency is pinned/bounded

**File:** `requirements.txt:11`
**Issue:** `lxml` (required by `pd.read_html` for the Wikipedia scrape) has no version constraint,
unlike all sibling dependencies. An unexpected major `lxml` release could change `read_html` parsing
behavior and silently alter the universe.
**Fix:** Add a bound, e.g. `lxml>=5.0,<6.0`.

### IN-04: `bot/scanner/__init__.py` is empty but listed as a reviewed source file

**File:** `bot/scanner/__init__.py:1`
**Issue:** The file is a 1-line empty package marker (no content). No defect; noted only because it was
in the explicit review scope. No action required.
**Fix:** None.

---

_Reviewed: 2026-06-23T23:00:02Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
