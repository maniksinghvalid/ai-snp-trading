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

@pytest.mark.xfail(reason="implemented in 05-02 (OpenDWatchdog not yet created)", strict=False)
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


@pytest.mark.xfail(reason="implemented in 05-02 (OpenDWatchdog not yet created)", strict=False)
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
