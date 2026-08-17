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
from datetime import date

import pandas_market_calendars as mcal

# Import-not-copy (D-02): the backtester replays these exact pure functions,
# never a reimplementation. Unused at skeleton time (T-09-01) — OptionChainSource.load()
# (T-09-03) is the consumer.
from bot.options.strategy import is_monthly_expiry, option_dte  # noqa: F401

_MASSIVE_TICKER_RE = re.compile(r"^O:([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
_NYSE = mcal.get_calendar("NYSE")
_DATE_FMT = "%Y-%m-%d"


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
