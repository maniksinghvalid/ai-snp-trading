---
phase: 06-backtester
plan: 08
subsystem: backtester-execution
tags: [backtester, execution, force-close, exit-fills]
dependency_graph:
  requires: [06-07]
  provides: [SimulatedExecution force-close fill mode, WR-01 no-fabrication exit contract]
  affects: [backtester/harness.py (Plan 09 will toggle _force_close), bot/position/manager.py force_close_all (consumed unchanged)]
tech_stack:
  added: []
  patterns:
    - "manage_exit branches on a harness-set self._force_close mode flag, not a new parameter (public signature parity with ExecutionEngine.manage_exit preserved)"
    - "on_bar records the full bar dict (not just time_key) so force-close fills have a real price anchor"
key_files:
  created: []
  modified:
    - backtester/execution.py
    - tests/backtester/test_execution.py
decisions:
  - "Both tasks (force-close fill mode + WR-01 no-fabrication fix) landed in a single GREEN commit — they modify the same manage_exit method body as sequential branches (force-close branch first, then the normal-mode next_bar==None fix); splitting them into two commits would require reconstructing an incoherent intermediate state of the same function. The RED test commit already covers both tasks together for the same reason."
metrics:
  duration_minutes: 12
  completed: 2026-07-07
---

# Phase 6 Plan 08: Force-close fill mechanic + WR-01 no-fabrication exit Summary

SimulatedExecution now records the full latest bar per code (not just its time_key) and gains a
harness-toggled `_force_close` mode that fills the full quantity at the last observed bar close;
the normal-mode `manage_exit` no longer fabricates a phantom `exit_price=None` fill when there is
no next bar — it returns 0 and leaves the position open, matching the live "return 0, stay open"
contract.

## What Was Built

- `SimulatedExecution.__init__`: added `self._force_close = False` (mode flag, harness-toggled)
  and `self._last_bar: Dict[str, dict] = {}` (full bar dict per code, alongside the existing
  `_last_bar_time_key`).
- `on_bar(bar)`: now also stores `self._last_bar[bar["code"]] = bar` so force-close mode has a
  real close price to fill against.
- `manage_exit(code, qty, side, escalation_step, escalation_cadence, ttl)` (signature unchanged):
  - **Force-close branch** (`self._force_close is True`): looks up `self._last_bar[code]`; if
    present, fills the full `qty` at `last["close"] + self._slippage`, appends the fill to both
    `exit_fills` and `fills`, and returns `int(qty)` — never consults `feed.next_bar`. If no bar
    has been recorded for that code yet (defensive path), returns `0` with no fill appended.
  - **Normal branch** (`self._force_close is False`): unchanged N+1-open lookup via
    `feed.next_bar(code, after=...)`. When `next_bar is None`, now returns `0` immediately with
    **no** entry appended to `exit_fills`/`fills` (previously appended a fake full fill with
    `exit_price=None` and returned `int(qty)`). When a next bar exists, behavior is unchanged:
    fills at `next_bar["open"] + slippage`, returns `int(qty)`.

## Tests Added (tests/backtester/test_execution.py)

- `test_manage_exit_force_close_fills_at_last_bar_close_and_returns_full_qty` — sets
  `_force_close = True`, calls `on_bar` then `manage_exit`, asserts full-qty return and
  `exit_price == last bar close`.
- `test_manage_exit_force_close_with_no_recorded_bar_returns_zero` — force-close mode with a code
  never seen via `on_bar` returns 0 and records no fill (defensive path).
- `test_manage_exit_returns_zero_and_records_no_fill_when_no_next_bar` — WR-01: normal mode with
  `_FakeFeed.next_bar` returning `None` (signal on the dataset's last bar) returns 0, appends
  nothing to `exit_fills` or `fills`.

## Verification

- `python3 -m pytest tests/backtester/ -q` — 38 passed.
- `python3 -m pytest tests/ -q` (full suite) — 627 passed, 1 skipped (pre-existing skip, unrelated).
- `grep -n "_force_close" backtester/execution.py` — flag set in `__init__`, read at the top of
  `manage_exit`.
- `grep -n "_last_bar\b" backtester/execution.py` — `on_bar` stores the full bar dict; force-close
  branch reads it.
- No `exit_price=None`/`"exit_price": None` construction remains anywhere in `manage_exit`.
- `manage_exit`'s parameter list is unchanged: `code, qty, side, escalation_step,
  escalation_cadence, ttl`.
- No broker/gateway import in `backtester/execution.py` (`grep -n "gateway\|moomoo"` matches only
  pre-existing docstring prose about `SimulatedGateway`/`gateway=None`, no import statement).

## Deviations from Plan

None — plan executed exactly as written. Both tasks were committed together at the GREEN step
(see `decisions` above for why) rather than as two separate `feat` commits; the RED test commit
likewise covers both tasks' test cases in one commit for the same reason (both tasks edit the
same `manage_exit` method body as adjacent branches).

## Known Stubs

None. No new UI/data-rendering surface introduced.

## Threat Flags

None. This plan's threat register (T-06-08-01, T-06-08-02, T-06-08-03, T-06-08-SC) covers exactly
the surface touched; no new network endpoint, auth path, file access pattern, or schema change was
introduced. `T-06-08-01` (fabricated fill) and `T-06-08-02` (missing/incorrect exit record) are the
mitigations this plan implements; `T-06-08-03` (no gateway/broker import) and `T-06-08-SC` (no new
package installs) are confirmed clean by the grep checks above.

## Self-Check: PASSED

- FOUND: backtester/execution.py (modified, `_force_close`/`_last_bar` present)
- FOUND: tests/backtester/test_execution.py (modified, 3 new test functions present)
- FOUND: 6344340 (test commit)
- FOUND: 5a5d45d (feat commit)
