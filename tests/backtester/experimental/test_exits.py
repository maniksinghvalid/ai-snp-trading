#!/usr/bin/env python3
"""
tests.backtester.experimental.test_exits — pct_ladder / partial_be_trail /
fixed_2r exit-model cases (Phase 10, plan 10-02 Task 2).

Every test drives advance() bar-by-bar directly (no engine.py yet — that's
Task 3) and resolves each returned leg's fill at the NEXT bar's open (the
same N+1 convention the engine will use), accumulating the qty-weighted
blended exit price/reason in the test body itself.

Plain def test_* functions, no pytest markers (project convention).
"""
import pytest

from backtester.experimental.exits import fixed_2r, partial_be_trail, pct_ladder


def _bar(close, low=None, high=None):
    low = close if low is None else low
    high = close if high is None else high
    return {"close": close, "low": low, "high": high}


# ============================================================
# pct_ladder — blended exit_price=101.5625, n_legs=4 (design-doc literal)
# ============================================================

def test_pct_ladder_long_blended_exit_price_and_n_legs():
    position = {
        "side": "long", "entry_price": 100.0, "initial_stop": 98.0, "stop": 98.0,
        "full_quantity": 400, "open_quantity": 400,
    }
    # next-bar opens used to resolve each leg's fill (N+1 convention)
    next_opens = {0: 101.25, 1: 102.00, 2: 103.00, 3: 100.00}

    legs = []

    # Bar 1: close crosses +1% (101.00) -> leg 1, stop -> breakeven (100.0)
    out = pct_ladder(position, _bar(close=101.00))
    assert len(out) == 1 and out[0]["reason"] == "ladder_1pct"
    legs.append((out[0]["qty"], next_opens[0]))
    assert position["stop"] == pytest.approx(100.0)

    # Bar 2: close crosses +2% (102.00) -> leg 2
    out = pct_ladder(position, _bar(close=102.00))
    assert len(out) == 1 and out[0]["reason"] == "ladder_2pct"
    legs.append((out[0]["qty"], next_opens[1]))

    # Bar 3: close crosses +3% (103.00) -> leg 3
    out = pct_ladder(position, _bar(close=103.00))
    assert len(out) == 1 and out[0]["reason"] == "ladder_3pct"
    legs.append((out[0]["qty"], next_opens[2]))

    # Bar 4: price falls back to the breakeven stop (100.0) -> runner exits
    out = pct_ladder(position, _bar(close=100.00))
    assert len(out) == 1 and out[0]["reason"] == "stop"
    legs.append((out[0]["qty"], next_opens[3]))

    assert position["open_quantity"] == 0
    n_legs = len(legs)
    total_qty = sum(qty for qty, _ in legs)
    blended_exit_price = sum(qty * price for qty, price in legs) / total_qty

    assert n_legs == 4
    assert total_qty == 400
    assert blended_exit_price == pytest.approx(101.5625)

    R = abs(position["entry_price"] - position["initial_stop"])
    r_multiple = (blended_exit_price - position["entry_price"]) / R
    assert r_multiple == pytest.approx(0.78125)


def test_pct_ladder_short_mirror_levels():
    position = {
        "side": "short", "entry_price": 100.0, "initial_stop": 102.0, "stop": 102.0,
        "full_quantity": 4, "open_quantity": 4,
    }
    out = pct_ladder(position, _bar(close=99.0))  # -1% level
    assert out[0]["reason"] == "ladder_1pct"
    assert position["stop"] == pytest.approx(100.0)  # breakeven after first take
    assert position["open_quantity"] == 3


def test_pct_ladder_no_signal_before_any_level_reached():
    position = {
        "side": "long", "entry_price": 100.0, "initial_stop": 98.0, "stop": 98.0,
        "full_quantity": 400, "open_quantity": 400,
    }
    assert pct_ladder(position, _bar(close=100.5)) == []
    assert position["open_quantity"] == 400


# ============================================================
# partial_be_trail — short math (Pitfall 3 sign proof) + never-loosens trail
# ============================================================

def test_partial_be_trail_short_075r_and_1r_breakeven():
    position = {
        "side": "short", "entry_price": 100.0, "initial_stop": 102.0, "stop": 102.0,
        "full_quantity": 300, "open_quantity": 300,
    }
    # R = |100-102| = 2; 0.75R level (short, price moves DOWN) = 100 - 1.5 = 98.5
    out = partial_be_trail(position, _bar(close=98.5))
    assert len(out) == 1
    assert out[0]["reason"] == "partial_075r"
    assert out[0]["qty"] == 100  # floor(300/3)
    assert position["open_quantity"] == 200
    assert position["state"] == "partial_taken"

    # 1.0R level = 100 - 2.0 = 98.0 -> stop moves to breakeven (entry = 100.0)
    out = partial_be_trail(position, _bar(close=98.0))
    assert out == []
    assert position["stop"] == pytest.approx(100.0)
    assert position["state"] == "breakeven"

    # A profitable short's stop-out from here would produce a POSITIVE r_multiple
    # contribution: exit(98.0) below entry(100.0), sign=-1 for short.
    exit_price = 98.0
    R = abs(position["entry_price"] - position["initial_stop"])
    sign = -1
    r_multiple = sign * (exit_price - position["entry_price"]) / R
    assert r_multiple > 0


def test_partial_be_trail_never_loosens_stop_on_worse_swing():
    position = {
        "side": "long", "entry_price": 100.0, "initial_stop": 95.0, "stop": 100.0,
        "full_quantity": 300, "open_quantity": 200, "state": "breakeven",
        "bars": [
            {"low": 101.0, "high": 102.0}, {"low": 100.5, "high": 101.5},
            {"low": 99.0, "high": 100.0},  # a WORSE (lower) swing low than the current stop
            {"low": 101.2, "high": 102.2}, {"low": 100.8, "high": 101.8},
        ],
    }
    out = partial_be_trail(position, _bar(close=101.0))
    assert out == []
    assert position["stop"] == pytest.approx(100.0)  # unchanged -- never loosened downward


def test_partial_be_trail_active_stop_out_before_any_partial():
    position = {
        "side": "long", "entry_price": 100.0, "initial_stop": 98.0, "stop": 98.0,
        "full_quantity": 300, "open_quantity": 300,
    }
    out = partial_be_trail(position, _bar(close=97.5))
    assert out == [{"qty": 300, "reason": "stop", "at": "next_open"}]
    assert position["open_quantity"] == 0
    assert position["state"] == "closed"


# ============================================================
# fixed_2r — breakeven at 1R, full exit at 2R
# ============================================================

def test_fixed_2r_breakeven_then_target_exit():
    position = {
        "side": "long", "entry_price": 100.0, "initial_stop": 98.0, "stop": 98.0,
        "full_quantity": 200, "open_quantity": 200,
    }
    # 1R = 102.0 -> stop to breakeven, no exit leg
    out = fixed_2r(position, _bar(close=102.0))
    assert out == []
    assert position["stop"] == pytest.approx(100.0)
    assert position["state"] == "breakeven"

    # 2R = 104.0 -> full exit
    out = fixed_2r(position, _bar(close=104.0))
    assert out == [{"qty": 200, "reason": "target_2r", "at": "next_open"}]
    assert position["open_quantity"] == 0


def test_fixed_2r_stop_out_before_1r():
    position = {
        "side": "short", "entry_price": 100.0, "initial_stop": 102.0, "stop": 102.0,
        "full_quantity": 150, "open_quantity": 150,
    }
    out = fixed_2r(position, _bar(close=102.5))
    assert out == [{"qty": 150, "reason": "stop", "at": "next_open"}]
    assert position["open_quantity"] == 0
