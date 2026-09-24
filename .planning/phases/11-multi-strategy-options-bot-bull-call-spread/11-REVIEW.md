---
phase: 11-multi-strategy-options-bot-bull-call-spread
reviewed: 2026-09-24T17:01:53Z
depth: standard
files_reviewed: 22
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
  warning: 7
  info: 7
  total: 15
status: issues_found
---

# Phase 11: Code Review Report (re-review after gap closure 11-07)

**Reviewed:** 2026-09-24T17:01:53Z
**Depth:** standard. All 22 files were read. The focus was the 11-07 diff `4a065fd^..38b2538` in `bot/options/service.py` and `bot/options/execution.py`.
**Files Reviewed:** 22
**Status:** issues_found

## Summary

The 11-07 plan fixes what it set out to fix:
- `_quote_ok` now guards the shared manage path, and nothing is marked, decided on, or closed against a missing, `'N/A'`, NaN, or crossed quote.
- A `close_legs` exception ends in NEEDS_ATTENTION, never CLOSING.
- `close_legs` never sells a long once a short leg failed.
- The structure-kind check runs both at startup reconcile and in `_manage_position`.
- BP headroom and the per-strategy concurrent count now include every ACTIVE row.

`pytest tests/options tests/backtester/options tests/state/test_migrations.py`: 474 passed.

One BLOCKER remains. It is the deferred backlog item **EX-02**, and it is more serious than the 11-07 plan says. The plan calls it a "misleading ABORTED report". I reproduced it with a scratch script. When the entry unwind fails, `open_position` still returns None. The service then marks the row ABORTED, a status that BP headroom, `busy`, and reconcile all ignore. So live broker legs become invisible to the bot, and the same underlying can be traded again on top of them.

The new code also adds four WARNINGs:
- The Q-01 escalation fires on the first bad cycle, with no retry.
- `_quote_ok` still accepts one-sided or arbitrarily wide quotes.
- A partially closed short is recorded as `CLOSED`.
- NEEDS_ATTENTION rows now permanently use up BP and strategy slots, and there is no way to resolve them other than editing SQLite by hand.

## Disposition of prior findings

| Prior ID | Status | Evidence |
|----------|--------|----------|
| CR-01 (unusable quote marked as $0) | **RESOLVED** | `service.py:132-151` `_quote_ok` rejects None, `''`, `'N/A'`, NaN/inf, `ask<=0`, `bid<0`, and `ask<bid`. It is applied to every leg before `mark_spread` (`service.py:974`). The `close_legs` exception now leads to NEEDS_ATTENTION (`service.py:1021-1031`). Tests: `test_quote_ok_accepts_only_two_sided_numeric_quotes`, `test_manage_skips_*_on_invalid_quote`, `test_manage_invalid_long_quote_does_not_trip_breaker`, `test_manage_close_exception_flags_needs_attention`. The residual gap is WR-07 below: one-sided and wide quotes still pass. |
| Q-01 (skipped forever near expiry) | **RESOLVED, with a new defect** | `service.py:974-998`. It escalates on the first failed cycle with no retry. See WR-06. |
| WR-01 (structure-kind change leaves positions unmanaged) | **RESOLVED** | Startup reconcile (`service.py:415-457`) and manage (`service.py:963-970`) both compare the debit/credit kind. Iron condor and PCS deliberately share one kind. The `ponytail:` comment explains why that is safe today. |
| WR-02 (entry premium recorded at mid, not fill) | **STILL OPEN (deferred)** | `service.py:771-782` is unchanged. Carried below. |
| WR-03 (non-deterministic `daily_scan` rank order) | **STILL OPEN (deferred)** | `universe.py:87` is unchanged. Carried below. |
| WR-04 (`legacy_view` unvalidated; KeyError; silent override) | **STILL OPEN (deferred)** | `config.py:514-554` and `options_run.py:228-232` are unchanged. Carried below. |
| WR-05 (BP headroom ignores NEEDS_ATTENTION/CLOSING) | **RESOLVED, with a new consequence** | `service.py:687, 693`. It sums and counts every ACTIVE row. See WR-09: those rows can never be released. |
| EX-01 (close sells long after short failed) | **RESOLVED** | `execution.py:249-292`. Longs are blocked once `open_shorts` is non-empty. There are 3 parametrized cases plus a test for a failed long. |
| EX-02 (entry-unwind failure reported as "legs unwound", ABORTED) | **STILL OPEN (deferred), escalated to BLOCKER** | See CR-02. |
| IN-01 … IN-06 | **STILL OPEN** | None of the touched lines changed. Carried below. |

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-02 (EX-02): A failed entry unwind leaves live broker legs in an ABORTED row that BP, `busy`, and reconcile all ignore

**File:** `bot/options/execution.py:209-216`; `bot/options/service.py:835-849`, `92`, `405`, `509`

**Issue:** `open_position` discards the return value of `close_legs(filled, quotes, aggressive=True)`. It logs `open_position_unwound` and returns None whether or not the unwind worked. EX-01 made `close_legs` return False on purpose when a short could not be bought back, and its docstring (`execution.py:231-234`) says the caller escalates. `open_position` does not.

Reproduced with a scratch script. A 2-lot bull call:
1. The long leg fills 2.
2. The short leg fills 1 of 2.
3. The short buy-back fills 0.

The log shows `close_longs_skipped_short_open`, then `open_position_unwound unwound_legs=2`, then the call returns `None`. The broker is left holding +2 long calls and -1 short call.

`_try_open` then:
- sets the row `ABORTED`;
- sends "legs unwound, position aborted".

After that:
- ABORTED is not in `_ACTIVE_STATUSES` (`service.py:92`). The position adds nothing to `open_max_loss_total` or `open_count`, and it is not in `busy` (`service.py:682`). The next scan can open another spread on the **same underlying**, on top of the stranded legs.
- Reconcile only loads OPEN/OPENING/CLOSING (`service.py:405`). The stranded codes are therefore not in `known_codes` and are logged at debug level as "external / ignored" (`service.py:509`). Nothing alerts, and the P&L of those legs never reaches the daily breaker.
- The operator receives a Telegram message saying the legs were unwound, which is false.

The 11-07 plan treats this as a cosmetic reporting issue. In fact it is unmonitored live exposure, plus a way to double up on an underlying. Before EX-01 the leftover was a naked short. After EX-01 it is defined-risk, but it is still invisible to the bot. The same thing happens when unwinding a long-only `filled` list and the long sale fails.

No test covers `open_position` when `close_legs` returns False.

**Fix:** Propagate the unwind result and escalate, rather than abort:
```python
# execution.py, open_position
unwound = await self.close_legs(filled, quotes, aggressive=True)
...
return None if unwound else False     # or raise / return a sentinel

# service.py, _try_open
if filled is None or filled is False:
    status = "ABORTED" if filled is None else "NEEDS_ATTENTION"
    self._store.set_position_status(position_id, status,
        closed_at=now_et().isoformat() if status == "ABORTED" else None,
        close_reason="open_failed")
    await self._alerter.send(
        f"<b>Options entry failed</b> {_esc(code)} — "
        + ("legs unwound, position aborted." if status == "ABORTED"
           else "UNWIND INCOMPLETE — legs still open; close manually."))
```
A NEEDS_ATTENTION row is active, so it keeps the underlying busy and counts against BP (WR-05). Add one test in which the short buy-back fills 0.

## Warnings

### WR-06: Q-01 permanently hands a position to manual control after one bad snapshot cycle

**File:** `bot/options/service.py:974-998`

**Issue:** The first cycle with any `_quote_ok` failure while `dte <= assignment_guard_dte` sets NEEDS_ATTENTION. That row then leaves the manage loop for good, because only OPEN rows are managed. Both shipped strategies use `assignment_guard_dte: 1` (`rules_options.json`). The bull call has `manage_dte: null`, so at DTE 1 the assignment guard is its only automated exit.

The first manage cycle runs at 09:35 ET (`service.py:84`). That is when quotes on single-name options are most likely to be briefly missing or crossed. Also, when `get_market_snapshot` fails for a whole chunk (`service.py:903-905`), `quotes` has no entries for up to 400 codes. In that case every position in the window escalates at once.

The result: one transient OpenD or quote hiccup cancels the automated aggressive close that would have run 5 minutes later. The operator then has to close by hand on the day before expiry. The 11-07 plan states "retries next cycle" outside the window, but inside the window there is no retry at all.

**Fix:** Escalate only after N consecutive failed cycles, or only in the last cycle before the manage cutoff. For example, keep a per-position counter in meta or on the instance:
```python
misses = self._quote_misses[pid] = self._quote_misses.get(pid, 0) + 1
if dte <= strat_cfg.assignment_guard_dte and (misses >= 3 or self._last_manage_cycle_today()):
    ...escalate...
```
Clear the counter whenever `bad` is empty. Also skip the escalation completely when the snapshot chunk itself failed, and log that as a snapshot outage instead.

### WR-07: `_quote_ok` accepts one-sided (`bid=0`) and arbitrarily wide quotes, so a spurious exit is still possible

**File:** `bot/options/service.py:148-151`; test `tests/options/test_service.py:1117`

**Issue:** The gate is described as "two-sided", but `{"bid": 0, "ask": X}` passes for any X, and nothing bounds `ask - bid`. Consider an illiquid single-name long call quoted `0.10 / 9.00`:
- It gets a mid of 4.55.
- `manage_decision_debit` fires `profit_target`.
- `close_legs` buys the short back, which succeeds, and then works the long at 4.53, then 4.50, 4.47, 4.44.
- The long order is never filled, and the row goes to NEEDS_ATTENTION.

So a bad quote turned a managed spread into an unmanaged naked long, plus a manual-intervention alert. On the credit side, a wide ask on a short leg inflates the mark and can falsely trip the global daily-loss breaker. This is the same failure CR-01 fixed, now caused by a wide quote rather than a missing one. The entry path already rejects these through `leg_is_liquid` (`bid <= 0`, width limits).

**Fix:** For *decisions* other than `assignment_guard`, require `bid > 0` and a sane width. For example, reuse the strategy's `max_spread_pct_of_mid` with a looser multiplier, or `spread <= max(0.5 * mid, 0.10)`. Keep the looser `_quote_ok` only for the guard-window close, where closing at any price beats pin risk.

### WR-08: A partially closed short leg is recorded as `status='CLOSED'` with an exit price

**File:** `bot/options/service.py:1017-1019`; `bot/options/execution.py:294-295`

**Issue:** `close_legs` calls `on_leg_filled` for partial fills (`result is not None`). `_on_exit_filled` then writes `status="CLOSED"` and the exit price, whatever `filled_qty` was. EX-01 makes "short partially bought back, longs held" an expected outcome. In that case the DB says the short is CLOSED, but the broker still holds `qty - filled_qty` short contracts.

The position goes to NEEDS_ATTENTION, and reconcile never checks NEEDS_ATTENTION rows again. The operator's only record of what is still open is therefore wrong on exactly the leg that carries the risk.

**Fix:** Pass `filled_qty` through and record it:
```python
async def _on_exit_filled(leg, order_id, price, filled_qty):
    full = filled_qty >= int(leg.get("filled_qty") or leg.get("qty"))
    exits[leg["code"]] = price
    self._store.set_leg_exit(leg["leg_id"], price=price,
                             status="CLOSED" if full else "PARTIAL")
```
Alternatively, store the closed quantity in a column.

### WR-09: NEEDS_ATTENTION rows are terminal with no way to resolve them, and they now permanently use up BP and strategy slots

**File:** `bot/options/service.py:683-693`

**Issue:** After WR-05, every NEEDS_ATTENTION row counts against `open_max_loss_total` and the strategy's `open_count`, and (as before) keeps its underlying in `busy`. The code comment says "the operator clears it by resolving the row". However:
- nothing in `bot/`, `scripts/`, or the docs changes a NEEDS_ATTENTION row to a terminal status;
- no runbook describes how to do it.

After the operator closes the legs in moomoo, the row still counts against BP and slots until someone hand-edits `data/options_state.db`. With `max_concurrent_positions: 4` for `super_bull_call`, four stuck rows turn the strategy off with no alert. `options_entry_scan` simply breaks out on the cap. 11-07 also adds two new ways into NEEDS_ATTENTION: Q-01 and the close-exception path. Those rows' realized P&L is never recorded either.

**Fix:** Add a small operator command, for example `python3 -m bot.options.resolve <position_id> --realized <usd>`. It would set `CLOSED`, `close_reason='manual'`, and `realized_pnl_usd`, and write an audit entry. Document it in CLAUDE.md next to the options-bot run instructions. At minimum, log `options_entry_scan_skipped reason=concurrent_cap` when the cap is reached.

### WR-02 (carried, deferred): Entry premium recorded at the pre-trade mid instead of the fill

**File:** `bot/options/service.py:771-782`, `1000-1043`
**Issue:** Unchanged since the prior review. `credit_per_spread` and `max_loss_usd` come from chain mids. The executor fills at the mid ± $0.02 or worse, and escalation can add up to $0.11 per leg. As a result, the 1/4-rule debit gate, BP headroom, and realized P&L are all biased in the bot's favour. The per-leg `entry_price` values are persisted but never used.
**Fix:** After `open_position` succeeds, recompute the net premium from `filled[*]["entry_price"]`, and update `credit_per_spread` and `max_loss_usd` before setting the row OPEN (see the prior report for the snippet).

### WR-03 (carried, deferred): `daily_scan` read has no tie-break when premarket and intraday rows share a rank

**File:** `bot/options/universe.py:87`
**Issue:** Unchanged. `ORDER BY rank ASC LIMIT ?` has no tie-breaker. The equity bot's intraday rescans reuse rank numbers, so which 20 names are returned, and in what order, depends on SQLite row order.
**Fix:** Use `ORDER BY rank ASC, gap_pct DESC, code ASC` at minimum. Better, filter explicitly to `scan_pass='premarket'`, and update D-17 to match.

### WR-04 (carried, deferred): `legacy_view` runs before validation

**File:** `bot/options/config.py:514-554`; `backtester/options_run.py:228-232`
**Issue:** Unchanged. A missing `risk` or `service` key raises a raw `KeyError`, and `options_run` only catches `ConfigError`. A per-strategy override of a global knob is silently replaced, even though the live loader rejects the same file. So the backtest can run a config the bot refuses to start with.
**Fix:** Validate with `load_options_book` or the same `_validate` path before projecting, and catch `KeyError`/`TypeError` in `options_run`.

## Info

### IN-01 (carried): The EOD HTML "Credit" column prints a negative credit for debit positions
**File:** `bot/options/service.py:267, 293`
**Fix:** Render `_premium_label(p.get("credit_per_spread") or 0)` and rename the header to "Premium".

### IN-02 (carried): `insert_option_position` silently defaults a missing `strategy_name` to tasty
**File:** `bot/options/store.py:59-73`
**Fix:** Raise `ValueError` when `strategy_name` is missing for new inserts.

### IN-03 (carried): `main.py` dispatch assumes the rules JSON is an object
**File:** `bot/main.py:80`
**Fix:** `if not isinstance(data, dict): print("[ERROR] ...", file=sys.stderr); sys.exit(1)`.

### IN-04 (carried): `equity_state_db` ignores the `BOT_STATE_DB` override that the equity bot honours
**File:** `bot/options/config.py`; `bot/options/universe.py:51`; `bot/options/service.py:639`
**Fix:** Document the coupling in CLAUDE.md, or log the resolved absolute path at startup.

### IN-05 (carried): The provenance doc does not list the 60% vs the video's 40–50% profit target as a deviation
**File:** `docs/research/2026-09-24-super-bull-call-spread.md:21, 56`
**Fix:** Add a Deviations bullet for the fixed-target percentage.

### IN-06 (carried): Blocking sqlite read with a 5 s busy timeout on the event loop
**File:** `bot/options/universe.py:58`; `bot/options/service.py:639`
**Fix:** Lower `timeout_s` to about 0.5 s, or run the read in an executor.

### IN-07 (new): A quote-skipped position counts as $0 unrealized, so the daily breaker cannot see its loss
**File:** `bot/options/service.py:997, 912-923`
**Issue:** CR-01 correctly stops false breaker trips. The trade-off is that every skipped position, including every position during a snapshot outage, contributes 0.0 to `unrealized_total`. The breaker fails open for as long as the quotes stay bad. This is acceptable as a trade-off, but it is not documented.
**Fix:** Log `unquoted_positions=len(skipped)` on the breaker check, or reuse the last good mark (with a staleness bound) for the breaker sum only.

---

_Reviewed: 2026-09-24T17:01:53Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
