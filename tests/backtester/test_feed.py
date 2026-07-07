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
    """A second SimulatedBarFeed over the same (symbol, range) hits the CSV cache -- 0 calls."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()

        feed1 = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                  cache_dir=str(tmp_path))
        list(feed1.replay("2026-06-01"))
        assert mock_dl.call_count == 1

        feed2 = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                  cache_dir=str(tmp_path))
        list(feed2.replay("2026-06-01"))
        assert mock_dl.call_count == 1, "second load must hit the CSV cache, not the network"


def test_moomoo_code_normalization_via_yfinance_to_moomoo(tmp_path):
    """Bar dicts carry moomoo-format codes derived via yfinance_to_moomoo (BRK-B edge case)."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.BRK-B"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        bars = list(feed.replay("2026-06-01"))
    assert bars
    assert all(b["code"] == "US.BRK-B" for b in bars)


def test_synthetic_today_price_uses_first_rth_bar(tmp_path):
    """synthetic_today_price mirrors resolve_today_price's RTH branch from loaded 5m bars."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        list(feed.replay("2026-06-01"))
        today_price = feed.synthetic_today_price("US.TEST", "2026-06-01")

    assert today_price is not None
    assert today_price.today_open == 100.00, "today_open must equal the first RTH bar's open"
    assert today_price.today_price == 107.50, "today_price must equal the latest RTH bar's close"
    assert today_price.today_high == 108.00, "today_high must equal the max RTH high"


def test_premarket_highs_excludes_bars_at_or_after_0930(tmp_path):
    """premarket_highs() counts only ET bars before 09:30 -- a 09:35 bar's high must not count."""
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = _make_multi_bar_frame()
        feed = SimulatedBarFeed(["US.TEST"], start="2026-06-01", end="2026-06-01",
                                 cache_dir=str(tmp_path))
        list(feed.replay("2026-06-01"))

        mock_dl.return_value = _make_premarket_5m_frame()
        highs = feed.premarket_highs("2026-06-01")

    assert highs == {"US.TEST": 102.0}, (
        "the 09:35 bar's high=250.0 must be excluded -- only pre-09:30 bars count"
    )


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
