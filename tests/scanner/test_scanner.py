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
import datetime as _dt
import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from bot.config.loader import StrategyConfig
from bot.state.migrations import run_migrations
from bot.state.store import StateStore

# ============================================================
# 02-05 migration helpers
# ============================================================

# Sentinel non-empty 1m data returned by patched download_intraday_1m.
# Non-empty so the whole-universe-degradation guard (len(failed)==len(universe))
# is not tripped when tests don't care about the intraday path.
_SENTINEL_1M = {"__sentinel__": True}
_INTRADAY_OK = (_SENTINEL_1M, set())

# Fixed premarket ET time used in tests that patch now_et (08:30 ET)
_ET = ZoneInfo("America/New_York")
_PREMARKET_ET = _dt.datetime(2026, 6, 23, 8, 30, tzinfo=_ET)


def _make_today_price(frame: pd.DataFrame):
    """Return TodayPrice from the last row of a daily frame (used to patch resolve_today_price).

    This allows patched tests to supply today's gap/D1/D3 numbers via the 1m
    path while still using the existing _make_daily_frame helper for the daily
    slow inputs (SMA200, RVOL baseline, prior close/high).
    """
    from bot.scanner.fetcher import TodayPrice
    last = frame.iloc[-1]
    return TodayPrice(
        today_open=float(last["open"]),
        today_price=float(last["close"]),
        today_high=float(last["high"]),
    )


# ============================================================
# Helpers / Factories
# ============================================================

# Phase 4 (04-01) added 10 required execution fields to StrategyConfig.
# These scanner tests don't exercise execution behavior, so _make_cfg spreads
# these canonical defaults (matching rules.json) to satisfy the constructor.
_EXECUTION_DEFAULTS = {
    "entry_limit_buffer_usd": 0.05,
    "entry_ttl_seconds": 20,
    "entry_max_retries": 2,
    "entry_poll_interval_seconds": 5,
    "exit_limit_buffer_usd": 0.05,
    "exit_ttl_seconds": 15,
    "exit_escalation_step_usd": 0.10,
    "exit_escalation_cadence_seconds": 10,
    "force_close_escalation_step_usd": 0.20,
    "force_close_escalation_cadence_seconds": 15,
}

# ============================================================
# Service defaults (Phase 5 — canonical rules.json service block)
# ============================================================
#
# StrategyConfig gained 15 required service fields in Phase 5 (05-00).
# These tests don't exercise service/scheduler behavior, so every
# StrategyConfig(...) call spreads these canonical defaults to satisfy the
# constructor. Values match the service block in rules.json exactly.
_SERVICE_DEFAULTS = {
    "premarket_scan_et": "08:30",
    "market_open_et": "09:30",
    "intraday_rescan_interval_min": 30,
    "intraday_rescan_start_et": "09:55",
    "intraday_rescan_end_et": "12:55",
    "eod_report_et": "15:55",
    "watchdog_poll_interval_s": 60,
    "watchdog_reconnect_initial_s": 5,
    "watchdog_reconnect_cap_s": 60,
    "alerts_enabled": True,
    "misfire_grace_scan_s": 3600,
    "misfire_grace_rescan_s": 600,
    "force_close_misfire_grace_s": 300,
    "launchd_throttle_interval_s": 30,
    "crash_loop_alert_threshold": 5,
}


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
        **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: passing_frame if sym == "PASS" else failing_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame_4pct), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result_low = run_daily_scan(store=store_low, gateway=None, cfg=cfg_low, scan_date=scan_date)

        store_low.close()

        # With 5.0% threshold → should NOT pass (gap is only 4.0%)
        cfg_high = _make_cfg(d3_min_gap_pct=5.0)
        store_high = StateStore()
        store_high.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame_4pct), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: short_frame if sym == "SHORT" else good_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: no_sma_frame if sym == "NOSMA" else good_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.NOSMA" not in result, (
            "Symbol without a computable SMA200 must be excluded (D2 cannot be verified)"
        )
        assert "US.GOOD" in result, "Full-history symbol must still be included"


# ============================================================
# CR-02: today-row alignment with scan_date (no-look-ahead)
# ============================================================

class TestTodayRowAlignment:
    """CR-02 regression: gap/D1/D3 must use the row dated scan_date, never iloc[-1]."""

    def test_premarket_no_today_bar_excluded(self, tmp_state_db):
        """When yfinance has not yet produced today's daily bar (the normal premarket
        case), frame.index[-1] is the PRIOR session. The scanner must NOT silently
        evaluate yesterday-vs-day-before; it must skip the symbol (no today bar).

        Build a frame whose last row is the day BEFORE scan_date. Previously the
        positional iloc[-1]/iloc[-2] path would happily rank the wrong session.
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        # Frame ending the prior business day — no row dated 2026-06-23.
        prior_day = pd.Timestamp(scan_date) - pd.tseries.offsets.BDay(1)
        n_days = 220
        dates = pd.date_range(end=prior_day, periods=n_days, freq="B")
        sma_base = 90.0
        closes = [sma_base] * n_days
        closes[-1] = 100.0
        highs = [sma_base * 1.02] * n_days
        highs[-1] = 105.0
        lows = [sma_base * 0.98] * n_days
        opens = [sma_base * 0.99] * n_days
        opens[-1] = 104.0
        volumes = [1_000_000.0] * n_days
        no_today_frame = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["PREMKT"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=no_today_frame), \
             patch("bot.scanner.scanner.resolve_today_price", return_value=None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert result == [], (
            "When no intraday price is available (resolve_today_price returns None), "
            "the symbol must be skipped fail-closed (CR-02 / symbol_skipped_no_intraday_price)"
        )

    def test_gap_uses_scan_date_row_not_last_positional(self, tmp_state_db):
        """When the frame contains BOTH the scan_date row and one extra later row,
        gap/D1/D3 must be computed against the scan_date row, not the positional last.

        Frame layout: ... prior(2026-06-22), today(2026-06-23, gap 4%),
        future(2026-06-24, gap 0%). The positional iloc[-1] is the 0%-gap future row,
        which would fail D3. The scan_date row has a 4% gap and must pass. A correct
        implementation persists gap_pct ~4.0 from the 2026-06-23 row.
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        n_days = 220
        dates = pd.date_range(end=pd.Timestamp("2026-06-24"), periods=n_days, freq="B")
        sma_base = 90.0
        closes = [sma_base] * n_days
        highs = [sma_base * 1.02] * n_days
        lows = [sma_base * 0.98] * n_days
        opens = [sma_base * 0.99] * n_days
        volumes = [1_000_000.0] * n_days

        # index positions: -3 = 2026-06-22 (prior), -2 = 2026-06-23 (today), -1 = 2026-06-24 (future)
        closes[-3] = 100.0       # prior close (D2/D3 baseline)
        highs[-3] = 105.0        # prior high (D1 baseline)
        opens[-2] = 104.0        # today open → gap (104-100)/100 = 4% (passes D3)
        closes[-2] = 106.0       # today close (above prior high 105 → D1 passes)
        highs[-2] = 107.0
        # future row (positional last) has a 0% gap and would FAIL D3 if used as "today"
        opens[-1] = 106.0
        closes[-1] = 106.0
        highs[-1] = 107.0

        frame = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

        store = StateStore()
        store.open()

        from bot.scanner.fetcher import TodayPrice as _TodayPrice
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["ALIGN"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   return_value=_TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        gap_row = store.conn.execute(
            "SELECT gap_pct, prior_close, prior_day_high FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.ALIGN"),
        ).fetchone()
        store.close()

        assert "US.ALIGN" in result, (
            "Symbol passing on the scan_date row must be included (gap evaluated on "
            "the correct session, not the positional last row)"
        )
        assert gap_row is not None
        assert abs(gap_row[0] - 4.0) < 1e-6, (
            f"gap_pct must be 4.0 from the 2026-06-23 row, got {gap_row[0]} "
            "(would be 0.0 if iloc[-1] future row were used)"
        )
        assert abs(gap_row[1] - 100.0) < 1e-6, "prior_close must be the 2026-06-22 close"
        assert abs(gap_row[2] - 105.0) < 1e-6, "prior_day_high must be the 2026-06-22 high"


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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: _make_frame_for(sym)), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
                 patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
                 patch("bot.scanner.scanner.get_ticker_frame", side_effect=_get_frame), \
                 patch("bot.scanner.scanner.resolve_today_price",
                       side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
        """Re-persisting the same code with a different rank/gap updates the existing row.

        Updated to use store.persist_watchlist() — _persist_watchlist was moved into
        StateStore.persist_watchlist() in plan 06.1-09 (CR-01 encapsulation).
        """
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

        store.persist_watchlist(scan_date, candidates, "premarket")

        # Re-persist with updated gap and rank
        candidates[0]["gap_pct"] = 5.5
        candidates[0]["rank"] = 2

        store.persist_watchlist(scan_date, candidates, "intraday_1")

        # Use guarded store method to read back (conn property is deprecated for external use)
        with store._lock:
            row = store._conn.execute(
                "SELECT gap_pct, rank, scan_pass FROM daily_scan WHERE scan_date=? AND code=?",
                (scan_date.isoformat(), "US.AAPL"),
            ).fetchone()
            count = store._conn.execute(
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
    # get_external_codes returns empty set → no exclusions (safe default for non-exclusion tests)
    gw.get_external_codes = AsyncMock(return_value=set())
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: _make_frame_for(sym)), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        row_count_after_daily = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=?",
            (scan_date.isoformat(),),
        ).fetchone()[0]

        # Re-scan with same symbols
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda d, s: _frame_for(s)), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
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

    def test_rescan_unsubscribes_evicted_active_code(self, tmp_state_db):
        """WR-01 regression: an active code evicted from the watchlist (its gap
        collapses and it no longer passes the re-filter) must be UNSUBSCRIBED, so
        the cumulative subscribed set never exceeds the top-20 cap across rescans.
        """
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        # KEEP passes (gap 4%); DROP fails D3 (gap 1% < 3%) so it is evicted.
        passing_frame = _make_daily_frame(
            prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )
        collapsing_frame = _make_daily_frame(
            prior_close=100.0, today_open=101.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )

        store = StateStore()
        store.open()
        gw = _make_mock_gateway()
        gw.unsubscribe = AsyncMock()

        # Both KEEP and DROP are currently active (subscribed).
        active_codes = {"US.KEEP", "US.DROP"}

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["KEEP", "DROP"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda d, s: passing_frame if s == "KEEP" else collapsing_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            result = run_intraday_rescan(
                store=store, gateway=gw, cfg=cfg,
                active_codes=active_codes, scan_date=scan_date, scan_pass="intraday_1",
            )

        store.close()

        assert "US.KEEP" in result, "Still-passing active code must remain in watchlist"
        assert "US.DROP" not in result, "Collapsed active code must be evicted from watchlist"

        # The evicted active code must be unsubscribed to free its quota slot.
        gw.unsubscribe.assert_called_once()
        unsubscribed_codes = gw.unsubscribe.call_args[0][0]
        assert "US.DROP" in unsubscribed_codes, "Evicted US.DROP must be unsubscribed (WR-01)"
        assert "US.KEEP" not in unsubscribed_codes, "Protected US.KEEP must NOT be unsubscribed"

    def test_rescan_logs_active_code_eviction(self, tmp_state_db):
        """WR-02 regression: evicting an active code that no longer passes must emit
        an explicit, auditable `active_code_evicted` warning rather than dropping it
        silently.
        """
        import bot.scanner.scanner as scanner_mod
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        passing_frame = _make_daily_frame(
            prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )
        collapsing_frame = _make_daily_frame(
            prior_close=100.0, today_open=101.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )

        store = StateStore()
        store.open()
        active_codes = {"US.KEEP", "US.DROP"}

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["KEEP", "DROP"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda d, s: passing_frame if s == "KEEP" else collapsing_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch.object(scanner_mod, "_logger", MagicMock()) as mock_logger:

            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes=active_codes, scan_date=scan_date, scan_pass="intraday_1",
            )

        store.close()

        evicted_events = [
            call for call in mock_logger.warning.call_args_list
            if call.args and call.args[0] == "active_code_evicted"
        ]
        assert len(evicted_events) == 1, (
            f"Exactly one active_code_evicted event expected, got {len(evicted_events)}"
        )
        assert evicted_events[0].kwargs.get("code") == "US.DROP", (
            "active_code_evicted must name the evicted code US.DROP"
        )

    def test_rescan_reuses_daily_download_cache(self, tmp_state_db):
        """Finding 3.2 regression: run_intraday_rescan must reuse a caller-owned,
        trading-day-keyed daily-bar cache instead of re-downloading the full
        ~500-symbol universe on every 30-min rescan.

        Two same-day rescans sharing one cache dict must call download_daily_bars
        exactly ONCE (the second call reuses cache[scan_date]). A rescan for a
        DIFFERENT scan_date must trigger a fresh download (no cross-day reuse —
        T-06.2-13).
        """
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        other_scan_date = date(2026, 6, 24)
        cfg = _make_cfg(d3_min_gap_pct=0.5)
        frame = _make_daily_frame(scan_date=scan_date)
        frame_other = _make_daily_frame(scan_date=other_scan_date)

        mock_download = MagicMock(return_value=({}, set()))

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", mock_download), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            shared_cache: dict = {}

            # First rescan for scan_date — must download.
            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes=set(), scan_date=scan_date, scan_pass="intraday_1",
                daily_bars_cache=shared_cache,
            )
            assert mock_download.call_count == 1, (
                "first rescan for a scan_date must download the daily universe"
            )

            # Second rescan, SAME scan_date, SAME cache — must reuse (no re-download).
            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes=set(), scan_date=scan_date, scan_pass="intraday_2",
                daily_bars_cache=shared_cache,
            )
            assert mock_download.call_count == 1, (
                "second same-day rescan must reuse the cached daily download, "
                f"got {mock_download.call_count} download_daily_bars calls"
            )

        # A DIFFERENT scan_date must trigger a fresh download (no cross-day reuse).
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL", "MSFT"]), \
             patch("bot.scanner.scanner.download_daily_bars", mock_download), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame_other), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes=set(), scan_date=other_scan_date, scan_pass="intraday_3",
                daily_bars_cache=shared_cache,
            )
            assert mock_download.call_count == 2, (
                "a different scan_date must re-download (no cross-day cache reuse), "
                f"got {mock_download.call_count} download_daily_bars calls"
            )

        store.close()


# ============================================================
# WR-04: scan_partial_data must be logged exactly once (by the fetcher)
# ============================================================

class TestPartialDataLoggedOnce:
    """WR-04: the scanner must NOT re-emit scan_partial_data — the fetcher is the
    single source of truth for that event."""

    def test_scanner_does_not_relog_scan_partial_data(self, tmp_state_db):
        """With a non-empty failed set, _compute_candidates must not emit a second
        scan_partial_data warning (the fetcher already logged it)."""
        import bot.scanner.scanner as scanner_mod
        from bot.scanner.scanner import _compute_candidates

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()
        frame = _make_daily_frame(scan_date=scan_date)

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=({}, {"BADSYM"})), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch.object(scanner_mod, "_logger", MagicMock()) as mock_logger:

            _compute_candidates(cfg, scan_date)

        partial_events = [
            call for call in mock_logger.warning.call_args_list
            if call.args and call.args[0] == "scan_partial_data"
        ]
        assert partial_events == [], (
            "scanner must not re-log scan_partial_data — the fetcher is the single "
            "source of truth (WR-04)"
        )


# ============================================================
# WR-06: scan entrypoints must not raise when called from a running loop
# ============================================================

class TestRunFromRunningEventLoop:
    """WR-06: the sync scan entrypoints must bridge to async gateway methods even
    when invoked from within an already-running event loop (Phase 4/5 schedulers)."""

    def test_run_daily_scan_inside_running_loop_subscribes(self, tmp_state_db):
        """run_daily_scan called from inside a running event loop must complete and
        still subscribe (no 'asyncio.run() cannot be called from a running event
        loop' RuntimeError)."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        frame = _make_daily_frame(scan_date=scan_date)
        store = StateStore()
        store.open()
        gw = _make_mock_gateway()

        async def _driver():
            # We are now inside a running event loop.
            with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
                 patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
                 patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
                 patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
                 patch("bot.scanner.scanner.resolve_today_price",
                       side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
                 patch("bot.scanner.scanner.is_trading_day", return_value=True):
                return run_daily_scan(store=store, gateway=gw, cfg=_make_cfg(), scan_date=scan_date)

        result = asyncio.run(_driver())
        store.close()

        assert "US.AAPL" in result, "Scan must complete from within a running loop"
        gw.subscribe.assert_called_once()
        assert set(gw.subscribe.call_args[0][0]) == set(result), (
            "subscribe must still receive the watchlist when bridged from a running loop"
        )


# ============================================================
# Task 1 (02-05): _evaluate_symbol gains today_price parameter
# ============================================================

class TestEvaluateSymbolTodayPrice:
    """Task 1 (02-05): _evaluate_symbol sources today's numbers from injected TodayPrice."""

    def _make_daily_prior_only(
        self,
        n_days: int = 220,
        prior_close: float = 100.0,
        prior_high: float = 105.0,
        scan_date: date = None,
    ) -> pd.DataFrame:
        """Build a daily frame with n_days rows, where ALL rows are dated < scan_date.

        Unlike _make_daily_frame, this does NOT include a row for scan_date itself —
        mirroring the live premarket case where yfinance has not yet published the
        daily bar for today.
        """
        if scan_date is None:
            scan_date = date(2026, 6, 23)
        # End the date range at the business day BEFORE scan_date
        prior_day = pd.Timestamp(scan_date) - pd.tseries.offsets.BDay(1)
        dates = pd.date_range(end=prior_day, periods=n_days, freq="B")

        sma_base = prior_close * 0.90   # ensures SMA200 < prior_close (D2 passes)
        closes = [sma_base] * n_days
        closes[-1] = prior_close        # last row is the prior trading day

        highs = [sma_base * 1.02] * n_days
        highs[-1] = prior_high

        lows = [sma_base * 0.98] * n_days
        opens = [sma_base * 0.99] * n_days
        volumes = [1_000_000.0] * n_days

        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

    def test_evaluate_symbol_gap_up_emits_candidate(self):
        """Injected TodayPrice(today_open=104, today_price=106, today_high=107)
        against prior_close=100 yields a candidate with gap_pct == 4.0.
        """
        from bot.scanner.scanner import _evaluate_symbol
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        frame = self._make_daily_prior_only(
            n_days=220, prior_close=100.0, prior_high=105.0, scan_date=scan_date
        )

        today_price = TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)

        # Patch get_ticker_frame so _evaluate_symbol receives our daily frame
        with patch("bot.scanner.scanner.get_ticker_frame", return_value=frame):
            result = _evaluate_symbol("AAPL", {}, cfg, scan_date, today_price)

        assert result is not None, "Gap-up symbol with D1/D2/D3 passing must return a candidate"
        assert abs(result["gap_pct"] - 4.0) < 1e-6, (
            f"gap_pct must be (104-100)/100*100 = 4.0, got {result['gap_pct']}"
        )
        assert "code" in result
        assert "prior_day_high" in result
        assert "prior_close" in result
        assert "sma200" in result
        assert "rvol_baseline" in result
        assert abs(result["prior_close"] - 100.0) < 1e-6

    def test_evaluate_symbol_below_d3_skipped(self):
        """TodayPrice(today_open=102, ...) → gap 2.0% < cfg.d3_min_gap_pct=3.0 → returns None."""
        from bot.scanner.scanner import _evaluate_symbol
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        frame = self._make_daily_prior_only(
            n_days=220, prior_close=100.0, prior_high=105.0, scan_date=scan_date
        )

        # gap = (102 - 100) / 100 * 100 = 2.0% < 3.0% threshold
        today_price = TodayPrice(today_open=102.0, today_price=106.0, today_high=107.0)

        with patch("bot.scanner.scanner.get_ticker_frame", return_value=frame):
            result = _evaluate_symbol("AAPL", {}, cfg, scan_date, today_price)

        assert result is None, (
            "Symbol with gap below D3 threshold must be excluded (D3 filter)"
        )

    def test_evaluate_symbol_none_today_price_skips_fail_closed(self):
        """today_price=None → returns None AND logs symbol_skipped_no_intraday_price."""
        from bot.scanner.scanner import _evaluate_symbol

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        import bot.scanner.scanner as scanner_mod
        with patch.object(scanner_mod, "_logger", MagicMock()) as mock_logger:
            result = _evaluate_symbol("AAPL", {}, cfg, scan_date, None)

        assert result is None, "None today_price must return None (fail-closed)"
        # Must log symbol_skipped_no_intraday_price (not symbol_skipped_no_today_bar)
        warning_events = [
            call.args[0] for call in mock_logger.warning.call_args_list
        ]
        assert "symbol_skipped_no_intraday_price" in warning_events, (
            f"Must log symbol_skipped_no_intraday_price; got: {warning_events}"
        )
        assert "symbol_skipped_no_today_bar" not in warning_events, (
            "Must NOT log the old symbol_skipped_no_today_bar event (it is gone)"
        )

    def test_evaluate_symbol_skips_on_stale_prior_row(self):
        """Finding 2.6 regression: a prior_row dated > 5 days before scan_date
        (e.g. week-old yfinance data) must skip the symbol rather than compute
        gap_pct off a stale prior_close. A fresh prior row (<=5 days) still
        evaluates normally.
        """
        from bot.scanner.scanner import _evaluate_symbol
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        today_price = TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)

        # Stale: shift the entire frame back 10 calendar days so the most-recent
        # prior row is > 5 days before scan_date.
        stale_frame = self._make_daily_prior_only(
            n_days=220, prior_close=100.0, prior_high=105.0, scan_date=scan_date,
        )
        stale_frame.index = stale_frame.index - pd.Timedelta(days=10)

        import bot.scanner.scanner as scanner_mod
        with patch("bot.scanner.scanner.get_ticker_frame", return_value=stale_frame), \
             patch.object(scanner_mod, "_logger", MagicMock()) as mock_logger:
            result = _evaluate_symbol("AAPL", {}, cfg, scan_date, today_price)

        assert result is None, "Stale prior_row (>5 days before scan_date) must skip the symbol"
        warning_events = [call.args[0] for call in mock_logger.warning.call_args_list]
        assert "scanner_skipped_stale_prior_row" in warning_events, (
            f"Must log scanner_skipped_stale_prior_row; got: {warning_events}"
        )

        # Fresh: unmodified frame (last row 1 business day before scan_date) evaluates normally.
        fresh_frame = self._make_daily_prior_only(
            n_days=220, prior_close=100.0, prior_high=105.0, scan_date=scan_date,
        )
        with patch("bot.scanner.scanner.get_ticker_frame", return_value=fresh_frame):
            result_fresh = _evaluate_symbol("AAPL", {}, cfg, scan_date, today_price)
        assert result_fresh is not None, "A fresh prior row (<=5 days) must evaluate normally"

    def test_evaluate_symbol_no_look_ahead_preserved(self):
        """SMA200 and rvol_baseline use only rows with date < scan_date.

        Build a daily frame that INCLUDES a row dated == scan_date with an anomalous
        close/volume. The injected TodayPrice drives gap/D1; the daily row dated
        scan_date must NOT enter SMA/RVOL computation.
        """
        from bot.scanner.scanner import _evaluate_symbol
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(rvol_lookback_days=14)

        # Build prior rows (220 sessions before scan_date)
        prior_close = 100.0
        sma_base = prior_close * 0.90
        prior_day = pd.Timestamp(scan_date) - pd.tseries.offsets.BDay(1)
        dates_prior = pd.date_range(end=prior_day, periods=220, freq="B")

        closes_prior = [sma_base] * 220
        closes_prior[-1] = prior_close

        highs_prior = [sma_base * 1.02] * 220
        highs_prior[-1] = 105.0

        lows_prior = [sma_base * 0.98] * 220
        opens_prior = [sma_base * 0.99] * 220
        volumes_prior = [1_000_000.0] * 220

        frame_prior = pd.DataFrame(
            {
                "open": opens_prior,
                "high": highs_prior,
                "low": lows_prior,
                "close": closes_prior,
                "volume": volumes_prior,
            },
            index=dates_prior,
        )

        # Add misleading scan_date row (huge close + huge volume that would inflate SMA/RVOL)
        scan_ts_date = pd.Timestamp(scan_date)
        misleading_row = pd.DataFrame(
            {
                "open": [999.0],
                "high": [999.0],
                "low": [999.0],
                "close": [999.0],
                "volume": [999_000_000.0],  # 999x normal — would inflate RVOL if included
            },
            index=[scan_ts_date],
        )
        frame_with_today = pd.concat([frame_prior, misleading_row])

        today_price = TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)

        with patch("bot.scanner.scanner.get_ticker_frame", return_value=frame_with_today):
            result = _evaluate_symbol("AAPL", {}, cfg, scan_date, today_price)

        assert result is not None, "Symbol must pass with injected TodayPrice"
        # RVOL baseline must use only the 14 prior rows (vol=1_000_000); scan_date row excluded
        assert abs(result["rvol_baseline"] - 1_000_000.0) < 1.0, (
            f"rvol_baseline={result['rvol_baseline']} must equal 1_000_000 "
            "(scan_date row with 999M volume must be excluded by date < scan_date mask)"
        )
        # SMA200 must be computed from prior closes only (sma_base ≈ 90.0)
        assert result["sma200"] is not None
        assert result["sma200"] < 95.0, (
            f"sma200={result['sma200']} must be near 90 (prior closes), not 999 "
            "(scan_date close must be excluded from SMA)"
        )


# ============================================================
# Task 2 (02-05): _compute_candidates batches download_intraday_1m
# ============================================================

def _today_price_from_frame(frame: pd.DataFrame):
    """Helper: build a TodayPrice from the last row of a daily frame.

    Simulates what resolve_today_price returns for a frame whose last row
    is a gap-up trading day (used to patch resolve_today_price in tests).
    Returns TodayPrice(today_open=last open, today_price=last close, today_high=last high).
    """
    from bot.scanner.fetcher import TodayPrice
    last = frame.iloc[-1]
    return TodayPrice(
        today_open=float(last["open"]),
        today_price=float(last["close"]),
        today_high=float(last["high"]),
    )


class TestComputeCandidates1mBatch:
    """Task 2 (02-05): _compute_candidates batches download_intraday_1m once per scan."""

    def test_compute_candidates_resolves_today_price_per_symbol(self, tmp_state_db):
        """PASS gets a gap-up TodayPrice; FAIL gets None → only PASS passes, FAIL is skipped.

        Strategy: patch resolve_today_price with a side_effect keyed by the SYMBOL
        (order-of-call tracking). PASS is first in the universe → resolve call 0 returns
        a gap-up TodayPrice. FAIL is second → resolve call 1 returns None.
        The _evaluate_symbol fail-closed guard then skips FAIL.
        """
        from bot.scanner.scanner import _compute_candidates
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        pass_frame = _make_daily_frame(
            n_days=220, prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )
        fail_frame = _make_daily_frame(
            n_days=220, prior_close=100.0, today_open=104.0, today_close=106.0,
            prior_high=105.0, scan_date=scan_date,
        )

        pass_today = TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)

        # resolve_today_price is called once per symbol in order (PASS first, FAIL second)
        resolve_returns = [pass_today, None]

        def _resolve_ordered(frame_1m, now_et_val):
            return resolve_returns.pop(0) if resolve_returns else None

        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
        fixed_now_et = _dt.datetime(2026, 6, 23, 8, 30, tzinfo=_ET)

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["PASS", "FAIL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=({"sentinel": True}, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=lambda data, sym: pass_frame if sym == "PASS" else fail_frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=_resolve_ordered), \
             patch("bot.scanner.scanner.now_et", return_value=fixed_now_et):

            candidates = _compute_candidates(cfg, scan_date)

        assert len(candidates) == 1, (
            f"Only PASS (with gap-up TodayPrice) must be a candidate; got {len(candidates)}"
        )
        assert candidates[0]["code"] == "US.PASS"

    def test_compute_candidates_whole_universe_degradation(self, tmp_state_db):
        """When download_intraday_1m yields nothing for the entire universe, ScanDegradationError is raised."""
        from bot.scanner.scanner import _compute_candidates
        from bot.scanner.fetcher import ScanDegradationError

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
        fixed_now_et = _dt.datetime(2026, 6, 23, 8, 30, tzinfo=_ET)

        # Whole universe fails: empty data + all symbols in failed set
        symbols = ["AAPL", "MSFT"]
        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=symbols), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=({}, set(symbols))), \
             patch("bot.scanner.scanner.now_et", return_value=fixed_now_et):

            with pytest.raises(ScanDegradationError):
                _compute_candidates(cfg, scan_date)

    def test_intraday_fetched_once_per_scan(self, tmp_state_db):
        """download_intraday_1m must be called exactly ONCE per _compute_candidates invocation."""
        from bot.scanner.scanner import _compute_candidates
        from bot.scanner.fetcher import TodayPrice

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=0.5)
        frame = _make_daily_frame(scan_date=scan_date)

        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
        fixed_now_et = _dt.datetime(2026, 6, 23, 8, 30, tzinfo=_ET)

        pass_today = TodayPrice(today_open=104.0, today_price=106.0, today_high=107.0)

        with patch("bot.scanner.scanner.fetch_sp500_symbols",
                   return_value=["AAPL", "MSFT", "GOOG"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=({"sentinel": True}, set())) as mock_1m, \
             patch("bot.scanner.scanner.get_ticker_frame", return_value=frame), \
             patch("bot.scanner.scanner.resolve_today_price", return_value=pass_today), \
             patch("bot.scanner.scanner.now_et", return_value=fixed_now_et):

            _compute_candidates(cfg, scan_date)

        mock_1m.assert_called_once(), (
            f"download_intraday_1m must be called exactly once per scan, "
            f"got {mock_1m.call_count} calls"
        )


# ============================================================
# Task 3 (02-05): Premarket non-empty-watchlist regression test
# Regression lock for the live UAT bug: premarket scan + gap-up 1m data
# → NON-EMPTY watchlist even when no daily today-bar has published.
# ============================================================

class TestPremarketNonEmptyWatchlist:
    """Task 3 (02-05): Live UAT bug regression — premarket scan with gap-up 1m data
    must yield a non-empty watchlist even when the daily today-bar has not yet published."""

    def _make_prior_only_frame(
        self,
        scan_date: date,
        n_days: int = 220,
        prior_close: float = 100.0,
        prior_high: float = 105.0,
    ) -> pd.DataFrame:
        """Daily frame with NO row for scan_date (all rows are dated < scan_date).

        This mirrors the live premarket condition: yfinance has not yet published
        the current day's daily bar (published ~11:00 ET; premarket scan at 08:30 ET).
        """
        prior_day = pd.Timestamp(scan_date) - pd.tseries.offsets.BDay(1)
        dates = pd.date_range(end=prior_day, periods=n_days, freq="B")
        sma_base = prior_close * 0.90
        closes = [sma_base] * n_days
        closes[-1] = prior_close        # last prior session close
        highs = [sma_base * 1.02] * n_days
        highs[-1] = prior_high
        lows = [sma_base * 0.98] * n_days
        opens = [sma_base * 0.99] * n_days
        volumes = [1_000_000.0] * n_days
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

    def _make_premarket_1m_frame(
        self,
        scan_date: date,
        latest_price: float = 104.0,
        high: float = 105.0,
    ) -> pd.DataFrame:
        """Build a realistic tz-aware premarket 1m DataFrame for scan_date.

        Returns a DataFrame with bars from ~04:00 to ~08:30 ET on scan_date,
        all with tz-aware index (America/New_York), as yfinance intraday returns.
        """
        from zoneinfo import ZoneInfo
        _ET_TZ = ZoneInfo("America/New_York")

        # Build 1m bars from 04:00 to 08:29 ET (premarket window)
        start_et = _dt.datetime(scan_date.year, scan_date.month, scan_date.day, 4, 0, tzinfo=_ET_TZ)
        end_et = _dt.datetime(scan_date.year, scan_date.month, scan_date.day, 8, 30, tzinfo=_ET_TZ)
        idx = pd.date_range(start=start_et, end=end_et, freq="1min")

        n = len(idx)
        closes = [latest_price] * n
        opens_ = [latest_price * 0.999] * n
        highs_ = [high] * n
        lows_ = [latest_price * 0.998] * n
        volumes = [50_000.0] * n

        return pd.DataFrame(
            {"open": opens_, "high": highs_, "low": lows_, "close": closes, "volume": volumes},
            index=idx,
        )

    def test_premarket_scan_yields_nonempty_watchlist(self, tmp_state_db):
        """THE regression test for the 2026-06-26 live UAT bug.

        Simulate a premarket scan at 08:30 ET:
        - Daily frame has NO row for scan_date (daily bar not yet published by yfinance).
        - 1m intraday data IS available with a gap-up price (premarket bars present).
        - resolve_today_price (premarket branch) returns today_price from latest 1m close.

        Expected: run_daily_scan returns a NON-EMPTY watchlist (>= 1 code) and
        persist_watchlist stores >= 1 row.

        This test FAILS on the pre-fix path (which skipped every symbol with
        no_today_bar) and PASSES after the 02-05 fix.
        """
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        # Daily frame WITHOUT today's bar (pre-fix failure condition)
        prior_frame = self._make_prior_only_frame(
            scan_date=scan_date, n_days=220,
            prior_close=100.0, prior_high=105.0,
        )

        # Premarket 1m frame: gap-up (today_price = 106.0, prior_close = 100.0 → gap 6%)
        # today_open == today_price in premarket branch == 106.0 (latest premarket 1m close)
        # prior_high = 105.0 → D1: today_price (106) > prior_high (105) ✓
        # D3: gap_pct (6%) >= d3_min_gap_pct (3.0%) ✓
        premarket_1m = self._make_premarket_1m_frame(
            scan_date=scan_date, latest_price=106.0, high=107.0
        )

        # Fixed premarket clock: 08:30 ET (well before 09:30 RTH open)
        premarket_now_et = _PREMARKET_ET

        # Differentiate daily vs intraday data sources via data-type sentinels.
        # _compute_candidates calls get_ticker_frame(daily, sym) for SMA/RVOL
        # and get_ticker_frame(intraday, sym) for resolve_today_price.
        daily_sentinel = {"_type": "daily"}
        intraday_sentinel = {"_type": "intraday", "AAPL": premarket_1m}

        def _gtf_by_data(data, sym):
            if isinstance(data, dict) and data.get("_type") == "intraday":
                return premarket_1m  # 1m frame → resolve_today_price (real resolver)
            return prior_frame      # daily frame → _evaluate_symbol SMA/RVOL

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=(daily_sentinel, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=(intraday_sentinel, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=_gtf_by_data), \
             patch("bot.scanner.scanner.now_et", return_value=premarket_now_et), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):

            # REAL resolve_today_price runs — no patch. This is the end-to-end
            # integration test that proves the fetcher→scanner path works correctly
            # at premarket time with gap-up 1m data.
            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        # Verify persisted row count
        row_count = store.conn.execute(
            "SELECT COUNT(*) FROM daily_scan WHERE scan_date=?",
            (scan_date.isoformat(),),
        ).fetchone()[0]
        store.close()

        assert len(result) >= 1, (
            f"Premarket scan with gap-up 1m data must return a NON-EMPTY watchlist; "
            f"got {len(result)} codes. This is the live UAT bug regression lock."
        )
        assert "US.AAPL" in result, (
            "AAPL with 6% gap-up 1m price must be in the premarket watchlist"
        )
        assert row_count >= 1, (
            f"At least 1 row must be persisted in daily_scan; got {row_count}"
        )

    def test_premarket_scan_real_resolver_end_to_end(self, tmp_state_db):
        """End-to-end integration: real resolve_today_price runs against a tz-aware 1m frame.

        No resolve_today_price patch — proves the fetcher→scanner integration is correct
        and that the premarket branch (now_et < 09:30 ET) resolves today_open == today_price
        == latest premarket 1m close, which gives a gap-up result against prior_close=100.
        """
        from bot.scanner.scanner import run_daily_scan
        from bot.scanner.fetcher import resolve_today_price as _real_resolve

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=3.0)

        prior_frame = self._make_prior_only_frame(
            scan_date=scan_date, n_days=220,
            prior_close=100.0, prior_high=105.0,
        )
        # latest_price=106: today_price (106) > prior_high (105) → D1 ✓
        # gap_pct = (106-100)/100*100 = 6.0% ≥ d3_min_gap_pct (3.0%) → D3 ✓
        premarket_1m = self._make_premarket_1m_frame(
            scan_date=scan_date, latest_price=106.0, high=107.0
        )

        premarket_now_et = _PREMARKET_ET

        daily_sentinel = {"_type": "daily"}
        intraday_sentinel = {"_type": "intraday", "AAPL": premarket_1m}

        def _gtf_by_data(data, sym):
            if isinstance(data, dict) and data.get("_type") == "intraday":
                return premarket_1m
            return prior_frame

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=(daily_sentinel, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=(intraday_sentinel, set())), \
             patch("bot.scanner.scanner.get_ticker_frame",
                   side_effect=_gtf_by_data), \
             patch("bot.scanner.scanner.now_et", return_value=premarket_now_et), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            # No resolve_today_price patch — the REAL resolver runs
            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        gap_row = store.conn.execute(
            "SELECT gap_pct FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()
        store.close()

        assert "US.AAPL" in result, (
            "End-to-end premarket scan must produce a non-empty watchlist with real resolver"
        )
        assert gap_row is not None
        # gap = (106 - 100) / 100 * 100 = 6.0% (premarket: today_open = latest 1m close = 106)
        assert abs(gap_row[0] - 6.0) < 0.1, (
            f"gap_pct={gap_row[0]} must be ~6.0 from premarket 1m price 106 vs prior_close 100"
        )


# ============================================================
# Scan-time external code exclusion (260702-ick Task 2)
# ============================================================

class TestExternalCodeExclusion:
    """Scan-time exclusion of external (manually held) codes before the top-20 cap.

    Both run_daily_scan and run_intraday_rescan must call _exclude_external_codes
    after _compute_candidates and before sorting/capping. Tests patch
    _compute_candidates to return a known list and provide a mock gateway.
    """

    def _make_candidates(self, codes):
        """Build minimal candidate dicts for the given moomoo codes."""
        return [{"code": c, "gap_pct": 5.0 + i, "rank": 0} for i, c in enumerate(codes)]

    def test_daily_scan_drops_external_code(self, tmp_state_db):
        """run_daily_scan: external code removed from watchlist, bot code retained."""
        from bot.scanner.scanner import run_daily_scan
        from bot.gateway.gateway import GatewayError

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        candidates = self._make_candidates(["US.NVDA", "US.AAPL"])
        # NVDA is external (manual holding), AAPL is bot-owned
        gw = MagicMock()
        gw.get_external_codes = AsyncMock(return_value={"US.NVDA"})
        gw.subscribe = AsyncMock()

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner._compute_candidates", return_value=candidates), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner.now_et", return_value=_dt.datetime(
                 2026, 6, 23, 8, 30, tzinfo=ZoneInfo("America/New_York"))):
            result = run_daily_scan(store=store, gateway=gw, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.NVDA" not in result, (
            "External (manually held) code must be dropped before the top-20 cap"
        )
        assert "US.AAPL" in result, (
            "Bot-owned code must be retained in the watchlist"
        )

    def test_daily_scan_gateway_error_keeps_full_list(self, tmp_state_db):
        """run_daily_scan: GatewayError → watchlist intact, no exclusion applied."""
        from bot.scanner.scanner import run_daily_scan
        from bot.gateway.gateway import GatewayError

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        candidates = self._make_candidates(["US.NVDA", "US.AAPL"])
        gw = MagicMock()
        gw.get_external_codes = AsyncMock(side_effect=GatewayError("broker offline"))
        gw.subscribe = AsyncMock()

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner._compute_candidates", return_value=candidates), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner.now_et", return_value=_dt.datetime(
                 2026, 6, 23, 8, 30, tzinfo=ZoneInfo("America/New_York"))):
            result = run_daily_scan(store=store, gateway=gw, cfg=cfg, scan_date=scan_date)

        store.close()

        # Fail-open: entire list preserved
        assert "US.NVDA" in result, (
            "On GatewayError, scan must proceed with the full candidate list (fail-open)"
        )
        assert "US.AAPL" in result, (
            "On GatewayError, bot-owned code must also be preserved"
        )

    def test_daily_scan_gateway_none_noop(self, tmp_state_db):
        """run_daily_scan with gateway=None: candidate list unchanged, no broker call."""
        from bot.scanner.scanner import run_daily_scan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        candidates = self._make_candidates(["US.NVDA", "US.AAPL"])

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner._compute_candidates", return_value=candidates), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner.now_et", return_value=_dt.datetime(
                 2026, 6, 23, 8, 30, tzinfo=ZoneInfo("America/New_York"))):
            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.NVDA" in result, "gateway=None must not exclude any code"
        assert "US.AAPL" in result, "gateway=None must not exclude any code"

    def test_rescan_drops_external_code(self, tmp_state_db):
        """run_intraday_rescan: external code removed, bot code retained."""
        from bot.scanner.scanner import run_intraday_rescan
        from bot.gateway.gateway import GatewayError

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        candidates = self._make_candidates(["US.NVDA", "US.AAPL"])
        gw = MagicMock()
        gw.get_external_codes = AsyncMock(return_value={"US.NVDA"})
        gw.subscribe = AsyncMock()
        gw.unsubscribe = AsyncMock()

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner._compute_candidates", return_value=candidates), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner.now_et", return_value=_dt.datetime(
                 2026, 6, 23, 10, 30, tzinfo=ZoneInfo("America/New_York"))):
            result = run_intraday_rescan(
                store=store, gateway=gw, cfg=cfg, active_codes=set(), scan_date=scan_date
            )

        store.close()

        assert "US.NVDA" not in result, (
            "run_intraday_rescan: external code must be dropped before the cap"
        )
        assert "US.AAPL" in result, (
            "run_intraday_rescan: bot-owned code must be retained"
        )

    def test_rescan_gateway_error_keeps_full_list(self, tmp_state_db):
        """run_intraday_rescan: GatewayError → watchlist intact."""
        from bot.scanner.scanner import run_intraday_rescan
        from bot.gateway.gateway import GatewayError

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg()

        candidates = self._make_candidates(["US.NVDA", "US.AAPL"])
        gw = MagicMock()
        gw.get_external_codes = AsyncMock(side_effect=GatewayError("broker offline"))
        gw.subscribe = AsyncMock()
        gw.unsubscribe = AsyncMock()

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner._compute_candidates", return_value=candidates), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner.now_et", return_value=_dt.datetime(
                 2026, 6, 23, 10, 30, tzinfo=ZoneInfo("America/New_York"))):
            result = run_intraday_rescan(
                store=store, gateway=gw, cfg=cfg, active_codes=set(), scan_date=scan_date
            )

        store.close()

        assert "US.NVDA" in result, (
            "run_intraday_rescan: on GatewayError must proceed with full list (fail-open)"
        )
        assert "US.AAPL" in result


# ============================================================
# Phase 7 SIG-RVOL-TOD: TOD baseline computation (Plan 07-02)
# ============================================================

def _make_5m_frame(sessions: list, volume_per_bar: float = 100_000.0) -> "pd.DataFrame":
    """Build a synthetic 5m OHLCV DataFrame with ET-timezone-aware DatetimeIndex.

    sessions: list of date objects, one per trading session.
    Each session gets 13 regular-session 5m bars (09:30–16:00 ET: 13 bars per session).
    volume_per_bar: uniform Volume value per bar.

    Returns DataFrame with ET-aware DatetimeIndex and 'Volume' column (capital V,
    matching yfinance output).
    """
    from zoneinfo import ZoneInfo
    import datetime as _dt
    _ET = ZoneInfo("America/New_York")
    rows = []
    # 13 bars: 09:30, 09:35, 09:40, ..., 10:30 ET (one session subset for simplicity)
    bar_times = [
        _dt.time(9, 30), _dt.time(9, 35), _dt.time(9, 40), _dt.time(9, 45),
        _dt.time(9, 50), _dt.time(9, 55), _dt.time(10, 0), _dt.time(10, 5),
        _dt.time(10, 10), _dt.time(10, 15), _dt.time(10, 20), _dt.time(10, 25),
        _dt.time(10, 30),
    ]
    for sess_date in sessions:
        for t in bar_times:
            dt_et = _dt.datetime.combine(sess_date, t, tzinfo=_ET)
            rows.append({"timestamp": dt_et, "Volume": volume_per_bar,
                         "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5})
    df = pd.DataFrame(rows)
    df = df.set_index("timestamp")
    return df


class TestComputeTodBaselines:
    """Unit tests for scanner._compute_tod_baselines helper (Phase 7 SIG-RVOL-TOD).

    Tests the pure function in isolation — no store, no network.
    """

    def test_baseline_dict_structure_and_values(self):
        """_compute_tod_baselines returns {"HH:MM": float} dict with correct cumulative means.

        Setup: 3 sessions, each with 4 bars at 09:30, 09:35, 09:40, 09:45 ET.
        Volume per bar: 100_000. Cumulative volumes per session:
          09:30 → 100_000 (cumsum after first bar)
          09:35 → 200_000
          09:40 → 300_000
          09:45 → 400_000
        Expected baselines (mean over 3 identical sessions): same values.
        """
        from bot.scanner.scanner import _compute_tod_baselines
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        sessions = [
            date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3),
        ]
        bar_times = [_dt.time(9, 30), _dt.time(9, 35), _dt.time(9, 40), _dt.time(9, 45)]
        rows = []
        for sess in sessions:
            for t in bar_times:
                dt_et = _dt.datetime.combine(sess, t, tzinfo=_ET)
                rows.append({"Volume": 100_000.0, "Open": 100.0, "High": 101.0,
                             "Low": 99.0, "Close": 100.5})
                # Store as index
        idx = []
        for sess in sessions:
            for t in bar_times:
                idx.append(_dt.datetime.combine(sess, t, tzinfo=_ET))

        df = pd.DataFrame(rows, index=idx)
        df.index.name = "timestamp"

        baselines = _compute_tod_baselines(df, lookback_days=14)

        assert isinstance(baselines, dict), "Must return a dict"
        assert "09:30" in baselines, "Must have '09:30' bucket"
        assert "09:45" in baselines, "Must have '09:45' bucket"
        # First bucket: cumsum after bar 1 = 100_000 (mean over 3 identical sessions)
        assert abs(baselines["09:30"] - 100_000.0) < 1.0, (
            f"09:30 baseline must be ~100_000; got {baselines['09:30']}"
        )
        # Last bucket: cumsum after bar 4 = 400_000
        assert abs(baselines["09:45"] - 400_000.0) < 1.0, (
            f"09:45 baseline must be ~400_000; got {baselines['09:45']}"
        )

    def test_lookback_days_limits_sessions(self):
        """_compute_tod_baselines uses only the most recent lookback_days sessions.

        Setup: 5 sessions available. lookback_days=2. Only the 2 most recent sessions
        should be averaged. The oldest 3 sessions have volume 50_000/bar; the 2 newest
        have volume 200_000/bar. Mean of 2 newest ≠ mean of all 5.
        """
        from bot.scanner.scanner import _compute_tod_baselines
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        # 5 sessions: first 3 have volume=50k, last 2 have volume=200k
        sessions_old = [date(2026, 6, 28), date(2026, 6, 29), date(2026, 6, 30)]
        sessions_new = [date(2026, 7, 1), date(2026, 7, 2)]
        bar_time = _dt.time(9, 30)

        rows = []
        idx = []
        for sess in sessions_old:
            dt_et = _dt.datetime.combine(sess, bar_time, tzinfo=_ET)
            idx.append(dt_et)
            rows.append({"Volume": 50_000.0, "Open": 100.0, "High": 101.0,
                         "Low": 99.0, "Close": 100.5})
        for sess in sessions_new:
            dt_et = _dt.datetime.combine(sess, bar_time, tzinfo=_ET)
            idx.append(dt_et)
            rows.append({"Volume": 200_000.0, "Open": 100.0, "High": 101.0,
                         "Low": 99.0, "Close": 100.5})

        df = pd.DataFrame(rows, index=idx)

        baselines = _compute_tod_baselines(df, lookback_days=2)

        # Mean of 2 newest sessions: 200_000 each (cumsum after single bar = 200_000)
        assert abs(baselines.get("09:30", -1) - 200_000.0) < 1.0, (
            f"lookback_days=2 must average only the 2 newest sessions (200k each); "
            f"got {baselines.get('09:30')}"
        )

    def test_utc_indexed_frame_bucketed_in_et(self):
        """UTC-indexed 5m frame must be converted to ET before time-bucket assignment (Pitfall 4).

        A bar at 14:30 UTC == 10:30 ET. If we bucket by UTC the key is '14:30';
        if we bucket by ET the key is '10:30'. The baseline dict must use '10:30'.
        """
        from bot.scanner.scanner import _compute_tod_baselines
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _UTC = ZoneInfo("UTC")

        # 14:30 UTC = 10:30 ET (EDT, UTC-4 in summer)
        dt_utc = _dt.datetime(2026, 7, 1, 14, 30, tzinfo=_UTC)
        df = pd.DataFrame(
            [{"Volume": 100_000.0, "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5}],
            index=[dt_utc],
        )

        baselines = _compute_tod_baselines(df, lookback_days=14)

        assert "10:30" in baselines, (
            f"UTC 14:30 must bucket as ET '10:30' (EDT UTC-4); "
            f"got keys: {list(baselines.keys())}"
        )
        assert "14:30" not in baselines, (
            "UTC '14:30' bucket must NOT appear — index was not properly converted to ET"
        )


class TestTodBaselineIntegration:
    """Integration tests: premarket scan calls upsert_tod_baselines per candidate (Phase 7).

    Tests the full run_daily_scan path with mocked fetcher/store to verify:
      - upsert_tod_baselines is called once per passing candidate with 5m data
      - empty/missing 5m frame does NOT call upsert_tod_baselines and does not raise
    """

    def test_premarket_scan_calls_upsert_tod_baselines_for_passing_candidate(self, tmp_state_db):
        """upsert_tod_baselines is called once per passing candidate that has 5m data."""
        from bot.scanner.scanner import run_daily_scan
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        scan_date = date(2026, 7, 3)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        premarket_now_et = _dt.datetime(2026, 7, 3, 8, 30, tzinfo=_ET)

        # Build a passing AAPL frame (220 days to satisfy SMA200 + RVOL lookback)
        prior_frame = _make_daily_frame(
            n_days=220,
            prior_close=100.0,
            prior_high=105.0,
            today_open=107.0,
            today_close=108.0,
            volume_today=3_000_000.0,
            volume_prior=1_000_000.0,
            scan_date=scan_date,
        )
        # 1m premarket data that resolves to 107.0 (above prior_high 105 → D1 ✓)
        from bot.scanner.fetcher import TodayPrice as _TP

        # Build a 5m frame for 3 sessions (enough for TOD baseline)
        sessions_5m = [date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 2)]
        frame_5m = _make_5m_frame(sessions=sessions_5m, volume_per_bar=100_000.0)

        daily_sentinel = {"_type": "daily"}
        intraday_1m_sentinel = {"_type": "1m"}

        def _gtf_by_data(data, sym):
            if isinstance(data, dict) and data.get("_type") == "1m":
                return None  # force resolve_today_price to return None
            return prior_frame

        store = StateStore()
        store.open()

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=(daily_sentinel, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=(intraday_1m_sentinel, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=_gtf_by_data), \
             patch("bot.scanner.scanner.resolve_today_price",
                   return_value=_TP(today_open=107.0, today_price=107.0, today_high=108.0)), \
             patch("bot.scanner.scanner.download_intraday_5m",
                   return_value=({"AAPL": frame_5m}, set())) as mock_5m_dl, \
             patch("bot.scanner.scanner.now_et", return_value=premarket_now_et), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        # upsert_tod_baselines should have been called for US.AAPL
        tod_rows = store.conn.execute(
            "SELECT COUNT(*) FROM tod_baselines WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()[0]
        store.close()

        assert "US.AAPL" in result, "AAPL must be in watchlist (gap 7% > 3% threshold)"
        assert tod_rows > 0, (
            f"tod_baselines must have at least 1 row for US.AAPL after premarket scan; "
            f"got {tod_rows}"
        )

    def test_missing_5m_data_does_not_call_upsert_and_does_not_raise(self, tmp_state_db):
        """If 5m download returns no frame for a candidate, upsert_tod_baselines is NOT called
        and the scan does not raise (graceful degradation: SignalEngine's Gate 2
        later fails closed for this code with no tod_baseline -- the legacy
        event.volume/rvol_baseline fallback was removed, strategy-audit P1-A)."""
        from bot.scanner.scanner import run_daily_scan
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        scan_date = date(2026, 7, 3)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        premarket_now_et = _dt.datetime(2026, 7, 3, 8, 30, tzinfo=_ET)

        prior_frame = _make_daily_frame(
            n_days=220,
            prior_close=100.0,
            prior_high=105.0,
            today_open=107.0,
            today_close=108.0,
            volume_today=3_000_000.0,
            volume_prior=1_000_000.0,
            scan_date=scan_date,
        )
        from bot.scanner.fetcher import TodayPrice as _TP

        daily_sentinel = {"_type": "daily"}
        intraday_1m_sentinel = {"_type": "1m"}

        def _gtf_by_data(data, sym):
            if isinstance(data, dict) and data.get("_type") == "1m":
                return None
            return prior_frame

        store = StateStore()
        store.open()

        # Patch upsert_tod_baselines to detect if it's called
        called_codes = []
        original_upsert = store.upsert_tod_baselines

        def _track_upsert(scan_date_str, code, baselines):
            called_codes.append(code)
            original_upsert(scan_date_str, code, baselines)

        store.upsert_tod_baselines = _track_upsert

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars",
                   return_value=(daily_sentinel, set())), \
             patch("bot.scanner.scanner.download_intraday_1m",
                   return_value=(intraday_1m_sentinel, set())), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=_gtf_by_data), \
             patch("bot.scanner.scanner.resolve_today_price",
                   return_value=_TP(today_open=107.0, today_price=107.0, today_high=108.0)), \
             patch("bot.scanner.scanner.download_intraday_5m",
                   return_value=({}, set())) as mock_5m_dl, \
             patch("bot.scanner.scanner.now_et", return_value=premarket_now_et), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True):
            # Must NOT raise even though 5m data is empty
            result = run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()

        assert "US.AAPL" in result, "AAPL must still be in watchlist (graceful degradation)"
        assert "US.AAPL" not in called_codes, (
            f"upsert_tod_baselines must NOT be called when 5m frame is missing; "
            f"called for: {called_codes}"
        )


# ============================================================
# P1-A (strategy-audit): TOD baselines for intraday-rescan-added codes
#
# Only run_daily_scan ever wrote tod_baselines rows before this fix -- an
# intraday-rescan-discovered code could never fire I3 (it always fell onto the
# unit-mismatched legacy RVOL fallback in SignalEngine, since deleted). This
# extracts run_daily_scan's Step 5b into _persist_tod_baselines(exclude_today=)
# so run_intraday_rescan can call it too, for exactly the newly-subscribed codes.
# ============================================================

class TestMaskPriorSessions:
    """Unit tests for scanner._mask_prior_sessions (pure function, no store/network).

    Mirrors backtester.harness.BacktestHarness._prior_sessions_only's own
    no-look-ahead rationale: a mid-day rescan's TOD-baseline fetch must never
    include today's own partial session, or the baseline is self-referential.
    """

    def test_masks_out_rows_on_and_after_scan_date(self):
        from bot.scanner.scanner import _mask_prior_sessions

        sessions = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
        frame = _make_5m_frame(sessions=sessions, volume_per_bar=100_000.0)

        masked = _mask_prior_sessions(frame, date(2026, 7, 3))

        masked_dates = sorted({ts.date() for ts in masked.index})
        assert masked_dates == [date(2026, 7, 1), date(2026, 7, 2)], (
            "must keep only sessions strictly BEFORE scan_date -- today's own "
            "partial session must never leak into the baseline"
        )

    def test_naive_index_is_treated_as_utc_before_masking(self):
        """Same tz-handling convention as _compute_tod_baselines itself."""
        from bot.scanner.scanner import _mask_prior_sessions

        sessions = [date(2026, 7, 1), date(2026, 7, 2)]
        frame = _make_5m_frame(sessions=sessions, volume_per_bar=100_000.0)
        naive_frame = frame.tz_localize(None)

        masked = _mask_prior_sessions(naive_frame, date(2026, 7, 2))
        assert len(masked) > 0
        assert all(ts.date() < date(2026, 7, 2) for ts in masked.index.tz_localize("UTC").tz_convert("America/New_York"))


class TestPersistTodBaselines:
    """Unit tests for scanner._persist_tod_baselines (bypasses the full scan
    pipeline -- exercises just the extracted Step 5b logic)."""

    def test_empty_codes_list_short_circuits_without_download(self, tmp_state_db):
        from bot.scanner.scanner import _persist_tod_baselines

        store = StateStore()
        store.open()
        cfg = _make_cfg()

        with patch("bot.scanner.scanner.download_intraday_5m") as mock_dl:
            _persist_tod_baselines(store, cfg, [], date(2026, 7, 3), exclude_today=False)
            mock_dl.assert_not_called()
        store.close()

    def test_persists_a_baseline_row_per_code_with_5m_data(self, tmp_state_db):
        from bot.scanner.scanner import _persist_tod_baselines

        store = StateStore()
        store.open()
        cfg = _make_cfg()
        scan_date = date(2026, 7, 3)
        sessions = [date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 2)]
        frame_5m = _make_5m_frame(sessions=sessions, volume_per_bar=100_000.0)

        with patch("bot.scanner.scanner.download_intraday_5m",
                  return_value=({"AAPL": frame_5m}, set())):
            _persist_tod_baselines(store, cfg, ["US.AAPL"], scan_date, exclude_today=False)

        rows = store.conn.execute(
            "SELECT COUNT(*) FROM tod_baselines WHERE scan_date=? AND code=?",
            (scan_date.isoformat(), "US.AAPL"),
        ).fetchone()[0]
        store.close()
        assert rows > 0

    def test_exclude_today_masks_the_scan_dates_own_session_before_computing(self, tmp_state_db):
        """A rescan fetch that ALSO includes scan_date's own partial session must
        not let those rows influence the persisted baseline -- verified by
        comparing against calling with exclude_today=False on the SAME frame
        (which lets today's own bars in and must therefore differ)."""
        from bot.scanner.scanner import _persist_tod_baselines

        scan_date = date(2026, 7, 3)
        # Sessions include scan_date ITSELF with a distinctly larger volume, so an
        # unmasked run's average is measurably pulled toward it.
        sessions = [date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 2), scan_date]
        frame_5m = _make_5m_frame(sessions=sessions[:-1], volume_per_bar=100_000.0)
        today_rows = _make_5m_frame(sessions=[scan_date], volume_per_bar=10_000_000.0)
        import pandas as _pd
        combined = _pd.concat([frame_5m, today_rows])

        # Explicit distinct :memory: stores -- two bare StateStore() calls would
        # both default to this test's shared tmp_state_db path and collide.
        store_masked = StateStore(db_path=":memory:")
        store_masked.open()
        store_unmasked = StateStore(db_path=":memory:")
        store_unmasked.open()
        cfg = _make_cfg()

        with patch("bot.scanner.scanner.download_intraday_5m",
                  return_value=({"AAPL": combined}, set())):
            _persist_tod_baselines(store_masked, cfg, ["US.AAPL"], scan_date, exclude_today=True)
            _persist_tod_baselines(store_unmasked, cfg, ["US.AAPL"], scan_date, exclude_today=False)

        masked_baseline = store_masked.get_tod_baseline(scan_date.isoformat(), "US.AAPL", "09:30")
        unmasked_baseline = store_unmasked.get_tod_baseline(scan_date.isoformat(), "US.AAPL", "09:30")
        store_masked.close()
        store_unmasked.close()

        assert masked_baseline > 0 and unmasked_baseline > 0
        assert masked_baseline < unmasked_baseline, (
            "exclude_today=True must exclude scan_date's own (much larger) volume "
            "from the averaged baseline; exclude_today=False lets it skew the average up"
        )


class TestIntradayRescanTodBaselines:
    """run_intraday_rescan must persist TOD baselines for newly-subscribed codes only."""

    def test_rescan_persists_tod_baselines_only_for_newly_subscribed_codes(self, tmp_state_db):
        from bot.scanner.scanner import run_intraday_rescan

        scan_date = date(2026, 6, 23)
        cfg = _make_cfg(d3_min_gap_pct=0.5)
        frame = _make_daily_frame(scan_date=scan_date)

        store = StateStore()
        store.open()

        active_code = "US.ACTIVE"  # already subscribed -- must NOT be treated as "new"
        new_symbols = ["NEWA", "NEWB"]
        all_symbols = new_symbols + ["ACTIVE"]

        persisted_calls = []

        def _fake_persist(store_arg, cfg_arg, codes, scan_date_arg, exclude_today):
            persisted_calls.append((set(codes), exclude_today))

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=all_symbols), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   side_effect=lambda f, net: _make_today_price(f) if f is not None else None), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner._persist_tod_baselines", side_effect=_fake_persist):
            run_intraday_rescan(
                store=store, gateway=None, cfg=cfg,
                active_codes={active_code}, scan_date=scan_date, scan_pass="intraday_1",
            )

        store.close()

        assert len(persisted_calls) == 1, "must call _persist_tod_baselines exactly once"
        codes_called, exclude_today = persisted_calls[0]
        assert codes_called == {"US.NEWA", "US.NEWB"}, (
            f"must persist for exactly the newly-subscribed codes, got {codes_called}"
        )
        assert active_code not in codes_called, (
            "an already-active code must not be re-persisted (it already has a "
            "baseline from whenever it was first subscribed)"
        )
        assert exclude_today is True, (
            "a mid-day rescan fetch must mask out today's own partial session "
            "(design risk: an unmasked fetch would self-contaminate the baseline)"
        )

    def test_daily_scan_still_persists_with_exclude_today_false(self, tmp_state_db):
        """run_daily_scan runs at premarket (no RTH bars exist yet for today), so it
        must keep passing exclude_today=False -- unchanged behavior."""
        from bot.scanner.scanner import run_daily_scan
        import datetime as _dt
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        scan_date = date(2026, 7, 3)
        cfg = _make_cfg(d3_min_gap_pct=3.0)
        premarket_now_et = _dt.datetime(2026, 7, 3, 8, 30, tzinfo=_ET)
        frame = _make_daily_frame(
            n_days=220, prior_close=100.0, prior_high=105.0,
            today_open=107.0, today_close=108.0, scan_date=scan_date,
        )
        from bot.scanner.fetcher import TodayPrice as _TP

        store = StateStore()
        store.open()
        persisted_calls = []

        def _fake_persist(store_arg, cfg_arg, codes, scan_date_arg, exclude_today):
            persisted_calls.append(exclude_today)

        with patch("bot.scanner.scanner.fetch_sp500_symbols", return_value=["AAPL"]), \
             patch("bot.scanner.scanner.download_daily_bars", return_value=({}, set())), \
             patch("bot.scanner.scanner.download_intraday_1m", return_value=_INTRADAY_OK), \
             patch("bot.scanner.scanner.get_ticker_frame", side_effect=lambda d, s: frame), \
             patch("bot.scanner.scanner.resolve_today_price",
                   return_value=_TP(today_open=107.0, today_price=107.0, today_high=108.0)), \
             patch("bot.scanner.scanner.now_et", return_value=premarket_now_et), \
             patch("bot.scanner.scanner.is_trading_day", return_value=True), \
             patch("bot.scanner.scanner._persist_tod_baselines", side_effect=_fake_persist):
            run_daily_scan(store=store, gateway=None, cfg=cfg, scan_date=scan_date)

        store.close()
        assert persisted_calls == [False]
