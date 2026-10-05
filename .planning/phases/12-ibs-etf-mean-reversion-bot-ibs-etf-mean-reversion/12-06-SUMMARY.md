---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
plan: 06
subsystem: service-core
tags: [ibs, reconcile, readiness-gate, decision-job, exits, tdd]
requires: ["12-03", "12-04", "12-05"]
provides:
  - "bot.ibs.service.IbsBot: ctor (watchdog duck-type contract), _readiness_gate, _broker_shares, reconcile, _deadline, _job_decide, _decide, _run_exits, _work"
  - "tests/ibs/conftest.py service fixtures (lazy IbsBot import) and tests/ibs/test_service.py (32 tests)"
affects: [12-08 entry batch / hard-cancel sweep, 12-09 arm_today / EOD / shutdown / run / main]
tech-stack:
  added: []
  patterns: ["OptionsBot shape, one row = one code", "meta key written before any broker call (idempotent decide)", "status flip as alert-once guard"]
key-files:
  created:
    - bot/ibs/service.py
    - tests/ibs/test_service.py
  modified:
    - tests/ibs/conftest.py
key-decisions:
  - "reconcile returns the broker {code: shares} map so Plan 08 can reuse it for the external-holdings exclusion"
  - "_decide returns (quotes, exited) so Plan 08 appends the entry batch without rewriting it"
  - "Exit alert shows the stored first exit_reason (not 'retry') and escapes the ampersand in P&L for HTML parse mode"
  - "Exit loop marks exit_pending first, then breaks on a triggered kill switch, so the decided exit survives to the next session"
requirements-completed: [IBS-04, IBS-05, IBS-07]
duration: ~20 min
completed: 2026-10-04
---

# Phase 12 Plan 06: IbsBot core Summary

IbsBot readiness gate (connect -> reconcile(startup) -> kill_switch.install -> entries enabled), own-rows-only reconcile with external-holdings logging, and the idempotent daily decision job through the sequential exit batch with persisted exit_pending retry.

## Tasks

| Task | Commits |
|------|---------|
| 1. Construction, gate, reconcile (TDD) | `5dd7db3` (RED), `9769c69` (GREEN) |
| 2. Decision guards, one snapshot, exits (TDD) | `a4cce66` (RED), `2424eaf` (GREEN) |

## Behaviour delivered

- Gate: a connect failure (paper guard) propagates, install is not called, entries stay disabled.
- Reconcile: startup OPENING/CLOSING and OPEN qty mismatches or missing holdings become NEEDS_ATTENTION with one alert and one audit event each. It never calls an order method. External holdings (universe codes with no active row) are logged as `ibs_reconcile_external_ignored`. A failed positions query raises and changes no row.
- `_job_decide`: skips on a non-trading day, disabled entries, kill switch, already-decided meta key, or now >= close - hard_cancel. Otherwise it writes `ibs_decision_date` first, then runs `_decide`. Errors produce one alert with no exception text.
- `_decide`: reconcile, then ONE batched `get_market_snapshot(universe)`, fail-closed `parse_snapshot`, then `_run_exits`.
- `_run_exits`: only OPEN rows; sequential; no quote or not enough time (< worst_case_order_s) defers with exit_pending persisted; a full fill closes the row and writes a trade; a partial fill keeps the row OPEN with the remaining qty; unfilled leaves it OPEN and pending; an exception sets NEEDS_ATTENTION and audits `ibs_exit_unknown`.
- `_work`: records every placed order id as WORKING via `on_placed`; after a normal return it closes the working orders and audits `ibs_order_done`. Exceptions propagate and leave the orders WORKING.

## Deviations from Plan

None - plan executed as written.

## Verification

- `python3 -m pytest tests/ibs/test_service.py -q`: 32 passed.
- `python3 -m pytest -q`: 1605 passed, 1 skipped (baseline 1573 + 32 new, zero regressions).
- Acceptance greps: one `get_market_snapshot(` call, no `force_close`, no `OrderType.MARKET`, no `unlock_trade`, no exception text in alerts.

## Known Stubs

None. Entry batch, hard-cancel sweep, arm_today, EOD, shutdown, run and main are deliberately left to Plans 08/09.

## Threat Flags

None.

## Self-Check: PASSED
