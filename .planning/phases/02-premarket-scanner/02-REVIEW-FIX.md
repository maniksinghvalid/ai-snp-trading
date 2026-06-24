---
phase: 02-premarket-scanner
fixed_at: 2026-06-23T23:45:00Z
review_path: .planning/phases/02-premarket-scanner/02-REVIEW.md
iteration: 1
findings_in_scope: 8
fixed: 8
skipped: 0
status: all_fixed
---

# Phase 2: Code Review Fix Report

**Fixed at:** 2026-06-23
**Source review:** .planning/phases/02-premarket-scanner/02-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope (Critical + Warning): 8
- Fixed: 8
- Skipped / false positive: 0
- Info findings (IN-01..IN-04): out of scope (not addressed)

**Test suite:** `python3 -m pytest tests/ -q` → **268 passed, 0 skipped**
(baseline before fixes: 252 passed; +16 new regression tests).

All fixes were applied in an isolated git worktree, committed atomically with
hooks (no `--no-verify`), and fast-forwarded onto `main`.

## Fixed Issues

### CR-01: D2 trend filter silently bypassed when SMA200 is unavailable (< 200 days history)

**Files modified:** `bot/scanner/scanner.py`, `tests/scanner/test_scanner.py`
**Commit:** 26f9041
**Status:** fixed (requires human verification — strategy-correctness / fail-closed logic)
**Applied fix:** `_evaluate_symbol` now fails closed when `sma200_val is None` —
the symbol is excluded with a `symbol_skipped_no_sma200` warning instead of
coercing the SMA to `0.0` (which made `_check_d2`'s `prior_close > 0.0` always
True). The `sma200_for_filter` coercion was removed; the real value is now passed.
**Regression test:** `TestInsufficientHistory::test_no_sma200_symbol_excluded` —
a 50-row frame (49 prior sessions: passes the 14-day RVOL gate, fails the 200-day
SMA gate) must be excluded. This is the case the old fixtures hid (always 220 rows).

### CR-02: gap/D1/D3 use `iloc[-1]` as "today" but the no-look-ahead cutoff uses `scan_date`

**Files modified:** `bot/scanner/scanner.py`, `tests/scanner/test_scanner.py`
**Commit:** 462df71
**Status:** fixed (requires human verification — no-look-ahead / day-alignment logic)
**Applied fix:** "today" is now resolved by date
(`frame.index.normalize() == scan_ts.normalize()`); if no row matches scan_date the
symbol is skipped with `symbol_skipped_no_today_bar`. `prior_row` is the last
session strictly before scan_date (from the same `prior_mask` used for SMA/RVOL),
and an explicit 2-row `(prior, today)` frame is passed to `passes_daily_filters` so
its `iloc[-2]/iloc[-1]` align with the date-resolved rows.
**Regression tests:**
- `TestTodayRowAlignment::test_premarket_no_today_bar_excluded` — a frame whose last
  bar predates scan_date (the real premarket case) must be skipped, not ranked on
  the wrong session.
- `TestTodayRowAlignment::test_gap_uses_scan_date_row_not_last_positional` — a frame
  with prior/today/future rows must compute gap (4%) from the scan_date row, not the
  positional last (0% future) row.

### WR-01: Evicted active codes never unsubscribed — cumulative subscriptions can exceed top-20

**Files modified:** `bot/gateway/gateway.py`, `bot/scanner/scanner.py`,
`tests/gateway/test_gateway.py`, `tests/scanner/test_scanner.py`
**Commit:** a126a16
**Applied fix:** Added `MoomooGateway.unsubscribe()` (K_5M `run_in_executor`
wrapper, mirrors `subscribe()`). `run_intraday_rescan` now calls a new
`_unsubscribe_evicted_codes` step that releases active codes no longer present in
the result (`active_codes - set(result)`), so the cumulative subscribed set stays
within the 20-slot cap across rescans.
**Regression tests:** `TestUnsubscribe` (gateway: wraps executor, empty no-op,
raises on non-RET_OK) and
`TestIntradayRescan::test_rescan_unsubscribes_evicted_active_code` (a collapsed
active code is evicted and unsubscribed; the protected one is not).

### WR-02: D-04 protection silently drops an actively-traded code that fails the re-filter

**Files modified:** `bot/scanner/scanner.py`, `tests/scanner/test_scanner.py`
**Commit:** ce59153
**Applied fix:** `run_intraday_rescan` now emits an explicit `active_code_evicted`
warning for each active code absent from the final watchlist, and reports the count
in `rescan_complete`. The eviction is now auditable rather than an emergent side
effect.
**Regression test:**
`TestIntradayRescan::test_rescan_logs_active_code_eviction` — asserts exactly one
`active_code_evicted` event naming the dropped code.

### WR-03: Migration 0002 applies multiple ALTER TABLE statements non-atomically

**Files modified:** `bot/state/migrations.py`, `tests/state/test_migrations.py`
**Commit:** 268934f
**Applied fix:** Migration 0002 is now an idempotent callable (`_migration_0002`)
that guards each `ALTER TABLE ADD COLUMN` with a `PRAGMA table_info(daily_scan)`
existence check, so re-application after a partial failure is a no-op rather than a
fatal "duplicate column name". `run_migrations` now dispatches str-vs-callable
migrations and bumps `user_version` in the same transaction as the DDL.
**Regression tests:** `TestMigration0002PartialApplication` — pre-adds some 0002
columns at user_version=1 and confirms a re-run completes without error, ends at v2,
and creates no duplicate columns.

### WR-04: `scan_partial_data` logged twice for every partial-failure scan

**Files modified:** `bot/scanner/scanner.py`, `tests/scanner/test_scanner.py`
**Commit:** 1ef3a11
**Applied fix:** Removed the redundant `scan_partial_data` warning block from
`_compute_candidates`; `download_daily_bars` (fetcher) is now the single source of
truth for the event.
**Regression test:**
`TestPartialDataLoggedOnce::test_scanner_does_not_relog_scan_partial_data` — with a
non-empty `failed` set, the scanner module's logger must not emit
`scan_partial_data`.

### WR-05: `fetch_sp500_symbols` blindly trusts `tables[0]` and returns unvalidated cache

**Files modified:** `bot/scanner/universe.py`, `tests/scanner/test_universe.py`
**Commit:** be14379
**Applied fix:** The constituents table is now selected by matching expected columns
(`Symbol` + `Security`/`GICS Sector`) via `_select_constituents_table`, not by
position. Both the scraped list and any cached list are rejected if smaller than
`_MIN_UNIVERSE_SIZE` (400) — preventing a truncated/garbage universe from being
cached as authoritative and skewing the 10% degradation denominator.
**Regression tests:** `TestConstituentsTableSelection` (select-by-columns,
fall-back when no matching table) and `TestUniverseSizeValidation` (small scrape
falls back to cache; small cache rejected → RuntimeError). Existing fixtures were
updated to realistic ~500-symbol shapes (the old 3-symbol fixtures would have
masked this).

### WR-06: subscribe paths call `asyncio.run` per call — raises inside a running loop

**Files modified:** `bot/scanner/scanner.py`, `tests/scanner/test_scanner.py`
**Commit:** 2fe4114
**Applied fix:** Added a `_run_coro` bridge used by all subscribe/unsubscribe call
sites. It detects whether an event loop is already running on the current thread;
if not it uses `asyncio.run` as before, and if one is running it executes the
coroutine on a worker thread with its own loop — so a future async scheduler
(Phase 4/5) never triggers "asyncio.run() cannot be called from a running event
loop" after persistence.
**Regression test:**
`TestRunFromRunningEventLoop::test_run_daily_scan_inside_running_loop_subscribes` —
drives `run_daily_scan` from inside `asyncio.run(...)` and asserts it completes and
still subscribes.

## Skipped Issues

None — all eight Critical and Warning findings were fixed. No finding was judged a
false positive.

## Notes

- Info findings IN-01 (dead defensive branch), IN-02 (numpy pin), IN-03 (unpinned
  lxml), IN-04 (empty `__init__.py`) were out of scope (`fix_scope:
  critical+warning`) and were not modified.
- CR-01 and CR-02 are classified as logic/correctness fixes; per verification
  policy they are flagged "requires human verification" so the developer confirms
  the fail-closed exclusion and day-alignment semantics match the intended
  no-look-ahead contract before the phase proceeds.
- `STATE.md` and `ROADMAP.md` were not modified.

---

_Fixed: 2026-06-23_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
