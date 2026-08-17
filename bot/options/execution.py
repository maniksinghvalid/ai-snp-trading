#!/usr/bin/env python3
"""
bot.options.execution — LegExecutor: single-leg order working and multi-leg sequencing.

Moomoo's API has no multi-leg/combo order for paper accounts, so a spread is
placed as sequential single-leg LIMIT orders. This module owns that loop:

  fill_leg      — mid-anchored limit, polled to a TTL, escalated toward and then
                  through the natural price, cancelled on every exit path.
  open_position — places the legs in the order given (pick_strikes returns the
                  long wings FIRST, so protection is bought before risk is sold),
                  persists each order_id before the next leg is placed, and
                  aggressively unwinds already-filled legs if any leg fails.
  close_legs    — buys back the SHORT legs before selling the long wings, so the
                  account is never momentarily naked-short.

Every tunable comes from OptionsConfig (CFG-01): limit_buffer_usd,
poll_interval_s, ttl_s, escalation_step_usd, max_retries.

Exports: LegExecutor
"""
import asyncio

from bot.execution.engine import _get_trd_side_buy, _get_trd_side_sell
from bot.safety.audit_log import append_audit
from bot.safety.logger import get_logger


_logger = get_logger(__name__)

# An option limit order can never be priced at or below zero.
_MIN_LIMIT_PRICE = 0.01


class LegExecutor:
    """Works individual option legs and sequences them into spreads (Phase 8)."""

    def __init__(self, gateway, cfg) -> None:
        """
        Args:
            gateway: MoomooGateway (connected).
            cfg:     OptionsConfig — supplies every price/TTL/retry tunable.
        """
        self._gw = gateway
        self._cfg = cfg

    # --------------------------------------------------------
    # Single leg
    # --------------------------------------------------------

    async def fill_leg(
        self, code, side, qty, bid, ask, aggressive=False, on_placed=None,
    ):
        """Work one leg as a mid-anchored limit order until filled or abandoned.

        Args:
            code:       Option code (e.g. "US.SPY260320P600000").
            side:       "BUY" or "SELL".
            qty:        Contracts to fill.
            bid, ask:   Current quote for `code` (from the caller's snapshot).
            aggressive: Start at the natural price (ask for BUY, bid for SELL)
                        instead of mid ± buffer — used when unwinding.
            on_placed:  Optional async callback awaited with (order_id)
                        IMMEDIATELY after each placement, before any polling.
                        This is what lets open_position persist the order_id
                        before the next leg goes out.

        Returns:
            (order_id, avg_price, filled_qty) 3-tuple, or None when nothing filled.

            The 3-tuple is a deliberate widening of the design's 2-tuple: a TTL
            partial fill has to tell the caller how many contracts actually
            filled, otherwise the unwind path cannot size its closing order.

        Guarantee: no resting order is left behind on any return path — the
        working order is cancelled before every escalation and before giving up.
        """
        cfg = self._cfg
        mid = (float(bid) + float(ask)) / 2
        if side == "BUY":
            price = float(ask) if aggressive else mid + cfg.limit_buffer_usd
        else:
            price = float(bid) if aggressive else mid - cfg.limit_buffer_usd
        price = max(round(price, 2), _MIN_LIMIT_PRICE)

        trd_side = _get_trd_side_buy() if side == "BUY" else _get_trd_side_sell()
        loop = asyncio.get_running_loop()

        for attempt in range(cfg.max_retries + 1):
            order_id = await self._gw.place_order(code, int(qty), price, trd_side)
            if on_placed is not None:
                await on_placed(order_id)
            _logger.info(
                "leg_order_placed",
                code=code, side=side, qty=int(qty), price=price,
                order_id=order_id, attempt=attempt,
            )

            deadline = loop.time() + cfg.ttl_s
            while loop.time() < deadline:
                await asyncio.sleep(cfg.poll_interval_s)
                dealt_qty, avg_price = await self._poll(order_id)
                if dealt_qty >= int(qty):
                    _logger.info(
                        "leg_filled", code=code, order_id=order_id,
                        filled_qty=dealt_qty, avg_price=avg_price,
                    )
                    return (order_id, avg_price, dealt_qty)

            # TTL expired — cancel before doing anything else, then re-read once:
            # the remainder may have filled while the cancel was in flight.
            try:
                await self._gw.cancel_order(order_id)
            except Exception:
                pass    # already fully filled / already cancelled — swallow (engine parity)

            dealt_qty, avg_price = await self._poll(order_id)
            if dealt_qty > 0:
                _logger.info(
                    "leg_partially_filled", code=code, order_id=order_id,
                    filled_qty=dealt_qty, requested_qty=int(qty), avg_price=avg_price,
                )
                return (order_id, avg_price, dealt_qty)

            if attempt < cfg.max_retries:
                # Walk the limit toward — and then through — the natural price.
                step = cfg.escalation_step_usd if side == "BUY" else -cfg.escalation_step_usd
                price = max(round(price + step, 2), _MIN_LIMIT_PRICE)

        _logger.warning(
            "leg_fill_abandoned",
            code=code, side=side, qty=int(qty), attempts=cfg.max_retries + 1,
        )
        append_audit({
            "event": "leg_fill_abandoned",
            "code": code, "side": side, "qty": int(qty),
        })
        return None

    async def _poll(self, order_id) -> tuple:
        """Return (dealt_qty, dealt_avg_price) for order_id; (0, 0.0) if unknown."""
        rows = await self._gw.get_order_status(order_id)
        for row in rows or []:
            if str(row.get("order_id", "")) == str(order_id):
                return (
                    int(row.get("dealt_qty", 0) or 0),
                    float(row.get("dealt_avg_price", 0.0) or 0.0),
                )
        return (0, 0.0)

    # --------------------------------------------------------
    # Multi-leg
    # --------------------------------------------------------

    async def open_position(
        self, legs, qty, quotes, on_leg_placed=None, on_leg_filled=None,
    ):
        """Open a spread leg by leg, unwinding everything filled if any leg fails.

        The legs are worked in the order given and NOT re-sorted: pick_strikes
        returns the long wings first, so the protection is always bought before
        the risk is sold.

        Args:
            legs:   leg dicts from pick_strikes ({code, right, strike, side, mid}).
            qty:    contracts per leg.
            quotes: {code: {"bid": float, "ask": float}} — ONE snapshot taken by
                    the caller. Required: strategy legs carry `mid` only.
            on_leg_placed: async callback (leg, order_id), awaited before the
                           next leg is placed (persist the order_id here).
            on_leg_filled: async callback (leg, order_id, price, filled_qty).

        Returns:
            list of {**leg, order_id, entry_price, filled_qty} on full success,
            or None when any leg failed (the filled legs have been unwound).
        """
        filled = []
        for leg in legs:
            quote = quotes[leg["code"]]

            placed_cb = None
            if on_leg_placed is not None:
                async def placed_cb(order_id, _leg=leg):
                    await on_leg_placed(_leg, order_id)

            result = await self.fill_leg(
                leg["code"], leg["side"], qty,
                quote["bid"], quote["ask"], on_placed=placed_cb,
            )

            if result is not None:
                order_id, price, filled_qty = result
                filled.append({
                    **leg,
                    "order_id": order_id,
                    "entry_price": price,
                    "filled_qty": filled_qty,
                })

            # A partial-qty leg counts as a failure, but it IS in `filled` and
            # therefore gets unwound below.
            if result is None or result[2] != qty:
                _logger.warning(
                    "open_position_leg_failed",
                    code=leg["code"], side=leg["side"],
                    filled_qty=(result[2] if result else 0), requested_qty=qty,
                )
                await self.close_legs(filled, quotes, aggressive=True)
                _logger.warning("open_position_unwound", unwound_legs=len(filled))
                append_audit({
                    "event": "open_position_unwound",
                    "failed_code": leg["code"],
                    "unwound_legs": len(filled),
                })
                return None

            if on_leg_filled is not None:
                await on_leg_filled(leg, result[0], result[1], result[2])

        return filled

    async def close_legs(
        self, legs, quotes, aggressive=False, on_leg_placed=None, on_leg_filled=None,
    ):
        """Close the given legs, SHORT legs first (bought back before wings are sold).

        Closing a long wing first would leave the short leg momentarily naked, so
        the ordering here is a risk control, not a preference. One leg failing
        does not stop the rest — a half-closed spread left unattended is worse.

        Args:
            legs:    leg dicts; qty comes from "filled_qty" when present, else "qty".
            quotes:  {code: {"bid": float, "ask": float}}.
            aggressive: start at the natural price (used for entry unwinds).
            on_leg_placed / on_leg_filled: same shape as open_position's.

        Returns:
            bool — True only when every leg closed for its full quantity.
        """
        shorts = [leg for leg in legs if leg.get("side") == "SELL"]
        longs = [leg for leg in legs if leg.get("side") != "SELL"]

        all_closed = True
        for leg in shorts + longs:
            code = leg["code"]
            quote = quotes.get(code)
            if not quote:
                # No quote → any limit we invent is wrong in one direction or the
                # other. Placing nothing is the safe failure; the caller escalates.
                _logger.warning("close_leg_no_quote", code=code)
                all_closed = False
                continue

            closing_side = "BUY" if leg.get("side") == "SELL" else "SELL"
            qty = leg.get("filled_qty") or leg.get("qty")

            placed_cb = None
            if on_leg_placed is not None:
                async def placed_cb(order_id, _leg=leg):
                    await on_leg_placed(_leg, order_id)

            result = await self.fill_leg(
                code, closing_side, qty, quote["bid"], quote["ask"],
                aggressive=aggressive, on_placed=placed_cb,
            )

            if result is None or result[2] != qty:
                _logger.warning(
                    "close_leg_failed",
                    code=code, closing_side=closing_side,
                    filled_qty=(result[2] if result else 0), requested_qty=qty,
                )
                all_closed = False

            if result is not None and on_leg_filled is not None:
                await on_leg_filled(leg, result[0], result[1], result[2])

        return all_closed
