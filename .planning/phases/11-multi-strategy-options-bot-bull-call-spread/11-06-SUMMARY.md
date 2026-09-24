---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 06
subsystem: options-bot-service
tags: [apscheduler, options, bull-call-spread, multi-strategy, service-composition, reconcile]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 02
    provides: "manage_decision_debit(mark, debit, width, dte, cfg)"
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 04
    provides: "shipped rules_options.json (strategies shape), bot/main.py dispatch widened"
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 05
    provides: "OptionsBot(strategies=...), self._strategies, per-strategy entry-scan jobs, per-strategy caps"
provides:
  - "_manage_position dispatches on pos['strategy_name'] to that strategy's OptionsConfig and decision function (manage_decision_debit for bull_call_spread, manage_decision for credit) — D-21"
  - "debit close reporting: %-of-max-profit (basis='max profit') instead of %-of-credit; unchanged realized-P&L math (realized_per_spread = credit - net_exit) proven correct for both signs — D-19"
  - "reconcile(startup=True) fails closed: any OPEN/OPENING/CLOSING row whose strategy_name is not in the loaded book -> NEEDS_ATTENTION + Telegram alert + options_unknown_strategy audit event, before the OPENING/CLOSING branch — D-29"
  - "_fmt_summary / _options_html name the strategy (Strategy column/leading field), HTML-escaped — D-24"
  - "main() composes ONE OptionsBot for every strategy via load_options_book(rules_path); cfg = book.strategies[0] carries the shared execution/service/risk values, DB, kill file, report dir — D-08/D-20/D-25"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Manage-side dispatch mirrors the entry-side pattern from 11-05: a strategy is looked up by pos['strategy_name'] in self._strategies and threaded as an explicit parameter into the decision function, never read from self._cfg"
    - "Debit vs credit branching keyed off pos['structure'] == 'bull_call_spread' (the stored fact about the position), while thresholds/parameters come from the position's own strategy config (the two concerns kept separate per D-21)"
    - "D-29 startup-only fail-closed guard placed before the pre-existing OPENING/CLOSING branch in the same reconcile loop, reusing the exact NEEDS_ATTENTION + alert + audit shape already established for options_reconcile_incomplete"

key-files:
  created: []
  modified:
    - bot/options/service.py
    - tests/options/test_service.py

key-decisions:
  - "manage_decision_debit's sign math (debit = -credit_per_spread) and the existing close math (realized_per_spread = credit - net_exit) needed zero changes to work for both structures — verified against the NVDA worked example: realized 2420.00 for 5 spreads closed at 8.00/1.20, pct-of-max-profit 60.2%"
  - "_manage_position keeps a defense-in-depth guard (skip + log + return 0.0) for a position whose strategy_name is not in self._strategies, behind the D-29 startup reconcile guard that should already have caught it — belt-and-suspenders, not a substitute for the startup check"
  - "_fmt_exit's basis parameter defaults to 'credit' so the plan's own acceptance criterion (existing 4-positional-argument call in test_formatters_escape_free_text_fields keeps working) holds without touching that test"

requirements-completed: [MSO-08, MSO-07, MSO-05, MSO-09]

# Metrics
duration: 14min
completed: 2026-09-24
---

# Phase 11 Plan 06: Multi-strategy service composition + phase gate Summary

**The options process now dispatches every open position — credit or debit — to its own strategy's config and decision function from ONE manage job, books bull-call closes as %-of-max-profit with proven-correct realized P&L, fails closed on an orphaned strategy at startup, names the strategy in every alert/EOD surface, and `main()` finally loads the whole `rules_options.json` book so `super_bull_call` is live end-to-end alongside the unchanged `tasty_credit_spreads` book.**

## Performance

- **Duration:** ~14 min
- **Tasks:** 2/2 completed
- **Files modified:** 2 (bot/options/service.py, tests/options/test_service.py)

## Accomplishments
- `_manage_position` dispatches on `pos["strategy_name"]` (via `self._strategies.get(...)`) to the position's own `OptionsConfig`, calling `manage_decision_debit(mark, -credit, width, dte, strat_cfg)` for a `bull_call_spread` position and `manage_decision(mark, credit, dte, strat_cfg)` for a credit structure — never `self._cfg` (D-21). A position whose strategy is unknown is skipped defensively (logs `options_manage_unknown_strategy`, returns 0.0, never closes).
- Bull-call closes report `%-of-max-profit` (`max_profit = width + credit`, i.e. `width - debit`) instead of `%-of-credit`; the pre-existing close math (`net_exit`, `realized_per_spread = credit - net_exit`) is byte-identical and proven correct for the signed debit convention via the NVDA worked example (5 spreads @ 8.00/1.20 exit -> realized $2,420.00, 60.2% of max profit) and the never-stops-out / assignment-guard cases.
- `reconcile(startup=True)` now fails closed on D-29: any `OPEN`/`OPENING`/`CLOSING` row whose `strategy_name` is not in the loaded book is set `NEEDS_ATTENTION`, alerted, and audited (`options_unknown_strategy`) — before the pre-existing OPENING/CLOSING branch, so an orphaned strategy is reported even if it also died mid-order. Steady-state `reconcile()` (startup=False) is unaffected — the guard is startup-only by design.
- `_fmt_exit` names the strategy (HTML-escaped) and reports `pct_of_credit`'s denominator via a new `basis` parameter (`"credit"` default, `"max profit"` for debit); `_fmt_summary` and `_options_html` gain the strategy name (leading field / new "Strategy" column) on every open/closed row, all HTML-escaped.
- `main()` now calls `load_options_book(rules_path)` and constructs ONE `OptionsBot` for every configured strategy (`strategies=book.strategies`); `cfg = book.strategies[0]` still carries the shared execution/service/risk values, the bot's own DB/kill file/report dir, and the watchdog config — unchanged D6/D-25 invariants.
- Module docstring updated to document the manage-dispatch, D-29 fail-closed guard, and book-based `main()` composition added across this plan.
- 12 new tests added across both tasks; zero existing test assertions were removed or altered.

## Task Commits

1. **Task 1: Manage dispatch per strategy + debit close reporting + D-29 unknown-strategy guard (D-15, D-19, D-21, D-29)** - `92a8131` (feat)
2. **Task 2: Strategy name in EOD summary/HTML + book-based main() + phase-gate invariants (D-08, D-20, D-24, D-25)** - `6d148c0` (feat)

_Both tasks used `tdd="true"` in the plan; tests and implementation landed together in each task's commit, verified green (quick suite after each task, full suite before this SUMMARY) before committing._

## Files Created/Modified
- `bot/options/service.py` - imports `manage_decision_debit`; `reconcile()` D-29 guard (module docstring updated); `_manage_position` strategy dispatch (`strat_cfg`, `is_debit`, `manage_decision_debit(mark, -credit, width, dte, strat_cfg)`, pct/basis branching, `strategy_name` on the `options_position_closed` audit); `_fmt_exit(pos, reason, pnl_usd, pct_of_credit, basis="credit")` (strategy name + basis text); `_fmt_summary` (strategy name + signed premium label per open row); `_options_html` (leading "Strategy" column on both tables, colspan 7/4); `main()` (`load_options_book`, `cfg = book.strategies[0]`, `OptionsBot(..., strategies=book.strategies)`); top module docstring
- `tests/options/test_service.py` - `_seed_bull_spread`/`LONG_C`/`SHORT_C` helper; reconcile: `test_reconcile_startup_flags_unknown_strategy`, `test_reconcile_steady_state_leaves_unknown_strategy_for_startup_only`; manage: `test_manage_closes_bull_call_at_profit_target_of_max`, `test_manage_bull_call_never_stops_out`, `test_manage_bull_call_assignment_guard`, `test_manage_dispatches_each_position_to_its_own_strategy`, `test_manage_skips_position_with_unknown_strategy`; EOD/formatters: `test_eod_summary_and_report_show_strategy_names`, `test_formatters_escape_strategy_name`; main()/jobs: `test_main_builds_one_bot_for_every_strategy`, `test_main_exits_1_on_config_error`, `test_shipped_book_registers_per_strategy_jobs`

## Decisions Made
- `manage_decision_debit`'s sign math and the existing `net_exit`/`realized_per_spread` close-math formulas required zero changes for the debit case — the D-19 signed-premium convention (`credit_per_spread = -debit`) already makes them correct; confirmed against the plan's literal NVDA worked example in three separate tests (profit target, never-stops-out, assignment guard).
- `_manage_position` keeps a defense-in-depth skip for an unknown strategy (in addition to the D-29 startup reconcile guard) — belt-and-suspenders per the threat register's T-11-04 mitigation, not a substitute for the startup check (a position could theoretically reach `OPEN` status between a startup reconcile and a config edit that removes its strategy, though no live path currently does this).
- `_fmt_exit`'s `basis` parameter defaults to `"credit"` so the plan's own acceptance criterion (the existing 4-positional-argument call in `test_formatters_escape_free_text_fields` keeps working unmodified) holds.

## Deviations from Plan

None — plan executed exactly as written. One acceptance-criteria observation, not a deviation: the plan's literal `grep -rn "StateStore\|bot.state.store" bot/options/universe.py` (D-17 no-import check, unrelated to this plan's two tasks — `universe.py` was not touched) does not print nothing, because that file's own docstrings (written in plan 11-03) mention `bot.state.store.StateStore.get_watchlist_codes` descriptively, not as an import. The actual invariant (no import of `bot.state.store`) is enforced by an AST-walk test (`test_module_does_not_import_equity_state`, plan 11-03) which is part of the green full suite. No code change was made or needed.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Per STATE.md/CLAUDE.md convention, the live options-bot process (if running) must be restarted after this change lands on `develop` — `main()` now loads the whole `rules_options.json` book, so a restart is what activates `super_bull_call` live for the first time (its entry scan existed since plan 11-05, but had no manage path to close it until this plan).

## Next Phase Readiness

- Phase 11's five requirements (MSO-05, MSO-07, MSO-08, MSO-09, plus the earlier-plan MSO-01..04/06) are now fully wired end to end: config, pure strategy core, store/migration, equity-watchlist universe reader, per-strategy entry scan, per-strategy manage dispatch, D-29 fail-closed reconcile, strategy-named alerts/EOD, and a book-based `main()`.
- Full suite green: 1265 passed, 1 skipped (baseline 1253 passed / 1 skipped + 12 new tests, 0 new failures, 0 new skips). Quick suite (`tests/options tests/backtester/options`): 389 passed.
- D-25 invariant gate confirmed: `git diff --quiet 405c8d2 -- bot/options/execution.py bot/gateway/ bot/service/bot.py bot/scanner/ rules.json` exits 0 — `LegExecutor`, the gateway/SIMULATE guard, the equity bot, the scanner, and the equity `rules.json` are byte-unchanged since before this phase began.
- **Pending operator verification (not performed by this executor per its safety constraints — no OpenD connection, no live bot launch):** the phase's Manual-Only live paper run (11-VALIDATION.md) — after merge to `develop` and an options-bot restart, during RTH after the equity premarket scan, run `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json` and confirm the `options_jobs_registered` log lists `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`, and that the 10:05 scan logs `options_watchlist_loaded` with a code count. ONE options-bot instance only.
- Safety: no OpenD connection, no live bot launch, and no network call were made in this plan's verification — every test runs against the fake gateway/store/alerter test doubles and a real `OptionsStore` on a tmp-path DB.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED
