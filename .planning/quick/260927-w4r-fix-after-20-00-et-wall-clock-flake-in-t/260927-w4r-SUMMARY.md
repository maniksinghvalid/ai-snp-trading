---
phase: quick-260927-w4r
plan: 01
subsystem: testing
tags: [pytest, timezone, sqlite, flaky-test, zoneinfo]
status: complete

requires: []
provides:
  - "TestP1BTradeRecording's two _on_quote tests are now time-of-day independent"
affects: [tests/position/test_manager.py]

tech-stack:
  added: []
  patterns:
    - "Freeze now_et() via patch(\"bot.position.manager.now_et\", return_value=...) around the exact await line under test, query the literal ET date string, matching sibling tests in the same class"

key-files:
  created: []
  modified:
    - "tests/position/test_manager.py"

key-decisions:
  - "Used a NAIVE datetime(2026, 6, 24, 10, 6, 0) for the freeze (not tz-aware) to match every sibling test in TestP1BTradeRecording; _on_quote only assigns now_et() to pos.updated_at and never compares it against a tz-aware value, and siblings already persist a naive now_et() through the same _persist_position / _record_trade_if_closed path"
  - "Grep confirmed only two real-SQLite-backed hazard sites existed (lines 3297, 3331 pre-fix); no additional fix needed elsewhere"

requirements-completed: [QUICK-260927-w4r]

duration: ~15min
completed: 2026-09-27
---

# Phase quick-260927-w4r: Fix after-20:00-ET wall-clock flake in TestP1BTradeRecording Summary

**Froze `now_et()` around both `_on_quote` trade-recording tests and switched their `get_closed_trades` query to the literal ET date, eliminating a flake that only manifested from 20:00 ET (EDT) / 19:00 ET (EST) onward and restoring the partial-fill test's previously-vacuous assertion.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 completed
- **Files modified:** 1 (`tests/position/test_manager.py`)

## Root Cause

`_on_quote` stamps `pos.updated_at = now_et()` (bot/position/manager.py:444 — the only `now_et()` use on that path). `_record_trade_if_closed` persists it as `closed_at`, an ET-offset ISO string (e.g. `2026-09-27T22:42:00-04:00`). `StateStore.get_closed_trades` filters `WHERE DATE(closed_at) = ?`, and SQLite's `DATE()` normalises the offset to UTC, producing `2026-09-28`. The two tests queried `pos.updated_at.date().isoformat()` — the ET date, `2026-09-27` — so:

- `test_on_quote_full_fill_closes_and_records_trade` returned 0 rows and failed whenever the host wall clock was at/after 20:00 ET (EDT) / 19:00 ET (EST).
- `test_on_quote_partial_fill_stays_open_no_trade_row_no_alert`'s `== []` assertion passed **vacuously** in that same window (querying a date that could never have a row, bug or not) — silently losing its teeth.

## Task Commits

1. **Task 1: RED — reproduce the flake with a throwaway late-clock pytest plugin** — no repo commit (scratchpad-only; plugin lives outside the repo at `/private/tmp/.../scratchpad/late_clock.py`, never staged)
2. **Task 2: GREEN — freeze now_et in both tests, verify, commit** — `ed86bff` (fix)

## RED Evidence (Task 1, unmodified HEAD, `-p late_clock` forcing now_et() = 2026-09-27 22:42 ET)

Command:
```
PYTHONPATH=<scratchpad> python3 -m pytest -p late_clock \
  "tests/position/test_manager.py::TestP1BTradeRecording::test_on_quote_full_fill_closes_and_records_trade" \
  "tests/position/test_manager.py::TestP1BTradeRecording::test_on_quote_partial_fill_stays_open_no_trade_row_no_alert" -q
```

Output tail:
```
        rows = open_store.get_closed_trades(pos.updated_at.date().isoformat())
>       assert len(rows) == 1, f"Expected exactly one trade row, got {rows!r}"
E       AssertionError: Expected exactly one trade row, got []
E       assert 0 == 1
E        +  where 0 = len([])

tests/position/test_manager.py:3298: AssertionError
----------------------------- Captured stdout call -----------------------------
2026-09-27 23:12:54 [info     ] quote_stop_triggered           bid_price=97.5 code=US.AAPL remaining_quantity=200 trail_stop=98.0
=========================== short test summary info ============================
FAILED tests/position/test_manager.py::TestP1BTradeRecording::test_on_quote_full_fill_closes_and_records_trade
1 failed, 1 passed in 0.12s
```

Confirms: full-fill test fails with 0 rows under the late clock; partial-fill test passes (vacuously, the second bug). Repo tree was clean throughout (`git diff --quiet` before and after).

## Step 1 Confirming Grep (Task 2)

```
grep -rnE "updated_at\.date\(\)|now_et\(\)\.date\(\)|date\.today\(\)|datetime\.now\(" tests/
```

Result: only `tests/position/test_manager.py:3297` and `:3331` feed `get_closed_trades` / `get_daily_trade_stats` / `record_trade` against a real SQLite `StateStore`. All other hits are unrelated:
- `tests/state/test_store_threadsafety.py`, `test_store_concurrency.py` — unrelated timestamp fields, not `closed_at`.
- `tests/risk/test_risk_engine.py`, `tests/backtester/*` — `emitted_at` / date-range feed inputs, not `closed_at`.
- `tests/scanner/test_fetcher.py`, `tests/service/test_bot.py` — string mentions in docstrings/comments, or a `MagicMock`-backed store (ignored per plan).

No additional hazard found; no extra fix needed.

## GREEN Evidence (Task 2, after fix)

**With late-clock plugin** (inner `patch` overrides the session-wide late clock):
```
PYTHONPATH=<scratchpad> python3 -m pytest -p late_clock "tests/position/test_manager.py::TestP1BTradeRecording" -q
......                                                                   [100%]
6 passed in 0.11s
```

**Without the plugin:**
```
python3 -m pytest "tests/position/test_manager.py::TestP1BTradeRecording" -q
......                                                                   [100%]
6 passed in 0.21s
```

**Full suite:**
```
python3 -m pytest -q
1373 passed, 1 skipped in 54.95s
```

## Files Created/Modified
- `tests/position/test_manager.py` — both `_on_quote` tests in `TestP1BTradeRecording` now wrap `await manager._on_quote(...)` in `with patch("bot.position.manager.now_et", return_value=datetime(2026, 6, 24, 10, 6, 0)):` and query `open_store.get_closed_trades("2026-06-24")` instead of `pos.updated_at.date().isoformat()`.

## Deviations from Plan

None — plan executed exactly as written. No additional hazard sites found in Step 1's confirming grep; no production code touched.

## Production note (not changed)

Production exit paths call `manage_exit` synchronously and stamp `now_et()`; orders use `Session.NONE` (RTH-only) and `force_close_et=15:51`, so RTH closes land on the same UTC date; the mismatch would only affect a trade whose `closed_at` is stamped at/after 20:00 ET (EDT) / 19:00 ET (EST) — it would be attributed to the NEXT session's Gate 7 -2R breaker and EOD report rather than crash. Likelihood low. No change made.

## Self-Check: PASSED

- `tests/position/test_manager.py` — FOUND (modified, verified via `git show --stat HEAD`)
- Commit `ed86bff` — FOUND (`git log --oneline -1` confirms; `git show --stat HEAD` confirms single-file diff to `tests/position/test_manager.py`; `git diff --quiet HEAD~1 -- bot/` confirms zero production-code change)
