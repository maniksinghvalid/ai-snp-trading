#!/usr/bin/env python3
"""
backtester.options.data — offline options data layer (Phase 9).

Fetches the option contracts reference + `O:` daily aggregates through the
EXISTING `backtester.massive.MassiveDataSource` (Bearer auth, 429 backoff,
`backtester/cache/massive/` cache) — no parallel HTTP client (D-05). Exposes
`OptionChainSource`, a per-decision-day chain view that structurally cannot
leak a future bar into day t's contract selection (D-07/D-08).

Decision-point convention: on day t, only a bar dated EXACTLY t is visible to
the strategy; there is no "most recent bar at or before t" fallback for entry
selection (D-07/D-08, RESEARCH Pitfall 6). Opens are derived from day-t's
close.

This module never imports the broker layer or its trading SDK — the
backtester can never construct a broker session or place an order (mirrors
`backtester/run.py`'s stated invariant).

VERIFIED 2026-08-17 (live probe against api.massive.com, today=2026-08-17,
budget: 5 requests, contract O:SPY250620C00600000 unless noted):
  - Option daily-aggregates history boundary: range 2024-05-01..2024-05-31 ->
    403 NOT_AUTHORIZED; 2024-07-01..2024-07-31 -> 403 NOT_AUTHORIZED;
    2024-08-01..2024-08-31 -> 200 (10 bars). The entitlement ceiling therefore
    falls inside August 2024 — ~24 months back from today — consistent with a
    ROLLING trailing-24-month window, not a fixed calendar date. Plan 09-02's
    hypotheses doc must fit its IS/OOS windows inside roughly
    [2024-08, today].
  - A2 (missing-bar behaviour): a far-OTM, effectively untraded contract
    (O:GDX250620C00080000, strike 80 vs a ~40-50 spot, range
    2025-05-01..2025-05-31 — inside the entitled window) returned ZERO result
    rows for the whole month. Non-trading days are ABSENT rows, never present
    with `v: 0`. Confirms D-07's "must have a bar dated exactly t" exact-key
    contract selection is correct as specified — there is nothing to
    carry-forward from.

Exports: parse_massive_ticker, format_massive_ticker, trading_days,
         OptionChainSource
"""
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas_market_calendars as mcal

# Import-not-copy (D-02): the backtester replays these exact pure functions,
# never a reimplementation.
from bot.options.strategy import is_monthly_expiry, option_dte

_MASSIVE_TICKER_RE = re.compile(r"^O:([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
_NYSE = mcal.get_calendar("NYSE")
_DATE_FMT = "%Y-%m-%d"
_RIGHT_MAP = {"call": "C", "put": "P"}


def parse_massive_ticker(ticker: str) -> dict:
    """Parse a Massive `O:` OCC-style option ticker into its components.

    Mirrors the live broker gateway's option-code parsing algorithm, adapted
    for Massive's `O:` prefix (the live gateway uses `US.`). Raises
    ValueError on anything that doesn't match — never returns a partial/
    garbage result.
    """
    m = _MASSIVE_TICKER_RE.match(ticker)
    if not m:
        raise ValueError(f"unparseable Massive option ticker: {ticker!r}")
    root, yy, mm, dd, right, strike8 = m.groups()
    return {
        "root": root,
        "expiry": date(2000 + int(yy), int(mm), int(dd)),
        "right": right,
        "strike": int(strike8) / 1000.0,
    }


def format_massive_ticker(root: str, expiry: date, right: str, strike: float) -> str:
    """Build a Massive `O:` OCC-style option ticker from its components."""
    return f"O:{root}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"


def trading_days(start: str, end: str) -> list:
    """NYSE trading days in [start, end], inclusive, as "YYYY-MM-DD" strings.

    Same 4-line shape as `backtester/run.py::_trading_days` — one definition,
    reused by the engine and CLI (T-09-04+).
    """
    valid = _NYSE.valid_days(start_date=start, end_date=end)
    return [d.strftime(_DATE_FMT) for d in valid]


class OptionChainSource:
    """Per-decision-day options chain view over cached Massive data.

    Day lookup is EXACT-KEY only (D-07/D-08) — a bar dated t+1 or t-1 can
    never answer a query for day t. `last_known_close` is the one documented
    exception (carry-forward for MANAGE-time marking only); keeping it next
    to the exact-key lookups makes the distinction auditable in one place.

    LAZY fetch design (T-09-13, VERIFICATION gap 3): `load()` fetches the
    contracts REFERENCE (cheap, one call) plus the underlying's daily bars —
    never a per-contract bar. Per-contract bars are fetched on demand by
    `rows_for(day, expiry, underlying_px, band_pct)`, which is called ONLY
    for the one expiry `pick_expiry` actually selects for that decision day,
    and ONLY for strikes in a narrow OTM-side band around that day's close —
    never the whole strike band x every expiry up front (the eager design
    this replaces measured 58k+ candidate contracts for one SPY window; the
    lazy design measures ~7.5k for SPY's entire 2024-08->2026-08 history at
    a +/-10% band). `_ensure_bars` remembers what it has already fetched
    in-memory for the lifetime of this object, so a later `rows_for` call on
    an already-seen expiry/band issues zero new requests.

    DEVIATION from the plan's literal "drop non-monthly expiries at load()
    time when prefer_monthly": a monthly (third-Friday) series does not exist
    inside every [t+min_dte, t+max_dte] window (e.g. rules_options.json's
    30..60-day window is 31 days wide, but the Aug-2025->Sep-2025 monthly gap
    is 35 days — decision days in that gap, e.g. 2025-04-17, have NO monthly
    expiry in range at all). Hard-dropping non-monthly candidates at load()
    time would silently remove the only valid `pick_expiry` answer on those
    days. `pick_expiry` (bot.options.strategy) already implements the correct
    "prefer monthly, fall back to all in-range candidates" logic itself — so
    `load()` keeps every DTE-range-relevant candidate (monthly AND
    non-monthly) and lets `pick_expiry` apply the preference at decision
    time. `prefer_monthly` is accepted here for signature parity but is a
    no-op on candidate selection (Rule 1 bug fix, T-09-03).
    """

    def __init__(self, source, underlying: str, start: str, end: str, workers: int = 1):
        self.source = source
        self.underlying = underlying
        self.start = start
        self.end = end
        self.workers = workers
        self._underlying_frame = None
        self._contracts = []       # [{"ticker", "right", "strike", "expiry"}]
        self._bars_by_ticker = {}  # {ticker: {"YYYY-MM-DD": {"close", "volume"}}}
        self.fetch_stats = {"requests": 0, "cache_hits": 0}

    def load(self, min_dte, max_dte, prefer_monthly=True):
        """Fetch the underlying's daily bars + the contracts REFERENCE only
        (no per-contract bar fetch — see class docstring's lazy-fetch note).
        Returns self.

        `_contracts` is narrowed to expiries that COULD ever be selected by
        any decision day in [start, end] — expiry in
        [start+min_dte, end+max_dte] — which shrinks the in-memory candidate
        list without requiring a single per-contract bar request.

        `prefer_monthly` is accepted for signature parity with the plan but
        does not filter candidates here — see the class docstring's
        DEVIATION note; `pick_expiry` applies the monthly preference itself.
        """
        self._underlying_frame = self.source.cached_bars(
            self.underlying, "1d", 1, "day", self.start, self.end
        )
        self._bars_by_ticker = {}
        if self._underlying_frame.empty:
            self._contracts = []
            return self

        expiry_lo = date.fromisoformat(self.start) + timedelta(days=min_dte)
        expiry_hi = date.fromisoformat(self.end) + timedelta(days=max_dte)
        raw_contracts = self.source.cached_contracts(
            self.underlying, self.start, expiry_hi.isoformat()
        )

        candidates = []
        for c in raw_contracts:
            right = _RIGHT_MAP.get(c.get("contract_type"))
            strike = c.get("strike_price")
            ticker = c.get("ticker")
            expiry_str = c.get("expiration_date")
            if right is None or strike is None or not ticker or not expiry_str:
                continue
            expiry = date.fromisoformat(expiry_str)
            if not (expiry_lo <= expiry <= expiry_hi):
                continue  # narrowing: outside any decision day's possible DTE window
            candidates.append(
                {"ticker": ticker, "right": right, "strike": float(strike), "expiry": expiry}
            )
        self._contracts = candidates
        return self

    def rows_for(self, day: str, expiry: date, underlying_px: float, band_pct: float) -> list:
        """Lazily fetch + return rows for `expiry`'s OTM-side band around
        `underlying_px` on `day` — the VERIFICATION gap-3 fix. Fetches bars
        ONLY for contracts of THIS expiry within the band (never the whole
        strike band x every expiry), via `_ensure_bars` (which no-ops for
        tickers already fetched this run).

        Same D-07 output shape as the old eager `contracts_for_day`: ticker,
        right, strike, expiry, dte, close, volume — for contracts with a bar
        dated EXACTLY `day` (D-07/D-08 preserved — never a neighbouring
        day's bar).
        """
        today = date.fromisoformat(day)
        put_lo, put_hi = underlying_px * (1 - band_pct / 100.0), underlying_px * 1.005
        call_lo, call_hi = underlying_px * 0.995, underlying_px * (1 + band_pct / 100.0)

        matches = []
        for c in self._contracts:
            if c["expiry"] != expiry:
                continue
            if c["right"] == "P":
                if not (put_lo <= c["strike"] <= put_hi):
                    continue
            elif not (call_lo <= c["strike"] <= call_hi):
                continue
            matches.append(c)

        self._ensure_bars([c["ticker"] for c in matches])

        rows = []
        for c in matches:
            bar = self._bars_by_ticker.get(c["ticker"], {}).get(day)
            if bar is None:
                continue
            dte = option_dte(c["expiry"], today)
            rows.append({
                "ticker": c["ticker"], "right": c["right"], "strike": c["strike"],
                "expiry": c["expiry"], "dte": dte,
                "close": bar["close"], "volume": bar["volume"],
            })
        return rows

    def _ensure_bars(self, tickers) -> None:
        """Fetch option bars for every ticker not already in `_bars_by_ticker`
        this run. Uses `ThreadPoolExecutor(self.workers)` when
        `self.workers > 1` (paid-tier parallel fetch); sequential otherwise
        (free tier default — 1 worker).
        """
        new_tickers = sorted({t for t in tickers if t not in self._bars_by_ticker})
        if not new_tickers:
            return

        # ponytail: cache-hit/miss is inferred by checking the cache file's
        # existence BEFORE the fetch call, since cached_option_bars itself
        # returns only the frame — cheaper than adding a second return value
        # to a hot-path method used by every contract.
        def _fetch_one(ticker):
            was_cached = os.path.exists(self.source.option_bar_cache_path(ticker))
            frame = self.source.cached_option_bars(ticker, self.start, self.end)
            return ticker, frame, was_cached

        if self.workers > 1:
            with ThreadPoolExecutor(self.workers) as pool:
                results = list(pool.map(_fetch_one, new_tickers))
        else:
            results = [_fetch_one(t) for t in new_tickers]

        for ticker, frame, was_cached in results:
            self.fetch_stats["cache_hits" if was_cached else "requests"] += 1
            per_day = {} if frame.empty else {
                ts.strftime(_DATE_FMT): {"close": float(row["Close"]), "volume": float(row["Volume"])}
                for ts, row in frame.iterrows()
            }
            self._bars_by_ticker[ticker] = per_day

        if len(new_tickers) > 20:
            print(
                f"[fetch] {self.underlying} +{len(new_tickers)} contracts "
                f"({len(self._bars_by_ticker)} loaded)",
                file=sys.stderr,
            )

    def expiries_for_day(self, day: str) -> list:
        """Sorted (expiry, dte) tuples for every expiry LISTED in the
        contracts reference with dte >= 0 on `day` — reference-based
        existence, no bar fetch here (`pick_expiry` applies the
        [min_dte, max_dte] window itself). Strike ROWS for the picked expiry
        still require a bar dated exactly `day` (D-07 preserved at the row
        level — see `rows_for`)."""
        today = date.fromisoformat(day)
        seen = {}
        for c in self._contracts:
            dte = option_dte(c["expiry"], today)
            if dte >= 0:
                seen[c["expiry"]] = dte
        return sorted(seen.items())

    def bar_close(self, ticker: str, day: str):
        """Exact-key close for `ticker` on `day`, or None."""
        bar = self._bars_by_ticker.get(ticker, {}).get(day)
        return bar["close"] if bar else None

    def bar_volume(self, ticker: str, day: str):
        """Exact-key volume for `ticker` on `day`, or None."""
        bar = self._bars_by_ticker.get(ticker, {}).get(day)
        return bar["volume"] if bar else None

    def underlying_close(self, day: str):
        """Exact-key underlying close on `day`, or None for a non-trading/
        absent day."""
        if self._underlying_frame is None or self._underlying_frame.empty:
            return None
        idx_dates = self._underlying_frame.index.strftime(_DATE_FMT)
        matches = self._underlying_frame.loc[idx_dates == day, "Close"]
        return float(matches.iloc[-1]) if not matches.empty else None

    def last_known_close(self, ticker: str, day: str):
        """Most-recent close AT OR BEFORE `day`, for MANAGE-time marking ONLY.

        NEVER use this for entry/contract selection (D-07/D-08) — it is the
        one documented carry-forward exception in this module.
        """
        per_day = self._bars_by_ticker.get(ticker)
        if not per_day:
            return None
        candidates = [d for d in per_day if d <= day]
        if not candidates:
            return None
        return per_day[max(candidates)]["close"]
