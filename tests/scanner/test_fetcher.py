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


# ============================================================
# Task 1: TodayPrice struct + resolve_today_price pure resolver
# ============================================================

def _make_1m_frame(bars: list, tz: str = "America/New_York") -> "pd.DataFrame":
    """Build a tz-aware 1m OHLCV DataFrame from a list of dicts.

    Each dict must have: 'ts' (ISO string, e.g. '2026-06-26 08:00'), and OHLCV floats:
    open, high, low, close, volume. The timestamp is interpreted as already being in the
    given tz (America/New_York by default) — so there is no UTC conversion.
    """
    import pandas as pd
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    timestamps = [
        pd.Timestamp(b["ts"]).tz_localize(zone) for b in bars
    ]
    df = pd.DataFrame(
        {
            "open": [b["open"] for b in bars],
            "high": [b["high"] for b in bars],
            "low": [b["low"] for b in bars],
            "close": [b["close"] for b in bars],
            "volume": [b.get("volume", 1000000) for b in bars],
        },
        index=pd.DatetimeIndex(timestamps, name="Datetime"),
    )
    return df


class TestResolveTodayPrice:
    """Task 1: resolve_today_price pure resolver — premarket / spanning-09:30 / empty cases."""

    def test_resolve_premarket_uses_latest_premarket_close(self):
        """Premarket (now_et < 09:30 ET): today_open == today_price == latest premarket close;
        today_high == max high across premarket bars."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        # Two premarket bars at 08:00 and 08:30 ET
        frame = _make_1m_frame([
            {"ts": "2026-06-26 08:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"ts": "2026-06-26 08:30", "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0},
        ])
        scan_ts = datetime(2026, 6, 26, 8, 30, tzinfo=ET)
        now_et = datetime(2026, 6, 26, 8, 30, tzinfo=ET)

        result = resolve_today_price(frame, now_et)

        assert result is not None
        # today_open == today_price == latest premarket close (08:30 bar close = 103.0)
        assert result.today_open == pytest.approx(103.0)
        assert result.today_price == pytest.approx(103.0)
        # today_high == max high across premarket bars (max(102.0, 104.0) = 104.0)
        assert result.today_high == pytest.approx(104.0)

    def test_resolve_after_open_uses_first_regular_session_open(self):
        """At/after 09:30 ET: today_open == open of first regular-session bar (>=09:30 ET),
        NOT a premarket bar open; today_price == latest bar close; today_high == max high
        of regular-session bars only."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        # Two premarket bars at 09:25, 09:28 ET then two regular-session bars at 09:30, 09:35 ET
        frame = _make_1m_frame([
            {"ts": "2026-06-26 09:25", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
            {"ts": "2026-06-26 09:28", "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0},
            {"ts": "2026-06-26 09:30", "open": 102.0, "high": 106.0, "low": 101.5, "close": 105.0},
            {"ts": "2026-06-26 09:35", "open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0},
        ])
        scan_ts = datetime(2026, 6, 26, 9, 40, tzinfo=ET)
        now_et = datetime(2026, 6, 26, 9, 40, tzinfo=ET)

        result = resolve_today_price(frame, now_et)

        assert result is not None
        # today_open == first regular-session bar open (09:30 bar open = 102.0, NOT 100.0 premarket)
        assert result.today_open == pytest.approx(102.0)
        # today_price == latest bar close (09:35 bar close = 107.0)
        assert result.today_price == pytest.approx(107.0)
        # today_high == max high of regular-session bars only: max(106.0, 108.0) = 108.0
        assert result.today_high == pytest.approx(108.0)

    def test_resolve_empty_frame_returns_none(self):
        """Empty or None 1m frame → resolve_today_price returns None (fail-closed)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price
        import pandas as pd

        ET = ZoneInfo("America/New_York")
        scan_ts = datetime(2026, 6, 26, 9, 40, tzinfo=ET)
        now_et = datetime(2026, 6, 26, 9, 40, tzinfo=ET)

        # None frame
        assert resolve_today_price(None, now_et) is None

        # Empty DataFrame
        empty_frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert resolve_today_price(empty_frame, now_et) is None

    def test_resolve_after_open_but_no_regular_bar_returns_none(self):
        """now_et >= 09:30 ET but frame has ONLY premarket bars → returns None (fail-closed)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        # Only premarket bars; no >=09:30 bar
        frame = _make_1m_frame([
            {"ts": "2026-06-26 08:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"ts": "2026-06-26 09:28", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
        ])
        scan_ts = datetime(2026, 6, 26, 9, 40, tzinfo=ET)
        now_et = datetime(2026, 6, 26, 9, 40, tzinfo=ET)

        result = resolve_today_price(frame, now_et)

        assert result is None

    def test_resolve_does_not_call_wall_clock(self):
        """Purity test: resolve_today_price must NOT call now_et()/datetime.now() internally.

        The injected now_et argument is the only clock source. Verify by patching
        bot.scanner.fetcher.now_et to a sentinel that raises if called.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from unittest.mock import patch

        ET = ZoneInfo("America/New_York")
        frame = _make_1m_frame([
            {"ts": "2026-06-26 08:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
        ])
        scan_ts = datetime(2026, 6, 26, 8, 30, tzinfo=ET)
        now_et = datetime(2026, 6, 26, 8, 30, tzinfo=ET)

        def _sentinel_clock(*args, **kwargs):
            raise AssertionError(
                "resolve_today_price must NOT call now_et() or datetime.now() internally; "
                "the clock must be the injected now_et argument."
            )

        from bot.scanner import fetcher as fetcher_module
        with patch.object(fetcher_module, "now_et", _sentinel_clock, create=True):
            # Should complete without raising AssertionError
            from bot.scanner.fetcher import resolve_today_price
            result = resolve_today_price(frame, now_et)

        # Premarket case: result should be valid
        assert result is not None
        assert result.today_open == pytest.approx(101.0)
        assert result.today_price == pytest.approx(101.0)
        assert result.today_high == pytest.approx(102.0)

    def test_resolve_tz_naive_index_does_not_crash_scan(self):
        """Regression (CR-01, 02-REVIEW): a tz-NAIVE 1m DatetimeIndex must NOT raise.

        yfinance returns tz-naive intraday timestamps (in UTC) under some
        yfinance/pandas combinations. The resolver previously called
        index.tz_convert(ET) unconditionally, which raises TypeError on a
        tz-naive index and aborts the ENTIRE scan for every symbol — a fail-OPEN
        crash worse than the empty-watchlist bug this phase fixes. The resolver
        must localize tz-naive timestamps as UTC, convert to ET, and return a
        valid TodayPrice (or None) — never raise.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price
        import pandas as pd

        ET = ZoneInfo("America/New_York")
        # tz-NAIVE timestamps interpreted as UTC. 2026-06-26 is EDT (UTC-4), so
        # 12:00/12:30 UTC == 08:00/08:30 ET (premarket).
        naive_idx = pd.DatetimeIndex(
            [pd.Timestamp("2026-06-26 12:00"), pd.Timestamp("2026-06-26 12:30")],
            name="Datetime",
        )
        assert naive_idx.tz is None  # guard: this frame really is tz-naive
        frame = pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 104.0],
                "low": [99.0, 100.0],
                "close": [101.0, 103.0],
                "volume": [1_000_000, 1_000_000],
            },
            index=naive_idx,
        )
        now_et = datetime(2026, 6, 26, 8, 30, tzinfo=ET)  # premarket
        scan_ts = datetime(2026, 6, 26, 8, 30, tzinfo=ET)

        # Must not raise; tz-naive bars are treated as UTC → ET (premarket here).
        result = resolve_today_price(frame, now_et)

        assert result is not None
        # Premarket: today_open == today_price == latest premarket close (103.0)
        assert result.today_open == pytest.approx(103.0)
        assert result.today_price == pytest.approx(103.0)
        assert result.today_high == pytest.approx(104.0)

    def test_resolve_non_datetime_index_returns_none(self):
        """Regression (CR-01): a frame whose index is not a DatetimeIndex must
        fail closed (return None), not raise, so one malformed frame cannot abort
        the whole scan."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price
        import pandas as pd

        ET = ZoneInfo("America/New_York")
        frame = pd.DataFrame(
            {
                "open": [100.0],
                "high": [102.0],
                "low": [99.0],
                "close": [101.0],
                "volume": [1_000_000],
            },
            index=pd.RangeIndex(1),  # not a DatetimeIndex
        )
        now_et = datetime(2026, 6, 26, 8, 30, tzinfo=ET)
        scan_ts = datetime(2026, 6, 26, 8, 30, tzinfo=ET)

        assert resolve_today_price(frame, now_et) is None


# ============================================================
# Task 2: download_intraday_1m batch fetcher (SCAN-06)
# ============================================================

class TestDownloadIntraday1m:
    """SCAN-06: download_intraday_1m mirrors download_daily_bars contract on 1m prepost feed."""

    def test_download_intraday_returns_per_ticker_frames(self):
        """Mocked yf.download returns a dict of 1m frames keyed by ticker; calls yf.download
        with the correct interval='1m', period='1d', prepost=True, group_by='ticker' params."""
        symbols = ["AAPL", "MSFT", "GOOG"]
        mock_data = _make_multi_ticker_data(symbols)

        with patch("yfinance.download", return_value=mock_data) as mock_dl, \
             patch("yfinance.shared._ERRORS", {}):
            from bot.scanner.fetcher import download_intraday_1m
            data, failed = download_intraday_1m(symbols)

        assert failed == set()
        assert "AAPL" in data
        assert "MSFT" in data
        assert "GOOG" in data

        # Verify yf.download was called with the required 1m / prepost params
        call_kwargs = mock_dl.call_args[1]
        assert call_kwargs.get("interval") == "1m"
        assert call_kwargs.get("period") == "1d"
        assert call_kwargs.get("prepost") is True
        assert call_kwargs.get("group_by") == "ticker"

    def test_download_intraday_clears_shared_errors(self):
        """yfinance.shared._ERRORS is cleared before each 1m download call to avoid stale state.

        Pre-seed _ERRORS with a stale key; after download_intraday_1m returns it must be gone.
        """
        import yfinance.shared as yf_shared

        symbols = ["AAPL"]
        mock_data = _make_multi_ticker_data(symbols)

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS["STALE_INTRADAY"] = "stale error from previous 1m run"

            with patch("yfinance.download", return_value=mock_data):
                from bot.scanner.fetcher import download_intraday_1m
                data, failed = download_intraday_1m(symbols)

            # STALE_INTRADAY must be gone (cleared before download)
            assert "STALE_INTRADAY" not in yf_shared._ERRORS, (
                "shared._ERRORS.clear() must remove stale entries before 1m download"
            )
            assert failed == set(), "No failures expected with clean mock"
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

    def test_download_intraday_detects_failures(self):
        """Some tickers returned all-NaN (real yfinance failure shape) appear in failed_set;
        reuses _detect_failed/get_ticker_frame (no new ad-hoc failure detection).

        Uses 100 symbols with 5 all-NaN failures (5% < 10% threshold) so the
        degradation gate does NOT trip — this test validates failure detection,
        not the degradation gate (that is tested separately in TestDegradationGate).
        """
        import yfinance.shared as yf_shared

        total = 100
        all_syms = [f"SYM{i:03d}" for i in range(total)]
        fail_keys = [f"SYM{i:03d}" for i in range(5)]  # 5 failures = 5% < 10% threshold
        ok = [s for s in all_syms if s not in fail_keys]
        mock_data = _make_data_with_nan_failures(ok, fail_keys)

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            with patch("yfinance.download", return_value=mock_data):
                from bot.scanner.fetcher import download_intraday_1m
                data, failed = download_intraday_1m(all_syms)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert set(fail_keys) == failed, "all-NaN tickers must be in failed_set"
        for sym in ok[:5]:  # spot-check a few OK symbols are not in failed
            assert sym not in failed

    def test_download_intraday_empty_input(self):
        """download_intraday_1m([]) returns ({}, set()) without calling yf.download."""
        with patch("yfinance.download") as mock_dl:
            from bot.scanner.fetcher import download_intraday_1m
            data, failed = download_intraday_1m([])

        assert data == {}
        assert failed == set()
        mock_dl.assert_not_called()
