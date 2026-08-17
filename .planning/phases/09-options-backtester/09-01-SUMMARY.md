---
phase: 09-options-backtester
plan: 01
subsystem: backtester-options-data
tags: [massive-api, options-data, no-look-ahead, cache]
dependency-graph:
  requires: [backtester/massive.py, bot/options/strategy.py, bot/options/config.py]
  provides: [backtester/options/data.py::OptionChainSource, backtester/massive.py::cached_contracts, backtester/massive.py::cached_option_bars]
  affects: [09-02-PLAN.md (hypotheses IS/OOS window design), 09-03-PLAN.md (engine.py consumes OptionChainSource)]
tech-stack:
  added: []
  patterns: [read-through-cache, import-not-copy, exact-key-day-lookup]
key-files:
  created:
    - backtester/options/__init__.py
    - backtester/options/data.py
    - tests/backtester/options/__init__.py
    - tests/backtester/options/conftest.py
    - tests/backtester/options/test_data.py
  modified:
    - backtester/massive.py
decisions:
  - "Massive option-aggregates entitlement boundary pinned to August 2024 (Jul-2024 403, Aug-2024 200) -- rolling ~24mo window from 2026-08-17"
  - "A2 confirmed: missing option bars are absent rows, never v=0 rows"
  - "Dropped load()-time monthly-only narrowing (Rule 1 bug fix) -- rules_options.json's 30..60 dte window is not always wide enough to contain a monthly expiry; pick_expiry's own fallback logic now does the real work"
metrics:
  duration: "~30 minutes (incl. live probe + throttled waits)"
  completed: 2026-08-17
---

# Phase 9 Plan 01: Options data layer (Massive contracts + O: aggregates, no-look-ahead chain view) Summary

Extended `MassiveDataSource` with two options-specific endpoints and built `OptionChainSource`,
a per-decision-day chain view that structurally cannot leak a future bar into day t's contract
selection.

## What Was Built

- **Live entitlement re-probe** (5 requests, budget 6): binary-searched the Massive option
  daily-aggregates history boundary for `O:SPY250620C00600000` — May 2024 and July 2024 both
  403'd (`NOT_AUTHORIZED: "Your plan doesn't include this data timeframe"`), August 2024
  returned 200 with 10 bars. **Boundary pinned inside August 2024** — a rolling ~24-month
  window from today (2026-08-17), not a fixed calendar date. Plan 09-02's hypotheses doc must
  fit its IS/OOS windows inside roughly `[2024-08, today]`.
  Also confirmed assumption A2: a far-OTM, effectively untraded contract
  (`O:GDX250620C00080000`, strike 80 vs ~40-50 spot, May 2025, inside the entitled window)
  returned **zero result rows** for the whole month — non-trading days are **absent rows**,
  never present with `v: 0`. Both findings are recorded verbatim, dated, in
  `backtester/options/data.py`'s module docstring.
- **`backtester/options/data.py`**: `parse_massive_ticker`/`format_massive_ticker` (OCC-style
  `O:` ticker codec mirroring the live gateway's option-code algorithm), `trading_days()`
  (NYSE calendar, same shape as `backtester/run.py::_trading_days`), and `OptionChainSource`.
- **`backtester/massive.py`**: `fetch_contracts`/`cached_contracts` (`/v3/reference/options/contracts`,
  same `next_url` pagination loop as `fetch_bars`, deduped by ticker, sorted by
  `(expiration_date, contract_type, strike_price)`, JSON read-through cache) and
  `cached_option_bars` (delegates to the existing `fetch_bars` — the `O:` prefix passes through
  `_massive_ticker` unchanged — with a CSV read-through cache, same Title-Case ET-indexed frame
  shape as `cached_bars`). `_OPTION_TICKER_RE` guards traversal-shaped option tickers before
  any path is built (same T-06-03 precedent as the existing `_SYMBOL_RE`).
- **`OptionChainSource(source, underlying, start, end)`**: `.load(min_dte, max_dte,
  prefer_monthly, strike_band_pct)` calls `cached_contracts` **once** per underlying for the
  whole window, narrows candidates to a strike band around the window's underlying close range,
  then fetches `cached_option_bars` per surviving contract. `contracts_for_day`,
  `expiries_for_day`, `bar_close`, `bar_volume`, `underlying_close` are all **exact-key day
  lookups** — a bar dated t+1 or t-1 can never answer a query for day t. `last_known_close` is
  the one documented carry-forward exception, explicitly for MANAGE-time marking only, never
  entry selection.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Dropped `load()`-time monthly-only narrowing**
- **Found during:** Writing `test_monthly_narrowing_covers_dte_window` (T-09-03) — the test
  that was supposed to *prove* "every `[min_dte, max_dte]` window contains at least one monthly
  expiry" instead disproved it. For `today = 2025-04-17`, window = `[2025-05-17, 2025-06-16]`:
  May's third Friday is 2025-05-16 (1 day before the window opens), June's is 2025-06-20 (4
  days after the window closes) — zero monthlies in range. The August-2025→September-2025
  monthly gap is 35 days, wider than the 31-day-wide `min_dte..max_dte` window, so this is a
  recurring alignment, not a one-off.
- **Issue:** The plan's literal spec ("when `prefer_monthly`, keep only `is_monthly_expiry(expiry)`
  contracts at `load()` time") would silently discard the *only* valid `pick_expiry` candidate
  on those days, since non-monthly bars for that expiry would never even be fetched.
- **Fix:** `load()` now keeps every DTE-range/strike-band candidate (monthly and non-monthly)
  and lets `bot.options.strategy.pick_expiry` — unmodified, imported per D-02 — apply the
  monthly preference itself (it already implements "prefer monthly, fall back to all in-range
  candidates on ties"). `prefer_monthly` remains a `load()` parameter for signature parity with
  the plan but is a no-op on candidate selection; the reasoning is documented in
  `OptionChainSource`'s class docstring under a `DEVIATION` heading.
- **Files modified:** `backtester/options/data.py`
- **Commit:** 853880c

Or otherwise: plan executed as written for T-09-01 and T-09-02.

## Self-Check

- `backtester/options/__init__.py` — FOUND
- `backtester/options/data.py` — FOUND
- `tests/backtester/options/__init__.py` — FOUND
- `tests/backtester/options/conftest.py` — FOUND
- `tests/backtester/options/test_data.py` — FOUND
- `backtester/massive.py` (extended) — FOUND
- Commit `bdde234` (T-09-01) — FOUND
- Commit `8b608c0` (T-09-02 RED) — FOUND
- Commit `e0e63dc` (T-09-02 GREEN) — FOUND
- Commit `084e2f0` (T-09-03 RED) — FOUND
- Commit `853880c` (T-09-03 GREEN) — FOUND

## TDD Gate Compliance

- T-09-02: RED (`8b608c0 test(09-01): ...`) precedes GREEN (`e0e63dc feat(09-01): ...`). Gate satisfied.
- T-09-03: RED (`084e2f0 test(09-01): ...`) precedes GREEN (`853880c feat(09-01): ...`). Gate satisfied.

## Verification

- `pytest tests/backtester/options/ -x -q` — 22 passed
- `pytest tests/backtester/ -x -q` — 102 passed
- `pytest -q` (full suite) — **987 passed, 1 skipped** (965 baseline + 22 new; no regression)
- `grep -rn 'bot.gateway\|moomoo' backtester/options/` — no matches
- Re-running `test_data.py` and `test_massive.py` twice: fixtures monkeypatch `_get_json`, zero
  live network calls anywhere in the suite

## Self-Check: PASSED
