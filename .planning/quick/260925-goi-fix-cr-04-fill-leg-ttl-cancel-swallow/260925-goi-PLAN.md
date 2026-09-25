---
phase: quick-260925-goi
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - bot/options/execution.py
  - tests/options/test_execution.py
  - bot/options/service.py
  - tests/options/test_service.py
autonomous: true
requirements: [MSO-08, CR-04, EX-03]

must_haves:
  truths:
    - "CR-04: when the TTL-path cancel_order raises and the immediate re-read shows dealt_qty < qty, fill_leg places NO further order and returns NO value. It raises RuntimeError('cancel of {order_id} unconfirmed: {code} {side} dealt {dealt}/{qty}') chained `from` the gateway error. The raise is inside the per-attempt try, so the 11-09 `except BaseException` path retries a shielded cancel_order(order_id). If the retry also fails, that path logs and audits leg_cancel_on_error_failed with the order_id"
    - "No false alarm for the one benign cause: if the TTL cancel raises but the re-read shows dealt_qty >= qty (the order filled while the cancel was in flight), fill_leg still returns (order_id, avg_price, dealt_qty) with one place_order and one cancel_order"
    - "Callers escalate and never report a clean outcome. Entry unwind: the order's cancel is unconfirmed, so open_position returns False (never None), and the row goes NEEDS_ATTENTION (CR-02). Manage close: close_legs raises, which logs options_close_error, and the row goes NEEDS_ATTENTION (existing path, unchanged). Opening leg: open_position raises (it is byte-unchanged, and its opening fill_leg call is outside the unwind's try)"
    - "EX-03: when open_position raises any Exception, OptionsBot._try_open logs error options_entry_open_error (with exc_info) and sets filled = False. That reuses the CR-02 branch unchanged: NEEDS_ATTENTION, the 'UNWIND INCOMPLETE … cancel any working orders …' alert naming every leg code, the options_entry_unwind_incomplete audit, and `return pos`. The row is never ABORTED and never left OPENING without an alert. asyncio.CancelledError still propagates out of _try_open"
    - "Invariants. execution.py changes only inside fill_leg (body, docstring) and the module docstring. _poll, open_position and close_legs are byte-unchanged. There is exactly one place_order call site (LIMIT-only), and longs-first open / shorts-first close are unchanged. service.py changes only inside _try_open. strategy.py, config.py, schema.py, store.py, universe.py, bot/gateway/, bot/service/watchdog.py, scripts/, rules.json and rules_options.json are byte-unchanged. Full suite: 1350 passed, 1 skipped (baseline 1345 passed, 1 skipped at a580871)"
  artifacts:
    - path: "bot/options/execution.py"
      provides: "fill_leg raises instead of escalating or returning when the TTL cancel is unconfirmed (CR-04)"
      contains: "unconfirmed"
    - path: "bot/options/service.py"
      provides: "_try_open routes an open_position exception into the CR-02 NEEDS_ATTENTION branch (EX-03)"
      contains: "options_entry_open_error"
    - path: "tests/options/test_execution.py"
      provides: "CR-04 executor tests (replaces test_cancel_failure_does_not_break_the_loop), including the reviewer's repro"
      contains: "test_open_position_short_leg_ttl_cancel_failure_raises_never_clean_unwind"
    - path: "tests/options/test_service.py"
      provides: "EX-03 service test"
      contains: "test_entry_scan_open_position_error_flags_needs_attention"
  key_links:
    - from: "bot/options/execution.py fill_leg TTL block"
      to: "fill_leg except BaseException → asyncio.shield(self._gw.cancel_order(order_id))"
      via: "raise RuntimeError(...) from cancel_error inside the per-attempt try, before the `if dealt_qty > 0` partial return"
      pattern: "cancel of \\{order_id\\} unconfirmed"
    - from: "bot/options/service.py _try_open"
      to: "the existing `if filled is False:` CR-02 branch"
      via: "try: filled = await self._executor.open_position(...) / except asyncio.CancelledError: raise / except Exception: filled = False"
      pattern: "options_entry_open_error"
---

<objective>
Close CR-04 (11-REVIEW.md, BLOCKER), recorded as the operator decision in 11-UAT.md item 5 ("fix CR-04", 2026-09-25). Also route EX-03, because the CR-04 fix sends more failures into it.

The bug. `LegExecutor.fill_leg` swallows a failed TTL-path `cancel_order` (execution.py:132-135). It then re-reads the order and, when dealt is 0, places the next, more aggressive order while the previous one may still be working. `gateway.cancel_order` raises GatewayError on any non-RET_OK result, with no rate-limit retry, and the account is shared with the equity bot. The reviewer reproduced it: during an entry, one failed cancel on the short leg ends with a row marked ABORTED "legs unwound" while a SELL short-put order is still live and untracked. The same happens on the close path and for a partial fill returned as final.

Purpose: an ABORTED or CLOSED row must never hide a live bot order that can fill into a naked short.

Output: a root-cause fix in fill_leg, which every caller routes through: open_position's opening legs, the open_position unwind, the manage close and the UAT probe. It adds a few-line EX-03 reuse in `_try_open` and five new tests (net +5). The operator asked to stop the phase-11 review/fix loop after this, so the scope is kept minimal.

Design choice, "cancel unconfirmed" (Claude's discretion): unconfirmed means `cancel_order` raised AND the immediate re-read shows `dealt_qty < qty`. The re-read row's `order_status` (CANCELLED_*, FAILED, and so on) is NOT consulted. Why:
(1) `_poll` must stay byte-unchanged, and it returns only (dealt_qty, avg_price). Reading the status would need a second `order_list_query` per TTL expiry, which spends the 10-per-30s rate budget shared with the equity bot, or a duplicate row parse inside fill_leg.
(2) The one common benign cause, a fill during the cancel, is already confirmed by `dealt_qty >= qty` and still returns the fill tuple.
(3) The only remaining false alarm is an order the broker ended on its own during the TTL window. It fails closed: NEEDS_ATTENTION plus an alert, the same "false alarm on the safe side" that 11-09 accepted. A misread status string would fail open, turning a live order into "confirmed".
The exception type is a plain RuntimeError, not a new class. Every caller already catches `Exception`, and no existing class fits: GatewayError is the broker layer's own type.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/STATE.md
@.planning/phases/11-multi-strategy-options-bot-bull-call-spread/11-REVIEW.md
@.planning/phases/11-multi-strategy-options-bot-bull-call-spread/11-09-SUMMARY.md
@bot/options/execution.py
@tests/options/test_execution.py

Key facts (verified at a580871; do not re-derive):
- `fill_leg` spans execution.py lines 55-200. In it, the TTL block is lines 130-143: a swallow try/except around `self._gw.cancel_order(order_id)`, then a re-read via `self._poll(order_id)`, then an `if dealt_qty > 0:` partial return, then escalation. The per-attempt `try` opens at line 110. `except GeneratorExit: raise` is at 152, and `except BaseException:` with the shielded cancel and `raise` is at 156-190. `_poll` is 202-211, `open_position` 217-304 and `close_legs` 306-380. The module docstring is lines 1-25.
- `open_position` calls the opening `fill_leg` at line 255, outside any try. Only the unwind's `close_legs` call (line 277-289) is wrapped in `except Exception` → `unwound = False` → `return False`.
- `close_legs` does not catch, so a fill_leg raise propagates. The service's manage close (`service.py:1218-1240`) catches `Exception`, logs `options_close_error` and ends NEEDS_ATTENTION. That is already tested with the real LegExecutor (`test_manage_close_poll_error_cancels_the_order_and_flags_needs_attention`).
- `OptionsBot._try_open` spans service.py lines 798-960. The `open_position` call is at 895-898, the `filled is None` ABORTED branch at 900-915 and the CR-02 `filled is False` branch at 917-945. `_scan_and_open`'s per-underlying `except Exception` (780-790) currently swallows an open_position raise, leaving the row OPENING with no alert (EX-03).
- `gateway.cancel_order` (gateway.py:1150-1181) raises GatewayError on any non-RET_OK result, with no retry. `get_order_status` rows carry `order_status`, but `_poll` does not return it.
- Test doubles in test_execution.py: `_gw(dealt, qty, price)`, whose place_order returns O1, O2, …; `_row(order_id, dealt, qty)`; `_cfg()`, with ttl 0.05, poll 0.01, max_retries 2 and step 0.03; `_legs()`, which is [WING BUY, SHORT SELL]; and `QUOTES`. Audit patching uses `from bot.options import execution as execution_module` plus `monkeypatch.setattr(execution_module, "append_audit", audit.append)`.
- Test doubles in test_service.py: `make_bot`, `store` (real OptionsStore), `alerter`, `gateway`, `_wire_scan(bot, gateway, monkeypatch)` (a US.SPY iron condor, 4 legs), `_statuses(store, status)`, and `monkeypatch.setattr(service, "append_audit", ...)`. The CR-02 block `test_entry_scan_incomplete_unwind_flags_needs_attention` is at about l.1348.
- Baselines at a580871: `python3 -m pytest -q tests/options tests/backtester/options` gives 469 passed. The full suite gives 1345 passed, 1 skipped. `grep -c "self._gw.place_order" bot/options/execution.py` gives 1, and `grep -c "self._gw.cancel_order"` gives 2. `grep -c 'set_position_status(pid, "NEEDS_ATTENTION")' bot/options/service.py` gives 5, and `grep -c 'set_position_status(position_id, "NEEDS_ATTENTION")'` gives 1.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: CR-04 — fill_leg raises instead of escalating/returning when the TTL cancel is unconfirmed</name>
  <files>tests/options/test_execution.py, bot/options/execution.py</files>
  <read_first>
    - bot/options/execution.py lines 1-25 and 55-200 (module docstring and fill_leg). Do not edit anything past line 200.
    - tests/options/test_execution.py lines 29-76 (helpers), 145-287 (the escalation and CR-03 tests, including the test being replaced at 167-170) and 348-473 (the open_position unwind tests, whose fakes you should mirror)
    - The reviewer's repro script /private/tmp/claude-501/-Users-acdc-Documents-AI-ai-snp-trading-claude--claude-worktrees-wonderful-knuth-7bbcb4/5eed6e67-474a-425b-bfd5-4ff21bc5f421/scratchpad/repro_ttl_cancel_swallow.py (optional; run with PYTHONPATH=. before and after the fix)
  </read_first>
  <behavior>
    - A: parametrized (dealt, qty) in [(0, 1), (1, 3)]. Every cancel fails. fill_leg raises RuntimeError matching `cancel of O1 unconfirmed.*dealt {dealt}/{qty}`, with `__cause__` equal to the gateway error. There is exactly ONE place_order. cancel_order is awaited with ("O1",) twice: the TTL cancel, then the 11-09 shielded retry. The audit is exactly [leg_cancel_on_error_failed {code SHORT, side SELL, order_id O1}], with no leg_fill_abandoned. At HEAD this returns None or a partial tuple after three placements, so it is RED
    - B (PRESERVATION): the TTL cancel raises, but the re-read shows dealt 1/1, so fill_leg returns ("O1", 2.05, 1) with one place_order and one cancel_order. It is GREEN at HEAD by design, and the mutation check below gives it teeth
    - C: the reviewer's repro at open_position level. The wing O1 fills and the short orders O2-O4 never fill. Every cancel of O2 fails. open_position RAISES RuntimeError matching `cancel of O2 unconfirmed`. It never returns None, which the service would book as ABORTED. place_order codes are [WING, SHORT], cancel args are ["O2", "O2"], and the audit events are exactly ["leg_cancel_on_error_failed"], with no open_position_unwound. At HEAD it returns None with O2 live, so it is RED
    - D: the unwind path the reviewer asked for ("False, not None"). The wing O1 fills. The shorts O2-O4 never fill, and their cancels succeed. The unwind's wing sale O5 never fills, and only O5's FIRST cancel fails. open_position returns False. place_order codes are [WING, SHORT, SHORT, SHORT, WING], with no O6. cancel args are ["O2", "O3", "O4", "O5", "O5"]. There is exactly one open_position_unwound audit, with complete False. At HEAD, O6 is placed and fills, so it returns None, a false clean unwind with O5 still working. RED
  </behavior>
  <action>
Write the tests first, in tests/options/test_execution.py.

(1) Delete `test_cancel_failure_does_not_break_the_loop`. It pins the unsafe behaviour: three orders are never cancelled and the result is reported as "nothing filled".

(2) In its place, add `test_ttl_cancel_failure_raises_and_places_no_next_attempt(monkeypatch, dealt, qty)`, parametrized as `@pytest.mark.parametrize("dealt, qty", [(0, 1), (1, 3)])`. Set up:
- `gw = _gw(dealt=dealt, qty=qty)`
- `gw.cancel_order = AsyncMock(side_effect=RuntimeError("modify_order failed"))`
- audit patched onto a list

Then assert behaviour A. Use `pytest.raises(RuntimeError, match=rf"cancel of O1 unconfirmed.*dealt {dealt}/{qty}") as excinfo`, then check `str(excinfo.value.__cause__) == "modify_order failed"`.

(3) Add `test_ttl_cancel_failure_after_full_fill_still_returns_the_fill` (behaviour B). Set up:
- a `state = {"cancelled": False}` dict
- an async `_cancel(oid)` that sets `state["cancelled"] = True` and then raises `RuntimeError("order already filled")`
- `gw.get_order_status = AsyncMock(side_effect=lambda oid: [_row(oid, 1 if state["cancelled"] else 0, 1)])`

Give it a one-line docstring saying it is a preservation pin against an over-eager fix.

(4) Add `test_open_position_short_leg_ttl_cancel_failure_raises_never_clean_unwind(monkeypatch)` (behaviour C). Set up:
- `gw = _gw()`
- `gw.get_order_status = AsyncMock(side_effect=lambda oid: [_row(oid, 0 if oid in ("O2", "O3", "O4") else 1, 1)])`. This is the same fill pattern as `test_short_leg_failure_unwinds_the_wing_aggressively`, so HEAD reproduces the reviewer's None result.
- a cancel side effect that raises `RuntimeError("modify_order failed")` for "O2" and returns None otherwise
- audit patched

Its docstring must say three things: open_position is byte-unchanged; the opening fill_leg call is outside the unwind's try, so an opening-leg failure raises out of open_position; and Task 2 turns that raise into NEEDS_ATTENTION.

(5) Add `test_unwind_ttl_cancel_failure_returns_false_not_none(monkeypatch)` (behaviour D). Set up:
- status `0 if oid in ("O2", "O3", "O4", "O5") else 1`
- a cancel side effect that raises only the first time it sees "O5", using a local set, and succeeds for every other call
- audit patched

Run the RED check before touching execution.py: `python3 -m pytest -q tests/options/test_execution.py -k ttl_cancel_failure`. Expect "4 failed, 1 passed" (B is the preservation pin), and record the line in the SUMMARY as "Red evidence (Task 1)".

Then fix fill_leg only, in the TTL block (current lines 130-143), per CR-04:
- Before the cancel, set `cancel_error = None`.
- Change the swallowing `except Exception: pass` to `except Exception as exc: cancel_error = exc`. Assign it: Python deletes the `as` name at the end of the except block.
- Keep the existing re-read `dealt_qty, avg_price = await self._poll(order_id)`. Directly after it, and BEFORE the existing `if dealt_qty > 0:` partial return, add: when `cancel_error is not None and dealt_qty < int(qty)`, raise `RuntimeError(f"cancel of {order_id} unconfirmed: {code} {side} dealt {dealt_qty}/{int(qty)}")` from `cancel_error`.
- The raise must stay inside the per-attempt try, so the existing `except BaseException` path performs the shielded retry cancel and the leg_cancel_on_error_failed log and audit. Do not add a separate log or audit call: every caller already logs the propagated exception with exc_info (open_position_unwind_error, options_close_error, and Task 2's options_entry_open_error).
- Replace the old "already fully filled / already cancelled — swallow (engine parity)" comment with a CR-04 comment. It should say that the order may still be working, so placing the next attempt would put a second live order out, and returning dealt_qty would size the unwind or close to a quantity that can still grow.
- Add a `ponytail:` comment naming the design ceiling. "Unconfirmed" means "cancel raised and not fully filled"; order_status is not consulted, because `_poll` returns only dealt/avg and a second order_list_query costs rate budget. The ceiling is a NEEDS_ATTENTION false alarm when the broker already ended the order. Upgrade path: have `_poll` return order_status.

Update the docstrings:
- In the fill_leg docstring's "Guarantee (CR-03)" paragraph, say that on the TTL path, if the cancel raises and the order is not fully filled, fill_leg raises RuntimeError instead of escalating or returning (CR-04), so the retry below runs. Also add a Raises line for that RuntimeError.
- Optionally, extend the module docstring's fill_leg line (lines 8-10) with "a failed TTL cancel raises instead of escalating (CR-04)".

Do NOT touch `_poll`, `open_position`, `close_legs`, the place_order call, the pricing or escalation arithmetic, or the leg_fill_abandoned tail.

Mutation check (it gives B its teeth): temporarily drop the ` and dealt_qty < int(qty)` clause and run `python3 -m pytest -q tests/options/test_execution.py -k after_full_fill`. It must report "1 failed". Restore the clause and record the result in the SUMMARY as "Mutation evidence (Task 1)".

Commit ONLY bot/options/execution.py and tests/options/test_execution.py, with the message `fix(quick-260925-goi): CR-04 fill_leg raises when the TTL cancel is unconfirmed`.
  </action>
  <verify>
    <automated>python3 -m pytest -q tests/options/test_execution.py -k ttl_cancel_failure && python3 -m pytest -q tests/options tests/backtester/options</automated>
  </verify>
  <acceptance_criteria>
    - The SUMMARY records Red "4 failed, 1 passed" from `-k ttl_cancel_failure` on the unmodified a580871 execution.py, and Mutation "1 failed" from `-k after_full_fill` with the dealt clause removed
    - `python3 -m pytest -q tests/options/test_execution.py -k ttl_cancel_failure` reports "5 passed". `python3 -m pytest -q tests/options tests/backtester/options` reports 473 passed
    - `grep -c "def test_cancel_failure_does_not_break_the_loop" tests/options/test_execution.py` prints 0
    - `git diff a580871 -- tests/options/test_execution.py | grep '^-[^-]' | grep -v -e 'test_cancel_failure_does_not_break_the_loop' -e 'gw = _gw(dealt=0)' -e 'already filled' -e 'fill_leg(SHORT, "SELL", 1, 2.00, 2.10)) is None'` prints nothing (only the replaced test was removed)
    - `grep -n "unconfirmed" bot/options/execution.py` matches. `grep -c "already fully filled / already cancelled" bot/options/execution.py` prints 0
    - `grep -c "self._gw.place_order" bot/options/execution.py` prints 1, and `grep -c "self._gw.cancel_order" bot/options/execution.py` prints 2 (unchanged)
    - `git diff -U0 a580871 -- bot/options/execution.py | grep '^@@' | awk '{o=substr($2,2); n=split(o,a,","); s=a[1]+0; l=(n>1)?a[2]+0:1; e=s+l-1; if (!((s>=1&&e<=25)||(s>=54&&e<=200))) print "OUT-OF-RANGE", $0}'` prints nothing (hunks only in the module docstring or fill_leg)
    - `python3 -c "import ast,subprocess;f=lambda s:{n.name:ast.get_source_segment(s,n) for n in ast.walk(ast.parse(s)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))};o=f(subprocess.check_output(['git','show','a580871:bot/options/execution.py']).decode());n=f(open('bot/options/execution.py').read());assert all(o[k]==n[k] for k in ('_poll','open_position','close_legs')),'changed';print('OK')"` prints OK
    - `git show --stat --format= HEAD` lists exactly bot/options/execution.py and tests/options/test_execution.py
  </acceptance_criteria>
  <done>A failed TTL cancel can no longer lead to a second order being placed, or to a quantity being reported as final, while the first order may still be working. fill_leg raises; the shielded retry cancel runs and is audited if it fails; the unwind returns False; and the close ends NEEDS_ATTENTION. A fill during the cancel still returns normally.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: EX-03 — _try_open treats an open_position exception as an incomplete unwind (NEEDS_ATTENTION)</name>
  <files>tests/options/test_service.py, bot/options/service.py</files>
  <read_first>
    - bot/options/service.py lines 773-960 (the `_scan_and_open` candidate loop with its per-underlying except, and all of `_try_open`, including the CR-02 `filled is False` branch at 917-945). Edit only inside `_try_open` (798-960).
    - tests/options/test_service.py lines 1344-1451 (the CR-02 block: test_entry_scan_incomplete_unwind_flags_needs_attention and test_clean_unwind_keeps_aborted_contract), 432-441 (`_wire_scan`) and 130-131 (`_statuses`)
  </read_first>
  <behavior>
    - open_position raises RuntimeError("cancel of O3 unconfirmed: dealt 0/2"). The result is exactly one NEEDS_ATTENTION row (underlying US.SPY, closed_at None). No OPENING row and no ABORTED row remain. There is one alert, containing "UNWIND INCOMPLETE" and "cancel any working orders". There is one options_entry_unwind_incomplete audit carrying the row's position_id, and no options_position_aborted. "options_entry_open_error" is among the service logger's error calls, and "options_entry_underlying_error" is not. At HEAD the row stays OPENING with no alert, so this is RED
  </behavior>
  <action>
Write the test first in tests/options/test_service.py, placed after `test_clean_unwind_keeps_aborted_contract`: `test_entry_scan_open_position_error_flags_needs_attention(make_bot, store, gateway, alerter, monkeypatch)`. Set up:
- `audit_events = []` with `monkeypatch.setattr(service, "append_audit", audit_events.append)`
- `log = MagicMock()` with `monkeypatch.setattr(service, "_logger", log)`
- `bot = make_bot()`
- `_wire_scan(bot, gateway, monkeypatch)`
- `bot._executor = MagicMock(open_position=AsyncMock(side_effect=RuntimeError("cancel of O3 unconfirmed: dealt 0/2")), close_legs=AsyncMock())`
- `_run(bot._job_entry_scan())`

Then assert the behaviour above. Read the error event names as `[c.args[0] for c in log.error.call_args_list]`. Give the test a docstring naming EX-03 and CR-04: the opening-leg raise must reach the CR-02 branch.

RED check before touching service.py: `python3 -m pytest -q tests/options/test_service.py -k open_position_error_flags_needs_attention` must report "1 failed". Record it in the SUMMARY as "Red evidence (Task 2)".

Then fix, inside `_try_open` only, per EX-03 as scoped by the operator. Wrap the existing `filled = await self._executor.open_position(...)` call (current lines 895-898) in a try:
- `except asyncio.CancelledError: raise`. This matches the file's convention at the `_scan_and_open` and `_manage_position` call sites; shutdown cancellation keeps its reconcile-alert path.
- `except Exception:`, which logs `_logger.error("options_entry_open_error", position_id=position_id, underlying=code, strategy=cfg.name, exc_info=True)` and then sets `filled = False`.

Add a short comment: an open_position exception means legs may be filled and one order may still be working (for example CR-04's unconfirmed TTL cancel on an opening leg), which is exactly the CR-02 incomplete state, so the branch below is reused unchanged. There is no automatic unwind of filled opening legs, because legs open longs-first, so whatever filled is defined-risk; the alert names every code and carries `_WORKING_ORDERS_HINT`.

Update the `_try_open` docstring's Returns sentence to include "or an open_position exception (EX-03)".

Leave the `filled is None` and `filled is False` branches, `_scan_and_open` and every other function byte-unchanged. Do not add a new alert text or audit event.

Commit ONLY bot/options/service.py and tests/options/test_service.py, with the message `fix(quick-260925-goi): EX-03 open_position exception routes to NEEDS_ATTENTION`.
  </action>
  <verify>
    <automated>python3 -m pytest -q tests/options/test_service.py -k "open_position_error_flags_needs_attention or incomplete_unwind or clean_unwind_keeps_aborted" && python3 -m pytest -q</automated>
  </verify>
  <acceptance_criteria>
    - The SUMMARY records Red "1 failed" from `-k open_position_error_flags_needs_attention` on the unmodified service.py
    - `python3 -m pytest -q tests/options tests/backtester/options` reports 474 passed. `python3 -m pytest -q` reports 1350 passed, 1 skipped, 0 failed
    - `grep -c '"options_entry_open_error"' bot/options/service.py` prints 1
    - `grep -c 'set_position_status(position_id, "NEEDS_ATTENTION")' bot/options/service.py` prints 1, and `grep -c 'set_position_status(pid, "NEEDS_ATTENTION")' bot/options/service.py` prints 5 (the branch is reused, not duplicated)
    - `python3 -c "import ast;s=open('bot/options/service.py').read();t=[n for n in ast.walk(ast.parse(s)) if getattr(n,'name','')=='_try_open'][0];src=ast.get_source_segment(s,t);i=src.index('except asyncio.CancelledError:');assert i<src.index('options_entry_open_error'),'order';print('OK')"` prints OK (CancelledError re-raised before the Exception handler)
    - `git diff -U0 a580871 -- bot/options/service.py | grep '^@@' | awk '{o=substr($2,2); n=split(o,a,","); s=a[1]+0; l=(n>1)?a[2]+0:1; e=s+l-1; if (!(s>=798&&e<=960)) print "OUT-OF-RANGE", $0}'` prints nothing (hunks only inside _try_open)
    - `git diff a580871 -- tests/options/test_service.py | grep '^-[^-]'` prints nothing (test only added)
    - `git show --stat --format= HEAD` lists exactly bot/options/service.py and tests/options/test_service.py
  </acceptance_criteria>
  <done>An exception out of open_position (CR-04's unconfirmed cancel on an opening leg, a failed poll, or a failing callback) now ends NEEDS_ATTENTION with the UNWIND INCOMPLETE alert, the working-orders hint and the audit. The row is never ABORTED and never left OPENING without an alert. Cancellation still propagates.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| LegExecutor → broker (place/poll/cancel) | A placed order outlives the bot's view of it unless a cancel is CONFIRMED. A swallowed cancel failure is an order nobody tracks |
| service → store row status | ABORTED and CLOSED rows leave reconcile's scope (`options_reconcile_external_ignored`). A wrong terminal status hides live exposure |
| service → operator (Telegram / audit) | NEEDS_ATTENTION plus the alert naming the codes and `_WORKING_ORDERS_HINT` is the operator's hand-off signal |

## STRIDE Threat Register (continues 11-09's T-11-51..T-11-61)

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-11-62 | Tampering (a naked short from the bot's own stale order; an untracked working order) | `fill_leg` TTL path: a failed cancel, then the next attempt, or a partial reported as final (CR-04) | mitigate | fill_leg raises RuntimeError (naming order_id and dealt/qty, chained from the gateway error) whenever the TTL cancel raised and the re-read shows dealt < qty. The raise is inside the per-attempt try, so the shielded retry and the leg_cancel_on_error_failed audit run, and there is exactly one live order per leg. Tests A, C and D (Task 1) |
| T-11-63 | Repudiation / Denial of Service (a silent OPENING row) | `_try_open` when open_position raises (EX-03). This re-dispositions T-11-58's EX-03 half from accept to mitigate | mitigate | An `except Exception` around open_position, which sets filled = False and so reaches the CR-02 NEEDS_ATTENTION branch, the alert with the codes and hint, and the audit. `except asyncio.CancelledError: raise` comes first. Test (Task 2) |
| T-11-64 | Denial of Service (a false alarm) | The TTL cancel raises for an order the broker already ended on its own, or the shielded retry succeeds | accept | This fails closed. The entry or close ends NEEDS_ATTENTION instead of a clean ABORTED or escalation, and the operator checks and resolves it. `order_status` is not consulted, for the reasons given in the objective. Preservation test B plus the mutation check guarantee that a fill during the cancel is NOT a false alarm. The added NEEDS_ATTENTION traffic is WR-09 (deferred) |
| T-11-65 | Tampering (a residual working order) | Both the TTL cancel and the shielded retry fail | accept | Same class as T-11-56. It is logged at error level and audited as `leg_cancel_on_error_failed` with the order_id. The row ends NEEDS_ATTENTION, and the alert tells the operator to cancel working orders on the named codes |
| T-11-66 | Tampering | EX-03 routing leaves already-filled opening legs open (there is no automatic unwind) | accept | Legs open longs-first (pick_strikes; the invariant is unchanged). With CR-04, at most ONE order per leg is outstanding, for the leg's own qty, so whatever filled or can still fill is covered by same-qty wings: defined risk. The operator is alerted with the codes and the hint |
| T-11-67 | Tampering (HTML injection) | alert text | mitigate | No new alert text. The reused CR-02 alert already passes every value through `_esc`. The new log event is structured logging, not HTML |
</threat_model>

<verification>
- Red → green per task: Task 1 goes from "4 failed, 1 passed" to "5 passed", with mutation evidence "1 failed". Task 2 goes from "1 failed" to "1 passed". Each Red run happens before that task's source edit and is recorded in the SUMMARY.
- Every commit is green. `python3 -m pytest -q tests/options tests/backtester/options` gives 473 after Task 1 and 474 after Task 2 (baseline 469).
- Phase gate: `python3 -m pytest -q` gives 1350 passed, 1 skipped, 0 failed (baseline 1345 / 1). The net +5 is: +1 from the parametrized replacement (2 tests for 1), +1 for B, +1 for C, +1 for D and +1 for the Task 2 test.
- `git diff --quiet a580871 -- bot/options/strategy.py bot/options/config.py bot/options/schema.py bot/options/store.py bot/options/universe.py bot/gateway/ bot/service/watchdog.py scripts/ rules.json rules_options.json` exits 0.
- `git diff --name-only a580871 -- bot/ scripts/ rules.json rules_options.json` lists exactly bot/options/execution.py and bot/options/service.py.
- `grep -c "self._gw.place_order" bot/options/execution.py` gives 1 (LIMIT-only, single order path). `_poll`, `open_position` and `close_legs` are byte-identical (the AST check in Task 1), so the longs-first open and the shorts-first close are unchanged.
- Offline only: no OpenD connection, no bot start, no access to data/*.db, and no `--live-1lot` probe.
</verification>

<success_criteria>
- CR-04 closed: no path in fill_leg places a next attempt, or returns a quantity, while the previous order's cancel is unconfirmed. The reviewer's repro now raises instead of returning a clean-unwind None.
- EX-03 closed: an opening-leg exception ends NEEDS_ATTENTION with an alert, never OPENING without an alert and never ABORTED.
- A fill that lands during a failed cancel still returns the normal fill tuple.
- The D-25 invariants hold: LIMIT only, one place_order site, longs-first open, shorts-first close, the options bot touches only its own option_legs codes, and SIMULATE only.
</success_criteria>

## Deferred (backlog): explicitly OUT OF SCOPE

The operator scoped this to CR-04 plus the few-line EX-03 reuse, and asked to stop the phase-11 review/fix loop after it.

- IN-12: the snapshot-outage counter is not session-scoped and is not reset on an empty book.
- IN-13: expiry-warning edge cases ("expires today" at dte < 0; the outage-branch dte computation outside the per-position try).
- WR-02: entry premium and max_loss are recorded at the pre-trade mid, not the fill.
- WR-03: the `daily_scan` read has no tie-break.
- WR-04: `legacy_view` runs before validation.
- WR-08: a partially closed short is recorded as CLOSED.
- WR-09: NEEDS_ATTENTION rows have no resolve tool and permanently consume BP and slots. This plan INCREASES traffic into NEEDS_ATTENTION (T-11-64), so it is the next priority.
- IN-01…IN-07, IN-09, IN-10, IN-11 (carried). IN-09 note: when the reused CR-02 alert is reached via EX-03, it says "UNWIND INCOMPLETE" although no unwind ran. The operator scoped the wording as unchanged, and the codes and the hint are correct. IN-11 note: the UAT probe (scripts/, unchanged) now gets a traceback rather than a silent "closed" on an unconfirmed cancel. That is still operator-at-console, and it inherits the fix.
- NEW, the equity-bot sibling of CR-04: `bot/execution/engine.py` swallows a failed `cancel_order` and then escalates or places again on the entry TTL (about l.392-395) and the exit TTL (about l.624-627). The D-06 partial path (about l.362-365) accepts a partial fill while the remainder may still be working. That is the same class of bug: a duplicate BUY entry, or a duplicate SELL that could leave the account short. It is out of scope (a different module and bot, with its own tests). Recommend a separate quick task.
- NEW, EX-03 residual: an exception in `_try_open` after the row insert but outside the open_position call (`insert_option_leg`, `set_position_status`, `alerter.send`) still reaches `_scan_and_open`'s per-underlying except with the row OPENING until restart. This existed before and is not a fill_leg path.
- Carried residuals: `_shutdown` ordering, which should cancel and await jobs before `gateway.close` (T-11-56); a readiness-gate warning for OPEN rows with dte <= 0 (T-11-57); counting manage cycles that raise before the snapshot (T-11-59).

<output>
Create `.planning/quick/260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow/260925-goi-SUMMARY.md`. It must include:
- the "Red evidence (Task 1)", "Mutation evidence (Task 1)" and "Red evidence (Task 2)" lines
- the per-commit counts (473 / 474) and the final full suite (1350 passed / 1 skipped)
- both commit hashes, each confirmed to contain only that task's source and tests
- the "cancel unconfirmed" design choice (cancel raised and dealt < qty; order_status not consulted) and its ceiling
- the note that open_position raises, rather than returning False, for an opening-leg failure, because it is byte-unchanged; the service-level False comes from EX-03
- T-11-62..T-11-67, with T-11-58's EX-03 half re-dispositioned to mitigate
- the reviewer repro's before/after output, if it was run

Do not edit phase-11 docs (11-REVIEW.md, 11-UAT.md) in the task commits.
</output>
