#!/usr/bin/env python3
"""
tests.scanner.test_universe — Unit tests for SCAN-01 universe fetch.

Covers: Wikipedia scrape returns symbols; BRK.B/BF.B ticker normalisation
to dash form; cache fallback when scrape fails; hard failure when no cache exists.
"""
import os
import pytest
import pandas as pd
from unittest.mock import patch


class TestWikipediaScrape:
    """SCAN-01: S&P 500 symbol list fetched from Wikipedia via pd.read_html."""

    def test_wikipedia_scrape_returns_symbols(self, tmp_path):
        """Mocked pd.read_html returns a list of S&P 500 ticker symbols, BRK.B normalised to BRK-B."""
        mock_df = pd.DataFrame({"Symbol": ["AAPL", "MSFT", "BRK.B"]})

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
        # Write a prior cache file
        cache_file = tmp_path / "sp500_2026-06-20.csv"
        cache_file.write_text("symbol\nAAPL\nMSFT\nBRK-B\n")

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
            with pytest.raises(RuntimeError, match="No cached S&P 500 symbol list"):
                fetch_sp500_symbols(cache_dir=str(tmp_path))
