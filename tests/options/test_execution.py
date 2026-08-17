#!/usr/bin/env python3
"""
tests/options/test_execution.py — LegExecutor (leg fills, ordering, unwind).

The gateway is a MagicMock with AsyncMock order methods; get_order_status
returns rows with exactly the real gateway keys. asyncio.sleep is NOT patched:
the TTL deadline is measured with loop.time(), so a no-op sleep would spin the
poll loop forever. The tiny cfg TTLs keep this file well under a second.
"""
import asyncio
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.options.execution import LegExecutor


SHORT = "US.SPY260320P600000"
WING = "US.SPY260320P594000"

QUOTES = {
    SHORT: {"bid": 2.00, "ask": 2.10},
    WING: {"bid": 0.50, "ask": 0.60},
}


# ============================================================
# Helpers
# ============================================================

def _run(coro):
    return asyncio.run(coro)


def _cfg(**over):
    cfg = SimpleNamespace(
        limit_buffer_usd=0.02,
        poll_interval_s=0.01,
        ttl_s=0.05,
        escalation_step_usd=0.03,
        max_retries=2,
    )
    for key, val in over.items():
        setattr(cfg, key, val)
    return cfg


def _row(order_id, dealt, qty=1, price=2.05, code=SHORT):
    """One order_list_query row — exactly the keys gateway.get_order_status returns."""
    return {
        "order_id": order_id, "code": code,
        "order_status": "FILLED_ALL" if dealt >= qty else "SUBMITTED",
        "qty": qty, "dealt_qty": dealt, "dealt_avg_price": price,
        "trd_side": "SELL",
    }


def _gw(dealt=1, qty=1, price=2.05):
    """Gateway whose every order fills `dealt` of `qty` on the first poll."""
    gw = MagicMock()
    ids = itertools.count(1)
    gw.place_order = AsyncMock(side_effect=lambda *a, **k: f"O{next(ids)}")
    gw.cancel_order = AsyncMock()
    gw.get_order_status = AsyncMock(
        side_effect=lambda oid: [_row(oid, dealt, qty, price)]
    )
    return gw


def _prices(gw):
    """Limit prices passed to place_order, in call order."""
    return [c.args[2] if len(c.args) > 2 else c.kwargs["price"]
            for c in gw.place_order.call_args_list]


# ============================================================
# fill_leg — happy path and pricing
# ============================================================

def test_fill_leg_returns_three_tuple_on_full_fill():
    gw = _gw(dealt=2, qty=2, price=2.07)
    ex = LegExecutor(gw, _cfg())

    result = _run(ex.fill_leg(SHORT, "SELL", 2, 2.00, 2.10))

    assert result == ("O1", 2.07, 2)
    gw.place_order.assert_awaited_once()
    gw.cancel_order.assert_not_awaited()


def test_buy_starts_at_mid_plus_buffer():
    gw = _gw()
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "BUY", 1, 2.00, 2.10))
    assert _prices(gw) == [2.07]        # mid 2.05 + 0.02


def test_sell_starts_at_mid_minus_buffer():
    gw = _gw()
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10))
    assert _prices(gw) == [2.03]        # mid 2.05 - 0.02


def test_aggressive_buy_starts_at_ask_and_sell_at_bid():
    gw = _gw()
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "BUY", 1, 2.00, 2.10, aggressive=True))
    assert _prices(gw) == [2.10]

    gw = _gw()
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10, aggressive=True))
    assert _prices(gw) == [2.00]


def test_limit_price_is_rounded_to_two_dp():
    gw = _gw()
    # mid = 2.0555 → 2.0755 → must be sent as 2.08, not 2.0755
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "BUY", 1, 2.001, 2.11))
    price = _prices(gw)[0]
    assert price == round(price, 2)
    assert price == 2.08


def test_limit_price_floored_at_one_cent():
    gw = _gw()
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 0.00, 0.01))
    assert _prices(gw) == [0.01]


def test_on_placed_fires_before_polling():
    gw = _gw()
    events = []
    gw.place_order = AsyncMock(side_effect=lambda *a, **k: events.append("place") or "O1")
    gw.get_order_status = AsyncMock(
        side_effect=lambda oid: events.append("poll") or [_row(oid, 1, 1)]
    )

    async def on_placed(order_id):
        events.append(f"placed:{order_id}")

    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10, on_placed=on_placed))
    assert events[:3] == ["place", "placed:O1", "poll"]


# ============================================================
# fill_leg — escalation, give-up, partials
# ============================================================

def test_buy_escalates_upward_then_gives_up():
    gw = _gw(dealt=0)
    ex = LegExecutor(gw, _cfg())

    result = _run(ex.fill_leg(SHORT, "BUY", 1, 2.00, 2.10))

    assert result is None
    assert _prices(gw) == [2.07, 2.10, 2.13]           # +0.03 per retry
    assert gw.place_order.await_count == 3             # max_retries(2) + 1
    assert gw.cancel_order.await_count == 3            # no resting order left behind


def test_sell_escalates_downward():
    gw = _gw(dealt=0)
    _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10))
    assert _prices(gw) == [2.03, 2.00, 1.97]


def test_cancel_failure_does_not_break_the_loop():
    gw = _gw(dealt=0)
    gw.cancel_order = AsyncMock(side_effect=RuntimeError("already filled"))
    assert _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10)) is None


def test_partial_fill_at_ttl_is_cancelled_and_returned():
    gw = _gw(dealt=1, qty=3, price=2.04)
    ex = LegExecutor(gw, _cfg())

    result = _run(ex.fill_leg(SHORT, "SELL", 3, 2.00, 2.10))

    assert result == ("O1", 2.04, 1)
    gw.cancel_order.assert_awaited_once_with("O1")
    gw.place_order.assert_awaited_once()               # partial stops the escalation


def test_fill_on_a_later_poll_still_returns():
    gw = _gw()
    polls = itertools.count()
    gw.get_order_status = AsyncMock(
        side_effect=lambda oid: [_row(oid, 1 if next(polls) >= 2 else 0, 1)]
    )
    result = _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10))
    assert result[2] == 1
    gw.cancel_order.assert_not_awaited()


def test_unknown_order_id_rows_are_ignored():
    gw = _gw()
    gw.get_order_status = AsyncMock(side_effect=lambda oid: [_row("SOMEONE-ELSE", 5, 1)])
    assert _run(LegExecutor(gw, _cfg()).fill_leg(SHORT, "SELL", 1, 2.00, 2.10)) is None


# ============================================================
# open_position
# ============================================================

def _legs():
    """pick_strikes output shape: long wing first, then the short."""
    return [
        {"code": WING, "right": "P", "strike": 594.0, "side": "BUY", "mid": 0.55},
        {"code": SHORT, "right": "P", "strike": 600.0, "side": "SELL", "mid": 2.05},
    ]


def test_open_position_buys_wing_first_and_persists_before_next_leg():
    gw = _gw()
    events = []
    ids = itertools.count(1)

    async def _place(code, qty, price, trd_side):
        events.append(("place", code))
        return f"O{next(ids)}"

    gw.place_order = AsyncMock(side_effect=_place)

    async def on_leg_placed(leg, order_id):
        events.append(("persisted", leg["code"]))

    filled = _run(LegExecutor(gw, _cfg()).open_position(
        _legs(), 1, QUOTES, on_leg_placed=on_leg_placed,
    ))

    assert [leg["code"] for leg in filled] == [WING, SHORT]
    assert events == [
        ("place", WING), ("persisted", WING),          # long wing bought first...
        ("place", SHORT), ("persisted", SHORT),        # ...and persisted before the short
    ]


def test_open_position_returns_enriched_legs():
    gw = _gw(dealt=2, qty=2, price=1.11)
    filled = _run(LegExecutor(gw, _cfg()).open_position(_legs(), 2, QUOTES))
    assert filled[0]["order_id"] == "O1"
    assert filled[0]["entry_price"] == 1.11
    assert filled[0]["filled_qty"] == 2
    assert filled[0]["strike"] == 594.0                # original leg fields survive


def test_open_position_fires_on_leg_filled():
    gw = _gw()
    seen = []

    async def on_leg_filled(leg, order_id, price, filled_qty):
        seen.append((leg["code"], order_id, filled_qty))

    _run(LegExecutor(gw, _cfg()).open_position(
        _legs(), 1, QUOTES, on_leg_filled=on_leg_filled,
    ))
    assert [s[0] for s in seen] == [WING, SHORT]


def test_short_leg_failure_unwinds_the_wing_aggressively():
    gw = _gw()
    ids = itertools.count(1)

    async def _place(code, qty, price, trd_side):
        return f"O{next(ids)}"

    gw.place_order = AsyncMock(side_effect=_place)

    # The wing (BUY) fills; the short leg (SELL) never does.
    def _status(oid):
        dealt = 0 if oid in ("O2", "O3", "O4") else 1
        return [_row(oid, dealt, 1)]

    gw.get_order_status = AsyncMock(side_effect=_status)

    result = _run(LegExecutor(gw, _cfg()).open_position(_legs(), 1, QUOTES))

    assert result is None
    # After the 3 short-leg attempts (O2-O4) the wing is sold back aggressively:
    # a SELL-to-close starting at the bid, not mid - buffer.
    unwind = gw.place_order.call_args_list[-1]
    assert unwind.args[0] == WING
    assert unwind.args[2] == 0.50                      # bid → aggressive


def test_partially_filled_leg_is_treated_as_failure_and_unwound():
    gw = _gw(dealt=1, qty=2, price=0.55)               # every order half-fills
    ex = LegExecutor(gw, _cfg())

    result = _run(ex.open_position(_legs(), 2, QUOTES))

    assert result is None
    # The half-filled wing is closed for the qty that actually filled (1).
    unwind = gw.place_order.call_args_list[-1]
    assert unwind.args[0] == WING
    assert unwind.args[1] == 1


def test_first_leg_failure_unwinds_nothing_and_returns_none():
    gw = _gw(dealt=0)
    assert _run(LegExecutor(gw, _cfg()).open_position(_legs(), 1, QUOTES)) is None
    # 3 attempts on the wing only — the short leg is never placed.
    assert {c.args[0] for c in gw.place_order.call_args_list} == {WING}


# ============================================================
# close_legs
# ============================================================

def _filled_legs():
    return [
        {"code": WING, "side": "BUY", "qty": 1, "filled_qty": 1},
        {"code": SHORT, "side": "SELL", "qty": 1, "filled_qty": 1},
    ]


def test_close_legs_buys_back_shorts_before_selling_wings():
    gw = _gw()
    ok = _run(LegExecutor(gw, _cfg()).close_legs(_filled_legs(), QUOTES))

    assert ok is True
    codes = [c.args[0] for c in gw.place_order.call_args_list]
    assert codes == [SHORT, WING]                      # never naked-short


def test_close_legs_inverts_each_side():
    gw = _gw()
    _run(LegExecutor(gw, _cfg()).close_legs(_filled_legs(), QUOTES))
    # short (SELL) closed by BUYing: mid 2.05 + buffer; wing (BUY) closed by
    # SELLing: mid 0.55 - buffer
    assert _prices(gw) == [2.07, 0.53]


def test_close_legs_false_when_a_leg_does_not_close():
    gw = _gw()
    ids = itertools.count(1)
    gw.place_order = AsyncMock(side_effect=lambda *a, **k: f"O{next(ids)}")
    # First order (the short buy-back) never fills; the wing does.
    gw.get_order_status = AsyncMock(
        side_effect=lambda oid: [_row(oid, 0 if oid in ("O1", "O2", "O3") else 1, 1)]
    )

    ok = _run(LegExecutor(gw, _cfg()).close_legs(_filled_legs(), QUOTES))

    assert ok is False
    # The wing was still attempted — a half-closed spread is worse than trying.
    assert WING in [c.args[0] for c in gw.place_order.call_args_list]


def test_close_legs_uses_filled_qty_then_qty():
    gw = _gw(dealt=3, qty=3)
    legs = [{"code": SHORT, "side": "SELL", "qty": 5, "filled_qty": 3}]
    _run(LegExecutor(gw, _cfg()).close_legs(legs, QUOTES))
    assert gw.place_order.call_args_list[0].args[1] == 3

    gw = _gw(dealt=5, qty=5)
    legs = [{"code": SHORT, "side": "SELL", "qty": 5}]
    _run(LegExecutor(gw, _cfg()).close_legs(legs, QUOTES))
    assert gw.place_order.call_args_list[0].args[1] == 5


def test_close_legs_missing_quote_places_nothing_and_fails():
    gw = _gw()
    legs = [{"code": "US.XYZ260320P100000", "side": "SELL", "qty": 1}]
    assert _run(LegExecutor(gw, _cfg()).close_legs(legs, QUOTES)) is False
    gw.place_order.assert_not_awaited()


def test_close_legs_empty_is_true():
    gw = _gw()
    assert _run(LegExecutor(gw, _cfg()).close_legs([], QUOTES)) is True


def test_close_legs_callbacks():
    gw = _gw()
    placed, filled = [], []

    async def on_leg_placed(leg, order_id):
        placed.append(leg["code"])

    async def on_leg_filled(leg, order_id, price, filled_qty):
        filled.append((leg["code"], filled_qty))

    _run(LegExecutor(gw, _cfg()).close_legs(
        _filled_legs(), QUOTES, on_leg_placed=on_leg_placed, on_leg_filled=on_leg_filled,
    ))

    assert placed == [SHORT, WING]
    assert filled == [(SHORT, 1), (WING, 1)]


def test_cfg_drives_every_tunable():
    """No literals: doubling max_retries doubles the attempts."""
    gw = _gw(dealt=0)
    _run(LegExecutor(gw, _cfg(max_retries=4)).fill_leg(SHORT, "SELL", 1, 2.00, 2.10))
    assert gw.place_order.await_count == 5
