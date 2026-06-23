#!/usr/bin/env python3
"""
tests.scanner.test_calendar — Unit tests for SCAN-04 NYSE calendar gate.

Covers: holiday date returns False from is_trading_day; half-day date returns
13:00 ET early close; normal trading day returns 16:00 ET close; prior N
trading days helper excludes the reference date itself.

Uses real pandas-market-calendars with known fixed NYSE dates — no mocking.
"""
from datetime import date

import pytest


class TestHolidayRejection:
    """SCAN-04: NYSE holiday dates are not trading days."""

    def test_holiday_rejection(self):
        """date(2024, 1, 1) (New Year's Day) is not a trading day per NYSE calendar."""
        from bot.scanner.calendar import is_trading_day
        assert is_trading_day(date(2024, 1, 1)) is False


class TestHalfDayClose:
    """SCAN-04: Half-day sessions have an early close at 13:00 ET."""

    def test_half_day_close(self):
        """date(2023, 11, 24) (Black Friday) has a market close of 13:00 ET."""
        from bot.scanner.calendar import get_market_close_et
        close = get_market_close_et(date(2023, 11, 24))
        assert close == "13:00"


class TestNormalDayClose:
    """SCAN-04: Normal trading days close at 16:00 ET."""

    def test_normal_day_close(self):
        """A known regular trading day (date(2024, 1, 2)) closes at 16:00 ET."""
        from bot.scanner.calendar import get_market_close_et
        close = get_market_close_et(date(2024, 1, 2))
        assert close == "16:00"


class TestPriorTradingDays:
    """SCAN-03/04: get_prior_n_trading_days excludes the reference date itself."""

    def test_prior_n_trading_days_excludes_ref_date(self):
        """get_prior_n_trading_days(ref_date, n) returns n dates all strictly < ref_date."""
        from bot.scanner.calendar import get_prior_n_trading_days, is_trading_day

        ref = date(2024, 1, 10)  # Wednesday — a trading day
        result = get_prior_n_trading_days(ref, 14)

        assert len(result) == 14
        for d in result:
            # Each date must be strictly before ref_date
            assert d.date() < ref, f"{d} is not strictly before ref_date {ref}"
            # Each date must be a NYSE trading day
            assert is_trading_day(d.date()), f"{d} is not a trading day"
