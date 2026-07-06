---
phase: 05-service-orchestration-and-reliability
plan: "04"
subsystem: dashboard, reporting, supervision, observability
tags: [html-report, svg-histogram, launchd, shell-supervisor, structlog, report-builder, dash-01, svc-03]

# Dependency graph
requires:
  - phase: 05-01
    provides: TradingBot with _job_eod_report placeholder + StateStore
  - phase: 05-03
    provides: TelegramAlerter.format_daily_summary + alert_summary (ALERT-03)
  - phase: 01-01
    provides: StateStore.get_open_positions, migrations (trades/positions schema)

provides:
  - "bot/service/report.py: ReportBuilder with build_daily_html, _build_r_histogram (returns dict), write_reports"
  - "StateStore.get_closed_trades(session_date) accessor in bot/state/store.py"
  - "_job_eod_report wired in bot/service/bot.py: fetch + build + write + ALERT-03 dispatch"
  - "deploy/com.bot.trading.plist: launchd LaunchAgent template (D-05/D-06)"
  - "deploy/run_forever.sh: portable while-loop fallback supervisor"
  - "deploy/README.md: launchctl commands, chmod 600, systemd Linux note"
  - "configure_logging() confirmed at startup in bot/main.py (SVC-03)"
affects:
  - phase-06-backtester

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ReportBuilder: store-injected class, build_daily_html returns self-contained no-JS HTML"
    - "_build_r_histogram returns dict {label: count} with float-parseable labels for test assertions"
    - "SVG histogram: 7 buckets [-3..-1, 0..2, 3], proportional <rect> bars (negative=red, positive=green)"
    - "run_in_executor pattern for file I/O off the asyncio event loop (T-05-04-04)"
    - "fire-and-forget ALERT-03 via asyncio.create_task(alerter.send(summary))"
    - "launchd LaunchAgent plist template with placeholder secrets + chmod 600 mandate"

key-files:
  created:
    - bot/service/report.py
    - deploy/com.bot.trading.plist
    - deploy/run_forever.sh
    - deploy/README.md
  modified:
    - bot/state/store.py (added get_closed_trades)
    - bot/service/bot.py (_job_eod_report replaced + import)
    - tests/service/test_report.py (removed xfail markers)
    - tests/service/test_bot.py (added test_eod_report_job_writes_reports_and_dispatches_summary)

key-decisions:
  - "_build_r_histogram returns dict {label: count} (not SVG) to match test interface (test uses float(k) on keys)"
  - "Bucket labels are float-parseable strings ('3' not '3+') so test dict-key float conversion doesn't raise ValueError"
  - "SVG histogram heading always emitted (even when no trades) so 'r-multiple' text is always present in HTML"
  - "PnL written as 'PnL:' (not 'P&L:') in raw HTML so test string 'pnl' in html_lower matches without HTML-entity parsing"
  - "_job_eod_report uses single run_in_executor call for fetch+build+write (minimizes thread pool round-trips)"
  - "plist ships placeholders only (T-05-04-02); template note mandates chmod 600 before deployment"

patterns-established:
  - "Report HTML: self-contained, no <script>, no CDN; all CSS in single inline <style> block"
  - "Report I/O: file writes via run_in_executor to keep asyncio event loop unblocked"
  - "Alert dispatch: asyncio.create_task(alerter.send(...)) for fire-and-forget (ALERT-04)"

requirements-completed: [DASH-01]

# Metrics
duration: 18min
completed: "2026-06-24"
---

# Phase 05 Plan 04: Dashboard, EOD Report, Supervisor Summary

**Self-contained no-JS HTML daily P&L dashboard with inline-SVG R-multiple histogram, launchd LaunchAgent supervisor, and wired EOD report job dispatching ALERT-03 Telegram summary**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-06-24T~08:30Z
- **Completed:** 2026-06-24T~08:48Z
- **Tasks:** 3 (TDD-guided Task 1, implementation Task 2, config/infra Task 3)
- **Files modified:** 7

## Accomplishments

- ReportBuilder (bot/service/report.py): `build_daily_html` produces complete self-contained HTML with inline-SVG R-multiple histogram + last-20-trades table + open-positions table; `_build_r_histogram` returns 7-bucket dict with float-parseable labels; `write_reports` writes dated + latest.html (D-16, defensive try/except)
- `_job_eod_report` wired in bot/service/bot.py: fetches trades + positions via `get_closed_trades` + `get_open_positions`, builds HTML, writes reports in `run_in_executor`, dispatches ALERT-03 summary via `asyncio.create_task` (all non-blocking, D-02)
- launchd template + shell fallback: `com.bot.trading.plist` with KeepAlive/RunAtLoad/ThrottleInterval=30/EnvironmentVariables/StandardOutPath (placeholder secrets, T-05-04-02); `run_forever.sh` portable `while true` supervisor; `deploy/README.md` with launchctl commands + chmod 600 requirement + systemd Linux note

## Task Commits

1. **Task 1: ReportBuilder + store accessor + green tests** - `f81bf2f` (feat)
2. **Task 2: Wire _job_eod_report + alerter dispatch** - `c115ac0` (feat)
3. **Task 3: launchd plist + shell fallback + deploy README** - `073caf6` (chore)

## Files Created/Modified

- `bot/service/report.py` — ReportBuilder class + module-level build_daily_html/write_reports functions; inline SVG histogram generation; html.escape on all free-text fields (T-05-04-01)
- `bot/state/store.py` — Added get_closed_trades(session_date): queries trades WHERE DATE(closed_at)=? LIMIT 20, row_factory arrow fetchall arrow reset pattern (RESEARCH Open Q1)
- `bot/service/bot.py` — _job_eod_report replaced: fetch+build+write in run_in_executor, ALERT-03 fire-and-forget dispatch, audit entry; imported build_daily_html + write_reports
- `tests/service/test_report.py` — Removed xfail markers; both test_html_report_sections and test_r_histogram_buckets now GREEN (DASH-01)
- `tests/service/test_bot.py` — Added test_eod_report_job_writes_reports_and_dispatches_summary
- `deploy/com.bot.trading.plist` — launchd LaunchAgent template (D-05/D-06)
- `deploy/run_forever.sh` — portable while-true fallback supervisor (executable)
- `deploy/README.md` — launchctl commands, chmod 600 mandate, systemd note

## Decisions Made

- **Histogram dict interface:** `_build_r_histogram` returns `dict {label: count}` (not an SVG string) to match the test interface where `float(k)` is called on every bucket key. Changed last bucket label from "3+" to "3" to avoid `float("3+")` ValueError. Display label "3+" is used only in SVG rendering via `_DISPLAY_LABELS`.
- **PnL text:** Used "PnL:" (not "P&L:") so the test `"pnl" in html_lower` matches without HTML-entity decoding.
- **SVG heading always present:** `_build_histogram_svg` always emits the R-Multiple Histogram heading even when no trades, so test `"r-multiple" in html_lower` always passes.
- **Executor scope:** Single `_fetch_build_write` closure in `run_in_executor` for fetch+build+write (minimizes thread-pool round-trips while keeping event loop unblocked, T-05-04-04).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _build_r_histogram interface mismatch**
- **Found during:** Task 1 (test execution)
- **Issue:** RESEARCH Pattern 5 shows `_build_r_histogram` returning SVG string, but the test expects a `dict`. The test also uses `float(k)` on all bucket keys, which would raise ValueError for label "3+".
- **Fix:** Changed `_build_r_histogram` to return `dict {label: count}` with float-parseable labels; moved SVG generation to internal `_build_histogram_svg`. Added `_DISPLAY_LABELS` for "3+" in SVG rendering only.
- **Files modified:** bot/service/report.py
- **Verification:** Both test_html_report_sections and test_r_histogram_buckets green
- **Committed in:** f81bf2f

**2. [Rule 1 - Bug] PnL text won't match test assertion**
- **Found during:** Task 1 (analyzing test assertions)
- **Issue:** `<b>P&amp;L:</b>` in raw HTML becomes `p&amp;l:` in lowercase. Neither `"pnl"` nor `"p&l"` is a substring of `p&amp;l:`.
- **Fix:** Changed display text to "PnL:" which lowercases to "pnl:" matching `"pnl" in html_lower`.
- **Files modified:** bot/service/report.py
- **Verification:** test_html_report_sections passes
- **Committed in:** f81bf2f

---

**Total deviations:** 2 auto-fixed (both Rule 1 — test interface / assertion alignment)
**Impact on plan:** Both fixes necessary for tests to pass; no scope creep.

## Issues Encountered

None beyond the auto-fixed deviations above.

## Known Stubs

None — all report sections render live data from StateStore; no hardcoded placeholder values in report output.

## Threat Flags

No new threat surface beyond plan threat model. All mitigations applied: html.escape on all free-text fields (T-05-04-01), placeholder-only plist (T-05-04-02), file I/O off event loop (T-05-04-04), paper-safety vars hardcoded in plist (T-05-04-SAFE).

## Next Phase Readiness

- Phase 6 (Backtester) can proceed; it depends on bot/strategy/ and bot/position/ (Phases 3-4), not the report pipeline.
- All DASH-01 requirements fulfilled: static no-JS offline HTML dashboard with SVG histogram + two tables; dated + latest.html written; reports/ gitignored.
- SVC-03 (configure_logging at startup) confirmed in bot/main.py.

## Self-Check: PASSED

- bot/service/report.py: FOUND
- bot/state/store.py (get_closed_trades): FOUND
- bot/service/bot.py (_job_eod_report wired): FOUND
- deploy/com.bot.trading.plist: FOUND
- deploy/run_forever.sh: FOUND
- deploy/README.md: FOUND
- Commits f81bf2f, c115ac0, 073caf6: VERIFIED

---
*Phase: 05-service-orchestration-and-reliability*
*Completed: 2026-06-24*
