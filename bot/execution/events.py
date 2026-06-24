#!/usr/bin/env python3
"""
bot.execution.events — FillEvent dataclass (Phase 4, EXEC-05).

FillEvent is emitted by ExecutionEngine when a fill (full or partial)
is detected via deal_list_query, keyed by order_id (EXEC-05 — never by
quantity). Consumed by PositionManager.on_fill() to advance the FSM
from AWAITING_FILL to ACTIVE on the entry fill, or to reconcile partial
and full exit fills.

Usage example:
    fill = FillEvent(
        order_id="12345",
        intent_id="abc-uuid",
        code="US.AAPL",
        filled_qty=100,
        avg_fill_price=182.55,
        is_entry=True,
        fill_time=datetime.now(),
    )
"""

from dataclasses import dataclass
from datetime import datetime


# ============================================================
# FillEvent
# ============================================================

@dataclass
class FillEvent:
    """Emitted by ExecutionEngine when a fill (full or partial) is detected.

    Keyed by order_id (EXEC-05) — never by quantity. Consumed by
    PositionManager.on_fill().

    Fields:
        order_id: Broker-assigned order ID — primary reconciliation key (EXEC-05).
        intent_id: Corresponding pending_intents.intent_id UUID string (D-12).
        code: Moomoo-format stock code (e.g. "US.AAPL").
        filled_qty: Total filled quantity (cumulative across all fill rows for
                    this order_id).
        avg_fill_price: Average fill price across all fill rows for this order_id
                        (weighted by quantity, computed by ExecutionEngine).
        is_entry: True = entry fill (AWAITING_FILL → ACTIVE);
                  False = exit fill (partial / stop-out / force-close).
        fill_time: create_time of the latest fill row for this order (now_et()
                   at detection time, from bot.safety.et_helpers).
    """

    order_id: str
    intent_id: str
    code: str
    filled_qty: int
    avg_fill_price: float
    is_entry: bool
    fill_time: datetime
