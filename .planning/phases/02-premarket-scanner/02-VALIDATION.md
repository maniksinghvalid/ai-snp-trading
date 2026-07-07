---
phase: 2
slug: premarket-scanner
status: verified
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-23
audited: 2026-07-07
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 8.0 (already in requirements-dev.txt) |
| **Config file** | none — uses pytest defaults (discovery from project root) |
| **Quick run command** | `pytest tests/scanner/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds (scanner subset); ~30s full suite |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/scanner/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 2-01-* | 01 | 1 | SCAN-01 | — | Wikipedia scrape returns ~500 symbols; cache fallback on failure | unit (mock HTTP) | `pytest tests/scanner/test_universe.py -x` | ✅ 10 tests | ✅ green |
| 2-01-* | 01 | 1 | SCAN-06 | — | Partial yfinance failure detected; ≥10% triggers abort | unit (mock yf) | `pytest tests/scanner/test_fetcher.py -x` | ✅ 30 tests | ✅ green |
| 2-01-* | 01 | 1 | SCAN-02 | — | D1/D3 daily filters applied via `passes_daily_filters()` | unit (synthetic df) | `pytest tests/scanner/test_scanner.py::TestDailyFilters::test_daily_filters -x` | ✅ | ✅ green |
| 2-02-* | 02 | 2 | SCAN-03 | — | RVOL baseline excludes today (date < scan_date) | unit (synthetic) | `pytest tests/scanner/test_scanner.py -k test_rvol_no_lookahead -x` | ✅ | ✅ green |
| 2-02-* | 02 | 2 | SCAN-05 | — | Running scanner twice same day: same row count | unit (tmp DB) | `pytest tests/scanner/test_scanner.py -k test_idempotency -x` | ✅ | ✅ green |
| 2-02-* | 02 | 2 | SCAN-08 | — | >20 passing candidates → exactly 20 stored | unit (synthetic) | `pytest tests/scanner/test_scanner.py -k test_top20_cap -x` | ✅ | ✅ green |
| 2-03-* | 03 | 3 | SCAN-04 | — | Holiday date → no output, no error; half-day → correct early-close | unit (mcal) | `pytest tests/scanner/test_calendar.py -x` | ✅ 4 tests | ✅ green |
| 2-03-* | 03 | 3 | SCAN-07 | — | Re-scan updates rank, no duplicate rows; active candidate not evicted | unit (tmp DB) | `pytest tests/scanner/test_scanner.py -k test_rescan_idempotent -x` | ✅ | ✅ green |
| 2-03-* | 03 | 3 | SIG-01 | — | subscribe() called only for capped top-20 list | unit (mock gateway) | `pytest tests/scanner/test_scanner.py -k test_subscribe_top20_only -x` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Coverage:** 9/9 requirements COVERED by green automated tests. Full scanner suite: **83 passed** (`pytest tests/scanner/ -q`, 2026-07-07). Every Wave-0 test file was built and merged during execution; the three live-feed behaviors below remain legitimately manual.

---

## Wave 0 Requirements

- [x] `tests/scanner/__init__.py` — package init
- [x] `tests/scanner/test_universe.py` — SCAN-01 scrape + cache fallback (mock HTTP) — 10 tests
- [x] `tests/scanner/test_fetcher.py` — SCAN-06 batch download + failure/degradation detection (mock `yf.download`) — 30 tests
- [x] `tests/scanner/test_scanner.py` — SCAN-02/03/05/07/08 + SIG-01 (synthetic DataFrames, mock DB/gateway) — 39 tests
- [x] `tests/scanner/test_calendar.py` — SCAN-04 (real pandas-market-calendars: holiday + half-day assertions) — 4 tests
- [x] yfinance and pandas-market-calendars added to `requirements.txt`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live Wikipedia scrape returns current real S&P 500 list | SCAN-01 | Depends on live network + Wikipedia table format; unit tests mock HTTP | Run scanner once with network on; confirm ~500 symbols and a dated cache file under `data/`. |
| Live yfinance batch download for real universe | SCAN-06 | Network-dependent, rate-limited; unit tests mock `yf.download` | Run a real scan during/after market hours; confirm bars returned, degradation ratio logged, watchlist non-empty. |
| Real K_5M subscription against OpenD | SIG-01 | Requires OpenD running + quota; unit tests mock the gateway | With OpenD up, run scan; confirm exactly the top-20 codes subscribed via `get_subscription` and no quota error. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ✅ Nyquist-compliant (audited 2026-07-07)

---

## Validation Audit 2026-07-07

| Metric | Count |
|--------|-------|
| Requirements audited | 9 |
| COVERED (green automated) | 9 |
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

State-A audit of a stale plan-time draft (all tasks were marked `pending / ❌ W0`). The phase
was fully executed and verified (`02-VERIFICATION.md`, passed 7/7); this audit confirmed every
Wave-0 test file exists, targets the mapped behavior, and runs green — `pytest tests/scanner/ -q`
→ **83 passed**. No test generation was needed; the draft frontmatter simply predated execution.
The three live-feed behaviors (Wikipedia scrape, yfinance batch, K_5M subscription) remain
legitimately manual-only and are documented above.
