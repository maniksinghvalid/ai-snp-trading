---
phase: 02-premarket-scanner
verified: 2026-06-23T00:00:00Z
status: human_needed
score: 7/7 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Live Wikipedia scrape — run scanner with network on; confirm ~500 symbols returned and a dated cache file written under data/"
    expected: "~500 yfinance-format symbols returned; data/sp500_YYYY-MM-DD.csv created"
    why_human: "Unit tests mock pd.read_html; live network call and real Wikipedia table structure cannot be confirmed programmatically without network access"
  - test: "Live yfinance batch download — run a real scan during/after market hours against the real universe"
    expected: "Bars returned for most symbols, degradation ratio logged, watchlist non-empty, partial-failure warning emitted when applicable"
    why_human: "Network-dependent and rate-limited; unit tests mock yf.download"
  - test: "Real K_5M subscription — with OpenD running, run a daily scan and confirm exactly the top-20 codes were subscribed"
    expected: "get_subscription returns exactly the top-20 moomoo codes; no quota error; no full-universe subscription"
    why_human: "Requires a live OpenD instance and moomoo account; unit tests inject a mock gateway"
---

# Phase 2: Premarket Scanner Verification Report

**Phase Goal:** The bot can run a daily premarket scan (and intraday re-scans) against the full S&P 500 universe using yfinance daily-bar data, apply all daily filters, and persist an idempotent, top-20-capped watchlist in StateStore.
**Verified:** 2026-06-23
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Scanner on a NYSE trading day fetches S&P 500 list, applies D1/D2/D3 filters, writes to StateStore | VERIFIED | `test_daily_filters`, `test_daily_filters_config_driven` in TestDailyFilters pass; `_evaluate_symbol` calls `TrendJoinLong(cfg).passes_daily_filters()`; `_persist_watchlist` uses `INSERT ... ON CONFLICT DO UPDATE` into `daily_scan` |
| 2 | RVOL 14-day baseline uses only completed prior trading days (date < scan_date, no look-ahead) | VERIFIED | `test_rvol_no_lookahead` passes; `_evaluate_symbol` computes `prior_mask = frame.index < scan_ts`; `prior_sorted` uses only `prior_mask` rows; CR-02 fix ensures `today_row` is date-resolved, not positional |
| 3 | Scanner twice same day does not duplicate watchlist; intraday re-scan is also idempotent | VERIFIED | `test_idempotency`, `test_rescan_idempotent` pass; `ON CONFLICT(scan_date, code) DO UPDATE` upsert in `_persist_watchlist` confirmed at scanner.py line 209 |
| 4 | Scanner refuses to run on NYSE holidays/half-days; known holiday produces no output/error | VERIFIED | `test_holiday_rejection`, `test_half_day_close`, `test_normal_day_close` in test_calendar.py pass against the real mcal NYSE calendar; `is_trading_day(date(2024,1,1))` returns False; `run_daily_scan` returns `[]` on non-trading day (TestNonTradingDay passes) |
| 5 | MoomooGateway.subscribe() called only for capped top-20 codes, never full universe | VERIFIED | `test_subscribe_top20_only` asserts `len(called_codes) == 20` with 25 passing candidates; subscribe called with `result` (top-20 list) at scanner.py line 422, not `yf_symbols`; `TestSubscribe` gateway tests pass |
| 6 | Scan data from yfinance with dot-to-dash normalization, threads=5, degradation detected | VERIFIED | `threads=5` confirmed in fetcher.py line 81; `shared._ERRORS.clear()` before download (line 73); `ScanDegradationError` raised at >= 10% failure with `append_audit` (lines 88-105); all 6 test_fetcher.py tests pass; `wiki_to_yfinance` tested in test_universe.py |
| 7 | Persisted watchlist capped at top 20 by gap %; > 20 candidates yields exactly 20 stored | VERIFIED | `test_top20_cap`: 25 symbols → exactly 20 rows in DB, rank=1 has highest gap; `passing[:_WATCHLIST_CAP]` at scanner.py line 399; `_WATCHLIST_CAP = 20` constant at line 34 |

**Score: 7/7 truths verified**

---

### Code Review Fix Confirmation (CR-01, CR-02)

The code review flagged two critical correctness defects. Both are confirmed fixed and have regression tests:

| Finding | Fix | Regression Test | Status |
|---------|-----|-----------------|--------|
| CR-01: None SMA200 coerced to 0.0 — D2 silently bypassed for < 200 sessions | `_evaluate_symbol` now returns `None` immediately when `sma200_val is None` (scanner.py lines 101-107) | `TestInsufficientHistory::test_no_sma200_symbol_excluded` — 50-row frame excluded | VERIFIED FIXED |
| CR-02: `iloc[-1]` used as "today" — diverges from no-look-ahead cutoff in premarket | `today_rows = frame.index[frame.index.normalize() == scan_ts.normalize()]`; symbol skipped if absent (scanner.py lines 127-134) | `TestTodayRowAlignment::test_premarket_no_today_bar_excluded` and `test_gap_uses_scan_date_row_not_last_positional` | VERIFIED FIXED |

Both CR-01 and CR-02 regression tests pass:
- `TestInsufficientHistory::test_no_sma200_symbol_excluded` PASSED
- `TestTodayRowAlignment::test_premarket_no_today_bar_excluded` PASSED
- `TestTodayRowAlignment::test_gap_uses_scan_date_row_not_last_positional` PASSED

### Warning Fix Confirmation (WR-01 through WR-06)

| Finding | Fix | Status |
|---------|-----|--------|
| WR-01: Evicted active codes never unsubscribed — cumulative quota leak | `_unsubscribe_evicted_codes()` called in `run_intraday_rescan` (scanner.py line 538); `MoomooGateway.unsubscribe()` added; `test_rescan_unsubscribes_evicted_active_code` passes | VERIFIED FIXED |
| WR-02: D-04 eviction silent | `active_code_evicted` warning emitted for each evicted active code (scanner.py lines 516-522); `test_rescan_logs_active_code_eviction` passes | VERIFIED FIXED |
| WR-03: Migration 0002 non-atomic multi-statement | `_migration_0002` is a callable with per-column PRAGMA table_info guard (migrations.py lines 112-122); `TestMigration0002PartialApplication` tests pass | VERIFIED FIXED |
| WR-04: `scan_partial_data` logged twice | Redundant block removed from `_compute_candidates`; fetcher is sole source (comment at scanner.py line 268); `test_scanner_does_not_relog_scan_partial_data` passes | VERIFIED FIXED |
| WR-05: `tables[0]` blind trust; unvalidated cache | `_select_constituents_table()` selects by column matching (universe.py lines 129-143); `_MIN_UNIVERSE_SIZE=400` validated in both scrape and cache paths (lines 89-122); `TestConstituentsTableSelection` and `TestUniverseSizeValidation` tests pass | VERIFIED FIXED |
| WR-06: `asyncio.run` raises inside a running event loop | `_run_coro()` detects running loop and bridges via ThreadPoolExecutor (scanner.py lines 287-310); `test_run_daily_scan_inside_running_loop_subscribes` passes | VERIFIED FIXED |

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `requirements.txt` | yfinance==1.4.1 + pandas-market-calendars==5.4.0 + lxml pins | VERIFIED | All three present; lxml unpinned (noted in IN-03 but info-only) |
| `bot/scanner/__init__.py` | Importable package init | VERIFIED | Exists; empty package marker |
| `bot/scanner/universe.py` | fetch_sp500_symbols, wiki_to_yfinance, yfinance_to_moomoo | VERIFIED | 144 lines; raises not exits; _select_constituents_table added (WR-05) |
| `bot/scanner/fetcher.py` | download_daily_bars, get_ticker_frame, ScanDegradationError | VERIFIED | 145 lines; threads=5; shared._ERRORS.clear() before download; append_audit on >= 10% failure |
| `bot/scanner/calendar.py` | is_trading_day, get_market_close_et, get_prior_n_trading_days | VERIFIED | 80 lines; module-level _nyse singleton; tz_convert to America/New_York |
| `bot/scanner/scanner.py` | run_daily_scan, run_intraday_rescan, _evaluate_symbol, _persist_watchlist | VERIFIED | 541 lines; CR-01/CR-02 fixed; _compute_candidates shared kernel; _run_coro bridge |
| `bot/state/migrations.py` | _migration_0002, CURRENT_VERSION=2 | VERIFIED | _migration_0002 callable with PRAGMA table_info guard; CURRENT_VERSION=2; user_version bumped atomically |
| `bot/gateway/gateway.py` | MoomooGateway.subscribe() async | VERIFIED | `async def subscribe` at line 301; deferred SubType/Session import; run_in_executor; _check_ret; MoomooGateway.unsubscribe() also added (WR-01) |
| `tests/scanner/test_universe.py` | 4 real tests covering SCAN-01 | VERIFIED | 4 tests pass; scrape + normalization + cache fallback + raise-on-no-cache |
| `tests/scanner/test_fetcher.py` | 6 real tests covering SCAN-06 | VERIFIED | 6 tests pass; degradation gate, threads=5, errors cleared, partial failure |
| `tests/scanner/test_scanner.py` | Tests for SCAN-02/03/05/07/08/SIG-01 | VERIFIED | Full implementation; includes CR-01/CR-02 regression tests, WR-01/02/04/06 regression tests |
| `tests/scanner/test_calendar.py` | 4 real tests covering SCAN-04 | VERIFIED | 4 tests pass against real mcal NYSE calendar |
| `tests/state/test_migrations.py` | Migration 0002 tests | VERIFIED | TestMigration0002FreshDb, TestMigration0002Idempotency, TestUpgradeFromV1Db, TestMigration0002PartialApplication — all pass |
| `tests/gateway/test_gateway.py` | TestSubscribe + TestUnsubscribe | VERIFIED | 3 subscribe + 3 unsubscribe tests pass |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `bot/scanner/universe.py` | `pandas.read_html` (Wikipedia) | `_select_constituents_table(pd.read_html(...))` | WIRED | Line 79-82; column-matching table selection |
| `bot/scanner/fetcher.py` | `yfinance.shared._ERRORS` | `shared._ERRORS.clear()` before; `set(shared._ERRORS.keys())` after | WIRED | Lines 73, 85 |
| `bot/scanner/fetcher.py` | `bot.safety.audit_log.append_audit` | D-07 durable surfacing on >= 10% failure | WIRED | Lines 19 (import), 96-101 |
| `bot/scanner/calendar.py` | `pandas_market_calendars NYSE` | Module-level `_nyse = mcal.get_calendar("NYSE")` | WIRED | Line 25 |
| `bot/scanner/scanner.py` | `TrendJoinLong.passes_daily_filters` | Per-symbol call with 2-row frame and real sma200_val | WIRED | Line 159; CR-02 2-row frame construction at lines 157-159 |
| `bot/scanner/scanner.py` | `indicators.sma` | `sma(prior_closes, 200)` for SMA200 baseline | WIRED | Line 90 |
| `bot/scanner/scanner.py` | `daily_scan (SQLite)` | `INSERT ... ON CONFLICT(scan_date, code) DO UPDATE` | WIRED | Lines 205-230 |
| `bot/scanner/scanner.py` | `MoomooGateway.subscribe` | `_run_coro(gateway.subscribe(result))` with top-20 codes | WIRED | Line 422 |
| `bot/gateway/gateway.py` | `moomoo SubType.K_5M` | Deferred import inside `subscribe()`; `subtypes = [SubType.K_5M]` | WIRED | Lines 318-321 |
| `bot/scanner/scanner.py` | `active_codes protection (D-04)` | `protected` list front-loads active codes before gap-fill | WIRED | Lines 479-491 |
| `bot/scanner/scanner.py` | `_unsubscribe_evicted_codes` | Eviction computed as `active_codes - set(result)`, unsubscribed via gateway | WIRED | Lines 332-349, 538 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `_persist_watchlist` (scanner.py) | `candidates` list | `_compute_candidates()` → `_evaluate_symbol()` for each symbol in universe | Yes — per-symbol gap_pct, sma200, rvol_baseline computed from downloaded bars | FLOWING |
| `run_daily_scan` return | `result` (top-20 codes) | `passing[:_WATCHLIST_CAP]` after sort+filter | Yes — derived from real candidate evaluation | FLOWING |
| `daily_scan` SQLite rows | `gap_pct, rank, sma200, rvol_baseline, prior_day_high, prior_close, scan_pass` | `_persist_watchlist` parameterized upsert | Yes — all values from candidate dict, no hardcoded fallbacks | FLOWING |
| `MoomooGateway.subscribe` | `codes` param | `result` list from run_daily_scan | Yes — same top-20 list returned and subscribed | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite passes | `python3 -m pytest tests/ -q` | 268 passed in 0.76s | PASS |
| CR-01 regression: no-SMA200 symbol excluded | `pytest tests/scanner/test_scanner.py::TestInsufficientHistory::test_no_sma200_symbol_excluded -v` | PASSED | PASS |
| CR-02 regression: no today bar → excluded | `pytest tests/scanner/test_scanner.py::TestTodayRowAlignment::test_premarket_no_today_bar_excluded -v` | PASSED | PASS |
| CR-02 regression: gap computed from scan_date row | `pytest tests/scanner/test_scanner.py::TestTodayRowAlignment::test_gap_uses_scan_date_row_not_last_positional -v` | PASSED | PASS |
| SIG-01: subscribe called with top-20 only | `pytest tests/scanner/test_scanner.py::TestSubscribeWiring::test_subscribe_top20_only -v` | PASSED | PASS |
| SCAN-05: idempotency (no duplicate rows) | `pytest tests/scanner/test_scanner.py::TestIdempotency -v` | PASSED | PASS |
| SCAN-08: top-20 cap enforced | `pytest tests/scanner/test_scanner.py::TestTop20Cap::test_top20_cap -v` | PASSED | PASS |
| WR-01: evicted active code unsubscribed | `pytest tests/scanner/test_scanner.py::TestIntradayRescan::test_rescan_unsubscribes_evicted_active_code -v` | PASSED | PASS |
| Migration 0002 partial-apply idempotent | `pytest tests/state/test_migrations.py::TestMigration0002PartialApplication -v` | PASSED | PASS |
| Run from inside running event loop | `pytest tests/scanner/test_scanner.py::TestRunFromRunningEventLoop -v` | PASSED | PASS |

---

### Probe Execution

No `scripts/*/tests/probe-*.sh` probe files declared or found. Step 7c: SKIPPED (no probe files in this phase).

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SCAN-01 | 02-01 | Fetch current S&P 500 constituent list as scan universe | SATISFIED | `fetch_sp500_symbols()` scrapes Wikipedia with dot-to-dash normalization; 4 tests pass |
| SCAN-02 | 02-02 | Filter by price >= $3 + D1 (above prior-day high) + D2 (prior close > SMA200) + D3 (gap >= 3%) | SATISFIED | `TrendJoinLong(cfg).passes_daily_filters()` called per symbol with config-driven thresholds; `test_daily_filters` + config-driven proof tests pass |
| SCAN-03 | 02-02 | 14-day RVOL baseline using only prior completed days (no look-ahead) | SATISFIED | `prior_mask = frame.index < scan_ts`; `test_rvol_no_lookahead` proves invariance to today's volume; CR-02 fix ensures date-resolved today_row |
| SCAN-04 | 02-01, 02-03 | Run only on NYSE trading days; holiday/half-day aware | SATISFIED | `is_trading_day()` via pandas-market-calendars; non-trading-day guard returns `[]`; `test_holiday_rejection`, `test_half_day_close`, `TestNonTradingDay` pass |
| SCAN-05 | 02-02 | Daily scan is idempotent (no duplicate watchlist on re-run) | SATISFIED | `ON CONFLICT(scan_date, code) DO UPDATE` upsert; `test_idempotency` confirms same row count on second run |
| SCAN-06 | 02-01 | Daily bars from yfinance, not Moomoo, to avoid broker quota | SATISFIED | `yf.download(threads=5, group_by="ticker")` in fetcher.py; `ScanDegradationError` + `append_audit` on >= 10% failure |
| SCAN-07 | 02-03 | Intraday re-scans update watchlist idempotently; active candidates protected | SATISFIED | `run_intraday_rescan()` with D-04 active_codes protection; `test_rescan_idempotent`, `test_active_candidate_protected` pass |
| SCAN-08 | 02-02, 02-03 | Persisted watchlist capped at top 20 by gap % | SATISFIED | `passing[:_WATCHLIST_CAP]` where `_WATCHLIST_CAP = 20`; `test_top20_cap` confirms exactly 20 rows with rank=1 at highest gap |
| SIG-01 | 02-03 | Subscribe only capped top-20 list — never full universe | SATISFIED | `gateway.subscribe(result)` where `result` is the 20-element list; `test_subscribe_top20_only` asserts `len(called_codes) == 20` with 25 candidates |

All 9 Phase 2 requirements: SATISFIED.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `requirements.txt` | — | `lxml` is unpinned while all other deps are pinned (IN-03 from review) | Info | Potential parsing behavior change on major lxml release; not a correctness defect |
| `requirements.txt` | — | `numpy==2.5.0` pinned but environment runs 2.4.6 (IN-02 from review) | Info | Environment/pin divergence; functional since numpy 2.x is stable |
| `bot/scanner/scanner.py` | 115-117 | Dead defensive branch (`if len(prior_sorted) < cfg.rvol_lookback_days: return None` — already checked above, IN-01 from review) | Info | Harmless misleading dead code; no behavioral impact |

No TBD, FIXME, or XXX markers found in Phase 2 production files. No stub implementations. No hardcoded strategy literals in scanner.py (grep confirms 0 matches for literals >= 3.0, >= 2.0, lookback = 14). All three anti-patterns above are Info-level from the code review and do not affect goal achievement.

---

### Human Verification Required

The following behaviors require a live environment (network + OpenD) to confirm. All automated tests pass, and the implementation is substantive and wired. These items are sourced from 02-VALIDATION.md Manual-Only Verifications, unchanged because they cannot be confirmed without external dependencies.

#### 1. Live S&P 500 Universe Scrape

**Test:** Run scanner once with network enabled; inspect console/log output
**Expected:** `fetch_sp500_symbols()` returns ~500 yfinance-format symbols; a `data/sp500_YYYY-MM-DD.csv` cache file is created under the project directory
**Why human:** Unit tests mock `pd.read_html`; the real Wikipedia page structure and the ~500-symbol count cannot be confirmed programmatically without live network access

#### 2. Live yfinance Batch Download

**Test:** Run a full scan during or after market hours; observe logs for degradation/partial-data events
**Expected:** `download_daily_bars` returns bars for most symbols; any failed symbols are counted; if < 10% failed, `scan_partial_data` warning is logged; if >= 10%, `scan_aborted_data_degradation` is logged with an audit entry and `ScanDegradationError` is raised; watchlist is non-empty when data is good
**Why human:** Network-dependent and rate-limited by Yahoo Finance; unit tests inject a mock `yf.download` return value

#### 3. Real K_5M Subscription Against OpenD

**Test:** With OpenD running and logged in, run `run_daily_scan` through the bot's main entry point or a test script; check the subscription status via `get_subscription` on the quote context
**Expected:** Exactly the top-20 moomoo codes (US.XXXX format) are subscribed for K_5M; no quota error; the full ~500-symbol universe is NOT subscribed
**Why human:** Requires a live OpenD instance, a moomoo SIMULATE account, and quota verification; unit tests inject a mock MoomooGateway with an AsyncMock subscribe

---

### Gaps Summary

No gaps. All 7 ROADMAP success criteria are verified in the codebase. All 9 requirement IDs (SCAN-01 through SCAN-08, SIG-01) are satisfied with substantive implementations and passing tests. All 8 Critical + Warning findings from the code review are confirmed fixed with regression tests. The 3 human verification items above are the only outstanding items, and they require live-environment confirmation, not code changes.

**Test suite result:** 268 passed, 0 failed, 0 skipped (run: `python3 -m pytest tests/ -q`)

The phase goal is achieved: the codebase can run a daily premarket scan and intraday re-scans against the full S&P 500 universe using yfinance daily-bar data, applies all daily filters with no-look-ahead correctness, and persists an idempotent top-20-capped watchlist in StateStore with broker subscription wired.

---

_Verified: 2026-06-23_
_Verifier: Claude (gsd-verifier)_
