---
phase: quick-260925-goi
plan: 01
subsystem: options-bot
tags: [asyncio, exception-chaining, options-execution, structlog]
status: complete

requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    provides: LegExecutor.fill_leg's CR-03 guarantee (cancel-before-return/raise on every exit path)
provides:
  - "fill_leg raises RuntimeError when a TTL-path cancel is unconfirmed (CR-04), instead of escalating or returning a still-growing quantity"
  - "_try_open routes any open_position exception into the existing CR-02 NEEDS_ATTENTION branch (EX-03)"
affects: [options-bot-execution, options-bot-service, phase-11-review]

tech-stack:
  added: []
  patterns:
    - "Chain a raised exception from a same-message COPY of the caught error object, not the live object, whenever a caller further downstream (here: the pre-existing 11-09 shielded retry) might raise that exact instance again while ours is still active — otherwise Python's implicit __context__ chaining links the two into a cycle that hangs rich/structlog traceback rendering on exc_info=True"

key-files:
  created: []
  modified:
    - bot/options/execution.py
    - bot/options/service.py
    - tests/options/test_execution.py
    - tests/options/test_service.py

key-decisions:
  - "cancel unconfirmed = cancel_order raised AND the immediate re-read shows dealt_qty < qty; order_status is never consulted (ceiling: a false NEEDS_ATTENTION alarm when the broker already ended the order on its own; upgrade path is having _poll return order_status too)"
  - "open_position raises (not returns False) when an OPENING leg's fill_leg call fails, because open_position is byte-unchanged and that call sits outside the unwind's try; the service-level False/NEEDS_ATTENTION comes entirely from EX-03's new try/except in _try_open"
  - "Chained the new RuntimeError from Exception(str(cancel_error)) rather than the live cancel_error object — found via a hung test (Rule 1 bug, see Deviations)"

requirements-completed: [MSO-08, CR-04, EX-03]

duration: ~45min
completed: 2026-09-25
---

# Quick Task 260925-goi: CR-04 fill_leg TTL-cancel-swallow Summary

**fill_leg now raises instead of silently escalating when a TTL-path cancel_order fails and the order isn't fully filled, and `_try_open` routes that raise (or any open_position exception) into the existing NEEDS_ATTENTION branch instead of leaving the row silently OPENING.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 2/2 completed
- **Files modified:** 4 (bot/options/execution.py, bot/options/service.py, tests/options/test_execution.py, tests/options/test_service.py)

## Accomplishments

- CR-04 closed: no path in `fill_leg` places a next attempt, or returns a quantity, while a TTL-path cancel is unconfirmed. The reviewer's repro now raises instead of returning a clean-unwind `None`.
- EX-03 closed: an opening-leg exception out of `open_position` now ends NEEDS_ATTENTION with the UNWIND INCOMPLETE alert, never left OPENING with no alert, never ABORTED.
- A fill that lands during a failed cancel still returns the normal fill tuple (preservation pin + mutation-tested).
- Found and fixed, mid-task, a real exception-chaining cycle bug that hung the test process (see Deviations).

## Task Commits

1. **Task 1: CR-04 — fill_leg raises instead of escalating/returning when the TTL cancel is unconfirmed** - `2cb1d0f` (fix, tdd)
2. **Task 2: EX-03 — _try_open treats an open_position exception as an incomplete unwind** - `7689f2b` (fix, tdd)

Both commits verified to contain only their own task's source and test file (`git show --stat --format= HEAD` for each).

## Red/Green/Mutation Evidence

**Red evidence (Task 1):** `python3 -m pytest -q tests/options/test_execution.py -k ttl_cancel_failure` on the unmodified a580871 `execution.py` → `4 failed, 1 passed in 0.93s` (B, the preservation pin, passes at HEAD by design; A×2, C and D fail as expected).

**Mutation evidence (Task 1):** with the ` and dealt_qty < int(qty)` clause temporarily removed, `python3 -m pytest -q tests/options/test_execution.py -k after_full_fill` → `1 failed, 42 deselected` (the preservation pin B fails once the clause is gone, confirming it has teeth). Clause restored afterward; `grep -n "dealt_qty < int(qty)"` confirms it is back.

**Red evidence (Task 2):** `python3 -m pytest -q tests/options/test_service.py -k open_position_error_flags_needs_attention` on the unmodified service.py → `1 failed, 122 deselected` (`assert 0 == 1` — the row never reached NEEDS_ATTENTION).

**Green, per commit:**
- After Task 1: `python3 -m pytest -q tests/options/test_execution.py -k ttl_cancel_failure` → `5 passed`. `python3 -m pytest -q tests/options tests/backtester/options` → `473 passed`.
- After Task 2: `python3 -m pytest -q tests/options/test_service.py -k "open_position_error_flags_needs_attention or incomplete_unwind or clean_unwind_keeps_aborted"` → `5 passed`. `python3 -m pytest -q tests/options tests/backtester/options` → `474 passed`.

**Phase gate:** `python3 -m pytest -q` → `1350 passed, 1 skipped` (baseline was 1345 passed, 1 skipped at a580871; net +5 matches the plan's accounting: +1 for the parametrized replacement (2 tests for the 1 deleted), +1 for the preservation pin B, +1 for C, +1 for D, +1 for the Task 2 test).

## Design Choice: "cancel unconfirmed"

Unconfirmed means `cancel_order` raised **and** the immediate re-read shows `dealt_qty < qty`. The re-read row's `order_status` is never consulted:

1. `_poll` must stay byte-unchanged and returns only `(dealt_qty, avg_price)` — reading status would need a second `order_list_query` per TTL expiry, spending the 10-per-30s rate budget shared with the equity bot.
2. The one common benign cause — a fill during the cancel — is already confirmed by `dealt_qty >= qty`, which still returns the normal fill tuple (preservation pin + mutation check, above).
3. The only remaining false alarm is an order the broker ended on its own during the TTL window. This fails closed: NEEDS_ATTENTION plus an alert, for the operator to check and resolve (T-11-64, accepted). A misread status string would instead fail open, turning a live order into "confirmed."

**Ceiling:** a false NEEDS_ATTENTION alarm when the broker already ended the order on its own inside the TTL window. **Upgrade path:** have `_poll` return `order_status` too, so the false-alarm case can be distinguished without a second broker round-trip.

## open_position raises, not returns False, for an opening-leg failure

`open_position` (byte-unchanged, per the AST check below) calls `fill_leg` for the opening legs at line 255, **outside** any try — only the unwind's own `close_legs` call is wrapped in `except Exception`. So a CR-04 raise on an OPENING leg's TTL cancel propagates straight out of `open_position` as an exception, not a `False` return. The service-level `False`/NEEDS_ATTENTION outcome comes entirely from Task 2's new `try/except` in `_try_open` (EX-03) — the two tasks compose exactly the way the plan's key_links describe.

## Files Created/Modified

- `bot/options/execution.py` — `fill_leg`'s TTL block: a failed cancel now raises `RuntimeError` (chained from a same-message copy of the cancel error) when the order is not fully filled, instead of swallowing and escalating. Docstrings updated (module + fill_leg Guarantee/Raises). `_poll`, `open_position`, `close_legs` byte-unchanged (AST-verified).
- `bot/options/service.py` — `_try_open`'s `open_position` call wrapped in `try`: `asyncio.CancelledError` re-raised; any other `Exception` logs `options_entry_open_error` and sets `filled = False`, reusing the existing CR-02 NEEDS_ATTENTION branch unchanged. Docstring's Returns sentence updated.
- `tests/options/test_execution.py` — replaced `test_cancel_failure_does_not_break_the_loop` (pinned the unsafe swallow) with 4 new tests: the parametrized CR-04 case, the preservation pin, and the reviewer's `open_position`-level repro for both the opening leg and the unwind's own closing order.
- `tests/options/test_service.py` — added `test_entry_scan_open_position_error_flags_needs_attention` (EX-03).

## Decisions Made

- "cancel unconfirmed" design choice and its ceiling — see above (this was Claude's discretion call per the plan's objective).
- `open_position` raises rather than returning `False` for an opening-leg failure — see above; this is a consequence of `open_position` being byte-unchanged, not a new design choice.
- Chained the new `RuntimeError` from `Exception(str(cancel_error))` instead of the live `cancel_error` object — see Deviations below.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Exception cause/context cycle hung `exc_info=True` logging**

- **Found during:** Task 1, while running the GREEN check for the newly-written `test_ttl_cancel_failure_raises_and_places_no_next_attempt` test (per the plan's own prescribed test setup, which uses a single shared `RuntimeError("modify_order failed")` instance as `gw.cancel_order`'s `side_effect` for every call).
- **Issue:** My first implementation did `raise RuntimeError(...) from cancel_error` using the live caught exception object. When the TTL cancel failed, `R1.__cause__` was set to that object (call it `E`). The pre-existing (untouched) 11-09 shielded-retry path then re-raises `E` again while `R1` is the active/handled exception — and because the test's mock reuses the exact same `E` instance for every call, Python's implicit exception chaining set `E.__context__ = R1`, creating a genuine cycle (`R1.cause → E`, `E.context → R1`). `rich`'s traceback renderer (invoked by structlog's dev renderer on `_logger.error(..., exc_info=True)`) has no cycle guard for this and hung indefinitely (confirmed via a `faulthandler`/`SIGALRM` stack dump showing it stuck inside `rich/pretty.py`'s `_traverse`, mid–garbage-collection, walking the cyclic chain).
- **Fix:** Chain from a same-message copy instead of the live object: `raise RuntimeError(...) from Exception(str(cancel_error))`. The copy is never itself raised again, so even if the gateway's exact exception instance is reused across calls (unusual, but not impossible), no cycle can form. `str(excinfo.value.__cause__)` still equals the original message, satisfying every test assertion; only object identity of the cause changed, which nothing else in the codebase inspects.
- **Files modified:** `bot/options/execution.py` (the one `raise` line inside the TTL block).
- **Verification:** Re-ran the previously-hanging test standalone with a `faulthandler` alarm to confirm it no longer hangs, then re-ran `-k ttl_cancel_failure` (5 passed in ~1s) and the full options/backtester suite.
- **Committed in:** `2cb1d0f` (part of the Task 1 commit — the hang was found and fixed before that commit was made, so there is no separate commit for it).

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug found via a hanging test, not present in any pre-existing code path).
**Impact on plan:** Necessary for correctness of the CR-04 fix itself; the plan's own prescribed test setup (a shared exception instance) would otherwise deadlock the test suite. No scope creep — the fix is a single line inside the TTL block already being edited.

## Issues Encountered

- The initial GREEN run of `-k ttl_cancel_failure` hung indefinitely (backgrounded and diagnosed with `faulthandler.register(SIGALRM)` — see Deviations above for the root cause and fix).

## T-11-62..T-11-67 (this plan's threat register)

| Threat ID | Disposition | Status |
|-----------|-------------|--------|
| T-11-62 | mitigate | Closed — `fill_leg` raises on TTL-cancel-unconfirmed; shielded retry + `leg_cancel_on_error_failed` audit still run. Tests A, C, D (Task 1) |
| T-11-63 | mitigate | Closed — re-dispositions T-11-58's EX-03 half from accept to mitigate. `_try_open`'s `except Exception` reaches the CR-02 NEEDS_ATTENTION branch. Test (Task 2) |
| T-11-64 | accept | Unchanged — false NEEDS_ATTENTION alarm when the broker ended the order on its own; fails closed by design. Preservation test B + mutation check guarantee the one benign case (a fill during the cancel) is not a false alarm |
| T-11-65 | accept | Unchanged — both cancels failing is logged/audited as `leg_cancel_on_error_failed`; row ends NEEDS_ATTENTION with the alert |
| T-11-66 | accept | Unchanged — EX-03 leaves already-filled opening legs open (no automatic unwind); defined-risk since legs open longs-first and CR-04 caps at one outstanding order per leg |
| T-11-67 | mitigate | Unchanged — no new alert text; the reused CR-02 alert already passes every value through `_esc` |

## Reviewer Repro: Before/After

Ran `PYTHONPATH=. python3 <scratchpad>/repro_ttl_cancel_swallow.py` after the fix (both commits applied):

**After (this plan):**
```
RuntimeError: cancel of O2 unconfirmed: US.SPY260320P600000 SELL dealt 0/1
```
`open_position` now raises directly out of the script's `await LegExecutor(b, cfg).open_position(...)` call — it never reaches the script's own `print`/`assert` lines, and never returns a clean `None` with `O2` still live and untracked. This is the exact defect the plan describes: at a580871 (baseline), the same repro would print `open_position -> None (None == clean unwind -> row ABORTED)` with `O2` present in `still-working broker orders`, then pass its own (bug-pinning) `assert res is None and "O2" in live`. Post-fix, that assert is never reached because the call raises first — confirming CR-04 is closed.

## Next Phase Readiness

- CR-04 and EX-03 are closed per the operator's 11-UAT.md item 5 decision. The operator asked to stop the phase-11 review/fix loop after this task; no further findings from 11-REVIEW.md remain open in this plan's scope.
- Deferred/out-of-scope items (unchanged, not touched by this task): IN-12, IN-13, WR-02, WR-03, WR-04, WR-08, WR-09 (now higher priority per T-11-64's added NEEDS_ATTENTION traffic), IN-01…IN-07/IN-09/IN-10/IN-11, the equity-bot sibling of CR-04 in `bot/execution/engine.py` (recommended as a separate quick task), the EX-03 residual for exceptions in `_try_open` outside the `open_position` call, and the carried residuals (`_shutdown` ordering, readiness-gate warning, manage-cycle counting).

---
*Quick task: 260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow*
*Completed: 2026-09-25*

## Self-Check: PASSED

All modified files (`bot/options/execution.py`, `bot/options/service.py`, `tests/options/test_execution.py`, `tests/options/test_service.py`) and this SUMMARY.md exist on disk. Both task commits (`2cb1d0f`, `7689f2b`) exist in git log.
