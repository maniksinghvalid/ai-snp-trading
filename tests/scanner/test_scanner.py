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
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

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
    """
    if scan_date is None:
        scan_date = date(2026, 6, 23)

    dates = pd.date_range(end=pd.Timestamp(scan_date), periods=n_days, freq="B")

    # Build volume: prior 14 days have volume_prior; last row has volume_today
    volumes = [volume_prior] * n_days
    volumes[-1] = volume_today

    # Build OHLCV: all rows same values except last two
    closes = [prior_close] * n_days
    closes[-2] = prior_close   # prior day close
    closes[-1] = today_close   # today close (above prior_high for D1)

    highs = [prior_close * 1.02] * n_days
    highs[-2] = prior_high     # prior day high
    highs[-1] = today_close * 1.01

    lows = [prior_close * 0.98] * n_days
    opens = [prior_close * 0.99] * n_days
    opens[-1] = today_open     # today open (used for gap computation)

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
