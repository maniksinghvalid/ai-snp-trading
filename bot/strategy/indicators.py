#!/usr/bin/env python3
"""
bot.strategy.indicators — Pure indicator functions for the Trend Join Long strategy.

All functions are pure (no I/O, no network, no broker calls) and operate on
pandas Series/DataFrames or plain Python lists. They are mechanical math
primitives; the strategy class (TrendJoinLong) supplies config-driven parameters
(period=200, lookback=cfg.rvol_lookback_days).

Exports: sma, rvol, swing_low_2_2
"""
import math
from typing import List, Optional, Union

import pandas as pd

from bot._utils import safe_float


# ============================================================
# SMA — Simple Moving Average
# ============================================================

def sma(series: pd.Series, period: int) -> Optional[float]:
    """
    Compute the trailing simple moving average of the last `period` values.

    Returns the mean of the last `period` elements of `series`.
    Returns float NaN when the series has fewer than `period` values
    (caller should treat NaN/None as "insufficient data").

    Parameters:
        series: pandas Series of numeric values (e.g. daily closes).
        period: lookback window size (e.g. 200 for SMA200).

    Returns:
        float — trailing mean of last `period` values, or float('nan') if
        len(series) < period.
    """
    if series is None or len(series) < period:
        return float("nan")
    window = series.iloc[-period:]
    return float(window.mean())


# ============================================================
# RVOL — Relative Volume (no look-ahead)
# ============================================================

def rvol(
    volume_frame: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback_days: int,
    today_volume: float,
) -> Optional[float]:
    """
    Compute Relative Volume (RVOL) with strict no-look-ahead enforcement.

    RVOL = today_volume / mean(prior `lookback_days` completed sessions volume)

    The denominator is computed ONLY from rows where date < signal_date
    (strictly prior completed trading days — current session is NEVER included).
    This guards against Pitfall #4 (RVOL Look-Ahead Bias).

    Parameters:
        volume_frame: DataFrame with columns 'date' (pd.Timestamp) and 'volume'
                      (float or int). May include the current day's row; it is
                      explicitly excluded from the denominator.
        signal_date:  The date being evaluated (signal bar date). Rows with
                      date >= signal_date are excluded from the denominator.
        lookback_days: Number of prior completed trading sessions to use as the
                       denominator (e.g. 14).
        today_volume:  The current session's cumulative volume to use as the
                       numerator.

    Returns:
        float — RVOL ratio, or None if there are fewer than `lookback_days`
        strictly-prior completed sessions available.
    """
    # ---- Build denominator: strictly prior completed sessions ----
    prior = volume_frame[volume_frame["date"] < signal_date].copy()

    # Sort descending by date, take last `lookback_days` rows
    prior = prior.sort_values("date", ascending=False).head(lookback_days)

    if len(prior) < lookback_days:
        return None  # insufficient prior data

    mean_prior_volume = safe_float(prior["volume"].mean(), default=0.0)
    if mean_prior_volume == 0.0:
        return None  # guard against division by zero

    return safe_float(today_volume) / mean_prior_volume


# ============================================================
# swing_low_2_2 — 5-minute Swing Low (2-bar-left / 2-bar-right)
# ============================================================

def swing_low_2_2(lows: Union[List[float], "pd.Series"]) -> Optional[float]:
    """
    Identify the most recent 2-bar-left / 2-bar-right confirmed pivot low.

    A pivot low at index i requires:
        lows[i] < lows[i-1]  AND  lows[i] < lows[i-2]  (strictly lower than 2 left bars)
        lows[i] < lows[i+1]  AND  lows[i] < lows[i+2]  (strictly lower than 2 right bars)

    Searches from the second-to-last candidate working backwards (so the most
    recent confirmed pivot is returned when multiple exist).

    Per ARCHITECTURE.md §Pattern 3: with the latest closed bar at position -1,
    the innermost candidate pivot is at index len-3 (requires bars[-5] through [-1]).

    Parameters:
        lows: Sequence of low prices from closed 5m bars (list or pd.Series).
              Must have at least 5 elements to contain any candidate pivot.

    Returns:
        float — the pivot low price, or None if no confirmed pivot exists or
        fewer than 5 bars are available.
    """
    if lows is None:
        return None

    # Convert to list for consistent indexing
    if hasattr(lows, "tolist"):
        lows = lows.tolist()
    else:
        lows = list(lows)

    n = len(lows)
    if n < 5:
        return None

    # Search from second-to-last candidate inward (index n-3) back to index 2
    # so that the most recent confirmed pivot is returned first.
    for i in range(n - 3, 1, -1):
        pivot = lows[i]
        if (
            pivot < lows[i - 1]
            and pivot < lows[i - 2]
            and pivot < lows[i + 1]
            and pivot < lows[i + 2]
        ):
            return float(pivot)

    return None
