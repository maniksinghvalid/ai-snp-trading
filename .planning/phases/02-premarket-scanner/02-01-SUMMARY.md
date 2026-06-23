---
phase: "02-premarket-scanner"
plan: "01"
subsystem: "scanner"
tags: [universe, fetcher, calendar, yfinance, wikipedia-scrape, degradation-gate, nyse-calendar, tdd]
dependency_graph:
  requires:
    - "02-00-SUMMARY.md (yfinance + pandas-market-calendars installed, bot/scanner package, 21 Wave 0 stubs)"
    - "01-04-SUMMARY.md (structlog logger, ET helpers, audit_log)"
  provides:
    - "bot/scanner/universe.py — fetch_sp500_symbols, wiki_to_yfinance, yfinance_to_moomoo"
    - "bot/scanner/fetcher.py — download_daily_bars, get_ticker_frame, ScanDegradationError"
    - "bot/scanner/calendar.py — is_trading_day, get_market_close_et, get_prior_n_trading_days"
    - "14 passing unit tests (4 universe + 6 fetcher + 4 calendar)"
  affects:
    - "02-02-PLAN.md (scanner orchestrator consumes all three modules)"
    - "02-03-PLAN.md (remaining 7 stubs for scanner/gateway integration)"
tech_stack:
  added: []
  patterns:
    - "Wikipedia pd.read_html scrape with dated CSV cache and fallback to newest cache on exception"
    - "yf.download(threads=5) with shared._ERRORS.clear() before call and failure-rate gate"
    - "pandas-market-calendars module-level NYSE singleton with tz_convert to ET for half-day detection"
    - "ScanDegradationError raised at >=10%; structlog.error + append_audit durable surfacing (D-07)"
    - "TDD RED/GREEN/REFACTOR cycle for all three modules"
key_files:
  created:
    - "bot/scanner/universe.py"
    - "bot/scanner/fetcher.py"
    - "bot/scanner/calendar.py"
  modified:
    - "tests/scanner/test_universe.py (4 stubs replaced with real tests)"
    - "tests/scanner/test_fetcher.py (6 stubs replaced with real tests)"
    - "tests/scanner/test_calendar.py (4 stubs replaced with real tests)"
decisions:
  - "Test for shared._ERRORS.clear() uses real yfinance.shared._ERRORS dict (not mock replacement) — patch('yfinance.shared._ERRORS', dict) replaces the object but our code calls .clear() on the module-level binding which wipes the mock; instead tests pre-populate the real dict and have mock_download populate it back after clear"
  - "wiki_to_yfinance uses simple str.replace('.', '-') — only substitution needed for current S&P 500 tickers (BRK.B, BF.B); no regex needed"
  - "calendar.py uses _nyse.schedule().iloc[0]['market_close'].tz_convert('America/New_York') — directly reads market_close from schedule rather than checking early_closes() separately; handles both 13:00 and 16:00 correctly via the same code path"
  - "get_prior_n_trading_days returns list of Timestamp objects (not date); caller must use .date() to compare; test accounts for this"
metrics:
  duration: "~8 minutes"
  completed: "2026-06-23"
  tasks_completed: 3
  tasks_total: 3
  files_changed: 6
---

# Phase 2 Plan 01: Scanner Data Acquisition Modules Summary

**One-liner:** Wikipedia scrape + dated-cache fallback (universe.py), yfinance batch download with threads=5 and 10% degradation gate + D-07 audit surfacing (fetcher.py), and NYSE calendar half-day/holiday gate via pandas-market-calendars singleton (calendar.py) — all TDD, raise-not-exit, 14 tests green.

---

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | bot/scanner/universe.py — Wikipedia scrape + dated cache + fallback (SCAN-01) | f98a5e9 | bot/scanner/universe.py, tests/scanner/test_universe.py |
| 2 | bot/scanner/fetcher.py — yfinance batch download + 10% degradation gate (SCAN-06, D-06/D-07) | 0eba081 | bot/scanner/fetcher.py, tests/scanner/test_fetcher.py |
| 3 | bot/scanner/calendar.py — NYSE trading-day + half-day gate (SCAN-04) | 45aa8fc | bot/scanner/calendar.py, tests/scanner/test_calendar.py |

---

## Verification Results

- `pytest tests/scanner/test_universe.py -q` → 4 passed
- `pytest tests/scanner/test_fetcher.py -q` → 6 passed
- `pytest tests/scanner/test_calendar.py -q` → 4 passed
- `pytest tests/scanner/ -q` → 14 passed, 7 skipped (scanner.py stubs for 02-02/02-03)
- `pytest tests/ -q` → 231 passed, 7 skipped (no Phase 1 regression)
- No sys.exit calls in any bot/scanner/ module (verified via AST walk)
- `grep -c 'append_audit' bot/scanner/fetcher.py` → 2 (import + D-07 abort call)
- `grep -c 'threads=5' bot/scanner/fetcher.py` → 1 (Pitfall #1 enforced)
- `grep -c 'threads=True' bot/scanner/fetcher.py` → 0 (Pitfall #1 verified absent)

### Success Criteria Check

| Criterion | Status |
|-----------|--------|
| fetch_sp500_symbols returns normalized symbols + writes dated cache + falls back correctly | PASS |
| download_daily_bars batches at threads=5, clears shared._ERRORS first, enforces 10% gate | PASS |
| is_trading_day / get_market_close_et correct on holiday, half-day, and normal day | PASS |
| All 14 new tests pass, full suite 231 passed, 7 skipped | PASS |

---

## Deviations from Plan

### Test Approach Adjustment — shared._ERRORS Patching

**[Rule 1 - Bug] `patch("yfinance.shared._ERRORS", dict)` wipes the mock before assertions**

- **Found during:** Task 2 test authoring
- **Issue:** The initial test approach used `patch("yfinance.shared._ERRORS", failed_tickers)` where `failed_tickers` was a plain dict prepopulated with failure entries. However, `download_daily_bars` correctly calls `shared._ERRORS.clear()` BEFORE calling `yf.download()` — which cleared the mock dict and left it empty, so the post-download `set(shared._ERRORS.keys())` was empty and tests failed.
- **Fix:** Tests now work with the real `yfinance.shared._ERRORS` dict directly: pre-clear it, then have `mock_download` side_effect populate it with failures (simulating what yfinance's internals do during a real download). A try/finally block restores original state. This accurately models the real production code path.
- **Files modified:** tests/scanner/test_fetcher.py
- **Impact:** Test intent unchanged (same behavior verified); only test mechanics adjusted for accuracy.

---

## Known Stubs

7 stubs remain in tests/scanner/test_scanner.py for plans 02-02 and 02-03:
- test_daily_filters (SCAN-02)
- test_rvol_no_lookahead (SCAN-03)
- test_idempotency (SCAN-05)
- test_top20_cap (SCAN-08)
- test_rescan_idempotent (SCAN-07)
- test_active_candidate_protected (SCAN-07/D-04)
- test_subscribe_top20_only (SIG-01)

These are intentional Wave 0 stubs, tracked for 02-02 (scanner orchestrator) and 02-03 (subscribe integration).

---

## Threat Flags

None. All three modules are data-acquisition utilities with no new network endpoints, auth paths, or schema changes. The Wikipedia HTTPS scrape and yfinance batch download are addressed by the threat mitigations already in the plan's threat model (T-02-02, T-02-03, T-02-04):
- Wikipedia "Symbol" column validation → falls through to cache (T-02-02 mitigated)
- yfinance all-NaN frames → get_ticker_frame() returns None (T-02-03 mitigated)
- >= 10% yfinance failure → ScanDegradationError + audit log (T-02-04 mitigated)

---

## Self-Check: PASSED

- `bot/scanner/universe.py` FOUND
- `bot/scanner/fetcher.py` FOUND
- `bot/scanner/calendar.py` FOUND
- Commit f98a5e9 FOUND (universe.py)
- Commit 0eba081 FOUND (fetcher.py)
- Commit 45aa8fc FOUND (calendar.py)
- `pytest tests/ -q` → 231 passed, 7 skipped
