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
import re
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

    `load()` calls the contracts-reference endpoint ONCE for the whole
    [start, end] window (RESEARCH Anti-Patterns — never once per day) and
    narrows the candidate set to a strike band around the window's underlying
    close range BEFORE fetching any per-contract bars, so a single underlying
    does not require thousands of per-contract requests (RESEARCH Pitfall 7).

    DEVIATION from the plan's literal "drop non-monthly expiries at load()
    time when prefer_monthly": a monthly (third-Friday) series does not exist
    inside every [t+min_dte, t+max_dte] window (e.g. rules_options.json's
    30..60-day window is 31 days wide, but the Aug-2025->Sep-2025 monthly gap
    is 35 days — decision days in that gap, e.g. 2025-04-17, have NO monthly
    expiry in range at all). Hard-dropping non-monthly candidates at load()
    time would silently remove the only valid `pick_expiry` answer on those
    days. `pick_expiry` (bot.options.strategy) already implements the correct
    "prefer monthly, fall back to all in-range candidates" logic itself — so
    `load()` keeps every DTE-range/strike-band candidate (monthly AND
    non-monthly) and lets `pick_expiry` apply the preference at decision
    time. `prefer_monthly` is accepted here for signature parity but is a
    no-op on candidate selection (Rule 1 bug fix, T-09-03).
    """

    def __init__(self, source, underlying: str, start: str, end: str):
        self.source = source
        self.underlying = underlying
        self.start = start
        self.end = end
        self._underlying_frame = None
        self._contracts = []       # [{"ticker", "right", "strike", "expiry"}]
        self._bars_by_ticker = {}  # {ticker: {"YYYY-MM-DD": {"close", "volume"}}}

    def load(self, min_dte, max_dte, prefer_monthly=True, strike_band_pct=20.0):
        """Fetch + narrow the chain for [start, end]. Returns self.

        `prefer_monthly` is accepted for signature parity with the plan but
        does not filter candidates here — see the class docstring's
        DEVIATION note; `pick_expiry` applies the monthly preference itself.
        """
        self._underlying_frame = self.source.cached_bars(
            self.underlying, "1d", 1, "day", self.start, self.end
        )
        closes = self._underlying_frame["Close"] if not self._underlying_frame.empty else None
        if closes is None or closes.empty:
            self._contracts = []
            self._bars_by_ticker = {}
            return self

        lo_close, hi_close = float(closes.min()), float(closes.max())
        band_lo = lo_close * (1 - strike_band_pct / 100.0)
        band_hi = hi_close * (1 + strike_band_pct / 100.0)

        # Widen expiry_lte past `end` by max_dte so the last decision day's
        # look-forward window is still covered by this single fetch.
        expiry_lte = (date.fromisoformat(self.end) + timedelta(days=max_dte)).isoformat()
        raw_contracts = self.source.cached_contracts(self.underlying, self.start, expiry_lte)

        candidates = []
        for c in raw_contracts:
            right = _RIGHT_MAP.get(c.get("contract_type"))
            strike = c.get("strike_price")
            ticker = c.get("ticker")
            expiry_str = c.get("expiration_date")
            if right is None or strike is None or not ticker or not expiry_str:
                continue
            expiry = date.fromisoformat(expiry_str)
            if not (band_lo <= strike <= band_hi):
                continue  # narrowing: strike band around the window's close range
            candidates.append(
                {"ticker": ticker, "right": right, "strike": float(strike), "expiry": expiry}
            )
        self._contracts = candidates

        self._bars_by_ticker = {}
        for c in candidates:
            frame = self.source.cached_option_bars(c["ticker"], self.start, self.end)
            if frame.empty:
                continue
            per_day = {
                ts.strftime(_DATE_FMT): {"close": float(row["Close"]), "volume": float(row["Volume"])}
                for ts, row in frame.iterrows()
            }
            self._bars_by_ticker[c["ticker"]] = per_day
        return self

    def contracts_for_day(self, day: str, min_dte: int, max_dte: int) -> list:
        """Rows for contracts with a bar dated EXACTLY `day` and dte in
        [min_dte, max_dte] — never a neighbouring day's bar (D-07/D-08)."""
        today = date.fromisoformat(day)
        rows = []
        for c in self._contracts:
            bar = self._bars_by_ticker.get(c["ticker"], {}).get(day)
            if bar is None:
                continue
            dte = option_dte(c["expiry"], today)
            if not (min_dte <= dte <= max_dte):
                continue
            rows.append({
                "ticker": c["ticker"], "right": c["right"], "strike": c["strike"],
                "expiry": c["expiry"], "dte": dte,
                "close": bar["close"], "volume": bar["volume"],
            })
        return rows

    def expiries_for_day(self, day: str) -> list:
        """Sorted (expiry, dte) tuples for expiries with at least one bar on
        `day`, exactly the shape `pick_expiry` consumes."""
        today = date.fromisoformat(day)
        seen = {}
        for c in self._contracts:
            if day not in self._bars_by_ticker.get(c["ticker"], {}):
                continue
            seen[c["expiry"]] = option_dte(c["expiry"], today)
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
