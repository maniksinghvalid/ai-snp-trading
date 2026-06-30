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

@pytest.mark.asyncio
async def test_exit_alert_for_all_reasons():
    """Exit alert must be sendable for each exit_reason value (ALERT-02).

    Covers all five spec reasons (partial/breakeven/trail_stop/stop_out/force_close)
    plus legacy values (trail_up/exit_fill). Each must produce a non-raw human label
    (no reason key rendered as its own raw string in the Telegram body).
    """
    from bot.service.alerter import TelegramAlerter
    alerter = _make_alerter_with_token()
    # Five spec reasons + trail_stop (the newly-required label) + legacy values
    reasons = [
        "stop_out", "partial", "breakeven", "trail_stop", "trail_up",
        "force_close", "exit_fill",
    ]

    captured_bodies = []

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
            # Capture the POST body for label-rendering assertions
            if mock_url.call_args is not None:
                req_obj = mock_url.call_args[0][0]
                body = req_obj.data.decode("utf-8") if hasattr(req_obj, "data") else str(req_obj)
                captured_bodies.append((reason, body))

    # All reason values must have produced a urlopen call (one per reason)
    assert mock_url.call_count == len(reasons), (
        f"Expected {len(reasons)} calls (one per reason), got {mock_url.call_count}"
    )

    # Each reason must render a human label, NOT the raw reason string, in the bold header
    # (trail_stop must NOT appear as "trail_stop" — must render as "Trail Stop" or similar)
    for reason, body in captured_bodies:
        assert f"<b>{reason}</b>" not in body, (
            f"Reason '{reason}' was rendered as raw string in alert body — "
            "must use a human label from _EXIT_REASON_LABELS"
        )


@pytest.mark.asyncio
async def test_trail_stop_label_not_raw_string():
    """trail_stop must render a human label (not the raw key string) in format_exit_alert.

    This is the ALERT-02 gap: _EXIT_REASON_LABELS was missing 'trail_stop' so a
    trail-stop exit would fall back to the raw string 'trail_stop' in the Telegram bold header.
    """
    alerter = _make_alerter_with_token()
    text = alerter.format_exit_alert("US.AAPL", "trail_stop", 1.5)
    # The human label must be present (not the raw key)
    assert "trail_stop" not in text or "Trail Stop" in text, (
        f"'trail_stop' rendered as raw string in exit alert: {text!r}. "
        "Must have a human label in _EXIT_REASON_LABELS."
    )
    assert "<b>trail_stop</b>" not in text, (
        f"Raw reason key 'trail_stop' appeared as bold header: {text!r}"
    )


# ============================================================
# ALERT-03: Daily summary content
# ============================================================

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


# ============================================================
# ALERT-03 regression: open_risk must use initial_stop/trail_stop
# ============================================================


def test_format_daily_summary_open_risk_uses_correct_stop_columns():
    """Open Risk must read initial_stop/trail_stop — not the missing 'stop' key (ALERT-03 regression).

    StateStore.get_open_positions() returns dicts keyed by the positions table columns:
      initial_stop, trail_stop, entry_price, remaining_quantity.
    There is NO 'stop' column.

    Bug: format_daily_summary reads r.get("stop") which is always None -> 0.0, so
         Open Risk degrades to sum(entry_price * qty) — gross notional, not risk-to-stop.

    Fix requirement:
      effective_stop = trail_stop if trail_stop is not None else initial_stop
      open_risk = sum(max(0, (entry_price - effective_stop) * remaining_quantity))
    """
    alerter = _make_alerter_with_token()

    open_positions = [
        # Position A: no trailing stop yet — effective_stop = initial_stop = 148.00
        # Per-position risk = (150.00 - 148.00) * 100 = $200.00
        {
            "entry_price": 150.00,
            "initial_stop": 148.00,
            "trail_stop": None,
            "remaining_quantity": 100,
        },
        # Position B: trailing stop active — effective_stop = trail_stop = 198.00
        # Per-position risk = (200.00 - 198.00) * 50 = $100.00
        {
            "entry_price": 200.00,
            "initial_stop": 195.00,
            "trail_stop": 198.00,
            "remaining_quantity": 50,
        },
    ]
    # Expected: $200.00 + $100.00 = $300.00
    expected_open_risk = 300.00

    text = alerter.format_daily_summary(trades_rows=[], open_positions=open_positions)

    assert f"Open Risk: ${expected_open_risk:.2f}" in text, (
        f"Expected 'Open Risk: ${expected_open_risk:.2f}' (risk-to-stop) but got:\n{text}\n\n"
        "format_daily_summary reads r.get('stop') which is always None — "
        "positions rows have 'initial_stop' and 'trail_stop', not 'stop'."
    )


def test_format_daily_summary_open_risk_clamped_at_zero_for_locked_profit():
    """Open Risk contribution for a position with trail_stop above entry must be clamped to 0.

    When a trailing stop has advanced past entry (locked profit), the position has no downside
    risk to stop. (entry - trail_stop) < 0 would produce negative risk that incorrectly
    offsets other positions' genuine risk. Per-position contribution must be max(0, ...).
    """
    alerter = _make_alerter_with_token()

    open_positions = [
        # At-risk position: risk = (100.00 - 98.00) * 50 = $100.00
        {
            "entry_price": 100.00,
            "initial_stop": 98.00,
            "trail_stop": None,
            "remaining_quantity": 50,
        },
        # Locked-profit position: trail_stop (102.00) > entry (100.00)
        # Contribution must be clamped to max(0, (100 - 102) * 30) = max(0, -60) = 0
        {
            "entry_price": 100.00,
            "initial_stop": 98.00,
            "trail_stop": 102.00,
            "remaining_quantity": 30,
        },
    ]
    # Expected: $100.00 (locked-profit position contributes $0, not -$60)
    expected_open_risk = 100.00

    text = alerter.format_daily_summary(trades_rows=[], open_positions=open_positions)

    assert f"Open Risk: ${expected_open_risk:.2f}" in text, (
        f"Expected 'Open Risk: ${expected_open_risk:.2f}' (clamped) but got:\n{text}\n\n"
        "A position whose trailing stop exceeds entry (locked profit) must contribute 0 "
        "risk, not a negative value that offsets other positions."
    )
