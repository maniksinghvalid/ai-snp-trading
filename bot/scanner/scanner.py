#!/usr/bin/env python3
"""
bot.scanner.scanner — Premarket scan and watchlist persistence.

Orchestrates: universe fetch → bar download → D1/D2/D3 filter → SMA200/RVOL
baseline computation → top-20 gap-ranked cap → idempotent upsert persist.

This module delivers the premarket scan path end-to-end EXCEPT the broker
subscription (Plan 02-03) — a clearly-marked seam is left for gateway.subscribe.

All filter thresholds are config-driven (cfg.d3_min_gap_pct, cfg.min_price_usd,
cfg.rvol_lookback_days). No strategy literals are hardcoded here.

Exports: run_daily_scan, _persist_watchlist, _evaluate_symbol
"""
import sqlite3
from datetime import date
from typing import List, Optional

import pandas as pd

from bot.config.loader import StrategyConfig
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import is_trading_day
from bot.scanner.fetcher import download_daily_bars, get_ticker_frame, ScanDegradationError
from bot.scanner.universe import fetch_sp500_symbols, yfinance_to_moomoo
from bot.state.store import StateStore
from bot.strategy.indicators import sma, rvol
from bot.strategy.trend_join_long import TrendJoinLong

_logger = get_logger(__name__)

# Maximum watchlist size (SCAN-08)
_WATCHLIST_CAP = 20


# ============================================================
# Symbol evaluation
# ============================================================

def _evaluate_symbol(
    symbol: str,
    data: object,
    cfg: StrategyConfig,
    scan_date: date,
) -> Optional[dict]:
    """Evaluate a single symbol against D1/D2/D3 daily filters.

    Fetches the ticker frame via get_ticker_frame, validates sufficient history,
    computes SMA200 (from prior closes, no look-ahead), computes the RVOL
    baseline (mean of prior cfg.rvol_lookback_days sessions, date < scan_date),
    computes gap_pct, and calls TrendJoinLong(cfg).passes_daily_filters.

    symbol:    yfinance-format ticker (e.g. "AAPL")
    data:      yf.download() return value (group_by="ticker")
    cfg:       StrategyConfig — all thresholds drawn from cfg, never hardcoded
    scan_date: date — used as the no-look-ahead cutoff for RVOL baseline

    Returns a candidate dict with the moomoo code, gap_pct, prior_day_high,
    prior_close, sma200, rvol_baseline; or None if the symbol is excluded.
    """
    frame = get_ticker_frame(data, symbol)
    if frame is None:
        return None

    # Require >= 2 rows (prior + today) for basic D1/D2/D3 evaluation
    if len(frame) < 2:
        _logger.warning("symbol_skipped_insufficient_rows", symbol=symbol, rows=len(frame))
        return None

    # Require >= cfg.rvol_lookback_days prior sessions for RVOL baseline
    # "prior" = rows with date < scan_date (strictly prior, matching RVOL strict cutoff)
    # The frame index is a DatetimeIndex; compare .date() to scan_date
    scan_ts = pd.Timestamp(scan_date)
    prior_mask = frame.index < scan_ts
    n_prior = prior_mask.sum()

    if n_prior < cfg.rvol_lookback_days:
        _logger.warning(
            "symbol_skipped_insufficient_history",
            symbol=symbol,
            prior_sessions=int(n_prior),
            required=cfg.rvol_lookback_days,
        )
        return None

    # SMA200 — computed from prior closes only (exclude today's row to avoid look-ahead)
    # Prior closes = all rows with date < scan_date
    prior_closes = frame.loc[prior_mask, "close"]
    sma200_val = sma(prior_closes, 200)
    # sma returns float('nan') if insufficient data — treat as None
    if pd.isna(sma200_val):
        sma200_val = None

    # RVOL baseline — mean volume of the prior cfg.rvol_lookback_days sessions
    # This is the DENOMINATOR of the RVOL ratio (stored for Phase 3 reuse, D-08).
    # Only rows with date < scan_date are included (strict no-look-ahead cutoff).
    prior_frame = frame.loc[prior_mask].copy()
    # Sort descending by date and take the most recent rvol_lookback_days rows
    prior_sorted = prior_frame.sort_index(ascending=False).head(cfg.rvol_lookback_days)
    if len(prior_sorted) < cfg.rvol_lookback_days:
        # Already checked above, but guard here for clarity
        return None
    rvol_baseline_val = float(prior_sorted["volume"].mean())

    # Convenience references
    prior_row = frame.iloc[-2]
    today_row = frame.iloc[-1]
    prior_close_val = float(prior_row["close"])
    prior_high_val = float(prior_row["high"])
    today_open_val = float(today_row["open"])

    # Compute gap_pct for ranking (used by caller; also mirrored by D3 check inside passes_daily_filters)
    if prior_close_val == 0.0:
        return None
    gap_pct = (today_open_val - prior_close_val) / prior_close_val * 100.0

    # Apply D1/D2/D3 + universe price filter via TrendJoinLong
    strategy = TrendJoinLong(cfg)
    sma200_for_filter = sma200_val if sma200_val is not None else 0.0
    if not strategy.passes_daily_filters(symbol, frame, sma200_for_filter):
        return None

    moomoo_code = yfinance_to_moomoo(symbol)

    return {
        "code": moomoo_code,
        "gap_pct": gap_pct,
        "prior_day_high": prior_high_val,
        "prior_close": prior_close_val,
        "sma200": sma200_val,
        "rvol_baseline": rvol_baseline_val,
    }


# ============================================================
# Persistence (Task 3 — idempotent upsert, SCAN-05)
# ============================================================

def _persist_watchlist(
    conn: sqlite3.Connection,
    scan_date: date,
    candidates: list,
    scan_pass: str,
) -> None:
    """Persist a ranked candidate list to daily_scan via an idempotent upsert.

    Uses INSERT ... ON CONFLICT(scan_date, code) DO UPDATE so re-running the
    same scan day does not duplicate rows (SCAN-05 / D-05). created_at is
    intentionally excluded from the UPDATE clause — first-seen timestamp is
    preserved across re-scans.

    All values are bound via ? placeholders (T-02-06 — no SQL injection surface).

    conn:       Open sqlite3.Connection with migrations applied.
    scan_date:  The scan date (date object).
    candidates: List of candidate dicts (code, gap_pct, rank, prior_day_high,
                prior_close, sma200, rvol_baseline); rank assigned by caller.
    scan_pass:  Label for the originating scan pass (e.g. "premarket").
    """
    created_at = now_et().isoformat()
    scan_date_str = scan_date.isoformat()

    for c in candidates:
        conn.execute(
            """
            INSERT INTO daily_scan
                (scan_date, code, gap_pct, rank, created_at,
                 prior_day_high, prior_close, sma200, rvol_baseline, scan_pass)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scan_date, code) DO UPDATE SET
                gap_pct        = excluded.gap_pct,
                rank           = excluded.rank,
                prior_day_high = excluded.prior_day_high,
                prior_close    = excluded.prior_close,
                sma200         = excluded.sma200,
                rvol_baseline  = excluded.rvol_baseline,
                scan_pass      = excluded.scan_pass
            """,
            (
                scan_date_str,
                c["code"],
                c["gap_pct"],
                c["rank"],
                created_at,
                c.get("prior_day_high"),
                c.get("prior_close"),
                c.get("sma200"),
                c.get("rvol_baseline"),
                scan_pass,
            ),
        )

    conn.commit()
    _logger.info(
        "watchlist_persisted",
        count=len(candidates),
        scan_date=scan_date_str,
        scan_pass=scan_pass,
    )


# ============================================================
# Main entrypoint
# ============================================================

def run_daily_scan(
    store: StateStore,
    gateway,  # MoomooGateway — accepted now; subscribe seam wired in Plan 02-03
    cfg: StrategyConfig,
    scan_date: date = None,
    scan_pass: str = "premarket",
) -> List[str]:
    """Orchestrate the premarket daily scan.

    Sequence:
      1. Resolve scan_date (ET-correct, defaults to today in ET).
      2. Guard: skip on non-trading days (NYSE holidays / weekends).
      3. Fetch the S&P 500 symbol list via fetch_sp500_symbols.
      4. Download 1-year daily bars via download_daily_bars (propagates ScanDegradationError).
      5. For each symbol: evaluate via _evaluate_symbol (D1/D2/D3, SMA200, RVOL baseline).
      6. Sort passing candidates by gap_pct DESC; cap at top-20 (SCAN-08).
      7. Assign rank 1..N and persist via _persist_watchlist (idempotent upsert).
      8. Return list of moomoo codes (top-20 capped watchlist).

    NOTE: gateway.subscribe() is NOT called here — that seam is wired in Plan 02-03.
    # SEAM(02-03): after persistence, call asyncio.run(gateway.subscribe(result))

    store:     Open StateStore (migrations applied).
    gateway:   MoomooGateway instance (passed now; subscribe wired in 02-03).
    cfg:       StrategyConfig — all thresholds are config-driven (D-12).
    scan_date: Optional date override (defaults to now_et().date()).
    scan_pass: Label for this scan pass (e.g. "premarket", "intraday_1").

    Returns list of Moomoo-format codes (e.g. ["US.AAPL", "US.MSFT"]) for the
    top-20 (or fewer) candidates. Returns [] on non-trading days.

    Raises ScanDegradationError if >= 10% of symbols fail to download (D-06).
    """
    # Step 1: resolve scan_date (ET-correct)
    if scan_date is None:
        scan_date = now_et().date()

    # Step 2: non-trading-day guard
    if not is_trading_day(scan_date):
        _logger.info("scan_skipped_not_trading_day", scan_date=str(scan_date))
        return []

    # Step 3: fetch S&P 500 symbol list
    yf_symbols = fetch_sp500_symbols()

    # Step 4: download daily bars (propagates ScanDegradationError on >= 10% failure)
    data, failed = download_daily_bars(yf_symbols)

    if failed:
        _logger.warning(
            "scan_partial_data",
            failed_count=len(failed),
            total=len(yf_symbols),
        )

    # Step 5: evaluate each symbol
    passing = []
    for sym in yf_symbols:
        candidate = _evaluate_symbol(sym, data, cfg, scan_date)
        if candidate is not None:
            passing.append(candidate)

    # Step 6: sort by gap_pct DESC, cap at top-20
    passing.sort(key=lambda c: c["gap_pct"], reverse=True)
    top20 = passing[:_WATCHLIST_CAP]

    # Step 7: assign rank 1..N and persist
    for rank_idx, candidate in enumerate(top20, start=1):
        candidate["rank"] = rank_idx

    _persist_watchlist(store.conn, scan_date, top20, scan_pass)

    # Step 8: return moomoo codes
    result = [c["code"] for c in top20]

    _logger.info(
        "scan_complete",
        scan_date=str(scan_date),
        candidates_passing=len(passing),
        watchlist_count=len(result),
        scan_pass=scan_pass,
    )

    # SEAM(02-03): asyncio.run(gateway.subscribe(result))
    # NOT called here — wired in Plan 02-03 after subscribe() method is added to gateway.

    return result
