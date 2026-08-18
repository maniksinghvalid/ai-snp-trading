#!/usr/bin/env python3
"""
tests.backtester.experimental.test_indicators — hand-computed values for
backtester.experimental.indicators (Phase 10, plan 10-02 Task 1).

Plain def test_* functions, no pytest markers (project convention). No network,
no bot/ writes — pure math against tiny, hand-built series/frames.
"""
import math

import pandas as pd
import pytest

from bot.safety.et_helpers import ET
from backtester.experimental import indicators as ind


def _et_index(day: str, times: list) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [pd.Timestamp(f"{day} {t}", tz=ET) for t in times]
    )


# ============================================================
# sma / ema / macd / atr
# ============================================================

def test_sma_hand_computed():
    series = pd.Series([float(x) for x in range(1, 11)])  # 1..10
    result = ind.sma(series, 3)
    assert math.isnan(result.iloc[0])
    assert math.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)   # mean(1,2,3)
    assert result.iloc[-1] == pytest.approx(9.0)  # mean(8,9,10)


def test_ema_uses_ewm_span_adjust_false_hand_computed():
    series = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ind.ema(series, span=3)  # alpha = 2/(3+1) = 0.5
    # e0=1; e1=0.5*2+0.5*1=1.5; e2=0.5*3+0.5*1.5=2.25; e3=0.5*4+0.5*2.25=3.125
    assert result.tolist() == pytest.approx([1.0, 1.5, 2.25, 3.125])


def test_macd_hand_computed_small_series():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    macd_line, signal_line = ind.macd(close, fast=2, slow=3, signal=2)
    assert macd_line.iloc[-1] == pytest.approx(0.4436728, rel=1e-5)
    assert signal_line.iloc[-1] == pytest.approx(0.4099794, rel=1e-5)


def test_atr_wilder_hand_computed():
    high = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])
    low = pd.Series([8.0, 9.0, 10.0, 9.0, 10.0])
    close = pd.Series([9.0, 10.0, 11.0, 10.0, 12.0])
    result = ind.atr(high, low, close, period=3)
    # TR = [2, 2, 2, 2, 3]; seed ATR[2] = mean(2,2,2) = 2.0
    # ATR[3] = (2.0*2 + 2)/3 = 2.0; ATR[4] = (2.0*2 + 3)/3 = 7/3
    assert math.isnan(result.iloc[0])
    assert math.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(7.0 / 3.0)


# ============================================================
# session_vwap / opening_range / prev_session_low_high
# ============================================================

def test_session_vwap_typical_price_and_session_reset():
    idx = _et_index("2026-01-05", ["09:30:00", "09:35:00"]).append(
        _et_index("2026-01-06", ["09:30:00"])
    )
    frame = pd.DataFrame({
        "high": [11.0, 13.0, 20.0],
        "low": [9.0, 11.0, 18.0],
        "close": [10.0, 12.0, 19.0],
        "volume": [100, 200, 50],
    }, index=idx)

    result = ind.session_vwap(frame)

    tp0 = (11.0 + 9.0 + 10.0) / 3.0  # 10.0
    assert result.iloc[0] == pytest.approx(tp0)  # first bar of session: vwap == its own tp

    tp1 = (13.0 + 11.0 + 12.0) / 3.0  # 12.0
    expected1 = (tp0 * 100 + tp1 * 200) / 300
    assert result.iloc[1] == pytest.approx(expected1)

    # New session date -> resets, does NOT carry session 1's cumulative sums
    tp2 = (20.0 + 18.0 + 19.0) / 3.0  # 19.0
    assert result.iloc[2] == pytest.approx(tp2)


def test_opening_range_nan_until_complete_then_frozen_resets_per_session():
    idx = _et_index("2026-01-05", ["09:30:00", "09:35:00", "09:40:00", "09:45:00"])
    frame = pd.DataFrame({
        "high": [10.0, 12.0, 8.0, 15.0],
        "low": [9.0, 10.0, 6.0, 13.0],
        "close": [9.5, 11.0, 7.0, 14.0],
        "volume": [1, 1, 1, 1],
    }, index=idx)

    or_high, or_low = ind.opening_range(frame, n_bars=2)

    assert math.isnan(or_high.iloc[0])
    assert math.isnan(or_low.iloc[0])
    # Bar 2 completes the 2-bar OR: high=max(10,12)=12, low=min(9,10)=9
    assert or_high.iloc[1] == pytest.approx(12.0)
    assert or_low.iloc[1] == pytest.approx(9.0)
    # Bars after completion stay frozen at the 2-bar OR, even though bar 3's
    # high(8) and low(6) would have expanded a naive running cummax/cummin.
    assert or_high.iloc[2] == pytest.approx(12.0)
    assert or_low.iloc[2] == pytest.approx(9.0)
    assert or_high.iloc[3] == pytest.approx(12.0)
    assert or_low.iloc[3] == pytest.approx(9.0)


def test_prev_session_low_high_broadcasts_prior_session_no_lookahead():
    idx = (
        _et_index("2026-01-05", ["09:30:00", "09:35:00"])
        .append(_et_index("2026-01-06", ["09:30:00", "09:35:00"]))
    )
    frame = pd.DataFrame({
        "high": [10.0, 12.0, 20.0, 22.0],
        "low": [8.0, 9.0, 18.0, 17.0],
    }, index=idx)

    prev_low, prev_high = ind.prev_session_low_high(frame)

    assert math.isnan(prev_low.iloc[0]) and math.isnan(prev_high.iloc[0])
    assert math.isnan(prev_low.iloc[1]) and math.isnan(prev_high.iloc[1])
    # Session 2 sees session 1's full extremes: low=min(8,9)=8, high=max(10,12)=12
    assert prev_low.iloc[2] == pytest.approx(8.0)
    assert prev_high.iloc[2] == pytest.approx(12.0)
    assert prev_low.iloc[3] == pytest.approx(8.0)
    assert prev_high.iloc[3] == pytest.approx(12.0)


# ============================================================
# htf_ema_bias — only COMPLETED hourly bars, span=1 for trivial hand values
# ============================================================

def test_htf_ema_bias_only_sees_completed_hourly_bars():
    # Hourly bins (offset="30min"): [09:30,10:30) labelled 09:30, completes at 10:30.
    times = [f"09:{m:02d}:00" for m in range(30, 60, 5)] + [
        f"10:{m:02d}:00" for m in range(0, 30, 5)
    ]  # 09:30 .. 10:25 (12 bars), the whole first hourly bin
    times.append("10:30:00")  # first bar of the second hourly bin
    idx = _et_index("2026-01-05", times)
    closes = [100.0] * (len(times) - 1) + [110.0]
    frame = pd.DataFrame({"close": closes}, index=idx)

    bias = ind.htf_ema_bias(frame, span=1)  # span=1 -> ema == the raw hourly close

    # At 10:25 the 09:30 hourly bin hasn't completed yet (completes at 10:30) ->
    # no htf ema visible -> comparison against NaN is False.
    assert bool(bias.iloc[-2]) is False
    # At 10:30 the 09:30-10:30 bin just completed with hourly close 100.0;
    # this bar's own close (110.0) > 100.0 -> True.
    assert bool(bias.iloc[-1]) is True


# ============================================================
# bars_since / cross_up / cross_down
# ============================================================

def test_bars_since_sentinel_and_zero_on_true_bar():
    condition = pd.Series([False, True, False, False, True])
    result = ind.bars_since(condition)
    sentinel = len(condition) + 1  # 6
    assert result.tolist() == [sentinel, 0, 1, 2, 0]


def test_cross_up_and_cross_down_hand_computed():
    a = pd.Series([1.0, 3.0, 2.0])
    b = pd.Series([2.0, 2.0, 3.0])
    up = ind.cross_up(a, b)
    down = ind.cross_down(a, b)
    assert up.tolist() == [False, True, False]
    assert down.tolist() == [False, False, True]


# ============================================================
# weekly_regime / regime_for_day
# ============================================================

def test_weekly_regime_classifies_strong_uptrend_as_bull_and_downtrend_as_bear():
    dates_up = pd.date_range("2026-01-01", periods=140, freq="D")
    closes_up = pd.Series(
        [100.0 * (1.02 ** i) for i in range(len(dates_up))], index=dates_up
    )
    up_frame = pd.DataFrame({"close": closes_up})
    up_labels = ind.weekly_regime(up_frame)
    assert up_labels.iloc[-1] == "bull"

    dates_down = pd.date_range("2026-01-01", periods=140, freq="D")
    closes_down = pd.Series(
        [100.0 * (0.98 ** i) for i in range(len(dates_down))], index=dates_down
    )
    down_frame = pd.DataFrame({"close": closes_down})
    down_labels = ind.weekly_regime(down_frame)
    assert down_labels.iloc[-1] == "bear"


def test_regime_for_day_strictly_before_no_same_week_lookahead():
    labels = pd.Series(
        ["bull", "bear", "neutral"],
        index=pd.DatetimeIndex(["2026-01-02", "2026-01-09", "2026-01-16"]),
    )
    # A day inside the "2026-01-09" week must see the LAST label strictly
    # before it (the "2026-01-02" week's "bull"), never the same week's own row.
    assert ind.regime_for_day(labels, "2026-01-09") == "bull"
    assert ind.regime_for_day(labels, "2026-01-12") == "bear"
    assert ind.regime_for_day(labels, "2026-01-01") == "neutral"  # nothing prior


# ============================================================
# swing_high_2_2 — mirror of swing_low_2_2
# ============================================================

def test_swing_high_2_2_mirrors_swing_low_2_2():
    from bot.strategy.indicators import swing_low_2_2

    highs = [10.0, 12.0, 15.0, 11.0, 9.0, 13.0]
    expected = swing_low_2_2([-h for h in highs])
    expected = -expected if expected is not None else None
    assert ind.swing_high_2_2(highs) == expected
