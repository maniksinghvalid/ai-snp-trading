---
phase: "02-premarket-scanner"
plan: "00"
subsystem: "scanner"
tags: [dependencies, test-stubs, wave-0, yfinance, pandas-market-calendars]
dependency_graph:
  requires:
    - "01-04-SUMMARY.md (Phase 1 foundation: pytest tree, conftest fixtures, structlog, ET helpers)"
  provides:
    - "yfinance==1.4.1 pinned and installed"
    - "pandas-market-calendars==5.4.0 pinned and installed"
    - "bot/scanner/ importable package"
    - "21 Wave 0 test stubs in tests/scanner/ (one slot per SCAN-01..08 / SIG-01 behavior)"
  affects:
    - "02-01-PLAN.md (implements universe fetch + fetcher against these stubs)"
    - "02-02-PLAN.md (implements scanner core against test_scanner.py stubs)"
    - "02-03-PLAN.md (implements calendar gate + subscribe against remaining stubs)"
tech_stack:
  added:
    - "yfinance==1.4.1 (read-only daily bar data for scanner + backtester)"
    - "pandas-market-calendars==5.4.0 (NYSE holiday/half-day calendar gate)"
    - "lxml (HTML parser required by pd.read_html for Wikipedia scrape; yfinance does not pull it transitively on Python 3.14)"
  patterns:
    - "Skipped Wave 0 stub pattern: @pytest.mark.skip + pytest.fail body keeps suite green with collectible named test slots"
key_files:
  created:
    - "requirements.txt (Phase 2 block appended)"
    - "bot/scanner/__init__.py (empty package init)"
    - "tests/scanner/__init__.py (empty package init)"
    - "tests/scanner/test_universe.py (4 stubs — SCAN-01)"
    - "tests/scanner/test_fetcher.py (6 stubs — SCAN-06)"
    - "tests/scanner/test_scanner.py (7 stubs — SCAN-02/03/05/07/08/SIG-01)"
    - "tests/scanner/test_calendar.py (4 stubs — SCAN-04)"
  modified: []
decisions:
  - "lxml added to requirements.txt explicitly — yfinance 1.4.1 does not pull lxml transitively on Python 3.14 / Homebrew, and pd.read_html (SCAN-01 Wikipedia scrape) requires an HTML parser"
  - "Task 1 package-legitimacy gate (T-02-SC) satisfied by operator pre-approval: yfinance PyPI -> github.com/ranaroussi/yfinance; pandas-market-calendars -> github.com/rsheftel/pandas_market_calendars; both name-checked as legitimate with exact version pins"
  - "pip --break-system-packages flag used because project Python is Homebrew-managed (same location as existing moomoo-api, pandas, numpy — consistent with Phase 1 install method)"
metrics:
  duration: "~5 minutes"
  completed: "2026-06-23"
  tasks_completed: 3
  tasks_total: 3
  files_changed: 7
---

# Phase 2 Plan 00: Dependency Foundation and Wave 0 Test Stubs Summary

**One-liner:** Pinned and installed yfinance==1.4.1 + pandas-market-calendars==5.4.0 + lxml, created bot/scanner package, and scaffolded 21 skipped Wave 0 test stubs (one named slot per SCAN-01..08 / SIG-01 behavior).

---

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Package legitimacy checkpoint (T-02-SC) — pre-approved | (pre-approved — no commit) | — |
| 2 | Add and install yfinance + pandas-market-calendars | 45d41e6 | requirements.txt |
| 3 | Create bot/scanner package + tests/scanner Wave 0 stubs | ced0efb | bot/scanner/__init__.py, tests/scanner/__init__.py, test_universe.py, test_fetcher.py, test_scanner.py, test_calendar.py |

---

## Verification Results

- `python3 -c "import yfinance, pandas_market_calendars, lxml; print('ok')"` → ok
- `grep -c '^yfinance==1.4.1$' requirements.txt` → 1
- `grep -c '^pandas-market-calendars==5.4.0$' requirements.txt` → 1
- `python3 -m pytest tests/scanner/ --collect-only -q` → **21 tests collected**
- `python3 -m pytest tests/scanner/ -q` → **21 skipped** (0 failed, suite green)
- `python3 -m pytest tests/ -q` → **217 passed, 21 skipped** (no Phase 1 regression)

### Test Stub Coverage Map

| Requirement | Test Function | File |
|-------------|---------------|------|
| SCAN-01 | test_wikipedia_scrape_returns_symbols | test_universe.py |
| SCAN-01 | test_dot_to_dash_normalization | test_universe.py |
| SCAN-01 | test_cache_fallback_on_scrape_failure | test_universe.py |
| SCAN-01 | test_raises_when_no_cache_and_scrape_fails | test_universe.py |
| SCAN-06 | test_download_returns_per_ticker_frames | test_fetcher.py |
| SCAN-06 | test_lowercase_column_normalization | test_fetcher.py |
| SCAN-06 | test_partial_failure_detected_via_shared_errors | test_fetcher.py |
| SCAN-06 | test_10pct_failure_raises_ScanDegradationError | test_fetcher.py |
| SCAN-06 | test_under_10pct_warns_and_proceeds | test_fetcher.py |
| SCAN-06 | test_shared_errors_cleared_before_download | test_fetcher.py |
| SCAN-02 | test_daily_filters | test_scanner.py |
| SCAN-03 | test_rvol_no_lookahead | test_scanner.py |
| SCAN-05 | test_idempotency | test_scanner.py |
| SCAN-08 | test_top20_cap | test_scanner.py |
| SCAN-07 | test_rescan_idempotent | test_scanner.py |
| SCAN-07/D-04 | test_active_candidate_protected | test_scanner.py |
| SIG-01 | test_subscribe_top20_only | test_scanner.py |
| SCAN-04 | test_holiday_rejection | test_calendar.py |
| SCAN-04 | test_half_day_close | test_calendar.py |
| SCAN-04 | test_normal_day_close | test_calendar.py |
| SCAN-03/04 | test_prior_n_trading_days_excludes_ref_date | test_calendar.py |

---

## Deviations from Plan

### Auto-added: lxml

**Rule 2 — Missing critical dependency for pd.read_html**
- **Found during:** Task 2 (post-install verification)
- **Issue:** `python3 -c "import lxml"` failed after installing yfinance and pandas-market-calendars. The plan notes "yfinance pulls in lxml transitively — confirm lxml is importable after install, and if it does not, add lxml to requirements.txt and install it too". It did not pull transitively on Python 3.14 / Homebrew.
- **Fix:** Installed lxml separately and added `lxml` (unpinned — lxml maintains stable APIs across minor versions) to requirements.txt under the Phase 2 block.
- **Files modified:** requirements.txt
- **Commit:** 45d41e6

### Note: pip --break-system-packages flag

Homebrew-managed Python 3.14 blocks bare `pip install`. Used `--break-system-packages` to install to the same `/opt/homebrew/lib/python3.14/site-packages/` path where Phase 1 packages (moomoo-api, pandas, numpy, structlog) already live — consistent with the existing project setup. No virtual environment exists; this matches the implicit project convention.

---

## Known Stubs

All 21 test functions in tests/scanner/ are intentional Wave 0 stubs. Each body calls `pytest.fail("Wave 0 stub")` under a `@pytest.mark.skip` decorator. These are tracked for plans 02-01 through 02-03 to implement. No stubs exist in production code.

---

## Threat Flags

None. This plan only modifies requirements.txt and creates empty package inits / test stubs. No new network endpoints, auth paths, file access patterns, or schema changes were introduced.

---

## Self-Check: PASSED

- `bot/scanner/__init__.py` FOUND
- `tests/scanner/__init__.py` FOUND
- `tests/scanner/test_universe.py` FOUND
- `tests/scanner/test_fetcher.py` FOUND
- `tests/scanner/test_scanner.py` FOUND
- `tests/scanner/test_calendar.py` FOUND
- Commit 45d41e6 FOUND (requirements.txt)
- Commit ced0efb FOUND (package + stubs)
- `pytest tests/ -q` → 217 passed, 21 skipped
