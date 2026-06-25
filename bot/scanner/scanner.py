#!/usr/bin/env python3
"""
bot.scanner.scanner — Premarket scan and watchlist persistence.

Orchestrates: universe fetch → bar download → D1/D2/D3 filter → SMA200/RVOL
baseline computation → top-20 gap-ranked cap → idempotent upsert persist.

All filter thresholds are config-driven (cfg.d3_min_gap_pct, cfg.min_price_usd,
cfg.rvol_lookback_days). No strategy literals are hardcoded here.

Exports: run_daily_scan, run_intraday_rescan, _evaluate_symbol
"""
import asyncio
import concurrent.futures
from datetime import date
from typing import List, Optional, Set

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

    # CR-01: An unavailable SMA200 means the D2 trend filter ("prior close > SMA200" —
    # the trend-join premise of the entire strategy) cannot be evaluated. Fail CLOSED:
    # a symbol with >= rvol_lookback_days but < 200 prior sessions must be EXCLUDED,
    # never admitted as if it were in an established uptrend. Previously sma200_val
    # was coerced to 0.0 before the filter, which made D2 (`prior_close > 0.0`)
    # always True for any real price — silently bypassing the trend filter.
    if sma200_val is None:
        _logger.warning(
            "symbol_skipped_no_sma200",
            symbol=symbol,
            prior_sessions=int(n_prior),
        )
        return None

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

    # CR-02: "today" must be the row whose date equals scan_date — NOT the positional
    # frame.iloc[-1]. During a real premarket scan (before ~09:30 ET, the actual use
    # case) yfinance has not yet produced today's daily bar, so frame.index[-1] is the
    # PRIOR trading day. Relying on iloc[-1]/iloc[-2] would then silently evaluate
    # yesterday-vs-day-before for gap/D1/D3 and fold "today" into the SMA/RVOL baseline.
    # Locate today's row explicitly by date and skip the symbol if it is absent, so the
    # scanner never ranks the wrong session.
    today_rows = frame.index[frame.index.normalize() == scan_ts.normalize()]
    if len(today_rows) == 0:
        _logger.warning(
            "symbol_skipped_no_today_bar",
            symbol=symbol,
            scan_date=str(scan_date),
        )
        return None
    today_row = frame.loc[today_rows[-1]]

    # prior_row = the most-recent session strictly before scan_date. prior_frame is the
    # date-ascending set of rows with date < scan_date (same mask used for SMA/RVOL),
    # so its last row is the immediately-prior trading day.
    prior_row = prior_frame.iloc[-1]

    prior_close_val = float(prior_row["close"])
    prior_high_val = float(prior_row["high"])
    today_open_val = float(today_row["open"])

    # Compute gap_pct for ranking (used by caller; also mirrored by D3 check inside passes_daily_filters)
    if prior_close_val == 0.0:
        return None
    gap_pct = (today_open_val - prior_close_val) / prior_close_val * 100.0

    # Apply D1/D2/D3 + universe price filter via TrendJoinLong.
    # sma200_val is guaranteed non-None here (excluded above if unavailable, CR-01).
    #
    # CR-02: pass an explicit 2-row (prior, today) frame so passes_daily_filters'
    # iloc[-2]/iloc[-1] align with the date-resolved prior/today rows above, rather
    # than the full frame whose last positional row may be the prior day in premarket.
    daily_2row = pd.DataFrame([prior_row, today_row])
    strategy = TrendJoinLong(cfg)
    if not strategy.passes_daily_filters(symbol, daily_2row, sma200_val):
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
# Shared candidate computation (factored for daily + intraday re-scan)
# ============================================================

def _compute_candidates(
    cfg: StrategyConfig,
    scan_date: date,
) -> List[dict]:
    """Fetch universe, download bars, and evaluate each symbol for D1/D2/D3 filters.

    This is the shared compute kernel used by both run_daily_scan and
    run_intraday_rescan. It does NOT persist or subscribe — callers handle that.

    cfg:       StrategyConfig — all thresholds config-driven (D-12).
    scan_date: Date to evaluate (no-look-ahead cutoff for RVOL/SMA).

    Returns a list of candidate dicts (unsorted, unranked) for all symbols
    that pass D1/D2/D3. Each dict contains: code, gap_pct, prior_day_high,
    prior_close, sma200, rvol_baseline.

    Raises ScanDegradationError if >= 10% of symbols fail to download (D-06).
    """
    # Fetch S&P 500 symbol list
    yf_symbols = fetch_sp500_symbols()

    # Download daily bars (propagates ScanDegradationError on >= 10% failure)
    # WR-04: download_daily_bars (fetcher) is the single source of truth for the
    # scan_partial_data event — it already logs it when `failed` is non-empty. Do
    # NOT re-log it here, which previously produced two identical warnings per
    # degraded scan (audit noise / double-counting risk downstream).
    data, failed = download_daily_bars(yf_symbols)

    # Evaluate each symbol
    passing = []
    for sym in yf_symbols:
        candidate = _evaluate_symbol(sym, data, cfg, scan_date)
        if candidate is not None:
            passing.append(candidate)

    return passing


# ============================================================
# Async bridge (WR-06)
# ============================================================

def _run_coro(coro):
    """Run an async coroutine from synchronous scan code, safely (WR-06).

    The scan entrypoints are synchronous but the gateway subscribe/unsubscribe
    methods are async. `asyncio.run` raises RuntimeError if called from within an
    already-running event loop (e.g. a future async scheduler in Phase 4/5), which
    would abort the scan AFTER persistence has happened — leaving a persisted
    watchlist with no subscriptions.

    This helper detects whether an event loop is already running on the current
    thread. If not, it uses asyncio.run as before. If one is running, it executes
    the coroutine to completion on a separate thread with its own loop, so the
    bridge never raises the "cannot be called from a running event loop" error.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop on this thread — the normal synchronous-scan path.
        return asyncio.run(coro)

    # A loop is already running on this thread: run the coroutine on a worker
    # thread with its own event loop to avoid the RuntimeError.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


# ============================================================
# Subscription helper
# ============================================================

def _subscribe_new_codes(gateway, codes: List[str], active_codes: Set[str]) -> List[str]:
    """Subscribe codes not already in active_codes via gateway.subscribe.

    gateway:      MoomooGateway instance (or None — skipped).
    codes:        Full list of moomoo codes to potentially subscribe.
    active_codes: Set of already-subscribed moomoo codes (D-04 protection).

    Returns list of newly-subscribed codes (codes - active_codes).
    """
    new_codes = [c for c in codes if c not in active_codes]
    if new_codes and gateway is not None:
        _run_coro(gateway.subscribe(new_codes))
    return new_codes


def _unsubscribe_evicted_codes(gateway, result: List[str], active_codes: Set[str]) -> List[str]:
    """Unsubscribe active codes that fell out of the protected watchlist (WR-01 / SIG-01).

    Without this, a code dropped from the top-20 keeps its live K_5M feed and the
    cumulative subscribed set grows across rescans, eventually exceeding the 20-slot
    cap the subscribe path is built around. The eviction set is the active codes that
    are no longer present in result.

    gateway:      MoomooGateway instance (or None — skipped).
    result:       Final protected watchlist (moomoo codes) after the rescan.
    active_codes: Set of currently-subscribed moomoo codes.

    Returns the list of evicted (unsubscribed) codes.
    """
    evicted = [c for c in active_codes if c not in set(result)]
    if evicted and gateway is not None:
        _run_coro(gateway.unsubscribe(evicted))
    return evicted


# ============================================================
# Main entrypoints
# ============================================================

def run_daily_scan(
    store: StateStore,
    gateway,  # MoomooGateway — accepts None for unit tests without broker
    cfg: StrategyConfig,
    scan_date: date = None,
    scan_pass: str = "premarket",
) -> List[str]:
    """Orchestrate the premarket daily scan.

    Sequence:
      1. Resolve scan_date (ET-correct, defaults to today in ET).
      2. Guard: skip on non-trading days (NYSE holidays / weekends).
      3. Fetch the S&P 500 symbol list + download daily bars via _compute_candidates.
      4. Sort passing candidates by gap_pct DESC; cap at top-20 (SCAN-08).
      5. Assign rank 1..N and persist via _persist_watchlist (idempotent upsert).
      6. Subscribe ONLY the capped top-20 codes via gateway.subscribe (SIG-01).
      7. Return list of moomoo codes (top-20 capped watchlist).

    store:     Open StateStore (migrations applied).
    gateway:   MoomooGateway instance; None is accepted (skips subscribe).
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

    # Step 3: compute candidates (shared with run_intraday_rescan)
    passing = _compute_candidates(cfg, scan_date)

    # Step 4: sort by gap_pct DESC, cap at top-20 (SCAN-08)
    passing.sort(key=lambda c: c["gap_pct"], reverse=True)
    top20 = passing[:_WATCHLIST_CAP]

    # Step 5: assign rank 1..N and persist (idempotent upsert)
    for rank_idx, candidate in enumerate(top20, start=1):
        candidate["rank"] = rank_idx

    store.persist_watchlist(scan_date, top20, scan_pass)

    # Step 6: return moomoo codes
    result = [c["code"] for c in top20]

    _logger.info(
        "scan_complete",
        scan_date=str(scan_date),
        candidates_passing=len(passing),
        watchlist_count=len(result),
        scan_pass=scan_pass,
    )

    # SIG-01: Subscribe ONLY the capped top-20 codes — never the full universe.
    # run_daily_scan is sync; _run_coro bridges to the async gateway.subscribe()
    # without raising even if invoked from within a running event loop (WR-06).
    if result and gateway is not None:
        _run_coro(gateway.subscribe(result))
    elif not result:
        _logger.info("subscribe_skipped_empty_watchlist", scan_date=str(scan_date))

    return result


def run_intraday_rescan(
    store: StateStore,
    gateway,  # MoomooGateway — accepts None for unit tests without broker
    cfg: StrategyConfig,
    active_codes: Set[str],
    scan_date: date = None,
    scan_pass: str = "intraday",
) -> List[str]:
    """Re-scan the universe intraday, protecting active live-feed candidates (SCAN-07).

    Implements D-04 (active candidate protection) and D-05 (idempotent upsert):
      - Re-ranks the union of newly-qualifying candidates by gap_pct DESC.
      - Active candidates that still pass filters are guaranteed in the top-20
        even if their gap ranks below position 20 (D-04).
      - Calls _persist_watchlist for idempotent upsert — never DELETEs rows (D-05).
      - Subscribes ONLY codes not already in active_codes (avoids re-subscription).

    store:        Open StateStore (migrations applied).
    gateway:      MoomooGateway instance; None is accepted (skips subscribe).
    cfg:          StrategyConfig — all thresholds config-driven.
    active_codes: Set of moomoo codes with active 5m feed (protected from eviction).
    scan_date:    Optional date override (defaults to now_et().date()).
    scan_pass:    Label for this re-scan pass (e.g. "intraday_1", "intraday_2").

    Returns list of Moomoo-format codes in the protected top-20.

    Raises ScanDegradationError if >= 10% of symbols fail to download (D-06).
    """
    # Step 1: resolve scan_date (ET-correct)
    if scan_date is None:
        scan_date = now_et().date()

    # Step 2: non-trading-day guard
    if not is_trading_day(scan_date):
        _logger.info("rescan_skipped_not_trading_day", scan_date=str(scan_date))
        return []

    # Step 3: compute candidates (shared path with run_daily_scan — no filter duplication)
    passing = _compute_candidates(cfg, scan_date)

    # Step 4: sort all passing candidates by gap_pct DESC
    passing.sort(key=lambda c: c["gap_pct"], reverse=True)

    # Step 5: build protected top-20 (D-04):
    #   a) First, add all active_codes that still qualify (they are protected).
    #   b) Then fill remaining slots (up to _WATCHLIST_CAP) with the highest-gap
    #      non-active candidates that haven't already been included.
    protected: List[dict] = []
    filled_codes: Set[str] = set()

    for candidate in passing:
        if candidate["code"] in active_codes:
            protected.append(candidate)
            filled_codes.add(candidate["code"])

    remaining_slots = _WATCHLIST_CAP - len(protected)
    for candidate in passing:
        if remaining_slots <= 0:
            break
        if candidate["code"] not in filled_codes:
            protected.append(candidate)
            filled_codes.add(candidate["code"])
            remaining_slots -= 1

    # Cap total at _WATCHLIST_CAP (active codes that exceed cap are truncated last)
    # Active codes are front-loaded, so they are preserved up to the cap.
    if len(protected) > _WATCHLIST_CAP:
        protected = protected[:_WATCHLIST_CAP]

    # Step 6: assign rank over the final protected list (by gap_pct DESC)
    protected.sort(key=lambda c: c["gap_pct"], reverse=True)
    for rank_idx, candidate in enumerate(protected, start=1):
        candidate["rank"] = rank_idx

    # Step 7: upsert (idempotent — never DELETE, D-05)
    store.persist_watchlist(scan_date, protected, scan_pass)

    result = [c["code"] for c in protected]

    # WR-02: D-04 protection only re-includes active codes that still pass the
    # re-filter. An active code whose gap collapses intraday is simply absent from
    # `passing` and silently drops out. Make that eviction explicit and auditable —
    # log each evicted active code rather than leaving the drop as an emergent
    # side effect of "active code happens to still pass".
    result_set = set(result)
    evicted_active = sorted(c for c in active_codes if c not in result_set)
    for evicted_code in evicted_active:
        _logger.warning(
            "active_code_evicted",
            code=evicted_code,
            scan_date=str(scan_date),
            scan_pass=scan_pass,
        )

    _logger.info(
        "rescan_complete",
        scan_date=str(scan_date),
        candidates_passing=len(passing),
        protected_active=len([c for c in protected if c["code"] in active_codes]),
        evicted_active=len(evicted_active),
        watchlist_count=len(result),
        scan_pass=scan_pass,
    )

    # Step 8: subscribe ONLY newly-added codes (not already in active_codes)
    _subscribe_new_codes(gateway, result, active_codes)

    # Step 9 (WR-01): unsubscribe active codes evicted from the protected watchlist,
    # so the cumulative subscribed set never exceeds the top-20 cap across rescans.
    _unsubscribe_evicted_codes(gateway, result, active_codes)

    return result
