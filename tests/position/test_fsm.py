#!/usr/bin/env python3
"""
tests.position.test_fsm — Pure FSM unit tests for PositionState (Phase 4, POS-01..03).

Test names EXACTLY match the validation map in 04-VALIDATION.md.
All transitions are judged on bar.close only (D-02/D-03).
Implemented in 04-01 Task 2 (GREEN phase — these are the failing stubs here).
"""
import pytest


def test_partial_profit_trigger():
    """POS-01: 1/3 partial sell at 0.75R bar close; FSM ACTIVE->PARTIAL_TAKEN.

    Implemented in 04-01 Task 2. With entry_price=100, initial_stop=96 (R=4),
    a bar with close=103 (== 100 + 0.75*4) triggers PARTIAL; close=102.99 does not.
    Judged on close only (D-03) — no wick repaint.
    """
    assert False, "TODO: implemented in 04-01"


def test_breakeven_trigger():
    """POS-02: Stop moves to entry at 1.0R bar close; FSM PARTIAL_TAKEN->BREAKEVEN.

    Implemented in 04-01 Task 2. From PARTIAL_TAKEN, a bar with close>=entry+R
    transitions to BREAKEVEN and sets trail_stop==entry_price. Config-swap
    (breakeven_trigger_R=1.5) confirms threshold is read from cfg (CFG-01).
    """
    assert False, "TODO: implemented in 04-01"


def test_trail_never_loosens():
    """POS-03: Trail stop ratchets up on swing-low; never moves down (D-11).

    Implemented in 04-01 Task 2. In TRAILING phase, a swing-low BELOW current
    trail_stop leaves trail_stop unchanged; a swing-low ABOVE raises it.
    max(persisted, new) formula holds on simulated restart-resume (D-11).
    """
    assert False, "TODO: implemented in 04-01"
