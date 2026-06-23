#!/usr/bin/env python3
"""
tests.scanner.test_calendar — Wave 0 stub tests for SCAN-04 NYSE calendar gate.

Covers: holiday date returns False from is_trading_day; half-day date returns
13:00 ET early close; normal trading day returns 16:00 ET close; prior N
trading days helper excludes the reference date itself.

Uses real pandas-market-calendars with known fixed NYSE dates — no mocking.

These stubs are skipped until implemented in plan 02-03.
"""
from datetime import date

import pytest


class TestHolidayRejection:
    """SCAN-04: NYSE holiday dates are not trading days."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_holiday_rejection(self):
        """date(2024, 1, 1) (New Year's Day) is not a trading day per NYSE calendar."""
        pytest.fail("Wave 0 stub")


class TestHalfDayClose:
    """SCAN-04: Half-day sessions have an early close at 13:00 ET."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_half_day_close(self):
        """date(2023, 11, 24) (Black Friday) has a market close of 13:00 ET."""
        pytest.fail("Wave 0 stub")


class TestNormalDayClose:
    """SCAN-04: Normal trading days close at 16:00 ET."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_normal_day_close(self):
        """A known regular trading day (e.g. date(2024, 1, 2)) closes at 16:00 ET."""
        pytest.fail("Wave 0 stub")


class TestPriorTradingDays:
    """SCAN-03/04: get_prior_n_trading_days excludes the reference date itself."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_prior_n_trading_days_excludes_ref_date(self):
        """get_prior_n_trading_days(ref_date, n) returns n dates all strictly < ref_date."""
        pytest.fail("Wave 0 stub")
