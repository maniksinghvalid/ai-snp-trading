#!/usr/bin/env python3
"""
backtester.experimental.strategies — vectorized ext2/orb/vwap_pb signal generators.

Pure functions: no I/O, no network, no SimulatedBarFeed/bot/ writes. Each
*_signals(frame, p) function computes its own indicators (via
backtester.experimental.indicators) from a raw bar frame — never a negative
(forward-looking) shift, never `.iloc[t+1:]`, never a centred rolling window —
so every output column is
prefix-invariant: fn(frame.iloc[:k], p).iloc[k-1] == fn(frame, p).iloc[k-1] for
every k. `frame` is the shape backtester.experimental.engine.build_frame
produces: a tz-aware ET DatetimeIndex with columns open/high/low/close/volume,
a `time_key` string column ("%Y-%m-%d %H:%M:%S"), and per-session running
`lod`/`hod` columns (the session running low/high INCLUDING the current bar).

DEFAULTS mirrors backtester/experimental/arms.json's `defaults` block exactly
(arms.json is the runtime single source of truth; DEFAULTS is the in-code
fallback so these functions are unit-testable without the file).

Exports: DEFAULTS, ext2_signals, orb_signals, vwap_pb_signals
"""
import pandas as pd

from backtester.experimental import indicators as ind

DEFAULTS = {
    "ext2": {
        "sma_period": 10,
        "macd": [12, 26, 9],
        "confirm_bars": 6,
        "entry_start": "09:35",
        "entry_end": "15:30",
        "stop_basis": "session",
        "atr_period": 14,
        "exit_model": "pct_ladder",
        "sizing": "risk1pct",
        "regime_gate": "none",
        "max_entries_per_symbol_per_day": None,
        "trend_lock": False,
        "max_concurrent": 5,
        "max_entries_per_day": 5,
        "daily_breaker_usd": -2000,
    },
    "orb": {
        "or_bars": 6,
        "entry_start": "10:00",
        "entry_end": "12:00",
        "htf_ema_span": 100,
        "max_or_height_pct": 3.0,
        "stop_basis": "or",
        "exit_model": "partial_be_trail",
        "sizing": "risk1pct",
        "regime_gate": "none",
        "max_entries_per_symbol_per_day": 1,
        "trend_lock": True,
        "max_concurrent": 5,
        "max_entries_per_day": 5,
        "daily_breaker_usd": -2000,
    },
    "vwap_pb": {
        "entry_start": "10:00",
        "entry_end": "15:00",
        "vol_mult": 1.2,
        "vol_lookback": 20,
        "stop_pad_pct": 0.1,
        "htf_ema_span": 100,
        "exit_model": "partial_be_trail",
        "sizing": "risk1pct",
        "regime_gate": "none",
        "max_entries_per_symbol_per_day": 2,
        "trend_lock": False,
        "max_concurrent": 5,
        "max_entries_per_day": 5,
        "daily_breaker_usd": -2000,
    },
}

def _merged(strategy: str, p: dict) -> dict:
    return {**DEFAULTS[strategy], **(p or {})}


def _time_window_mask(frame: pd.DataFrame, entry_start: str, entry_end: str) -> pd.Series:
    """time_key[11:16] string comparison against HH:MM bounds — never a tz-aware
    parse (10-RESEARCH Pitfall 5)."""
    tod = frame["time_key"].str.slice(11, 16)
    return (tod >= entry_start) & (tod < entry_end)


def _compute_stop_by_basis(frame: pd.DataFrame, p: dict):
    """(stop_long, stop_short) for the "session"/"prev_day"/"atr2"/"or" bases
    shared by ext2 and orb."""
    basis = p.get("stop_basis", "session")
    if basis == "session":
        return frame["lod"], frame["hod"]
    if basis == "prev_day":
        return ind.prev_session_low_high(frame)
    if basis == "atr2":
        atr_series = ind.atr(frame["high"], frame["low"], frame["close"], p.get("atr_period", 14))
        return frame["close"] - 2 * atr_series, frame["close"] + 2 * atr_series
    if basis == "or":
        or_high, or_low = ind.opening_range(frame, p.get("or_bars", 6))
        return or_low, or_high
    raise ValueError(f"unknown stop_basis {basis!r}")


def _htf_ema_value(frame: pd.DataFrame, span: int) -> pd.Series:
    """Same completed-hourly-bar EMA construction as indicators.htf_ema_bias, but
    returns the raw mapped EMA series (NaN before the first completed hourly
    bar) instead of a boolean. orb/vwap_pb need BOTH bull (close > htf_ema) and
    bear (close < htf_ema) sides; negating indicators.htf_ema_bias's boolean
    would turn "unknown" (NaN) into a false "bear" (~False == True) — this
    duplicate keeps "unknown" false on both sides."""
    hourly_close = frame["close"].resample("60min", offset="30min").last().dropna()
    hourly_ema = ind.ema(hourly_close, span)
    completed_at = hourly_ema.index + pd.Timedelta(minutes=60)
    completed = pd.DataFrame({"htf_ema": hourly_ema.values}, index=completed_at).sort_index()
    left = pd.DataFrame({"ts": frame.index, "close": frame["close"].values})
    right = completed.reset_index().rename(columns={"index": "ts"})
    merged = pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"),
                           on="ts", direction="backward")
    merged.index = frame.index
    return merged["htf_ema"]


def ext2_signals(frame: pd.DataFrame, p: dict) -> pd.DataFrame:
    """SMA(10)+MACD(12,26,9) cross-confirmation signals (Ext#2)."""
    p = _merged("ext2", p)
    close = frame["close"]
    sma_series = ind.sma(close, p["sma_period"])
    fast, slow, signal = p["macd"]
    macd_line, signal_line = ind.macd(close, fast, slow, signal)

    sma_up = ind.cross_up(close, sma_series)
    sma_down = ind.cross_down(close, sma_series)
    macd_up = ind.cross_up(macd_line, signal_line)
    macd_down = ind.cross_down(macd_line, signal_line)

    bars_since_macd_up = ind.bars_since(macd_up)
    bars_since_macd_down = ind.bars_since(macd_down)
    bars_since_sma_up = ind.bars_since(sma_up)
    bars_since_sma_down = ind.bars_since(sma_down)

    n = p["confirm_bars"]
    long_a = sma_up & (macd_line > signal_line) & (bars_since_macd_up <= n)
    long_b = macd_up & (bars_since_sma_up <= n)
    long = long_a | long_b

    short_a = sma_down & (macd_line < signal_line) & (bars_since_macd_down <= n)
    short_b = macd_down & (bars_since_sma_down <= n)
    short = short_a | short_b

    time_ok = _time_window_mask(frame, p["entry_start"], p["entry_end"])
    long = long & time_ok
    short = short & time_ok

    stop_long, stop_short = _compute_stop_by_basis(frame, p)

    return pd.DataFrame(
        {"long": long, "short": short, "stop_long": stop_long, "stop_short": stop_short},
        index=frame.index,
    )


def orb_signals(frame: pd.DataFrame, p: dict) -> pd.DataFrame:
    """ORB-30 + session VWAP + 1H-EMA100 HTF-bias trend core."""
    p = _merged("orb", p)
    or_high, or_low = ind.opening_range(frame, p["or_bars"])
    vwap = ind.session_vwap(frame)
    htf_ema = _htf_ema_value(frame, p["htf_ema_span"])
    htf_bull = frame["close"] > htf_ema
    htf_bear = frame["close"] < htf_ema

    close = frame["close"]
    or_height_pct = (or_high - or_low) / close * 100.0
    or_ok = or_height_pct <= p["max_or_height_pct"]

    time_ok = _time_window_mask(frame, p["entry_start"], p["entry_end"])

    long = (close > or_high) & (close > vwap) & htf_bull & or_ok & time_ok
    short = (close < or_low) & (close < vwap) & htf_bear & or_ok & time_ok

    stop_long, stop_short = _compute_stop_by_basis(frame, p)

    return pd.DataFrame(
        {"long": long, "short": short, "stop_long": stop_long, "stop_short": stop_short},
        index=frame.index,
    )


def vwap_pb_signals(frame: pd.DataFrame, p: dict) -> pd.DataFrame:
    """VWAP pullback-bounce entry: HTF bull/bear + confirmed first-hour trend +
    a VWAP-touching reclaim/rejection bar with above-average volume."""
    p = _merged("vwap_pb", p)
    close, high, low, open_ = frame["close"], frame["high"], frame["low"], frame["open"]
    vwap = ind.session_vwap(frame)
    htf_ema = _htf_ema_value(frame, p["htf_ema_span"])
    htf_bull = close > htf_ema
    htf_bear = close < htf_ema

    # vwap_pb has no or_bars param of its own in DEFAULTS -- falls back to the
    # same 30-minute (6-bar) window orb's own base default uses; overridable
    # per-call via p["or_bars"] for testability.
    or_high, or_low = ind.opening_range(frame, p.get("or_bars", 6))

    session = pd.Series(frame.index.date, index=frame.index)
    tod = frame["time_key"].str.slice(11, 16)
    since_10am = tod >= "10:00"

    high_since_10 = frame["high"].where(since_10am, other=float("-inf")).groupby(session).cummax()
    low_since_10 = frame["low"].where(since_10am, other=float("inf")).groupby(session).cummin()
    confirmed_up = high_since_10.shift(1) > or_high
    confirmed_down = low_since_10.shift(1) < or_low

    vol_mean_prior = (
        frame["volume"].rolling(p["vol_lookback"], min_periods=p["vol_lookback"]).mean().shift(1)
    )
    vol_ok = frame["volume"] >= p["vol_mult"] * vol_mean_prior

    time_ok = _time_window_mask(frame, p["entry_start"], p["entry_end"])

    long = (
        htf_bull & confirmed_up & (low <= vwap) & (vwap <= close) & (close > open_)
        & vol_ok & time_ok
    )
    short = (
        htf_bear & confirmed_down & (high >= vwap) & (vwap >= close) & (close < open_)
        & vol_ok & time_ok
    )

    pad = p["stop_pad_pct"] / 100.0
    stop_long = pd.concat([low, vwap], axis=1).min(axis=1) * (1 - pad)
    stop_short = pd.concat([high, vwap], axis=1).max(axis=1) * (1 + pad)

    return pd.DataFrame(
        {"long": long, "short": short, "stop_long": stop_long, "stop_short": stop_short},
        index=frame.index,
    )
