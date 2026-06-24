#!/usr/bin/env python3
"""
bot.risk.events — OrderIntent dataclass (Phase 3, RISK-01/02/03, D-12).

OrderIntent is produced by RiskEngine when a SignalEvent passes all
risk gates (sizing math, notional cap, equity read). It is both logged
to structlog and persisted to StateStore as a "pending intent" record
(migration 0003, D-12). No broker order is placed until Phase 4.
"""

from dataclasses import dataclass
from datetime import datetime

from bot.signal.events import SignalEvent


@dataclass
class OrderIntent:
    """Verified, sized trade intent (no order placed until Phase 4).

    Produced by RiskEngine after applying:
      - 1% risk sizing: risk_dollars = equity * max_risk_per_trade_pct / 100 (RISK-01)
      - Risk quantity: floor(risk_dollars / (entry_price - stop_price))
      - 10% notional cap: floor(equity * max_position_size_pct / 100 / entry_price) (RISK-02)
      - Quantity: min(risk_qty, notional_cap_qty); emit no intent if qty < 1 (D-07)
      - Intent persisted to pending_intents table (migration 0003, D-12)
      - Intent logged to structlog for audit trail (D-12, RISK-03 #5)

    Fields:
        code: Moomoo-format stock code.
        entry_price: bar.close at signal time (indicative; Phase 4 may adjust at fill).
        stop_price: compute_initial_stop(signal.lod) — LOD minus 1% (RISK-03).
        quantity: Risk-sized whole shares, rounded down, >= 1 (D-07).
        equity_used: Live account equity used for sizing (for audit; D-04/D-05).
        risk_dollars: equity_used * max_risk_per_trade_pct / 100.
        notional: entry_price * quantity (position notional value).
        emitted_at: now_et() at OrderIntent emission.
        source_signal: The SignalEvent that triggered this intent.
        intent_id: UUID4 string — primary key in pending_intents table (D-12).
    """

    code: str
    entry_price: float
    stop_price: float
    quantity: int
    equity_used: float
    risk_dollars: float
    notional: float
    emitted_at: datetime
    source_signal: SignalEvent
    intent_id: str
