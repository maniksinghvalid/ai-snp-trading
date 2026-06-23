#!/usr/bin/env python3
"""
tests.scanner.test_fetcher — Wave 0 stub tests for SCAN-06 daily-bar download.

Covers: yfinance batch download returns per-ticker DataFrames; lowercase column
normalisation; partial failure detection via yfinance.shared._ERRORS; >= 10%
failure rate triggers ScanDegradationError; < 10% logs warning and proceeds;
shared errors dict cleared before each download call.

These stubs are skipped until implemented in plan 02-01.
"""
import pytest


class TestDownloadDailyBars:
    """SCAN-06: download_daily_bars returns per-ticker bar DataFrames."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_download_returns_per_ticker_frames(self):
        """Mocked yf.download returns a dict of DataFrames keyed by ticker symbol."""
        pytest.fail("Wave 0 stub")

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_shared_errors_cleared_before_download(self):
        """yfinance.shared._ERRORS is cleared before each download call to avoid stale state."""
        pytest.fail("Wave 0 stub")


class TestTickerNormalization:
    """SCAN-06: Column names lowercased after yf.download."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_lowercase_column_normalization(self):
        """OHLCV column names are normalised to lowercase (open, high, low, close, volume)."""
        pytest.fail("Wave 0 stub")


class TestDegradationGate:
    """SCAN-06: Data-degradation policy at >= 10% and < 10% failure rates."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_partial_failure_detected_via_shared_errors(self):
        """Failed tickers are detected by inspecting yfinance.shared._ERRORS after download."""
        pytest.fail("Wave 0 stub")

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_10pct_failure_raises_ScanDegradationError(self):
        """When >= 10% of tickers fail (e.g. 50 of 500), ScanDegradationError is raised."""
        pytest.fail("Wave 0 stub")

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_under_10pct_warns_and_proceeds(self):
        """When < 10% of tickers fail, a warning is logged and the partial result is returned."""
        pytest.fail("Wave 0 stub")
