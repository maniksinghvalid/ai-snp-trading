#!/usr/bin/env python3
"""
bot.scanner.fetcher — yfinance daily-bar batch downloader for the premarket scanner.

Downloads 1-year daily OHLCV bars for ~500 S&P 500 symbols in a bounded-
concurrency batch call. Pure data-fetch layer: no filter logic, no persistence.
On partial failure (< 10% of universe), logs a warning and returns partial data.
On >= 10% failure, raises ScanDegradationError and surfaces a durable audit
entry (D-06, D-07).

Exports: download_daily_bars, get_ticker_frame, ScanDegradationError
"""
from typing import Optional, Set, Tuple

import pandas as pd
import yfinance as yf
import yfinance.shared as shared

from bot.safety.audit_log import append_audit
from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)


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

    yf_symbols: list of str — yfinance-format symbols (e.g. ["AAPL", "BRK-B"]).
    threads: int — number of download threads (default 5, bounded concurrency).
    degradation_threshold: float — fraction of failures that triggers abort.
    Returns (data, failed_set): data is the yf.download result; failed_set is the
        set of ticker strings that failed (from shared._ERRORS).
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

    failed = set(shared._ERRORS.keys())
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
