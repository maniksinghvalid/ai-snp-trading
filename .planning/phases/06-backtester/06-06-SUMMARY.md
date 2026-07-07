---
phase: 06-backtester
plan: 06
subsystem: backtester-cli
tags: [backtester, cli, argparse, composition-root, wave-4, BT-01, BT-03, BT-04]
dependency-graph:
  requires:
    - backtester.feed.SimulatedBarFeed / BacktestWindowError (06-02)
    - backtester.report.write_report (06-04)
    - backtester.harness.BacktestHarness (06-05)
  provides:
    - backtester.run.build_arg_parser() -> argparse.ArgumentParser
    - backtester.run.main(argv=None) -> int
    - backtester.run._scratch_db_path() (Pitfall 4 live-DB collision guard)
    - backtester.run._trading_days(start, end) (NYSE calendar iteration)
  affects:
    - Phase 6 verification/`/gsd-verify-work` (this is the phase's final composition root)
    - Phase 7 (07-06) exit-model comparison work, which runs on top of this CLI
tech-stack:
  added: []
  patterns:
    - "CLI composition root mirrors bot/main.py's construction order (config load -> ConfigError->stderr+exit(1) -> component construction -> asyncio.run)"
    - "pandas_market_calendars reused directly for trading-day iteration (no reimplemented NYSE holiday logic; mirrors bot/scanner/calendar.py's own wrapper pattern)"
key-files:
  created:
    - backtester/run.py
  modified:
    - tests/backtester/test_run.py
decisions:
  - "06-06: main(argv=None) returns an int exit code rather than calling sys.exit directly -- trivially testable without catching SystemExit; sys.exit(main()) only at the __main__ guard"
  - "06-06: input validation (dates/symbols) runs BEFORE configure_logging/load_strategy_config -- cheapest checks first, matches V5 'before any fetch' requirement unambiguously"
  - "06-06: DB-collision guard tested by monkeypatching the module's own _scratch_db_path() to return DEFAULT_DB_PATH, since the real function always generates a fresh uuid path that can never naturally collide -- this proves the guard fires without requiring a --db override flag the plan doesn't specify"
  - "06-06: trading-day iteration reuses pandas_market_calendars directly (mcal.get_calendar('NYSE').valid_days(...)) rather than importing bot/scanner/calendar.py's private _nyse singleton -- avoids reaching into another module's underscore-prefixed internals while still not reimplementing holiday/weekend logic"
  - "06-06: the end-to-end CLI test reuses tests/backtester/test_harness.py's dedicated signal/fill/stop-out dataset and _mock_yf_download (not tests/backtester/fixtures.py's ahead-only fixture named in the plan's read_first) -- the ahead-only fixture's breakout bar closes below its own high and never clears the real SignalEngine I2 gate (bar_aggregator's hod snapshot includes the bar's own high), so it produces zero real trades through the full pipeline; this was already discovered and documented as 06-05-SUMMARY.md deviation 5, and reusing that proven dataset avoids re-deriving an equivalent fixture from scratch"
  - "06-06: no --cache-dir/--db override CLI flag added (plan's build_arg_parser spec names exactly 5 flags) -- SimulatedBarFeed's default cache_dir='backtester/cache' and the run's backtester/runs/<uuid>/state.db scratch path are both gitignored directories, so test runs writing into them is consistent with production behavior and leaves no git-visible side effect"
metrics:
  duration_minutes: 20
  completed: 2026-07-07
---

# Phase 06 Plan 06: Backtester CLI Summary

Built `backtester/run.py`, the Phase 6 composition root: an argparse CLI
(`--symbols/--start/--end/--rules-json/--output-dir`) that loads the same `rules.json` via
the same `bot.config.loader.load_strategy_config` the live bot uses, validates all operator
input before any yfinance fetch, hard-refuses to run against the live paper-trading state
DB, and wires `SimulatedBarFeed -> BacktestHarness -> backtester.report.write_report` across
every NYSE trading day in the requested range. This is the last file of Phase 6 — the CLI an
operator actually runs to backtest Trend Join Long.

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-07
- **Tasks:** 2 (landed in one commit — see Decisions)
- **Files modified:** 2 (1 created, 1 created/modified test file)

## Accomplishments

- `build_arg_parser()`/`main(argv=None)` in `backtester/run.py`: argparse CLI, V5 input
  validation (date parsing, non-empty symbols) before any config load or fetch, `ConfigError`
  → `[ERROR]` stderr + exit 1 mirroring `bot/main.py`'s own block
- Pitfall-4 live-DB collision guard: the scratch `StateStore` path is always
  `backtester/runs/<uuid>/state.db`; a guard refuses to run if that path ever resolves to
  `data/bot_state.db`
- Full feed → harness → report wiring: iterates NYSE trading days via
  `pandas_market_calendars`, calls `harness.setup_day(day, symbols)` per day, runs the async
  replay via `asyncio.run(harness.run())`, then `write_report(harness.trade_log,
  args.output_dir)`
- `BacktestWindowError` (out-of-window `--start`) surfaced as a loud `[ERROR]` + non-zero
  exit — never a silent empty report (BT-04/Open-Q1)
- Zero broker-gateway construction anywhere in the module (grep-verified: 0 occurrences)

## Task Commits

Both tasks landed in a single commit since Task 2 extends the exact same `main()` function
Task 1 wrote (splitting the working, tested diff after the fact would have required
artificially breaking passing tests to fabricate an intermediate state — same rationale
06-05-SUMMARY.md documented for its own two tasks):

1. **Task 1 + Task 2: CLI argparse, config load, DB guard, input validation, feed→harness→report wiring** - `cab87ae` (feat)

## Files Created/Modified

- `backtester/run.py` - CLI entry point: `build_arg_parser`, `main`, `_scratch_db_path`, `_trading_days`
- `tests/backtester/test_run.py` - 7 tests: bad-date/empty-symbols validation, ConfigError exit 1, DB-collision guard, no-broker-construction assertion, out-of-window `BacktestWindowError` handling, full end-to-end replay writing `summary.json` + a non-empty `trades.csv`

## Decisions Made

See frontmatter `decisions` — key points: `main()` returns an int exit code (not
`sys.exit`) for testability; input validation runs before config load; the DB-collision
guard test monkeypatches `_scratch_db_path` directly since the real function can never
naturally collide; trading-day iteration reuses `pandas_market_calendars` directly rather
than reaching into `bot/scanner/calendar.py`'s private singleton; the end-to-end test reuses
`test_harness.py`'s dedicated dataset instead of the ahead-only fixture named in the plan's
`read_first`, because the ahead-only fixture cannot clear the real `SignalEngine` I2 gate.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug avoidance / test-design] End-to-end test dataset swapped from the plan's named fixture to test_harness.py's dedicated dataset**
- **Found during:** Task 2, before writing the end-to-end test
- **Issue:** The plan's `<read_first>` names `tests/backtester/fixtures.py`'s
  `make_ahead_only_5m_dataset` as the feed seam for the end-to-end test. That fixture's
  breakout bar closes below its own high (`close < hod`), so it never clears the real
  `SignalEngine.on_bar()`'s I2 gate (`bar_aggregator`'s `hod` snapshot includes the bar's own
  high) — it was already proven, in 06-05-SUMMARY.md's own deviation 5, to produce zero real
  signals through the full live pipeline (only usable via hand-built `OrderIntent`s in
  06-01/06-03's narrower tests). Using it here would make the end-to-end test's own
  acceptance criterion ("trades.csv has >= 1 data row") unsatisfiable.
- **Fix:** Reused `tests/backtester/test_harness.py`'s already-proven dedicated
  signal/fill/stop-out/exit dataset and its `_mock_yf_download` helper (imported directly,
  no duplication) for the CLI's own end-to-end test.
- **Files modified:** tests/backtester/test_run.py
- **Commit:** cab87ae (Task 2, part of the combined commit)

---

**Total deviations:** 1 auto-fixed (test-design correction, no production-code impact)
**Impact on plan:** No scope creep — `backtester/run.py` itself matches the plan's behavior
spec exactly; only the test's data fixture choice changed, and only because the plan-named
fixture was already known (from a prior plan's own documented finding) to be structurally
incapable of clearing the real signal gates it would need to prove the end-to-end wiring.

## Issues Encountered

None beyond the deviation above — all tests passed on first implementation attempt with no
debugging cycles required.

## User Setup Required

None - no external service configuration required.

## Verification

```
python3 -m pytest tests/backtester/test_run.py -q   → 7 passed
python3 -m pytest tests/backtester/ -q               → 28 passed (no skips remain)
python3 -m pytest tests/ -q                          → 617 passed, 1 skipped (pre-existing)
grep -c 'MoomooGateway' backtester/run.py            → 0
grep -n 'write_report|asyncio.run|BacktestHarness' backtester/run.py → all three present
git status --short backtester/                       → only backtester/run.py tracked
                                                        (cache/ and runs/ scratch dirs
                                                        created during tests remain
                                                        gitignored, untracked)
```

No file under `bot/` was touched. Full-suite stability (617 = prior 610 + 7 new CLI tests)
confirms no regression.

## Known Stubs

None. `main()` is fully wired end-to-end against the real reused pipeline (feed → harness →
report); no placeholder/mock data paths remain in `backtester/run.py`.

## Threat Flags

None. All four threat-register entries this plan owned (T-06-13 live-DB guard, T-06-14 CLI
input validation, T-06-15 broker-free construction, T-06-16 loud out-of-window failure) are
exactly the mitigations implemented above — no new unmitigated surface was introduced.

## Next Phase Readiness

Phase 6 (backtester) is now feature-complete: `backtester/run.py` is the operator-facing CLI
that replays Trend Join Long against historical yfinance 5m bars through the exact same
`SignalEngine`/`RiskEngine`/`PositionManager`/`TrendJoinLong` pipeline the live bot uses,
producing `summary.json` + `trades.csv`. Entire `tests/backtester/` suite is green (28
passed, no skips), and the full project suite (617 passed, 1 pre-existing skip) shows no
regression to `bot/`. Ready for `/gsd-verify-work` on Phase 6; the manual verification item
(run `backtester/run.py` with a recent live date range) remains for the operator per the
plan's `<verification>` section.

---
*Phase: 06-backtester*
*Completed: 2026-07-07*

## Self-Check: PASSED

- FOUND: backtester/run.py
- FOUND: tests/backtester/test_run.py
- FOUND commit cab87ae (Task 1 + Task 2: CLI argparse, config load, DB guard, input validation, feed→harness→report wiring)
