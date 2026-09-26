#!/usr/bin/env python3
"""
bot.execution.engine — ExecutionEngine: OrderIntent → broker orders → FillEvent.

Turns a verified OrderIntent (from RiskEngine, persisted in pending_intents) into
real (paper) orders on the SIMULATE account via MoomooGateway:

  - consume_intent(intent): price entry at ask+buffer, place marketable-limit order,
    run the TTL poll loop, cancel-replace on TTL up to cfg.entry_max_retries, then
    abandon (D-05). Returns FillEvent on any fill (full or partial — D-06), or None.

  - _manage_entry_order(intent): inner async coroutine implementing the TTL poll loop.

  - manage_exit(code, qty, side, ...): marketable-limit exit, retry-until-flat with
    escalating prices until order_id-matched filled qty == requested qty (D-07/EXEC-02).

Safety invariants (never violated):
  - SIMULATE only (EXEC-01): cfg.trd_env is always SIMULATE; gateway enforces it.
  - NORMAL order type only (EXEC-02): only gateway.place_order() touches OrderType;
    this module never submits a market order type.
  - order_id reconciliation (EXEC-05): fills matched by order_id, never by code/qty.
  - Bounded retries (D-05): entry bounded by cfg.entry_max_retries; exit bounded by
    remaining_quantity reaching 0.
  - Partial entry accepted as position (D-06): cancel remainder, emit FillEvent.
  - Unconfirmed cancel (CR-04 parity): if cancel_order raises and a re-read does
    not show the order filled or terminal, escalate and raise CancelUnconfirmedError
    — never place the next order or book a still-growing qty as final. The
    TTL-cancel sites also re-read after a SUCCESSFUL cancel, so a partial fill in
    the cancel window is booked, and a failed re-read escalates (260925-inw).

Usage:
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)
    fill_event = await engine._manage_entry_order(intent)
    if fill_event:
        # pass to PositionManager
        ...
"""
import asyncio
from typing import Optional

from bot.execution.events import FillEvent
from bot.gateway.gateway import GatewayError
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

_logger = get_logger(__name__)

# Finding 2.4: bounded retry for a transient bid/ask snapshot failure before
# falling back to the last known good price (never a $0/negative exit limit).
_PRICE_FETCH_RETRIES = 2
_PRICE_FETCH_RETRY_DELAY_S = 1.0

# CR-04 (quick 260925-ho6): terminal order_status values used both by
# consume_intent's duplicate-order guard and by _cancel_confirmed below —
# hoisted to a single module constant so the two checks can never drift.
_TERMINAL_ORDER_STATUSES = frozenset({
    "FILLED_ALL", "CANCELLED_ALL", "CANCELLED_PART", "FAILED", "DELETED", "EXPIRED",
})


class CancelUnconfirmedError(RuntimeError):
    """Raised when cancel_order fails AND a re-read cannot confirm the order
    is dead (filled to its placed qty, or in a terminal status).

    filled_qty is a LOWER BOUND of shares confirmed filled across the engine
    call that raised (the order may still be working at the broker). fill is
    populated only for entry sites — the FillEvent the caller must still book
    and protect; it is always None for exit sites.
    """

    def __init__(self, message, *, code, order_id, filled_qty=0, avg_price=0.0, fill=None):
        super().__init__(message)
        self.code = code
        self.order_id = order_id
        self.filled_qty = filled_qty
        self.avg_price = avg_price
        self.fill = fill


def _cancel_confirmed(row: Optional[dict], placed_qty: int) -> bool:
    """True iff a cancel_order failure is nonetheless confirmed dead by a re-read.

    Confirmed iff row is not None AND (dealt_qty >= placed_qty for this order,
    OR order_status is terminal). A failed re-read (row is None) is always
    unconfirmed (CR-04 D2).
    """
    if row is None:
        return False
    dealt_qty = int(row.get("dealt_qty", 0) or 0)
    order_status = str(row.get("order_status", "") or "")
    return dealt_qty >= int(placed_qty) or order_status in _TERMINAL_ORDER_STATUSES


# ============================================================
# Deferred TrdSide resolver (mirrors subscribe() deferred import pattern)
# ============================================================
# Module-level sentinels resolved lazily — allows `import bot.execution.engine`
# to succeed in environments where moomoo-api is not installed (test env).

_TrdSide_BUY = None
_TrdSide_SELL = None


def _get_trd_side_buy():
    """Lazily import and return TrdSide.BUY from the moomoo SDK."""
    global _TrdSide_BUY
    if _TrdSide_BUY is None:
        from moomoo import TrdSide
        _TrdSide_BUY = TrdSide.BUY
    return _TrdSide_BUY


def _get_trd_side_sell():
    """Lazily import and return TrdSide.SELL from the moomoo SDK."""
    global _TrdSide_SELL
    if _TrdSide_SELL is None:
        from moomoo import TrdSide
        _TrdSide_SELL = TrdSide.SELL
    return _TrdSide_SELL


# ============================================================
# ExecutionEngine
# ============================================================

class ExecutionEngine:
    """Translates OrderIntents into broker orders and emits FillEvents (Phase 4).

    Relies on MoomooGateway for all broker I/O; uses StateStore only to persist
    pending_intent status (EXPIRED on abandon — D-05). The engine never touches
    PositionState directly; the emitted FillEvent is consumed upstream by
    PositionManager.

    All numeric tunables come from cfg (StrategyConfig, sourced from rules.json)
    — no TTL/buffer/retry literals in this module (CFG-01).
    """

    def __init__(self, gateway, store, cfg, alerter=None) -> None:
        """Initialise ExecutionEngine.

        Args:
            gateway: MoomooGateway instance (must be connected before use).
            store:   StateStore instance (open; conn accessible).
            cfg:     StrategyConfig with execution tunables (CFG-01).
            alerter: Optional TelegramAlerter (CR-04 D3 step 5). None by
                     default — existing 3-arg constructions keep working.
                     Used only to notify the operator of an unconfirmed
                     cancel (ALERT-04: failure never breaks the trade loop).
        """
        self._gw = gateway
        self._store = store
        self._cfg = cfg
        self._alerter = alerter
        # ponytail: in-memory exit hold, lost on restart (restart implies
        # operator involvement and startup_reconcile re-derives qty from
        # broker truth); upgrade path: persist the hold and auto-release once
        # the held order is terminal AND reconcile has re-synced qty.
        self._exit_hold: dict = {}

    def _entry_chase_limit_exceeded(self, intent, limit_price: float) -> bool:
        """P2 (strategy-audit finding): True when limit_price has chased beyond
        cfg.max_entry_chase_r * (entry_price - stop_price) above the signal's
        own entry_price. cfg.max_entry_chase_r is None by default (unbounded
        chase, today's behavior) -- the guard is a no-op unless explicitly
        configured. Live-only: the backtester's N+1-open fill model has no
        re-quote loop to bound, so this is never consulted offline.
        """
        max_chase_r = getattr(self._cfg, "max_entry_chase_r", None)
        if max_chase_r is None:
            return False
        stop_distance = intent.entry_price - intent.stop_price
        if stop_distance <= 0:
            return False  # defensive -- RiskEngine already rejects this upstream
        cap = intent.entry_price + max_chase_r * stop_distance
        return limit_price > cap

    async def _get_price_with_fallback(
        self, code: str, side: str, fallback: Optional[float] = None,
    ) -> float:
        """Fetch bid/ask price with a bounded retry (Finding 2.4).

        gateway.get_bid_price/get_ask_price now raise GatewayError instead of
        returning 0.0 on a snapshot failure. Retries up to _PRICE_FETCH_RETRIES
        times with a short delay; if still failing and `fallback` (the last
        known good price for this exit loop) is available, uses it rather than
        ever computing a limit price from 0.0. Re-raises GatewayError only when
        both the retries and the fallback are exhausted (never silently prices
        an order at $0 — an explicit raise is the caller's signal to abandon).

        Args:
            code:     Moomoo-format stock code.
            side:     "bid" or "ask".
            fallback: Last known good price, or None if none is available yet.
        """
        getter = self._gw.get_bid_price if side == "bid" else self._gw.get_ask_price
        last_exc: Optional[GatewayError] = None
        for attempt in range(_PRICE_FETCH_RETRIES):
            try:
                return await getter(code)
            except GatewayError as exc:
                last_exc = exc
                _logger.warning(
                    "price_fetch_retry", code=code, side=side, attempt=attempt, error=str(exc),
                )
                if attempt < _PRICE_FETCH_RETRIES - 1:
                    await asyncio.sleep(_PRICE_FETCH_RETRY_DELAY_S)
        if fallback is not None:
            _logger.warning(
                "price_fetch_fallback_last_known", code=code, side=side, fallback=fallback,
            )
            return fallback
        raise last_exc

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def consume_intent(self, intent) -> Optional[FillEvent]:
        """Consume an OrderIntent: broker-verified duplicate guard then entry order.

        EXEC-04 broker-verified duplicate guard runs AT THE TOP of this method,
        BEFORE place_order is ever called. This is NOT an in-memory-only check —
        it uses get_positions(refresh_cache=True) and get_order_status() to verify
        broker state directly (Pitfall F — crash-between-place-and-persist).

        Guard protocol (EXEC-04):
          1. get_positions(refresh_cache=True) — if the broker reports an open
             position for intent.code, block the entry and return None.
          2. get_order_status() — if any open BUY order exists for intent.code,
             block the entry and return None (Pitfall F: crash-between-place-and-persist).
          Both checks are mandatory; either alone is insufficient (Pitfall F).

        Args:
            intent: OrderIntent from RiskEngine (code, quantity, entry_price,
                    stop_price, intent_id).

        Returns:
            FillEvent on any fill (full or partial, D-06), or None if:
              - The code is under a CR-04 exit hold (D5), OR
              - Duplicate detected by broker-verified guard (EXEC-04), OR
              - Abandoned after exceeding entry_max_retries (D-05).

        Raises:
            CancelUnconfirmedError: CR-04 parity — propagated from
                _manage_entry_order when a cancel_order failure cannot be
                confirmed dead by a re-read.
        """
        # CR-04 D5: manage_exit refuses every SELL for a held code until restart,
        # so a new position here could never be stopped out or force-closed.
        held = self._exit_hold.get(intent.code)
        if held is not None:
            _logger.warning(
                "entry_blocked_cancel_unconfirmed",
                code=intent.code,
                intent_id=intent.intent_id,
                order_id=held,
            )
            append_audit({
                "event": "entry_blocked_cancel_unconfirmed",
                "code": intent.code,
                "intent_id": intent.intent_id,
                "order_id": held,
            })
            return None

        # ---- EXEC-04: Broker-verified duplicate guard (BEFORE any place_order) ----
        # Check 1: Broker has an open position for this code → block
        try:
            ret, broker_data = await self._gw.get_positions(refresh_cache=True)
            if ret == 0 and broker_data is not None and len(broker_data) > 0:
                broker_codes = set()
                for _, row in broker_data.iterrows():
                    code_val = str(row.get("code", "") or "")
                    if code_val:
                        broker_codes.add(code_val)
                if intent.code in broker_codes:
                    _logger.warning(
                        "duplicate_entry_blocked_open_position",
                        code=intent.code,
                        intent_id=intent.intent_id,
                    )
                    append_audit({
                        "event": "duplicate_entry_blocked",
                        "reason": "open_broker_position",
                        "code": intent.code,
                        "intent_id": intent.intent_id,
                    })
                    return None
        except Exception:
            # On error, allow through — block only on confirmed duplicate (fail open)
            _logger.warning(
                "duplicate_guard_positions_check_failed",
                code=intent.code,
                exc_info=True,
            )

        # Check 2: Open BUY order for this code → block (Pitfall F)
        try:
            open_orders = await self._gw.get_order_status()
            for order in open_orders:
                order_code = str(order.get("code", "") or "")
                order_status = str(order.get("order_status", "") or "")
                order_side = str(order.get("trd_side", "") or "")
                # Order is "open" if not in a terminal status
                is_terminal = order_status in _TERMINAL_ORDER_STATUSES
                is_buy = "BUY" in order_side.upper() or order_side == "0"
                if (
                    order_code == intent.code
                    and is_buy
                    and not is_terminal
                ):
                    _logger.warning(
                        "duplicate_entry_blocked_open_buy_order",
                        code=intent.code,
                        intent_id=intent.intent_id,
                        order_status=order_status,
                    )
                    append_audit({
                        "event": "duplicate_entry_blocked",
                        "reason": "open_buy_order",
                        "code": intent.code,
                        "intent_id": intent.intent_id,
                    })
                    return None
        except Exception:
            # On error, allow through (fail open — prefer miss over false block)
            _logger.warning(
                "duplicate_guard_order_status_check_failed",
                code=intent.code,
                exc_info=True,
            )

        # ---- Guard passed — proceed to place the entry order ----
        return await self._manage_entry_order(intent)

    # --------------------------------------------------------
    # CR-04 parity — shared cancel-unconfirmed helpers (quick 260925-ho6)
    # --------------------------------------------------------

    async def _reread_order(self, order_id) -> Optional[dict]:
        """Re-read one order's status after a failed cancel_order, AND after a
        successful TTL cancel (sites 2/4, 260925-inw) (CR-04 D2).

        Returns the first row whose order_id matches, or None on a failed
        re-read (get_order_status raising) or no matching row -- both count
        as "unconfirmed" to the caller.
        """
        try:
            rows = await self._gw.get_order_status(order_id)
        except Exception as exc:
            _logger.warning("cancel_reread_failed", order_id=order_id, error=str(exc))
            return None
        for row in rows:
            if str(row.get("order_id", "")) == str(order_id):
                return row
        return None

    def _emit_entry_fill(self, intent, order_id, filled_qty, avg_price) -> FillEvent:
        """Build the entry FillEvent and write the entry_fill_detected audit/log.

        Extracted unchanged from the original site-1 inline block so every
        entry-fill emission (site 1 and site 2's CR-04 fill-during-cancel
        case) goes through one path.
        """
        fill_event = FillEvent(
            order_id=str(order_id),
            intent_id=intent.intent_id,
            code=intent.code,
            filled_qty=int(filled_qty),
            avg_fill_price=float(avg_price),
            is_entry=True,
            fill_time=now_et(),
        )
        append_audit({
            "event": "entry_fill_detected",
            "order_id": order_id,
            "filled_qty": int(filled_qty),
            "avg_fill_price": float(avg_price),
            "intent_id": intent.intent_id,
        })
        _logger.info(
            "entry_fill_detected",
            order_id=order_id,
            filled_qty=int(filled_qty),
            avg_fill_price=float(avg_price),
        )
        return fill_event

    async def _escalate_unconfirmed_cancel(
        self, err, *, code, order_id, side, dealt_qty, qty, filled_qty, avg_price, fill=None,
    ):
        """Shared CR-04 D3 escalation for all four cancel-swallow sites.

        Steps (D3, always in order): audit cancel_unconfirmed, log error, one
        best-effort cleanup cancel_order retry (never changes control flow),
        exit-side hold (SELL only, D5), best-effort Telegram alert (ALERT-04),
        then always raise CancelUnconfirmedError.

        D1: the raise below sits outside every except block (including the
        cleanup retry's) so it never implicitly chains from the live gateway
        exception -- callers must call this AFTER their own try/except has
        already exited (never from inside it).
        """
        append_audit({
            "event": "cancel_unconfirmed",
            "code": code,
            "order_id": order_id,
            "side": side,
            "dealt_qty": dealt_qty,
            "qty": qty,
            "error": str(err),
        })
        _logger.error(
            "cancel_unconfirmed",
            code=code,
            order_id=order_id,
            side=side,
            dealt_qty=dealt_qty,
            qty=qty,
            error=str(err),
        )

        # Step 3: one best-effort cleanup retry -- dealt may have moved since
        # the re-read, but this never changes control flow either way.
        try:
            await self._gw.cancel_order(order_id)
        except Exception as retry_err:
            append_audit({
                "event": "cancel_retry_failed",
                "code": code,
                "order_id": order_id,
                "error": str(retry_err),
            })

        # Step 4: exit-side hold -- blocks every later manage_exit for this code.
        if side == "SELL":
            self._exit_hold[code] = str(order_id)

        # Step 5: best-effort Telegram alert (ALERT-04 -- never breaks the loop).
        # str(err) is deliberately excluded from the alert text: a broker error
        # string can contain '<' or '&', which breaks parse_mode HTML. The
        # error is already captured in the audit entry and the log above.
        if self._alerter is not None:
            try:
                await self._alerter.send(
                    f"<b>CANCEL UNCONFIRMED</b>: {code} {side} order {order_id} "
                    f"dealt {dealt_qty}/{qty}. Cancel the order in moomoo, verify "
                    f"the position, then restart the bot."
                )
            except Exception:
                _logger.warning(
                    "cancel_unconfirmed_alert_failed", code=code, order_id=order_id,
                )

        # Step 6: always raise.
        raise CancelUnconfirmedError(
            f"cancel of {order_id} unconfirmed: {code} {side} dealt {dealt_qty}/{qty}: {err}",
            code=code, order_id=order_id, filled_qty=filled_qty, avg_price=avg_price, fill=fill,
        )

    async def _manage_entry_order(self, intent) -> Optional[FillEvent]:
        """Place a marketable-limit entry; poll order_list_query for fills by order_id.

        Protocol (D-04/D-05/D-06/EXEC-03/EXEC-05):
          1. Price entry at ask + cfg.entry_limit_buffer_usd (D-04).
          2. Place via gateway.place_order (OrderType.NORMAL — EXEC-02 enforced by gateway).
          3. Poll every cfg.entry_poll_interval_seconds until cfg.entry_ttl_seconds.
          4. Match fills by order_id only (EXEC-05) via get_order_status(order_id) which
             returns cumulative dealt_qty/dealt_avg_price from order_list_query.
             NOTE: deal_list_query is NOT used here because it is unsupported on SIMULATE
             paper accounts (Futu returns ret=-1 "Paper trading does not support deal data.").
             order_list_query returns per-order cumulative dealt_qty, which is correct for
             fill detection (EXEC-05). On first poll that shows dealt_qty > 0, we cancel the
             remainder and emit FillEvent — no cross-round double-count risk because we
             return immediately on the first partial or full fill (D-06).
          5. On any fill (dealt_qty > 0): cancel remainder (D-06), emit FillEvent(is_entry=True).
          6. On TTL with no fill: cancel, re-price at fresh ask+buffer, re-place.
             Repeat up to cfg.entry_max_retries times (D-05).
          7. After exceeding the cap: cancel, mark pending_intent EXPIRED, return None.

        Args:
            intent: OrderIntent (code, quantity, intent_id).

        Returns:
            FillEvent(is_entry=True) on fill, or None on abandon.

        Raises:
            CancelUnconfirmedError: CR-04 parity (sites 1/2) -- cancel_order
                raised and a re-read cannot confirm the order is dead, or a
                successful TTL cancel whose re-read fails (260925-inw).
        """
        # D-04: price at/through current ask + buffer
        try:
            ask_price = await self._get_price_with_fallback(intent.code, "ask")
        except GatewayError:
            # Finding 2.4: no order has been placed yet — abandon this intent
            # rather than propagate an unhandled exception into the bar-processing
            # loop (D-05 abandon semantics, same as an exhausted-retries abandon).
            self._resolve_intent_expired(intent.intent_id)
            _logger.warning(
                "entry_price_fetch_failed_abandoning", code=intent.code, intent_id=intent.intent_id,
            )
            return None
        limit_price = round(ask_price + self._cfg.entry_limit_buffer_usd, 4)

        # P2 (strategy-audit finding): never place at a price the signal never
        # justified, even on the very first placement.
        if self._entry_chase_limit_exceeded(intent, limit_price):
            self._resolve_intent_expired(intent.intent_id)
            _logger.warning(
                "entry_chase_limit_exceeded",
                code=intent.code,
                intent_id=intent.intent_id,
                limit_price=limit_price,
                max_entry_chase_r=self._cfg.max_entry_chase_r,
            )
            return None

        trd_side_buy = _get_trd_side_buy()
        order_id = await self._gw.place_order(
            intent.code, intent.quantity, limit_price, trd_side_buy
        )
        append_audit({
            "event": "entry_order_placed",
            "code": intent.code,
            "order_id": order_id,
            "price": limit_price,
            "qty": intent.quantity,
            "intent_id": intent.intent_id,
        })
        _logger.info(
            "entry_order_placed",
            code=intent.code,
            order_id=order_id,
            price=limit_price,
            qty=intent.quantity,
        )

        loop = asyncio.get_event_loop()

        for attempt in range(self._cfg.entry_max_retries + 1):
            deadline = loop.time() + self._cfg.entry_ttl_seconds

            while loop.time() < deadline:
                await asyncio.sleep(self._cfg.entry_poll_interval_seconds)

                # Poll via order_list_query (get_order_status) — cumulative dealt_qty.
                # deal_list_query (get_order_fills) is NOT used: it is unsupported on
                # SIMULATE paper accounts ("Paper trading does not support deal data.").
                # get_order_status(order_id) returns ONE row per order with cumulative
                # dealt_qty and dealt_avg_price — sufficient for EXEC-05 fill matching.
                # On first dealt_qty > 0 we cancel the remainder and return immediately
                # so there is no cross-round double-count risk (D-06).
                order_rows = await self._gw.get_order_status(order_id)
                matched = [
                    r for r in order_rows
                    if str(r.get("order_id", "")) == str(order_id)
                ]

                if matched:
                    row = matched[0]
                    total_filled = int(row.get("dealt_qty", 0) or 0)
                    avg_fill_price = float(row.get("dealt_avg_price", 0.0) or 0.0)

                    if total_filled > 0:
                        # D-06: cancel unfilled remainder, accept partial fill as position
                        cancel_err = None
                        try:
                            await self._gw.cancel_order(order_id)
                        except Exception as exc:
                            # CR-04 site 1: cancel failed -- confirm via re-read
                            # before trusting this snapshot as final; the
                            # remainder may already be fully filled, OR may
                            # still be working at the broker.
                            cancel_err = exc

                        row = None
                        if cancel_err is not None:
                            row = await self._reread_order(order_id)
                            if row is not None:
                                total_filled = int(row.get("dealt_qty", 0) or 0)
                                avg_fill_price = float(row.get("dealt_avg_price", 0.0) or 0.0)
                            # else: keep the pre-cancel total_filled/avg_fill_price

                        fill_event = self._emit_entry_fill(
                            intent, order_id, total_filled, avg_fill_price,
                        )

                        if cancel_err is not None and not _cancel_confirmed(row, intent.quantity):
                            await self._escalate_unconfirmed_cancel(
                                cancel_err, code=intent.code, order_id=order_id, side="BUY",
                                dealt_qty=total_filled, qty=intent.quantity,
                                filled_qty=total_filled, avg_price=avg_fill_price, fill=fill_event,
                            )
                        return fill_event

            # TTL expired for this attempt — cancel current order
            cancel_err = None
            try:
                await self._gw.cancel_order(order_id)
            except Exception as exc:
                cancel_err = exc

            fill_event = None
            # CR-04 site 2 (+ quick 260925-inw): re-read after EVERY TTL
            # cancel, not only a failed one.
            #   (a) A partial fill can land between the last poll and the
            #       cancel (CANCELLED_PART, dealt_qty > 0) even when
            #       cancel_order itself reports success -- previously
            #       dropped under a re-placed full-size BUY.
            #   (b) If the re-read fails on the success path too (raises, or
            #       no matching row), the cancel is confirmed but the filled
            #       qty is unknown, so treat it as unconfirmed (ho6 D2).
            #       order_list_query returns today's cancelled orders, so an
            #       empty result is anomalous.
            #   (c) The success path deliberately skips the _cancel_confirmed
            #       status check -- a successful cancel is trusted, and
            #       escalating on a transient non-terminal status would halt
            #       every normal re-price.
            #   (d) Rate budget: this adds ONE order_list_query per TTL
            #       expiry. The gateway cap is 10 calls/30s (RATE-01 retries
            #       on the cap); paper account 1727266 is shared with the
            #       options bot. With rules.json today (entry poll 5s over a
            #       20s TTL) that is about 4 polls + 1 re-read per attempt.
            row = await self._reread_order(order_id)
            dealt = int(row.get("dealt_qty", 0) or 0) if row is not None else 0
            row_avg = float(row.get("dealt_avg_price", 0.0) or 0.0) if row is not None else 0.0
            if dealt > 0:
                fill_event = self._emit_entry_fill(intent, order_id, dealt, row_avg)
            if row is None or (cancel_err is not None and not _cancel_confirmed(row, intent.quantity)):
                # Unconfirmed -- never place the next attempt or abandon.
                await self._escalate_unconfirmed_cancel(
                    cancel_err or "post-cancel re-read failed",
                    code=intent.code, order_id=order_id, side="BUY",
                    dealt_qty=dealt, qty=intent.quantity,
                    filled_qty=dealt, avg_price=row_avg, fill=fill_event,
                )
            if fill_event is not None:
                # Confirmed cancel, but a fill landed during the cancel
                # window -- previously silently dropped (a second BUY was
                # re-placed on top of it).
                return fill_event

            _logger.info("entry_ttl_expired", order_id=order_id, attempt=attempt)

            if attempt < self._cfg.entry_max_retries:
                # Re-price at fresh ask + buffer and re-place (EXEC-03). Finding
                # 2.4: fall back to the last known ask_price on a transient
                # snapshot failure rather than ever pricing off 0.0.
                ask_price = await self._get_price_with_fallback(
                    intent.code, "ask", fallback=ask_price,
                )
                limit_price = round(ask_price + self._cfg.entry_limit_buffer_usd, 4)

                # P2 (strategy-audit finding): abandon rather than chase the
                # price past the configured cap on a re-price too.
                if self._entry_chase_limit_exceeded(intent, limit_price):
                    self._resolve_intent_expired(intent.intent_id)
                    _logger.warning(
                        "entry_chase_limit_exceeded",
                        code=intent.code,
                        intent_id=intent.intent_id,
                        limit_price=limit_price,
                        max_entry_chase_r=self._cfg.max_entry_chase_r,
                    )
                    return None

                order_id = await self._gw.place_order(
                    intent.code, intent.quantity, limit_price, trd_side_buy
                )
                append_audit({
                    "event": "entry_order_repriced",
                    "code": intent.code,
                    "order_id": order_id,
                    "price": limit_price,
                    "attempt": attempt + 1,
                    "intent_id": intent.intent_id,
                })
                _logger.info(
                    "entry_order_repriced",
                    code=intent.code,
                    order_id=order_id,
                    attempt=attempt + 1,
                )
            else:
                # D-05: exceeded entry_max_retries — abandon, resolve as EXPIRED
                self._resolve_intent_expired(intent.intent_id)
                _logger.warning(
                    "entry_abandoned",
                    code=intent.code,
                    intent_id=intent.intent_id,
                    retries=self._cfg.entry_max_retries,
                )
                return None

        # Should not reach here, but guard for safety
        self._resolve_intent_expired(intent.intent_id)
        return None

    async def manage_exit(
        self,
        code: str,
        qty: int,
        side,
        escalation_step: float,
        escalation_cadence: float,
        ttl: float,
    ) -> tuple:
        """Place a marketable-limit exit; escalate until fully flat (D-07/EXEC-02).

        Prices the exit through the bid (bid - cfg.exit_limit_buffer_usd). If
        unfilled within ttl seconds, cancel-replaces at a progressively more
        aggressive price (subtract escalation_step each round) until the
        order_id-matched cumulative filled qty equals the requested qty
        (retry-until-flat). A partial exit fill does NOT stop the loop — the
        escalation continues until remaining_quantity == 0 (Pitfall E).

        NEVER uses a market order (EXEC-02). All order types via gateway.place_order
        which enforces OrderType.NORMAL.

        Args:
            code:               Moomoo-format code (e.g. "US.AAPL").
            qty:                Total shares to exit.
            side:               TrdSide.SELL (or caller-supplied side sentinel).
            escalation_step:    USD amount to subtract from limit price each retry.
            escalation_cadence: Seconds between escalation attempts within a TTL.
            ttl:                Seconds before each cancel-replace cycle.

        Returns:
            (total_filled, avg_price) — total_filled is the cumulative filled
            quantity across all exit order_ids; avg_price is the qty-weighted
            average fill price across every leg (P1-B: lets the caller record
            one blended trades-table row instead of discarding fill prices).
            avg_price is 0.0 when total_filled is 0 (no fill occurred).

        Fill detection uses get_order_status(order_id) → order_list_query (cumulative
        dealt_qty per order) instead of get_order_fills() → deal_list_query. The latter
        is unsupported on SIMULATE paper accounts ("Paper trading does not support deal
        data."). Each outer while iteration places a NEW order_id; within the inner poll
        loop for a given order_id, dealt_qty is cumulative for that order — assigning
        order_filled_this_round = dealt_qty is correct because we break on the first
        non-zero read (no cross-round double-count for the same order_id). Across
        outer iterations total_filled accumulates the per-order dealt_qty values,
        which are independent (different order_ids). This preserves the EXEC-05 /
        CR-02 quantity-tracking invariants (remaining decrements once per order_id fill).

        Raises:
            CancelUnconfirmedError: CR-04 parity (sites 3/4) -- cancel_order
                raised and a re-read cannot confirm the order is dead, or a
                successful TTL cancel whose re-read fails (260925-inw). Also
                raised immediately (D5) when `code` is currently held after an
                earlier unconfirmed cancel -- no order is placed in that case.
        """
        # D5: exit hold -- an earlier unconfirmed cancel for this code blocks
        # every later manage_exit call until restart (the single choke point
        # for every automatic SELL: bar stop-out, partial, quote-tick stop,
        # and EOD force_close_all all route through this method).
        held = self._exit_hold.get(code)
        if held is not None:
            append_audit({
                "event": "exit_blocked_cancel_unconfirmed",
                "code": code,
                "order_id": held,
            })
            _logger.warning("exit_blocked_cancel_unconfirmed", code=code, order_id=held)
            raise CancelUnconfirmedError(
                f"exit for {code} blocked -- unconfirmed cancel held on order {held}",
                code=code, order_id=held, filled_qty=0, avg_price=0.0,
            )

        total_filled = 0
        total_notional = 0.0  # P1-B: qty-weighted price accumulator across legs
        remaining = qty
        # Price through bid with buffer (D-07). Finding 2.4: bounded retry inside
        # _get_price_with_fallback; no fallback price exists yet for this very
        # first fetch, so a total snapshot outage still raises here (the caller,
        # _place_exit_order, catches it and returns 0 — the pre-existing "no
        # fill" contract — rather than ever pricing this order off 0.0/negative).
        bid_price = await self._get_price_with_fallback(code, "bid")
        limit_price = round(bid_price - self._cfg.exit_limit_buffer_usd, 4)
        escalation_rounds = 0

        while remaining > 0:
            order_id = await self._gw.place_order(code, remaining, limit_price, side)
            order_qty = remaining  # CR-04: placed qty for this round's _cancel_confirmed check
            append_audit({
                "event": "exit_order_placed",
                "code": code,
                "order_id": order_id,
                "price": limit_price,
                "qty": remaining,
            })
            _logger.info("exit_order_placed", code=code, order_id=order_id,
                         price=limit_price, qty=remaining)

            loop = asyncio.get_event_loop()
            deadline = loop.time() + ttl
            order_filled_this_round = 0

            while loop.time() < deadline:
                await asyncio.sleep(escalation_cadence)

                # Poll via order_list_query (get_order_status) — cumulative dealt_qty.
                # deal_list_query (get_order_fills) is NOT used: unsupported on SIMULATE
                # ("Paper trading does not support deal data."). get_order_status returns
                # ONE row per order with cumulative dealt_qty. We break on first non-zero
                # read so order_filled_this_round = dealt_qty is the total fill for this
                # order_id in this round (no cross-round double-count — EXEC-05 / CR-02).
                #
                # Defense-in-depth (RATE-01): absorb any transient status-query failure
                # (e.g. a residual rate-limit burst that get_order_status's own retry
                # could not clear in time). The exit order is still live at the broker;
                # we continue polling on the next cadence tick rather than propagating
                # out of manage_exit, which would orphan the placed order.
                try:
                    order_rows = await self._gw.get_order_status(order_id)
                except Exception as _poll_exc:
                    _logger.warning(
                        "exit_poll_status_failed",
                        code=code,
                        order_id=order_id,
                        error=str(_poll_exc),
                    )
                    continue  # order stays live; retry on next cadence tick
                matched = [
                    r for r in order_rows
                    if str(r.get("order_id", "")) == str(order_id)
                ]
                if matched:
                    order_filled_this_round = int(matched[0].get("dealt_qty", 0) or 0)
                    # Fallback price if the post-cancel re-query below has no match
                    # (P1-B: manage_exit's own weighted-avg-price accumulator).
                    order_filled_price_this_round = float(
                        matched[0].get("dealt_avg_price", 0.0) or 0.0
                    )
                    if order_filled_this_round > 0:
                        break

            if order_filled_this_round > 0:
                # Cancel any unfilled remainder before computing the definitive fill.
                # For a fully-filled order this is a no-op; for a partial fill it
                # prevents the remainder from executing while we re-query.
                cancel_err = None
                try:
                    await self._gw.cancel_order(order_id)
                except Exception as exc:
                    cancel_err = exc

                # Fix 1.4: re-query dealt_qty AFTER cancel to get the definitive
                # cumulative fill for this order_id. A partial fill arriving during
                # the cancel window is captured here; using only the pre-cancel
                # snapshot would over-size the replacement order (over-sell into short).
                # (T-06.2-03: tamper-prevention on cancel-replace qty)
                # CR-04 site 3: this is now the SAME shared re-read used to
                # confirm the cancel -- no extra get_order_status call.
                post_row = await self._reread_order(order_id)
                if post_row is not None:
                    post_cancel_filled = int(post_row.get("dealt_qty", 0) or 0)
                    post_cancel_price = float(post_row.get("dealt_avg_price", 0.0) or 0.0)
                else:
                    post_cancel_filled = order_filled_this_round  # safe fallback
                    post_cancel_price = order_filled_price_this_round

                total_filled += post_cancel_filled
                total_notional += post_cancel_filled * post_cancel_price
                remaining = qty - total_filled

                append_audit({
                    "event": "exit_fill_detected",
                    "code": code,
                    "order_id": order_id,
                    "filled_this_round": post_cancel_filled,
                    "remaining": remaining,
                })
                _logger.info("exit_fill_detected", code=code, order_id=order_id,
                             filled=post_cancel_filled, remaining=remaining)

                if cancel_err is not None and not _cancel_confirmed(post_row, order_qty):
                    avg_price = total_notional / total_filled if total_filled > 0 else 0.0
                    await self._escalate_unconfirmed_cancel(
                        cancel_err, code=code, order_id=order_id, side="SELL",
                        dealt_qty=post_cancel_filled, qty=order_qty,
                        filled_qty=total_filled, avg_price=avg_price,
                    )

                if remaining <= 0:
                    break
                # Price next round at current bid - buffer - escalation. Finding
                # 2.4: fall back to the last known bid_price on a transient
                # snapshot failure — the stop-out loop must never abort here.
                bid_price = await self._get_price_with_fallback(code, "bid", fallback=bid_price)
                limit_price = round(
                    bid_price - self._cfg.exit_limit_buffer_usd
                    - escalation_rounds * escalation_step,
                    4,
                )
            else:
                # TTL expired with no fill — cancel and escalate price (D-07)
                cancel_err = None
                try:
                    await self._gw.cancel_order(order_id)
                except Exception as exc:
                    cancel_err = exc

                # CR-04 site 4 (+ quick 260925-inw): re-read after EVERY TTL
                # cancel, not only a failed one -- see the site 2 comment in
                # _manage_entry_order for the full rationale and the rate
                # budget (here: one order_list_query per TTL expiry, exit
                # cadence 10s over a 15s TTL). A fill that completes during a
                # SUCCESSFUL cancel is credited before sizing the next SELL;
                # a failed re-read escalates and sets the D5 exit hold, so no
                # later SELL can over-sell into a short.
                row = await self._reread_order(order_id)
                dealt = int(row.get("dealt_qty", 0) or 0) if row is not None else 0
                dealt_price = float(row.get("dealt_avg_price", 0.0) or 0.0) if row is not None else 0.0
                if dealt > 0:
                    total_filled += dealt
                    total_notional += dealt * dealt_price
                    remaining = qty - total_filled
                    append_audit({
                        "event": "exit_fill_detected",
                        "code": code,
                        "order_id": order_id,
                        "filled_this_round": dealt,
                        "remaining": remaining,
                    })
                    _logger.info("exit_fill_detected", code=code, order_id=order_id,
                                 filled=dealt, remaining=remaining)

                if row is None or (cancel_err is not None and not _cancel_confirmed(row, order_qty)):
                    avg_price = total_notional / total_filled if total_filled > 0 else 0.0
                    await self._escalate_unconfirmed_cancel(
                        cancel_err or "post-cancel re-read failed",
                        code=code, order_id=order_id, side="SELL",
                        dealt_qty=dealt, qty=order_qty,
                        filled_qty=total_filled, avg_price=avg_price,
                    )

                if remaining <= 0:
                    break

                escalation_rounds += 1
                # Finding 2.4: fall back to the last known bid_price on failure.
                bid_price = await self._get_price_with_fallback(code, "bid", fallback=bid_price)
                limit_price = round(
                    bid_price - self._cfg.exit_limit_buffer_usd
                    - escalation_rounds * escalation_step,
                    4,
                )
                _logger.info("exit_escalating", code=code, round=escalation_rounds,
                             new_limit=limit_price)

        avg_price = total_notional / total_filled if total_filled > 0 else 0.0
        return total_filled, avg_price

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    def _resolve_intent_expired(self, intent_id: str) -> None:
        """Mark a pending_intent row as EXPIRED in StateStore (D-05).

        Called when entry_max_retries is exhausted and the engine abandons.
        Updates the status column so Phase 3's daily counter and re-entry gate
        can observe the intent was not filled (Phase-3 D-09/D-12).
        """
        try:
            self._store.expire_pending_intent(intent_id, now_et().isoformat())
        except Exception:
            _logger.warning("intent_expire_failed", intent_id=intent_id, exc_info=True)
