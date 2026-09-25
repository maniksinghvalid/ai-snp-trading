---
phase: 11-multi-strategy-options-bot-bull-call-spread
reviewed: 2026-09-25T14:20:00Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - backtester/options_run.py
  - bot/main.py
  - bot/options/config.py
  - bot/options/execution.py
  - bot/options/schema.py
  - bot/options/service.py
  - bot/options/store.py
  - bot/options/strategy.py
  - bot/options/universe.py
  - bot/state/migrations.py
  - docs/research/2026-09-24-super-bull-call-spread.md
  - rules_options.json
  - scripts/uat_options_probe.py
  - tests/backtester/options/test_options_run.py
  - tests/options/conftest.py
  - tests/options/test_config.py
  - tests/options/test_dispatch.py
  - tests/options/test_execution.py
  - tests/options/test_service.py
  - tests/options/test_store.py
  - tests/options/test_strategy.py
  - tests/options/test_universe.py
  - tests/state/test_migrations.py
findings:
  critical: 1
  warning: 5
  info: 12
  total: 18
status: issues_found
---

# Phase 11: Code Review Report (fourth review, after gap closure 11-09)

**Reviewed:** 2026-09-25T14:20:00Z
**Depth:** standard. All 23 files were read. The focus was the 11-09 diff `06b6787..922a60c` (commits ef38a74, 6a3d067, bcd1c0a). Since the last review, only `bot/options/execution.py`, `bot/options/service.py` and their two test files changed. `git diff --stat` shows the other 19 files are byte-identical.
**Files Reviewed:** 23
**Status:** issues_found

## Summary

11-09 does what it claims for all four findings in scope. The details are in the disposition table below. Checks run:
- Full suite: `1345 passed, 1 skipped`.
- There is still exactly one `place_order` call site, and it is LIMIT only.
- `close_legs` is unchanged: shorts close first, and the EX-01 long block is intact.
- No new path into NEEDS_ATTENTION was added.

Checks on the new `fill_leg` wrapper:
- A normal return (full fill, TTL partial, or `None`) happens inside `try` and never enters the `except`. No order is cancelled after a successful return.
- The `(order_id, avg_price, filled_qty)` / `None` contract is unchanged.
- The original exception is re-raised with a bare `raise`, so it is never swallowed.
- A second CancelledError that arrives during the shielded cancel propagates, and the cancel keeps running as a shielded task.
- `cancel_order` runs through `run_in_executor`, so it cannot block the event loop.
- A repeat cancel after the TTL path already cancelled the order fails "safe" (a logged, audited false alarm), as the plan documents.

Checks on the expiry warning:
- It is keyed on `position_id` and fires once per process.
- It is only reachable when `dte <= 0` (`service.py:1062, 1166`), so never at dte ≥ 1.
- It changes no status.

Checks on the streak:
- It is scoped by `today`, which `_job_manage` derives from `now_et().date()` (ET, not host-local time).
- The rollover rule `(prev if day == today else 0) + 1` is correct.

**One BLOCKER remains, and it is older than 11-09, but 11-09's guarantee rests on it.** 11-09's CR-03 design says "Only the order of the current attempt can be live. Every earlier attempt's order was cancelled on the TTL path before the next placement" (11-09-PLAN l.88). The new docstring says "every order this method places is cancelled before it returns or raises, unless it filled". Both claims are false. The TTL-path cancel still swallows every failure (`execution.py:132-135`), and the loop then places the next, more aggressive order while the previous one may still be working.

This is **reproduced** below. One transient cancel failure on the short leg during an entry produces:
- a **clean-unwind `None`**, so the row is marked ABORTED with "legs unwound, position aborted";
- the wing already sold back;
- a **live SELL short-put order on the broker** that nothing tracks.

It is not the accepted T-11-56. That risk covers the *shielded* cancel failing, a path that is logged, audited, and escalated. This path is silent.

## Disposition of prior findings

| Prior ID | Status | Evidence |
|----------|--------|----------|
| CR-03 (the unwind order was left working when a status poll raised) | **RESOLVED for the exception path. The adjacent TTL path is still open, see CR-04.** | `execution.py:108-190`: a per-attempt `try`, `except GeneratorExit: raise`, `except BaseException` → `asyncio.shield(cancel_order)` → `raise`. The prior repro now cancels O5 (`test_unwind_poll_error_cancels_the_unwind_order`). All three hand-off alerts carry the leg codes and `_WORKING_ORDERS_HINT` (`service.py:526-531, 924-928, 1231-1235`). |
| WR-10 (a skipped final cycle meant an expiry-day position could expire with no alert) | **RESOLVED** (T-11-57, a late restart, is accepted) | `service.py:1166-1167` warns on the first counted expiry-day miss. `1062-1063` warns on an outage. The prior repro scenario is now `test_expiry_day_unquotable_warns_before_cutoff_even_if_final_cycle_is_skipped`. The automated close stays armed (`test_expiry_day_warning_keeps_the_automated_close`). |
| WR-11 (a persistent snapshot outage was never alerted) | **RESOLVED** (T-11-59 is accepted) | `service.py:1022-1048`: one alert at 3 consecutive cycles, and a clean cycle resets the counter and re-arms the alert. Outages still never touch the per-position streak. Session-boundary edges are covered in IN-12 (Info). |
| IN-08 (the streak carried overnight) | **RESOLVED** | `service.py:1149-1151` stores `(today, streak)`. `test_manage_near_expiry_streak_resets_on_a_new_session` shows streak 1 and no escalation. |
| WR-02, WR-03, WR-04, WR-08, WR-09 | **STILL OPEN (deferred)** | Those lines are unchanged. The current locations are listed below. |
| IN-01 … IN-07, IN-09, IN-10, IN-11 | **STILL OPEN (deferred)** | Unchanged. |
| EX-03 (a `fill_leg` exception while opening propagates, and the row stays OPENING with no alert) | **STILL OPEN (deferred, T-11-58)** | Its working order is now cancelled first. Note that the CR-04 fix below routes more failures into this path. |

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-04: A failed TTL-path cancel is swallowed, and the next attempt places a second order, so a clean "ABORTED" entry can leave a live, untracked short-put order

**File:** `bot/options/execution.py:130-151` (the TTL cancel `except Exception: pass`, followed by a re-read and the next attempt); reached from `open_position` (`execution.py:255-299`) and `close_legs` (`execution.py:362-365`); `bot/options/service.py:900-914` (ABORTED on `None`) and `service.py:449-450` (steady-state reconcile inspects only OPEN rows)

**Issue:** When the TTL expires, `fill_leg` calls `cancel_order` and ignores any exception, on the assumption that the order is "already fully filled / already cancelled". It then re-reads `dealt_qty`. If that is 0, it escalates and places a **new** order for the full quantity. `gateway.cancel_order` raises `GatewayError` on any non-RET_OK result, and it has no rate-limit retry (unlike `get_order_status`). So a transient failure leaves the old order working, with no log and no audit record:
- a modify_order rate limit (the account is shared with the equity bot);
- an OpenD timeout;
- a broker rejection while the order is still live.

The consequences depend on where this happens:
- **Entry, short leg (reproduced):** O2 (SELL short @2.03) fails to cancel. O3 and O4 are placed and cancelled. `fill_leg` returns `None`, `open_position` sells the wing back, and it returns `None` (a *clean* unwind). The service marks the row ABORTED and alerts "legs unwound, position aborted". O2 is still working. If it fills, the account is short a put with no wing. That is a naked short from the bot's own order, on a row that reconcile never inspects again (ABORTED codes fall into `options_reconcile_external_ignored`).
- **Close, long-wing leg:** attempt 1's SELL-wing order fails to cancel, and attempt 2 fills. The row is booked CLOSED. If attempt 1 then fills, it sells a wing the account no longer holds. The result is a naked short put, untracked.
- **Partial fill with a failed cancel:** `fill_leg` returns the partial tuple as a known quantity while the remainder is still working. The unwind or close sizes itself to a quantity that can still grow.

The existing test `test_cancel_failure_does_not_break_the_loop` (`tests/options/test_execution.py:167-170`) pins this unsafe behaviour. With cancel always failing and 0 dealt, it asserts `None` ("nothing filled") while three orders were never cancelled.

**Reproduced:** `scratchpad/repro_ttl_cancel_swallow.py` (real `LegExecutor`, a fake broker in which only O2's cancel fails once):
```
open_position -> None (None == clean unwind -> row ABORTED)
still-working broker orders: {'O2': {'code': 'US.SPY260320P600000', 'qty': 1, 'price': 2.03, 'side': 'SELL', 'live': True, 'dealt': 0}}
audit: [leg_fill_abandoned ..., open_position_unwound complete=True]
```
The only trace is `leg_fill_abandoned`. There is no error, no `leg_cancel_on_error_failed`, and no alert.

**Fix:** Never place the next attempt, and never return a quantity, while the previous order's cancel is unconfirmed. Raise instead. The new `except BaseException` path then retries the cancel (shielded), audits `leg_cancel_on_error_failed` if that also fails, and the callers escalate: unwind → False → NEEDS_ATTENTION, close → NEEDS_ATTENTION, open → EX-03.
```python
cancel_failed = False
try:
    await self._gw.cancel_order(order_id)
except Exception:
    cancel_failed = True

dealt_qty, avg_price = await self._poll(order_id)
if cancel_failed and dealt_qty < int(qty):
    # The order may still be working: its remainder can fill at any time.
    raise RuntimeError(f"cancel of {order_id} unconfirmed (dealt {dealt_qty}/{int(qty)})")
if dealt_qty > 0:
    ...partial return (unchanged)...
```
To avoid false alarms, you can check `order_status` in the re-read row (for example `CANCELLED_ALL`, `CANCELLED_PART`, `FILLED_ALL`, `FAILED`) instead of the bare `cancel_failed`. Replace `test_cancel_failure_does_not_break_the_loop` with a test that asserts `pytest.raises` and exactly one `place_order`. Also add a unwind test in which the first short cancel fails and `open_position` returns False (not `None`). Opening-leg failures now land on EX-03 (the row stays OPENING until restart). That is still strictly safer than a false ABORTED, but it is a reason to prioritise EX-03.

## Warnings

### WR-02 (carried, deferred): Entry premium recorded at the pre-trade mid instead of the fill
**File:** `bot/options/service.py:838-848`
**Issue:** Unchanged. `credit_per_spread` and `max_loss_usd` come from chain mids, not fills. That biases the debit gate, BP headroom and realized P&L.
**Fix:** After `open_position` returns a list, recompute from `filled[*]["entry_price"]` before setting the row OPEN.

### WR-03 (carried, deferred): `daily_scan` read has no tie-break
**File:** `bot/options/universe.py:87`
**Issue:** Unchanged. `ORDER BY rank ASC LIMIT ?` is non-deterministic across premarket and intraday rows that share a rank.
**Fix:** `ORDER BY rank ASC, gap_pct DESC, code ASC`, or filter `scan_pass='premarket'`.

### WR-04 (carried, deferred): `legacy_view` runs before validation
**File:** `bot/options/config.py` (`legacy_view`); `backtester/options_run.py:229`
**Issue:** Unchanged. It can raise a raw `KeyError` that is not caught, and a per-strategy override is silently replaced by a global knob.
**Fix:** Validate through `load_options_book` before projecting, and catch `KeyError`/`TypeError` in `options_run`.

### WR-08 (carried, deferred): A partially closed short leg is recorded as `status='CLOSED'`
**File:** `bot/options/service.py:1214-1216`
**Issue:** Unchanged. `_on_exit_filled` writes CLOSED whatever `filled_qty` was.
**Fix:** Compare `filled_qty` with the leg qty, and record `PARTIAL` or the closed quantity.

### WR-09 (carried, deferred): NEEDS_ATTENTION rows cannot be resolved and permanently use BP and slots
**File:** `bot/options/service.py:745-756`
**Issue:** Unchanged. With `max_concurrent_positions: 4`, four stuck rows switch a strategy off with no alert. The CR-04 fix adds traffic to NEEDS_ATTENTION and OPENING.
**Fix:** Add an operator resolve command (`CLOSED`, `close_reason='manual'`, `realized_pnl_usd`, plus an audit entry), and log `options_entry_scan_skipped reason=concurrent_cap`.

## Info

### IN-12 (new): The outage counter is not session-scoped and is not reset on an empty book
**File:** `bot/options/service.py:994-998, 1022-1026`
**Issue:** `_snapshot_outage_cycles` is updated only after the `if not positions: return` early exit. It also carries across sessions, which is the same class of problem IN-08 fixed for the streak. Two effects follow:
- **Early alerts.** Outages at 15:45 and 15:50 (count 2), or two outage cycles and then an empty book, make the *first* outage cycle of a later episode alert at once. The error is on the human side.
- **Missing alerts.** An outage that persists across sessions alerts only on day 1. The counter is past 3 on day 2 (assignment-guard day for dte=1), so no alert is sent then, although the expiry-day warning still fires.

**Fix:** Store `(today, cycles)` like the streak, reset it on the early return, or re-alert once per session.

### IN-13 (new): Edge cases in the expiry-warning helper
**File:** `bot/options/service.py:1062, 1273-1300`
**Issue:**
1. At dte < 0 (a position still OPEN after expiry because the broker still lists the contract), the text says "expires today".
2. In the outage branch, `date.fromisoformat(pos["expiry"])` and `_warn_expiry_unmanaged` run **outside** the per-position `try`. A malformed expiry on one row would abort the whole cycle, including the breaker check. Rows are written by the bot, so this is low risk.

**Fix:** Word the alert by `dte` ("expired" when `dte < 0`). Move the outage-branch dte computation inside a try, or reuse the per-position guard.

### IN-09 (carried): The CR-02 NEEDS_ATTENTION alert lists every leg, not the ones actually open
**File:** `bot/options/service.py:919-928`
**Fix:** Carry per-leg filled quantities or order ids from the unwind (WR-08 scope).

### IN-10 (carried): `options_manage_missing_quote` does not tell a missing quote from an unmarkable one
**File:** `bot/options/service.py:1195` (the `else` branch)
**Fix:** Add `reason="unmarkable"|"invalid"`.

### IN-11 (carried, probe): The UAT probe closes against an unvalidated snapshot
**File:** `scripts/uat_options_probe.py:226, 238`
**Fix:** Filter through `service._quote_ok`, and wrap `close_legs` so that any exception sets NEEDS_ATTENTION.

### IN-01 (carried): The EOD HTML "Credit" column is negative for debit rows
**File:** `bot/options/service.py:312, 343`
**Fix:** Render `_premium_label(...)`, and rename the header to "Premium".

### IN-02 (carried): `insert_option_position` silently defaults a missing `strategy_name`
**File:** `bot/options/store.py:59-73`; `scripts/uat_options_probe.py:184-190`
**Fix:** Raise on new inserts that have no strategy_name.

### IN-03 (carried): `main.py` dispatch assumes the rules JSON is an object
**File:** `bot/main.py:80`
**Fix:** Add an `isinstance(data, dict)` guard with an `[ERROR]` exit.

### IN-04 (carried): `equity_state_db` ignores `BOT_STATE_DB`
**File:** `bot/options/config.py`; `bot/options/universe.py:51`
**Fix:** Document the coupling, or log the resolved path at startup.

### IN-05 (carried): The provenance doc does not list the 60% vs 40–50% profit-target deviation
**File:** `docs/research/2026-09-24-super-bull-call-spread.md`
**Fix:** Add a Deviations bullet.

### IN-06 (carried): A blocking sqlite read with a 5 s busy timeout runs on the event loop
**File:** `bot/options/universe.py:58`; `bot/options/service.py:702`
**Fix:** Lower `timeout_s` to about 0.5 s, or use `run_in_executor`.

### IN-07 (carried): A skipped position counts as $0 unrealized for the breaker
**File:** `bot/options/service.py:1053-1064, 1194-1196`
**Fix:** Log `unquoted_positions=` on the breaker check, or reuse the last good mark within a staleness bound.

---

_Reviewed: 2026-09-25T14:20:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
