#!/usr/bin/env python3
"""
backtester.experimental.exits — pct_ladder / partial_be_trail / fixed_2r exit
models, long AND short.

Pure functions: no I/O, no broker, no SimulatedBarFeed. Each model shares the
signature `advance(position, bar, params) -> list[dict]`, where `position` is a
plain mutable dict the caller (backtester.experimental.engine.Engine) owns and
mutates across bars:
    side: "long" | "short"
    entry_price, initial_stop, stop: float (stop is mutated in place — never
        loosened in the adverse direction once past the initial value)
    full_quantity, open_quantity: int (open_quantity is mutated in place)
    state: str — model-specific FSM phase ("active", "partial_taken",
        "breakeven", "trailing", "closed"); mutated in place
    bars: list[dict] — session bar history so far (needs "low"/"high" keys),
        most recent last; only partial_be_trail's swing trail reads this
        (mirrors bot.position.manager's bar_buffer wiring)

Each call returns zero or one exit-leg instruction (at most ONE state
transition per bar, mirroring bot.position.state.PositionState.evaluate_close):
    {"qty": int, "reason": str, "at": "next_open"}
The caller fills every leg at feed.next_bar(...)'s open (the exit model never
computes a fill price itself — production-parity N+1-open convention).

R is always `abs(entry_price - initial_stop)`; direction is carried on
`position["side"]`, never inferred from the sign of the stop distance
(10-RESEARCH Pitfall 3).

Exports: pct_ladder, partial_be_trail, fixed_2r
"""
import math

from bot.strategy.indicators import swing_low_2_2
from backtester.experimental.indicators import swing_high_2_2

_LADDER_LEG_PCTS = [0.01, 0.02, 0.03]


def _sign(side: str) -> int:
    return 1 if side == "long" else -1


def _stop_hit(position: dict, close: float) -> bool:
    return close <= position["stop"] if position["side"] == "long" else close >= position["stop"]


def _close_all(position: dict, reason: str) -> list:
    qty = position["open_quantity"]
    position["open_quantity"] = 0
    position["state"] = "closed"
    return [{"qty": qty, "reason": reason, "at": "next_open"}]


def pct_ladder(position: dict, bar: dict, params: dict = None) -> list:
    """25% of the ENTRY quantity out at +1%/+2%/+3% from entry (short mirror:
    -1%/-2%/-3%), triggered on bar CLOSE reaching the level; the stop moves to
    entry (breakeven) after the FIRST take; the final 25% runner exits on the
    (breakeven) stop or a later force-close."""
    close = bar["close"]

    if _stop_hit(position, close):
        return _close_all(position, "stop")

    legs_taken = position.setdefault("legs_taken", 0)
    if legs_taken >= len(_LADDER_LEG_PCTS):
        return []

    sign = _sign(position["side"])
    pct = _LADDER_LEG_PCTS[legs_taken]
    level = position["entry_price"] * (1 + sign * pct)
    reached = close >= level if position["side"] == "long" else close <= level
    if not reached:
        return []

    leg_qty = min(
        math.floor(position["full_quantity"] * 0.25), position["open_quantity"]
    )
    position["open_quantity"] -= leg_qty
    position["legs_taken"] = legs_taken + 1
    if legs_taken == 0:
        position["stop"] = position["entry_price"]  # breakeven after the FIRST take
    return [{"qty": leg_qty, "reason": f"ladder_{legs_taken + 1}pct", "at": "next_open"}]


def partial_be_trail(position: dict, bar: dict, params: dict = None) -> list:
    """1/3 of entry qty out at 0.75R, stop to breakeven at 1.0R, then trail on
    swing_low_2_2 (long) / swing_high_2_2 (short) over the last <=50 bars of the
    session — never loosens the stop."""
    close = bar["close"]
    sign = _sign(position["side"])
    R = abs(position["entry_price"] - position["initial_stop"])
    phase = position.setdefault("state", "active")

    if phase in ("active", "partial_taken", "breakeven", "trailing") and _stop_hit(position, close):
        reason = "trail" if phase == "trailing" else "stop"
        return _close_all(position, reason)

    if phase == "active":
        target = position["entry_price"] + sign * 0.75 * R
        reached = close >= target if position["side"] == "long" else close <= target
        if reached:
            qty = min(
                math.floor(position["full_quantity"] / 3), position["open_quantity"]
            )
            position["open_quantity"] -= qty
            position["state"] = "partial_taken"
            return [{"qty": qty, "reason": "partial_075r", "at": "next_open"}]
        return []

    if phase == "partial_taken":
        target = position["entry_price"] + sign * 1.0 * R
        reached = close >= target if position["side"] == "long" else close <= target
        if reached:
            position["stop"] = position["entry_price"]
            position["state"] = "breakeven"
        return []

    if phase in ("breakeven", "trailing"):
        # The caller (Engine) may pass an already-computed pivot ("new_swing_low"/
        # "new_swing_high" -- mirrors bot.position.manager's own
        # _compute_swing_low-then-pass-in convention); fall back to computing it
        # from "bars" directly so this function stays independently testable
        # without an engine (Task 2 predates engine.py).
        if position["side"] == "long":
            pivot = position.get("new_swing_low")
            if pivot is None:
                bars = position.get("bars", [])[-50:]
                pivot = swing_low_2_2([b["low"] for b in bars])
            if pivot is not None and pivot > position["stop"]:
                position["stop"] = pivot
                position["state"] = "trailing"
        else:
            pivot = position.get("new_swing_high")
            if pivot is None:
                bars = position.get("bars", [])[-50:]
                pivot = swing_high_2_2([b["high"] for b in bars])
            if pivot is not None and pivot < position["stop"]:
                position["stop"] = pivot
                position["state"] = "trailing"
        return []

    return []


def fixed_2r(position: dict, bar: dict, params: dict = None) -> list:
    """Stop to breakeven at 1.0R; all remaining qty out at 2.0R."""
    close = bar["close"]
    sign = _sign(position["side"])
    R = abs(position["entry_price"] - position["initial_stop"])
    phase = position.setdefault("state", "active")

    if _stop_hit(position, close):
        return _close_all(position, "stop")

    target_2r = position["entry_price"] + sign * 2.0 * R
    reached_2r = close >= target_2r if position["side"] == "long" else close <= target_2r
    if reached_2r:
        return _close_all(position, "target_2r")

    if phase == "active":
        target_1r = position["entry_price"] + sign * 1.0 * R
        reached_1r = close >= target_1r if position["side"] == "long" else close <= target_1r
        if reached_1r:
            position["stop"] = position["entry_price"]
            position["state"] = "breakeven"
    return []
