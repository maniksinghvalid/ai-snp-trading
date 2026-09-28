# Deferred Items — Quick Task 260927-r53

## Out-of-scope pre-existing test failure

**Test:** `tests/position/test_manager.py::TestP1BTradeRecording::test_on_quote_full_fill_closes_and_records_trade`

**Failure:** `AssertionError: Expected exactly one trade row, got []` (0 rows found via `get_closed_trades(pos.updated_at.date().isoformat())`).

**Scope check performed:** Reproduced identically with the ORIGINAL committed `tests/conftest.py` (no `_isolate_bot_log` fixture) and with this task's modified `tests/conftest.py`. Same assertion failure both times, in isolation and inside the full suite run. This proves the failure is pre-existing and unrelated to the 260927-r53 logging-isolation change.

**Files this task is allowed to touch:** `tests/conftest.py`, `tests/safety/test_logger.py` only (per task constraints). `tests/position/test_manager.py` and `bot/` are out of scope for this task — not fixed here.

**Action:** Logged only, not fixed, per the Scope Boundary rule (only auto-fix issues directly caused by the current task's changes).

**Root cause (orchestrator, 2026-09-27):** clock-dependent, not flaky. `_on_quote` sets `pos.updated_at = now_et()` and the trade row stores `closed_at` as ET ISO (`...-04:00`). SQLite `DATE(closed_at)` normalises to the UTC date, but the test queries `pos.updated_at.date()` (the ET date). Between 20:00 and 24:00 ET the two dates differ: `date('2026-09-27T22:42:00-04:00')` returns `'2026-09-28'`. It also fails on base 0f80abf. Production RTH closes (force-close 15:51 ET) are unaffected, as `record_trade`'s docstring notes.
