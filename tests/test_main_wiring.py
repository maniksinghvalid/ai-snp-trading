#!/usr/bin/env python3
"""
tests.test_main_wiring — Regression guard for the production wiring of
`gateway` into PositionManager (RISK-TICK-STOP, v1.0-MILESTONE-AUDIT Gap 1).

Phase 7 shipped `arm_stop_protection` correct but DEAD: bot/main.py never passed
`gateway=` into its `PositionManager(...)` construction, so `self._gateway` was
None on every live/paper run and `arm_stop_protection()` silently no-op'd. The
old unit tests missed it because they hand-built `PositionManager(gateway=mock)`
— the exact pattern that let the bug ship.

These tests instead drive bot.main.main()'s REAL construction sequence with only
I/O seams patched, capture the real PositionManager instance, and assert the
wiring holds (crit 1) and that arm dispatch reaches the gateway (crit 2).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.main
from bot.position.state import PositionPhase, PositionState


# Real event-loop runner, captured before any patch of bot.main.asyncio.run,
# so the arm-dispatch assertion actually executes the coroutine.
_real_asyncio_run = asyncio.run


def _drive_real_main(monkeypatch):
    """Run bot.main.main() with only the three I/O seams patched.

    Returns (captured_pm, mock_gateway): the REAL PositionManager instance built
    by main()'s production sequence, and the shared gateway mock that `gateway`
    (bot/main.py:75) bound to.
    """
    mock_gateway = AsyncMock()
    monkeypatch.setattr(bot.main, "MoomooGateway", lambda *a, **k: mock_gateway)
    monkeypatch.setattr(bot.main, "StateStore", MagicMock())

    def _noop_run(coro):
        # Never actually run the bot loop; close the coroutine so Python does
        # not emit a "coroutine was never awaited" warning.
        coro.close()

    monkeypatch.setattr(bot.main.asyncio, "run", _noop_run)

    captured = {}
    real_pm_cls = bot.main.PositionManager

    def _capturing_pm(*args, **kwargs):
        pm = real_pm_cls(*args, **kwargs)
        captured["pm"] = pm
        captured["gateway_kwarg"] = kwargs.get("gateway")
        return pm

    monkeypatch.setattr(bot.main, "PositionManager", _capturing_pm)

    bot.main.main()

    assert "pm" in captured, "bot.main.main() never constructed PositionManager"
    return captured["pm"], mock_gateway


def test_main_wires_gateway_into_position_manager(monkeypatch):
    """RISK-TICK-STOP crit 1: the real construction sequence leaves
    position_manager._gateway pointing at the same MoomooGateway instance the
    other engines received (not None)."""
    captured_pm, mock_gateway = _drive_real_main(monkeypatch)

    assert captured_pm._gateway is mock_gateway, (
        "bot/main.py did not pass gateway= into PositionManager(...); "
        "_gateway is None so arm_stop_protection() no-ops in production (Gap 1)."
    )


def test_armed_stop_dispatches_through_real_construction(monkeypatch):
    """RISK-TICK-STOP crit 2: arm_stop_protection() on the real-construction
    PositionManager dispatches to the gateway instead of no-op'ing.

    Shipped rules.json has use_broker_stop_orders=false, so the D-02 quote-tick
    path is exercised (gateway.subscribe_quote). If that flag is ever flipped to
    true, the asserted dispatch becomes gateway.place_stop_order.
    """
    captured_pm, mock_gateway = _drive_real_main(monkeypatch)

    pos = PositionState(
        position_id="POS-TEST-001",
        code="US.AAPL",
        phase=PositionPhase.ACTIVE,
        entry_price=100.0,
        initial_stop=98.0,
        trail_stop=98.0,
        full_quantity=300,
        remaining_quantity=300,
        entry_order_id="ORDER-ENTRY-001",
    )

    _real_asyncio_run(captured_pm.arm_stop_protection(pos))

    assert mock_gateway.subscribe_quote.called, (
        "arm_stop_protection() did not reach gateway.subscribe_quote — "
        "_gateway was None (unwired) so the D-02 tick-stop path is dead."
    )
