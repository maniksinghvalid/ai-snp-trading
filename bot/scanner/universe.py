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
        df = tables[0]
        # Validate structure — KeyError/missing column falls through to cache
        if "Symbol" not in df.columns:
            raise KeyError("'Symbol' column not found in Wikipedia table")
        raw_symbols = df["Symbol"].tolist()
        yf_symbols = [wiki_to_yfinance(s) for s in raw_symbols]

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
            _logger.warning(
                "wikipedia_scrape_failed_using_cache",
                cache=cache_files[0],
            )
            cached_df = pd.read_csv(cache_files[0])
            return cached_df["symbol"].tolist()
        raise RuntimeError(
            "No cached S&P 500 symbol list available and scrape failed"
        )
