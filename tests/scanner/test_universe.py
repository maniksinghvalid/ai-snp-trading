#!/usr/bin/env python3
"""
tests.scanner.test_universe — Wave 0 stub tests for SCAN-01 universe fetch.

Covers: Wikipedia scrape returns ~500 symbols; BRK.B/BF.B ticker normalisation
to dash form; cache fallback when scrape fails; hard failure when no cache exists.

These stubs are skipped until implemented in plan 02-01.
"""
import pytest


class TestWikipediaScrape:
    """SCAN-01: S&P 500 symbol list fetched from Wikipedia via pd.read_html."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_wikipedia_scrape_returns_symbols(self):
        """Mocked pd.read_html returns a list of ~500 S&P 500 ticker symbols."""
        pytest.fail("Wave 0 stub")


class TestTickerNormalization:
    """SCAN-01: Dot-to-dash normalisation for Moomoo-format codes."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_dot_to_dash_normalization(self):
        """BRK.B -> BRK-B and BF.B -> BF-B are normalised before persistence."""
        pytest.fail("Wave 0 stub")


class TestCacheFallback:
    """SCAN-01: Dated CSV cache used when live scrape fails."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_cache_fallback_on_scrape_failure(self):
        """When pd.read_html raises, the most-recent data/sp500_YYYY-MM-DD.csv is returned."""
        pytest.fail("Wave 0 stub")

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_raises_when_no_cache_and_scrape_fails(self):
        """When scrape fails and no cache file exists, RuntimeError is raised."""
        pytest.fail("Wave 0 stub")
