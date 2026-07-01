---
status: complete
phase: 02-premarket-scanner
source: [02-VERIFICATION.md]
started: 2026-06-23T23:20:00Z
updated: 2026-06-23T23:42:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Live Wikipedia S&P 500 scrape (SCAN-01)
expected: fetch_sp500_symbols() returns ~500 symbols, creates data/sp500_YYYY-MM-DD.csv, normalizes BRK.B→BRK-B / BF.B→BF-B, and falls back to newest cache on scrape failure.
result: pass
note: "Initially failed (HTTP 403, no User-Agent). Fixed in commit 3810e60: urllib fetch with User-Agent + io.StringIO(html). Re-run live: 503 symbols, BRK-B/BF-B present, data/sp500_2026-06-24.csv written, fallback serves cache (503) with warning log. +2 regression tests; full suite 270 passed."

### 2. Live yfinance batch download + degradation gate (SCAN-06)
expected: download_daily_bars() over the universe returns per-ticker frames with lowercased columns; the failure ratio is logged; <10% failures warns and proceeds, ≥10% raises ScanDegradationError; run_daily_scan persists a non-empty top-20-capped watchlist in StateStore.
result: pass
note: "Found + fixed a safety bug (commit 0ee8bcc): yfinance 1.4.1 leaves shared._ERRORS empty (failed tickers present but all-NaN), so the degradation gate never tripped. Now derives failures from the data. Verified live: 15/15 download with lowercased OHLCV cols; 2/17 (11.8%) raises ScanDegradationError + audit; 1/16 (6.25%) warns+proceeds. Full live S&P 500 scan runs in ~10s and persists an idempotent, top-20-capped, gap-ranked watchlist (ranks 1..20 contiguous, gap DESC, sma200/rvol_baseline populated). Empty watchlist on 2026-06-23 with default d3_min_gap_pct=3.0 is legitimate (mean gap −0.38%, funnel instrumented: today-row+SMA200 resolve for all, fails are D1/D2/D3 confluence). +2 regression tests; suite 272 passed."

### 3. Real K_5M subscription against OpenD (SIG-01)
expected: With OpenD running/logged in (127.0.0.1:11111, SIMULATE), run_daily_scan subscribes EXACTLY the persisted top-20 codes via MoomooGateway.subscribe([SubType.K_5M]) — never the full universe — with no quota error. Across an intraday re-scan, evicted codes are unsubscribed and active codes are protected (D-04).
result: pass
note: "Verified LIVE against OpenD (SIMULATE acct 1727266). gateway.connect() paper guard passed (PAPER_TRADING=true, FUTU_TRD_ENV=SIMULATE, broker trd_env=SIMULATE). run_daily_scan(gateway) subscribed exactly the 20 top-ranked codes; query_subscription confirms total_used=20, remain=80, sub_list.K_5M = the 20 watchlist codes — ret=0, no quota error, full universe never subscribed (SIG-01 holds). Note: FUTU_ACC_ID in .env is the default 0; the live SIMULATE acc_id is 1727266 — operator should set FUTU_ACC_ID=1727266 for unattended runs (config, not a code defect)."

## Summary

total: 3
passed: 3
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

All gaps found during UAT were fixed and re-verified live (status: resolved).

- truth: "fetch_sp500_symbols() returns ~500 symbols from a live Wikipedia scrape (SCAN-01)"
  status: resolved
  reason: "pd.read_html(url) returned HTTP 403 (no User-Agent); broad except masked it. Fixed in 3810e60 (urllib fetch with User-Agent + io.StringIO). Re-verified live: 503 symbols."
  severity: major
  test: 1
  artifacts: ["bot/scanner/universe.py"]
  fix_commit: 3810e60

- truth: "download_daily_bars degradation gate trips at >=10% failures (SCAN-06 / D-06)"
  status: resolved
  reason: "yfinance 1.4.1 leaves shared._ERRORS empty (failed tickers present but all-NaN), so the gate never tripped. Fixed in 0ee8bcc (derive failures from data via get_ticker_frame). Re-verified live: 11.8% failures raises ScanDegradationError."
  severity: major
  test: 2
  artifacts: ["bot/scanner/fetcher.py"]
  fix_commit: 0ee8bcc
