#!/usr/bin/env python3
"""
tests.scanner.test_scanner — Tests for bot/scanner/scanner.py

Covers:
  SCAN-02: D1/D2/D3 daily filters via passes_daily_filters() (config-driven)
  SCAN-03: RVOL baseline uses only prior completed trading sessions (no look-ahead)
  SCAN-05: Idempotent upsert — running scan twice yields same row count
  SCAN-08: Top-20 cap — >20 candidates persists exactly 20 (gap-ranked)
  Test insufficient history exclusion (< rvol_lookback_days prior sessions → excluded)
  Test upsert updates rank on re-persist
"""
import asyncio
import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from bot.config.loader import StrategyConfig
from bot.state.migrations import run_migrations
from bot.state.store import StateStore


# ============================================================
# Helpers / Factories
# ============================================================

def _make_cfg(
    min_price_usd: float = 3.0,
    d3_min_gap_pct: float = 3.0,
    rvol_lookback_days: int = 14,
) -> StrategyConfig:
    """Build a minimal StrategyConfig for testing."""
    return StrategyConfig(
        min_price_usd=min_price_usd,
        d3_min_gap_pct=d3_min_gap_pct,
        rvol_min=2.0,
        rvol_lookback_days=rvol_lookback_days,
        earliest_entry_et="10:05",
        latest_entry_et="15:30",
        force_close_et="15:51",
        initial_stop_pct=1.0,
        partial_profit_trigger_r=0.75,
        partial_profit_fraction=0.3333,
        breakeven_trigger_r=1.0,
        max_risk_per_trade_pct=1.0,
        max_position_size_pct=10,
        max_concurrent_positions=5,
        max_trades_per_day=5,
    )


def _make_daily_frame(
    n_days: int = 220,
    prior_close: float = 100.0,
    prior_high: float = 105.0,
    today_open: float = 104.0,
    today_close: float = 106.0,
    volume_today: float = 2_000_000.0,
    volume_prior: float = 1_000_000.0,
    scan_date: date = None,
) -> pd.DataFrame:
    """Build a synthetic daily OHLCV DataFrame with n_days rows.

    The last row represents today (scan_date); the second-to-last row
    represents the prior trading day. The index is a DatetimeIndex.
    All column names are lowercase (as returned by get_ticker_frame).

    By default:
      - prior_close=100.0, prior_high=105.0
      - today_open=104.0 (gap = (104-100)/100*100 = 4.0% — above 3.0% threshold)
      - today_close=106.0 (above prior_high=105.0 — D1 passes)

    SMA200 invariant: the first (n_days - 2) rows are set to prior_close * 0.90
    so that the 200-day SMA of prior closes (90.0) is strictly below prior_close (100.0),
    ensuring D2 (prior_close > SMA200) passes by default.
    """
    if scan_date is None:
        scan_date = date(2026, 6, 23)

    dates = pd.date_range(end=pd.Timestamp(scan_date), periods=n_days, freq="B")

    # Build volume: prior rows have volume_prior; last row has volume_today
    volumes = [volume_prior] * n_days
    volumes[-1] = volume_today

    # Build OHLCV: bulk rows use prior_close * 0.90 so SMA200 < prior_close (D2)
    # Prior day (iloc[-2]) = prior_close; today (iloc[-1]) = today values
    sma_base = prior_close * 0.90   # 90.0 when prior_close=100.0 → SMA200 ≈ 90.0
    closes = [sma_base] * n_days
    closes[-2] = prior_close        # prior day close (used in D2/D3)
    closes[-1] = today_close        # today close (must be above prior_high for D1)

    highs = [sma_base * 1.02] * n_days
    highs[-2] = prior_high          # prior day high
    highs[-1] = today_close * 1.01

    lows = [sma_base * 0.98] * n_days
    opens = [sma_base * 0.99] * n_days
    opens[-1] = today_open          # today open (used for gap computation)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=dates)

    return df


def _open_in_memory_store() -> StateStore:
    """Return a StateStore backed by an in-memory SQLite DB (runs migrations)."""
    store = StateStore(db_path=":memory:")
    store.open()
    return store


# ============================================================
# SCAN-02: Daily Filters (config-driven)
# ============================================================

class TestDailyFilters:
    """SCAN-02: D1/D2/D3 filter logic via passes_daily_filters()."""

    def test_daily_filters(self, tmp_state_db, minimal_rules):
        """A frame satisfying D1/D2/D3 + price passes; one failing D3 (gap < threshold) is excluded.

        Config-driven proof (D-12): raising cfg.d3_min_gap_pct above the frame's gap
        excludes a symbol that previously passed.
        """
        from bot.scanner.scanner import run_daily_scan

        cfg = _make_cfg(d3_min_gap_pct=3.0)
        scan_date = date(2026, 6, 23)

        # Build a passing frame: gap = (104-100)/100*100 = 4.0% (above 3.0%)
        passing_frame = _make_daily_frame(
            prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )
        # Build a failing frame: gap = (102-100)/100*100 = 2.0% (below 3.0%)
        failing_frame = _make_daily_frame(
            prior_close=100.0, today_open=102.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )

        store = StateStore()
        store.open()

        # Patch the leaf modules so no actual network/file I/O occurs
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["PASS", "FAIL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: passing_frame if sym == "PASS" else failing_frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.PASS" in result, "Passing symbol must be in watchlist"
        assert "US.FAIL" not in result, "Failing symbol (gap below threshold) must be excluded"

    def test_daily_filters_config_driven(self, tmp_state_db):
        """Raising cfg.d3_min_gap_pct excludes a symbol that previously passed (D-12)."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)

        # Frame with gap = 4.0%
        frame_4pct = _make_daily_frame(
            prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )

        # With 3.0% threshold → should pass
        cfg_low = _make_cfg(d3_min_gap_pct=3.0)
        store_low = StateStore()
        store_low.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame_4pct), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result_low = run_daily_scan(store=store_low, gateway=None, cfg=cfg_low, scan_date=scan_date)

        store_low.close()

        # With 5.0% threshold → should NOT pass (gap is only 4.0%)
        cfg_high = _make_cfg(d3_min_gap_pct=5.0)
        store_high = StateStore()
        store_high.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame_4pct), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result_high = run_daily_scan(store=store_high, gateway=None, cfg=cfg_high, scan_date=scan_date)

        store_high.close()

        assert "US.AAPL" in result_low, "4% gap must pass 3% threshold"
        assert "US.AAPL" not in result_high, "4% gap must fail 5% threshold (config-driven)"


# ============================================================
# SCAN-03: RVOL No Look-Ahead
# ============================================================

class TestRvolNoLookahead:
    """SCAN-03: RVOL baseline uses only completed prior trading days."""

    def test_rvol_no_lookahead(self, tmp_state_db):
        """RVOL baseline equals mean of prior 14 sessions; today's huge volume is excluded.

        Today's volume is set to 99x normal. The baseline (mean of prior 14 sessions)
        must be the same regardless of whether today's row is included in the frame,
        because the scanner uses date < scan_date (strict cutoff).
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        normal_vol = 1_000_000.0
        huge_today_vol = 99_000_000.0

        # Frame with huge today volume but normal prior sessions
        frame = _make_daily_frame(
            n_days=220,
            prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
            volume_today=huge_today_vol,
            volume_prior=normal_vol,
        )

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=_make_cfg(), scan_date=scan_date)

        # Read back the persisted rvol_baseline
        row = store.conn.execute(
            "SELECT rvol_baseline FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()
        store.close()

        assert "US.AAPL" in result, "AAPL should pass filters"
        assert row is not None, "Row must be persisted"
        # rvol_baseline is the mean of prior 14 sessions (all normal_vol)
        # It must be normal_vol, NOT inflated by today's huge volume
        assert abs(row[0] - normal_vol) < 1.0, (
            f"rvol_baseline={row[0]} should equal normal_vol={normal_vol} "
            "(today's huge volume must be excluded from baseline)"
        )


# ============================================================
# Insufficient History Exclusion
# ============================================================

class TestInsufficientHistory:
    """Symbols with < rvol_lookback_days prior sessions are excluded."""

    def test_insufficient_history_excluded(self, tmp_state_db):
        """A symbol with fewer prior sessions than rvol_lookback_days is excluded from watchlist."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(rvol_lookback_days=14)

        # Frame with only 10 rows total (9 prior, 1 today) — insufficient for 14-day lookback
        short_frame = _make_daily_frame(n_days=10, scan_date=scan_date)
        good_frame = _make_daily_frame(n_days=220, scan_date=scan_date)

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["SHORT", "GOOD"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: short_frame if sym == "SHORT" else good_frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.SHORT" not in result, "Short-history symbol must be excluded"
        assert "US.GOOD" in result, "Good-history symbol must be included"

    def test_no_sma200_symbol_excluded(self, tmp_state_db):
        """CR-01 regression: a symbol with >= rvol_lookback_days but < 200 prior
        sessions has no SMA200 and MUST be excluded (D2 trend filter cannot be
        evaluated). Previously a None SMA200 was coerced to 0.0, making D2
        (`prior_close > 0.0`) always True — silently admitting the symbol with
        zero trend verification.

        NO_SMA200 has 50 rows (49 prior sessions): above the 14-day RVOL gate but
        well below 200, so sma(prior_closes, 200) is NaN. It otherwise satisfies
        D1/D3/price, so the ONLY thing that can exclude it is the SMA200 gate.
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(rvol_lookback_days=14)

        # 50 rows → 49 prior sessions: passes 14-day history gate, fails 200-day SMA.
        no_sma_frame = _make_daily_frame(n_days=50, scan_date=scan_date)
        good_frame = _make_daily_frame(n_days=220, scan_date=scan_date)

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["NOSMA", "GOOD"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: no_sma_frame if sym == "NOSMA" else good_frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.NOSMA" not in result, (
            "Symbol without a computable SMA200 must be excluded (D2 cannot be verified)"
        )
        assert "US.GOOD" in result, "Full-history symbol must still be included"


# ============================================================
# SCAN-08: Top-20 Cap
# ============================================================

class TestTop20Cap:
    """SCAN-08: Watchlist is capped at top-20 by gap_pct."""

    def test_top20_cap(self, tmp_state_db):
        """When > 20 candidates pass daily filters, exactly 20 rows are persisted in daily_scan.

        25 symbols are generated with incrementally-sized gaps (1..25 %).
        After the scan: only 20 rows in DB; rank=1 has the highest gap (25%).
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        n_symbols = 25
        symbols = [f"SYM{i:02d}" for i in range(1, n_symbols + 1)]

        def _make_frame_for(sym: str) -> pd.DataFrame:
            idx = int(sym[3:])  # 1..25
            gap_pct = float(idx)  # 1%, 2%, ... 25%
            today_open = 100.0 * (1 + gap_pct / 100.0)
            return _make_daily_frame(
                n_days=220,
                prior_close=100.0,
                today_open=today_open,
                today_close=today_open + 2.0,  # above prior_high
                prior_high=today_open - 0.5,   # below today_close for D1
                scan_date=scan_date,
            )

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=symbols), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: _make_frame_for(sym)), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=_make_cfg(d3_min_gap_pct=0.5),
                                    scan_date=scan_date)

        # Verify exactly 20 rows
        rows = store.conn.execute(
            "SELECT code, gap_pct, rank FROM daily_scan WHERE scan_date=? ORDER BY rank",
            (scan_date.isoformat(),),
        ).fetchall()
        store.close()

        assert len(result) == 20, f"run_daily_scan must return exactly 20 codes, got {len(result)}"
        assert len(rows) == 20, f"daily_scan must have exactly 20 rows, got {len(rows)}"

        # rank=1 must have highest gap (SYM25 = 25%)
        assert rows[0][0] == "US.SYM25", f"rank=1 should be SYM25 (highest gap), got {rows[0][0]}"
        assert rows[0][2] == 1, "rank=1 row must have rank=1"

        # SYM01..SYM05 (gaps 1-5%) must not be persisted
        persisted_codes = {r[0] for r in rows}
        for i in range(1, 6):
            assert f"US.SYM{i:02d}" not in persisted_codes, (
                f"SYM{i:02d} (low gap) must be excluded from top-20"
            )


# ============================================================
# Non-trading day
# ============================================================

class TestNonTradingDay:
    """run_daily_scan returns [] on a non-trading day without raising."""

    def test_non_trading_day_returns_empty(self, tmp_state_db):
        """scan_date that is_trading_day=False → return [] with no exception."""
        from bot.scanner.scanner import run_daily_scan

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.is_trading_day", return_value=False):
            result = run_daily_scan(store=store, gateway=None, cfg=_make_cfg(),
                                    scan_date=date(2024, 1, 1))

        store.close()
        assert result == [], "Non-trading-day scan must return empty list"


# ============================================================
# SCAN-05: Idempotency
# ============================================================

class TestIdempotency:
    """SCAN-05: Running the scan twice on the same day yields identical DB state."""

    def test_idempotency(self, tmp_state_db):
        """Running run_daily_scan twice with same scan_date produces same row count (no duplicates)."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        frame = _make_daily_frame(scan_date=scan_date)
        store = StateStore()
        store.open()

        kwargs = dict(
            fetch_sp500=dict(return_value=["AAPL", "MSFT"]),
            download=dict(return_value=({}, set())),
        )

        def _get_frame(data, sym):
            return frame

        for _ in range(2):
            with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
                 patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
                 patch("bot.scanner.scanner.get_ticker_frame", side_effect=_get_frame), \
                 patch("bot.scanner.scanner.is_trading_day", return_value=True):

                run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        row_count = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=?",
            (scan_date.isoformat(),),
        ).fetchone()[0]
        store.close()

        assert row_count == 2, (
            f"Expected 2 rows (AAPL + MSFT), got {row_count} — idempotency failure"
        )

    def test_upsert_updates_rank(self, tmp_state_db):
        """Re-persisting the same code with a different rank/gap updates the existing row."""
        from bot.scanner.scanner import _persist_watchlist

        store = StateStore()
        store.open()

        scan_date = date(2026, 6, 23)
        candidates = [
            {
                "code": "US.AAPL",
                "gap_pct": 4.0,
                "rank": 1,
                "prior_day_high": 105.0,
                "prior_close": 100.0,
                "sma200": 90.0,
                "rvol_baseline": 1_000_000.0,
            }
        ]

        _persist_watchlist(store.conn, scan_date, candidates, "premarket")

        # Re-persist with updated gap and rank
        candidates[0]["gap_pct"] = 5.5
        candidates[0]["rank"] = 2

        _persist_watchlist(store.conn, scan_date, candidates, "intraday_1")

        row = store.conn.execute(
            "SELECT gap_pct, rank, scan_pass FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()
        count = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()[0]
        store.close()

        assert count == 1, "Re-upsert must not create a duplicate row"
        assert row[0] == 5.5, f"gap_pct must be updated to 5.5, got {row[0]}"
        assert row[1] == 2, f"rank must be updated to 2, got {row[1]}"
        assert row[2] == "intraday_1", f"scan_pass must be updated, got {row[2]}"


# ============================================================
# SIG-01: Subscribe wiring in run_daily_scan
# ============================================================

def _make_mock_gateway():
    """Return an async-capable mock gateway for subscribe tests."""
    gw = MagicMock()
    gw.subscribe = AsyncMock()
    return gw


class TestSubscribeWiring:
    """SIG-01: run_daily_scan calls gateway.subscribe with exactly the top-20 codes."""

    def test_subscribe_top20_only(self, tmp_state_db):
        """run_daily_scan with 25 passing candidates → gateway.subscribe called once with len==20."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        n_symbols = 25
        symbols = [f"SYM{i:02d}" for i in range(1, n_symbols + 1)]

        def _make_frame_for(sym: str) -> pd.DataFrame:
            idx = int(sym[3:])  # 1..25
            gap_pct = float(idx)  # 1%, 2%, ... 25%
            today_open = 100.0 * (1 + gap_pct / 100.0)
            return _make_daily_frame(
                n_days=220,
                prior_close=100.0,
                today_open=today_open,
                today_close=today_open + 2.0,
                prior_high=today_open - 0.5,
                scan_date=scan_date,
            )

        store = StateStore()
        store.open()
        gw = _make_mock_gateway()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=symbols), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: _make_frame_for(sym)), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(
                store=store, gateway=gw, cfg=_make_cfg(d3_min_gap_pct=0.5),
                scan_date=scan_date,
            )

        store.close()

        # subscribe must be called exactly once
        gw.subscribe.assert_called_once()
        called_codes = gw.subscribe.call_args[0][0]

        assert len(result) == 20, f"run_daily_scan must return 20 codes, got {len(result)}"
        assert len(called_codes) == 20, (
            f"gateway.subscribe must be called with exactly 20 codes (SIG-01), got {len(called_codes)}"
        )
        # The subscribe codes must match the returned codes exactly
        assert set(called_codes) == set(result), (
            "gateway.subscribe must be called with the same codes as returned"
        )

    def test_subscribe_skipped_when_empty(self, tmp_state_db):
        """When no candidates pass (non-trading day or empty result), subscribe must NOT be called."""
        from bot.scanner.scanner import run_daily_scan

        store = StateStore()
        store.open()
        gw = _make_mock_gateway()

        with patch("bot.scanner.scanner.is_trading_day", return_value=False):
            result = run_daily_scan(
                store=store, gateway=gw, cfg=_make_cfg(),
                scan_date=date(2024, 1, 1),  # NYSE holiday
            )

        store.close()

        assert result == [], "Non-trading day must return empty list"
        gw.subscribe.assert_not_called()


# ============================================================
# SCAN-07: run_intraday_rescan (D-04, D-05)
# ============================================================

class TestIntradayRescan:
    """SCAN-07: run_intraday_rescan — idempotent merge, protect active candidates."""

    def test_rescan_idempotent(self, tmp_state_db):
        """Running run_intraday_rescan for the same scan_date with overlapping candidates
        does not grow the row count for already-present codes (upsert, D-05).
        """
        from bot.scanner.scanner import run_daily_scan, run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=0.5)
        frame = _make_daily_frame(scan_date=scan_date)

        store = StateStore()
        store.open()

        # Initial daily scan
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        row_count_after_daily = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=?",
            (scan_date.isoformat(),),
        ).fetchone()[0]

        # Re-scan with same symbols
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes=set(), scan_date=scan_date, scan_pass="intraday_1",
            )

        row_count_after_rescan = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=?",
            (scan_date.isoformat(),),
        ).fetchone()[0]
        store.close()

        assert row_count_after_daily == row_count_after_rescan, (
            f"Re-scan must not grow row count: before={row_count_after_daily}, "
            f"after={row_count_after_rescan} (D-05 idempotency failure)"
        )

    def test_active_candidate_protected(self, tmp_state_db):
        """An active_code ranking 22nd on re-rank is STILL kept in the top-20 (D-04).

        Setup: 25 new candidates rank 1..25 by gap. An active_code has lower gap
        so it would naturally rank 22nd, but must be guaranteed in the top-20.
        """
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=0.5)

        # 21 new candidates with gaps 5..25% (21 codes, ranking above protected)
        n_new = 21
        new_symbols = [f"NEW{i:02d}" for i in range(1, n_new + 1)]

        def _frame_for(sym: str) -> pd.DataFrame:
            if sym.startswith("NEW"):
                idx = int(sym[3:])
                gap_pct = 5.0 + float(idx)  # 6..26%
            else:
                gap_pct = 4.0  # active candidate has lower gap
            today_open = 100.0 * (1 + gap_pct / 100.0)
            return _make_daily_frame(
                n_days=220,
                prior_close=100.0,
                today_open=today_open,
                today_close=today_open + 2.0,
                prior_high=today_open - 0.5,
                scan_date=scan_date,
            )

        # The active code that would be bumped out
        active_sym = "PROT"  # yfinance symbol
        active_code = "US.PROT"
        all_symbols = new_symbols + [active_sym]

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=all_symbols), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda d, s: _frame_for(s)), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes={active_code},
                scan_date=scan_date, scan_pass="intraday_1",
            )

        persisted_codes = {
            row[0] for row in store.conn.execute(
                "SELECT code FROM daily_scan WHERE scan_date=?",
                (scan_date.isoformat(),),
            ).fetchall()
        }
        store.close()

        assert active_code in result, (
            f"Active candidate {active_code} must be protected in top-20 (D-04)"
        )
        assert active_code in persisted_codes, (
            f"Active candidate {active_code} must be persisted (D-04)"
        )
        # Total persisted must be capped at 20
        assert len(result) == 20, f"Top-20 cap enforced: expected 20, got {len(result)}"

    def test_rescan_subscribes_only_new(self, tmp_state_db):
        """run_intraday_rescan subscribes only codes NOT already in active_codes."""
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=0.5)
        frame = _make_daily_frame(scan_date=scan_date)

        store = StateStore()
        store.open()
        gw = _make_mock_gateway()

        # AAPL is already active; MSFT is new
        active_codes = {"US.AAPL"}

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            run_intraday_rescan(
                store=store, gateway=gw, cfg=cfg,
                active_codes=active_codes, scan_date=scan_date, scan_pass="intraday_1",
            )

        store.close()

        # subscribe must have been called only with new codes
        gw.subscribe.assert_called_once()
        subscribed_codes = gw.subscribe.call_args[0][0]

        assert "US.AAPL" not in subscribed_codes, (
            "Already-active US.AAPL must NOT be re-subscribed"
        )
        assert "US.MSFT" in subscribed_codes, (
            "New code US.MSFT must be subscribed"
        )
