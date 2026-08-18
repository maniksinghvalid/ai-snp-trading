#!/usr/bin/env python3
"""
backtester.experimental.indicators — pure pandas/numpy indicator functions for the
external-strategy research package (ext2/orb/vwap_pb signal inputs + SPY weekly regime).

No I/O, no network, no bot/ imports except bot.strategy.indicators.swing_low_2_2
(reused, not reimplemented, per 10-CONTEXT.md). Never look-ahead: every function
operates on rows <= t only — no shift(-n), no .iloc[t+1:], no centred rolling
windows anywhere in this file. Callers pass a frame with a tz-aware ET
DatetimeIndex (the shape backtester.experimental.engine.build_frame produces).

Exports: sma, ema, macd, atr, session_vwap, opening_range, prev_session_low_high,
    htf_ema_bias, bars_since, cross_up, cross_down, weekly_regime, regime_for_day,
    swing_high_2_2
"""
import pandas as pd

from bot.strategy.indicators import swing_low_2_2


# ============================================================
# Moving averages / MACD / ATR
# ============================================================

def sma(series: pd.Series, period: int) -> pd.Series:
    """Rolling simple moving average; NaN until `period` values are available."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average, `ewm(span=..., adjust=False)` (standard MACD/EMA
    semantics — matches the design doc's stated convention)."""
    return series.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line) — MACD(fast, slow) and its EMA(signal)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder true-range ATR: seed = simple mean of the first `period` true ranges,
    then Wilder smoothing atr[i] = (atr[i-1]*(period-1) + tr[i]) / period. NaN
    before the seed bar."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    if len(tr) > 0:
        tr.iloc[0] = high.iloc[0] - low.iloc[0]  # bar 0 has no prior close

    result = pd.Series(float("nan"), index=tr.index)
    if len(tr) < period:
        return result
    result.iloc[period - 1] = tr.iloc[:period].mean()
    for i in range(period, len(tr)):
        result.iloc[i] = (result.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period
    return result


# ============================================================
# Session-relative price levels
# ============================================================

def _session_key(frame: pd.DataFrame) -> pd.Series:
    """Per-row session calendar date, derived from the tz-aware index (never a
    time_key string parse — the index is already the authoritative ET timestamp)."""
    return pd.Series(frame.index.date, index=frame.index)


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    """Typical price (H+L+C)/3, per-session cumulative sum(tp*volume)/sum(volume);
    resets at each new session date."""
    session = _session_key(frame)
    tp = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    tpv = tp * frame["volume"]
    cum_tpv = tpv.groupby(session).cumsum()
    cum_vol = frame["volume"].groupby(session).cumsum()
    return cum_tpv / cum_vol


def opening_range(frame: pd.DataFrame, n_bars: int):
    """(or_high, or_low): NaN until the session's n-th bar completes, then frozen
    (constant) for the rest of that session; resets per session."""
    session = _session_key(frame)
    bar_num = session.groupby(session).cumcount() + 1
    cum_high = frame["high"].groupby(session).cummax()
    cum_low = frame["low"].groupby(session).cummin()
    frozen_high = cum_high.where(bar_num == n_bars).groupby(session).ffill()
    frozen_low = cum_low.where(bar_num == n_bars).groupby(session).ffill()
    or_high = frozen_high.where(bar_num >= n_bars)
    or_low = frozen_low.where(bar_num >= n_bars)
    return or_high, or_low


def prev_session_low_high(frame: pd.DataFrame):
    """(prev_low, prev_high): the PRIOR session's full-session low/high broadcast
    onto every bar of the CURRENT session (no look-ahead — the prior session is
    already complete before the current session starts)."""
    session = _session_key(frame)
    daily = frame.groupby(session).agg(low=("low", "min"), high=("high", "max"))
    daily_shifted = daily.shift(1)
    prev_low = session.map(daily_shifted["low"])
    prev_high = session.map(daily_shifted["high"])
    return prev_low, prev_high


def htf_ema_bias(frame: pd.DataFrame, span: int = 100) -> pd.Series:
    """True when the 5m close is above the EMA(span) of COMPLETED 60-minute bars
    (resampled with offset="30min" so hourly bars align to 09:30/10:30/...). A 5m
    bar may never see the hourly bar it is still inside — merge_asof(direction=
    "backward") is matched against each hourly bar's COMPLETION timestamp
    (bin start + 60min), never its label/start."""
    hourly_close = frame["close"].resample("60min", offset="30min").last().dropna()
    hourly_ema = ema(hourly_close, span)
    completed_at = hourly_ema.index + pd.Timedelta(minutes=60)
    completed = pd.DataFrame({"htf_ema": hourly_ema.values}, index=completed_at).sort_index()

    left = pd.DataFrame({"ts": frame.index, "close": frame["close"].values})
    right = completed.reset_index().rename(columns={"index": "ts"})
    merged = pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"),
                           on="ts", direction="backward")
    merged.index = frame.index
    return merged["close"] > merged["htf_ema"]


# ============================================================
# Crossovers / bars-since
# ============================================================

def bars_since(condition: pd.Series) -> pd.Series:
    """Bars elapsed since the last True (0 on the True bar itself); a large
    sentinel (len(condition) + 1) before the first True."""
    positions = pd.Series(range(len(condition)), index=condition.index)
    last_true_position = positions.where(condition.astype(bool)).ffill()
    elapsed = positions - last_true_position
    sentinel = len(condition) + 1
    return elapsed.fillna(sentinel).astype(int)


def cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    """True at t when a crosses above b: a[t] > b[t] and a[t-1] <= b[t-1]."""
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    """True at t when a crosses below b: a[t] < b[t] and a[t-1] >= b[t-1]."""
    return (a < b) & (a.shift(1) >= b.shift(1))


# ============================================================
# Weekly SPY regime
# ============================================================

def weekly_regime(spy_daily: pd.DataFrame) -> pd.Series:
    """Weekly (W-FRI) regime labels: 'bull' when weekly close > weekly SMA10 AND
    weekly MACD line > signal line; 'bear' when both comparisons are opposite;
    else 'neutral'."""
    weekly_close = spy_daily["close"].resample("W-FRI").last().dropna()
    weekly_sma10 = sma(weekly_close, 10)
    macd_line, signal_line = macd(weekly_close)

    bull = (weekly_close > weekly_sma10) & (macd_line > signal_line)
    bear = (weekly_close < weekly_sma10) & (macd_line < signal_line)

    labels = pd.Series("neutral", index=weekly_close.index)
    labels[bull] = "bull"
    labels[bear] = "bear"
    return labels


def regime_for_day(labels: pd.Series, day) -> str:
    """The last weekly regime label STRICTLY BEFORE `day` (no same-week look-ahead);
    'neutral' when no prior label exists."""
    day_ts = pd.Timestamp(day)
    if labels.index.tz is not None and day_ts.tzinfo is None:
        day_ts = day_ts.tz_localize(labels.index.tz)
    elif labels.index.tz is None and day_ts.tzinfo is not None:
        day_ts = day_ts.tz_localize(None)
    prior = labels[labels.index < day_ts]
    if prior.empty:
        return "neutral"
    return str(prior.iloc[-1])


# ============================================================
# Swing pivots
# ============================================================

def swing_high_2_2(highs):
    """2-bar-left/2-bar-right confirmed pivot high — the exact mirror of the
    imported swing_low_2_2 on negated highs."""
    if highs is None:
        return None
    values = highs.tolist() if hasattr(highs, "tolist") else list(highs)
    pivot = swing_low_2_2([-h for h in values])
    return -pivot if pivot is not None else None
