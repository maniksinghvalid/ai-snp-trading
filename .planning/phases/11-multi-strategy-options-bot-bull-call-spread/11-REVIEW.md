---
phase: 11-multi-strategy-options-bot-bull-call-spread
reviewed: 2026-09-24T20:15:39Z
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
  warning: 7
  info: 11
  total: 19
status: issues_found
---

# Phase 11: Code Review Report (third review, after gap closure 11-08)

**Reviewed:** 2026-09-24T20:15:39Z
**Depth:** standard. All 23 files were read. The focus was the 11-08 diff `f7ceede..284d0e3` (commits 3f32b6a, 7a6744f, 026844c) in `bot/options/execution.py`, `bot/options/service.py` and `scripts/uat_options_probe.py`.
**Files Reviewed:** 23
**Status:** issues_found

## Summary

11-08 does what it says for the three findings in scope:
- `open_position` now returns False when the entry unwind is incomplete.
- `_try_open` turns that False into an active NEEDS_ATTENTION row, and the same scan counts it against BP headroom.
- Near-expiry escalation needs a streak of misses or the expiry session's final cycle.
- `_quote_markable` gates every decision outside the guard window.

Checks run:
- Full suite: `1330 passed, 1 skipped`.
- `git diff f7ceede..HEAD` touches only the five files the plan lists.
- There is still exactly one `place_order` call site (LIMIT only), and `close_legs` is byte-unchanged (shorts first, EX-01 long block).
- `in_guard` uses the same `dte <= cfg.assignment_guard_dte` test, on the same `strat_cfg`, that both decision functions check first (`strategy.py:307, 352`). So the gate choice cannot disagree with the decision.
- `_quote_miss_streak` is keyed by `position_id` (a uuid4 hex, never reused). It is popped on any fully quoted cycle and on escalation. The close path is only reached after `bad` is empty, so the entry is already popped by then. The only stale entries belong to rows that leave OPEN through reconcile. They are bounded, as the `ponytail:` note says, and harmless unless a NEEDS_ATTENTION row is hand-edited back to OPEN.

The fixes open or expose three new problems. Each is reproduced with a scratch script under the session scratchpad (`repro_unwind_resting.py`, `test_repro_streak.py`):
1. **CR-03 (BLOCKER).** 11-08 now catches an exception raised during the unwind and alerts "UNWIND INCOMPLETE — close manually". But `fill_leg` leaves its aggressive unwind order **working on the broker** when the status poll raises. If the operator follows the alert, that order can still fill afterwards and create a naked short.
2. **WR-10.** The expiry-day "final cycle" escalation fires only if some manage cycle actually lands in the last 5 minutes. A skipped fire, which `max_instances=1` makes likely during a slow close, lets the position expire with no alert.
3. **WR-11.** Snapshot outages are never counted and never alerted. A snapshot failure that persists while OpenD stays connected (for example a quote-rights error, or one code that fails the whole chunk) therefore leaves every affected position unmanaged through expiry, and nothing is sent. The acceptance of T-11-46 relies on OpenDWatchdog, but the watchdog only polls `get_global_state` and cannot see this failure.

## Disposition of prior findings

| Prior ID | Status | Evidence |
|----------|--------|----------|
| CR-02 (EX-02: failed entry unwind left legs in an ABORTED row) | **RESOLVED, with a residual** | `execution.py:218-240` propagates the unwind result, and an exception counts as incomplete. `service.py:902-930` sets NEEDS_ATTENTION with no `closed_at`, sends a truthful alert, and returns `pos`, so the current scan counts the row (`service.py:777-781`). NEEDS_ATTENTION is in `_ACTIVE_STATUSES`, so later scans count it too. The clean-unwind ABORTED contract is unchanged. The probe honours False (`uat_options_probe.py:209-212`). Tests: `test_open_position_incomplete_unwind_returns_false` (3 cases), `test_entry_scan_incomplete_unwind_flags_needs_attention`, `test_incomplete_unwind_counts_against_bp_in_the_same_scan`, `test_clean_unwind_keeps_aborted_contract`. Residual: the exception branch hands the operator a position that may still have a working order. See CR-03. |
| WR-06 (Q-01 escalated after one bad cycle) | **RESOLVED, with new edge defects** | `service.py:1080-1124` implements the streak of `_QUOTE_MISS_ESCALATE_CYCLES = 3` plus the final-cycle rule. Failed chunks are skipped per position at `service.py:983-1006`. Edge defects: WR-10 (the final cycle can be skipped), WR-11 (a persistent outage is never escalated) and IN-08 (the streak carries overnight). |
| WR-07 (`_quote_ok` accepted one-sided or wide quotes) | **RESOLVED** | `service.py:174-188` adds `_quote_markable`. `service.py:1077-1079` selects the gate once. One-sided quotes with ask ≤ $0.10 are accepted on purpose (operator decision; not re-raised). The 14 gate cases, 4 manage cases, the breaker case and the 2 guard-window preservation cases all pass. |
| WR-02 (entry premium recorded at mid, not fill) | **STILL OPEN (deferred)** | `service.py:826-833` is unchanged. It also bounds T-11-45's under-count. |
| WR-03 (`daily_scan` order has no tie-break) | **STILL OPEN (deferred)** | `universe.py:87` is unchanged. |
| WR-04 (`legacy_view` runs before validation) | **STILL OPEN (deferred)** | `config.py:514`, `options_run.py:229-230` are unchanged. |
| WR-05 | RESOLVED (prior review) | Unchanged. |
| WR-08 (partial short close recorded as CLOSED) | **STILL OPEN (deferred)** | `service.py:1147-1149` is unchanged. |
| WR-09 (NEEDS_ATTENTION rows cannot be resolved and permanently use BP and slots) | **STILL OPEN (deferred), priority raised** | 11-08 adds two more ways into NEEDS_ATTENTION: the CR-02 unwind and the WR-06 escalation. |
| IN-01 … IN-07 | **STILL OPEN (deferred)** | None of the touched lines changed. IN-07 is now reached more often: every WR-07 reject and every outage skip also contributes $0 to the breaker. |
| EX-03 (a `fill_leg` exception while opening propagates, row stays OPENING) | **STILL OPEN (deferred)** | Unchanged. The resting-order defect in CR-03 applies to this path too. |

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-03: A failed status poll leaves the unwind order live on the broker, and the new "UNWIND INCOMPLETE — close manually" alert says nothing about it

**File:** `bot/options/execution.py:93-120` (`fill_leg`), `218-240` (new unwind `except` branch); `bot/options/service.py:902-930`, `1156-1170`

**Issue:** `fill_leg` places a LIMIT order (l.93) and then awaits `on_placed` (l.95), `_poll` → `gateway.get_order_status` (l.105, 120) and `cancel_order` (l.116). None of that is inside a try/finally. `get_order_status` raises `GatewayError` in two cases: any non-rate-limit error (for example an OpenD hiccup), or a rate limit that is still hit after the retries run out. The order_list_query budget is 10 requests per 30 s and is shared with the equity bot on account 1727266, while this loop polls every 4 s. When the poll raises, the working order is **never cancelled**.

This defect predates 11-08. What 11-08 changed:
- Before 11-08, an unwind exception propagated. The row stayed OPENING, and startup reconcile flagged it.
- Now `open_position` catches the exception (l.224-230), returns False, and `_try_open` alerts: "UNWIND INCOMPLETE — legs still open (...); close manually."
- T-11-38 records this path as mitigated.

The operator is told to close the legs by hand, while an aggressive order the bot placed may still be resting at the natural price.

**Reproduced** (`scratchpad/repro_unwind_resting.py`):
- Setup: the WING fills; the SHORT never fills on O2-O4; the unwind's `get_order_status` raises.
- Result: `result: False`. `placed` shows O5 = SELL WING. `cancelled: ['O2','O3','O4']`. **O5 is never cancelled.**
- Consequence: if the operator sells the wing manually and O5 then fills, the account is short one put with no protection. That is a naked short opened by the bot's own order, which breaks the defined-risk invariant.

Other paths with the same exposure:
- The 11-07 close-exception path (`options_close_error` → "close incomplete — check the account", `service.py:1156-1170`).
- EX-03 (the opening legs).
- Task cancellation during `fill_leg`. `asyncio.run` cancels pending job tasks at shutdown, and the row is then flagged "restarted mid-opening" while the order stays live.

**Fix:** Guarantee the cancel on every exit path from `fill_leg`, not only the TTL path, and name any order that may still be live in the escalation:
```python
order_id = await self._gw.place_order(code, int(qty), price, trd_side)
try:
    if on_placed is not None:
        await on_placed(order_id)
    ...poll loop / TTL cancel / re-read (unchanged)...
except BaseException:
    try:
        await asyncio.shield(self._gw.cancel_order(order_id))
    except Exception:
        _logger.error("leg_cancel_on_error_failed", code=code, order_id=order_id)
    raise
```
In the `_try_open` False alert and the `options_close_error` alert, add "check for working orders before closing manually". Better still, pass along the order_ids from the `open_position_unwound` audit. Add an executor test in which `get_order_status` raises on the unwind order, and assert that `cancel_order` was called with it.

## Warnings

### WR-10: Expiry-day escalation depends on one cycle that APScheduler can skip, so an unquotable position can expire with no alert

**File:** `bot/options/service.py:1091-1096`; job registration `service.py:589` (`max_instances=1`)

**Issue:** On expiry day (dte 0), a position whose streak is still below 3 escalates only if some cycle satisfies `now + 5 min >= cutoff`, which means it runs in [15:50, 15:55). There is exactly one such scheduled fire. It is lost in any of these cases:
- **The previous cycle overruns.** `options_manage` has `max_instances=1`, and APScheduler drops a fire while the previous run is still going. A non-aggressive close (for example a profit_target on another position) can take up to `(max_retries+1) × ttl_s` = 4 × 45 s = 3 min **per leg**, so one 2-leg close already exceeds the 5-minute interval.
- **The snapshot chunk fails on that cycle.** The position is skipped as an outage (l.997-1006) and never reaches the final-cycle test.
- **The bot restarts between about 15:50 and 15:55.** The first IntervalTrigger fire then lands after the cutoff.

**Reproduced** (`scratchpad/test_repro_streak.py::test_final_cycle_missed_never_escalates`):
- An expiry-day position is unquotable at 15:43 and at 15:48. Both are retries, because 15:48 + 5 = 15:53 < 15:55.
- With the 15:53 fire skipped, the position is still OPEN, 0 alerts have been sent, and `_is_rth_now()` is False at 15:58. It expires unmanaged, and the only trace is `..._retry` log warnings.
- For a bull call, the risk is exercise or assignment of 100 × qty shares per contract, which is exactly what the guard window exists to prevent.

The `ponytail:` note considers only the opposite error (a delayed cycle reading as final one cycle early).

**Fix:** On expiry day, stop depending on a single cycle. The simplest option is to widen the final window by one missed cycle: `now + 2 * interval >= cutoff`. The more robust option is to send a one-time heads-up alert (no status change) on the **first** counted miss when `dte <= 0`, so the operator is never silent at expiry. Also let an outage on an expiry-day position count once `final_cycle` is true (see WR-11).

### WR-11: A persistent snapshot failure is treated as transient forever, so near-expiry positions are never escalated and never alerted

**File:** `bot/options/service.py:983-1006`; plan threat T-11-46

**Issue:** Any chunk with `ret != _RET_OK` puts all of its codes (up to 400, which in practice is the whole book) into `unsnapped`. Every position with a leg in that set is skipped, and the only signal is a log warning. There is no counter and no alert, and the skip happens even on the final cycle of expiry day.

Before 11-08, a failed chunk produced `bad` for every leg, so positions inside the guard window escalated and the operator was alerted. 11-08 removed that last alerting path. T-11-46 accepts the risk on the grounds that "OpenDWatchdog alerts on OpenD disconnect". But `bot/service/watchdog.py` polls only `get_global_state()`, which reports connection and login state. The failures that plausibly **persist while OpenD stays connected** are the ones it cannot see:
- a US-options quote-rights or subscription error;
- a code that makes the SDK reject the whole snapshot request.

In either case every OPEN position goes unmanaged through its assignment-guard window and expiry with zero Telegram messages. The same failure also blinds the breaker (IN-07).

**Fix:** Keep outages from counting toward the per-position streak (operator scope), but make them visible:
```python
if unsnapped:
    self._snapshot_outage_cycles += 1
    if self._snapshot_outage_cycles == _QUOTE_MISS_ESCALATE_CYCLES:
        await self._alerter.send("<b>Options NEEDS ATTENTION</b> — option snapshot failing for "
                                 f"{self._snapshot_outage_cycles} manage cycles; positions unmanaged.")
else:
    self._snapshot_outage_cycles = 0
```
In addition, when `dte <= 0` and `final_cycle` is true, handle an outage the same way as a counted miss.

### WR-02 (carried, deferred): Entry premium recorded at the pre-trade mid instead of the fill
**File:** `bot/options/service.py:826-833`
**Issue:** Unchanged. `credit_per_spread` and `max_loss_usd` come from chain mids, not fills. That biases the debit gate, BP headroom and realized P&L in the bot's favour. It also bounds the T-11-45 under-count on CR-02 rows.
**Fix:** After `open_position` returns a list, recompute the premium from `filled[*]["entry_price"]` before setting the row OPEN.

### WR-03 (carried, deferred): `daily_scan` read has no tie-break
**File:** `bot/options/universe.py:87`
**Issue:** Unchanged. `ORDER BY rank ASC LIMIT ?` is non-deterministic when premarket and intraday rows share a rank.
**Fix:** `ORDER BY rank ASC, gap_pct DESC, code ASC`, or filter on `scan_pass='premarket'`.

### WR-04 (carried, deferred): `legacy_view` runs before validation
**File:** `bot/options/config.py:514`; `backtester/options_run.py:229-230`
**Issue:** Unchanged. It can raise a raw `KeyError` that is not caught, and a per-strategy override is silently replaced by a global knob.
**Fix:** Validate through `load_options_book` before projecting, and catch `KeyError`/`TypeError` in `options_run`.

### WR-08 (carried, deferred): A partially closed short leg is recorded as `status='CLOSED'`
**File:** `bot/options/service.py:1147-1149`
**Issue:** Unchanged. `_on_exit_filled` writes CLOSED whatever `filled_qty` was.
**Fix:** Compare `filled_qty` with the leg qty, and record `PARTIAL` or the closed quantity.

### WR-09 (carried, deferred, priority raised): NEEDS_ATTENTION rows cannot be resolved and permanently use BP and slots
**File:** `bot/options/service.py:733-741`
**Issue:** Unchanged. 11-08 adds two more ways into NEEDS_ATTENTION (the CR-02 unwind and the WR-06 escalation). With `max_concurrent_positions: 4`, four such rows switch `super_bull_call` off with no alert.
**Fix:** Add an operator resolve command (`CLOSED`, `close_reason='manual'`, `realized_pnl_usd`, plus an audit entry), and log `options_entry_scan_skipped reason=concurrent_cap`.

## Info

### IN-08 (new): The near-expiry streak carries overnight, so the first 09:35 miss on expiry day can escalate at once
**File:** `bot/options/service.py:1085-1086, 390-397`
**Issue:** The streak is not tied to a session. Suppose a late start on the day before expiry records 2 misses (for example at 15:45 and 15:50). The first 09:35 miss on expiry day is then streak 3, and it escalates. That is the exact price-discovery gap WR-06 was meant to ride out. Reproduced in `test_repro_streak.py::test_streak_carries_overnight_and_first_0935_miss_escalates`: the alert text says "at 0 DTE after 3 manage cycle(s)". The error is toward the human (manual close instead of the automated close 5 minutes later), so it is Info.
**Fix:** Store `(date, streak)` and start the count over when the date changes, or pop every streak on the first cycle of a new session.

### IN-09 (new): The CR-02 NEEDS_ATTENTION record and alert do not say which legs are actually open
**File:** `bot/options/service.py:908-913`; `bot/options/execution.py:218-219`
**Issue:** The alert lists **every** leg code, including legs that never filled. The unwind calls `close_legs` with no callbacks, so its order ids and fills never reach the DB. `on_leg_filled` is also never called for the failing leg's partial fill, so that leg's row stays `WORKING` with no price. The operator's DB record therefore cannot tell them what to close. The `ponytail:` comment states that the broker is the source of truth for quantities.
**Fix:** Pass the `open_position_unwound` audit's order ids and per-leg filled quantities into the alert. Alternatively, return them together with False once WR-08 adds partial-quantity persistence.

### IN-10 (new): `options_manage_missing_quote` now also fires for present but wide or one-sided quotes
**File:** `bot/options/service.py:1126`
**Issue:** After WR-07, a leg rejected by `_quote_markable` for its width is logged with the same event name as a leg that has no quote at all. That makes it hard to tell a data problem from a liquidity problem during triage.
**Fix:** Add `reason="unmarkable"` or `"invalid"`, for example by logging `_quote_ok(q)` for each bad code.

### IN-11 (pre-existing, probe): The UAT probe closes against an unvalidated snapshot
**File:** `scripts/uat_options_probe.py:226, 238`
**Issue:** `q2` is built straight from the raw snapshot. A `'N/A'` bid or ask makes `fill_leg`'s `float(bid)` raise in the middle of `close_legs`. If the short has already been bought back, the script crashes with the long still held and the scratch row left `OPEN`, not `NEEDS_ATTENTION`.
**Fix:** Before the close, filter `q2` through `service._quote_ok` and fall back to `quotes`, or abort. Wrap `close_legs` in try/except and set NEEDS_ATTENTION on any exception.

### IN-01 (carried): The EOD HTML "Credit" column is negative for debit rows
**File:** `bot/options/service.py:312, 338`
**Fix:** Render `_premium_label(...)` and rename the header to "Premium".

### IN-02 (carried): `insert_option_position` silently defaults a missing `strategy_name` to tasty
**File:** `bot/options/store.py:59-73`
**Fix:** Raise `ValueError` for new inserts that have no strategy_name. The UAT probe insert at `uat_options_probe.py:184-190` also omits it.

### IN-03 (carried): `main.py` dispatch assumes the rules JSON is an object
**File:** `bot/main.py:80`
**Fix:** `if not isinstance(data, dict): print("[ERROR] ...", file=sys.stderr); sys.exit(1)`.

### IN-04 (carried): `equity_state_db` ignores `BOT_STATE_DB`
**File:** `bot/options/config.py`; `bot/options/universe.py:51`
**Fix:** Document the coupling, or log the resolved absolute path at startup.

### IN-05 (carried): The provenance doc does not list the 60% vs 40–50% profit-target deviation
**File:** `docs/research/2026-09-24-super-bull-call-spread.md`
**Fix:** Add a Deviations bullet.

### IN-06 (carried): A blocking sqlite read with a 5 s busy timeout runs on the event loop
**File:** `bot/options/universe.py:58`; `bot/options/service.py:687`
**Fix:** Lower `timeout_s` to about 0.5 s, or use `run_in_executor`.

### IN-07 (carried, now reached more often): A skipped position counts as $0 unrealized for the breaker
**File:** `bot/options/service.py:997-1006, 1126-1127`
**Issue:** Every WR-07 reject, every WR-06 retry and every outage skip now contribute 0.0 to `unrealized_total`. During a persistent outage (WR-11), the breaker sees only realized P&L.
**Fix:** Log `unquoted_positions=` on the breaker check, or reuse the last good mark within a staleness bound.

---

_Reviewed: 2026-09-24T20:15:39Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
