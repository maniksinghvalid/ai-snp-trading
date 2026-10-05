#!/usr/bin/env python3
"""
bot.ibs.execution — IbsExecutor: deadline-bounded order working for the IBS bot (IBS-05).

D-08: LIMIT orders only. Every order goes through LegExecutor.fill_leg ->
gateway.place_order (normal limit order type, audited by the gateway); this module
never places or cancels an order itself.

Ruling 2: LegExecutor is REUSED BY IMPORT. The IBS package never copies or edits
bot/options. Its config object is adapted with a SimpleNamespace (entry buffer /
exit buffer), and the quote is passed as bid = ask = last so mid = last: the first
price is exactly BUY = last + entry_limit_buffer_usd / SELL = last - exit_limit_buffer_usd,
then escalated by escalation_step_usd up to max_reprices times.

D-09: work() is bounded by asyncio.wait_for to the deadline the service passes
(hard-cancel time - execution.executor_margin_s, CR-02). On timeout
fill_leg's shielded cancel removes the resting order and TimeoutError propagates.

Return contract (inherited from fill_leg): (order_id, avg_price, filled_qty) on a full
or TTL-partial fill, None when nothing filled (or the deadline already passed). An
exception after on_placed fired means "exposure unknown"; one before it (place_order
raised) placed nothing — the service tells the two apart (CR-01).

Pitfall 7: callers work orders SEQUENTIALLY, never in parallel (broker rate limits are
shared with the options bot).

Exports: IbsExecutor
"""
import asyncio
from types import SimpleNamespace

from bot.options.execution import LegExecutor
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

_logger = get_logger(__name__)


def _leg_cfg(cfg, buffer_usd) -> SimpleNamespace:
    """Adapt IbsConfig to the five attributes LegExecutor reads."""
    return SimpleNamespace(
        limit_buffer_usd=buffer_usd,
        poll_interval_s=cfg.poll_interval_seconds,
        ttl_s=cfg.order_ttl_seconds,
        escalation_step_usd=cfg.escalation_step_usd,
        max_retries=cfg.max_reprices,
    )


class IbsExecutor:
    """Works one IBS order at a time as a marketable limit with a hard deadline."""

    def __init__(self, gateway, cfg) -> None:
        self._entry = LegExecutor(gateway, _leg_cfg(cfg, cfg.entry_limit_buffer_usd))
        self._exit = LegExecutor(gateway, _leg_cfg(cfg, cfg.exit_limit_buffer_usd))

    async def work(self, side, code, qty, last, deadline, on_placed=None):
        """Work a BUY (entry) or SELL (exit) until filled, abandoned or `deadline`.

        Raises:
            ValueError: side is not BUY/SELL (nothing placed).
            asyncio.TimeoutError: the deadline passed mid-order (resting order cancelled).
            RuntimeError: a TTL cancel was unconfirmed (CR-04).
        """
        if side == "BUY":
            ex = self._entry
        elif side == "SELL":
            ex = self._exit
        else:
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        remaining = (deadline - now_et()).total_seconds()
        if remaining <= 0:
            _logger.warning("ibs_order_skipped_deadline", code=code, side=side)
            return None
        last = float(last)
        return await asyncio.wait_for(
            ex.fill_leg(code, side, int(qty), last, last, on_placed=on_placed),
            timeout=remaining,
        )
