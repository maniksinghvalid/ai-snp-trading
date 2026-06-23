#!/usr/bin/env python3
"""
tests.strategy.test_indicators — Unit tests for pure indicator functions.

Covers:
- sma: trailing mean matches hand-computed value; returns NaN/None below period
- rvol: denominator strictly prior completed sessions (no look-ahead bias);
         large current-day volume does not appear in denominator
- swing_low_2_2: returns correct pivot on clean synthetic series; None if unavailable

No network calls, no I/O, no broker dependencies.
"""
import math
import pandas as pd
import pytest
from datetime import date

from bot.strategy.indicators import sma, rvol, swing_low_2_2


# ============================================================
# SMA tests
# ============================================================

class TestSma:
    def test_sma_correct_value(self):
        """SMA of 200 values should equal their mean."""
        import pandas as pd
        vals = [float(i) for i in range(1, 201)]
        series = pd.Series(vals)
        result = sma(series, 200)
        expected = sum(vals) / len(vals)
        assert abs(result - expected) < 1e-9, f"SMA={result}, expected={expected}"

    def test_sma_uses_trailing_window(self):
        """SMA of last 3 values from a longer series."""
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(series, 3)
        expected = (3.0 + 4.0 + 5.0) / 3
        assert abs(result - expected) < 1e-9

    def test_sma_insufficient_data_returns_nan_or_none(self):
        """With fewer than period values, sma must return NaN or None."""
        series = pd.Series([1.0, 2.0, 3.0])
        result = sma(series, 200)
        # Accept either None or float NaN
        is_nan = (result is None) or (isinstance(result, float) and math.isnan(result))
        assert is_nan, f"Expected NaN/None for insufficient data, got {result}"

    def test_sma_exactly_period_values(self):
        """SMA with exactly 200 values should return their mean."""
        import random
        random.seed(42)
        vals = [random.uniform(10.0, 100.0) for _ in range(200)]
        series = pd.Series(vals)
        result = sma(series, 200)
        expected = sum(vals) / 200
        assert abs(result - expected) < 1e-9

    def test_sma_single_value_below_period_is_nan(self):
        """SMA of 1 value with period 200 should be NaN/None."""
        series = pd.Series([42.0])
        result = sma(series, 200)
        is_nan = (result is None) or (isinstance(result, float) and math.isnan(result))
        assert is_nan


# ============================================================
# RVOL tests
# ============================================================

def _make_volume_df(dates_and_volumes):
    """Helper to build a daily-bar DataFrame with date and volume columns."""
    rows = [{"date": d, "volume": v} for d, v in dates_and_volumes]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


class TestRvol:
    def test_rvol_no_lookahead_excludes_signal_date(self):
        """
        RVOL denominator must NOT include any row with date >= signal_date.
        Feed a series where today (signal_date) has volume=999999 — the
        denominator mean must remain unaffected by that value.
        """
        signal_date = pd.Timestamp("2026-01-15")
        # 14 prior days at volume=100 each, plus signal day at volume=999999
        dates_and_volumes = [(signal_date - pd.Timedelta(days=i), 100) for i in range(1, 15)]
        dates_and_volumes.append((signal_date, 999_999))  # current day — must NOT be in denominator
        df = _make_volume_df(dates_and_volumes)

        today_volume = 500
        result = rvol(df, signal_date, lookback_days=14, today_volume=today_volume)

        # denominator should be 100.0, not contaminated by 999999
        assert result is not None
        expected = today_volume / 100.0
        assert abs(result - expected) < 1e-6, f"RVOL={result}, expected={expected}"

    def test_rvol_denominator_contains_no_signal_date_row(self):
        """
        Direct assertion: after calling rvol, no row in the denominator DataFrame
        should have date >= signal_date. We test this via a side-effect hook by
        supplying a DataFrame where including signal_date would change the result.
        """
        signal_date = pd.Timestamp("2026-02-01")
        # prior days: volumes 100, except 14th prior = 200
        dates_and_volumes = []
        for i in range(1, 15):
            vol = 200 if i == 14 else 100
            dates_and_volumes.append((signal_date - pd.Timedelta(days=i), vol))
        # Add signal day with volume=0 — if included it would lower mean to ~93
        dates_and_volumes.append((signal_date, 0))
        df = _make_volume_df(dates_and_volumes)

        today_volume = 1000
        result = rvol(df, signal_date, lookback_days=14, today_volume=today_volume)

        # denominator = mean([200, 100, 100, ...(13 total prior)]) = (200 + 13*100)/14
        expected_denom = (200 + 13 * 100) / 14
        expected_rvol = today_volume / expected_denom
        assert abs(result - expected_rvol) < 1e-6

    def test_rvol_correct_calculation(self):
        """Basic RVOL = today_volume / mean_prior_14_days_volume."""
        signal_date = pd.Timestamp("2026-03-01")
        dates_and_volumes = [(signal_date - pd.Timedelta(days=i), 1000.0) for i in range(1, 15)]
        df = _make_volume_df(dates_and_volumes)

        today_volume = 2500.0
        result = rvol(df, signal_date, lookback_days=14, today_volume=today_volume)

        expected = 2500.0 / 1000.0
        assert abs(result - expected) < 1e-6

    def test_rvol_insufficient_prior_days_returns_none(self):
        """If fewer than lookback_days of prior data, return None."""
        signal_date = pd.Timestamp("2026-04-01")
        # Only 5 days prior data, need 14
        dates_and_volumes = [(signal_date - pd.Timedelta(days=i), 1000.0) for i in range(1, 6)]
        df = _make_volume_df(dates_and_volumes)

        result = rvol(df, signal_date, lookback_days=14, today_volume=1500.0)
        assert result is None

    def test_rvol_large_current_day_volume_not_in_denominator(self):
        """
        Concrete proof: if today's volume appears in denominator, the mean would
        be massively inflated and RVOL would be < 1.0; correct behavior gives > 1.0.
        """
        signal_date = pd.Timestamp("2026-05-01")
        # 14 prior days at 1000 volume each
        dates_and_volumes = [(signal_date - pd.Timedelta(days=i), 1000.0) for i in range(1, 15)]
        # today at 1,000,000 volume — if included in denominator, mean would be ~72222
        dates_and_volumes.append((signal_date, 1_000_000))
        df = _make_volume_df(dates_and_volumes)

        today_volume = 2000.0
        result = rvol(df, signal_date, lookback_days=14, today_volume=today_volume)

        # Correct: denominator is 1000, RVOL = 2000/1000 = 2.0
        # Buggy: denominator would include 1M → mean ~72222 → RVOL ~0.027
        assert result is not None
        assert result > 1.5, (
            f"RVOL={result}: if > 1.5 the large current-day volume is NOT in denominator. "
            f"If near 0, look-ahead bias is present."
        )


# ============================================================
# swing_low_2_2 tests
# ============================================================

class TestSwingLow22:
    def test_clean_pivot_returns_pivot_low(self):
        """
        Classic 2/2 pivot: bars go down 2 then up 2 with a clear minimum at center.
        lows = [5, 4, 2, 3, 6] — pivot is at index 2 (value=2).
        """
        lows = [5.0, 4.0, 2.0, 3.0, 6.0]
        result = swing_low_2_2(lows)
        assert result == 2.0, f"Expected 2.0, got {result}"

    def test_no_pivot_returns_none(self):
        """Monotonically decreasing — no 2/2 pivot."""
        lows = [10.0, 9.0, 8.0, 7.0, 6.0]
        result = swing_low_2_2(lows)
        assert result is None

    def test_fewer_than_5_bars_returns_none(self):
        """With < 5 bars there can be no confirmed 2/2 pivot."""
        assert swing_low_2_2([]) is None
        assert swing_low_2_2([1.0]) is None
        assert swing_low_2_2([1.0, 2.0, 3.0, 4.0]) is None

    def test_exactly_5_bars_with_pivot(self):
        """5-bar array with pivot at index 2."""
        lows = [10.0, 8.0, 5.0, 7.0, 9.0]
        result = swing_low_2_2(lows)
        assert result == 5.0

    def test_pivot_at_center_of_longer_series(self):
        """
        In a 9-bar series the last 5 bars contain a pivot.
        lows = [20, 18, 16, 14, 12, 10, 13, 16, 19]
        Potential pivot at index 5 (value=10): lows[5]=10 < lows[4]=12, lows[3]=14
        and lows[5]=10 < lows[6]=13, lows[7]=16 → confirmed pivot.
        """
        lows = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 13.0, 16.0, 19.0]
        result = swing_low_2_2(lows)
        assert result == 10.0

    def test_tie_not_a_pivot(self):
        """If low[i] == low[i-1], not strictly less, should not be a pivot."""
        lows = [5.0, 5.0, 5.0, 5.0, 5.0]
        result = swing_low_2_2(lows)
        assert result is None

    def test_returns_none_for_equal_neighbors(self):
        """bar with equal left neighbors is not strictly lower."""
        lows = [10.0, 8.0, 8.0, 9.0, 10.0]
        # index 2 = 8.0, lows[1] = 8.0 → NOT strict so not a pivot
        result = swing_low_2_2(lows)
        assert result is None


# ============================================================
# No I/O validation
# ============================================================

class TestNoNetworkOrIO:
    def test_indicators_has_no_io_imports(self):
        """indicators.py must not import network/I/O-heavy modules."""
        import inspect
        import bot.strategy.indicators as ind_mod
        src = inspect.getsource(ind_mod)
        forbidden = ["sqlite3", "requests", "moomoo", "futu", "urllib", "socket"]
        for name in forbidden:
            assert name not in src, f"indicators.py must not import '{name}'"
