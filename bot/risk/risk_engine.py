#!/usr/bin/env python3
"""
bot.risk.risk_engine — RiskEngine: trade sizing, stop computation, OrderIntent emission.

Takes a verified SignalEvent, reads LIVE account equity from the gateway, computes
the 1%-risk and 10%-notional-cap share counts (takes the smaller, rounds DOWN),
derives the LOD-1% initial stop via compute_initial_stop(), and emits a fully-specified
OrderIntent that is both logged to structlog and persisted to StateStore.

No orders are placed in this module — Phase 4 owns order execution.

Requirements closed:
  - RISK-01: 1% of live equity per trade (get_equity() on every signal)
  - RISK-02: 10% notional cap applied; smaller of risk_qty / notional_cap_qty taken
  - RISK-03: Initial stop = LOD-1% via compute_initial_stop(lod)
  - D-07: Shares rounded DOWN via math.floor; <1 share emits no intent
  - D-12: OrderIntent logged to structlog AND persisted to pending_intents table

Exports: RiskEngine
"""

import math
import uuid
from typing import Optional

from bot.config.loader import StrategyConfig
from bot.risk.events import OrderIntent
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.signal.events import SignalEvent
from bot.state.store import StateStore
from bot.strategy.trend_join_long import TrendJoinLong


# ============================================================
# Module Logger
# ============================================================

_logger = get_logger(__name__)


# ============================================================
# RiskEngine
# ============================================================

class RiskEngine:
    """Sizes a SignalEvent into an OrderIntent using live equity and config thresholds.

    Constructor:
        cfg: StrategyConfig — all sizing thresholds sourced here (CFG-01).
        gateway: MoomooGateway — async broker I/O (get_equity()).
        store: StateStore — open SQLite connection (pending_intents write).
        signal_engine: Optional SignalEngine — for calling note_intent_emitted()
            after each emitted intent (D-09 burst guard tally).

    Pipeline:
        1. Receive SignalEvent (all intraday gates already passed in SignalEngine).
        2. Read live equity via gateway.get_equity() (RISK-01, D-05).
        3. Compute stop_price = compute_initial_stop(signal.lod) (RISK-03).
        4. If stop_distance <= 0, log and return None (guard against pathological input).
        5. Size to 1% risk: risk_qty = floor(risk_dollars / stop_distance) (RISK-01, D-07).
        6. Cap at 10% notional: notional_cap_qty = floor(cap / entry_price) (RISK-02, D-07).
        7. qty = min(risk_qty, notional_cap_qty) — take the smaller (D-07).
        8. If qty < 1, log 'intent_skipped_under_budget' and return None (D-07).
        9. Build and emit OrderIntent; persist to pending_intents; log to structlog (D-12).
        10. Call signal_engine.note_intent_emitted() if provided (D-09 tally, RISK-05).
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        gateway,
        store: StateStore,
        signal_engine=None,
    ) -> None:
        """Initialise the RiskEngine.

        Args:
            cfg: Loaded StrategyConfig (all sizing thresholds; CFG-01).
            gateway: MoomooGateway instance (async broker access for get_equity).
            store: Open StateStore (SQLite connection for pending_intents persistence).
            signal_engine: Optional SignalEngine — provides note_intent_emitted() for
                           the D-09 burst guard. If None, the tally increment is skipped.
        """
        self._cfg = cfg
        self._gateway = gateway
        self._store = store
        self._signal_engine = signal_engine
        # Instantiate once — compute_initial_stop reads cfg.initial_stop_pct
        self._strategy = TrendJoinLong(cfg)

    # ============================================================
    # Core Method
    # ============================================================

    async def on_signal(self, signal: SignalEvent) -> Optional[OrderIntent]:
        """Size a SignalEvent into an OrderIntent and emit it (or return None).

        Args:
            signal: Verified SignalEvent (all intraday gates already passed).

        Returns:
            OrderIntent if the trade is viable (qty >= 1); None otherwise.
        """
        entry_price = signal.bar.close

        # RISK-03: compute stop via the strategy helper (do NOT reimplement the math)
        stop_price = self._strategy.compute_initial_stop(signal.lod)

        stop_distance = entry_price - stop_price
        if stop_distance <= 0:
            _logger.warning(
                "non_positive_stop_distance",
                code=signal.code,
                entry_price=entry_price,
                stop_price=stop_price,
                stop_distance=stop_distance,
            )
            return None

        # RISK-01: live equity on every sizing decision (D-05 — never cached)
        equity = await self._gateway.get_equity()

        # RISK-01: 1%-risk sizing
        risk_dollars = equity * self._cfg.max_risk_per_trade_pct / 100.0
        risk_qty = math.floor(risk_dollars / stop_distance)  # round DOWN (D-07)

        # RISK-02: 10%-notional cap
        notional_cap = equity * self._cfg.max_position_size_pct / 100.0
        notional_cap_qty = math.floor(notional_cap / entry_price)  # round DOWN (D-07)

        # D-07: take the smaller quantity to stay within both constraints
        qty = min(risk_qty, notional_cap_qty)

        if qty < 1:
            _logger.info(
                "intent_skipped_under_budget",
                code=signal.code,
                risk_qty=risk_qty,
                notional_cap_qty=notional_cap_qty,
                equity=equity,
                stop_distance=stop_distance,
            )
            return None

        # Build the OrderIntent
        intent = OrderIntent(
            code=signal.code,
            entry_price=entry_price,
            stop_price=stop_price,
            quantity=qty,
            equity_used=equity,
            risk_dollars=risk_dollars,
            notional=entry_price * qty,
            emitted_at=now_et(),
            source_signal=signal,
            intent_id=str(uuid.uuid4()),
        )

        # D-12: persist to pending_intents via guarded store method (CR-01)
        self._store.insert_pending_intent(
            intent.intent_id,
            intent.code,
            intent.entry_price,
            intent.stop_price,
            intent.quantity,
            intent.emitted_at.isoformat(),
        )

        # D-12 / RISK-03 #5: structured audit log with stop_price and quantity
        _logger.info(
            "order_intent_emitted",
            intent_id=intent.intent_id,
            code=intent.code,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            quantity=intent.quantity,
            equity_used=intent.equity_used,
            risk_dollars=intent.risk_dollars,
            notional=intent.notional,
        )

        # D-09 / RISK-05: increment signal engine pending tally so daily-cap gate
        # correctly counts this emitted intent before any fill registers.
        if self._signal_engine is not None:
            self._signal_engine.note_intent_emitted()

        return intent
