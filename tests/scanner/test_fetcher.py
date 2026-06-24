#!/usr/bin/env python3
"""
tests.scanner.test_fetcher — Unit tests for SCAN-06 daily-bar download.

Covers: yfinance batch download returns per-ticker DataFrames; lowercase column
normalisation; partial failure detection via yfinance.shared._ERRORS; >= 10%
failure rate triggers ScanDegradationError; < 10% logs warning and proceeds;
shared errors dict cleared before each download call.
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock


def _make_ticker_df(symbol: str) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame for a ticker (capitalized columns)."""
    return pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [105.0, 106.0],
        "Low": [99.0, 100.0],
        "Close": [104.0, 105.0],
        "Volume": [1000000, 1100000],
    })


def _make_nan_ticker_df() -> pd.DataFrame:
    """Return an all-NaN OHLCV frame — how yfinance 1.4.1 surfaces a failed ticker
    (present in the result but with no usable data, shared._ERRORS left empty)."""
    return pd.DataFrame({
        "Open": [float("nan"), float("nan")],
        "High": [float("nan"), float("nan")],
        "Low": [float("nan"), float("nan")],
        "Close": [float("nan"), float("nan")],
        "Volume": [float("nan"), float("nan")],
    })


def _make_multi_ticker_data(symbols: list) -> dict:
    """Return a dict-like object keyed by ticker symbol."""
    return {sym: _make_ticker_df(sym) for sym in symbols}


def _make_data_with_nan_failures(ok: list, failed: list) -> dict:
    """Build a result where failed tickers are PRESENT but all-NaN (yfinance 1.4.1
    real failure shape), not absent — exercises data-derived failure detection."""
    data = {sym: _make_ticker_df(sym) for sym in ok}
    for sym in failed:
        data[sym] = _make_nan_ticker_df()
    return data


class TestDownloadDailyBars:
    """SCAN-06: download_daily_bars returns per-ticker bar DataFrames."""

    def test_download_returns_per_ticker_frames(self, monkeypatch):
        """Mocked yf.download returns a dict of DataFrames keyed by ticker symbol."""
        symbols = ["AAPL", "MSFT", "GOOG"]
        mock_data = _make_multi_ticker_data(symbols)

        with patch("yfinance.download", return_value=mock_data), \
             patch("yfinance.shared._ERRORS", {}):
            from bot.scanner.fetcher import download_daily_bars
            data, failed = download_daily_bars(symbols)

        assert failed == set()
        assert "AAPL" in data
        assert "MSFT" in data

    def test_shared_errors_cleared_before_download(self, monkeypatch):
        """yfinance.shared._ERRORS is cleared before each download call to avoid stale state.

        After download_daily_bars returns, shared._ERRORS must NOT contain the
        stale key that was present before the call — proving that .clear() was
        called before yf.download().
        """
        import yfinance.shared as yf_shared

        symbols = ["AAPL"]
        mock_data = _make_multi_ticker_data(symbols)

        # Pre-populate _ERRORS with a stale key that yf.download won't add back
        original_errors = yf_shared._ERRORS
        try:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS["STALE_TICKER"] = "stale error from previous run"

            with patch("yfinance.download", return_value=mock_data):
                from bot.scanner.fetcher import download_daily_bars
                data, failed = download_daily_bars(symbols)

            # After the call, STALE_TICKER must be gone (cleared before download)
            # and since mock yf.download doesn't add errors, _ERRORS should be empty
            assert "STALE_TICKER" not in yf_shared._ERRORS, (
                "shared._ERRORS.clear() must remove stale entries before download"
            )
            assert failed == set(), "No failures expected with clean mock"
        finally:
            yf_shared._ERRORS.clear()


class TestTickerNormalization:
    """SCAN-06: Column names lowercased after yf.download."""

    def test_lowercase_column_normalization(self, monkeypatch):
        """OHLCV column names are normalised to lowercase (open, high, low, close, volume)."""
        symbols = ["AAPL"]
        mock_data = _make_multi_ticker_data(symbols)  # columns: Open, High, Low, Close, Volume

        with patch("yfinance.download", return_value=mock_data), \
             patch("yfinance.shared._ERRORS", {}):
            from bot.scanner.fetcher import download_daily_bars, get_ticker_frame
            data, failed = download_daily_bars(symbols)
            ticker_df = get_ticker_frame(data, "AAPL")

        assert ticker_df is not None
        # Columns must be lowercase
        assert "open" in ticker_df.columns
        assert "high" in ticker_df.columns
        assert "low" in ticker_df.columns
        assert "close" in ticker_df.columns
        assert "volume" in ticker_df.columns
        # Capitalized form must not exist
        assert "Open" not in ticker_df.columns


class TestDegradationGate:
    """SCAN-06: Data-degradation policy at >= 10% and < 10% failure rates."""

    def test_all_nan_tickers_detected_without_shared_errors(self, monkeypatch):
        """UAT Test 2 regression: failures present as all-NaN frames with an EMPTY
        shared._ERRORS must still be counted (yfinance 1.4.1 behaviour).

        The pre-fix code read only shared._ERRORS, so this case reported 0
        failures and the degradation gate never tripped.
        """
        import yfinance.shared as yf_shared

        total = 100
        symbols = [f"SYM{i:03d}" for i in range(total)]
        fail_keys = {f"SYM{i:03d}" for i in range(15)}  # 15% — all-NaN, not absent
        ok = [s for s in symbols if s not in fail_keys]
        mock_data = _make_data_with_nan_failures(ok, sorted(fail_keys))

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()  # stays empty — the real 1.4.1 case
            with patch("yfinance.download", return_value=mock_data), \
                 patch("bot.scanner.fetcher.append_audit"):
                from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
                with pytest.raises(ScanDegradationError):
                    download_daily_bars(symbols)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

    def test_under_10pct_all_nan_warns_and_proceeds(self, monkeypatch):
        """< 10% all-NaN failures (empty shared._ERRORS) are counted but do not raise."""
        import yfinance.shared as yf_shared

        total = 100
        symbols = [f"SYM{i:03d}" for i in range(total)]
        fail_keys = {f"SYM{i:03d}" for i in range(7)}  # 7% all-NaN
        ok = [s for s in symbols if s not in fail_keys]
        mock_data = _make_data_with_nan_failures(ok, sorted(fail_keys))

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            with patch("yfinance.download", return_value=mock_data):
                from bot.scanner.fetcher import download_daily_bars
                data, failed = download_daily_bars(symbols)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert failed == fail_keys, "all-NaN tickers must be detected from data, not shared._ERRORS"

    def test_partial_failure_detected_via_shared_errors(self, monkeypatch):
        """Failed tickers are detected by inspecting yfinance.shared._ERRORS after download."""
        import yfinance.shared as yf_shared

        total = 100
        symbols = [f"SYM{i:03d}" for i in range(total)]
        # 3 failures — below 10%
        fail_keys = {"SYM000", "SYM001", "SYM002"}
        successful = symbols[3:]
        mock_data = _make_multi_ticker_data(successful)

        # Simulate yf.download() populating _ERRORS after the call
        def mock_download_with_errors(**kwargs):
            # After our code clears _ERRORS, simulate yfinance adding failures
            for k in fail_keys:
                yf_shared._ERRORS[k] = "download error"
            return mock_data

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            with patch("yfinance.download", side_effect=mock_download_with_errors):
                from bot.scanner.fetcher import download_daily_bars
                data, failed = download_daily_bars(symbols)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert failed == fail_keys

    def test_10pct_failure_raises_ScanDegradationError(self, monkeypatch):
        """When >= 10% of tickers fail (e.g. 10 of 100), ScanDegradationError is raised."""
        import yfinance.shared as yf_shared

        total = 100
        symbols = [f"SYM{i:03d}" for i in range(total)]
        # Exactly 10 failures — >= 10% threshold triggers abort
        fail_keys = {f"SYM{i:03d}" for i in range(10)}
        successful = symbols[10:]
        mock_data = _make_multi_ticker_data(successful)

        def mock_download_10pct(**kwargs):
            for k in fail_keys:
                yf_shared._ERRORS[k] = "download error"
            return mock_data

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            with patch("yfinance.download", side_effect=mock_download_10pct), \
                 patch("bot.scanner.fetcher.append_audit") as mock_audit:
                from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
                with pytest.raises(ScanDegradationError):
                    download_daily_bars(symbols)
                # D-07: durable audit surfacing must be called on abort path
                mock_audit.assert_called_once()
                audit_call = mock_audit.call_args[0][0]
                assert audit_call["event"] == "scan_aborted_data_degradation"
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

    def test_under_10pct_warns_and_proceeds(self, monkeypatch):
        """When < 10% of tickers fail, a warning is logged and the partial result is returned."""
        import yfinance.shared as yf_shared

        total = 100
        symbols = [f"SYM{i:03d}" for i in range(total)]
        # 9 failures — below 10% threshold, should NOT raise
        fail_keys = {f"SYM{i:03d}" for i in range(9)}
        successful = symbols[9:]
        mock_data = _make_multi_ticker_data(successful)

        def mock_download_under_10pct(**kwargs):
            for k in fail_keys:
                yf_shared._ERRORS[k] = "download error"
            return mock_data

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            with patch("yfinance.download", side_effect=mock_download_under_10pct):
                from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
                # Must NOT raise
                data, failed = download_daily_bars(symbols)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert len(failed) == 9
        # Data dict returned (partial success)
        assert data is not None
