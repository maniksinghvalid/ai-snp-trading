#!/usr/bin/env python3
"""
tests/service/test_alerter.py — RED unit stubs for TelegramAlerter (ALERT-01..04).

These tests target bot.service.alerter.TelegramAlerter which is implemented in plan 05-03.
They are xfail until that plan lands, so the suite collects cleanly and the
later wave has concrete verification targets to turn green.

Requirements covered:
  ALERT-01 — Entry alert includes ticker/size/entry/stop fields
  ALERT-02 — Exit alert covers all exit_reason values (stop_out, partial, breakeven, trail)
  ALERT-03 — Daily summary includes trades/wins/PnL/open-risk
  ALERT-04 — urllib.request.urlopen exception must never propagate from send()
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio


# ============================================================
# Helpers
# ============================================================

def _make_alerter_with_token():
    """Return a TelegramAlerter configured with a fake token + chat_id.

    Raises ImportError (caught by xfail) until bot.service.alerter is implemented.
    """
    from bot.service.alerter import TelegramAlerter  # noqa

    alerter = TelegramAlerter(token="FAKE_TOKEN", chat_id="12345")
    return alerter


# ============================================================
# ALERT-01: Entry alert content
# ============================================================

@pytest.mark.xfail(reason="implemented in 05-03 (TelegramAlerter not yet created)", strict=False)
@pytest.mark.asyncio
async def test_entry_alert_content():
    """Entry alert must include ticker, size, entry price, and stop (ALERT-01)."""
    alerter = _make_alerter_with_token()

    sent_messages = []

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock(return_value=False)
        mock_url.return_value.read.return_value = b'{"ok": true}'

        await alerter.send_entry_alert(
            code="US.AAPL",
            qty=100,
            entry_price=150.00,
            initial_stop=148.50,
        )
        # Capture the POST body from urlopen call
        call_args = mock_url.call_args
        assert call_args is not None, "urlopen must have been called"

    # The request body must contain the ticker and key fields
    # (exact format is plan 05-03's responsibility; test the intent)
    req_obj = call_args[0][0]
    body = req_obj.data.decode("utf-8") if hasattr(req_obj, "data") else str(req_obj)
    assert "AAPL" in body or "US.AAPL" in body, f"Ticker missing from alert body: {body}"
    assert "150" in body or "150.00" in body, f"Entry price missing: {body}"
    assert "148" in body or "148.50" in body, f"Stop price missing: {body}"


# ============================================================
# ALERT-02: Exit alert for all exit reasons
# ============================================================

@pytest.mark.xfail(reason="implemented in 05-03 (TelegramAlerter not yet created)", strict=False)
@pytest.mark.asyncio
async def test_exit_alert_for_all_reasons():
    """Exit alert must be sendable for each exit_reason value (ALERT-02)."""
    alerter = _make_alerter_with_token()
    reasons = ["stop_out", "partial", "breakeven", "trail_up", "force_close", "exit_fill"]

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock(return_value=False)
        mock_url.return_value.read.return_value = b'{"ok": true}'

        for reason in reasons:
            await alerter.send_exit_alert(
                code="US.AAPL",
                exit_reason=reason,
                r_multiple=1.5,
            )

    # All reason values must have produced a urlopen call (one per reason)
    assert mock_url.call_count == len(reasons), (
        f"Expected {len(reasons)} calls (one per reason), got {mock_url.call_count}"
    )


# ============================================================
# ALERT-03: Daily summary content
# ============================================================

@pytest.mark.xfail(reason="implemented in 05-03 (TelegramAlerter not yet created)", strict=False)
@pytest.mark.asyncio
async def test_daily_summary_content():
    """Daily summary alert must include trades, wins, PnL, and open-risk (ALERT-03)."""
    alerter = _make_alerter_with_token()

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock(return_value=False)
        mock_url.return_value.read.return_value = b'{"ok": true}'

        await alerter.send_daily_summary(
            trades=3,
            wins=2,
            pnl_usd=450.00,
            open_risk_usd=100.00,
        )
        call_args = mock_url.call_args

    req_obj = call_args[0][0]
    body = req_obj.data.decode("utf-8") if hasattr(req_obj, "data") else str(req_obj)
    assert "3" in body, f"Trade count missing from summary: {body}"
    assert "2" in body, f"Win count missing from summary: {body}"
    assert "450" in body, f"PnL missing from summary: {body}"


# ============================================================
# ALERT-04: Failure isolation — send() must not raise
# ============================================================

@pytest.mark.xfail(reason="implemented in 05-03 (TelegramAlerter not yet created)", strict=False)
@pytest.mark.asyncio
async def test_send_failure_does_not_raise():
    """urllib.request.urlopen raising OSError must not propagate from send() (ALERT-04)."""
    alerter = _make_alerter_with_token()

    with patch("urllib.request.urlopen", side_effect=OSError("network unavailable")):
        # Must NOT raise — alerter swallows the exception
        try:
            await alerter.send("Test alert message")
        except Exception as exc:
            pytest.fail(
                f"TelegramAlerter.send() raised {type(exc).__name__}: {exc} — "
                "alert failures must never propagate (ALERT-04)"
            )
