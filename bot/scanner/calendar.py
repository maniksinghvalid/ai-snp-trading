#!/usr/bin/env python3
"""
bot.scanner.calendar — NYSE trading-day and market-close helpers.

Thin wrapper around pandas_market_calendars for holiday / half-day awareness.
All returned times are in US Eastern. The module-level _nyse singleton mirrors
the ET = ZoneInfo(...) pattern in bot.safety.et_helpers — created once at
import time to avoid repeated calendar construction overhead.

Exports: is_trading_day, get_market_close_et, get_prior_n_trading_days
"""
from datetime import date
from typing import List

import pandas as pd
import pandas_market_calendars as mcal

from bot.safety.et_helpers import ET

# ============================================================
# Module-level NYSE calendar singleton
# ============================================================

# Created exactly once at import time (analogous to ET = ZoneInfo("America/New_York"))
_nyse = mcal.get_calendar("NYSE")


# ============================================================
# Public API
# ============================================================

def is_trading_day(dt: date) -> bool:
    """Return True if dt is a NYSE trading day (not a holiday, weekend, or special closure).

    Uses pandas_market_calendars valid_days() which handles observed holidays
    (e.g. Christmas shifted to Monday) correctly.

    dt: date — the calendar date to check.
    Returns True if NYSE is open on dt, False otherwise.
    """
    valid = _nyse.valid_days(start_date=dt, end_date=dt)
    return len(valid) > 0


def get_market_close_et(dt: date) -> str:
    """Return the NYSE market close time on dt as an HH:MM string in US Eastern.

    Handles early closes (half-days): Black Friday (13:00), Christmas Eve (13:00),
    July 3rd (13:00), etc. Returns "13:00" on half-days and "16:00" on normal days.

    dt: date — a NYSE trading day. Must be a valid trading day.
    Returns HH:MM string (e.g. "16:00" or "13:00").
    Raises ValueError if dt is not a NYSE trading day.
    """
    sched = _nyse.schedule(start_date=dt, end_date=dt)
    if sched.empty:
        raise ValueError(f"{dt} is not a NYSE trading day")
    # market_close is UTC-aware; convert to ET for correct ET time string
    close_utc = sched.iloc[0]["market_close"]
    close_et = close_utc.tz_convert("America/New_York")
    return close_et.strftime("%H:%M")


def get_prior_n_trading_days(ref_date: date, n: int) -> List:
    """Return the n most recent completed NYSE trading days strictly before ref_date.

    Looks back n*2+10 calendar days to ensure enough coverage across holidays
    and long weekends. Always excludes ref_date itself (strict < comparison).

    ref_date: date — the reference date (excluded from results).
    n: int — number of prior trading days to return.
    Returns list of n Timestamp objects, each a NYSE trading day, all < ref_date,
        sorted in ascending order (oldest first).
    """
    start = pd.Timestamp(ref_date) - pd.Timedelta(days=n * 2 + 10)
    valid = _nyse.valid_days(start_date=start.date(), end_date=ref_date)
    # Exclude ref_date itself (completed days only)
    prior = [d for d in valid if d.date() < ref_date]
    return prior[-n:]
