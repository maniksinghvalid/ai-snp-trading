---
slug: trades-keyed-by-utc-date
status: resolved
trigger: "tests/position/test_manager.py::TestP1BTradeRecording::test_on_quote_full_fill_closes_and_records_trade fails deterministically at ~17:00 PDT 2026-10-03 (after 00:00 UTC, still 2026-10-03 ET): AssertionError 'Expected exactly one trade row, got []' at rows = open_store.get_closed_trades(pos.updated_at.date().isoformat()). Position closes (phase CLOSED, remaining 0). Determine whether bug is in test query key or production (PositionManager._record_trade_if_closed / StateStore date keying — production must key trades by ET session date); reproduce by freezing time to evening-ET/after-midnight-UTC; fix root cause; confirm full suite passes at daytime and evening timestamps."
created: 2026-10-03
updated: 2026-10-03
resolved: 2026-10-03
---

# Debug Session: trades-keyed-by-utc-date

## Symptoms

- **Expected behavior:** A trade closed on ET session date D is returned by `StateStore.get_closed_trades(D)` and counted by `get_daily_trade_stats(D)`, whatever the wall-clock time.
- **Actual behavior:** A trade closed at/after 20:00 EDT (19:00 EST) is invisible under its ET session date; it is filed under D+1.
- **Error messages:** `AssertionError: Expected exactly one trade row, got []` (test_manager.py, P1B trade-recording test, pre-ed86bff query form).
- **Timeline:** Latent since trades were keyed with SQLite `DATE(closed_at)`. Surfaced as an evening-only test flake; ed86bff (260927-w4r) froze now_et to a naive 10:06 in the test, masking the production defect rather than fixing it.
- **Reproduction:** Record a trade with `closed_at = now_et()` after 20:00 EDT, then query `get_closed_trades(now_et().date())` → `[]`.

## Current Focus

- status: RESOLVED — hypothesis confirmed (RED/GREEN + mutation check).
- hypothesis: `StateStore.get_closed_trades` / `get_daily_trade_stats` filter `WHERE DATE(closed_at) = ?`. `closed_at` is stored as an ET-offset ISO string (`now_et().isoformat()`, e.g. `2026-10-03T20:30:00-04:00`); SQLite `DATE()` normalises offsets to UTC → `2026-10-04`. Callers (report, daily summary, Gate 7 -2R breaker) pass the ET session date, so evening closes miss.
- test: store-level regression — record a trade at 2026-10-03 20:30 EDT (ET-aware and the same instant UTC-aware), query `2026-10-03` → expect 1 row / trade_count 1, and `2026-10-04` → 0.
- expecting: RED before fix (0 rows), GREEN after.
- next_action: none — resolved. Operator: merge to develop + restart bot for it to go live.

## Evidence

- timestamp: 2026-10-03T21:25-04:00 — `git show ed86bff`: commit message itself names `DATE()` UTC normalisation as the cause, but changes only the test (freeze naive now_et + literal date).
- timestamp: 2026-10-03T21:25-04:00 — `bot/state/store.py:429,465` are the only `DATE(closed_at)` filters in the repo; callers `bot/service/bot.py:922-923`, `bot/service/report.py:93`, `bot/signal/signal_engine.py:442` all pass ET session dates.
- timestamp: 2026-10-03T21:25-04:00 — all writers pass ET-aware datetimes: `pos.updated_at = now_et()` or `fill.fill_time` (engine.py:373 `now_et()`); backtester harness passes tz-aware ET `pos.updated_at`.

- timestamp: 2026-10-03T21:22-04:00 — RED: new store test fails with the reported message (`Expected exactly one trade row, got []`) for both ET-aware and UTC-aware closed_at.
- timestamp: 2026-10-03T21:23-04:00 — manager-level repro (original test logic, now_et frozen tz-aware): 10:06 ET passes, 20:30 ET fails.
- timestamp: 2026-10-03T21:24-04:00 — read-only check of live data/bot_state.db: 1 trade row, `2026-08-28T15:51:15.682035-04:00`; substr and DATE keys agree → no migration needed.

## Eliminated

- hypothesis: PositionManager._record_trade_if_closed writes the wrong timestamp — it persists `pos.updated_at` (ET-aware) unchanged; the defect is purely in the store's read-side keying.

## Resolution

- root_cause: PRODUCTION, not the test. `StateStore.get_closed_trades` / `get_daily_trade_stats` keyed on SQLite `DATE(closed_at)`, which converts the stored ET-offset ISO timestamp to UTC; closes at/after 20:00 EDT (19:00 EST) were filed under the next day while every caller (EOD report, Daily Summary, Gate 7 -2R breaker) queries by ET session date. The test's `pos.updated_at.date()` (ET) query key was correct; ed86bff only masked the defect by freezing now_et to a naive daytime value.
- fix: `record_trade` normalises tz-aware closed_at to ET before isoformat (single writer); both readers filter `substr(closed_at, 1, 10) = ?` (the ET session date). Commits e5eedda (test) + 7a5af92 (fix).
- verification: regression GREEN; full suite 1375 passed / 1 skipped at real clock 2026-10-03 21:23 EDT (01:23 UTC) and under time-machine at 2026-10-05 11:00 ET, 2026-10-05 20:30 ET (00:30 UTC) and 2026-10-03 20:00:30 EDT (00:00:30 UTC — the reported instant). Mutation check: original unfrozen test (ed86bff^) fails at 20:30 ET on pre-fix store, passes at both clocks on fixed store.
- files_changed: bot/state/store.py, tests/state/test_store.py
