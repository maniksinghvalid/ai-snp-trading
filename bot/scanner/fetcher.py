#!/usr/bin/env python3
"""
bot.scanner.fetcher — yfinance batch downloader and intraday price resolver for the premarket scanner.

Downloads 1-year daily OHLCV bars and live 1m intraday bars for ~500 S&P 500
symbols in bounded-concurrency batch calls. Pure data-fetch layer: no filter
logic, no persistence. On partial failure (< 10% of universe), logs a warning
and returns partial data. On >= 10% failure, raises ScanDegradationError and
surfaces a durable audit entry (D-06, D-07).

Exports: download_daily_bars, download_intraday_1m, get_ticker_frame,
         resolve_today_price, TodayPrice, ScanDegradationError
"""
import datetime
from dataclasses import dataclass
from typing import Optional, Set, Tuple

import pandas as pd
import yfinance as yf
import yfinance.shared as shared

from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import ET, to_et
from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)


# ============================================================
# Structs
# ============================================================

@dataclass(frozen=True)
class TodayPrice:
    """Today's intraday price summary resolved from a 1m yfinance frame.

    today_open:  float — open of the first regular-session 1m bar (>=09:30 ET)
                 when at/after 09:30 ET; latest premarket 1m close when premarket.
    today_price: float — latest 1m close (current price proxy).
    today_high:  float — max high across the relevant phase's bars (premarket
                 bars before 09:30, regular-session bars at/after 09:30).
    """

    today_open: float
    today_price: float
    today_high: float


# ============================================================
# Custom Exceptions
# ============================================================

class ScanDegradationError(Exception):
    """Raised when yfinance failure rate >= 10% of the universe (D-06).

    Signals that the data quality is insufficient for a reliable scan;
    caller should abort the scan session rather than publish a thin universe.
    """


# ============================================================
# Public API
# ============================================================

def download_daily_bars(
    yf_symbols: list,
    threads: int = 5,
    degradation_threshold: float = 0.10,
) -> Tuple[object, Set[str]]:
    """Download 1-year daily OHLCV bars for a batch of yfinance symbols.

    Clears yfinance.shared._ERRORS before downloading (Pitfall #2 — shared
    module-level state from previous calls must not inflate the failure count).
    Uses threads=5 (Pitfall #1 — integer, not True, to honour bounded-
    concurrency constraint D-01; avoids cpu_count*2 default).

    On partial failure < degradation_threshold: logs "scan_partial_data" warning,
    returns (data, failed_set).
    On failure >= degradation_threshold: logs "scan_aborted_data_degradation",
    surfaces a durable audit entry (D-07), raises ScanDegradationError.

    Failure detection is data-derived (UAT Test 2 / SCAN-06): yfinance 1.4.1 no
    longer populates ``shared._ERRORS`` reliably — a failed ticker is returned
    PRESENT but all-NaN while ``shared._ERRORS`` stays empty — so reading that
    dict silently under-counts failures and the D-06 gate would never trip. We
    instead treat a ticker as failed when ``get_ticker_frame`` yields None
    (missing or all-NaN), unioned with any ``shared._ERRORS`` keys older
    yfinance versions still record.

    yf_symbols: list of str — yfinance-format symbols (e.g. ["AAPL", "BRK-B"]).
    threads: int — number of download threads (default 5, bounded concurrency).
    degradation_threshold: float — fraction of failures that triggers abort.
    Returns (data, failed_set): data is the yf.download result; failed_set is the
        set of ticker strings with no usable data.
    Raises ScanDegradationError when failure_rate >= degradation_threshold.
    """
    if not yf_symbols:
        return {}, set()

    # Pitfall #2: clear shared module-level state before download
    shared._ERRORS.clear()

    data = yf.download(
        tickers=yf_symbols,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=threads,   # Pitfall #1: integer, not True
        progress=False,
    )

    failed = _detect_failed(data, yf_symbols)
    failure_rate = len(failed) / len(yf_symbols) if yf_symbols else 0.0

    if failure_rate >= degradation_threshold:
        _logger.error(
            "scan_aborted_data_degradation",
            failed_count=len(failed),
            total=len(yf_symbols),
            failure_rate_pct=round(failure_rate * 100, 1),
        )
        # D-07: durable surfacing via audit log (not Telegram — that is Phase 5)
        append_audit({
            "event": "scan_aborted_data_degradation",
            "failed_count": len(failed),
            "total": len(yf_symbols),
            "failure_rate_pct": round(failure_rate * 100, 1),
        })
        raise ScanDegradationError(
            f"Data degradation: {len(failed)}/{len(yf_symbols)} symbols failed "
            f"({round(failure_rate * 100, 1)}% >= {degradation_threshold * 100:.0f}% threshold)"
        )

    if failed:
        _logger.warning(
            "scan_partial_data",
            failed_count=len(failed),
            total=len(yf_symbols),
        )

    return data, failed


def _detect_failed(data: object, yf_symbols: list) -> Set[str]:
    """Return the set of symbols with no usable data after a yf.download (SCAN-06).

    A symbol counts as failed when get_ticker_frame returns None — i.e. it is
    missing from the result or its frame is entirely NaN (the way yfinance 1.4.1
    surfaces a delisted/failed ticker). Unioned with any shared._ERRORS keys that
    older yfinance versions still populate, so detection is version-robust.
    """
    failed = {sym for sym in yf_symbols if get_ticker_frame(data, sym) is None}
    requested = set(yf_symbols)
    failed |= {sym for sym in shared._ERRORS.keys() if sym in requested}
    return failed


def get_ticker_frame(data: object, symbol: str) -> Optional[pd.DataFrame]:
    """Extract and normalise a per-ticker DataFrame from a yf.download result.

    Retrieves the sub-frame for symbol from data, lowercases column names,
    and returns None for missing or all-NaN frames.

    data: object — the return value of yf.download() with group_by="ticker".
    symbol: str — the yfinance-format ticker (e.g. "AAPL").
    Returns a pd.DataFrame with lowercase columns, or None if absent/all-NaN.
    """
    try:
        ticker_df = data[symbol]
    except (KeyError, TypeError):
        return None

    if ticker_df is None:
        return None

    if not isinstance(ticker_df, pd.DataFrame):
        return None

    if ticker_df.dropna(how="all").empty:
        return None

    # Normalise column names to lowercase (Open Question #1 — defensive)
    ticker_df = ticker_df.copy()
    ticker_df.columns = ticker_df.columns.str.lower()
    return ticker_df


def resolve_today_price(
    frame_1m,
    scan_ts,
    now_et,
) -> Optional[TodayPrice]:
    """Resolve today's open, current price, and HOD from a 1m yfinance frame.

    PURE FUNCTION — no network calls, no internal clock. The caller injects the
    current time via `now_et` (a tz-aware datetime in ET). This satisfies the
    Phase 6 backtest seam: a backtester can replay historical 1m frames by
    injecting a historical now_et without touching live code.

    Phase selection is driven by the INJECTED now_et argument only:

    At/after 09:30 ET (regular session open):
      today_open  = open of the FIRST 1m bar whose ET bar-time >= 09:30
                    (the authoritative regular-session open; NOT a premarket bar open)
      today_price = close of the LATEST bar in the full frame (current price proxy)
      today_high  = max high of regular-session bars only (ET bar-time >= 09:30)

    Premarket (now_et < 09:30 ET):
      today_open  = today_price = close of the LATEST premarket bar
                    (provisional gap-up proxy; standard premarket %-change behaviour)
      today_high  = max high across all premarket bars in the frame

    Fail-closed:
      Returns None if frame_1m is None, empty, or no usable bar exists for the
      current phase (e.g. now_et >= 09:30 but the frame has no >=09:30 bars yet).

    Spec decision (2026-06-26-scanner-live-today-price-design.md §Decisions #2):
      The 09:30 ET regular-session start is a MARKET CONSTANT defined inline as
      datetime.time(9, 30). It is NOT read from rules.json or StrategyConfig —
      rules.json is the single source of STRATEGY parameters only; the RTH open
      time is a fixed market fact that never varies by strategy.

    frame_1m: pd.DataFrame | None — 1m OHLCV frame from get_ticker_frame() with a
              tz-aware DatetimeIndex (yfinance intraday carries tz info).
    scan_ts:  datetime — the timestamp of this scan run (passed through; unused
              internally but kept for caller context and future backtester use).
    now_et:   datetime — the current ET time (injected; MUST be tz-aware).
    Returns TodayPrice on success, None on fail-closed.
    """
    # Fail-closed: None or empty frame → no price data
    if frame_1m is None:
        return None
    if not isinstance(frame_1m, pd.DataFrame) or frame_1m.empty:
        return None

    # Market constant: regular-session open time (NOT from rules.json — see docstring).
    _RTH_OPEN = datetime.time(9, 30)  # US Eastern regular-session open — market constant

    # Determine phase from injected clock only (no internal now()/datetime.now() call).
    is_rth = now_et.time() >= _RTH_OPEN

    # Convert each 1m bar's index timestamp to ET for bar-clock comparison.
    # yfinance intraday frames USUALLY carry tz info, but some yfinance/pandas
    # combinations return a tz-naive (UTC) DatetimeIndex. Localize-or-convert
    # defensively and fail-closed on a non-datetime index — an unconditional
    # tz_convert here would raise and abort the ENTIRE scan (CR-01, 02-REVIEW).
    idx = frame_1m.index
    if not isinstance(idx, pd.DatetimeIndex):
        return None
    if idx.tz is None:
        # yfinance sometimes returns tz-naive intraday timestamps in UTC.
        idx_et = idx.tz_localize("UTC").tz_convert(ET)
    else:
        idx_et = idx.tz_convert(ET)

    if is_rth:
        # Regular-session phase: select bars with ET bar-time >= 09:30
        rth_mask = [ts.time() >= _RTH_OPEN for ts in idx_et]
        rth_bars = frame_1m.loc[rth_mask]
        if rth_bars.empty:
            # No >=09:30 bar yet — fail-closed (e.g. data lag on open)
            return None

        # today_open: open of the FIRST regular-session bar (authoritative gap open)
        today_open = float(rth_bars["open"].iloc[0])
        # today_price: close of the LATEST bar across the whole frame (current price)
        today_price = float(frame_1m["close"].iloc[-1])
        # today_high: max high of regular-session bars only
        today_high = float(rth_bars["high"].max())

    else:
        # Premarket phase: use all bars in the frame (all are premarket in this phase)
        premarket_bars = frame_1m  # frame_1m is non-empty (guarded above)
        if premarket_bars.empty:
            return None

        # today_open == today_price == latest premarket close (provisional gap proxy)
        latest_close = float(premarket_bars["close"].iloc[-1])
        today_open = latest_close
        today_price = latest_close
        # today_high: max high across premarket bars
        today_high = float(premarket_bars["high"].max())

    return TodayPrice(
        today_open=today_open,
        today_price=today_price,
        today_high=today_high,
    )


def download_intraday_1m(
    yf_symbols: list,
    threads: int = 5,
    degradation_threshold: float = 0.10,
) -> Tuple[object, Set[str]]:
    """Download live 1m intraday OHLCV bars (prepost=True) for a batch of yfinance symbols.

    Mirrors download_daily_bars contract exactly — same shared._ERRORS clearing
    (Pitfall #2), same bounded-concurrency threads parameter (Pitfall #1), same
    _detect_failed/get_ticker_frame failure detection, same ScanDegradationError
    gate at >= degradation_threshold, and same durable append_audit on abort (D-07).

    Uses yf.download(interval="1m", period="1d", prepost=True) so premarket bars
    are included starting ~04:00 ET. The daily-bar download path is unchanged; this
    function adds the intraday overlay for today's open/current-price/HOD only.

    On partial failure < degradation_threshold: logs "intraday_partial_data" warning,
    returns (data, failed_set).
    On failure >= degradation_threshold: logs "intraday_scan_aborted_data_degradation",
    surfaces a durable audit entry (D-07), raises ScanDegradationError.

    yf_symbols: list of str — yfinance-format symbols (e.g. ["AAPL", "BRK-B"]).
    threads: int — number of download threads (default 5, bounded concurrency).
    degradation_threshold: float — fraction of failures that triggers abort.
    Returns (data, failed_set): data is the yf.download result; failed_set is the
        set of ticker strings with no usable 1m data.
    Raises ScanDegradationError when failure_rate >= degradation_threshold.
    """
    if not yf_symbols:
        return {}, set()

    # Pitfall #2: clear shared module-level state before download
    shared._ERRORS.clear()

    data = yf.download(
        tickers=yf_symbols,
        period="1d",
        interval="1m",
        prepost=True,
        group_by="ticker",
        auto_adjust=True,
        threads=threads,   # Pitfall #1: integer, not True
        progress=False,
    )

    failed = _detect_failed(data, yf_symbols)
    failure_rate = len(failed) / len(yf_symbols) if yf_symbols else 0.0

    if failure_rate >= degradation_threshold:
        _logger.error(
            "intraday_scan_aborted_data_degradation",
            failed_count=len(failed),
            total=len(yf_symbols),
            failure_rate_pct=round(failure_rate * 100, 1),
        )
        # D-07: durable surfacing via audit log (not Telegram — that is Phase 5)
        append_audit({
            "event": "intraday_scan_aborted_data_degradation",
            "failed_count": len(failed),
            "total": len(yf_symbols),
            "failure_rate_pct": round(failure_rate * 100, 1),
        })
        raise ScanDegradationError(
            f"Intraday data degradation: {len(failed)}/{len(yf_symbols)} symbols failed "
            f"({round(failure_rate * 100, 1)}% >= {degradation_threshold * 100:.0f}% threshold)"
        )

    if failed:
        _logger.warning(
            "intraday_partial_data",
            failed_count=len(failed),
            total=len(yf_symbols),
        )

    return data, failed
