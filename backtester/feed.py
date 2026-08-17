#!/usr/bin/env python3
"""
backtester.feed — SimulatedBarFeed: yfinance/Massive/CSV-cache 5m bar replay for the backtester.

Supports two sources: yfinance (default, rolling ~60-calendar-day 5m window) and
the Massive API via backtester.massive (deep history — see _load_massive).

Historical-5m-bar source that replaces the live moomoo SDK push stream (BT-04). Downloads
5m bars via the reused bot.scanner.fetcher batch kernel (_download_batch directly, plus
download_intraday_5m/download_daily_bars for the TOD-baseline/daily-bar accessors), routes
every frame through get_ticker_frame (Pitfall 3 — raw yf.download frames are Title-Case;
get_ticker_frame lowercases columns), caches responses to CSV under cache_dir so the
backtestable history grows past yfinance's rolling ~60-calendar-day 5m window over time,
and replays bars as BarEvent-shaped dicts in chronological order with session-running
hod/lod/cum_volume computed point-in-time (mirrors BarAggregator's per-session
accumulation). A per-trading-day coverage guard (_enforce_coverage) raises loudly by name
if any requested day has zero replay bars (CR-03), and next_bar() never crosses a session
boundary (CR-04).

Also exposes the point-in-time setup-data accessors the harness (06-05) needs once per
historical session date: daily_bars() for _evaluate_symbol, synthetic_today_price() (a
TodayPrice mirroring resolve_today_price's PREMARKET branch — premarket-only, never the
RTH close/high, CR-07), intraday_5m_for_tod() for _compute_tod_baselines, and
premarket_highs() (CR-02).

PREMARKET-HIGH APPROXIMATION: _load_premarket() loads a SEPARATE prepost=True 5m frame ONCE
per feed construction (the RVOL-TOD path via download_intraday_5m/intraday_5m_for_tod stays
prepost=False, per its own docstring rationale). This is a same-methodology/different-source
APPROXIMATION of the live broker's `pre_high_price` snapshot field — yfinance's own
premarket 5m coverage may differ from what the broker's tape included — not a bit-for-bit
reproduction. See 06-RESEARCH Pitfall 5 / Assumption A3.

Imports ONLY bot.scanner.fetcher / bot.scanner.universe + pandas (T-06-04) — never the
broker gateway layer; this module is a pure data source, no broker/execution path.

Exports: SimulatedBarFeed, BacktestWindowError.
"""
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import pandas_market_calendars as mcal

from bot.safety.et_helpers import ET
from bot.scanner.fetcher import (
    _download_batch,
    download_daily_bars,
    download_intraday_5m,
    get_ticker_frame,
    TodayPrice,
)
from bot.scanner.universe import yfinance_to_moomoo

# ============================================================
# Module constants
# ============================================================

# yfinance's ACTUAL intraday 5m window is a ROLLING ~60-CALENDAR-day window measured
# from "now" (06-RESEARCH Pitfall 2, CR-03) -- the previous 58-trading-day / 7:5
# calendar-conversion pair was the mismatch: it claimed a wider window than the fetch
# below (period="60d") ever actually requested, so a replay could be told "in window"
# and still receive zero bars for its tail days. One constant, one true number.
INTRADAY_5M_WINDOW_CALENDAR_DAYS = 60

CACHE_DIR = "backtester/cache"

_NYSE = mcal.get_calendar("NYSE")

# T-06-03: cache filenames interpolate the (prefix-stripped) symbol -- restrict it to
# ticker-shaped characters so no path separator/traversal segment can reach os.path.join.
_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]+")

# Market constant -- regular-session open (ET). NOT read from rules.json (mirrors
# resolve_today_price's own documented rationale: a fixed market fact, not a strategy
# parameter that could ever vary by config).
_RTH_OPEN = datetime.strptime("09:30", "%H:%M").time()
_RTH_END = datetime.strptime("16:00", "%H:%M").time()

# Massive-source fetch padding: daily bars need >=200 prior sessions for SMA200
# (~290 calendar days) and the 5m fetch needs >=14 prior sessions for the
# RVOL-TOD baseline. Calendar-day pads with margin.
_MASSIVE_DAILY_PAD_DAYS = 400
_MASSIVE_TOD_PAD_DAYS = 30


class BacktestWindowError(Exception):
    """Raised when --start precedes the available yfinance 5m window with no CSV cache.

    Never silently return an empty bar list for an out-of-window request (06-RESEARCH
    Open-Q1) -- the operator must be told loudly that the requested range cannot be
    replayed from either a live fetch or the local cache.
    """


class SimulatedBarFeed:
    """Historical 5m bar replay engine: yfinance + CSV cache -> chronological BarEvent dicts."""

    def __init__(self, codes: List[str], start: str, end: str, cache_dir: str = CACHE_DIR,
                 source: str = "yfinance", massive=None):
        """codes: moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
        start/end: "YYYY-MM-DD" strings bounding the requested 5m range.
        cache_dir: directory for the yfinance CSV read-through cache.
        source: "yfinance" (default, rolling ~60-day 5m window) or "massive"
            (Massive API — deep history; run.py resolves MASSIVE_API_KEY).
        massive: MassiveDataSource instance, required when source == "massive"
            (injected so tests can pass a fake; it owns its own CSV cache).
        """
        self.codes = list(codes)
        for code in self.codes:
            if not _SYMBOL_RE.fullmatch(self._yf_symbol(code)):
                raise ValueError(
                    f"invalid symbol {code!r}: cache filenames accept only [A-Z0-9.-] (T-06-03)"
                )
        self.start = start
        self.end = end
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._bars_by_code: Dict[str, list] = {}
        self._premarket_bars_by_code: Dict[str, list] = {}
        self._source = source
        self._massive = massive
        self._massive_daily: Dict[str, pd.DataFrame] = {}
        self._massive_tod: Dict[str, pd.DataFrame] = {}
        if source == "massive":
            if massive is None:
                raise ValueError('source="massive" requires a MassiveDataSource instance')
            self._load_massive()
        else:
            self._load_5m()
            self._load_premarket()
        self._enforce_coverage()

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
            # Direct _download_batch call (not download_intraday_5m, which hardcodes
            # period="30d" -- ~21 trading days, the CR-03 root cause) so the replay
            # bars actually cover the advertised INTRADAY_5M_WINDOW_CALENDAR_DAYS window.
            data, _failed = _download_batch(
                misses,
                threads=5,
                degradation_threshold=0.10,
                download_kwargs={"period": "60d", "interval": "5m", "prepost": False},
                abort_event="backtest_5m_scan_aborted_data_degradation",
                partial_event="backtest_5m_partial_data",
                degradation_message="Backtest 5m data degradation",
            )
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
        cutoff = datetime.now() - timedelta(days=INTRADAY_5M_WINDOW_CALENDAR_DAYS)
        start_dt = datetime.strptime(self.start, "%Y-%m-%d")
        if start_dt < cutoff:
            raise BacktestWindowError(
                f"Requested start={self.start} precedes the available yfinance 5m window "
                f"(~{INTRADAY_5M_WINDOW_CALENDAR_DAYS} calendar days back to "
                f"~{cutoff.date().isoformat()}) and no CSV cache file under {self.cache_dir!r} "
                "covers this range. Build a cache by running backtests/scans over time, or "
                "supply a flat-file export -- this never silently clips to an empty result."
            )

    def _enforce_coverage(self) -> None:
        """Raise BacktestWindowError naming any NYSE trading day in [start, end] with ZERO
        replay bars across every requested code (CR-03).

        Converts a silent zero-bar replay day (the loop would simply produce no signals,
        no fills, no error -- indistinguishable from a legitimately quiet day) into a loud,
        named failure. Runs after _load_5m() so it sees whatever bars actually loaded,
        whether from the CSV cache or a fresh network fetch.
        """
        trading_days = [
            d.strftime("%Y-%m-%d")
            for d in _NYSE.valid_days(start_date=self.start, end_date=self.end)
        ]
        covered = {
            b["time_key"][:10] for bars in self._bars_by_code.values() for b in bars
        }
        missing = [d for d in trading_days if d not in covered]
        if missing:
            raise BacktestWindowError(
                f"No replay bars loaded for NYSE trading day(s) {', '.join(missing)} in "
                f"[{self.start}, {self.end}] across any requested code -- refusing to "
                "silently replay an empty day."
            )

    def _materialize_bars(self, sym: str, frame: pd.DataFrame) -> list:
        """Build chronological BarEvent-shaped dicts with point-in-time hod/lod/cum_volume.

        Resets the running accumulators at each new session date (mirrors BarAggregator's
        per-session accumulation).
        """
        moomoo_code = yfinance_to_moomoo(sym)
        frame = frame.sort_index()
        # A realistic multi-ticker yf.download(group_by="ticker") frame is built on the
        # UNION of every requested symbol's timestamps -- a symbol lacking a bar at a peer's
        # timestamp (halts, illiquid names, staggered premarket coverage) gets an all-NaN
        # padding row rather than being omitted. Drop those before the per-row int()/float()
        # loop below so a NaN never reaches `int(row["volume"])` (T-06-10-01). Sits after
        # sort_index() and before the loop so it applies identically to the CSV-cache-hit
        # path (T-06-10-03), since a cache file written by an earlier unguarded fetch
        # preserves NaN rows verbatim.
        frame = frame.dropna(subset=["open", "high", "low", "close", "volume"])
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

    def _load_premarket(self) -> None:
        """One-time prepost=True premarket load: pre-09:30 ET 5m bars per code, keyed into
        self._premarket_bars_by_code. Replaces the old per-call period="5d"-from-now fetch
        inside premarket_highs -- that fetch was relative to "now" at CALL time, so a
        replay day more than ~5 days in the past silently returned an empty premarket
        frame regardless of how the feed's own [start, end] window was constructed (CR-02).

        Loaded and cached exactly like _load_5m (CSV read-through under a distinct
        interval tag "5m_pre" so premarket history also grows past the 60-day window over
        time), but degradation_threshold=1.0: premarket data is best-effort and must never
        abort the whole backtest (mirrors the prior premarket_highs rationale).
        """
        frames: Dict[str, pd.DataFrame] = {}
        misses = []
        for code in self.codes:
            sym = self._yf_symbol(code)
            path = self._cache_path(sym, "5m_pre")
            if os.path.exists(path):
                frames[sym] = pd.read_csv(path, index_col=0, parse_dates=True)
            else:
                misses.append(sym)

        if misses:
            data, _failed = _download_batch(
                misses,
                threads=5,
                degradation_threshold=1.0,  # best-effort: never abort the backtest on premarket degradation
                download_kwargs={"period": "60d", "interval": "5m", "prepost": True},
                abort_event="backtest_premarket_scan_aborted_data_degradation",
                partial_event="backtest_premarket_partial_data",
                degradation_message="Backtest premarket 5m data degradation",
            )
            for sym in misses:
                frame = get_ticker_frame(data, sym)
                if frame is None or frame.empty:
                    continue
                frame.to_csv(self._cache_path(sym, "5m_pre"))
                frames[sym] = frame

        for code in self.codes:
            sym = self._yf_symbol(code)
            frame = frames.get(sym)
            if frame is None or frame.empty:
                continue
            moomoo_code = yfinance_to_moomoo(sym)
            bars = []
            # Same union-index NaN-padding guard as _materialize_bars (T-06-10-02): a
            # dropped-NaN row here can never become a NaN high stored in
            # self._premarket_bars_by_code, so premarket_highs()'s max(...) can never
            # silently poison Gate 1's close > premarket_high check with a NaN comparison.
            pre = frame.sort_index().dropna(subset=["open", "high", "low", "close", "volume"])
            for ts, row in pre.iterrows():
                ts_et = (
                    ts.tz_convert(ET) if ts.tzinfo is not None
                    else ts.tz_localize("UTC").tz_convert(ET)
                )
                if ts_et.time() >= _RTH_OPEN:
                    continue
                bars.append({
                    "time_key": ts_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                })
            self._premarket_bars_by_code[moomoo_code] = bars

    # --------------------------------------------------------
    # Massive-source load (backtester.massive — deep history past the yfinance window)
    # --------------------------------------------------------

    def _load_massive(self) -> None:
        """Massive-source load: ONE 5m fetch + ONE daily fetch per symbol (CSV-cached).

        The single extended-hours 5m frame per symbol is sliced three ways:
          - RTH bars within [start, end]        -> replay bars (_materialize_bars)
          - pre-09:30 bars within [start, end]  -> premarket bars (Gate 1 / TodayPrice)
          - RTH bars over the padded range      -> RVOL-TOD baseline frames
        The daily fetch covers [start - _MASSIVE_DAILY_PAD_DAYS, end] so
        _evaluate_symbol's SMA200 / date < scan_date cutoff has real history for
        every replay day (a from-now yfinance period="1y" fetch would hold zero
        history for a deep-past window).

        No _enforce_window() here — that guard models yfinance's rolling window;
        Massive history is bounded by the account's subscription, and
        _enforce_coverage() still fails loudly per missing trading day.
        """
        start_dt = datetime.strptime(self.start, "%Y-%m-%d")
        start_d = start_dt.date()
        end_d = datetime.strptime(self.end, "%Y-%m-%d").date()
        tod_start = (start_dt - timedelta(days=_MASSIVE_TOD_PAD_DAYS)).date().isoformat()
        daily_start = (start_dt - timedelta(days=_MASSIVE_DAILY_PAD_DAYS)).date().isoformat()

        for code in self.codes:
            sym = self._yf_symbol(code)
            moomoo_code = yfinance_to_moomoo(sym)

            daily = self._massive.cached_bars(sym, "1d", 1, "day", daily_start, self.end)
            if not daily.empty:
                # yfinance daily frames are tz-NAIVE dates, and _evaluate_symbol
                # compares the index against a naive pd.Timestamp(scan_date)
                # (bot/scanner/scanner.py) — a tz-aware index there raises
                # "Cannot compare tz-naive and tz-aware". Strip the ET tz
                # (keeping ET wall dates) so Massive daily frames match the
                # consumer's expected shape exactly.
                daily.index = daily.index.tz_localize(None).normalize()
                self._massive_daily[sym] = daily

            full_5m = self._massive.cached_bars(sym, "5m", 5, "minute", tod_start, self.end)
            if full_5m.empty:
                continue

            times = full_5m.index.time
            dates = full_5m.index.date
            rth_mask = (times >= _RTH_OPEN) & (times < _RTH_END)
            pre_mask = times < _RTH_OPEN
            in_range = (dates >= start_d) & (dates <= end_d)

            rth_padded = full_5m[rth_mask]
            if not rth_padded.empty:
                self._massive_tod[sym] = rth_padded  # prior sessions feed TOD baselines

            replay = get_ticker_frame({sym: full_5m[rth_mask & in_range]}, sym)
            if replay is not None and not replay.empty:
                self._bars_by_code[moomoo_code] = self._materialize_bars(sym, replay)

            pre = get_ticker_frame({sym: full_5m[pre_mask & in_range]}, sym)
            if pre is not None and not pre.empty:
                pre = pre.sort_index().dropna(subset=["open", "high", "low", "close", "volume"])
                self._premarket_bars_by_code[moomoo_code] = [
                    {
                        "time_key": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                    for ts, row in pre.iterrows()
                ]

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
        """Return the strictly-next SAME-SESSION bar for code with time_key > after, else None.

        Bars are already chronological (_materialize_bars sorts by index), so the first
        bar with time_key > after is the candidate next bar. Never crosses a session
        boundary (CR-04): if that candidate falls on a later calendar day than `after`,
        there is no same-session next bar -- a signal or stop on day D's last bar must not
        fill on day D+1's open.
        """
        after_date = after[:10]
        for b in self._bars_by_code.get(code, []):
            if b["time_key"] > after:
                return b if b["time_key"][:10] == after_date else None
        return None

    # --------------------------------------------------------
    # Point-in-time setup-data accessors (06-05 harness, per historical session date)
    # --------------------------------------------------------

    def daily_bars(self, codes: Optional[List[str]] = None):
        """Raw daily-bar mapping for _evaluate_symbol (get_ticker_frame handles dicts).

        massive source: the preloaded {yf_symbol: frame} dict — NO network per
        call (the harness calls this once per replay day; refetching would burn
        the API rate limit day after day for identical data).
        yfinance source: download_daily_bars result (raw, group_by="ticker").
        """
        if self._source == "massive":
            return dict(self._massive_daily)
        codes = codes or self.codes
        yf_symbols = [self._yf_symbol(c) for c in codes]
        data, _failed = download_daily_bars(yf_symbols)
        return data

    def intraday_5m_for_tod(self, codes: Optional[List[str]] = None):
        """Raw prepost-free 5m mapping for _compute_tod_baselines (capital "Volume").

        massive source: preloaded RTH-only frames over the padded range (no network);
        yfinance source: download_intraday_5m result (raw, prepost=False).
        """
        if self._source == "massive":
            return dict(self._massive_tod)
        codes = codes or self.codes
        yf_symbols = [self._yf_symbol(c) for c in codes]
        data, _failed = download_intraday_5m(yf_symbols)
        return data

    def synthetic_today_price(self, code: str, day) -> Optional[TodayPrice]:
        """TodayPrice built from `day`'s premarket-only 5m bars (self._premarket_bars_by_code).

        Mirrors resolve_today_price's PREMARKET branch (CR-07), NOT the RTH branch:
        today_open == today_price == the LATEST premarket bar's close, today_high == the
        MAX premarket high. NEVER uses self._bars_by_code (RTH bars) or a full-day max
        high -- the prior RTH-branch implementation leaked the day's close/max-high (data
        that would not exist yet at premarket-scan time) into the daily-scan TodayPrice.
        Returns None when `code` has no premarket bars for `day`.
        """
        day_str = str(day)
        day_bars = sorted(
            (b for b in self._premarket_bars_by_code.get(code, []) if b["time_key"].startswith(day_str)),
            key=lambda b: b["time_key"],
        )
        if not day_bars:
            return None
        latest_close = day_bars[-1]["close"]
        return TodayPrice(
            today_open=latest_close,
            today_price=latest_close,
            today_high=max(b["high"] for b in day_bars),
        )

    def premarket_highs(self, day) -> Dict[str, float]:
        """{moomoo_code: max(high)} of `day`'s premarket (pre-09:30 ET) 5m bars, read from
        the one-time _load_premarket() load -- NO network call per invocation.

        Point-in-time for the requested replay day regardless of how long ago it was
        (CR-02): the prior implementation fetched a period="5d"-from-now frame on every
        call, so a replay day older than ~5 days silently returned an empty dict here.
        See module docstring: same-methodology/different-source APPROXIMATION of the live
        broker's pre_high_price field, not a bit-for-bit reproduction.
        """
        day_str = str(day)
        highs: Dict[str, float] = {}
        for code, bars in self._premarket_bars_by_code.items():
            day_bars = [b for b in bars if b["time_key"].startswith(day_str)]
            if day_bars:
                highs[code] = max(b["high"] for b in day_bars)
        return highs
