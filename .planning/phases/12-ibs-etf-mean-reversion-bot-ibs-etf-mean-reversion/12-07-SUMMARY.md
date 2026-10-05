---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 07
subsystem: operator-tooling
tags: [ibs, uat-probe, launchd, runbook, tdd]
requires: ["12-02", "12-03", "12-04", "12-05"]
provides:
  - "scripts/uat_ibs_probe.py: _p, _hr, async probe(cfg, gateway, now), async live_1lot(cfg, gateway, symbol, now), main(argv=None)"
  - "deploy/com.bot.ibs.plist (Label com.bot.ibs)"
  - "deploy/IBS-RUNBOOK.md (cutover, stop/start, first-week checks)"
  - "CLAUDE.md section: Phase 12 — IBS bot (ibs_etf_mean_reversion)"
  - "tests/ibs/test_operator_tooling.py (7 tests)"
affects: [12-08, 12-09]
tech-stack:
  added: []
  patterns: ["options-probe shape", "argparse --confirm gate exits 2 before any gateway", "probe reuses production pure functions (no re-implementation)"]
key-files:
  created:
    - scripts/uat_ibs_probe.py
    - deploy/com.bot.ibs.plist
    - deploy/IBS-RUNBOOK.md
    - tests/ibs/test_operator_tooling.py
  modified:
    - CLAUDE.md
key-decisions:
  - "Probe never creates the IBS DB: it opens cfg.state_db only if it already exists"
  - "live_1lot uses a scratch IbsStore in a tempdir and reports the scratch path on any failure so the operator can finish by hand"
  - "plist KeepAlive is {SuccessfulExit: false}: a .bot_kill_ibs exit (0) is not restarted, crashes are"
requirements-completed: [IBS-09]
duration: ~15 min
completed: 2026-10-04
---

# Phase 12 Plan 07: Operator tooling Summary

Read-only RTH probe (IBS table, broker holdings, WOULD EXIT / WOULD ENTER via the service's own parse_snapshot / decide_exits / decide_entries / size_position / trading_days_held), a `--live-1lot --confirm` one-share BUY+SELL round trip through IbsExecutor, a lint-clean launchd template, the cutover runbook, and the CLAUDE.md Phase 12 section.

## Tasks

| Task | Commits |
|------|---------|
| 1. UAT probe (TDD) | `57ebded` (RED), `8a25124` (GREEN) |
| 2. plist, runbook, CLAUDE.md (TDD) | `84ab425` (RED), `8576635` (GREEN) |

## Verification

- `python3 -m pytest tests/ibs -q`: 195 passed.
- Full suite `python3 -m pytest -q`: 1612 passed, 1 skipped (baseline 1605 + 7 new, zero regressions).
- `plutil -lint deploy/com.bot.ibs.plist`: OK. `python3 scripts/uat_ibs_probe.py --live-1lot` prints the refusal and exits 2 without contacting OpenD.
- deploy/com.bot.trading.plist and deploy/README.md untouched.
- Nothing was run against a broker; no process was started or stopped.

## Deviations from Plan

None to the plan's intent. Notes:
- The test suite has no pytest-asyncio, so async probe tests use `asyncio.run` (house pattern in tests/ibs/test_execution.py).
- Added one extra test (`test_live_1lot_buys_then_sells_one_share`, fake executor) beyond the plan's list because the 1-lot path is the only money-moving code in the plan.
- CLAUDE.md heading uses the plain `ibs_etf_mean_reversion` (no backticks) to match the plan's acceptance grep.

## Known Stubs

None.

## Manual-only (operator) steps still open

RTH read-only probe; `--live-1lot --confirm`; launchd load plus kill-file stop without restart; the cutover itself (deploy/IBS-RUNBOOK.md). The runbook mentions `reports/ibs/latest.html` and the 15:50 / 15:59 / 16:05 job times, which depend on Plan 09 (arm_today / EOD report) being built as specified.

## Self-Check: PASSED

Files scripts/uat_ibs_probe.py, deploy/com.bot.ibs.plist, deploy/IBS-RUNBOOK.md, tests/ibs/test_operator_tooling.py exist; commits 57ebded, 8a25124, 84ab425, 8576635 present.
