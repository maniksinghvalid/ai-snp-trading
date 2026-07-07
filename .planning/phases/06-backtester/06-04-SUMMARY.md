---
phase: 06-backtester
plan: 04
subsystem: backtester-report
tags: [backtester, metrics, csv, report, wave-2]
dependency-graph:
  requires:
    - tests.backtester.fixtures.make_trade_log (06-01)
  provides:
    - backtester.report.compute_metrics(trades) -> dict
    - backtester.report.write_report(trades, output_dir) -> dict
  affects:
    - backtester/harness.py (06-05) — will call write_report(trade_log, output_dir) at end of a replay run
    - backtester/run.py (06-06) — CLI entry point wiring the report output_dir flag
tech-stack:
  added: []
  patterns:
    - "stdlib csv.DictWriter for flat trade-log export (no third-party CSV dependency)"
    - "realized_pnl derived inline per trade, never read from a stored DB field"
key-files:
  created:
    - backtester/report.py
  modified:
    - tests/backtester/test_report.py (no changes needed — Wave-0 stub's real assertions turned GREEN as-is)
decisions:
  - "06-04: win_rate/gross_profit/gross_loss use derived realized_pnl > 0 (not r_multiple > 0, which bot/state/store.py::get_daily_trade_stats uses for its own win definition) — plan's <behavior> block explicitly specifies realized_pnl as the win criterion for this module; on the fixture the two definitions agree (no case where sign(pnl) != sign(r_multiple)), so this is a documented choice, not a fixed bug"
  - "06-04: profit_factor==float('inf') is NOT special-cased for JSON serialization — json.dump's default allow_nan=True already emits inf as the (non-strict-RFC-8259 but valid-Python-json) literal Infinity, and json.load reads it back as float('inf'), so summary.json round-trips exactly without a string/sentinel encoding"
  - "06-04: docstring rewritten to avoid the literal substring 'get_closed_trades' (mirrors the 06-01 Task 2 self-inflicted grep false-positive) so the plan's verification grep -L 'get_closed_trades' backtester/report.py correctly lists the file as not depending on the empty trades table"
metrics:
  duration_minutes: 10
  completed: 2026-07-07
---

# Phase 06 Plan 04: Backtest Performance Report Summary

Built `backtester/report.py`: `compute_metrics` (win rate, avg R-multiple, profit
factor, max drawdown, total trades — deriving realized_pnl per trade rather than
reading a stored field, since nothing in `bot/` ever writes the `trades` table) and
`write_report` (per-trade `trades.csv` + `summary.json` via stdlib `csv`/`json`, no new
dependencies).

## What Was Built

**Task 1 — `compute_metrics`:**
- `realized_pnl = (exit_price - entry_price) * quantity`, derived per trade — mirrors
  `bot/state/store.py::get_daily_trade_stats`'s own inline PnL formula; the `trades`
  table has no `realized_pnl` column (`bot/state/migrations.py`), so this must never be
  read as a stored field.
- `win_rate` = fraction of trades with derived realized_pnl > 0 (0.0 on empty list).
- `avg_r_multiple` = mean of `t["r_multiple"]` across all trades (0.0 on empty list).
- `profit_factor` = gross_profit / gross_loss; `float("inf")` when gross_loss == 0.
- `max_drawdown_usd` = running peak-to-trough of the cumulative-PnL curve, iterating
  trades in the order received (harness supplies exit-time / chronological order).
- Empty trade list returns a fully zeroed dict without raising.

**Task 2 — `write_report`:**
- `os.makedirs(output_dir, exist_ok=True)` then writes `trades.csv` via
  `csv.DictWriter` with fields `[code, entry_price, exit_price, quantity, exit_reason,
  r_multiple, closed_at]` (one row per trade) and `summary.json` via
  `json.dump(compute_metrics(trades), ...)`.
- Returns the metrics dict so callers (06-05 harness, 06-06 CLI) can print/log it
  without re-parsing `summary.json`.

## Verification

```
python3 -m pytest tests/backtester/test_report.py -q   → 3 passed
python3 -m pytest tests/ -q                             → 604 passed, 2 skipped
grep -c 'realized_pnl' backtester/report.py             → 3 (computed inline, no stored-field read)
grep -n 'import csv' backtester/report.py               → line 18 (stdlib, no third-party CSV lib)
grep -L 'get_closed_trades' backtester/report.py        → backtester/report.py (does not depend on the empty trades table)
```

No file under `bot/` was touched — full suite stability (604 passed, same 2
pre-existing skips as before this plan) confirms no regression.

## Deviations from Plan

**[Rule 1 - Bug] Docstring literal string broke its own grep acceptance check**
- **Found during:** Task 1 verification (`grep -L 'get_closed_trades' backtester/report.py` returned no match — i.e. the file DID contain the literal substring, failing the "does not depend on" acceptance criterion)
- **Issue:** The module docstring explained the design correction using the literal text `StateStore.get_closed_trades()` (to say the module explicitly does NOT call it), which itself satisfied the grep pattern the acceptance criterion checks against — a repeat of the exact same self-inflicted false positive documented in 06-01's SUMMARY for `test_execution.py`.
- **Fix:** Reworded the docstring to describe "StateStore's closed-trade query" without embedding the literal method-name string.
- **Files modified:** backtester/report.py
- **Commit:** 185439b (Task 1, caught before commit — not a separate fix-up)

No other deviations — plan executed exactly as written.

## Known Stubs

None. Both `compute_metrics` and `write_report` are fully wired to real inputs (the
harness-supplied trade-log list) and produce real output files; no placeholder/mock
data paths.

## Threat Flags

None. Both threats in the plan's `<threat_model>` (T-06-07 empty-trades-table
avoidance, T-06-08 output_dir path traversal) were already dispositioned by the plan
(`mitigate` / `accept` respectively) and no new surface was introduced beyond what the
plan anticipated — `write_report`'s `output_dir` remains an operator-chosen local path
with no untrusted-remote input.

## Self-Check: PASSED

- FOUND: backtester/report.py
- FOUND commit 185439b (Task 1: compute_metrics)
- FOUND commit d4d24f5 (Task 2: write_report)
