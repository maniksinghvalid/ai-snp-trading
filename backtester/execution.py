#!/usr/bin/env python3
"""
backtester.execution — SimulatedExecution (N+1-open fill, BT-02) + SimulatedGateway stub.

SimulatedExecution replaces bot.execution.engine.ExecutionEngine for the backtest replay.
It keeps the SAME public surface (consume_intent, manage_exit) so PositionManager code is
unchanged, but fills at the NEXT bar's open (via feed.next_bar) instead of polling a real
broker -- the anti-look-ahead guarantee BT-02 requires (the signal used bar N's close; the
fill can only happen on bar N+1). Every fill is recorded (self.fills / self.exit_fills) so
the harness can build the per-trade log without touching a broker or the `trades` DB table.

SimulatedGateway answers only the two async methods the reused pipeline calls offline:
get_positions (SignalEngine Gate 4 concurrent-cap read) and get_equity (only reached when
cfg.sizing_equity_usd is None; current rules.json sets 100000 so this path is bypassed).
It exposes no broader order-placement/quote/subscription broker surface -- 06-RESEARCH
Pattern 3. PositionManager still receives gateway=None (06-05) so arm_stop_protection()
correctly no-ops.

Imports nothing from the moomoo broker gateway layer (T-06-05) -- zero broker/order path
in a backtest.

Exports: SimulatedExecution, SimulatedGateway.
"""
from typing import Dict, Optional
from uuid import uuid4

import pandas as pd

from bot.execution.events import FillEvent


class SimulatedExecution:
    """Fills OrderIntents/exits at the next bar's open + slippage (BT-02 N+1-open rule)."""

    def __init__(self, feed, slippage_usd: float = 0.0):
        """feed: object exposing next_bar(code, after) -> bar dict or None (e.g. SimulatedBarFeed)."""
        self._feed = feed
        self._slippage = slippage_usd
        self.fills = []
        self.exit_fills = []
        # manage_exit's live signature carries no bar/time_key -- the harness (06-05) must
        # call on_bar() for every closed bar (mirrors the bar_buffer wiring discipline,
        # 06-RESEARCH Pitfall 6) so manage_exit knows the "after" anchor for its next_bar lookup.
        self._last_bar_time_key: Dict[str, str] = {}

    def on_bar(self, bar: dict) -> None:
        """Record the latest bar seen for bar["code"] -- the anchor for manage_exit's N+1 lookup."""
        self._last_bar_time_key[bar["code"]] = bar["time_key"]

    async def consume_intent(self, intent) -> Optional[FillEvent]:
        """Fill an entry at the next bar's open + slippage; None if no next bar (D-05 abandon).

        NEVER fills at intent.entry_price (the signal bar's close) -- that would be a
        look-ahead bug (Pitfall 1). The signal fired on bar N's close; the earliest an order
        could actually execute is bar N+1's open.
        """
        next_bar = self._feed.next_bar(intent.code, after=intent.source_signal.bar.time_key)
        if next_bar is None:
            return None  # no next bar (end of session/data) -- unfilled, matches D-05 abandon

        fill = FillEvent(
            order_id=str(uuid4()),
            intent_id=intent.intent_id,
            code=intent.code,
            filled_qty=intent.quantity,
            avg_fill_price=next_bar["open"] + self._slippage,
            is_entry=True,
            fill_time=next_bar["time_key"],
        )
        self.fills.append(fill)
        return fill

    async def manage_exit(
        self,
        code: str,
        qty: int,
        side,
        escalation_step: float,
        escalation_cadence: float,
        ttl: float,
    ) -> int:
        """Fill an exit at the next bar's open + slippage (symmetric N+1, Assumption A2).

        Matches ExecutionEngine.manage_exit's signature exactly so PositionManager's
        _trigger_stop_out/_place_exit_order call sites are unchanged. No TTL/escalation loop
        here (no wall-clock in a backtest) -- the args are accepted for signature parity and
        otherwise ignored.
        """
        after = self._last_bar_time_key.get(code, "")
        next_bar = self._feed.next_bar(code, after=after)
        exit_price = next_bar["open"] + self._slippage if next_bar is not None else None
        time_key = next_bar["time_key"] if next_bar is not None else None

        exit_fill = {"code": code, "exit_price": exit_price, "qty": qty, "time_key": time_key}
        self.exit_fills.append(exit_fill)
        self.fills.append(exit_fill)
        return int(qty)


class SimulatedGateway:
    """Minimal stub answering only get_positions + get_equity (06-RESEARCH Pattern 3).

    Passed to SignalEngine/RiskEngine ONLY; PositionManager still receives gateway=None
    (06-05) so arm_stop_protection() correctly no-ops (tick-level broker stops have no
    offline analog at 5m bar granularity).
    """

    def __init__(self, position_manager_ref):
        """position_manager_ref: zero-arg callable resolving to the PositionManager.

        A callable (not the instance directly) so the harness can pass a lambda before the
        PositionManager exists yet, then the gateway resolves it lazily on each call.
        """
        self._position_manager_ref = position_manager_ref

    async def get_positions(self, refresh_cache: bool = True):
        """(0, DataFrame) of open codes -- excludes AWAITING_FILL and CLOSED phases."""
        manager = self._position_manager_ref()
        rows = [
            {"code": code}
            for code, pos in manager._positions.items()
            if pos.phase.value not in ("AWAITING_FILL", "CLOSED")
        ]
        return 0, pd.DataFrame(rows)  # ret=0 == RET_OK

    async def get_equity(self) -> float:
        """Fixed $100k -- only reached if cfg.sizing_equity_usd is None."""
        return 100_000.0
