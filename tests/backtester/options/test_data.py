#!/usr/bin/env python3
"""tests.backtester.options.test_data — backtester.options.data unit tests (no network)."""
from datetime import date

import pytest

import bot.options.strategy as live_strategy
import backtester.options.data as data


@pytest.mark.parametrize(
    "root,expiry,right,strike",
    [
        ("SPY", date(2026, 9, 18), "P", 450.0),
        ("SPY", date(2026, 9, 18), "C", 402.5),
        ("QQQE", date(2025, 6, 20), "C", 100.0),
        ("GDX", date(2025, 6, 20), "P", 13.0),
    ],
)
def test_ticker_roundtrip(root, expiry, right, strike):
    ticker = data.format_massive_ticker(root, expiry, right, strike)
    parsed = data.parse_massive_ticker(ticker)
    assert parsed == {"root": root, "expiry": expiry, "right": right, "strike": strike}
    assert data.format_massive_ticker(**{**parsed, "root": parsed["root"]}) == ticker


@pytest.mark.parametrize(
    "bad_ticker",
    ["US.SPY260918P00450000", "O:SPY260918X00450000", "not-a-ticker", "O:SPY2609P00450000"],
)
def test_parse_malformed_ticker_raises(bad_ticker):
    with pytest.raises(ValueError):
        data.parse_massive_ticker(bad_ticker)


def test_data_imports_strategy_not_copies():
    assert data.is_monthly_expiry is live_strategy.is_monthly_expiry
    assert data.option_dte is live_strategy.option_dte
