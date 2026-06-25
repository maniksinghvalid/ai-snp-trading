#!/usr/bin/env python3
"""
bot.position.state — PositionState FSM dataclass and PositionPhase enum (Phase 4).

PositionPhase defines the lifecycle states of a position.
PositionState is a pure, broker-independent FSM dataclass that drives all
exit logic for the Trend Join Long strategy:

  AWAITING_FILL — order placed, no confirmed fill yet
  ACTIVE        — entry filled; initial stop active; no profit milestone hit yet
  PARTIAL_TAKEN — 1/3 partial exit taken at 0.75R close (POS-01)
  BREAKEVEN     — full 1R close hit; stop moved to entry price (POS-02)
  TRAILING      — 5m swing-low trail active; stop ratchets up, never down (POS-03, D-11)
  CLOSED        — position fully exited

FSM transition methods:

  apply_entry_fill(fill)          — AWAITING_FILL -> ACTIVE on confirmed fill (D-06)
  evaluate_close(close, cfg, ...) — per-bar close evaluation; returns an FSMAction
                                    string describing the triggered action WITHOUT
                                    executing any broker calls (pure, testable)

All thresholds (partial_profit_trigger_R, breakeven_trigger_R, partial_profit_fraction)
are read from StrategyConfig (CFG-01) — no literals in this file.

Transitions judged on bar CLOSE only:
  - Stop trigger: close <= trail_stop  (D-02, never on bar.low/wick)
  - Profit milestone: close >= threshold  (D-03, symmetric with D-02)

D-11 (never loosen stop): evaluate_close applies max(trail_stop, new_swing_low);
a lower swing-low never reduces the stop.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


# ============================================================
# PositionPhase Enum
# ============================================================

class PositionPhase(str, Enum):
    """Lifecycle phases of a single position in the FSM.

    str, Enum allows phase.value to equal the phase name string — compatible
    with SQLite TEXT storage and PositionManager comparisons.
    """

    AWAITING_FILL = "AWAITING_FILL"  # entry order placed; no fill confirmed
    ACTIVE        = "ACTIVE"         # entry filled; initial stop monitoring
    PARTIAL_TAKEN = "PARTIAL_TAKEN"  # 1/3 off at 0.75R (POS-01)
    BREAKEVEN     = "BREAKEVEN"      # stop moved to entry at 1.0R (POS-02)
    TRAILING      = "TRAILING"       # 5m swing-low trail active (POS-03)
    CLOSED        = "CLOSED"         # all shares exited


# ============================================================
# FSMAction — action names returned by evaluate_close
# ============================================================

# String constants for the action returned by evaluate_close(). Callers
# (PositionManager) switch on these to determine what broker action to take.
FSM_ACTION_NONE       = "NONE"         # no transition triggered this bar
FSM_ACTION_STOP_OUT   = "STOP_OUT"     # close <= trail_stop — exit all remaining
FSM_ACTION_PARTIAL    = "PARTIAL"      # close >= entry + 0.75R — sell partial qty
FSM_ACTION_BREAKEVEN  = "BREAKEVEN"    # close >= entry + 1.0R — move stop to entry
FSM_ACTION_TRAIL_UP   = "TRAIL_UP"     # trail_stop ratcheted up by new swing-low


# ============================================================
# PositionState Dataclass
# ============================================================

@dataclass
class PositionState:
    """Per-position FSM state (Phase 4).

    Persisted to StateStore (positions table) on every transition — the DB row
    is the source of truth on restart (D-09, D-11). Pure Python; no broker,
    no asyncio, no DB access inside this class.

    Fields:
        position_id: Unique position identifier (UUID or broker-assigned ID).
        code: Moomoo-format stock code (e.g. "US.AAPL").
        phase: Current FSM phase (PositionPhase enum).
        entry_price: Confirmed avg fill price for the entry (set in AWAITING_FILL
                     → ACTIVE via apply_entry_fill; D-06).
        initial_stop: Initial stop price (LOD − 1%); used to compute R throughout
                      the lifecycle (set on position creation, never changes).
        trail_stop: Current stop price (starts == initial_stop; ratchets up via
                    max() on swing-low updates; never decreases — D-11).
        full_quantity: Total shares filled at entry (set at first fill; D-06).
        remaining_quantity: Shares still open (decremented on each partial exit).
        entry_order_id: Broker order_id from place_order() — fill reconciliation
                        key (EXEC-05).
        exit_order_id: Broker order_id for any current open exit order (nullable).
        avg_fill_price: Average fill price for the entry (same as entry_price after
                        AWAITING_FILL → ACTIVE; tracked separately for audit).
        opened_at: UTC datetime when the entry fill was confirmed.
        updated_at: UTC datetime of the last FSM transition (set by PositionManager).
    """

    position_id: str
    code: str
    phase: PositionPhase
    entry_price: float
    initial_stop: float
    trail_stop: float
    full_quantity: int
    remaining_quantity: int
    entry_order_id: str
    exit_order_id: Optional[str] = None
    avg_fill_price: Optional[float] = None
    opened_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # In-memory FSM exit cause (never persisted — DB upsert column list is fixed).
    # Set at the FSM trigger point before the exit fill arrives so the alerter
    # can pass the real reason (not a hardcoded constant) to on_exit_alert (ALERT-02).
    pending_exit_reason: Optional[str] = None

    # ============================================================
    # Phase 4 FSM Methods
    # ============================================================

    def apply_entry_fill(self, fill) -> None:
        """Advance AWAITING_FILL -> ACTIVE on a confirmed entry fill.

        Sets entry_price and full_quantity/remaining_quantity from the fill.
        The fill's avg_fill_price is the definitive entry price for all R math
        throughout the lifecycle (D-06: partial entry fill → keep as position).

        State is mutated in-place; PositionManager must persist to DB AFTER
        calling this method (Pitfall G: DB-first write pattern).

        Args:
            fill: A FillEvent (bot.execution.events). Not type-hinted here to
                  avoid a runtime import cycle — callers must pass the correct type.

        Raises:
            ValueError: if the current phase is not AWAITING_FILL.
        """
        if self.phase != PositionPhase.AWAITING_FILL:
            raise ValueError(
                f"apply_entry_fill called in phase {self.phase}; expected AWAITING_FILL"
            )
        self.entry_price = fill.avg_fill_price
        self.avg_fill_price = fill.avg_fill_price
        self.full_quantity = fill.filled_qty
        self.remaining_quantity = fill.filled_qty
        self.phase = PositionPhase.ACTIVE

    def evaluate_close(
        self,
        close: float,
        cfg,
        new_swing_low: Optional[float] = None,
    ) -> tuple:
        """Evaluate a bar-close price against the current FSM state.

        Implements the full Trend Join Long exit logic:
          - ACTIVE:        stop check + 0.75R partial trigger (POS-01)
          - PARTIAL_TAKEN: stop check + 1.0R breakeven trigger (POS-02)
          - BREAKEVEN/TRAILING: stop check + swing-low trail ratchet (POS-03)
          - CLOSED/AWAITING_FILL: no-op

        All checks use bar.close ONLY (D-02 for stops, D-03 for profit milestones).
        No bar.low or bar.high is referenced here.

        The stop is NEVER loosened — trail_stop = max(trail_stop, new_swing_low)
        (D-11). new_swing_low is computed by the caller (PositionManager via
        compute_swing_low_2_2); the FSM only applies the max() ratchet.

        All thresholds come from cfg (CFG-01 — no literals in this file).

        Args:
            close: The bar's closing price (float). Only this value drives transitions.
            cfg: StrategyConfig with fields:
                   partial_profit_trigger_r (float) — e.g. 0.75
                   partial_profit_fraction  (float) — e.g. 0.3333
                   breakeven_trigger_r      (float) — e.g. 1.0
            new_swing_low: Optional swing-low value computed by the caller.
                Only used in BREAKEVEN/TRAILING phases to ratchet the trail stop.
                If None, the stop is not updated this bar.

        Returns:
            (action, partial_qty) tuple where:
              action (str):      FSMAction constant (NONE, STOP_OUT, PARTIAL,
                                 BREAKEVEN, TRAIL_UP)
              partial_qty (int): Shares to sell for PARTIAL action
                                 (floor(remaining * fraction)); 0 for other actions.

        Notes:
            This method MUTATES self.phase and self.trail_stop on transitions.
            PositionManager must persist to DB AFTER calling this method.

            Decrement-ownership convention (handler-owns):
            evaluate_close is advisory-only on quantity. For PARTIAL, it computes
            and returns partial_qty but does NOT mutate remaining_quantity. The
            handler (_trigger_partial_profit) applies exactly ONE decrement driven
            by the broker's filled_qty. This is symmetric with STOP_OUT branches
            which also do not mutate remaining_quantity here.
        """
        if self.phase in (PositionPhase.AWAITING_FILL, PositionPhase.CLOSED):
            return (FSM_ACTION_NONE, 0)

        R = self.entry_price - self.initial_stop  # risk distance (always > 0)

        if self.phase == PositionPhase.ACTIVE:
            # D-02: stop check on close only
            if close <= self.trail_stop:
                self.phase = PositionPhase.CLOSED
                return (FSM_ACTION_STOP_OUT, self.remaining_quantity)
            # D-03: 0.75R partial profit trigger (POS-01)
            if close >= self.entry_price + cfg.partial_profit_trigger_r * R:
                partial_qty = math.floor(
                    self.remaining_quantity * cfg.partial_profit_fraction
                )
                # Handler-owns convention: do NOT decrement remaining_quantity here.
                # The handler (_trigger_partial_profit) applies the single decrement
                # driven by the broker's filled_qty. This is symmetric with the
                # STOP_OUT branches above (which also return without decrementing).
                self.phase = PositionPhase.PARTIAL_TAKEN
                return (FSM_ACTION_PARTIAL, partial_qty)

        elif self.phase == PositionPhase.PARTIAL_TAKEN:
            # D-02: stop check on close only
            if close <= self.trail_stop:
                self.phase = PositionPhase.CLOSED
                return (FSM_ACTION_STOP_OUT, self.remaining_quantity)
            # D-03: 1.0R breakeven trigger (POS-02)
            if close >= self.entry_price + cfg.breakeven_trigger_r * R:
                self.trail_stop = self.entry_price   # stop moves to entry (POS-02)
                self.phase = PositionPhase.BREAKEVEN
                return (FSM_ACTION_BREAKEVEN, 0)

        elif self.phase in (PositionPhase.BREAKEVEN, PositionPhase.TRAILING):
            # D-02: stop check on close only
            if close <= self.trail_stop:
                self.phase = PositionPhase.CLOSED
                return (FSM_ACTION_STOP_OUT, self.remaining_quantity)
            # POS-03 / D-11: ratchet trail stop on new swing-low (never loosen)
            if new_swing_low is not None:
                new_stop = max(self.trail_stop, new_swing_low)
                if new_stop > self.trail_stop:
                    self.trail_stop = new_stop
                    self.phase = PositionPhase.TRAILING
                    return (FSM_ACTION_TRAIL_UP, 0)

        return (FSM_ACTION_NONE, 0)
