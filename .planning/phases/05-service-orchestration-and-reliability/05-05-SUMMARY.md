---
phase: 05-service-orchestration-and-reliability
plan: "05"
subsystem: position-manager-alerter
tags: [ALERT-02, ALERT-04, gap-closure, TDD, FSM, exit-reason, pending_exit_reason]
dependency_graph:
  requires: [05-01, 05-03]
  provides: [ALERT-02, ALERT-04]
  affects: [bot/position/state.py, bot/position/manager.py, bot/service/alerter.py]
tech_stack:
  added: []
  patterns: [TDD-RED-GREEN, FSM-annotation, fire-and-forget-ALERT-04, pending_exit_reason]
key_files:
  created: []
  modified:
    - bot/position/state.py
    - bot/position/manager.py
    - bot/service/alerter.py
    - tests/position/test_manager.py
    - tests/service/test_alerter.py
decisions:
  - "pending_exit_reason is in-memory only — DB upsert column list is fixed, no migration needed"
  - "prev_phase captured before evaluate_close in on_bar to derive BREAKEVEN/TRAILING/else reason"
  - "Partial alert fires at _trigger_partial_profit (not in _on_exit_fill) since remaining_quantity stays > 0"
  - "R-multiple for partial alert uses entry_price as exit proxy at trigger time (approximate, acceptable)"
  - "trail_stop added to _EXIT_REASON_LABELS alongside existing trail/trail_up keys (back-compat preserved)"
metrics:
  duration: "~18 minutes"
  completed: "2026-06-24"
  tasks: 3
  files: 5
---

# Phase 05 Plan 05: ALERT-02 Exit-Alert Reason Threading Summary

Closed the ALERT-02 UAT gap: all five exit reasons (partial, breakeven, trail-stop, stop-out, force-close) now reach `on_exit_alert` with their true reason string; partial scale-outs fire an exit alert; and `_EXIT_REASON_LABELS` reconciles the manager vocabulary.

## One-Liner

ALERT-02 gap closed: `pending_exit_reason` annotation on PositionState threads the true exit cause from FSM trigger to TelegramAlerter, partial scale-outs now fire exit alerts, and `trail_stop` label added to alerter vocabulary.

## What Was Built

### Task 1 — pending_exit_reason field on PositionState (TDD)

**PositionState** (`bot/position/state.py`): Added `pending_exit_reason: Optional[str] = None` as the last optional field. In-memory only — the DB upsert column list in `StateStore.upsert_position` is fixed, so no migration is needed and the field never reaches the DB.

**PositionManager** (`bot/position/manager.py`):
- `on_bar`: Captures `prev_phase = pos.phase` immediately before `evaluate_close()` mutates the FSM phase.
- `_trigger_stop_out(pos, qty, time_key, prev_phase)`: New `prev_phase` parameter; derives the reason: `BREAKEVEN → "breakeven"`, `TRAILING → "trail_stop"`, anything else (`ACTIVE`/`PARTIAL_TAKEN`) → `"stop_out"`.
- `_trigger_partial_profit`: Records `pos.pending_exit_reason = "partial"` before persist.
- `force_close_all`: Records `pos.pending_exit_reason = "force_close"` in the fully-flat branch before persist.

**Tests added** (5 new, all green):
- `test_stop_out_records_stop_out_reason` — ACTIVE + stop → "stop_out"
- `test_trailing_stop_records_trail_stop_reason` — TRAILING + stop → "trail_stop"
- `test_breakeven_stop_records_breakeven_reason` — BREAKEVEN + stop → "breakeven"
- `test_partial_records_partial_reason` — 0.75R partial → "partial"
- `test_force_close_records_force_close_reason` — force_close_all fully fills → "force_close"

### Task 2 — Real reason in alert + partial alert + trail_stop label (TDD)

**TelegramAlerter** (`bot/service/alerter.py`): Added `"trail_stop": "Trail Stop"` to `_EXIT_REASON_LABELS` — all five manager-emitted reason keys now have human labels; `format_exit_alert` no longer falls back to the raw string for trailing-stop exits.

**PositionManager** (`bot/position/manager.py`):
- `_on_exit_fill`: Changed alert reason from hardcoded `"exit_fill"` to `pos.pending_exit_reason or "exit_fill"`. The `or "exit_fill"` is the defensive fallback for in-flight exits arriving after a restart (no reason was pre-recorded). All other behavior (gate, R-multiple math, try/except) unchanged.
- `_trigger_partial_profit`: After the exit order is placed, fires `self._on_exit_alert(pos.code, "partial", round(r_multiple, 2))` wrapped in `try/except Exception → _logger.warning("on_exit_alert_error", ...)` — mirrors the ALERT-04 isolation pattern from `_on_exit_fill`. R-multiple uses `avg_fill_price or entry_price` as the exit proxy (approximate, acceptable for partial semantics).

**Tests added** (7 new in test_manager.py, 2 in test_alerter.py):
- `test_full_exit_fill_alerts_real_reason` — pending_exit_reason="stop_out" → "stop_out" reaches alert
- `test_full_exit_fill_falls_back_to_exit_fill_when_no_reason` — None → "exit_fill" fallback
- `test_partial_scaleout_fires_exit_alert` — on_bar partial trigger → 1 alert with reason "partial", remaining_quantity > 0
- `test_partial_does_not_double_alert_on_later_full_close` — partial fires 1; full-close fires 1; total 2, no double
- `test_exit_alert_callback_exception_is_swallowed_full_close` — raising callback on full-close: position still closed (ALERT-04)
- `test_exit_alert_callback_exception_is_swallowed_on_partial` — raising callback on partial: partial still executed (ALERT-04)
- `test_exit_alert_for_all_reasons` — extended to include `trail_stop`; all 7 reasons produce non-raw bold headers
- `test_trail_stop_label_not_raw_string` — `format_exit_alert("US.AAPL", "trail_stop", 1.5)` renders "Trail Stop", not "trail_stop"

### Task 3 — Full-suite regression gate

`python3 -m pytest -q` result: **405 passed, 1 skipped, 0 failed** (up from 393 + 1 skipped baseline).

Grep verification:
1. `grep -n "pending_exit_reason or" bot/position/manager.py` → line 537 (real reason pass-through confirmed)
2. `grep -n '"partial"' bot/position/manager.py` → lines 562, 594 (partial alert fires confirmed)
3. `grep -v '^#' bot/service/alerter.py | grep -c "trail_stop"` → 1 (label present)

`git diff HEAD~4 -- bot/position/state.py` shows only the `pending_exit_reason` field addition — `evaluate_close` is byte-for-byte unchanged (no strategy-logic change).

`bot/main.py` line 94: `on_exit_alert=lambda code, reason, r: asyncio.create_task(alerter.send_exit_alert(code, reason, r))` — unchanged 3-arg signature.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| f91d148 | test | Failing tests for pending_exit_reason FSM field recording (RED) |
| 87d3ecc | feat | Record real exit reason on PositionState at FSM trigger points |
| 73525d2 | test | Failing tests for real-reason alert + partial alert + trail_stop label (RED) |
| 6c7a37c | feat | Thread real exit reason to on_exit_alert + partial alert + trail_stop label |

## Deviations from Plan

None — plan executed exactly as written.

The only planned-but-noted behavior change: `_trigger_partial_profit` now fires `on_exit_alert` with `"partial"` reason. This is the ALERT-02 gap-closure intent, not a deviation. No prior test asserted the old (silent) behavior for partial scale-outs, so no test updates were needed to relax prior expectations.

## Known Stubs

None. All five exit reasons are now wired to real labels and the alert dispatch is fully plumbed.

## Threat Flags

No new network endpoints, auth paths, or schema changes introduced. All file modifications are internal to the FSM annotation and alert vocabulary layers (see threat_model in PLAN.md for T-05-05-01 through T-05-05-04, all mitigated).

## Self-Check: PASSED

Files verified:
- `bot/position/state.py` — pending_exit_reason field present: FOUND
- `bot/position/manager.py` — real reason plumbing present: FOUND  
- `bot/service/alerter.py` — trail_stop label present: FOUND
- Commits f91d148, 87d3ecc, 73525d2, 6c7a37c: all in git log
- Full suite: 405 passed, 1 skipped, 0 failed
