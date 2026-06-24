#!/usr/bin/env python3
"""
bot.scanner.universe — S&P 500 constituent list fetcher with dated-cache fallback.

Scrapes the S&P 500 constituent list from Wikipedia on each scan, normalises
ticker symbols to yfinance/Moomoo formats, and writes a dated CSV cache under
data/. On scrape failure, falls back to the most recent dated cache file.
Never calls sys.exit — raises on unrecoverable failure.

Exports: fetch_sp500_symbols, wiki_to_yfinance, yfinance_to_moomoo
"""
import glob
import os

import pandas as pd

from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)

# URL for S&P 500 constituent list (Wikipedia)
_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# WR-05: minimum plausible universe size. The S&P 500 has ~500 constituents; a
# parsed/cached list materially smaller than this is treated as corrupt rather
# than authoritative (a truncated universe would also skew the 10% data-degradation
# denominator downstream, D-06).
_MIN_UNIVERSE_SIZE = 400


# ============================================================
# Public API
# ============================================================

def wiki_to_yfinance(symbol: str) -> str:
    """Convert a Wikipedia ticker symbol to yfinance format.

    Replaces dots with dashes so that BRK.B becomes BRK-B and
    BF.B becomes BF-B, matching Yahoo Finance URL format.

    symbol: str — ticker as it appears in the Wikipedia table (e.g. "BRK.B").
    Returns the yfinance-compatible symbol string (e.g. "BRK-B").
    """
    return symbol.replace(".", "-")


def yfinance_to_moomoo(yf_symbol: str) -> str:
    """Convert a yfinance ticker symbol to Moomoo code format.

    Prepends the "US." market prefix required by the Moomoo API.

    yf_symbol: str — yfinance-format symbol (e.g. "BRK-B").
    Returns the Moomoo code string (e.g. "US.BRK-B").
    """
    return f"US.{yf_symbol}"


def fetch_sp500_symbols(cache_dir: str = "data") -> list:
    """Fetch the current S&P 500 constituent list as yfinance-format symbols.

    Scrapes Wikipedia, normalises tickers (dot → dash), writes a dated CSV
    cache under cache_dir, and returns the symbol list. On ANY scrape exception,
    falls back to the most recent dated cache file. Raises RuntimeError if no
    cache exists and scrape fails.

    The cache filename uses now_et().date() (US Eastern) to remain
    consistent regardless of host timezone.

    cache_dir: str — directory for dated CSV cache files (created if absent).
    Returns list of str yfinance-format symbols (e.g. ["AAPL", "BRK-B"]).
    Raises RuntimeError when scrape fails and no cache file exists.
    """
    try:
        tables = pd.read_html(_WIKI_URL)
        # WR-05: do NOT blindly trust tables[0] — Wikipedia may reorder tables.
        # Select the constituents table by matching its expected columns.
        df = _select_constituents_table(tables)
        raw_symbols = df["Symbol"].tolist()
        yf_symbols = [wiki_to_yfinance(s) for s in raw_symbols]

        # WR-05: reject an implausibly small universe (truncated/garbage scrape)
        # before it can be written as an authoritative cache and silently shrink
        # the universe (which would also skew the 10% degradation denominator).
        if len(yf_symbols) < _MIN_UNIVERSE_SIZE:
            raise ValueError(
                f"Parsed S&P 500 table has only {len(yf_symbols)} symbols "
                f"(< {_MIN_UNIVERSE_SIZE} expected)"
            )

        # Write dated cache
        os.makedirs(cache_dir, exist_ok=True)
        today_str = now_et().date().isoformat()
        cache_path = os.path.join(cache_dir, f"sp500_{today_str}.csv")
        pd.DataFrame({"symbol": yf_symbols}).to_csv(cache_path, index=False)

        return yf_symbols

    except Exception:
        # Fall back to the most recent dated cache on any exception
        pattern = os.path.join(cache_dir, "sp500_*.csv")
        cache_files = sorted(glob.glob(pattern), reverse=True)
        if cache_files:
            cached_df = pd.read_csv(cache_files[0])
            cached_symbols = cached_df["symbol"].tolist()
            # WR-05: validate the cache too — a truncated/garbage cache must not be
            # returned as authoritative.
            if len(cached_symbols) >= _MIN_UNIVERSE_SIZE:
                _logger.warning(
                    "wikipedia_scrape_failed_using_cache",
                    cache=cache_files[0],
                )
                return cached_symbols
            _logger.warning(
                "cached_universe_too_small_rejected",
                cache=cache_files[0],
                count=len(cached_symbols),
                minimum=_MIN_UNIVERSE_SIZE,
            )
        raise RuntimeError(
            "No valid cached S&P 500 symbol list available and scrape failed"
        )


def _select_constituents_table(tables: list) -> pd.DataFrame:
    """Return the S&P 500 constituents table from a list of parsed tables (WR-05).

    Identifies the constituents table by its expected columns ("Symbol" plus a
    descriptive company column such as "Security" or "GICS Sector") rather than
    assuming it is tables[0]. Raises KeyError if no matching table is found.
    """
    for table in tables:
        cols = set(table.columns)
        if "Symbol" in cols and ("Security" in cols or "GICS Sector" in cols):
            return table
    raise KeyError(
        "No S&P 500 constituents table found (no table with 'Symbol' + "
        "'Security'/'GICS Sector' columns)"
    )
