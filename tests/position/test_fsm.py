#!/usr/bin/env python3
"""
tests.position.test_fsm — Pure FSM unit tests for PositionState (Phase 4, POS-01..03).

All transitions are judged on bar.close only (D-02/D-03). No broker, no asyncio,
no DB. Thresholds come from cfg; no literals in the FSM (CFG-01).

Test names EXACTLY match the validation map in 04-VALIDATION.md.
"""
import math
from datetime import datetime

import pytest

from bot.position.state import (
    PositionPhase,
    PositionState,
    FSM_ACTION_NONE,
    FSM_ACTION_STOP_OUT,
    FSM_ACTION_PARTIAL,
    FSM_ACTION_BREAKEVEN,
    FSM_ACTION_TRAIL_UP,
)


# ============================================================
# Helpers
# ============================================================

class _Cfg:
    """Minimal StrategyConfig-compatible namespace for FSM testing."""
    def __init__(self, partial_r=0.75, be_r=1.0, frac=0.3333):
        self.partial_profit_trigger_r = partial_r
        self.breakeven_trigger_r = be_r
        self.partial_profit_fraction = frac


def _make_pos(
    entry_price=100.0,
    initial_stop=96.0,
    trail_stop=None,
    remaining_quantity=100,
    full_quantity=100,
    phase=PositionPhase.ACTIVE,
):
    """Build a PositionState in ACTIVE state for FSM testing."""
    return PositionState(
        position_id="POS-001",
        code="US.AAPL",
        phase=phase,
        entry_price=entry_price,
        initial_stop=initial_stop,
        trail_stop=trail_stop if trail_stop is not None else initial_stop,
        full_quantity=full_quantity,
        remaining_quantity=remaining_quantity,
        entry_order_id="ORDER-001",
    )


# ============================================================
# POS-01: Partial profit trigger at 0.75R
# ============================================================

def test_partial_profit_trigger():
    """POS-01: 1/3 partial sell at 0.75R bar close; FSM ACTIVE->PARTIAL_TAKEN.

    With entry_price=100, initial_stop=96 (R=4):
      trigger_price = 100 + 0.75 * 4 = 103.0

    close=103.0 => triggers PARTIAL; close=102.99 => does NOT trigger.
    Judged on close only (D-03) — no wick repaint.
    """
    cfg = _Cfg(partial_r=0.75, frac=0.3333)

    # --- exact-threshold: should trigger ---
    pos = _make_pos(entry_price=100.0, initial_stop=96.0)
    action, qty = pos.evaluate_close(103.0, cfg)
    assert action == FSM_ACTION_PARTIAL, f"Expected PARTIAL at 103.0, got {action}"
    assert pos.phase == PositionPhase.PARTIAL_TAKEN
    assert qty == math.floor(100 * 0.3333)   # 33

    # --- one cent below threshold: should NOT trigger ---
    pos2 = _make_pos(entry_price=100.0, initial_stop=96.0)
    action2, qty2 = pos2.evaluate_close(102.99, cfg)
    assert action2 == FSM_ACTION_NONE, f"Expected NONE at 102.99, got {action2}"
    assert pos2.phase == PositionPhase.ACTIVE
    assert qty2 == 0

    # --- low below stop but CLOSE above stop: no stop-out (D-02 close-only rule) ---
    pos3 = _make_pos(entry_price=100.0, initial_stop=96.0, trail_stop=96.0)
    # bar.low could be 95 (below stop), bar.close=100.5 (above stop) — FSM uses close only
    action3, _ = pos3.evaluate_close(100.5, cfg)  # close well above stop
    assert action3 == FSM_ACTION_NONE, "Bar low below stop must NOT trigger if close is above stop"

    # --- stop check uses close (D-02): close <= trail_stop => STOP_OUT ---
    pos4 = _make_pos(entry_price=100.0, initial_stop=96.0, trail_stop=96.0)
    action4, qty4 = pos4.evaluate_close(96.0, cfg)  # close == stop => STOP_OUT
    assert action4 == FSM_ACTION_STOP_OUT
    assert pos4.phase == PositionPhase.CLOSED
    assert qty4 == 100  # all remaining shares

    # --- remaining_quantity decrements on PARTIAL ---
    pos5 = _make_pos(entry_price=100.0, initial_stop=96.0, remaining_quantity=90)
    action5, qty5 = pos5.evaluate_close(103.0, cfg)
    assert action5 == FSM_ACTION_PARTIAL
    expected_partial = math.floor(90 * 0.3333)   # 29
    assert qty5 == expected_partial
    assert pos5.remaining_quantity == 90 - expected_partial


# ============================================================
# POS-02: Breakeven trigger at 1.0R
# ============================================================

def test_breakeven_trigger():
    """POS-02: Stop moves to entry at 1.0R bar close; FSM PARTIAL_TAKEN->BREAKEVEN.

    With entry_price=100, initial_stop=96 (R=4):
      breakeven_price = 100 + 1.0 * 4 = 104.0

    close=104.0 => BREAKEVEN + trail_stop == entry_price (100.0).
    close=103.99 => does NOT trigger breakeven.

    Config-swap test: breakeven_trigger_r=1.5 shifts the threshold to 106.0,
    proving that no literal is hardcoded in state.py (CFG-01).
    """
    cfg = _Cfg(partial_r=0.75, be_r=1.0, frac=0.3333)

    # --- exact breakeven threshold: PARTIAL_TAKEN -> BREAKEVEN ---
    pos = _make_pos(phase=PositionPhase.PARTIAL_TAKEN, entry_price=100.0, initial_stop=96.0)
    action, qty = pos.evaluate_close(104.0, cfg)
    assert action == FSM_ACTION_BREAKEVEN, f"Expected BREAKEVEN at 104.0, got {action}"
    assert pos.phase == PositionPhase.BREAKEVEN
    assert pos.trail_stop == 100.0, (
        f"trail_stop must be entry_price=100.0 after breakeven, got {pos.trail_stop}"
    )
    assert qty == 0  # no additional shares sold at breakeven

    # --- one cent below threshold: should NOT trigger ---
    pos2 = _make_pos(phase=PositionPhase.PARTIAL_TAKEN, entry_price=100.0, initial_stop=96.0)
    action2, qty2 = pos2.evaluate_close(103.99, cfg)
    assert action2 == FSM_ACTION_NONE, f"Expected NONE at 103.99, got {action2}"
    assert pos2.phase == PositionPhase.PARTIAL_TAKEN

    # --- CONFIG SWAP: breakeven_trigger_r=1.5 -> threshold=106.0 (CFG-01 proof) ---
    cfg_alt = _Cfg(partial_r=0.75, be_r=1.5, frac=0.3333)
    # close=104.0 should NOT trigger with be_r=1.5 (threshold is 106.0)
    pos3 = _make_pos(phase=PositionPhase.PARTIAL_TAKEN, entry_price=100.0, initial_stop=96.0)
    action3, _ = pos3.evaluate_close(104.0, cfg_alt)
    assert action3 == FSM_ACTION_NONE, (
        "breakeven_trigger_r=1.5 means 104.0 should NOT trigger breakeven"
    )
    # close=106.0 SHOULD trigger with be_r=1.5
    pos4 = _make_pos(phase=PositionPhase.PARTIAL_TAKEN, entry_price=100.0, initial_stop=96.0)
    action4, _ = pos4.evaluate_close(106.0, cfg_alt)
    assert action4 == FSM_ACTION_BREAKEVEN, (
        "breakeven_trigger_r=1.5 means 106.0 should trigger breakeven"
    )
    assert pos4.trail_stop == 100.0, "trail_stop must be entry_price after breakeven"

    # --- stop still active in PARTIAL_TAKEN: close <= trail_stop => STOP_OUT ---
    pos5 = _make_pos(phase=PositionPhase.PARTIAL_TAKEN, entry_price=100.0,
                     initial_stop=96.0, trail_stop=96.0)
    action5, qty5 = pos5.evaluate_close(95.0, cfg)
    assert action5 == FSM_ACTION_STOP_OUT
    assert pos5.phase == PositionPhase.CLOSED


# ============================================================
# POS-03: Trail stop ratchet — never loosens (D-11)
# ============================================================

def test_trail_never_loosens():
    """POS-03: Trail stop ratchets up on swing-low; never moves down (D-11).

    In BREAKEVEN/TRAILING phase, evaluate_close(close, cfg, new_swing_low=X) must:
      - If X > trail_stop: raise trail_stop to X (TRAIL_UP action)
      - If X <= trail_stop: leave trail_stop unchanged (NONE action)
      - Stop check still fires first: close <= trail_stop => STOP_OUT regardless

    D-11 restart simulation: loading a persisted trail_stop and applying a
    lower swing-low must never produce a looser stop.
    """
    cfg = _Cfg()

    # --- Start in BREAKEVEN with trail_stop == entry_price (100.0) ---
    pos = _make_pos(
        phase=PositionPhase.BREAKEVEN,
        entry_price=100.0,
        initial_stop=96.0,
        trail_stop=100.0,   # set to entry at breakeven (POS-02)
    )

    # (a) Swing-low ABOVE current trail (ratchet up: D-11) —
    # close is above stop so no stop-out; new_swing_low=101.5 > trail_stop=100.0
    action, _ = pos.evaluate_close(105.0, cfg, new_swing_low=101.5)
    assert action == FSM_ACTION_TRAIL_UP, f"Expected TRAIL_UP, got {action}"
    assert pos.trail_stop == 101.5, f"trail_stop should be 101.5, got {pos.trail_stop}"
    assert pos.phase == PositionPhase.TRAILING

    # (b) Swing-low BELOW current trail (never loosen — D-11)
    # new_swing_low=100.0 < trail_stop=101.5 => trail_stop stays at 101.5
    action2, _ = pos.evaluate_close(106.0, cfg, new_swing_low=100.0)
    assert action2 == FSM_ACTION_NONE, (
        f"A lower swing-low must not move trail_stop down (D-11); action={action2}"
    )
    assert pos.trail_stop == 101.5, (
        f"trail_stop must remain 101.5 when new_swing_low=100.0 < 101.5 (D-11)"
    )

    # (c) Swing-low further ABOVE (continue ratcheting up)
    action3, _ = pos.evaluate_close(108.0, cfg, new_swing_low=103.0)
    assert action3 == FSM_ACTION_TRAIL_UP
    assert pos.trail_stop == 103.0

    # (d) Stop check fires on close: close=103.0 (== trail_stop) => STOP_OUT
    action4, qty4 = pos.evaluate_close(103.0, cfg)
    assert action4 == FSM_ACTION_STOP_OUT
    assert pos.phase == PositionPhase.CLOSED
    assert qty4 == 100  # all remaining

    # --- D-11 restart simulation: persisted trail_stop survives lower swing-low ---
    # Simulate: bot restarts, loads trail_stop=105.0 from DB (the persisted value),
    # and immediately receives a stale/lower swing-low=102.0 from re-subscribed bars.
    # The stop must NEVER drop from 105.0 to 102.0.
    pos_restart = _make_pos(
        phase=PositionPhase.TRAILING,
        entry_price=100.0,
        initial_stop=96.0,
        trail_stop=105.0,   # persisted value from before restart
    )
    action_restart, _ = pos_restart.evaluate_close(
        110.0,              # close above stop — no stop-out
        cfg,
        new_swing_low=102.0,  # stale/lower swing-low from history replay
    )
    assert action_restart == FSM_ACTION_NONE, (
        "After restart, a lower swing-low than persisted trail_stop must be ignored (D-11)"
    )
    assert pos_restart.trail_stop == 105.0, (
        f"Persisted trail_stop=105.0 must not loosen to 102.0 on restart (D-11)"
    )

    # (e) D-02: close above trail_stop, no swing_low provided => NONE (not STOP_OUT)
    pos2 = _make_pos(
        phase=PositionPhase.TRAILING,
        entry_price=100.0,
        initial_stop=96.0,
        trail_stop=103.0,
    )
    action5, _ = pos2.evaluate_close(108.0, cfg, new_swing_low=None)
    assert action5 == FSM_ACTION_NONE, (
        "close above trail_stop with no new swing-low should return NONE"
    )
    assert pos2.trail_stop == 103.0, "trail_stop must not change if no swing_low supplied"
