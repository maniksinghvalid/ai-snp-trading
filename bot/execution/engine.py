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
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

_logger = get_logger(__name__)


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

    def __init__(self, gateway, store, cfg) -> None:
        """Initialise ExecutionEngine.

        Args:
            gateway: MoomooGateway instance (must be connected before use).
            store:   StateStore instance (open; conn accessible).
            cfg:     StrategyConfig with execution tunables (CFG-01).
        """
        self._gw = gateway
        self._store = store
        self._cfg = cfg

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    async def consume_intent(self, intent) -> Optional[FillEvent]:
        """Consume an OrderIntent: place entry order, poll fills, emit FillEvent.

        Wraps _manage_entry_order; the caller (PositionManager) receives the
        FillEvent and advances the FSM from AWAITING_FILL → ACTIVE.

        Args:
            intent: OrderIntent from RiskEngine (code, quantity, entry_price,
                    stop_price, intent_id).

        Returns:
            FillEvent on any fill (full or partial, D-06), or None if abandoned
            after exceeding entry_max_retries (D-05).
        """
        return await self._manage_entry_order(intent)

    async def _manage_entry_order(self, intent) -> Optional[FillEvent]:
        """Place a marketable-limit entry; poll deal_list_query for fills by order_id.

        Protocol (D-04/D-05/D-06/EXEC-03/EXEC-05):
          1. Price entry at ask + cfg.entry_limit_buffer_usd (D-04).
          2. Place via gateway.place_order (OrderType.NORMAL — EXEC-02 enforced by gateway).
          3. Poll every cfg.entry_poll_interval_seconds until cfg.entry_ttl_seconds.
          4. Match fills by order_id only (EXEC-05).
          5. On any fill: cancel remainder (D-06), emit FillEvent(is_entry=True).
          6. On TTL with no fill: cancel, re-price at fresh ask+buffer, re-place.
             Repeat up to cfg.entry_max_retries times (D-05).
          7. After exceeding the cap: cancel, mark pending_intent EXPIRED, return None.

        Args:
            intent: OrderIntent (code, quantity, intent_id).

        Returns:
            FillEvent(is_entry=True) on fill, or None on abandon.
        """
        # D-04: price at/through current ask + buffer
        ask_price = await self._gw.get_ask_price(intent.code)
        limit_price = round(ask_price + self._cfg.entry_limit_buffer_usd, 4)

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

                fills = await self._gw.get_order_fills()
                matched = [
                    f for f in fills
                    if str(f.get("order_id", "")) == str(order_id)
                ]

                if matched:
                    total_filled = sum(int(f.get("qty", 0) or 0) for f in matched)
                    total_value = sum(
                        float(f.get("qty", 0) or 0) * float(f.get("price", 0) or 0)
                        for f in matched
                    )
                    avg_fill_price = total_value / total_filled if total_filled > 0 else 0.0

                    # D-06: cancel unfilled remainder, accept partial fill as position
                    try:
                        await self._gw.cancel_order(order_id)
                    except Exception:
                        pass  # remainder may already be fully filled — swallow

                    fill_event = FillEvent(
                        order_id=str(order_id),
                        intent_id=intent.intent_id,
                        code=intent.code,
                        filled_qty=int(total_filled),
                        avg_fill_price=float(avg_fill_price),
                        is_entry=True,
                        fill_time=now_et(),
                    )
                    append_audit({
                        "event": "entry_fill_detected",
                        "order_id": order_id,
                        "filled_qty": int(total_filled),
                        "avg_fill_price": float(avg_fill_price),
                        "intent_id": intent.intent_id,
                    })
                    _logger.info(
                        "entry_fill_detected",
                        order_id=order_id,
                        filled_qty=int(total_filled),
                        avg_fill_price=float(avg_fill_price),
                    )
                    return fill_event

            # TTL expired for this attempt — cancel current order
            try:
                await self._gw.cancel_order(order_id)
            except Exception:
                pass
            _logger.info("entry_ttl_expired", order_id=order_id, attempt=attempt)

            if attempt < self._cfg.entry_max_retries:
                # Re-price at fresh ask + buffer and re-place (EXEC-03)
                ask_price = await self._gw.get_ask_price(intent.code)
                limit_price = round(ask_price + self._cfg.entry_limit_buffer_usd, 4)
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
    ) -> int:
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
            int — total filled quantity across all exit order_ids (cumulative).
        """
        total_filled = 0
        remaining = qty
        # Price through bid with buffer (D-07)
        bid_price = await self._gw.get_bid_price(code)
        limit_price = round(bid_price - self._cfg.exit_limit_buffer_usd, 4)
        escalation_rounds = 0

        while remaining > 0:
            order_id = await self._gw.place_order(code, remaining, limit_price, side)
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

                fills = await self._gw.get_order_fills()
                matched = [
                    f for f in fills
                    if str(f.get("order_id", "")) == str(order_id)
                ]
                if matched:
                    order_filled_this_round = sum(int(f.get("qty", 0) or 0) for f in matched)
                    break

            if order_filled_this_round > 0:
                total_filled += order_filled_this_round
                remaining -= order_filled_this_round
                append_audit({
                    "event": "exit_fill_detected",
                    "code": code,
                    "order_id": order_id,
                    "filled_this_round": order_filled_this_round,
                    "remaining": remaining,
                })
                _logger.info("exit_fill_detected", code=code, order_id=order_id,
                             filled=order_filled_this_round, remaining=remaining)
                if remaining <= 0:
                    break
                # Cancel any remainder before re-placing
                try:
                    await self._gw.cancel_order(order_id)
                except Exception:
                    pass
                # Price next round at current bid - buffer - escalation
                bid_price = await self._gw.get_bid_price(code)
                limit_price = round(
                    bid_price - self._cfg.exit_limit_buffer_usd
                    - escalation_rounds * escalation_step,
                    4,
                )
            else:
                # TTL expired with no fill — cancel and escalate price (D-07)
                try:
                    await self._gw.cancel_order(order_id)
                except Exception:
                    pass
                escalation_rounds += 1
                bid_price = await self._gw.get_bid_price(code)
                limit_price = round(
                    bid_price - self._cfg.exit_limit_buffer_usd
                    - escalation_rounds * escalation_step,
                    4,
                )
                _logger.info("exit_escalating", code=code, round=escalation_rounds,
                             new_limit=limit_price)

        return total_filled

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
            self._store.conn.execute(
                "UPDATE pending_intents SET status='EXPIRED', resolved_at=? "
                "WHERE intent_id=?",
                (now_et().isoformat(), intent_id),
            )
            self._store.conn.commit()
        except Exception:
            _logger.warning("intent_expire_failed", intent_id=intent_id, exc_info=True)
