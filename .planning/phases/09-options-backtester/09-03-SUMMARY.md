---
phase: 09-options-backtester
plan: 03
subsystem: backtester-options-engine
tags: [daily-replay, credit-spreads, import-not-copy, fill-model, sortino, sharpe]
dependency-graph:
  requires: [backtester/options/data.py::OptionChainSource, backtester/options/greeks.py::atm_iv/iv_rank/implied_vol/bs_delta, bot/options/strategy.py (all 9 pure functions), bot/options/config.py::OptionsConfig, backtester/report.py::_sharpe_ratio/_sortino_ratio/_calmar_ratio/_win_loss_stats]
  provides: [backtester/options/engine.py::OptionsBacktestEngine, backtester/options/engine.py::build_rows/synthesize_bid_ask/leg_fill_price/settle_at_expiry, backtester/options/report.py::compute_options_metrics/write_options_report/build_options_equity_curve]
  affects: [09-04-PLAN.md (options_run.py CLI wires OptionsBacktestEngine + write_options_report end to end, then runs the real IS/OOS hypothesis backtests)]
tech-stack:
  added: []
  patterns: [import-not-copy (D-02), live cap-gate-order mirroring (D-13), fill-model reuse (backtester/execution.py adverse-slippage convention), ratio-math reuse without P&L-shape reuse (D-16)]
key-files:
  created:
    - backtester/options/engine.py
    - backtester/options/report.py
    - tests/backtester/options/test_engine.py
    - tests/backtester/options/test_report.py
  modified:
    - .planning/phases/09-options-backtester/09-VALIDATION.md
decisions:
  - "Position dict's credit_per_spread is the FILL-adjusted credit (post-slippage sum of SELL fills minus BUY fills), not pick_strikes' pre-fill mid-based sel[\"credit\"] -- live's own credit_per_spread field is the pre-fill sel[\"credit\"]; the backtest intentionally uses the more realistic post-slip number for both max_loss_usd and every downstream manage-time comparison, since a backtest with no execution-side system to reconcile against has no reason to keep live's pre-fill approximation"
  - "min_leg_volume is captured at OPEN time from the build_rows candidate rows (which carry bar_volume), not recomputed at close -- pos[\"legs\"] entries are bot.options.strategy._leg() projections (code/right/strike/side/mid) with no volume field, so the trade-log's min_leg_volume would otherwise be unrecoverable at close time"
  - "Expiry settlement (today >= expiry) is checked BEFORE mark/manage_decision in _manage_day, not after -- an expiring position's decision must always be settle-at-intrinsic, never a manage_decision exit reason racing against it on the same day"
metrics:
  duration: "~50 minutes"
  completed: 2026-08-17
---

# Phase 9 Plan 03: Daily replay engine + options report glue Summary

Built the daily replay engine (`OptionsBacktestEngine`) that feeds day-t chain rows through
the unmodified Phase 8 `bot/options/strategy.py` functions in the live portfolio-cap order,
fills legs at mid ± slippage, manages/settles positions with live-identical P&L arithmetic,
and the report glue that turns the resulting trade log into `trades.csv`/`summary.json`
using only `backtester/report.py`'s reusable ratio math — never its per-share stock pipeline.

## What Was Built

- **`backtester/options/engine.py`**: `synthesize_bid_ask`/`leg_fill_price`/`settle_at_expiry`
  (verbatim from RESEARCH.md's code examples); `build_rows` (turns `OptionChainSource
  .contracts_for_day()` output into the exact live `screen_options` row shape — `code`,
  `right`, `strike`, `delta`, `bid`, `ask`, `open_interest`, `expiry`, `dte` — dropping any
  contract whose `implied_vol` fails to solve); `OptionsBacktestEngine` with `run(days)` /
  `run_day(day)` mirroring `bot/options/service.py`'s `_job_entry_scan`/`_scan_and_open` gate
  order exactly (daily-loss breaker realized-only check BEFORE the per-day cap, then
  `sorted(self.chains)` per-underlying loop with break-on-cap/continue-if-already-open,
  `passes_entry_gate` → `pick_expiry` → `pick_strikes` → `size_position`); `_manage_day`
  (expiry settlement checked before `manage_decision`, missing-bar legs carry-forward via
  `last_known_close` for MARKING only, trade-log row records `carried_mark`); realized P&L
  is byte-for-byte `(credit - net_exit) * 100 * qty`, matching
  `bot/options/service.py:770-776`. Module docstring documents the four intentional
  divergences from live (daily decision point, `sorted(symbols)` iteration order, synthesized
  bid/ask, carry-forward marking).
- **`backtester/options/report.py`**: imports only `_sharpe_ratio`/`_sortino_ratio`/
  `_calmar_ratio`/`_win_loss_stats` from `backtester.report`; `build_options_equity_curve`
  (NYSE-day walk reusing `backtester.options.data.trading_days`); `compute_options_metrics`
  (win/loss stats, Sharpe/Sortino/Calmar, trade-sequence + daily-equity drawdown computed
  inline, `avg_credit_captured_pct`, `per_symbol` breakdown, `assumptions` block; empty trade
  list returns a zeroed dict without raising); `write_options_report` (writes `trades.csv`
  with the D-16 column order + `summary.json`).
- **`tests/backtester/options/test_engine.py`** (18 tests total across engine+report): a
  hand-built `_FakeChain` test double (OptionChainSource-shaped) plus a BS-priced put-strike
  ladder fixture (`_put_chain`, real `bs_price` — no hand-tuned delta literals); import
  identity for all nine strategy functions; no-broker-imports source grep; `build_rows` key
  shape + IV-failure drop; per-day cap, concurrent cap, already-open skip, daily-loss breaker
  (evaluated before the per-day cap), manage-before-entry-scan ordering, deterministic
  `sorted(symbols)` iteration (two independent engine builds produce an identical `trade_log`);
  fill/close arithmetic against a hand-computed literal (`106.80`, not recomputed from the
  engine's own helpers), `manage_decision` priority (assignment_guard overrides profit_target),
  expiry settlement at intrinsic (OTM + ITM), carried-mark-on-missing-bar.
- **`tests/backtester/options/test_report.py`**: output-shape (CSV header == `_OPTIONS_CSV_FIELDS`,
  every D-16 summary key present), hand-computed win-rate/profit-factor/total-P&L, an
  AST-based test that strips the module docstring and asserts the stock-shaped helper names
  never appear in the executable code that follows, empty-trades zeroed-metrics contract, and
  an equity-curve flat-day-carry-forward check.

## Task Commits

1. **T-09-07/08 tests: engine entry path, cap gate order, fill/settlement** — `3e33e14` (test)
2. **T-09-07/08 impl: daily replay engine** — `80b563f` (feat)
3. **T-09-09: options report glue** — `5813ac3` (feat)

**Plan metadata:** committed at the end of this task (docs).

## Files Created/Modified

- `backtester/options/engine.py` — daily replay engine, fill model, manage/settlement (429 lines)
- `backtester/options/report.py` — trades.csv/summary.json writer using reused ratio math (183 lines)
- `tests/backtester/options/test_engine.py` — 13 tests
- `tests/backtester/options/test_report.py` — 5 tests
- `.planning/phases/09-options-backtester/09-VALIDATION.md` — T-09-07..09 statuses flipped to green

## Decisions Made

- `credit_per_spread` recorded on the position dict is the fill-adjusted (post-slippage)
  credit, not `pick_strikes`' pre-fill `sel["credit"]` — see key-decisions in frontmatter.
- `min_leg_volume` is captured once at open time from the `build_rows` candidate rows, since
  `bot.options.strategy._leg()`'s leg projection carries no volume field.
- Expiry settlement is checked before `manage_decision` runs each day, so an expiring
  position is never raced against a manage-time exit reason on its own expiry day.

## Deviations from Plan

None — plan executed as written. Both `<behavior>` bullet lists in T-09-07/T-09-08 are covered
1:1 by named tests (`test_cap_gate_order`, `test_concurrent_cap_blocks_even_with_day_cap_room`,
`test_already_open_underlying_is_skipped`, `test_daily_loss_breaker_blocks_entries`,
`test_manage_runs_before_entry_scan`, `test_deterministic_underlying_order`,
`test_build_rows_keys_and_drops_invalid_iv`, `test_fill_and_settlement`,
`test_assignment_guard_overrides_profit_target`, `test_expiry_settles_at_intrinsic`,
`test_carried_mark_on_missing_bar`). One CLAUDE.md-driven adjustment: the module docstrings
in both new files avoid the literal tokens `bot.gateway`/`moomoo`/`report.compute_metrics`/
`report.write_report`/`_net_pnl` in prose (paraphrased instead), since the plan's own
acceptance-criteria greps (`grep -v '^#' file | grep -c '...'`) strip only `#`-comment lines,
not triple-quoted docstring text — a literal mention in the docstring would have failed the
acceptance criteria it was trying to satisfy.

## TDD Gate Compliance

- T-09-07/T-09-08: RED (`3e33e14 test(09-03): add tests for engine entry path, cap gate order,
  fill/settlement`) precedes GREEN (`80b563f feat(09-03): daily replay engine`). Same
  commit-granularity precedent as Plan 09-02 (one test commit, one implementation commit,
  covering both tasks' behavior in a single RED/GREEN pair since they share one file). Gate
  satisfied.
- T-09-09 is `type="auto"` (no `tdd="true"`) — no RED/GREEN gate applies; test + implementation
  shipped in one commit per the plan's own action text (no separate RED requirement stated).

## Verification

- `pytest tests/backtester/options/ -x -q` — 54 passed
- `pytest tests/backtester/ tests/options/ -q` — 317 passed
- `pytest -q` (full suite) — **1019 passed, 1 skipped** (1001 baseline + 18 new; no regression)
- `grep -c 'from bot.options.strategy import' backtester/options/engine.py` — 1, all nine names present
- `grep -v '^#' backtester/options/engine.py | grep -c 'bot.gateway\|moomoo\|place_order'` — 0
- `grep -c 'sorted(' backtester/options/engine.py` — 5
- `grep -c 'credit - net_exit' backtester/options/engine.py` — 3
- `grep -c 'def settle_at_expiry' backtester/options/engine.py` — 1
- `grep -c 'from backtester.report import' backtester/options/report.py` — 1, exactly the four
  private ratio names
- `grep -v '^#' backtester/options/report.py | grep -c 'report.compute_metrics\|report.write_report\|_net_pnl'` — 0
- `grep -rn 'bot.gateway\|moomoo\|place_order' backtester/options/` — no matches (directory-wide)
- Determinism: `test_deterministic_underlying_order` builds two independent engines over the
  same fixture and asserts identical `trade_log` contents

## Self-Check: PASSED
