---
phase: 06-backtester
reviewed: 2026-07-07T14:42:19Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - backtester/__init__.py
  - backtester/execution.py
  - backtester/feed.py
  - backtester/harness.py
  - backtester/report.py
  - backtester/run.py
  - bot/state/store.py
  - tests/backtester/__init__.py
  - tests/backtester/fixtures.py
  - tests/backtester/test_execution.py
  - tests/backtester/test_feed.py
  - tests/backtester/test_harness.py
  - tests/backtester/test_report.py
  - tests/backtester/test_run.py
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-07-07T14:42:19Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

Adversarial re-review of the backtester after the 06-07..06-12 gap-closure rounds. I traced the full replay pipeline (`run.py` -> `feed.py` -> `harness.py` -> `execution.py` -> `report.py`) bar-for-bar, checked the N+1 fill / anti-look-ahead model end to end, and verified the four previously-flagged BLOCKERs are genuinely fixed rather than papered over:

- **NaN feed hygiene** — `feed._materialize_bars` (line 220) and `_load_premarket` (line 301) both `dropna(subset=[open,high,low,close,volume])` before the per-row `int()/float()` loop, applied identically to the CSV-cache-hit path. Covered by `test_union_index_nan_rows_do_not_crash_or_poison_premarket_high`. Sound.
- **Gate-7 trades persistence** — `harness._capture_closed_trades` calls `store.record_trade` (a new, correctly parameterized, lock-guarded INSERT) for every newly-CLOSED position at the end of `_process_bar`, strictly before the next bar's Gate-7 read. `test_gate7_circuit_breaker_trips_from_backtest_recorded_trades` proves the breaker trips from the backtest's own recorded loss. Sound.
- **Exit-slippage sign** — entry BUY slips `+` (execution.py:76), both exit legs (normal and force-close SELL) slip `-` (execution.py:116, 132). `test_exit_slippage_is_adverse` locks all three legs. Sound.
- **Fixture date anchoring** — every fixture and test date is derived from `recent_session_days()` (runtime-relative, ~15-20 days back), so `feed._enforce_window`'s rolling ~60-calendar-day cutoff can never turn the suite red on a future run date. No hardcoded `2026-…` literals remain in the replay-window-sensitive fixtures. Sound.

The N+1 anti-look-ahead model is correct: signals decide on bar N's close, fills read only bar N+1's *open price* (never a gate input), `next_bar()` refuses to cross a session boundary, and a signal on a session's last bar abandons. No look-ahead leak found. `record_trade` and the other `store.py` methods are parameterized (no SQL injection) and the cache-path symbol regex blocks traversal (`test_traversal_symbol_rejected_before_any_cache_or_network_access`).

No BLOCKER-severity defects remain. Findings below are correctness-adjacent hygiene issues (WARNING) and documented trade-offs (INFO).

## Narrative Findings (AI reviewer)

### Warnings

#### WR-01: Abandon path writes a status label into the `resolved_at` timestamp column

**File:** `backtester/harness.py:363`
**Issue:** When `consume_intent` returns `None` (D-05 abandon — signal on a session's last bar, no N+1 bar), the harness calls:
```python
self._store.resolve_pending_intent(intent.intent_id, "ABANDONED")
```
`StateStore.resolve_pending_intent(intent_id, resolved_at)` (store.py:743) hardcodes `status='RESOLVED'` and writes its second argument verbatim into the `resolved_at` column. So the string literal `"ABANDONED"` lands in a column meant to hold an ISO-8601 timestamp. It does not crash and nothing in the backtest reads `resolved_at`, so impact is contained — but it is a category error: the row's `resolved_at` is now un-parseable garbage, and the pattern is a trap if this column is ever read, or if the call is copied into live code where `resolved_at` *is* consumed.
**Fix:** Pass the actual resolution time, matching every other call site of this method:
```python
self._store.resolve_pending_intent(
    intent.intent_id, self._replay_clock.isoformat()
)
```
If distinguishing "abandoned" from "resolved" matters for the backtest audit, that belongs in a status/reason column, not smuggled into the timestamp.

#### WR-02: Scratch StateStore is never closed and `backtester/runs/<uuid>/` accumulates unboundedly

**File:** `backtester/run.py:135` (and `_scratch_db_path`, run.py:81-86)
**Issue:** `main()` opens `store = StateStore(db_path=db_path).open()` (WAL mode) but never calls `store.close()` — there is no `try/finally` around the run, so the SQLite connection (plus its `-wal`/`-shm` sidecar files) leaks until process exit, and if `harness.run()` raises, the report step is skipped and the store is still never closed. Separately, every invocation mints a fresh `backtester/runs/<uuid>/state.db` and nothing ever removes it, so the `backtester/runs/` tree grows without bound across repeated backtests. Both are hygiene issues rather than correctness bugs (the scratch DB is write-only; the report reads `harness.trade_log` from memory), but on an operator's machine running many backtests this is silent disk growth and file-descriptor churn.
**Fix:** Wrap the run in `try/finally: store.close()`, and either write the scratch DB under the OS temp dir (`tempfile.mkdtemp`) with cleanup, or delete `os.path.dirname(db_path)` after `write_report` succeeds:
```python
store = StateStore(db_path=db_path).open()
try:
    feed = SimulatedBarFeed(symbols, args.start, args.end)
    harness = BacktestHarness(cfg, feed, store)
    for day in _trading_days(args.start, args.end):
        harness.setup_day(day, symbols)
    asyncio.run(harness.run())
    write_report(harness.trade_log, args.output_dir)
finally:
    store.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)
```

### Info

#### IN-01: `profit_factor` serialized as the non-RFC `Infinity` token in summary.json

**File:** `backtester/report.py:55, 105`
**Issue:** `profit_factor = ... if gross_loss > 0 else float("inf")`, then `json.dump(metrics, ...)` uses the default `allow_nan=True`, emitting the bare token `Infinity`. This is a *common* case, not an edge case — any backtest with zero losing trades (a single all-winning run) produces it. `json.load` round-trips it, but it is invalid per RFC 8259, so any strict parser or `JSON.parse` in a downstream tool (dashboard, JS report viewer) will reject `summary.json`. The behaviour is deliberate and documented in the module docstring.
**Fix:** If cross-tool robustness is wanted, cap or sentinel it — e.g. emit `null` (or a large finite number) for the no-losses case, or `json.dump(..., allow_nan=False)` after replacing `inf` with `None`. Leave as-is if only Python consumers read the file.

#### IN-02: `_process_bar` silently swallows BarEvent construction errors

**File:** `backtester/harness.py:299-300`
**Issue:** The `try/except Exception: return` around `BarEvent(...)` discards any malformed bar without a log line. In practice `_materialize_bars` always populates every key, so this is near-dead defensive code — but if a future feed change drops a field, bars would be silently skipped mid-replay with no signal to the operator, producing a quietly-incomplete backtest.
**Fix:** Narrow the catch and log the drop, e.g. `except (KeyError, TypeError) as exc: logger.warning("skipping malformed bar %s: %s", bar_data.get("time_key"), exc); return`.

#### IN-03: `record_trade` closed-date matching relies on ET/UTC coinciding for RTH times

**File:** `bot/state/store.py:388-390`, consumed by `get_daily_trade_stats` (store.py:465)
**Issue:** `record_trade` stores `closed_at` as `pos.updated_at.isoformat()`, an ET-offset string (e.g. `...T15:55:00-04:00`). `get_daily_trade_stats` filters `WHERE DATE(closed_at) = ?` with the ET session date, and SQLite's `DATE()` converts an offset-bearing timestamp to **UTC** before extracting the date. For RTH close/force-close times (afternoon ET) the UTC date is the same calendar day, so Gate 7 matches correctly — but the equivalence is only guaranteed inside RTH. Any `closed_at` at/after 20:00 ET would roll to the next UTC date and silently fall out of the day's stats. The current force-close/stop-out paths never produce post-20:00-ET closes, so this is latent, not live; the docstring acknowledges the assumption.
**Fix:** No change needed for the current strategy. If after-hours closes ever become possible, store the ET calendar date explicitly (a separate `session_date` column) rather than depending on `DATE()`'s UTC conversion.

---

_Reviewed: 2026-07-07T14:42:19Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
