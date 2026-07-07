#!/usr/bin/env python3
"""
backtester.feed — SimulatedBarFeed: yfinance/CSV-cache 5m bar replay for the backtester.

Historical-5m-bar source that replaces the live moomoo SDK push stream (BT-04). Downloads
5m bars via the reused bot.scanner.fetcher batch kernel (download_intraday_5m /
_download_batch), routes every frame through get_ticker_frame (Pitfall 3 — raw yf.download
frames are Title-Case; get_ticker_frame lowercases columns), caches responses to CSV under
cache_dir so the backtestable history grows past yfinance's rolling ~58-trading-day 5m
window over time, and replays bars as BarEvent-shaped dicts in chronological order with
session-running hod/lod/cum_volume computed point-in-time (mirrors BarAggregator's
per-session accumulation).

Imports ONLY bot.scanner.fetcher / bot.scanner.universe + pandas (T-06-04) — never the
broker gateway layer; this module is a pure data source, no broker/execution path.

Exports: SimulatedBarFeed, BacktestWindowError.
"""
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from bot.safety.et_helpers import ET
from bot.scanner.fetcher import download_intraday_5m, get_ticker_frame
from bot.scanner.universe import yfinance_to_moomoo

# ============================================================
# Module constants
# ============================================================

# yfinance's intraday 5m window is a ROLLING window measured from "now" (06-RESEARCH
# Pitfall 2), not an arbitrary historical range -- ~58 trading days is the practically
# available window. Converted to an approximate calendar-day cutoff (5 trading days per
# 7 calendar days) since a live fetch has no "trading day" concept until it succeeds.
INTRADAY_5M_WINDOW_TRADING_DAYS = 58
_WINDOW_CALENDAR_DAYS = round(INTRADAY_5M_WINDOW_TRADING_DAYS * 7 / 5)

CACHE_DIR = "backtester/cache"


class BacktestWindowError(Exception):
    """Raised when --start precedes the available yfinance 5m window with no CSV cache.

    Never silently return an empty bar list for an out-of-window request (06-RESEARCH
    Open-Q1) -- the operator must be told loudly that the requested range cannot be
    replayed from either a live fetch or the local cache.
    """


class SimulatedBarFeed:
    """Historical 5m bar replay engine: yfinance + CSV cache -> chronological BarEvent dicts."""

    def __init__(self, codes: List[str], start: str, end: str, cache_dir: str = CACHE_DIR):
        """codes: moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
        start/end: "YYYY-MM-DD" strings bounding the requested 5m range.
        cache_dir: directory for the CSV read-through cache (created if absent).
        """
        self.codes = list(codes)
        self.start = start
        self.end = end
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._bars_by_code: Dict[str, list] = {}
        self._load_5m()

    # --------------------------------------------------------
    # Ticker normalisation / cache paths
    # --------------------------------------------------------

    @staticmethod
    def _yf_symbol(code: str) -> str:
        """Moomoo code -> yfinance symbol (strip "US." prefix, matches scanner.py convention)."""
        return code.removeprefix("US.")

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self.cache_dir, f"{symbol}_{interval}_{self.start}_{self.end}.csv")

    # --------------------------------------------------------
    # 5m load: CSV cache read-through, window guard, hod/lod/cum_volume
    # --------------------------------------------------------

    def _load_5m(self) -> None:
        """Load 5m bars for every requested code: CSV cache hit, else network + write-through."""
        frames: Dict[str, pd.DataFrame] = {}
        misses = []
        for code in self.codes:
            sym = self._yf_symbol(code)
            path = self._cache_path(sym, "5m")
            if os.path.exists(path):
                frames[sym] = pd.read_csv(path, index_col=0, parse_dates=True)
            else:
                self._enforce_window()
                misses.append(sym)

        if misses:
            data, _failed = download_intraday_5m(misses)
            for sym in misses:
                frame = get_ticker_frame(data, sym)
                if frame is None or frame.empty:
                    continue
                frame.to_csv(self._cache_path(sym, "5m"))
                frames[sym] = frame

        for code in self.codes:
            sym = self._yf_symbol(code)
            frame = frames.get(sym)
            if frame is None or frame.empty:
                continue
            self._bars_by_code[yfinance_to_moomoo(sym)] = self._materialize_bars(sym, frame)

    def _enforce_window(self) -> None:
        """Raise BacktestWindowError if self.start precedes the available window (no cache hit)."""
        cutoff = datetime.now() - timedelta(days=_WINDOW_CALENDAR_DAYS)
        start_dt = datetime.strptime(self.start, "%Y-%m-%d")
        if start_dt < cutoff:
            raise BacktestWindowError(
                f"Requested start={self.start} precedes the available yfinance 5m window "
                f"(~{INTRADAY_5M_WINDOW_TRADING_DAYS} trading days back to "
                f"~{cutoff.date().isoformat()}) and no CSV cache file under {self.cache_dir!r} "
                "covers this range. Build a cache by running backtests/scans over time, or "
                "supply a flat-file export -- this never silently clips to an empty result."
            )

    def _materialize_bars(self, sym: str, frame: pd.DataFrame) -> list:
        """Build chronological BarEvent-shaped dicts with point-in-time hod/lod/cum_volume.

        Resets the running accumulators at each new session date (mirrors BarAggregator's
        per-session accumulation).
        """
        moomoo_code = yfinance_to_moomoo(sym)
        frame = frame.sort_index()
        bars = []
        running_hod = float("-inf")
        running_lod = float("inf")
        cum_volume = 0
        current_date = None
        for ts, row in frame.iterrows():
            ts_et = ts.tz_convert(ET) if ts.tzinfo is not None else ts.tz_localize("UTC").tz_convert(ET)
            session_date = ts_et.date()
            if session_date != current_date:
                current_date = session_date
                running_hod = float("-inf")
                running_lod = float("inf")
                cum_volume = 0
            running_hod = max(running_hod, float(row["high"]))
            running_lod = min(running_lod, float(row["low"]))
            cum_volume += int(row["volume"])
            bars.append({
                "code": moomoo_code,
                "time_key": ts_et.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "hod": running_hod,
                "lod": running_lod,
                "cum_volume": cum_volume,
            })
        return bars

    # --------------------------------------------------------
    # Replay
    # --------------------------------------------------------

    def replay(self, day):
        """Yield this session date's bars across all codes, sorted by (time_key, code)."""
        day_str = str(day)
        all_bars = [
            b for bars in self._bars_by_code.values() for b in bars
            if b["time_key"].startswith(day_str)
        ]
        all_bars.sort(key=lambda b: (b["time_key"], b["code"]))
        for b in all_bars:
            yield b

    def next_bar(self, code: str, after: str) -> Optional[dict]:
        """Return the strictly-next bar for code with time_key > after, else None."""
        for b in self._bars_by_code.get(code, []):
            if b["time_key"] > after:
                return b
        return None
