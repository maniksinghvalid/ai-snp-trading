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


@pytest.fixture(autouse=True)
def _zero_retry_backoff(monkeypatch):
    """Zero the yfinance retry backoff for every test in this module.

    _download_batch sleeps _RETRY_BACKOFF_S (default 3s) between each of its
    _RETRY_MAX_ATTEMPTS retries, so failure-path tests (which trigger the retry)
    otherwise add ~6s each. No test depends on the real backoff DURATION — only
    the retry count/behaviour matters — so collapsing it to 0 keeps the suite
    fast without changing any assertion.
    """
    monkeypatch.setattr("bot.scanner.fetcher._RETRY_BACKOFF_S", 0.0, raising=False)


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

    def test_resolve_prior_session_bars_return_none(self):
        """Regression (WR-05, 02-REVIEW): a 1m frame containing ONLY bars dated a
        prior session must fail closed (return None), never resolve stale bars as
        "today's" open/price/high.

        yf.download(period="1d", interval="1m") returns the most recent AVAILABLE
        session, which on a weekend/holiday/data-lag may be a prior day. The
        resolver must compare each bar's ET date against the injected now_et.date()
        and return None when no bar matches today.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        # Bars dated 2026-06-25 (the prior session) ...
        frame = _make_1m_frame([
            {"ts": "2026-06-25 09:30", "open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0},
            {"ts": "2026-06-25 09:35", "open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0},
        ])
        # ... but now_et says today is 2026-06-26 (RTH). No bar matches today.
        now_et = datetime(2026, 6, 26, 9, 40, tzinfo=ET)

        assert resolve_today_price(frame, now_et) is None, (
            "Stale prior-session 1m bars must not be resolved as today's price (WR-05)"
        )

    def test_resolve_mixed_dates_uses_only_today_bars(self):
        """WR-05: a frame containing BOTH a prior-session bar and today's bars must
        compute open/price/high from today's bars only, not the stale prior bar."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        # Prior-session premarket bar (should be ignored) + today's premarket bars.
        frame = _make_1m_frame([
            {"ts": "2026-06-25 08:00", "open": 50.0, "high": 999.0, "low": 49.0, "close": 51.0},
            {"ts": "2026-06-26 08:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"ts": "2026-06-26 08:30", "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0},
        ])
        now_et = datetime(2026, 6, 26, 8, 30, tzinfo=ET)  # premarket

        result = resolve_today_price(frame, now_et)

        assert result is not None
        # Premarket: today_open == today_price == latest TODAY bar close (103.0).
        assert result.today_open == pytest.approx(103.0)
        assert result.today_price == pytest.approx(103.0)
        # today_high must be max of TODAY's bars (104.0), NOT the stale 999.0 high.
        assert result.today_high == pytest.approx(104.0)

    def test_resolve_rth_trailing_nan_row_uses_last_valid_close(self):
        """Regression (live probe 2026-06-26): download_intraday_1m batches via
        yf.download(group_by="ticker"), which reindexes EVERY ticker onto the UNION
        of all timestamps. The union's last row is a real print for only ONE ticker;
        every other ticker gets an all-NaN row there. So `today_frame["close"].iloc[-1]`
        is NaN for almost every symbol — NaN today_price → NaN gap_pct → the symbol
        silently fails all D-filters → empty watchlist again (different path, same
        symptom). The resolver must use the last/first VALID (non-NaN) value.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        nan = float("nan")
        # Two real RTH bars, then a trailing all-NaN union-artifact row.
        frame = _make_1m_frame([
            {"ts": "2026-06-26 09:30", "open": 102.0, "high": 106.0, "low": 101.5, "close": 105.0},
            {"ts": "2026-06-26 09:35", "open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0},
            {"ts": "2026-06-26 17:23", "open": nan, "high": nan, "low": nan, "close": nan},
        ])
        now_et = datetime(2026, 6, 26, 18, 0, tzinfo=ET)  # after close → RTH branch

        result = resolve_today_price(frame, now_et)

        assert result is not None
        # today_price must be the last VALID close (107.0), NOT the trailing NaN.
        assert result.today_price == pytest.approx(107.0)
        # today_open: first VALID regular-session open (102.0).
        assert result.today_open == pytest.approx(102.0)
        # today_high: max valid high (108.0).
        assert result.today_high == pytest.approx(108.0)

    def test_resolve_premarket_trailing_nan_row_uses_last_valid_close(self):
        """Regression (live probe): same union-NaN artifact in the PREMARKET branch —
        the latest premarket close must be the last VALID close, never the trailing NaN."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        nan = float("nan")
        frame = _make_1m_frame([
            {"ts": "2026-06-26 08:00", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"ts": "2026-06-26 08:30", "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0},
            {"ts": "2026-06-26 08:31", "open": nan, "high": nan, "low": nan, "close": nan},
        ])
        now_et = datetime(2026, 6, 26, 8, 35, tzinfo=ET)  # premarket

        result = resolve_today_price(frame, now_et)

        assert result is not None
        assert result.today_open == pytest.approx(103.0)
        assert result.today_price == pytest.approx(103.0)
        assert result.today_high == pytest.approx(104.0)

    def test_resolve_all_nan_today_bars_return_none(self):
        """If every today-dated bar is all-NaN (no usable print), fail closed."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.scanner.fetcher import resolve_today_price

        ET = ZoneInfo("America/New_York")
        nan = float("nan")
        frame = _make_1m_frame([
            {"ts": "2026-06-26 09:30", "open": nan, "high": nan, "low": nan, "close": nan},
            {"ts": "2026-06-26 09:35", "open": nan, "high": nan, "low": nan, "close": nan},
        ])
        now_et = datetime(2026, 6, 26, 10, 0, tzinfo=ET)

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


# ============================================================
# Retry-on-partial-failure: _download_batch retries failed subset
# ============================================================

class TestDownloadBatchRetry:
    """Bounded retry of the failed subset: recovered tickers are spliced into
    data and excluded from failed_set; ScanDegradationError is only raised on
    POST-retry failure rates.

    Before the fix: _download_batch makes exactly ONE yf.download call.  Any
    ticker whose frame is missing or all-NaN is permanently in failed_set for
    that scan.  If the initial failure rate is >= degradation_threshold the
    function raises ScanDegradationError even for transient yfinance
    rate-limit errors that a single retry would have resolved.

    After the fix: up to _RETRY_MAX_ATTEMPTS retries of just the failed subset
    are made (with backoff); recovered tickers are spliced back into data;
    failure_rate and ScanDegradationError are evaluated ONLY on the post-retry
    final failed set.
    """

    def test_failed_subset_retried_and_recovered_does_not_raise(self, monkeypatch):
        """Core retry contract (TDD RED): a ticker that fails on attempt 1 but
        returns valid data on attempt 2 must appear in data, be absent from
        failed_set, and must NOT trigger ScanDegradationError even though the
        initial failure rate was >= degradation_threshold.

        Setup:
          10 symbols, degradation_threshold=0.10 (default).
          Attempt 1: NDSN returns all-NaN -> 1/10 = 10% -> AT threshold ->
                     ScanDegradationError raised without retry.
          Retry (attempt 2): yf.download called with ["NDSN"] only -> returns
                     valid data -> failure_rate drops to 0% -> no raise.

        This test MUST FAIL before the fix: without retry, the 10% initial
        failure rate raises ScanDegradationError instead of returning (data, set()).
        """
        import yfinance.shared as yf_shared

        all_syms = [
            "AAPL", "MSFT", "GOOG", "NFLX", "NVDA",
            "AMZN", "META", "TSLA", "BRKB", "NDSN",
        ]
        ok_syms = all_syms[:-1]  # everyone except NDSN

        # Attempt-1 result: NDSN present but all-NaN (yfinance 1.4.1 failure shape)
        attempt1_data = {
            **{sym: _make_ticker_df(sym) for sym in ok_syms},
            "NDSN": _make_nan_ticker_df(),
        }
        # Retry result: called with ["NDSN"] only; returns valid data for NDSN
        retry_data = {"NDSN": _make_ticker_df("NDSN")}

        download_call_tickers: list = []

        def fake_yf_download(**kwargs):
            tickers = kwargs.get("tickers", [])
            if isinstance(tickers, str):
                tickers = [tickers]
            download_call_tickers.append(list(tickers))
            if len(download_call_tickers) == 1:
                return attempt1_data
            return retry_data

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            # Zero out the backoff so the test runs instantly.
            # raising=False: before the fix _RETRY_BACKOFF_S doesn't exist yet;
            # the no-op lets the test fail for the CORRECT reason (ScanDegradationError)
            # rather than AttributeError. After the fix it patches to 0.0 correctly.
            monkeypatch.setattr("bot.scanner.fetcher._RETRY_BACKOFF_S", 0.0, raising=False)
            with patch("yfinance.download", side_effect=fake_yf_download), \
                 patch("bot.scanner.fetcher.append_audit"):
                from bot.scanner.fetcher import (
                    download_daily_bars,
                    ScanDegradationError,
                    get_ticker_frame,
                )
                # Must NOT raise: NDSN recovers on retry -> 0% final failure rate
                data, failed = download_daily_bars(all_syms)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        # 1. Recovered ticker must be absent from failed_set
        assert "NDSN" not in failed, (
            "NDSN was recovered on retry and must not remain in failed_set"
        )
        assert failed == set(), (
            "All failures must be resolved by retry; failed_set must be empty"
        )

        # 2. Recovered ticker must be accessible via get_ticker_frame (splice correctness)
        ndsn_frame = get_ticker_frame(data, "NDSN")
        assert ndsn_frame is not None, (
            "get_ticker_frame must return valid data for NDSN after retry splice; "
            "got None — merged frame is missing or still all-NaN"
        )

        # 3. Retry must have been for the failed subset only (not the full universe)
        assert len(download_call_tickers) >= 2, (
            f"Expected at least 2 yf.download calls (initial + retry), "
            f"got {len(download_call_tickers)}"
        )
        assert download_call_tickers[1] == ["NDSN"], (
            f"Second yf.download call must be for the failed subset only, "
            f"got {download_call_tickers[1]!r} instead of ['NDSN']"
        )

    def test_post_retry_failure_rate_evaluated_not_initial(self, monkeypatch):
        """ScanDegradationError must be evaluated against POST-RETRY failures.

        20 symbols, degradation_threshold=0.10.
        Attempt 1: 3 symbols fail (SYM00-SYM02) -> 3/20 = 15% > threshold.
        Retry: all 3 recover -> 0/20 = 0% -> no ScanDegradationError.

        Without retry: raises immediately at 15%.
        With retry:  recovers -> returns (data, set()).
        """
        import yfinance.shared as yf_shared

        total = 20
        all_syms = [f"SYM{i:02d}" for i in range(total)]
        fail_set = {"SYM00", "SYM01", "SYM02"}
        ok_syms = [s for s in all_syms if s not in fail_set]

        attempt1_data = {
            **{sym: _make_ticker_df(sym) for sym in ok_syms},
            **{sym: _make_nan_ticker_df() for sym in fail_set},
        }
        retry_data = {sym: _make_ticker_df(sym) for sym in fail_set}

        call_count = [0]

        def fake_download(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return attempt1_data
            return retry_data

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            monkeypatch.setattr("bot.scanner.fetcher._RETRY_BACKOFF_S", 0.0, raising=False)
            with patch("yfinance.download", side_effect=fake_download), \
                 patch("bot.scanner.fetcher.append_audit"):
                from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
                # Must NOT raise: 3/20 initial failure rate resolves to 0% after retry
                data, failed = download_daily_bars(all_syms)
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert failed == set(), (
            "All 3 failures must be recovered on retry; failed_set must be empty"
        )
        # Exactly 2 calls: initial (20 symbols) + one retry (3 symbols)
        assert call_count[0] == 2, (
            f"Expected exactly 2 yf.download calls, got {call_count[0]}"
        )

    def test_still_failed_after_retries_raises_degradation(self, monkeypatch):
        """If failures persist through ALL retry attempts, ScanDegradationError
        is still raised (with the POST-retry count).

        10 symbols, 1 fails on every attempt (never recovers) -> 10% -> raise.
        Verifies the degradation gate is preserved even with retry logic.
        """
        import yfinance.shared as yf_shared

        all_syms = ["AAPL", "MSFT", "GOOG", "NFLX", "NVDA",
                    "AMZN", "META", "TSLA", "BRKB", "NDSN"]
        ok_syms = all_syms[:-1]

        # Every call returns the same all-NaN for NDSN; it never recovers
        persistent_fail_data = {
            **{sym: _make_ticker_df(sym) for sym in ok_syms},
            "NDSN": _make_nan_ticker_df(),
        }

        original_errors = dict(yf_shared._ERRORS)
        try:
            yf_shared._ERRORS.clear()
            monkeypatch.setattr("bot.scanner.fetcher._RETRY_BACKOFF_S", 0.0, raising=False)
            with patch("yfinance.download", return_value=persistent_fail_data), \
                 patch("bot.scanner.fetcher.append_audit") as mock_audit:
                from bot.scanner.fetcher import download_daily_bars, ScanDegradationError
                with pytest.raises(ScanDegradationError) as exc_info:
                    download_daily_bars(all_syms)
                # D-07 audit must still be emitted with final (post-retry) numbers
                mock_audit.assert_called_once()
                audit_payload = mock_audit.call_args[0][0]
                assert audit_payload["event"] == "scan_aborted_data_degradation"
                assert audit_payload["failed_count"] == 1
                assert audit_payload["total"] == 10
        finally:
            yf_shared._ERRORS.clear()
            yf_shared._ERRORS.update(original_errors)

        assert "1/10 symbols failed" in str(exc_info.value)


# ============================================================
# Phase 7 SIG-RVOL-TOD: download_intraday_5m — 30-day 5m history downloader
# ============================================================

class TestDownloadIntraday5m:
    """SCAN-06 extension: download_intraday_5m mirrors download_intraday_1m contract
    for 30-day 5m regular-session bar history used in TOD baseline computation.

    Verified behaviors:
      (a) delegates to _download_batch with download_kwargs={"period":"30d","interval":"5m","prepost":False}
      (b) passes through threads and degradation_threshold, returns (frame, failed_set) unchanged
      (c) abort/partial event names are the tod_baseline_* names
    """

    def test_download_intraday_5m_kwargs(self):
        """download_intraday_5m delegates to _download_batch with 5m/30d/prepost=False kwargs."""
        from unittest.mock import patch, MagicMock

        symbols = ["AAPL", "MSFT"]
        sentinel_frame = {"AAPL": MagicMock(), "MSFT": MagicMock()}
        sentinel_failed = set()

        with patch("bot.scanner.fetcher._download_batch",
                   return_value=(sentinel_frame, sentinel_failed)) as mock_batch:
            from bot.scanner.fetcher import download_intraday_5m
            frame, failed = download_intraday_5m(symbols, threads=3, degradation_threshold=0.15)

        mock_batch.assert_called_once()
        call_kwargs = mock_batch.call_args[1]
        dl_kwargs = call_kwargs.get("download_kwargs", {})
        assert dl_kwargs.get("interval") == "5m", (
            f"download_intraday_5m must use interval='5m'; got {dl_kwargs.get('interval')!r}"
        )
        assert dl_kwargs.get("period") == "30d", (
            f"download_intraday_5m must use period='30d'; got {dl_kwargs.get('period')!r}"
        )
        assert dl_kwargs.get("prepost") is False, (
            f"download_intraday_5m must use prepost=False (regular-session only); "
            f"got {dl_kwargs.get('prepost')!r}"
        )

    def test_download_intraday_5m_passthrough(self):
        """threads and degradation_threshold are passed through to _download_batch unchanged."""
        from unittest.mock import patch, MagicMock

        symbols = ["NVDA"]
        sentinel = ({"NVDA": MagicMock()}, {"FAIL1"})

        with patch("bot.scanner.fetcher._download_batch",
                   return_value=sentinel) as mock_batch:
            from bot.scanner.fetcher import download_intraday_5m
            result = download_intraday_5m(symbols, threads=7, degradation_threshold=0.25)

        assert result == sentinel, "download_intraday_5m must return _download_batch output unchanged"

        call_kwargs = mock_batch.call_args[1]
        assert call_kwargs.get("threads") == 7, (
            f"threads must be passed through to _download_batch; got {call_kwargs.get('threads')}"
        )
        assert call_kwargs.get("degradation_threshold") == 0.25, (
            f"degradation_threshold must be passed through; "
            f"got {call_kwargs.get('degradation_threshold')}"
        )

    def test_download_intraday_5m_event_names(self):
        """Abort and partial event names must be the tod_baseline_* strings."""
        from unittest.mock import patch, MagicMock

        symbols = ["TSLA"]
        sentinel = ({"TSLA": MagicMock()}, set())

        with patch("bot.scanner.fetcher._download_batch",
                   return_value=sentinel) as mock_batch:
            from bot.scanner.fetcher import download_intraday_5m
            download_intraday_5m(symbols)

        call_kwargs = mock_batch.call_args[1]
        assert call_kwargs.get("abort_event") == "tod_baseline_scan_aborted_data_degradation", (
            f"abort_event must be 'tod_baseline_scan_aborted_data_degradation'; "
            f"got {call_kwargs.get('abort_event')!r}"
        )
        assert call_kwargs.get("partial_event") == "tod_baseline_partial_data", (
            f"partial_event must be 'tod_baseline_partial_data'; "
            f"got {call_kwargs.get('partial_event')!r}"
        )
