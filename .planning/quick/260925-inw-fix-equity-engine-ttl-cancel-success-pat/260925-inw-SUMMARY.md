---
id: 260925-inw
status: complete
date: 2026-09-25
phase: quick-260925-inw
plan: 01
subsystem: execution
tags: [asyncio, cr-04, order-management, moomoo, cancel-replace]

requires:
  - phase: quick-260925-ho6
    provides: "CR-04 cancel-swallow parity helpers (_reread_order, _cancel_confirmed, _escalate_unconfirmed_cancel, CancelUnconfirmedError) at all 4 cancel sites"
provides:
  - "Site 2 (_manage_entry_order TTL expiry) and site 4 (manage_exit TTL expiry) re-read order status after EVERY TTL cancel, not only a failed one"
  - "A cancel-window partial fill after a SUCCESSFUL TTL cancel is booked as a FillEvent (entry) or credited to total_filled before sizing the next SELL (exit)"
  - "A failed post-cancel re-read on the success path (raises or no matching row) escalates via _escalate_unconfirmed_cancel with err 'post-cancel re-read failed'"
affects: [bot.execution.engine, bot.service.bot, bot.position.manager]

tech-stack:
  added: []
  patterns:
    - "Success-path re-read: TTL cancel sites always call _reread_order after cancel_order, regardless of whether cancel_order raised; escalate condition is `row is None or (cancel_err is not None and not _cancel_confirmed(row, qty))`"

key-files:
  created: []
  modified:
    - bot/execution/engine.py
    - tests/execution/test_engine.py

key-decisions:
  - "err passed to _escalate_unconfirmed_cancel is `cancel_err or \"post-cancel re-read failed\"` -- a plain string works because _escalate_unconfirmed_cancel only ever uses err via str()"
  - "Success path deliberately skips the _cancel_confirmed status check (only the row-is-None failed-re-read case escalates on success) -- escalating on a transient non-terminal status would halt every normal re-price/re-escalation"
  - "Legacy test mocks that returned bare [] from get_order_status after a cancel were fixed via one shared _unfilled_status_for(gw) helper (dealt 0, CANCELLED_ALL once cancel_order was awaited for that order_id, else SUBMITTED) rather than duplicating ad hoc fixes per test"

requirements-completed: [EXEC-03, EXEC-05]

duration: 25min
completed: 2026-09-25
---

# Quick Task 260925-inw: Fix equity engine success-path TTL-cancel partial-fill race Summary

**A successful TTL cancel at CR-04 sites 2 (entry) and 4 (exit) now always re-reads order status once, so a fill that lands in the cancel window is booked instead of silently dropped under a re-placed BUY or an oversized SELL.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-25
- **Completed:** 2026-09-25
- **Tasks:** 2 completed
- **Files modified:** 2 (bot/execution/engine.py, tests/execution/test_engine.py)

## Accomplishments

- Closed the CR-04 follow-up gap from 260925-ho6: the post-cancel re-read at sites 2 and 4 previously ran ONLY when `cancel_order` raised. A partial fill landing between the last poll and a SUCCESSFUL cancel was silently dropped -- entry re-placed a full-size BUY on top of untracked shares; exit sized the next SELL for the full remaining qty, risking an over-sell into a short.
- Site 2 (`_manage_entry_order` TTL-expiry block) and site 4 (`manage_exit` TTL-expiry else-branch) now unconditionally re-read after every TTL cancel. `dealt > 0` books the fill (entry: FillEvent; exit: credited to `total_filled`/`total_notional` before the next SELL is sized). A failed re-read (raises, or no matching row) escalates through the existing `_escalate_unconfirmed_cancel` exactly like a failed `cancel_order` -- audit, Telegram alert, exit hold on SELL, and a raised `CancelUnconfirmedError` -- never trading blind.
- 4 new RED-first regression tests pin the fix (entry partial-during-success-cancel returns a fill; exit partial-during-success-cancel sizes the next SELL for the remainder, not the full qty; both re-read-failed variants escalate and hold).
- 4 pre-existing tests had unrealistic `get_order_status` mocks (returning bare `[]` for a just-cancelled order, which `order_list_query` never actually does) fixed via one shared `_unfilled_status_for(gw)` helper -- no assertion was changed or weakened.

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- 4 success-path race regression tests** - `9b44953` (test)
2. **Task 2: GREEN -- always re-read after the TTL cancel at sites 2 and 4** - `96a1497` (fix)

_Note: this is a TDD plan-level RED/GREEN pair, not a per-task TDD sub-cycle._

## Files Created/Modified

- `bot/execution/engine.py` - Site 2 (`_manage_entry_order`) and site 4 (`manage_exit`) TTL-cancel blocks de-indented so the re-read always runs; escalation condition extended to `row is None or (cancel_err is not None and not _cancel_confirmed(...))`; `err` passed as `cancel_err or "post-cancel re-read failed"`; docstrings for `_reread_order`, the module CR-04 bullet, and the `Raises:` sections of `_manage_entry_order`/`manage_exit` updated to mention the success-path re-read.
- `tests/execution/test_engine.py` - Added `_unfilled_status_for(gw)` helper; added the 4 new `test_ttl_success_cancel_*` tests; fixed 4 legacy mocks (`test_ttl_cancel_replace` scenarios 1/2/3, `test_max_entry_chase_r_abandons_at_reprice_not_initial_placement`) to use the realistic never-filled-order stub instead of bare `[]`.

## Decisions Made

- The success path intentionally does NOT run the `_cancel_confirmed` status check that the failed-cancel path uses -- only `row is None` (the re-read itself failed) triggers escalation on a successful cancel. Running `_cancel_confirmed` on the success path would escalate on any transient non-terminal status returned mid-re-price, halting normal chase/escalation behavior that works correctly today.
- `_unfilled_status_for(gw)` centralizes the legacy-mock fix as one small helper (reads `gw.cancel_order.await_args_list` to decide CANCELLED_ALL vs SUBMITTED) rather than patching each of the 4 call sites with bespoke inline logic.

## Deviations from Plan

### Auto-fixed Issues (plan-anticipated, not unplanned)

The plan explicitly named these 4 pre-existing tests as needing their mocks touched (Task 2 action block, "Legacy mocks" section) because their `get_order_status` mocks returned an unrealistic bare `[]` for an order that had just been cancelled -- `order_list_query` always returns a row for today's cancelled orders, so an empty result is anomalous. No assertion in any of these tests was changed or weakened; only the mock's `get_order_status` wiring changed.

1. **[Plan-specified] `test_ttl_cancel_replace` scenario 1** (`mock_order_status_s1`'s `return []` fallthrough)
   - **Found during:** Task 2 green run
   - **Issue:** After the fix, the fallthrough for an order not yet at "second placement" was reached during the post-cancel re-read of the first order, returning `[]` (row=None) and wrongly escalating instead of proceeding to the re-place.
   - **Fix:** Fallthrough now calls `_unfilled_status_for(gw1)(order_id)`, returning a realistic SUBMITTED/CANCELLED_ALL dealt-0 row.
   - **Files modified:** `tests/execution/test_engine.py`
   - **Verification:** `test_ttl_cancel_replace` passes; assertions unchanged.
   - **Committed in:** `96a1497` (part of Task 2 commit)

2. **[Plan-specified] `test_ttl_cancel_replace` scenario 2** (`gw2.get_order_status = AsyncMock(return_value=[])`)
   - **Found during:** Task 2 green run
   - **Issue:** Same anomalous-`[]` re-read problem on every TTL expiry across all retries.
   - **Fix:** `gw2.get_order_status = AsyncMock(side_effect=_unfilled_status_for(gw2))`.
   - **Files modified:** `tests/execution/test_engine.py`
   - **Verification:** Scenario 2 still abandons after `entry_max_retries` and calls `store2.expire_pending_intent`.
   - **Committed in:** `96a1497`

3. **[Plan-specified] `test_ttl_cancel_replace` scenario 3** (`gw3.get_order_status = AsyncMock(return_value=[])`)
   - **Found during:** Task 2 green run
   - **Issue:** Same as scenario 2, for the `entry_max_retries=1` config-swap proof.
   - **Fix:** `gw3.get_order_status = AsyncMock(side_effect=_unfilled_status_for(gw3))`.
   - **Files modified:** `tests/execution/test_engine.py`
   - **Verification:** CFG-01 config-swap assertions (`len(placed_orders_s3) <= 2`, retry-count comparison) still pass.
   - **Committed in:** `96a1497`

4. **[Plan-specified] `test_max_entry_chase_r_abandons_at_reprice_not_initial_placement`** (`gw.get_order_status = AsyncMock(return_value=[])`)
   - **Found during:** Task 2 green run
   - **Issue:** Same anomalous-`[]` re-read problem; the first TTL cancel's re-read would return `None` and escalate instead of letting the max-entry-chase guard fire at the re-price step.
   - **Fix:** `gw.get_order_status = AsyncMock(side_effect=_unfilled_status_for(gw))`.
   - **Files modified:** `tests/execution/test_engine.py`
   - **Verification:** Test still asserts exactly one placement before the over-cap re-price abandons.
   - **Committed in:** `96a1497`

---

**Total deviations:** 0 unplanned (Rules 1-4 not invoked) / 4 plan-specified legacy-mock fixes, all in the test file only.
**Impact on plan:** None on scope -- these were explicitly called out in the plan's Task 2 action block as expected fallout of making the mocks realistic, and only the mock wiring changed, never an assertion.

## Issues Encountered

None. The RED run behaved exactly as the plan predicted for all 4 new tests (see Red-Run Evidence below), and the GREEN run needed exactly the 4 plan-anticipated legacy-mock fixes with no further failures.

## Red-Run Evidence (Task 1, before any bot/ edit)

Command: `python3 -m pytest -q tests/execution/test_engine.py -k ttl_success_cancel`

Result: `4 failed, 25 deselected` -- no ImportError in any failure.

| Test | Failure mode |
|---|---|
| `test_ttl_success_cancel_entry_partial_returns_fill` | `assert None is not None` -- unmodified engine returns `None` (abandoned after 3 placements) instead of a `FillEvent` for the 30-share cancel-window fill |
| `test_ttl_success_cancel_exit_partial_sizes_next_sell` | `assert 3 == 2` -- unmodified engine places 3 SELL orders (drops the 40-share fill, sizes ORD-2 at the full 100, needs a third round) instead of exactly 2, with the second sized at 60 |
| `test_ttl_success_cancel_entry_reread_failed_escalates` | `Failed: DID NOT RAISE <class 'Exception'>` -- unmodified engine never re-reads on a successful cancel, so it proceeds to re-place rather than escalating on the failed re-read |
| `test_ttl_success_cancel_exit_reread_failed_escalates_and_holds` | `Failed: DID NOT RAISE <class 'Exception'>` -- unmodified engine proceeds to a second SELL (which happens to fill) rather than escalating and holding the code |

Pre-existing test suite (before any bot/ edit): `python3 -m pytest -q tests/execution/test_engine.py -k "not ttl_success_cancel"` → `24 passed, 1 skipped, 4 deselected`.

## Green-Run Evidence (Task 2)

- `python3 -m pytest -q tests/execution/test_engine.py` → `28 passed, 1 skipped`
- `python3 -c "import bot.main"` → clean
- `python3 -m pytest -q` (full suite) → `1151 passed, 1 skipped` (baseline 1147 + 4 new tests, 0 failed)
- `git diff --name-only HEAD~1 -- bot/` → only `bot/execution/engine.py` under `bot/`
- `grep -c "post-cancel re-read failed" bot/execution/engine.py` → `2` (site 2 and site 4)

## Known Ceiling

The success path deliberately trusts a successful `cancel_order` call and does not re-check `_cancel_confirmed`'s terminal-status logic against the re-read row -- only a fully failed re-read (`row is None`) escalates. If `order_list_query` ever returns a stale non-terminal row for an order the broker has actually cancelled (rather than raising or omitting it), that row's `dealt_qty` is trusted as final and no escalation occurs. This mirrors the same trust boundary the ho6 failed-cancel path already accepts for a confirmed cancel; it is not a new gap introduced by this fix. `ponytail: one order_list_query call per TTL expiry, budget documented inline at site 2 -- revisit if the shared 10-call/30s cap with the options bot ever gets tight.`

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

CR-04 parity is now complete across all 4 cancel-swallow sites for both the failed-cancel path (260925-ho6) and the success-path cancel-window race (this task). No further CR-04 follow-ups are known. Callers (`bot/service/bot.py`, `bot/position/manager.py`) already handle `CancelUnconfirmedError` from the ho6 work and needed no changes here.

---
*Quick task: 260925-inw*
*Completed: 2026-09-25*

## Self-Check: PASSED

- FOUND: bot/execution/engine.py
- FOUND: tests/execution/test_engine.py
- FOUND: .planning/quick/260925-inw-fix-equity-engine-ttl-cancel-success-pat/260925-inw-SUMMARY.md
- FOUND: 9b44953 (test commit)
- FOUND: 96a1497 (fix commit)
