#!/usr/bin/env python3
"""
tests.scanner.test_universe — Unit tests for SCAN-01 universe fetch.

Covers: Wikipedia scrape returns symbols; BRK.B/BF.B ticker normalisation
to dash form; cache fallback when scrape fails; hard failure when no cache exists;
WR-05 constituents-table selection and small-list rejection.
"""
import os
import pytest
import pandas as pd
from unittest.mock import patch


def _make_constituents_df(symbols=None) -> pd.DataFrame:
    """Build a realistic Wikipedia constituents table (~500 rows) with a
    'Symbol' + 'Security' column, mirroring the real page shape (WR-05).

    Always includes BRK.B so dot-to-dash normalisation is exercised; pads with
    synthetic tickers up to a plausible S&P-500 size.
    """
    base = list(symbols) if symbols is not None else ["AAPL", "MSFT", "BRK.B"]
    padded = list(base)
    i = 0
    while len(padded) < 503:
        padded.append(f"SYM{i:03d}")
        i += 1
    return pd.DataFrame({
        "Symbol": padded,
        "Security": [f"{s} Inc" for s in padded],
        "GICS Sector": ["Information Technology"] * len(padded),
    })


def _make_cache_text(n: int = 503) -> str:
    """Build a CSV cache body with n plausible symbols (>= _MIN_UNIVERSE_SIZE)."""
    lines = ["symbol", "AAPL", "MSFT", "BRK-B"]
    i = 0
    while len(lines) - 1 < n:
        lines.append(f"SYM{i:03d}")
        i += 1
    return "\n".join(lines) + "\n"


class TestWikipediaScrape:
    """SCAN-01: S&P 500 symbol list fetched from Wikipedia via pd.read_html."""

    def test_wikipedia_scrape_returns_symbols(self, tmp_path):
        """Mocked pd.read_html returns a list of S&P 500 ticker symbols, BRK.B normalised to BRK-B."""
        mock_df = _make_constituents_df()

        with patch("pandas.read_html", return_value=[mock_df]):
            from bot.scanner.universe import fetch_sp500_symbols
            result = fetch_sp500_symbols(cache_dir=str(tmp_path))

        assert "AAPL" in result
        assert "MSFT" in result
        assert "BRK-B" in result     # normalised from BRK.B
        assert "BRK.B" not in result  # original dot form must not appear


class TestTickerNormalization:
    """SCAN-01: Dot-to-dash normalisation for Moomoo-format codes."""

    def test_dot_to_dash_normalization(self):
        """BRK.B -> BRK-B and BF.B -> BF-B are normalised; plain symbols unchanged."""
        from bot.scanner.universe import wiki_to_yfinance, yfinance_to_moomoo

        assert wiki_to_yfinance("BRK.B") == "BRK-B"
        assert wiki_to_yfinance("BF.B") == "BF-B"
        assert wiki_to_yfinance("AAPL") == "AAPL"
        assert yfinance_to_moomoo("BRK-B") == "US.BRK-B"


class TestCacheFallback:
    """SCAN-01: Dated CSV cache used when live scrape fails."""

    def test_cache_fallback_on_scrape_failure(self, tmp_path):
        """When pd.read_html raises, the most-recent data/sp500_YYYY-MM-DD.csv is returned."""
        # Write a prior cache file with a plausible (>= 400) universe.
        cache_file = tmp_path / "sp500_2026-06-20.csv"
        cache_file.write_text(_make_cache_text())

        with patch("pandas.read_html", side_effect=Exception("Network error")):
            from bot.scanner.universe import fetch_sp500_symbols
            result = fetch_sp500_symbols(cache_dir=str(tmp_path))

        assert "AAPL" in result
        assert "MSFT" in result
        assert "BRK-B" in result

    def test_raises_when_no_cache_and_scrape_fails(self, tmp_path):
        """When scrape fails and no cache file exists, RuntimeError is raised."""
        with patch("pandas.read_html", side_effect=Exception("Network error")):
            from bot.scanner.universe import fetch_sp500_symbols
            with pytest.raises(RuntimeError, match="No valid cached S&P 500 symbol list"):
                fetch_sp500_symbols(cache_dir=str(tmp_path))


class TestConstituentsTableSelection:
    """WR-05: the constituents table is selected by columns, not by position."""

    def test_selects_table_by_columns_not_position(self, tmp_path):
        """When the constituents table is NOT tables[0], it is still selected by its
        'Symbol' + 'Security' columns rather than blindly using tables[0]."""
        # tables[0] is a decoy that happens to have a 'Symbol' column but is not the
        # constituents table (no Security/GICS Sector); the real table is tables[1].
        decoy = pd.DataFrame({"Symbol": ["DECOY1", "DECOY2"], "Note": ["x", "y"]})
        real = _make_constituents_df()

        with patch("pandas.read_html", return_value=[decoy, real]):
            from bot.scanner.universe import fetch_sp500_symbols
            result = fetch_sp500_symbols(cache_dir=str(tmp_path))

        assert "AAPL" in result, "Real constituents table must be selected by columns"
        assert "DECOY1" not in result, "Decoy tables[0] must NOT be used"

    def test_falls_back_to_cache_when_no_matching_table(self, tmp_path):
        """If no parsed table has the expected constituents columns, the validated
        cache is used rather than trusting a wrong table."""
        decoy = pd.DataFrame({"Symbol": ["DECOY1"], "Note": ["x"]})
        cache_file = tmp_path / "sp500_2026-06-20.csv"
        cache_file.write_text(_make_cache_text())

        with patch("pandas.read_html", return_value=[decoy]):
            from bot.scanner.universe import fetch_sp500_symbols
            result = fetch_sp500_symbols(cache_dir=str(tmp_path))

        assert "AAPL" in result and "DECOY1" not in result


class TestUniverseSizeValidation:
    """WR-05: implausibly small scrape/cache results are rejected."""

    def test_small_scrape_result_falls_back_to_cache(self, tmp_path):
        """A constituents table with too few symbols is rejected; the validated cache
        is returned instead of a truncated universe."""
        tiny = pd.DataFrame({
            "Symbol": ["AAPL", "MSFT"],
            "Security": ["Apple Inc", "Microsoft Corp"],
        })
        cache_file = tmp_path / "sp500_2026-06-20.csv"
        cache_file.write_text(_make_cache_text())

        with patch("pandas.read_html", return_value=[tiny]):
            from bot.scanner.universe import fetch_sp500_symbols
            result = fetch_sp500_symbols(cache_dir=str(tmp_path))

        # Full cached universe returned, not the 2-symbol truncated scrape.
        assert len(result) >= 400, "Truncated scrape must be rejected in favour of cache"

    def test_small_cache_rejected(self, tmp_path):
        """A truncated/garbage cache (< 400 symbols) must NOT be returned as
        authoritative; with no valid cache, RuntimeError is raised."""
        cache_file = tmp_path / "sp500_2026-06-20.csv"
        cache_file.write_text("symbol\nAAPL\nMSFT\nBRK-B\n")  # only 3 symbols

        with patch("pandas.read_html", side_effect=Exception("Network error")):
            from bot.scanner.universe import fetch_sp500_symbols
            with pytest.raises(RuntimeError, match="No valid cached S&P 500 symbol list"):
                fetch_sp500_symbols(cache_dir=str(tmp_path))
