# Phase 2: Premarket Scanner - Research

**Researched:** 2026-06-23
**Domain:** yfinance batch data, pandas-market-calendars NYSE gate, SQLite schema migration, Moomoo K_5M subscriptions
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** S&P 500 symbol list scraped from Wikipedia (`pandas.read_html`) on each scan; cached to a dated local file under `data/`; fall back to last good cache on scrape failure.
- **D-02:** yfinance supplies daily bars only — not the S&P 500 membership list (no index-constituents endpoint). The Wikipedia scrape produces the symbol list; that list feeds yfinance for bar download.
- **D-03:** Cache location is the existing gitignored `data/` dir; filename carries the snapshot date.
- **D-04:** Intraday re-scan re-ranks the union of existing + newly-qualifying candidates by gap %, keeps top 20, but NEVER evicts an already-subscribed/active candidate.
- **D-05:** Idempotency enforced via existing `daily_scan` `UNIQUE(scan_date, code)` constraint.
- **D-06:** Proceed + warn while failed symbols are < 10% of universe; abort + alert at >= 10% (~50 of ~500 symbols).
- **D-07:** Degradation "alert" this phase = logged/surfaced event (structlog + audit). Telegram promotion is Phase 5.
- **D-08:** Extend `daily_scan` schema with a NEW ordered migration step (not amending `_MIGRATION_0001`) to persist per-candidate: prior-day high, prior close, SMA200, gap %, RVOL 14-day baseline, originating scan pass. Explicit typed columns, not a JSON blob.

### Claude's Discretion

- Scanner module decomposition and exact callable/CLI entrypoint shape for manual/test runs (scheduling is Phase 5).
- Exact `MoomooGateway.subscribe()` signature and `SubType.K_5M` wiring for the capped list (SIG-01) — mirror the existing gateway async `run_in_executor` pattern.
- yfinance batch-download mechanics (period/window, retry/backoff per symbol) and the ~5-thread concurrency primitive.
- RVOL-baseline edge cases (insufficient prior history for a symbol) — handle conservatively (exclude/flag).
- Exact dated-cache filename convention and snapshot-staleness warning behavior.
- Whether the schema extension is a fresh migration `0002` vs. amending `0001` — almost certainly `0002` since Phase 1 schema is shipped.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope. Intraday signal evaluation, order/position management, APScheduler service, Telegram alert delivery, and backtester remain mapped to Phases 3–6.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCAN-01 | Bot fetches the current S&P 500 constituent list as the scan universe | Wikipedia `read_html` scrape with dated cache + fallback (§Wikipedia Scrape) |
| SCAN-02 | Premarket scan filters by price >= $3 and D1/D2/D3 daily setup | Already implemented in `TrendJoinLong.passes_daily_filters()` — scanner feeds it; no reimplementation needed |
| SCAN-03 | 14-day RVOL baseline using only prior completed trading days | `indicators.rvol()` already enforces `date < signal_date`; scanner must pass correct history window (§RVOL Baseline) |
| SCAN-04 | Scan runs only on NYSE trading days (holiday/half-day aware) | `pandas_market_calendars` `valid_days()` + `schedule()` + `early_closes()` (§NYSE Calendar Gate) |
| SCAN-05 | Daily scan is idempotent — re-running same day does not duplicate | `UNIQUE(scan_date, code)` + `INSERT … ON CONFLICT DO UPDATE` upsert pattern (§Migration 0002 + Upsert) |
| SCAN-06 | Scan data from yfinance with ticker normalization + bounded concurrency; partial failures surfaced | `yf.download(group_by="ticker", threads=True)` + `yfinance.shared._ERRORS` + 10% degradation gate (§yfinance Download) |
| SCAN-07 | Scan re-runs intraday; each pass updates watchlist idempotently | Re-scan entrypoint reuses same upsert path; D-04 "protect active candidate" guard (§Re-scan Merge) |
| SCAN-08 | Persisted watchlist capped at top-20 by gap % | Sort by gap_pct descending; take top 20 before persistence (§Watchlist Cap) |
| SIG-01 | Bot subscribes live 5m bars only for capped watchlist — never full universe | `subscribe(code_list, [SubType.K_5M])` in async `run_in_executor` wrapper; quota = 1 slot per symbol (§K_5M Subscription) |
</phase_requirements>

---

## Summary

Phase 2 builds the bot's first end-to-end data path: Universe fetch → daily-bar download → D1/D2/D3 filter pass → top-20 watchlist persist → 5m subscription wiring. All four main sub-problems have clear, verified solutions.

**Universe source** (D-01): `pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]` returns the first table, with the ticker column named `"Symbol"`. Dot-to-dash normalization (`BRK.B` → `BRK-B`) is required for Yahoo Finance compatibility; `BF.B` is the other common case. The dated cache (`data/sp500_YYYY-MM-DD.csv`) prevents a network failure from killing the scan.

**Daily-bar download** (SCAN-06): `yf.download(tickers=list_of_yf_symbols, period="1y", group_by="ticker", auto_adjust=True, threads=True)` fetches enough history for SMA200 + RVOL baseline in one call. Partial failures are detected via `yfinance.shared._ERRORS` after the call; counting `len(shared._ERRORS)` against the universe size implements the D-06 10% threshold gate. The `threads=True` default uses `cpu_count() * 2` threads, which is more than the ~5-thread bound in the CONTEXT — the planner should specify `threads=5` to stay within the D-01 bounded-concurrency constraint.

**Schema migration 0002** (D-08): Add `ALTER TABLE daily_scan ADD COLUMN` for the six new fields, then set `PRAGMA user_version = 2`. The `INSERT … ON CONFLICT(scan_date, code) DO UPDATE SET …` upsert covers both first-time inserts and re-scan rank updates while honoring the idempotency requirement (D-05). The "protect active candidate" rule (D-04) is enforced in application logic before issuing the upsert — not in SQL.

**K_5M subscription** (SIG-01): `quote_ctx.subscribe(code_list, [SubType.K_5M], subscribe_push=True)` is the synchronous call. The gateway wraps this in `run_in_executor` to keep it non-blocking for the asyncio caller. Each subscribed symbol consumes one quota slot; the top-20 cap keeps usage well within the 100-slot minimum tier.

**Primary recommendation:** Use `yf.download()` with `threads=5`, `period="1y"`, `group_by="ticker"`, detect failures via `yfinance.shared._ERRORS`, enforce the 10% degradation gate before persisting, and write a NEW migration 0002 for the richer `daily_scan` schema.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| S&P 500 constituent list | `bot/scanner/universe.py` (file fetch) | `data/sp500_*.csv` (cache) | Pure file I/O; no broker, no asyncio |
| Daily-bar download | `bot/scanner/fetcher.py` | yfinance (external) | CPU-bound pandas work; sync, wrapped in executor if called from async |
| Daily filter evaluation | `bot/strategy/trend_join_long.py` | `bot/strategy/indicators.py` | Already implemented in Phase 1; scanner feeds it |
| Watchlist persistence | `bot/state/store.py` (SQLite) | `bot/state/migrations.py` | Existing durable store; migration 0002 extends schema |
| NYSE calendar gate | `bot/scanner/calendar.py` | `pandas_market_calendars` | Thin wrapper around mcal API; determines is_trading_day + close_time |
| K_5M subscription | `bot/gateway/gateway.py` | moomoo SDK | Broker I/O; async run_in_executor pattern matches existing gateway methods |
| Degradation alert | `bot/scanner/scanner.py` | structlog | Phase 2 = log only; Telegram promotion is Phase 5 |

---

## Standard Stack

### Core (Phase 2 additions)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `yfinance` | 1.4.1 | Daily OHLCV bars for ~500 S&P 500 symbols | Only free daily-bar source that handles batch multi-ticker download; already in project design (SCAN-06) |
| `pandas-market-calendars` | 5.4.0 | NYSE trading-day / holiday / early-close detection | Already in STACK.md; `valid_days()` + `schedule()` + `early_closes()` are exact API needed |

**Existing (Phase 1 — already in requirements.txt, no change):**

| Library | Version | Purpose |
|---------|---------|---------|
| `pandas` | >=2.0,<4.0 | DataFrame for daily bars, indicator computation |
| `moomoo-api` | >=10.4.6408,<11.0 | K_5M subscription via `SubType.K_5M` (confirmed installed) |
| `numpy` | 2.5.0 | Indicator math (transitive dep of pandas; already pinned) |
| `structlog` | 26.1.0 | Degradation event logging (D-07) |

### Installation (additions only)

```bash
pip install "yfinance==1.4.1" "pandas-market-calendars==5.4.0"
```

Add to `requirements.txt`:
```
yfinance==1.4.1
pandas-market-calendars==5.4.0
```

### Version Verification

```bash
pip index versions yfinance         # confirms 1.4.1 current
pip index versions pandas-market-calendars  # confirms 5.4.0 current
```

[VERIFIED: PyPI registry — yfinance 1.4.1, pandas-market-calendars 5.4.0]

---

## Package Legitimacy Audit

> slopcheck was not available at research time. Packages verified via PyPI and official sources. All packages below are tagged [ASSUMED] per protocol — planner must gate each install behind a checkpoint:human-verify task.

| Package | Registry | Source Repo | Notes | Disposition |
|---------|----------|-------------|-------|-------------|
| `yfinance` | PyPI | github.com/ranaroussi/yfinance | Widely used, 14k+ GitHub stars, active issue tracker | Approved [ASSUMED] |
| `pandas-market-calendars` | PyPI | github.com/rsheftel/pandas_market_calendars | Referenced in project STACK.md; officially documented in readthedocs | Approved [ASSUMED] |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none
*slopcheck unavailable at research time — planner must add checkpoint:human-verify before each install.*

---

## Architecture Patterns

### System Architecture Diagram

```
[Wikipedia HTML]                    [yfinance / Yahoo Finance]
       |                                        |
       v                                        v
 universe.py                            fetcher.py
 pd.read_html()                     yf.download(tickers,
 -> S&P 500 symbols                   period="1y",
 -> normalize dots->dashes            group_by="ticker",
 -> dated cache write                 threads=5)
       |                                        |
       |       symbol_list                      | per-ticker DataFrame
       +-------------------> scanner.py <-------+
                                  |
                           calendar.py
                           is_trading_day()?
                           get_close_time()?
                                  |
                     YES — proceed
                                  |
                     for each symbol:
                       build daily_data df (2+ rows)
                       compute sma200 from close series
                       compute rvol baseline (prior 14 days)
                       call TrendJoinLong.passes_daily_filters()
                                  |
                     D-06 gate: len(failures) >= 10% -> abort + log
                                  |
                     sort by gap_pct DESC, take top 20
                                  |
                          store.py (SQLite)
                     INSERT daily_scan
                     ON CONFLICT(scan_date, code) DO UPDATE
                     (migration 0002 columns)
                                  |
                     D-04: filter out already-subscribed codes
                                  |
                          gateway.py
                     subscribe(new_codes, [SubType.K_5M])
```

### Recommended Project Structure

```
bot/
├── scanner/
│   ├── __init__.py
│   ├── scanner.py        # run_daily_scan(), run_intraday_rescan() entrypoints
│   ├── universe.py       # fetch_sp500_symbols(), with dated cache + fallback
│   ├── fetcher.py        # download_daily_bars() — yf.download wrapper
│   └── calendar.py       # is_trading_day(), get_market_close_et()
tests/
└── scanner/
    ├── __init__.py
    ├── test_universe.py   # Wikipedia scrape + cache fallback
    ├── test_fetcher.py    # yf.download partial failure, normalization
    ├── test_scanner.py    # D1/D2/D3 filter pass, top-20 cap, idempotency
    └── test_calendar.py   # holiday rejection, half-day close time
```

---

## Research Area 1: S&P 500 Universe Fetch (SCAN-01 / D-01)

### Wikipedia Scrape

**URL:** `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies`

**API:**
```python
# Source: confirmed via WebSearch + community examples (2024)
tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
df = tables[0]          # table index 0 is the constituents table
symbols = df["Symbol"].tolist()   # column named "Symbol"
```

[CITED: multiple 2024 sources confirm table index 0, column name "Symbol"]

**Typical result size:** ~503 symbols (index occasionally has >500 during transitions). Treat as dynamic; never hardcode count.

### Ticker Normalization: Wikipedia → yfinance → Moomoo

| Step | From | To | Example | Rule |
|------|------|----|---------|------|
| Wikipedia → yfinance | `BRK.B` | `BRK-B` | Berkshire B | Replace `.` with `-` in ticker symbols containing a dot |
| Wikipedia → yfinance | `BF.B` | `BF-B` | Brown-Forman B | Same rule — applies to any `.` in the ticker |
| yfinance → Moomoo | `BRK-B` | `US.BRK-B` | Any US equity | Prepend `US.` prefix |

**Implementation:**
```python
def wiki_to_yfinance(symbol: str) -> str:
    """Replace dots with dashes for Yahoo Finance compatibility."""
    return symbol.replace(".", "-")

def yfinance_to_moomoo(yf_symbol: str) -> str:
    """Add US. prefix for Moomoo code format."""
    return f"US.{yf_symbol}"
```

[VERIFIED: Yahoo Finance URL for BRK-B confirmed at finance.yahoo.com/quote/BRK-B/]

**Known special cases:** Only `.` needs replacement. No other substitutions confirmed. `BRK.B` and `BF.B` are the two known cases in the S&P 500 with dots.

### Dated Cache Pattern (D-03)

```python
# Filename carries snapshot date — staleness visible at a glance
cache_path = f"data/sp500_{date.today().isoformat()}.csv"   # e.g. data/sp500_2026-06-23.csv

def fetch_sp500_symbols() -> list[str]:
    """Scrape Wikipedia; write dated cache; fall back to most recent cache on failure."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        symbols = tables[0]["Symbol"].tolist()
        # write dated cache
        pd.DataFrame({"symbol": symbols}).to_csv(cache_path, index=False)
        return symbols
    except Exception:
        # Fall back to most recent dated cache
        cache_files = sorted(glob.glob("data/sp500_*.csv"), reverse=True)
        if cache_files:
            logger.warning("wikipedia_scrape_failed_using_cache", cache=cache_files[0])
            return pd.read_csv(cache_files[0])["symbol"].tolist()
        raise RuntimeError("No cached S&P 500 symbol list available and scrape failed")
```

[ASSUMED — cache fallback pattern is standard; no official documentation for this specific implementation]

**Failure modes:**
- Network timeout → fall back to cache (D-01)
- `requests.exceptions.ConnectionError` → fall back to cache
- Wikipedia changes table structure (column rename) → `KeyError` on `"Symbol"` → log + fall back
- Cache directory does not exist → create `data/` on first run (already created by Phase 1 DB path logic)

---

## Research Area 2: yfinance Daily-Bar Download (SCAN-06)

### Recommended API

```python
import yfinance as yf
import yfinance.shared as shared

# Clear the errors dict before download (shared state between calls)
shared._ERRORS.clear()

data = yf.download(
    tickers=yf_symbols,       # list of yfinance-format symbols (BRK-B, etc.)
    period="1y",              # 1 year of daily bars — enough for SMA200 (200 trading days)
                              # and RVOL 14-day baseline; avoids start/end date complexity
    interval="1d",            # daily bars
    group_by="ticker",        # returned as data["BRK-B"] per ticker
    auto_adjust=True,         # adjust for splits/dividends
    threads=5,                # D-01: bounded concurrency ~5 threads (override default cpu*2)
    progress=False,           # suppress progress bar in production
)
```

[VERIFIED: yf.download signature confirmed via official docs at ranaroussi.github.io/yfinance]
[ASSUMED — `threads=5` integer value overrides the `True`/auto default; PyPI docs confirm `threads` is a parameter but exact integer behavior not explicitly confirmed via docs in this session]

**Important note on yfinance version 1.x:** In version 1.x, `yf.download()` returns a MultiIndex DataFrame by default (`multi_level_index=True`). Access per-ticker data as `data["AAPL"]` when `group_by="ticker"`.

### Period Justification for SMA200 + RVOL

- **SMA200** requires 200 trading days of close prices. One calendar year (~252 trading days) provides adequate buffer, including around holidays.
- **RVOL 14-day baseline** requires prior 14 completed trading days. Included in the 1-year window.
- **Prior-day high / prior close** for D1/D2/D3: immediately available as `df.iloc[-2]` (yesterday) and `df.iloc[-1]` (today's open, used for gap calculation).
- **Use `period="1y"`** over explicit `start`/`end` to avoid off-by-one errors with DST and market holiday boundaries. The library handles the window correctly.

### Partial Failure Detection (D-06)

```python
# After yf.download() returns:
failed = set(shared._ERRORS.keys())       # tickers that failed
succeeded = set(yf_symbols) - failed
failure_rate = len(failed) / len(yf_symbols)

if failure_rate >= 0.10:                  # D-06: >= 10% threshold
    # Abort and surface degradation event
    logger.error(
        "scan_aborted_data_degradation",
        failed_count=len(failed),
        total=len(yf_symbols),
        failure_rate_pct=round(failure_rate * 100, 1),
        failed_sample=list(failed)[:10],
    )
    raise ScanDegradationError(f"Data degradation: {len(failed)}/{len(yf_symbols)} symbols failed")
elif failed:
    # < 10%: proceed with partial watchlist, warn
    logger.warning(
        "scan_partial_data",
        failed_count=len(failed),
        total=len(yf_symbols),
        failed_symbols=list(failed),
    )
```

[VERIFIED: `yfinance.shared._ERRORS` is a dict with ticker keys and error-message string values — confirmed via GitHub Discussion #2083]

**How per-ticker failures surface in the returned DataFrame:**
- If a ticker fails entirely, it will be absent from `data` (no column for that ticker) OR all its rows will be NaN.
- Check: `ticker_df = data.get(symbol)` — if `ticker_df is None` or `ticker_df.dropna().empty`, treat as failed.
- `shared._ERRORS` is the authoritative failure count; NaN checking is a secondary validation.

### Accessing Per-Ticker Data

```python
# With group_by="ticker", access data as:
for symbol in yf_symbols:
    if symbol in shared._ERRORS:
        continue   # already counted as failed
    try:
        ticker_df = data[symbol]         # DataFrame with columns: Open, High, Low, Close, Volume
        if ticker_df is None or ticker_df.dropna(how="all").empty:
            continue   # no data returned
        # Now ticker_df.index is DatetimeIndex (daily)
        # ticker_df.columns: ['Open', 'High', 'Low', 'Close', 'Volume']
    except (KeyError, TypeError):
        continue
```

[ASSUMED — MultiIndex access pattern is widely documented; exact behavior for missing tickers in 1.x not directly tested in this session]

---

## Research Area 3: RVOL Baseline — No Look-Ahead (SCAN-03 / PITFALLS #4)

### What "RVOL Baseline" Means in Phase 2 vs Phase 3

| Phase | RVOL Component | Computed From | Formula |
|-------|---------------|---------------|---------|
| Phase 2 (this phase) | **RVOL 14-day baseline denominator** | Prior 14 completed daily sessions (total daily volume) | `mean(volume for prior 14 NYSE trading days)` |
| Phase 3 (future) | **Intraday RVOL ratio** | Today's cumulative volume at time T vs baseline at time T (time-of-day normalized) | `today_volume_at_T / mean(volume_at_T for prior 14 sessions)` |

**Phase 2 responsibility:** Compute and persist the baseline only. Phase 3 uses it as a denominator.

### Exact Computation Using Existing `indicators.rvol()`

```python
# indicators.rvol() signature (already implemented in Phase 1):
# rvol(volume_frame, signal_date, lookback_days, today_volume) -> Optional[float]
#
# For Phase 2 RVOL baseline (denominator only), we use a simplified call:
# Pass today_volume=1.0 and note we only care about the denominator

# Build volume_frame from yfinance daily data:
import pandas as pd
from bot.strategy.indicators import rvol, sma
from bot.safety.et_helpers import now_et

scan_date = pd.Timestamp(now_et().date())   # today's date in ET

# volume_frame from yfinance ticker_df:
volume_frame = pd.DataFrame({
    "date": ticker_df.index.tz_localize(None) if ticker_df.index.tz else ticker_df.index,
    "volume": ticker_df["Volume"].values,
})

# RVOL baseline denominator only (today_volume=1.0 yields the 1/mean ratio
# but Phase 2 only needs mean — derive directly):
prior = volume_frame[volume_frame["date"] < scan_date].copy()
prior = prior.sort_values("date", ascending=False).head(14)
if len(prior) == 14:
    rvol_baseline = float(prior["volume"].mean())
else:
    rvol_baseline = None   # insufficient history — exclude this symbol (conservative)
```

**Or equivalently:** Call `indicators.rvol(volume_frame, scan_date, 14, today_volume=prior["volume"].mean())` which will return 1.0 when today_volume equals the baseline — but it is cleaner to compute the baseline mean directly rather than back-solving from the RVOL ratio.

**No-look-ahead guard (PITFALLS #4):**
- `ticker_df.index` from `yf.download(period="1y")` will include today's bar if the market is open at scan time.
- Filter: `volume_frame[volume_frame["date"] < scan_date]` — the strict `<` excludes today, matching the existing `indicators.rvol()` implementation.
- Verified: `indicators.rvol()` already uses `volume_frame[volume_frame["date"] < signal_date]` (Phase 1, 01-03).

### Edge Case: Insufficient History

Conservative approach (per CONTEXT.md): If a symbol has fewer than 14 prior completed trading days in the yfinance history, set `rvol_baseline = None` and **exclude the symbol from the watchlist** (do not pass it to `passes_daily_filters()`). Log a warning per excluded symbol.

---

## Research Area 4: NYSE Calendar Gate (SCAN-04)

### API (pandas-market-calendars 5.4.0)

```python
import pandas_market_calendars as mcal
from datetime import date
import pandas as pd

nyse = mcal.get_calendar("NYSE")

def is_trading_day(dt: date) -> bool:
    """Return True if dt is a NYSE trading day (not a holiday or weekend)."""
    # [VERIFIED: pandas-market-calendars readthedocs.io usage guide]
    valid = nyse.valid_days(start_date=dt, end_date=dt)
    return len(valid) > 0

def get_market_close_et(dt: date) -> str:
    """Return the NYSE market close time in ET as HH:MM string.

    Handles early closes (half-days): Black Friday, Christmas Eve, July 3rd, etc.
    Returns "13:00" on half-days, "16:00" on normal days.
    """
    # [VERIFIED: pandas-market-calendars readthedocs.io — schedule() returns
    #  DataFrame with market_open and market_close columns in UTC]
    sched = nyse.schedule(start_date=dt, end_date=dt)
    if sched.empty:
        raise ValueError(f"{dt} is not a trading day")
    # market_close is UTC-aware; convert to ET
    close_utc = sched.iloc[0]["market_close"]
    close_et = close_utc.tz_convert("America/New_York")
    return close_et.strftime("%H:%M")

def get_prior_n_trading_days(ref_date: date, n: int) -> list:
    """Return list of the n most recent completed NYSE trading days before ref_date."""
    # Look back 2*n calendar days to be safe (handles holidays, long weekends)
    start = pd.Timestamp(ref_date) - pd.Timedelta(days=n * 2 + 10)
    valid = nyse.valid_days(start_date=start.date(), end_date=ref_date)
    # Exclude ref_date itself (completed days only)
    prior = [d for d in valid if d.date() < ref_date]
    return prior[-n:]
```

[VERIFIED: `valid_days()`, `schedule()`, `early_closes()` confirmed via pandas-market-calendars readthedocs.io usage guide]
[VERIFIED: `early_closes()` requires a `schedule=` DataFrame argument, not just a date]

**Half-day detection:**
```python
def is_early_close(dt: date) -> bool:
    sched = nyse.schedule(start_date=dt, end_date=dt)
    if sched.empty:
        return False
    early_df = nyse.early_closes(schedule=sched)
    return not early_df.empty
```

**Force-close time on half-days:** On a half-day, NYSE closes at 13:00 ET. `get_market_close_et()` above returns the correct time by reading from `schedule()`. The Phase 4 force-close (15:51 logic) must use this to compute `close_time - 9 minutes`.

### Test Approach for Success Criterion #4

```python
# Known NYSE holidays (use fixed dates that are always holidays):
# 2024-01-01 (New Year's Day — Monday)
# 2023-11-23 (Thanksgiving)
# 2023-07-04 (Independence Day)
# Known half-day: 2023-11-24 (Black Friday after Thanksgiving)
def test_holiday_rejection():
    assert not is_trading_day(date(2024, 1, 1))

def test_half_day_close_time():
    close = get_market_close_et(date(2023, 11, 24))
    assert close == "13:00"
```

---

## Research Area 5: Schema Migration 0002 (D-08)

### Migration Design

The existing `daily_scan` table (migration 0001) has: `scan_date, code, gap_pct, rank, created_at, UNIQUE(scan_date, code)`.

Migration 0002 adds the six new columns required by D-08:

```python
# bot/state/migrations.py additions

_MIGRATION_0002 = """
ALTER TABLE daily_scan ADD COLUMN prior_day_high   REAL;
ALTER TABLE daily_scan ADD COLUMN prior_close      REAL;
ALTER TABLE daily_scan ADD COLUMN sma200           REAL;
ALTER TABLE daily_scan ADD COLUMN rvol_baseline    REAL;
ALTER TABLE daily_scan ADD COLUMN scan_pass        TEXT;
"""

# Note: gap_pct is already in migration 0001; no duplicate needed.
# "prior close" is semantically different from gap_pct (gap_pct is a ratio).
# scan_pass: TEXT like "premarket" | "intraday_1" | "intraday_2" etc.

MIGRATIONS = [
    _MIGRATION_0001,
    _MIGRATION_0002,   # adds rich context columns to daily_scan
]

CURRENT_VERSION = 2
```

[VERIFIED: SQLite `ALTER TABLE ADD COLUMN` is the correct way to add columns to existing tables; `IF NOT EXISTS` is not supported on ADD COLUMN — the migration runner's `user_version` gate prevents re-application]
[ASSUMED — SQLite ALTER TABLE ADD COLUMN cannot add NOT NULL columns without a default; all new columns are nullable REAL/TEXT, which is correct here]

**Why ALTER TABLE, not CREATE TABLE IF NOT EXISTS?**
- The `daily_scan` table already exists in migration 0001.
- SQLite's `CREATE TABLE IF NOT EXISTS` is idempotent but does not add new columns to existing tables.
- `ALTER TABLE ADD COLUMN` adds columns to existing tables without data loss.
- Since the migration runner uses `PRAGMA user_version` to gate migrations, migration 0002 only runs once.

### Idempotent Upsert Pattern (D-05)

For the first premarket scan of the day (INSERT new row):
```sql
INSERT INTO daily_scan
    (scan_date, code, gap_pct, rank, created_at,
     prior_day_high, prior_close, sma200, rvol_baseline, scan_pass)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(scan_date, code) DO UPDATE SET
    gap_pct       = excluded.gap_pct,
    rank          = excluded.rank,
    prior_day_high = excluded.prior_day_high,
    prior_close   = excluded.prior_close,
    sma200        = excluded.sma200,
    rvol_baseline = excluded.rvol_baseline,
    scan_pass     = excluded.scan_pass;
```

[VERIFIED: SQLite `INSERT ... ON CONFLICT(col) DO UPDATE SET` syntax is standard since SQLite 3.24 (2018); stdlib sqlite3 exposes this via `conn.execute()`]

For intraday re-scans, this same upsert updates the rank and gap_pct for existing codes, and inserts new qualifying codes — implementing D-05 (idempotent) and D-04 (re-rank, don't delete).

### Protecting Active Candidates (D-04)

The SQL upsert alone cannot protect active candidates — the protection is in **application logic** before issuing the upsert. The scanner must:

1. Query currently subscribed codes: `active_codes = gateway.get_subscribed_codes()` (or track in memory).
2. When re-ranking for the top-20 cap: keep all `active_codes` in the top-20 list regardless of rank; fill remaining slots with highest gap-% non-active candidates.
3. **Never DELETE rows from `daily_scan` for the current scan date** — upsert only (rank update or new row). Eviction happens only by rank dropping below 20 AND not being in `active_codes`.

The gateway needs a `get_subscribed_codes()` method or an in-memory set maintained by the scanner. The planner should design this as an in-memory set in the scanner module (simpler than a DB query).

---

## Research Area 6: MoomooGateway.subscribe() — SIG-01

### SubType.K_5M Confirmation

```bash
$ python3 -c "from moomoo import SubType; print([x for x in dir(SubType) if 'K_' in x])"
['K_10M', 'K_120M', 'K_15M', 'K_180M', 'K_1M', 'K_240M', 'K_30M', 'K_3M', 'K_5M', 'K_60M', 'K_DAY', ...]
```

[VERIFIED: `SubType.K_5M` confirmed present in installed `moomoo-api` package]

### Subscribe Call (from skills reference)

```python
# From skills/moomooapi/scripts/subscribe/subscribe.py (reference pattern):
ret, msg = ctx.subscribe(
    code_list,           # list of "US.AAPL" format codes
    [SubType.K_5M],
    is_first_push=True,
    subscribe_push=True,
    extended_time=False,
    session=Session.NONE,
)
```

[VERIFIED: Subscribe signature confirmed via `skills/moomooapi/scripts/subscribe/subscribe.py`]
[VERIFIED: Official Moomoo API docs at openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html confirm `(code_list, subtype_list, is_first_push, subscribe_push, ...)` returns `(ret, err_message)`]

### Gateway Method Implementation Pattern

Following the existing `run_in_executor` pattern in `bot/gateway/gateway.py`:

```python
async def subscribe(self, codes: list, subtypes: list = None) -> None:
    """Subscribe to real-time candlestick pushes for the given codes.

    Only subscribes the capped top-20 list (SIG-01) — never the full universe.
    Each code+subtype pair consumes 1 subscription quota slot.

    Parameters:
        codes: List of Moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
               Must be <= 20 items to stay within quota bounds (SIG-01).
        subtypes: List of SubType values. Defaults to [SubType.K_5M].

    Raises:
        GatewayError: if subscribe() returns non-RET_OK.
    """
    from moomoo import SubType, Session
    if subtypes is None:
        subtypes = [SubType.K_5M]

    loop = asyncio.get_event_loop()

    def _subscribe_blocking():
        ret, msg = self._quote_ctx.subscribe(
            codes,
            subtypes,
            is_first_push=True,
            subscribe_push=True,
            extended_time=False,
            session=Session.NONE,
        )
        _check_ret(ret, msg, "subscribe")

    await loop.run_in_executor(None, _subscribe_blocking)
    _logger.info("subscribed_k5m", codes=codes, count=len(codes))
```

### Quota Considerations (API_LIMITS.md)

| Tier | Subscription Quota | Phase 2 Usage |
|------|--------------------|---------------|
| Basic (< 10K HKD) | 100 slots | 20 codes × 1 SubType = 20 slots (20% of quota) |
| Standard (>= 10K HKD) | 300 slots | 20 codes = 7% of quota |

**Top-20 cap (SCAN-08) directly addresses Pitfall #11** — 20 × 1 SubType = 20 slots, well within even the basic 100-slot tier.

**60-second hold:** After subscribing, the SDK requires at least 60 seconds before unsubscribing. Phase 2 only subscribes; unsubscription logic is deferred to Phase 4/5 (EOD force-close).

[VERIFIED: Quota tiers and 60-second hold confirmed in `skills/moomooapi/docs/API_LIMITS.md`]

---

## Research Area 7: Module Decomposition and Entrypoints

### Scanner Package Layout

The scanner is a `bot/scanner/` subpackage (CONTEXT.md §Integration Points, consistent with Phase 1 D-01 layout).

**Public entrypoints** (callable for manual/test runs; scheduled calls are Phase 5):

```python
# bot/scanner/scanner.py

def run_daily_scan(
    store: StateStore,
    gateway: MoomooGateway,
    cfg: StrategyConfig,
    scan_date: date = None,    # defaults to today_et()
) -> list[str]:
    """Run the premarket scan and return the top-20 Moomoo code list.

    Sequence:
      1. Calendar gate — abort if not a trading day
      2. Fetch S&P 500 universe (Wikipedia + cache)
      3. Download daily bars via yfinance (period="1y", threads=5)
      4. D-06 degradation check
      5. For each symbol: compute SMA200, RVOL baseline, gap%; call passes_daily_filters()
      6. Sort by gap% DESC, take top 20
      7. Persist to daily_scan via upsert
      8. Subscribe top-20 via gateway.subscribe() [SIG-01]
      9. Return list of Moomoo codes
    """

async def run_intraday_rescan(
    store: StateStore,
    gateway: MoomooGateway,
    cfg: StrategyConfig,
    active_codes: set,          # codes currently subscribed / have forming positions
    scan_pass: str = "intraday",
) -> list[str]:
    """Run an intraday re-scan and update the watchlist idempotently.

    D-04: active_codes are never evicted regardless of rank.
    D-05: uses same upsert path as run_daily_scan.
    Returns updated top-20 Moomoo code list.
    """
```

**CLI entrypoint** (for manual testing):
```python
# bot/scanner/scanner.py __main__ block or a thin bot/main_scan.py:
if __name__ == "__main__":
    from bot.state.store import StateStore
    from bot.gateway.gateway import MoomooGateway, get_gateway_config
    from bot.config.loader import load_strategy_config
    store = StateStore().open()
    gw = MoomooGateway(get_gateway_config())
    gw.connect()
    cfg = load_strategy_config()
    codes = run_daily_scan(store, gw, cfg)
    print(f"Watchlist ({len(codes)} symbols): {codes}")
    gw.close()
    store.close()
```

Note: `run_daily_scan()` can be synchronous (no asyncio needed until the gateway subscribe call). The async gateway method can be called with `asyncio.run()` from the sync scanner or the scanner can become async. The planner should make `run_daily_scan` sync-with-async-subscribe using `asyncio.run(gateway.subscribe(codes))` or a coroutine wrapper — mirror the existing gateway pattern.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| NYSE holiday / half-day detection | Custom holiday list in code | `pandas_market_calendars.get_calendar("NYSE")` | Exchange calendars change; manual lists go stale; `valid_days()` handles observed holidays (Christmas on Monday, etc.) |
| Daily bar download for 500 symbols | Per-symbol HTTP calls in a manual loop | `yf.download(tickers=list, period="1y", group_by="ticker", threads=5)` | Batches + threads built in; `shared._ERRORS` provides failure detection |
| Ticker symbol normalization edge cases | Custom regex | `.replace(".", "-")` — simple and complete for the current S&P 500 | Only `.` needs replacing; no other special characters documented in current index |
| SQLite upsert / conflict resolution | Custom SELECT-then-INSERT-or-UPDATE logic | `INSERT ... ON CONFLICT(scan_date, code) DO UPDATE SET ...` | Atomic in SQLite; avoids TOCTOU race condition in concurrent re-scan passes |
| RVOL computation with look-ahead guard | Custom date filtering | `indicators.rvol()` (already implemented in Phase 1) | Look-ahead guard is already in `indicators.rvol()` — reuse it; don't reimplement |
| SMA200 computation | Rolling window from scratch | `indicators.sma(ticker_df["Close"], 200)` (already implemented in Phase 1) | Already tested in Phase 1 |

**Key insight:** Phase 2's strategy computation is almost entirely delegated to Phase 1 code. The scanner's job is data acquisition + persistence + orchestration, not strategy logic.

---

## Common Pitfalls

### Pitfall 1: yfinance `threads=True` Uses cpu_count() × 2 — Exceeds D-01 Bound

**What goes wrong:** The D-01 locked decision specifies "bounded concurrency ~5 threads." The yfinance default `threads=True` scales to `cpu_count() * 2` automatically, which on a modern MacBook (8–12 cores) would yield 16–24 threads — more than the intended bound and potentially causing higher rate-limit exposure.

**How to avoid:** Pass `threads=5` (an integer) to `yf.download()`. The `threads` parameter accepts both a boolean (`True` = auto) and an integer (explicit count).

[ASSUMED — integer value for `threads` accepted by yfinance; documented as "how many threads to use for mass downloading" but exact integer semantics not explicitly tested in this session]

### Pitfall 2: yfinance `shared._ERRORS` is Module-Level Mutable State

**What goes wrong:** If `shared._ERRORS` is not cleared before each `yf.download()` call, errors from a previous download (different scan day, different process) accumulate and inflate the failure count.

**How to avoid:** Call `shared._ERRORS.clear()` immediately before calling `yf.download()`.

[VERIFIED: `shared._ERRORS` is a module-level dict — confirmed via GitHub Discussion #2083]

### Pitfall 3: RVOL Baseline Includes Today's yfinance Bar (Look-Ahead)

**What goes wrong:** `yf.download(period="1y")` returns today's partial daily bar if run during market hours. If the scanner includes today in the RVOL denominator, the denominator uses an incomplete volume — look-ahead bias.

**How to avoid:** Filter `volume_frame[volume_frame["date"] < scan_date]` before computing the mean. The existing `indicators.rvol()` already does this — use it.

**Warning sign:** RVOL baseline computed at 9:30 ET differs from RVOL baseline at 16:00 ET for the same symbol on the same day.

### Pitfall 4: Wikipedia Table Structure Changes

**What goes wrong:** The Wikipedia S&P 500 page has changed its table structure before. If the column `"Symbol"` is renamed or the table index changes from 0, `df["Symbol"]` raises `KeyError` and the scan aborts.

**How to avoid:** On `KeyError`, fall back to the dated cache (D-01 fallback already handles this). Add a validation check: `assert "Symbol" in df.columns`.

### Pitfall 5: Moomoo Code vs yfinance Symbol Mismatch

**What goes wrong:** The scanner persists `daily_scan.code` with values like `US.BRK-B` (Moomoo format). If the wrong format is persisted (e.g., `BRK.B` without the `US.` prefix), Phase 3's subscription lookup will fail to find the record.

**How to avoid:** Normalize at ingest time. Persist `code` as the Moomoo `US.` format; internally track the yfinance symbol for bar lookup. A mapping dict `{moomoo_code: yf_symbol}` makes this explicit.

### Pitfall 6: D-04 Active-Candidate Protection Not Enforced at SQL Level

**What goes wrong:** The upsert SQL blindly updates the rank of all candidates. If a formerly top-20 candidate drops to rank 21 on re-scan but has an active subscription, the bot may later unsubscribe it (when subscription pruning is added) even though it was being evaluated.

**How to avoid:** Track `active_codes` as an in-memory set in the scanner module (populated from the gateway's subscription list). Before finalizing the top-20 list, union `active_codes` with the newly-ranked top-20. The SQL upsert then operates on this protected list.

---

## Code Examples

### Example 1: Wikipedia Scrape with Dot-to-Dash Normalization

```python
# Source: community consensus + Yahoo Finance URL verification
import pandas as pd

def _fetch_from_wikipedia() -> list[str]:
    """Returns list of yfinance-format symbols (dots replaced with dashes)."""
    tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    df = tables[0]
    symbols = df["Symbol"].tolist()
    # Normalize: BRK.B -> BRK-B, BF.B -> BF-B
    return [s.replace(".", "-") for s in symbols]
```

### Example 2: yfinance Batch Download with Failure Detection

```python
# Source: yfinance official docs + GitHub Discussion #2083
import yfinance as yf
import yfinance.shared as shared

def download_daily_bars(yf_symbols: list[str], threads: int = 5) -> tuple:
    """Download 1-year daily bars. Returns (data_df, failed_set)."""
    shared._ERRORS.clear()
    data = yf.download(
        tickers=yf_symbols,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=threads,
        progress=False,
    )
    failed = set(shared._ERRORS.keys())
    return data, failed
```

### Example 3: NYSE Trading Day Check

```python
# Source: pandas-market-calendars readthedocs.io
import pandas_market_calendars as mcal
from datetime import date

_nyse = mcal.get_calendar("NYSE")

def is_trading_day(dt: date) -> bool:
    valid = _nyse.valid_days(start_date=dt, end_date=dt)
    return len(valid) > 0

def get_close_time_et(dt: date) -> str:
    sched = _nyse.schedule(start_date=dt, end_date=dt)
    close_utc = sched.iloc[0]["market_close"]
    return close_utc.tz_convert("America/New_York").strftime("%H:%M")
```

### Example 4: Migration 0002 — ALTER TABLE

```python
# bot/state/migrations.py addition
_MIGRATION_0002 = """
ALTER TABLE daily_scan ADD COLUMN prior_day_high   REAL;
ALTER TABLE daily_scan ADD COLUMN prior_close      REAL;
ALTER TABLE daily_scan ADD COLUMN sma200           REAL;
ALTER TABLE daily_scan ADD COLUMN rvol_baseline    REAL;
ALTER TABLE daily_scan ADD COLUMN scan_pass        TEXT;
"""
```

### Example 5: Idempotent Upsert

```python
# Source: SQLite official upsert docs (sqlite.org/lang_UPSERT.html)
conn.execute("""
    INSERT INTO daily_scan
        (scan_date, code, gap_pct, rank, created_at,
         prior_day_high, prior_close, sma200, rvol_baseline, scan_pass)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(scan_date, code) DO UPDATE SET
        gap_pct        = excluded.gap_pct,
        rank           = excluded.rank,
        prior_day_high = excluded.prior_day_high,
        prior_close    = excluded.prior_close,
        sma200         = excluded.sma200,
        rvol_baseline  = excluded.rvol_baseline,
        scan_pass      = excluded.scan_pass
""", (scan_date, code, gap_pct, rank, created_at,
      prior_day_high, prior_close, sma200, rvol_baseline, scan_pass))
```

### Example 6: MoomooGateway.subscribe() Async Wrapper

```python
# Mirror of existing run_in_executor pattern in bot/gateway/gateway.py
import asyncio
from moomoo import SubType, Session, RET_OK

async def subscribe(self, codes: list, subtypes: list = None) -> None:
    """Async wrapper: subscribe codes to K_5M pushes (non-blocking)."""
    if subtypes is None:
        subtypes = [SubType.K_5M]
    loop = asyncio.get_event_loop()

    def _subscribe_blocking():
        ret, msg = self._quote_ctx.subscribe(
            codes, subtypes,
            is_first_push=True,
            subscribe_push=True,
            extended_time=False,
            session=Session.NONE,
        )
        if ret != RET_OK:
            raise GatewayError(f"subscribe failed: ret={ret}, msg={msg}")

    await loop.run_in_executor(None, _subscribe_blocking)
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `get_plate_stock("US.S&P500")` for constituent list | Wikipedia `pd.read_html()` + dated cache | Free, no broker quota, survives network failure via cache |
| Single-ticker `Ticker.history()` loop | `yf.download(group_by="ticker", threads=N)` batch | ~5–10x faster for 500 symbols; one HTTP session |
| Explicit `shared._ERRORS` not checked | Check `len(shared._ERRORS)` post-download | Enables D-06 10% degradation gate |
| `INSERT OR REPLACE` (delete + insert) | `INSERT ... ON CONFLICT DO UPDATE SET` (upsert) | Preserves row data not in the UPDATE clause; atomic; avoids losing `created_at` |
| Manual counting of prior trading days (timedelta loops) | `pandas_market_calendars.valid_days()` | Handles observed holidays, DST; one-line check |

**Deprecated/outdated:**
- `yf.download(group_by="column")`: Returns MultiIndex with column as top level; harder to access per-ticker; prefer `group_by="ticker"` for clarity.
- `shared._ERRORS` pattern: This is a workaround for yfinance's lack of a formal `raise_errors` parameter for bulk downloads. It is the current best practice but may change in future yfinance versions.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `threads=5` (integer) overrides the `True`/auto default in `yf.download()` | Standard Stack, Research Area 2 | If integer is not accepted, must implement manual `concurrent.futures.ThreadPoolExecutor` with 5 workers around per-symbol `Ticker.history()` calls |
| A2 | Wikipedia S&P 500 table is at `tables[0]` and has a `"Symbol"` column | Research Area 1 | If table index or column name changes, scrape fails; cache fallback handles gracefully |
| A3 | `yfinance.shared._ERRORS` exists and accumulates per-download errors in version 1.4.1 | Research Area 2 | If removed/renamed in 1.4.1, must detect failures via NaN-checking per ticker column |
| A4 | `yf.download(group_by="ticker")["SYMBOL"]` returns a DataFrame with columns Open/High/Low/Close/Volume | Research Area 2 | If column names differ in 1.4.1 (e.g., case change), all downstream indicator calls break |
| A5 | Active candidate set can be tracked in-memory by the scanner (no DB query needed for D-04) | Research Area 7 | If bot restarts mid-session, in-memory set is lost; need to persist subscribed codes to DB or query from gateway |

---

## Open Questions (RESOLVED)

1. **yfinance column name casing in 1.4.1**
   - What we know: historical docs show `Open, High, Low, Close, Volume` (capitalized)
   - What's unclear: whether 1.4.1 changed to lowercase (some issues report case changes)
   - Recommendation: In `fetcher.py`, normalize column names to lowercase on read: `ticker_df.columns = ticker_df.columns.str.lower()`
   - **RESOLVED:** Plan 02-01 T2 normalizes columns to lowercase on read (defensive; works for either casing).

2. **In-memory active-code set survives restart?**
   - What we know: Phase 2 stores subscribed codes in memory; Phase 5 adds the long-running service
   - What's unclear: If the bot restarts mid-session (between premarket scan and intraday re-scan), the active-code set is lost
   - Recommendation: Planner should add a `subscribed_codes` column to `daily_scan` (boolean, default False) or a separate table — so on restart the scanner can query `WHERE subscribed_codes = 1` to rebuild the set. Alternatively, defer this edge case to Phase 5 (service restart is a Phase 5 concern).
   - **RESOLVED:** Deferred to Phase 5 (service restart-recovery is a Phase 5 concern per CONTEXT `<deferred>`). Phase 2 passes `active_codes` as an in-memory set parameter to the re-scan entrypoint (02-03 T3).

3. **Does yfinance 1.4.1 include today's partial bar in `period="1y"`?**
   - What we know: Documented behavior is that `period="1y"` fetches to the current date
   - What's unclear: Whether today's partial bar (during market hours) is included or if the API returns only completed bars
   - Recommendation: Always apply the `date < scan_date` filter regardless; this is defensive and costs nothing.
   - **RESOLVED:** Plan 02-02 T2 applies the strict `date < scan_date` filter unconditionally — correct whether or not the partial bar is present.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.x | All | ✓ | System Python | — |
| `moomoo-api` | SIG-01, SubType.K_5M | ✓ | 10.7.6708 (installed, SubType.K_5M confirmed) | — |
| `pandas` | indicator computation, yfinance DataFrames | ✓ | present in requirements.txt (>=2.0,<4.0) | — |
| `yfinance` | SCAN-06 | ✗ not yet installed | 1.4.1 (PyPI latest) | No fallback — required dependency |
| `pandas-market-calendars` | SCAN-04 | ✗ not yet installed | 5.4.0 (PyPI latest) | No fallback — required dependency |
| `lxml` or `html5lib` | `pd.read_html()` (Wikipedia scrape) | Unknown | — | `beautifulsoup4` as alternative parser |
| `requests` | `pd.read_html()` HTTP layer | ✓ (transitive dep of yfinance) | — | — |
| `structlog` | D-07 degradation logging | ✓ | 26.1.0 (in requirements.txt) | — |

**Missing dependencies with no fallback:**
- `yfinance` — required for SCAN-06; must be installed
- `pandas-market-calendars` — required for SCAN-04; must be installed
- `lxml` or `html5lib` — required for `pd.read_html()`; check if available; `yfinance` may pull in `lxml` as a transitive dependency

**Missing dependencies with fallback:**
- None identified in this phase

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest >= 8.0 (already in requirements-dev.txt) |
| Config file | None detected — uses pytest defaults (discovery from project root) |
| Quick run command | `pytest tests/scanner/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAN-01 | Wikipedia scrape returns ~500 symbols | unit (mock HTTP) | `pytest tests/scanner/test_universe.py -x` | ❌ Wave 0 |
| SCAN-01 | Cache fallback when scrape fails | unit (mock) | `pytest tests/scanner/test_universe.py::test_cache_fallback -x` | ❌ Wave 0 |
| SCAN-02 | D1/D2/D3 filters applied via `passes_daily_filters()` | unit (synthetic df) | `pytest tests/scanner/test_scanner.py::test_daily_filters -x` | ❌ Wave 0 |
| SCAN-03 | RVOL baseline excludes today (date < scan_date) | unit (synthetic) | `pytest tests/scanner/test_scanner.py::test_rvol_no_lookahead -x` | ❌ Wave 0 |
| SCAN-04 | Holiday date produces no output and no error | unit (mcal mock) | `pytest tests/scanner/test_calendar.py::test_holiday_rejection -x` | ❌ Wave 0 |
| SCAN-04 | Half-day returns correct early-close time | unit (mcal) | `pytest tests/scanner/test_calendar.py::test_half_day_close -x` | ❌ Wave 0 |
| SCAN-05 | Running scanner twice same day: same row count | unit (tmp DB) | `pytest tests/scanner/test_scanner.py::test_idempotency -x` | ❌ Wave 0 |
| SCAN-06 | Partial failure detected via shared._ERRORS | unit (mock yf) | `pytest tests/scanner/test_fetcher.py::test_degradation_detection -x` | ❌ Wave 0 |
| SCAN-06 | >= 10% failure triggers abort | unit (mock yf) | `pytest tests/scanner/test_fetcher.py::test_10pct_abort -x` | ❌ Wave 0 |
| SCAN-07 | Re-scan updates rank; does not duplicate | unit (tmp DB) | `pytest tests/scanner/test_scanner.py::test_rescan_idempotent -x` | ❌ Wave 0 |
| SCAN-07 | Active candidate not evicted by re-rank | unit (mock) | `pytest tests/scanner/test_scanner.py::test_active_candidate_protected -x` | ❌ Wave 0 |
| SCAN-08 | > 20 passing candidates → exactly 20 stored | unit (synthetic) | `pytest tests/scanner/test_scanner.py::test_top20_cap -x` | ❌ Wave 0 |
| SIG-01 | subscribe() called only for capped list | unit (mock gateway) | `pytest tests/scanner/test_scanner.py::test_subscribe_top20_only -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/scanner/ -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/scanner/__init__.py` — package init
- [ ] `tests/scanner/test_universe.py` — SCAN-01 scrape + cache fallback (mock HTTP)
- [ ] `tests/scanner/test_fetcher.py` — SCAN-06 batch download + failure detection (mock yf.download)
- [ ] `tests/scanner/test_scanner.py` — SCAN-02/03/05/07/08 + SIG-01 (synthetic DataFrames, mock DB)
- [ ] `tests/scanner/test_calendar.py` — SCAN-04 (uses real mcal — holiday and half-day assertions)

---

## Security Domain

> `security_enforcement: true` (from .planning/config.json); ASVS Level 1 applies.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Phase 2 has no user-facing authentication |
| V3 Session Management | No | No user sessions in scanner |
| V4 Access Control | No | Single-user bot; no multi-tenancy |
| V5 Input Validation | Yes | Validate Wikipedia table structure before use; validate yfinance data shapes (NaN, empty df) |
| V6 Cryptography | No | No encryption in scanner |

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Wikipedia scrape response tampered in transit | Tampering | HTTPS enforced by `pd.read_html()`; validate `Symbol` column exists before use |
| yfinance returns malformed / spoofed data | Tampering | Validate DataFrame shape, non-null close prices, and positive prices before computing indicators |
| `data/` cache file manipulated on disk | Tampering | Cache is gitignored + operator-controlled machine; 0600 permissions on write (consistent with `atomic_write_json` pattern) |
| Credential logging | Information Disclosure | Scanner never handles credentials; no risk in this component |

---

## Sources

### Primary (HIGH confidence)

- `skills/moomooapi/scripts/subscribe/subscribe.py` — confirmed `subscribe(code_list, subtypes, is_first_push, subscribe_push, session)` signature and K_5M in argparse choices
- `bot/strategy/indicators.py` (Phase 1 codebase) — `rvol()` strict date < signal_date cutoff; `sma()` interface
- `bot/strategy/trend_join_long.py` (Phase 1 codebase) — `passes_daily_filters(code, daily_data, sma200)` interface
- `bot/state/migrations.py` (Phase 1 codebase) — PRAGMA user_version migration pattern; existing `daily_scan` schema
- `skills/moomooapi/docs/API_LIMITS.md` — subscription quota tiers (100/300/1000), 60-second hold rule
- `openapi.moomoo.com/moomoo-api-doc/en/quote/update-kl.html` — `SubType.K_5M` confirmed; callback DataFrame fields
- `openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html` — `subscribe()` return signature `(ret, err_message)`
- Python install check: `from moomoo import SubType; dir(SubType)` — `K_5M` confirmed present
- `pandas-market-calendars.readthedocs.io/en/latest/usage.html` — `valid_days()`, `schedule()`, `early_closes()` API
- `pypi.org/pypi/yfinance/json` — version 1.4.1 confirmed latest stable
- `pypi.org/pypi/pandas-market-calendars/json` — version 5.4.0 confirmed latest stable; Python 3.10+ required
- `ranaroussi.github.io/yfinance/reference/api/yfinance.download.html` — `tickers`, `period`, `interval`, `group_by`, `threads`, `auto_adjust` parameters confirmed
- `sqlite.org/lang_UPSERT.html` — `INSERT ... ON CONFLICT DO UPDATE SET` syntax, `excluded` keyword

### Secondary (MEDIUM confidence)

- GitHub Discussion #2083 (ranaroussi/yfinance) — `yfinance.shared._ERRORS` structure confirmed; dict with ticker keys and error-string values
- GitHub Issue #2614 (ranaroussi/yfinance) — rate limiting behavior with many tickers; threads=True may exacerbate rate limits
- deepwiki.com/ranaroussi/yfinance/4.2-working-with-multiple-tickers — threading uses `multitasking` library; `cpu_count() * 2` default
- Multiple 2024 community sources — Wikipedia table index 0, column "Symbol"
- Yahoo Finance URL `finance.yahoo.com/quote/BRK-B/` — confirms `BRK-B` (dash) is the Yahoo/yfinance format

### Tertiary (LOW confidence)

- None — all critical claims verified via primary or secondary sources

---

## Metadata

**Confidence breakdown:**
- yfinance download mechanics: MEDIUM — API confirmed but `threads=5` integer behavior assumed (A1)
- Wikipedia scrape: HIGH — URL, table index, column name confirmed via multiple 2024 sources
- pandas-market-calendars: HIGH — API confirmed via readthedocs
- Migration 0002 pattern: HIGH — `PRAGMA user_version` pattern inherited from Phase 1 codebase
- SubType.K_5M: HIGH — confirmed via Python runtime + official docs
- Failure detection via `shared._ERRORS`: MEDIUM — confirmed via GitHub discussion, behavior may change in future versions

**Research date:** 2026-06-23
**Valid until:** 2026-07-23 (yfinance API changes frequently; recheck `shared._ERRORS` API if library is updated)
