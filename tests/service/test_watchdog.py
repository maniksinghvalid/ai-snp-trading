#!/usr/bin/env python3
"""
tests/service/test_watchdog.py — RED unit stubs for OpenDWatchdog (SVC-02).

These tests target bot.service.watchdog.OpenDWatchdog which is implemented in plan 05-02.
They are xfail until that plan lands, so the suite collects cleanly and the
later wave has concrete verification targets to turn green.

Requirements covered:
  SVC-02 — Watchdog detects OpenD disconnect → disables entries + alerts
         → on reconnect runs startup_reconcile → re-enables entries
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
import asyncio


# ============================================================
# Helpers
# ============================================================

def _make_watchdog_with_mocks():
    """Return an OpenDWatchdog with all dependencies mocked.

    Raises ImportError (caught by xfail) until bot.service.watchdog is implemented.
    """
    from bot.service.watchdog import OpenDWatchdog  # noqa

    mock_gateway = MagicMock()
    mock_gateway.get_global_state = AsyncMock(
        return_value={"connected": True, "qot_logined": True, "trd_logined": True}
    )
    mock_gateway.startup_reconcile = AsyncMock()
    mock_bot = MagicMock()
    mock_bot._entries_enabled = True
    mock_alerter = MagicMock()
    mock_alerter.send = AsyncMock()
    mock_cfg = MagicMock()
    mock_cfg.watchdog_poll_interval_s = 60
    mock_cfg.watchdog_reconnect_initial_s = 5
    mock_cfg.watchdog_reconnect_cap_s = 60

    watchdog = OpenDWatchdog(
        gateway=mock_gateway,
        bot=mock_bot,
        alerter=mock_alerter,
        cfg=mock_cfg,
    )
    return watchdog, mock_gateway, mock_bot, mock_alerter


# ============================================================
# SVC-02: Watchdog behavior
# ============================================================

@pytest.mark.asyncio
async def test_disconnect_disables_entries():
    """Watchdog must set bot._entries_enabled = False on OpenD disconnect (SVC-02)."""
    watchdog, gateway_mock, bot_mock, _ = _make_watchdog_with_mocks()

    # Simulate disconnect: get_global_state returns connected=False
    gateway_mock.get_global_state = AsyncMock(
        return_value={"connected": False, "qot_logined": False, "trd_logined": False}
    )

    # Run one check cycle
    await watchdog._check_once()

    # entries must be disabled
    assert bot_mock._entries_enabled is False, (
        "Watchdog must disable entries on OpenD disconnect"
    )


@pytest.mark.asyncio
async def test_reconnect_reenables_entries():
    """On reconnect, watchdog must call startup_reconcile then re-enable entries (SVC-02)."""
    watchdog, gateway_mock, bot_mock, _ = _make_watchdog_with_mocks()

    # Step 1: disconnect cycle
    gateway_mock.get_global_state = AsyncMock(
        return_value={"connected": False, "qot_logined": False, "trd_logined": False}
    )
    await watchdog._check_once()
    assert bot_mock._entries_enabled is False

    # Step 2: reconnect cycle
    gateway_mock.get_global_state = AsyncMock(
        return_value={"connected": True, "qot_logined": True, "trd_logined": True}
    )
    await watchdog._check_once()

    # After reconnect: startup_reconcile called, entries re-enabled
    gateway_mock.startup_reconcile.assert_called()
    assert bot_mock._entries_enabled is True, (
        "Watchdog must re-enable entries after reconnect + reconcile"
    )


# ============================================================
# Finding 1.3: re-register bar handler after OpenD reconnect
# ============================================================

@pytest.mark.asyncio
async def test_reconnect_reregisters_bar_handler():
    """Regression 1.3: _on_reconnect must call gateway.set_handler(bot._bar_agg) after reconnect.

    After OpenD restarts, the new quote context must have the BarAggregator push
    handler re-registered so 5m bars resume flowing. Previously _on_reconnect
    re-subscribed feeds but never re-attached the bar handler, leaving the bot
    deaf to bars until the next manual restart.

    Assertions:
    - gateway.set_handler called exactly once with bot._bar_agg
    - Call occurs AFTER startup_reconcile and AFTER subscribe (ordering preserved)
    - If bot._bar_agg is None, set_handler is not called and no error raised
    """
    from unittest.mock import call

    watchdog, mock_gateway, mock_bot, mock_alerter = _make_watchdog_with_mocks()

    # Give the bot a non-None bar aggregator (the handler to re-register).
    mock_bar_agg = MagicMock()
    mock_bot._bar_agg = mock_bar_agg

    # Wire the bot's store so get_watchlist_codes returns some codes.
    mock_bot._store = MagicMock()
    mock_bot._store.get_watchlist_codes.return_value = ["US.AAPL"]
    mock_bot._position_manager = MagicMock()

    # Use a call recorder to capture ordering: startup_reconcile, subscribe, set_handler.
    call_recorder = MagicMock()
    call_recorder.startup_reconcile = AsyncMock()
    call_recorder.subscribe = AsyncMock()
    call_recorder.set_handler = MagicMock()

    mock_gateway.startup_reconcile = call_recorder.startup_reconcile
    mock_gateway.subscribe = call_recorder.subscribe
    mock_gateway.set_handler = call_recorder.set_handler

    # Trigger _on_reconnect (the reconnect handler)
    await watchdog._on_reconnect()

    # Assert set_handler called once with the bot's bar aggregator.
    call_recorder.set_handler.assert_called_once_with(mock_bar_agg)

    # Assert ordering: startup_reconcile and subscribe must precede set_handler.
    recorded_names = [c[0] for c in call_recorder.mock_calls if c[0]]
    assert "startup_reconcile" in recorded_names, "startup_reconcile must be called"
    assert "subscribe" in recorded_names, "subscribe must be called"
    assert "set_handler" in recorded_names, "set_handler must be called"
    assert recorded_names.index("startup_reconcile") < recorded_names.index("set_handler"), (
        "set_handler must be called AFTER startup_reconcile"
    )
    assert recorded_names.index("subscribe") < recorded_names.index("set_handler"), (
        "set_handler must be called AFTER subscribe"
    )


@pytest.mark.asyncio
async def test_reconnect_skips_set_handler_when_bar_agg_none():
    """Finding 1.3 guard: set_handler must not be called if bot._bar_agg is None."""
    watchdog, mock_gateway, mock_bot, mock_alerter = _make_watchdog_with_mocks()

    # Explicitly set _bar_agg to None — set_handler must be skipped.
    mock_bot._bar_agg = None
    mock_bot._store = MagicMock()
    mock_bot._store.get_watchlist_codes.return_value = []
    mock_bot._position_manager = MagicMock()

    mock_gateway.set_handler = MagicMock()

    # Must not raise and must not call set_handler.
    await watchdog._on_reconnect()

    mock_gateway.set_handler.assert_not_called()
