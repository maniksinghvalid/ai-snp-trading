#!/usr/bin/env python3
"""
tests.backtester.test_feed — BT-04: SimulatedBarFeed 5m replay + window guard.

Wave 0 stub (plan 06-01): `pytest.importorskip` guards this whole module until
backtester/feed.py exists (plan 06-02) — the suite SKIPs cleanly until then, then these
real assertions run RED -> GREEN. See 06-RESEARCH Validation Architecture / 06-PATTERNS
feed.py section for the API surface asserted below.

Covers:
  - replay(day) yields bars in chronological order with computed hod/lod/cum_volume
  - next_bar(code, after) returns the strictly-next bar, or None past the end of data
  - an out-of-window --start (older than the yfinance ~58-trading-day 5m window, no cache
    hit) raises BacktestWindowError loudly -- never a silent empty bar list (Pitfall 2)
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

mod = pytest.importorskip("backtester.feed")

SimulatedBarFeed = mod.SimulatedBarFeed
BacktestWindowError = mod.BacktestWindowError


def _empty_yf_download(*args, **kwargs):
    """Mock yfinance.download returning an empty frame -- feed must not silently proceed."""
    import pandas as pd
    return pd.DataFrame()


def test_replay_yields_chronologically_ordered_bars_with_computed_running_fields(tmp_path):
    """replay(day) yields bars sorted by time_key with point-in-time hod/lod/cum_volume."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        bars = list(feed.replay("2026-06-01"))

    assert len(bars) >= 3
    time_keys = [b["time_key"] for b in bars if b["code"] == "US.TEST"]
    assert time_keys == sorted(time_keys), "replay() must yield strictly chronological bars"

    running_hod = float("-inf")
    for b in bars:
        if b["code"] != "US.TEST":
            continue
        running_hod = max(running_hod, b["high"])
        assert b["hod"] == running_hod, "hod must be the running max-high through this bar"


def test_next_bar_returns_strictly_next_bar_or_none_at_end(tmp_path):
    """next_bar(code, after) returns the first bar with time_key > after, else None."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        list(feed.replay("2026-06-01"))  # populate feed's internal bar store

        first_key = "2026-06-01 09:30:00"
        nxt = feed.next_bar("US.TEST", after=first_key)
        assert nxt is not None
        assert nxt["time_key"] > first_key

        last_key = "2026-06-01 09:55:00"
        assert feed.next_bar("US.TEST", after=last_key) is None, (
            "next_bar past the last bar must return None, never raise or wrap around"
        )


def test_out_of_window_start_raises_backtest_window_error_not_silent_empty(tmp_path):
    """A --start far older than the yfinance rolling 5m window (no cache) raises loudly."""
    ancient_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    with patch("yfinance.download", side_effect=_empty_yf_download):
        with pytest.raises(BacktestWindowError):
            feed = SimulatedBarFeed(["US.TEST"], start=ancient_start, end=ancient_start,
                                     cache_dir=str(tmp_path))
            list(feed.replay(ancient_start))


def test_second_load_reads_csv_cache_zero_network_calls(tmp_path):
    """A second SimulatedBarFeed over the same (symbol, range) hits the CSV cache -- 0 calls.

    First construction makes 2 network calls (the RTH _load_5m miss + the premarket
    _load_premarket miss, each cached under a distinct interval tag); the second
    construction over the identical range must hit both CSV caches and add zero calls.
    """
    with patch("yfinance.download", side_effect=_dispatch_by_prepost) as mock_dl:
        feed1 = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                  cache_dir=str(tmp_path))
        list(feed1.replay("2026-06-01"))
        assert mock_dl.call_count == 2

        feed2 = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                  cache_dir=str(tmp_path))
        list(feed2.replay("2026-06-01"))
        assert mock_dl.call_count == 2, "second load must hit the CSV cache, not the network"


def test_moomoo_code_normalization_via_yfinance_to_moomoo(tmp_path):
    """Bar dicts carry moomoo-format codes derived via yfinance_to_moomoo (BRK-B edge case)."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.BRK-B"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        bars = list(feed.replay("2026-06-01"))
    assert bars
    assert all(b["code"] == "US.BRK-B" for b in bars)


def _dispatch_by_prepost(*args, **kwargs):
    """yfinance.download side_effect: routes to the premarket fixture when the feed's
    internal _load_premarket() call passes prepost=True, else the RTH fixture -- mirrors
    the feed's two distinct kwargs shapes (_load_5m: prepost=False, _load_premarket:
    prepost=True) so a single mock exercises both loads with day-appropriate data."""
    if kwargs.get("prepost"):
        return _make_premarket_5m_frame()
    return _make_multi_bar_frame()


def test_premarket_highs_excludes_bars_at_or_after_0930(tmp_path):
    """premarket_highs() counts only ET bars before 09:30 -- a 09:35 bar's high must not count."""
    with patch("yfinance.download", side_effect=_dispatch_by_prepost):
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        highs = feed.premarket_highs("2026-06-01")

    assert highs == {"US.TEST": 102.0}, (
        "the 09:35 bar's high=250.0 must be excluded -- only pre-09:30 bars count"
    )


def test_premarket_highs_keyed_correctly_per_distinct_day(tmp_path):
    """premarket_highs(day) for two loaded days each returns THAT day's own pre-09:30 max
    -- not the other day's, and not stale/empty (CR-02, point-in-time for any replay day)."""
    def _two_day_dispatch(*args, **kwargs):
        if kwargs.get("prepost"):
            return _make_two_day_premarket_frame()
        return _make_two_day_frame()  # RTH bars on BOTH days -- satisfies the coverage guard

    with patch("yfinance.download", side_effect=_two_day_dispatch):
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-02",
                                 cache_dir=str(tmp_path))

    highs_day1 = feed.premarket_highs("2026-06-01")
    highs_day2 = feed.premarket_highs("2026-06-02")
    assert highs_day1 == {"US.TEST": 102.0}
    assert highs_day2 == {"US.TEST": 111.0}
    assert highs_day1 != highs_day2


def test_synthetic_today_price_is_premarket_only_not_rth(tmp_path):
    """synthetic_today_price mirrors resolve_today_price's PREMARKET branch (CR-07):
    today_price = latest premarket close, today_high = max premarket high -- both must
    differ from the day's RTH close (107.50) / RTH max high (108.00) fixture values."""
    with patch("yfinance.download", side_effect=_dispatch_by_prepost):
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        today_price = feed.synthetic_today_price("US.TEST", "2026-06-01")

    assert today_price is not None
    assert today_price.today_price == 101.5, "today_price must equal the latest premarket bar's close"
    assert today_price.today_high == 102.0, "today_high must equal the max premarket high"
    assert today_price.today_open == today_price.today_price
    assert today_price.today_price != 107.50, "must never equal the day's RTH close"
    assert today_price.today_high != 108.00, "must never equal the day's RTH max high"


def test_synthetic_today_price_none_without_premarket_bars(tmp_path):
    """synthetic_today_price returns None for a day/code with zero premarket bars."""
    with patch("yfinance.download", side_effect=_dispatch_by_prepost):
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        assert feed.synthetic_today_price("US.TEST", "2099-01-01") is None


def _make_two_day_premarket_frame():
    """prepost=True 5m frame with distinct pre-09:30 highs on two different days: day1 max
    high 102.0 (same as _make_premarket_5m_frame), day2 max high 111.0."""
    import pandas as pd

    index = pd.to_datetime([
        "2026-06-01 09:00:00", "2026-06-01 09:15:00", "2026-06-01 09:35:00",
        "2026-06-02 09:00:00", "2026-06-02 09:15:00",
    ]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [100.0, 100.5, 200.0, 109.0, 110.0],
        "High": [101.0, 102.0, 250.0, 110.0, 111.0],
        "Low": [99.5, 100.0, 199.0, 108.5, 109.5],
        "Close": [100.5, 101.5, 220.0, 109.5, 110.5],
        "Volume": [500, 600, 900, 400, 400],
    }, index=index)


def test_traversal_symbol_rejected_before_any_cache_or_network_access(tmp_path):
    """T-06-03: a symbol with path separators must raise ValueError, never build a cache path."""
    with patch("yfinance.download") as mock_dl:
        with pytest.raises(ValueError):
            SimulatedBarFeed(["US.../../evil"], start="2026-06-01", end="2026-06-01",
                             cache_dir=str(tmp_path))
        mock_dl.assert_not_called()
    assert list(tmp_path.iterdir()) == [], "no cache file may be created for a rejected symbol"


def test_window_guard_and_fetch_agree_at_60_calendar_days(tmp_path):
    """CR-03: --start 55 calendar days back (no cache) loads bars; 90 days back raises."""
    near_start = (datetime.now() - timedelta(days=55)).strftime("%Y-%m-%d")
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame_for(near_start)
        feed = SimulatedBarFeed(["US.TEST"], start=near_start, end=near_start,
                                 cache_dir=str(tmp_path))
    assert feed._bars_by_code.get("US.TEST"), "55-day-back start must load replay bars"

    far_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    with patch("yfinance.download", side_effect=_empty_yf_download):
        with pytest.raises(BacktestWindowError):
            SimulatedBarFeed(["US.TEST"], start=far_start, end=far_start,
                              cache_dir=str(tmp_path))


def test_coverage_guard_names_the_uncovered_trading_day(tmp_path):
    """CR-03: a two-trading-day range with bars for only day 1 raises BacktestWindowError
    naming day 2."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()  # bars only on 2026-06-01
        with pytest.raises(BacktestWindowError) as exc_info:
            SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-02",
                             cache_dir=str(tmp_path))
    assert "2026-06-02" in str(exc_info.value)


def test_next_bar_never_crosses_a_session_boundary(tmp_path):
    """CR-04: next_bar must not return the following day's open as the "next" bar."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_two_day_frame()
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-02",
                                 cache_dir=str(tmp_path))

    assert feed.next_bar("US.TEST", after="2026-06-01 15:55:00") is None, (
        "day D's last bar must not fill on day D+1's open"
    )
    nxt = feed.next_bar("US.TEST", after="2026-06-01 10:05:00")
    assert nxt is not None
    assert nxt["time_key"] == "2026-06-01 10:10:00"


def _make_multi_bar_frame_for(day: str):
    """Same shape as _make_multi_bar_frame() but dated `day` (YYYY-MM-DD)."""
    import pandas as pd

    index = pd.to_datetime([
        f"{day} 09:30:00", f"{day} 09:35:00", f"{day} 09:40:00",
    ]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [100.00, 100.20, 100.60],
        "High": [100.50, 100.80, 101.00],
        "Low": [99.50, 100.00, 100.30],
        "Close": [100.20, 100.60, 100.90],
        "Volume": [10_000, 9_000, 8_000],
    }, index=index)


def _make_two_day_frame():
    """Title-Case 5m frame spanning two NYSE trading days (2026-06-01, 2026-06-02)."""
    import pandas as pd

    index = pd.to_datetime([
        "2026-06-01 10:05:00", "2026-06-01 10:10:00", "2026-06-01 15:55:00",
        "2026-06-02 09:30:00", "2026-06-02 09:35:00",
    ]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [100.00, 100.20, 100.60, 101.00, 101.20],
        "High": [100.50, 100.80, 101.00, 101.50, 101.80],
        "Low": [99.50, 100.00, 100.30, 100.80, 101.00],
        "Close": [100.20, 100.60, 100.90, 101.20, 101.60],
        "Volume": [10_000, 9_000, 8_000, 7_000, 6_000],
    }, index=index)


def test_union_index_nan_rows_do_not_crash_or_poison_premarket_high(tmp_path):
    """06-VERIFICATION Gap 1: a realistic multi-ticker frame with all-NaN union-index
    padding rows (the NORMAL shape yf.download(group_by="ticker") emits when a symbol
    lacks a bar at a peer's timestamp -- halts, illiquid names, staggered premarket
    coverage) must not crash SimulatedBarFeed construction (T-06-10-01) and must never
    poison premarket_highs() with a NaN (T-06-10-02). US.LIQUID has real bars at every
    timestamp; US.HALTED shares those same timestamps but is NaN at one RTH bar and one
    premarket bar -- the exact union-index padding shape, not an all-NaN frame (which
    get_ticker_frame already filters out entirely, so it wouldn't reach _materialize_bars
    at all)."""
    import math

    import pandas as pd
    import pandas_market_calendars as mcal

    # Runtime-derived recent NYSE trading day -- no hardcoded date literal, so this test
    # never contributes to the 06-12 window-guard time bomb (_enforce_window requires
    # start within ~60 calendar days of "now").
    day = mcal.get_calendar("NYSE").valid_days(
        start_date=(datetime.now() - timedelta(days=15)).date(),
        end_date=(datetime.now() - timedelta(days=2)).date(),
    )[-1].strftime("%Y-%m-%d")

    rth_index = pd.to_datetime([f"{day} 09:30:00", f"{day} 09:35:00"]).tz_localize(
        "America/New_York"
    )
    pre_index = pd.to_datetime([f"{day} 09:00:00", f"{day} 09:15:00"]).tz_localize(
        "America/New_York"
    )

    liquid_rth = pd.DataFrame(
        {"Open": [100.0, 100.5], "High": [101.0, 101.5], "Low": [99.5, 100.0],
         "Close": [100.5, 101.0], "Volume": [10_000, 11_000]},
        index=rth_index,
    )
    liquid_pre = pd.DataFrame(
        {"Open": [98.0, 98.5], "High": [99.0, 99.5], "Low": [97.5, 98.0],
         "Close": [98.5, 99.0], "Volume": [500, 600]},
        index=pre_index,
    )
    # US.HALTED: real bar at the FIRST shared timestamp, all-NaN union-index padding at
    # the SECOND -- reproduces the verifier's exact crash site (int(row["volume"]) on a
    # NaN row) while keeping the frame as a whole non-all-NaN (get_ticker_frame only
    # filters a frame that is NaN in EVERY row).
    halted_rth = pd.DataFrame(
        {"Open": [55.0, float("nan")], "High": [56.0, float("nan")],
         "Low": [54.5, float("nan")], "Close": [55.5, float("nan")],
         "Volume": [2_000, float("nan")]},
        index=rth_index,
    )
    halted_pre = pd.DataFrame(
        {"Open": [40.0, float("nan")], "High": [41.0, float("nan")],
         "Low": [39.5, float("nan")], "Close": [40.5, float("nan")],
         "Volume": [300, float("nan")]},
        index=pre_index,
    )

    def _dispatch_union_padded(*_args, **kwargs):
        tickers = kwargs.get("tickers") or []
        if isinstance(tickers, str):
            tickers = [tickers]
        interval = kwargs.get("interval")
        prepost = kwargs.get("prepost", False)
        if interval != "5m":
            return {}
        result = {}
        if prepost:
            if "LIQUID" in tickers:
                result["LIQUID"] = liquid_pre
            if "HALTED" in tickers:
                result["HALTED"] = halted_pre
        else:
            if "LIQUID" in tickers:
                result["LIQUID"] = liquid_rth
            if "HALTED" in tickers:
                result["HALTED"] = halted_rth
        return result

    with patch("yfinance.download", side_effect=_dispatch_union_padded):
        # Pre-fix: raised ValueError: cannot convert float NaN to integer.
        feed = SimulatedBarFeed(
            ["US.LIQUID", "US.HALTED"], start=day, end=day, cache_dir=str(tmp_path)
        )
        highs = feed.premarket_highs(day)

    assert highs, "at least one code must have a premarket high"
    for code, high in highs.items():
        assert not math.isnan(high), f"{code}'s premarket high must never be NaN"
    if "US.HALTED" in highs:
        assert highs["US.HALTED"] == 41.0, "HALTED's real premarket bar must survive dropna"

    # Mirrors the verifier's own reproduction directly: an all-NaN lowercase-column frame
    # fed straight into _materialize_bars must not raise.
    all_nan_frame = pd.DataFrame(
        {"open": [float("nan")], "high": [float("nan")], "low": [float("nan")],
         "close": [float("nan")], "volume": [float("nan")]},
        index=pd.to_datetime([f"{day} 09:40:00"]).tz_localize("America/New_York"),
    )
    assert feed._materialize_bars("HALTED", all_nan_frame) == []


def test_dashed_and_dotted_real_tickers_still_accepted(tmp_path):
    """T-06-03 guard must not reject legitimate symbol shapes (BRK-B, BF.B)."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.BRK-B"], start="2026-06-01",
                                end="2026-06-01", cache_dir=str(tmp_path))
    assert feed.codes == ["US.BRK-B"]


def _make_premarket_5m_frame():
    """Title-Case 5m frame with premarket (< 09:30 ET) and one regular-session bar."""
    import pandas as pd

    index = pd.to_datetime([
        "2026-06-01 09:00:00", "2026-06-01 09:15:00", "2026-06-01 09:35:00",
    ]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [100.0, 100.5, 200.0],
        "High": [101.0, 102.0, 250.0],
        "Low": [99.5, 100.0, 199.0],
        "Close": [100.5, 101.5, 220.0],
        "Volume": [500, 600, 900],
    }, index=index)


def _make_multi_bar_frame():
    """Title-Case OHLCV frame mimicking a raw yf.download() 5m result for one symbol."""
    import pandas as pd

    index = pd.to_datetime([
        "2026-06-01 09:30:00", "2026-06-01 09:35:00", "2026-06-01 09:40:00",
        "2026-06-01 09:45:00", "2026-06-01 09:50:00", "2026-06-01 09:55:00",
    ]).tz_localize("America/New_York")
    return pd.DataFrame({
        "Open": [100.00, 100.20, 100.60, 100.90, 103.50, 103.80],
        "High": [100.50, 100.80, 101.00, 105.50, 104.00, 108.00],
        "Low": [99.50, 100.00, 100.30, 100.80, 103.00, 103.70],
        "Close": [100.20, 100.60, 100.90, 105.00, 103.80, 107.50],
        "Volume": [10_000, 9_000, 8_000, 50_000, 20_000, 30_000],
    }, index=index)
