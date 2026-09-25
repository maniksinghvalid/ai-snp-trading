---
phase: quick-260925-inw
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/execution/test_engine.py
  - bot/execution/engine.py
autonomous: true
requirements: [EXEC-03, EXEC-05]

must_haves:
  truths:
    - "Entry side: when a partial fill lands between the last poll and a SUCCESSFUL TTL cancel (the broker shows CANCELLED_PART with dealt_qty > 0), _manage_entry_order returns a FillEvent for the dealt shares. No second BUY is placed and the intent is not expired."
    - "Exit side: when a partial fill lands between the last poll and a SUCCESSFUL TTL cancel, manage_exit adds it to total_filled. The next SELL is sized as qty minus the shares already counted, never the full remaining qty, so the exit can never over-sell into a short."
    - "If the post-cancel re-read fails after a SUCCESSFUL TTL cancel (get_order_status raises OR returns no matching row), the engine escalates through _escalate_unconfirmed_cancel with err 'post-cancel re-read failed'. That means a cancel_unconfirmed audit, the Telegram alert, the exit hold on SELL, and a CancelUnconfirmedError raise. Entry never re-places or abandons blind, and exit never sizes a SELL blind."
    - "When a SUCCESSFUL TTL cancel is re-read and the row shows dealt_qty 0, behaviour is exactly as before: entry re-prices or abandons, exit escalates the price. The success path never applies the _cancel_confirmed status check."
    - "The failed-cancel CR-04 behaviour from 260925-ho6 is unchanged, and the full test suite is green (1151 passed, 1 skipped)."
  artifacts:
    - path: "bot/execution/engine.py"
      provides: "Sites 2 and 4 always re-read once after the TTL cancel, and escalate on a failed re-read"
      contains: "post-cancel re-read failed"
    - path: "tests/execution/test_engine.py"
      provides: "4 success-path race regression tests"
      contains: "def test_ttl_success_cancel_"
  key_links:
    - from: "bot/execution/engine.py _manage_entry_order (site 2)"
      to: "self._reread_order / self._emit_entry_fill / self._escalate_unconfirmed_cancel"
      via: "unconditional re-read after the TTL cancel_order"
      pattern: "cancel_err or \"post-cancel re-read failed\""
    - from: "bot/execution/engine.py manage_exit (site 4)"
      to: "total_filled / remaining"
      via: "dealt qty from the re-read is added before the next SELL is sized"
      pattern: "total_filled \\+= dealt"
---

<objective>
Fix the success-path partial-fill race at CR-04 site 2 (entry TTL cancel) and site 4 (exit TTL cancel) in `bot/execution/engine.py`. This is follow-up 1 from quick task 260925-ho6.

Today the post-cancel `_reread_order` runs ONLY when `cancel_order` raised. If the cancel SUCCEEDS but a partial fill landed after the last poll, that fill is silently dropped:
- Entry: the untracked shares stay open and the engine re-places a full-size BUY, which leaves an oversized, untracked long.
- Exit: the next SELL is sized for the full remaining qty, which over-sells into an untracked SHORT.

Purpose: the core value is a safe, unattended trade loop, so no fill may ever go untracked.
Output: 4 new RED-first regression tests (test-only commit), then the minimal engine fix plus updates to the unrealistic legacy mocks (one commit).
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@.planning/quick/260925-ho6-fix-equity-engine-cancel-swallow-unconfi/260925-ho6-SUMMARY.md
@.planning/quick/260925-ho6-fix-equity-engine-cancel-swallow-unconfi/260925-ho6-CONTEXT.md
@bot/execution/engine.py
@tests/execution/test_engine.py

<interfaces>
Existing helpers in bot/execution/engine.py (from ho6). Reuse them; do not add new helpers:
- `_cancel_confirmed(row, placed_qty) -> bool` (module fn, ~line 79): False when row is None; True when dealt_qty >= placed_qty or order_status is in _TERMINAL_ORDER_STATUSES.
- `CancelUnconfirmedError(message, *, code, order_id, filled_qty=0, avg_price=0.0, fill=None)` (~line 60).
- `async ExecutionEngine._reread_order(order_id) -> Optional[dict]` (~line 339): returns the matching row, or None if get_order_status raised or no row matched.
- `ExecutionEngine._emit_entry_fill(intent, order_id, filled_qty, avg_price) -> FillEvent` (~line 356): writes the entry_fill_detected audit and log.
- `async ExecutionEngine._escalate_unconfirmed_cancel(err, *, code, order_id, side, dealt_qty, qty, filled_qty, avg_price, fill=None)` (~line 387): audits (error=str(err)), logs, does a cleanup cancel retry, sets the exit hold on SELL, sends the alert, then ALWAYS raises. `err` is only ever used through str(), so a plain string is fine.

Site 2 is the `# TTL expired for this attempt` block in `_manage_entry_order` (~lines 594-622). Site 4 is the `else:  # TTL expired with no fill` branch in `manage_exit` (~lines 876-913). In both, the re-read currently sits inside `if cancel_err is not None:`.

Callers already handle CancelUnconfirmedError (no change needed): bot/service/bot.py:309 (books exc.fill, or leaves the intent PENDING) and bot/position/manager.py:1303 (returns exc.filled_qty/avg_price).

Test helpers in tests/execution/test_engine.py: `_MockCfg` (entry_ttl 0.05, poll 0.01, entry_max_retries 2), `_MockIntent` (US.AAPL qty 100), `_make_mock_store()`, `_run(coro)`, `_cr04_find_audit_event(audit_mock, event_name)` (~line 1116).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: RED — 4 success-path race regression tests (test-only commit)</name>
  <files>tests/execution/test_engine.py</files>
  <behavior>
    - test_ttl_success_cancel_entry_partial_returns_fill: ORD-1 is SUBMITTED with dealt 0 on every poll. After the TTL cancel (which succeeds), the ORD-1 re-read returns CANCELLED_PART, dealt 30 @ 182.60. Expect a FillEvent with order_id "ORD-1", filled_qty 30, avg_fill_price approx 182.60; place_order awaited once; expire_pending_intent not called; no cancel_unconfirmed audit.
    - test_ttl_success_cancel_exit_partial_sizes_next_sell: manage_exit with qty 100. ORD-1 is SUBMITTED dealt 0 until cancelled, then CANCELLED_PART dealt 40 @ 182.40. ORD-2 is FILLED_ALL dealt 60 @ 182.30. ORD-3 is FILLED_ALL dealt 40 @ 182.30, so the unfixed engine still terminates. Expect place_order awaited exactly twice, second call's qty arg (await_args_list[1].args[1]) == 60, total_filled == 100, avg == approx((40*182.40 + 60*182.30)/100), and no cancel_unconfirmed audit.
    - test_ttl_success_cancel_entry_reread_failed_escalates: get_order_status raises GatewayError for any order_id already in the cancelled list, and returns SUBMITTED dealt 0 otherwise. Expect CancelUnconfirmedError with fill None and filled_qty 0; place_order once; expire_pending_intent not called; cancel_unconfirmed audited with order_id "ORD-1", side "BUY", error "post-cancel re-read failed".
    - test_ttl_success_cancel_exit_reread_failed_escalates_and_holds: ORD-1 is SUBMITTED dealt 0 until cancelled; after that, get_order_status raises GatewayError for ORD-1. Every other order is FILLED_ALL dealt 100 @ 182.20. Expect CancelUnconfirmedError with filled_qty 0 and place_order once. A second manage_exit for US.AAPL also raises, place_order is still awaited once, and exit_blocked_cancel_unconfirmed is audited.
  </behavior>
  <action>
Add a new section to tests/execution/test_engine.py directly after test_cr04_exit_hold_blocks_new_entry_for_held_code, before test_max_entry_chase_r_abandons_at_reprice_not_initial_placement. Give it a banner comment in the file's existing style: "CR-04 success-path re-read (quick 260925-inw)". Follow it with a 2-3 line note: a partial fill can land between the last poll and a SUCCESSFUL TTL cancel, so sites 2 and 4 must re-read after every TTL cancel.

Write the 4 tests named in <behavior>. Mirror the existing test_cr04_entry_ttl_fill_during_failed_cancel_returns_fill pattern exactly:
- Build a MagicMock gateway. place_order is AsyncMock(side_effect=["ORD-1", "ORD-2", "ORD-3"]).
- Set gw.get_ask_price (entry tests) or gw.get_bid_price (exit tests) to AsyncMock(return_value=...).
- cancel_order is a plain AsyncMock() that SUCCEEDS on every call.
- get_order_status is AsyncMock(side_effect=_status), where the sync function _status(order_id="") computes cancelled_ids = [str(c.args[0]) for c in gw.cancel_order.await_args_list] and branches on order_id and on whether order_id is in cancelled_ids.
- Rows use the keys order_id, code, order_status, qty, dealt_qty, dealt_avg_price, trd_side.
- Wrap each run in patch("bot.execution.engine.append_audit") as audit, and use _cr04_find_audit_event for the audit assertions.
- Exit tests call engine.manage_exit(code="US.AAPL", qty=100, side="SELL", escalation_step=0.10, escalation_cadence=0.01, ttl=0.05).
- Import GatewayError from bot.gateway.gateway for the raise side_effects. Use a message such as "order_list_query failed: rate limit".

In the two escalation tests, use pytest.raises(Exception) as ei. Import CancelUnconfirmedError from bot.execution.engine only AFTER that block closes, then assert isinstance. This way an unfixed engine fails with DID NOT RAISE (a behaviour failure) and never masks itself as a caught ImportError. This follows the same discipline as the ho6 red run.

Do NOT touch bot/ in this task. Run the new tests against the unmodified engine and confirm all 4 FAIL on behaviour:
- entry partial: returns None / assertion on fill.
- exit partial: 3 placements, or a second SELL sized 100.
- both re-read-failed tests: DID NOT RAISE.

Confirm every pre-existing test in the file still passes. Commit test-only: "test(260925-inw): add failing success-path TTL-cancel race regression tests", ending with the line "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>".
  </action>
  <verify>
    <automated>cd /Users/acdc/Documents/AI/ai-snp-trading-claude/.claude/worktrees/magical-mcclintock-b87dfe && git diff --quiet HEAD -- bot/ && python3 -m pytest -q tests/execution/test_engine.py -k ttl_success_cancel 2>&1 | tail -1 | grep -q "4 failed" && ! python3 -m pytest -q tests/execution/test_engine.py -k ttl_success_cancel 2>&1 | grep -q "ImportError" && python3 -m pytest -q tests/execution/test_engine.py -k "not ttl_success_cancel" 2>&1 | tail -1 | grep -qv failed</automated>
  </verify>
  <done>
- All 4 new tests exist and fail against the unmodified engine on behaviour, not on ImportError. The red output (which assertion failed, per test) is recorded for the SUMMARY.
- All pre-existing test_engine.py tests pass.
- bot/ is untouched.
- The test-only commit is made.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: GREEN — always re-read after the TTL cancel at sites 2 and 4, update unrealistic legacy mocks, full suite</name>
  <files>bot/execution/engine.py, tests/execution/test_engine.py</files>
  <behavior>
    - All 4 test_ttl_success_cancel_* tests pass.
    - All ho6 test_cr04_* tests pass unchanged, including test_cr04_preserve_entry_ttl_confirmed_cancelled_proceeds, where ORD-2/ORD-3 successful cancels now re-read SUBMITTED dealt 0 and proceed.
    - Legacy tests pass after changing mocks only. No assertion is changed or weakened.
  </behavior>
  <action>
Engine (bot/execution/engine.py only, minimal diff, per the locked design):

Site 2 (`_manage_entry_order`, TTL-expiry block after the inner poll loop):
- Keep the cancel try/except that captures cancel_err.
- Move the `self._reread_order(order_id)` call and the dealt/row_avg computation OUT of `if cancel_err is not None:` so they ALWAYS run once after the TTL cancel. Compute them exactly as today.
- If dealt > 0, build fill_event via `self._emit_entry_fill(intent, order_id, dealt, row_avg)`.
- Escalate via the existing `self._escalate_unconfirmed_cancel` when `row is None or (cancel_err is not None and not _cancel_confirmed(row, intent.quantity))`. Pass `cancel_err or "post-cancel re-read failed"` as err, with the same keyword args as today: side "BUY", dealt_qty=dealt, qty=intent.quantity, filled_qty=dealt, avg_price=row_avg, fill=fill_event.
- Then keep `if fill_event is not None: return fill_event`.
- Replace the stale comment ("dealt=0 keeps the pre-fix no-fill semantics when the re-read itself fails") with a "CR-04 site 2 (+ quick 260925-inw)" comment covering these points:
  (a) The re-read now also runs after a SUCCESSFUL cancel. A partial fill can land between the last poll and the cancel (CANCELLED_PART, dealt_qty > 0) and was previously dropped under a re-placed full-size BUY.
  (b) If the re-read fails on the success path (raises or no matching row), the cancel is confirmed but the filled qty is unknown, so treat it as unconfirmed. This matches ho6 D2. order_list_query returns today's cancelled orders, so an empty result is anomalous.
  (c) The success path deliberately skips the _cancel_confirmed status check. A successful cancel is trusted; escalating on a transient non-terminal status would halt every normal re-price.
  (d) Rate budget: this adds ONE order_list_query per TTL expiry. The cap is 10 calls per 30s (gateway RATE-01 retries on the cap), and paper account 1727266 is shared with the options bot. With rules.json today (entry poll 5s over a 20s TTL) that is about 4 polls + 1 re-read per attempt.
- The success path with the row found and dealt 0 must fall through to the unchanged re-price / abandon code.

Site 4 (`manage_exit`, the TTL no-fill else-branch):
- Same shape. De-indent the existing re-read and credit block out of `if cancel_err is not None:`:
  - row = `_reread_order`
  - dealt / dealt_price
  - if dealt > 0: add to total_filled and total_notional, recompute remaining, then audit and log exit_fill_detected exactly as today.
- Escalate when `row is None or (cancel_err is not None and not _cancel_confirmed(row, order_qty))`. Pass `cancel_err or "post-cancel re-read failed"` and keep today's kwargs: side "SELL", dealt_qty=dealt, qty=order_qty, filled_qty=total_filled, avg_price=blended total_notional/total_filled or 0.0. The SELL escalation sets the D5 exit hold, so no later SELL can over-sell.
- Then `if remaining <= 0: break`. This now also runs on the success path, so a fill that completes during the cancel ends the loop.
- Add a short "CR-04 site 4 (+ quick 260925-inw)" comment that says the same as the site 2 comment and points to it for the rate budget. Mention the exit cadence of 10s over a 15s TTL.

Docstrings (same file):
- Change `_reread_order`'s first line from "after a failed cancel_order" to say it runs after a failed cancel_order AND after a successful TTL cancel (sites 2/4, 260925-inw).
- Extend the module docstring CR-04 bullet with one sentence: the TTL-cancel sites also re-read after a SUCCESSFUL cancel, so a partial fill in the cancel window is booked, and a failed re-read escalates.
- Extend the Raises: notes in `_manage_entry_order` and `manage_exit` to include "or a successful TTL cancel whose re-read fails".
- Keep the file's comment style.

Do NOT touch sites 1/3, `_sync_broker_stop`, the exit hold persistence, bot/service/bot.py, bot/position/manager.py, or bot/main.py. Those are out of scope; the ho6 caller handling already covers the new raises (the interfaces block lists the lines to confirm by reading).

Legacy mocks (tests/execution/test_engine.py): some pre-existing tests return `[]` from get_order_status for a placed order after a successful cancel. That is unrealistic, because order_list_query always returns a just-cancelled order. These tests will now escalate. Known cases:
- test_ttl_cancel_replace: scenario 1 (the `return []` fallthrough in mock_order_status_s1), scenario 2 (gw2), and scenario 3 (gw3).
- test_max_entry_chase_r_abandons_at_reprice_not_initial_placement.

Fix them by adding ONE small sync helper next to the other helpers near the top of the file: `_unfilled_status_for(gw)`. It returns a function `(order_id="")` that yields a one-row list for the queried order_id, with dealt_qty 0 and dealt_avg_price 0.0. The row's order_status is CANCELLED_ALL if str(order_id) is in gw.cancel_order's awaited args, else SUBMITTED.
- For scenarios 2 and 3 and the max-chase test, use `AsyncMock(side_effect=_unfilled_status_for(gwX))`.
- In scenario 1, replace the fallthrough `return []` with a call to `_unfilled_status_for(gw1)(order_id)`.

Never change or weaken an assertion. Run the file. If the green run reveals any other pre-existing test failing for the same reason (no row for a cancelled order), fix only its mock the same way. Record every touched pre-existing test in the SUMMARY under Deviations, with the reason (unrealistic empty order_list_query after a cancel).

Commit engine.py + test_engine.py together: "fix(260925-inw): re-read after successful TTL cancel so a cancel-window partial fill is never dropped", ending with "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>".
  </action>
  <verify>
    <automated>cd /Users/acdc/Documents/AI/ai-snp-trading-claude/.claude/worktrees/magical-mcclintock-b87dfe && python3 -m pytest -q tests/execution/test_engine.py && test "$(grep -c 'post-cancel re-read failed' bot/execution/engine.py)" -ge 2 && python3 -c "import bot.main" && python3 -m pytest -q && git diff --name-only HEAD~1 -- bot/ | grep -vx bot/execution/engine.py | wc -l | grep -qx " *0"</automated>
  </verify>
  <done>
- test_engine.py is fully green.
- The full suite passes with 1151 passed, 1 skipped (baseline 1147 + 4), 0 failed.
- `import bot.main` is clean.
- Only bot/execution/engine.py changed under bot/.
- The success path with row found and dealt 0 is behaviourally unchanged.
- The SUMMARY records the red-run evidence and each touched legacy test as a deviation.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| engine → OpenD/broker order state | The engine's local fill bookkeeping must match broker truth. A cancel-window fill that is never re-read makes the two diverge. |
| engine → shared order_list_query budget | 10 calls/30s, shared with the options bot on acc 1727266. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-inw-01 | Tampering (position integrity) | engine.py site 2 `_manage_entry_order` TTL cancel | mitigate | Re-read unconditionally after the TTL cancel. dealt > 0 returns a FillEvent, so the shares are booked and protected by the ho6 caller path. There is no re-placed full-size BUY. |
| T-inw-02 | Tampering (over-sell into short) | engine.py site 4 `manage_exit` TTL cancel | mitigate | Add the re-read dealt qty to total_filled before sizing the next SELL. A failed re-read escalates and sets the D5 exit hold, so no blind SELL is placed. |
| T-inw-03 | Denial of Service (rate budget) | extra get_order_status per TTL expiry | accept | One call per TTL expiry (about 1 per 20s entry / 15s exit). The gateway's RATE-01 retries on the cap, and the budget is documented inline at site 2. |
| T-inw-04 | Denial of Service (false halt) | success-path escalation on a failed re-read | accept | Fail-safe by design: an unknown fill qty must not be traded blind. The operator gets a Telegram alert. The success path skips status checks, so normal re-prices never escalate. |
</threat_model>

<verification>
- Task 1 red run: 4 failed on behaviour (no ImportError), and pre-existing tests green, before any bot/ edit.
- Task 2: `python3 -m pytest -q tests/execution/test_engine.py` is green. `python3 -m pytest -q` gives 1151 passed, 1 skipped. `python3 -c "import bot.main"` is clean.
- `git diff` for the task touches only bot/execution/engine.py and tests/execution/test_engine.py.
</verification>

<success_criteria>
- A cancel-window partial fill after a successful TTL cancel is booked on entry and credited on exit. It is never dropped under a re-placed BUY or an oversized SELL.
- A failed post-cancel re-read on the success path escalates (audit + alert + exit hold on SELL + raise) instead of trading blind.
- The ho6 failed-cancel behaviour and the success path with a confirmed zero fill are unchanged.
- There are two atomic commits (test-only red, then fix), and the full suite is green.
</success_criteria>

<output>
Create `.planning/quick/260925-inw-fix-equity-engine-ttl-cancel-success-pat/260925-inw-SUMMARY.md` when done. It must include the red-run evidence, the touched legacy tests as deviations, and the final test count.
</output>
