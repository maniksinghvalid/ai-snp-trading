---
status: testing
phase: 02-premarket-scanner
source: [02-VERIFICATION.md]
started: 2026-06-23T23:20:00Z
updated: 2026-06-23T23:20:00Z
---

## Current Test

number: 1
name: Live Wikipedia S&P 500 scrape
expected: |
  Running fetch_sp500_symbols() with network access returns ~500 symbols and
  writes data/sp500_YYYY-MM-DD.csv (ET date). Dot-to-dash names appear normalized
  (e.g., BRK-B, BF-B). On a forced scrape failure it falls back to the newest cache.
awaiting: user response

## Tests

### 1. Live Wikipedia S&P 500 scrape (SCAN-01)
expected: fetch_sp500_symbols() returns ~500 symbols, creates data/sp500_YYYY-MM-DD.csv, normalizes BRK.B→BRK-B / BF.B→BF-B, and falls back to newest cache on scrape failure.
result: [pending]

### 2. Live yfinance batch download + degradation gate (SCAN-06)
expected: download_daily_bars() over the universe returns per-ticker frames with lowercased columns; the failure ratio is logged; <10% failures warns and proceeds, ≥10% raises ScanDegradationError; run_daily_scan persists a non-empty top-20-capped watchlist in StateStore.
result: [pending]

### 3. Real K_5M subscription against OpenD (SIG-01)
expected: With OpenD running/logged in (127.0.0.1:11111, SIMULATE), run_daily_scan subscribes EXACTLY the persisted top-20 codes via MoomooGateway.subscribe([SubType.K_5M]) — never the full universe — with no quota error. Across an intraday re-scan, evicted codes are unsubscribed and active codes are protected (D-04).
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
