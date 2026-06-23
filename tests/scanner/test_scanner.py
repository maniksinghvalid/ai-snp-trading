#!/usr/bin/env python3
"""
tests.scanner.test_scanner — Wave 0 stub tests for SCAN-02/03/05/07/08 + SIG-01.

Covers: D1/D2/D3 daily filters; RVOL baseline no look-ahead; scan idempotency;
top-20 cap enforcement; intraday re-scan idempotency; active-candidate eviction
protection; K_5M subscribe called only for capped watchlist.

These stubs are skipped until implemented in plans 02-01, 02-02, 02-03.
"""
import pytest


class TestDailyFilters:
    """SCAN-02: D1/D2/D3 filter logic via passes_daily_filters()."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-01")
    def test_daily_filters(self):
        """Synthetic per-ticker DataFrame is filtered by D1 (above prior-day high),
        D2 (prior close > SMA200), and D3 (gap >= min_gap_pct) using the strategy config."""
        pytest.fail("Wave 0 stub")


class TestRvolNoLookahead:
    """SCAN-03: RVOL baseline uses only completed prior trading days."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-02")
    def test_rvol_no_lookahead(self):
        """RVOL 14-day baseline is computed with date < scan_date (strict cutoff, no look-ahead)."""
        pytest.fail("Wave 0 stub")


class TestIdempotency:
    """SCAN-05: Running the scan twice on the same day yields identical DB state."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-02")
    def test_idempotency(self):
        """Running run_daily_scan twice with the same scan_date produces the same watchlist row count."""
        pytest.fail("Wave 0 stub")


class TestTop20Cap:
    """SCAN-08: Watchlist is capped at top-20 by gap_pct."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-02")
    def test_top20_cap(self):
        """When > 20 candidates pass daily filters, exactly 20 rows are persisted in daily_scan."""
        pytest.fail("Wave 0 stub")


class TestIntradayRescan:
    """SCAN-07: Intraday re-scan updates rank without duplicate rows or active-candidate eviction."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_rescan_idempotent(self):
        """Re-running run_intraday_rescan on the same date updates ranks without adding duplicate rows."""
        pytest.fail("Wave 0 stub")

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_active_candidate_protected(self):
        """A candidate already subscribed/forming a position is not evicted by a higher-gap newcomer (D-04)."""
        pytest.fail("Wave 0 stub")


class TestSubscribeTop20Only:
    """SIG-01: gateway.subscribe() called only for the capped top-20 watchlist."""

    @pytest.mark.skip(reason="Wave 0 stub — implemented in 02-03")
    def test_subscribe_top20_only(self):
        """After run_daily_scan, subscribe() is invoked with at most 20 Moomoo-format codes."""
        pytest.fail("Wave 0 stub")
