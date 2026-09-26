# Quick Task 260925-ho6: Fix equity engine cancel-swallow (CR-04 parity) - Context

**Gathered:** 2026-09-25
**Status:** Ready for planning

<domain>
## Task Boundary

`bot/execution/engine.py` swallows a failed `self._gw.cancel_order(order_id)` with
`except Exception: pass` at four sites, then carries on as if the order is dead:

| Site | Where (approx line) | What happens today after a swallowed cancel failure |
|------|---------------------|------------------------------------------------------|
| 1 | `_manage_entry_order`, partial-fill remainder cancel (~363) | returns `FillEvent(total_filled)` as final while the BUY remainder may still be working → position under-booked, extra shares fill later |
| 2 | `_manage_entry_order`, TTL expiry (~393) | places the NEXT, higher-priced BUY (second live order for the same intent), or on the last attempt marks the intent EXPIRED → late fill = untracked long with no stop |
| 3 | `manage_exit`, cancel before the post-cancel dealt_qty re-read (~571) | books the re-read qty as final and places the NEXT SELL for `remaining` while the old SELL remainder may still be working → over-sell into an untracked SHORT |
| 4 | `manage_exit`, TTL escalation round (~625) | places the NEXT, more aggressive SELL while the old one may still be working → SHORT |

`gateway.cancel_order` (bot/gateway/gateway.py:1150) raises `GatewayError` on any non-RET_OK
with no rate-limit retry. Paper account 1727266 is shared with the options bot, so a
transient modify_order rate-limit or OpenD timeout is realistic.

Reference fix (options bot, identical defect): quick task 260925-goi, commits 2cb1d0f + 7689f2b
on branch `claude/wonderful-knuth-7bbcb4` (`git show 2cb1d0f -- bot/options/execution.py`).
Rule carried over: when the cancel raises and the re-read dealt_qty is below the order's qty,
raise instead of placing the next attempt or returning a quantity; a fill that lands during the
cancel (dealt_qty >= qty) still returns normally.

</domain>

<decisions>
## Implementation Decisions (locked by orchestrator after tracing every caller)

### D1: Exception type
- New `CancelUnconfirmedError(RuntimeError)` defined in `bot/execution/engine.py`.
- Attributes: `code`, `order_id`, `filled_qty` (LOWER BOUND of shares confirmed filled across
  this engine call), `avg_price` (qty-weighted, 0.0 when none), `fill` (Optional[FillEvent],
  entry side only — the known-filled shares the caller must still book and protect).
- Do NOT chain from the live gateway exception instance: capture the error in the `except`,
  act outside it, and put `str(err)` in the message (or chain from a same-message copy as the
  options fix did). Reason: the cleanup retry (D3) can raise the very same instance again and
  implicit context chaining can build a cycle that hangs rich/structlog traceback rendering.

### D2: What "confirmed" means (only evaluated when cancel_order RAISED)
- Re-read the order once via `get_order_status(order_id)` (match row by order_id).
- Confirmed iff the re-read succeeded with a matching row AND
  (`dealt_qty >= that order's placed qty` OR `order_status` in the terminal set).
- Terminal set: hoist the literal set already in `consume_intent`
  (`FILLED_ALL, CANCELLED_ALL, CANCELLED_PART, FAILED, DELETED, EXPIRED`) to a module constant
  and reuse it in both places. The status comes free on the same row — no extra query.
- Failed re-read or no matching row → unconfirmed.
- When `cancel_order` SUCCEEDS: existing behaviour is unchanged at every site. No new
  get_order_status calls on the success path (order_list_query is 10/30s and shared with the
  options bot). Site 3 already re-reads on both paths — keep that.

### D3: Escalation (one shared engine helper, used by all four sites)
1. `append_audit({"event": "cancel_unconfirmed", "code", "order_id", "side": "BUY"|"SELL", "dealt_qty", "qty", "error": str(err)})`
2. `_logger.error("cancel_unconfirmed", ...)`
3. One best-effort cleanup retry of `cancel_order(order_id)`; on failure
   `append_audit({"event": "cancel_retry_failed", "code", "order_id", "error"})`.
   The retry does NOT change control flow (dealt may have moved since the re-read) —
   escalation continues regardless. (Parity with the options 11-09 shielded retry.)
4. Exit side only: `self._exit_hold[code] = str(order_id)` (see D5).
5. Telegram alert via a new optional `alerter` ctor arg: `ExecutionEngine(gateway, store, cfg, alerter=None)`.
   `await self._alerter.send(text)` inside try/except (ALERT-04: never break the trade loop).
   Text names code, side, order_id, dealt/qty, and the operator action: cancel the order in
   moomoo, verify the position, then restart the bot. HTML `<b>` tags OK (alerter parse mode).
6. Raise `CancelUnconfirmedError`.

### D4: Per-site behaviour
- **Site 1 (entry remainder cancel):** on cancel failure → re-read; build the FillEvent from the
  re-read dealt_qty/avg (fallback: the pre-cancel total_filled/avg if the re-read fails).
  Confirmed → return that FillEvent (definitive qty). Unconfirmed → escalate and raise with
  `fill=` that FillEvent. The `entry_fill_detected` audit/log is still written for the known shares.
- **Site 2 (entry TTL expiry):** on cancel failure → re-read. dealt>0 → FillEvent for dealt.
  Confirmed → return the FillEvent if dealt>0 (a fill that landed during the cancel — today it is
  silently dropped), else continue exactly as today (next attempt / abandon).
  Unconfirmed → escalate and raise with `fill=` FillEvent or None. Never place the next attempt,
  never call `_resolve_intent_expired`.
- **Site 3 (exit remainder cancel):** keep the existing post-cancel re-read; also read
  `order_status` from it. Cancel raised and not confirmed → credit what is known (existing
  total_filled/total_notional math — a lower bound), escalate, raise with
  `filled_qty=total_filled`, `avg_price=` blended. Never place the next round.
- **Site 4 (exit TTL escalation):** on cancel failure → re-read; credit any dealt>0 into
  total_filled/total_notional/remaining (+ `exit_fill_detected` audit). Confirmed → continue
  exactly as today (break when remaining<=0, else escalate price and place the next round).
  Unconfirmed → escalate, raise with `filled_qty=total_filled`, blended avg. Never place the next round.

### D5: Exit hold (the single choke point for every automatic SELL)
- All equity-bot SELLs route through `engine.manage_exit`: bar stop-out, partial, quote-tick stop
  (all via `PositionManager._place_exit_order`) and EOD `force_close_all` (direct call).
- At the top of `manage_exit`: if `code in self._exit_hold` → place nothing,
  `append_audit({"event": "exit_blocked_cancel_unconfirmed", "code", "order_id"})`, log warning,
  raise `CancelUnconfirmedError(filled_qty=0, ...)` (no second Telegram alert).
- In-memory until restart. Needs a `ponytail:` comment naming the ceiling (lost on restart;
  restart implies operator involvement and startup_reconcile re-derives from broker truth) and
  the upgrade path (persist + auto-release once the held order is terminal AND reconcile has
  re-synced qty).
- No entry-side hold: an unconfirmed entry leaves the intent PENDING (D6), and the D-10
  re-entry gate (`signal_engine.has_pending_intent`) plus the EXEC-04 open-BUY guard already
  block a second BUY for that code. Holding exits after an ENTRY failure would be wrong (the
  booked shares must stay exitable).

### D6: Callers
- `bot/service/bot.py` `_process_bar`: wrap `consume_intent`. On `CancelUnconfirmedError` →
  `fill = exc.fill`. If fill is not None the existing register_position / on_fill /
  arm_stop_protection path runs unchanged, so the known shares are booked and protected.
  If fill is None → do NOT `resolve_pending_intent(..., "ABANDONED")`: leave the intent PENDING.
  Reasons: orphan adoption's SAFE-OG-01 guard (gateway.py ~1557) only adopts a code with
  `store.has_pending_intent(code)` or an open DB position, so an ABANDONED intent would turn a
  late fill of the stray BUY into an unmanaged, stop-less long; PENDING also keeps the D-10
  re-entry gate closed. `note_intent_resolved()` is still called as today.
- `PositionManager._place_exit_order` (bot/position/manager.py ~1266): add
  `except CancelUnconfirmedError as exc: return int(exc.filled_qty), float(exc.avg_price)`
  before the generic `except Exception`. This credits confirmed-sold shares (a lower bound, so
  remaining is never understated and a later SELL cannot over-sell). The engine hold stops any
  further SELL. The existing stop_out_incomplete / partial_short_fill handling then applies.
- `force_close_all`: no change (its generic except audits force_close_stuck; the engine already
  alerted and holds).
- `bot/main.py`: construct the alerter before the engine and pass `alerter=alerter`.
- Import direction: bot.py and manager.py import `CancelUnconfirmedError` from
  `bot.execution.engine` (manager already takes the engine; check for import cycles).

### Claude's Discretion
- Helper names/signatures and how the four sites share the re-read + escalate code, as long as
  the success path stays byte-for-byte equivalent in behaviour and the diff stays minimal.

</decisions>

<specifics>
## Regression tests (write FIRST, run them red against current engine.py, then fix)

Core property: a gateway whose `cancel_order` raises once while the order is unfilled must never
produce a second live order for the same intent. Use
`cancel_order = AsyncMock(side_effect=[GatewayError("rate limit"), None])` (first cancel raises,
cleanup retry succeeds) and assert `place_order.await_count == 1`.

- Entry TTL: order row SUBMITTED dealt 0 on every read → `_manage_entry_order` raises
  CancelUnconfirmedError, place_order awaited once, `store.expire_pending_intent` not called,
  `exc.fill is None`, `cancel_unconfirmed` audited (patch `bot.execution.engine.append_audit`).
- Entry partial: FILLED_PART dealt 40/100, cancel raises, re-read still 40 FILLED_PART → raises
  with `exc.fill.filled_qty == 40`; place_order once.
- Exit TTL: SUBMITTED dealt 0, cancel raises once → `manage_exit` raises; place_order once; a
  second `manage_exit` for the same code places nothing and raises (hold).
- Exit partial: FILLED_PART 40/100, cancel raises, re-read 40 → raises with `filled_qty == 40`;
  place_order once.
- Preservation: cancel raises but re-read FILLED_ALL (dealt == qty) → entry returns FillEvent for
  the full qty; exit returns `(qty, avg)`. Entry TTL with cancel raising and re-read
  CANCELLED_ALL dealt 0 → proceeds to the next attempt exactly as today.
- Alerter: engine built with an alerter mock → `send` awaited once, text contains the order_id.
- bot.py: consume_intent raising with a fill → register_position + on_fill + arm_stop_protection
  called; without a fill → `resolve_pending_intent` NOT called with "ABANDONED";
  `note_intent_resolved` called in both.
- PositionManager: engine.manage_exit raising CancelUnconfirmedError(filled_qty=40, avg_price=x)
  → `_place_exit_order` returns `(40, x)`.
- All existing successful-cancel tests stay green unchanged.

Do not use bare `git stash` (shared stash stack across worktrees). Prove red by running the new
tests before editing engine.py.

</specifics>

<canonical_refs>
## Canonical References

- Options reference fix: `git show 2cb1d0f` and `git show 7689f2b` (branch claude/wonderful-knuth-7bbcb4)
- Options review finding: `.planning/phases/11-multi-strategy-options-bot-bull-call-spread/11-REVIEW.md` § CR-04 on that branch (`git show claude/wonderful-knuth-7bbcb4:.planning/phases/11-multi-strategy-options-bot-bull-call-spread/11-REVIEW.md`)
- Orphan-adoption ownership guard: `bot/gateway/gateway.py` `_reconcile_core` (~1534-1580)
- Re-entry gate: `bot/signal/signal_engine.py` `has_pending_intent` (~394)

## Out of scope (record in SUMMARY as follow-ups, do not fix)
- Success-path race: a fill landing between the last poll and a SUCCESSFUL TTL cancel is never
  re-read at sites 2/4 (entry: untracked partial + a re-placed full-size BUY; exit: next SELL
  sized without it → short). Pre-existing; the task says keep successful-cancel behaviour.
- `PositionManager._sync_broker_stop` swallows a failed stop cancel then places a new stop (two
  live stops → double SELL). Inactive while rules.json `use_broker_stop_orders` is false.

</canonical_refs>
