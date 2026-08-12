#!/usr/bin/env python3
"""tests.backtester.test_feed_massive — SimulatedBarFeed source="massive" path (no network).

Fixed historical dates on purpose: the massive path must NOT apply the yfinance
~60-day _enforce_window guard, so dates far in the past are exactly the case to
prove. 2025-03-07/10/11/12 are real NYSE trading days (post-DST: EDT offsets).
"""
import pandas as pd
import pytest

from bot.safety.et_helpers import ET
from backtester.feed import BacktestWindowError, SimulatedBarFeed

PRIOR = "2025-03-07"  # prior session — feeds TOD baselines, must never replay
DAY1, DAY2 = "2025-03-10", "2025-03-11"


def _frame(rows):
    """rows: (iso_ts_et, o, h, l, c, v) -> Title-Case frame with tz-aware ET index."""
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=pd.DatetimeIndex([pd.Timestamp(r[0], tz=ET) for r in rows]),
    )


class FakeMassive:
    """cached_bars stand-in: canned frames keyed by interval_tag; records calls."""

    def __init__(self, frames_5m, frames_daily):
        self._by_tag = {"5m": frames_5m, "1d": frames_daily}
        self.calls = []

    def cached_bars(self, sym, interval_tag, multiplier, timespan, start, end):
        self.calls.append((sym, interval_tag, start, end))
        return self._by_tag[interval_tag].get(sym, pd.DataFrame())


def _fake(sym="AAPL"):
    five = _frame([
        (f"{PRIOR} 09:30:00", 1.0, 1.0, 1.0, 1.0, 100),      # prior RTH (TOD only)
        (f"{DAY1} 09:00:00", 9.0, 9.5, 8.9, 9.2, 500),       # premarket
        (f"{DAY1} 09:30:00", 10.0, 10.5, 9.8, 10.2, 1000),
        (f"{DAY1} 09:35:00", 10.2, 10.8, 10.1, 10.6, 1200),
        (f"{DAY1} 16:00:00", 99.0, 99.0, 99.0, 99.0, 1),     # after-hours: excluded
        (f"{DAY2} 09:30:00", 11.0, 11.5, 10.9, 11.2, 900),
    ])
    daily = _frame([(f"{PRIOR} 00:00:00", 9.0, 9.0, 9.0, 9.0, 10_000)])
    return FakeMassive({sym: five}, {sym: daily})


def test_massive_replay_bars_are_rth_in_range_only(tmp_path):
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=_fake())
    bars = list(feed.replay(DAY1))
    assert [b["time_key"] for b in bars] == [f"{DAY1} 09:30:00", f"{DAY1} 09:35:00"]
    assert bars[0]["hod"] == 10.5 and bars[1]["cum_volume"] == 2200
    assert list(feed.replay(DAY2))[0]["time_key"] == f"{DAY2} 09:30:00"


def test_massive_premarket_highs_and_synthetic_today_price(tmp_path):
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=_fake())
    assert feed.premarket_highs(DAY1) == {"US.AAPL": 9.5}
    tp = feed.synthetic_today_price("US.AAPL", DAY1)
    assert tp.today_price == 9.2 and tp.today_high == 9.5


def test_massive_daily_and_tod_accessors_use_preloaded_frames(tmp_path):
    fake = _fake()
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=fake)
    n_fetches = len(fake.calls)
    daily = feed.daily_bars()
    tod = feed.intraday_5m_for_tod()
    assert len(fake.calls) == n_fetches  # no new fetches per accessor call
    assert "Volume" in daily["AAPL"].columns
    tod_keys = [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in tod["AAPL"].index]
    assert f"{PRIOR} 09:30:00" in tod_keys           # padded prior session kept
    assert f"{DAY1} 09:00:00" not in tod_keys        # premarket excluded
    assert f"{DAY1} 16:00:00" not in tod_keys        # after-hours excluded


def test_massive_skips_window_guard_but_keeps_coverage_guard(tmp_path):
    # DAY1/DAY2 are far outside yfinance's ~60-day window: massive loads fine...
    SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                     source="massive", massive=_fake())
    # ...but a trading day with zero bars anywhere still fails loudly (CR-03).
    with pytest.raises(BacktestWindowError):
        SimulatedBarFeed(["US.AAPL"], DAY1, "2025-03-12", cache_dir=str(tmp_path),
                         source="massive", massive=_fake())


def test_massive_requires_source_instance(tmp_path):
    with pytest.raises(ValueError):
        SimulatedBarFeed(["US.AAPL"], DAY1, DAY1, cache_dir=str(tmp_path),
                         source="massive")
