#!/usr/bin/env python3
"""
bot.ibs.strategy — Pure IBS mean-reversion decision core (IBS-03; D-02..D-05, D-16).

No I/O, no clock, no calendar, no broker, no logging: only math and datetime.
Every threshold comes from the IbsConfig passed in. The rule is the research
rule in backtester/experimental/ibs_search/strategy_search.py::simulate
(close-fill branch); tests/ibs/test_parity.py pins this module to it.

Exports: compute_ibs, parse_snapshot, decide_exits, decide_entries,
         size_position, trading_days_held
"""
import math
from datetime import datetime


def _pos_float(v):
    """float(v) if finite and > 0, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def compute_ibs(last, high, low):
    """D-02: (last - low) / (high - low); None on any missing / non-finite /
    non-positive input, high <= low, or last outside [low, high]."""
    last, high, low = _pos_float(last), _pos_float(high), _pos_float(low)
    if last is None or high is None or low is None:
        return None
    if high <= low or not (low <= last <= high):
        return None
    return (last - low) / (high - low)


def parse_snapshot(rows, now, max_age_s):
    """Ruling 6 / T-12-02: per-code fail-closed validation of broker snapshot rows.

    Returns (quotes, skipped). quotes[code] = {last, high, low, ibs, update_time};
    skipped[code] = reason in bad_price / no_range / last_outside_range /
    suspended / bad_update_time / stale_date / stale_age. `now` is an ET-aware
    datetime; update_time is naive ET (first 19 chars — live values carry
    fractional seconds). bid/ask are never used.
    """
    quotes, skipped = {}, {}
    for row in rows:
        code = str(row.get("code", ""))
        if not code:
            continue
        last = _pos_float(row.get("last_price"))
        high = _pos_float(row.get("high_price"))
        low = _pos_float(row.get("low_price"))
        if last is None or high is None or low is None:
            skipped[code] = "bad_price"
            continue
        if high <= low:
            skipped[code] = "no_range"
            continue
        if not (low <= last <= high):
            skipped[code] = "last_outside_range"
            continue
        if row.get("suspension") not in (False, 0):  # missing key fails closed
            skipped[code] = "suspended"
            continue
        raw_ts = row.get("update_time")
        try:
            ts = datetime.strptime(str(raw_ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
        except ValueError:
            skipped[code] = "bad_update_time"
            continue
        if ts.date() != now.date():
            skipped[code] = "stale_date"
            continue
        if (now - ts).total_seconds() > max_age_s:  # slightly negative (clock skew) allowed
            skipped[code] = "stale_age"
            continue
        quotes[code] = {"last": last, "high": high, "low": low,
                        "ibs": compute_ibs(last, high, low), "update_time": raw_ts}
    return quotes, skipped


def decide_exits(open_codes, ibs_by_code, held_days, pending, cfg):
    """D-04: [(code, reason)] in input order; reason 'retry' | 'ibs' | 'time'.

    'retry' (ruling 4): an earlier decided exit that did not complete is
    re-placed unconditionally. 'time' fires even when IBS is unknown.
    """
    out = []
    for code in open_codes:
        ibs = ibs_by_code.get(code)
        if code in pending:
            out.append((code, "retry"))
        elif ibs is not None and ibs > cfg.ibs_exit_min:
            out.append((code, "ibs"))
        elif held_days.get(code, 0) >= cfg.max_hold_trading_days:
            out.append((code, "time"))
    return out


def decide_entries(ibs_by_code, excluded, free_slots, cfg, universe):
    """D-03: codes to enter, IBS ascending (ties by universe index), capped at free_slots.

    Caller passes `excluded` = active rows | codes exited this session (ruling 3,
    no same-day re-entry) | external broker holdings (D-12) | codes with working
    orders (D-03), and free_slots = max_concurrent_positions - active rows AFTER
    exits (D-04, research Pitfall 5).
    """
    order = list(universe)
    cand = [c for c, v in ibs_by_code.items()
            if v is not None and v < cfg.ibs_entry_max and c not in excluded and c in order]
    cand.sort(key=lambda c: (ibs_by_code[c], order.index(c)))
    return cand[:max(int(free_slots), 0)]


def size_position(limit_price, cfg):
    """D-05: floor(sizing_equity_usd x position_pct_of_equity / 100 / limit_price); 0 on a bad price."""
    p = _pos_float(limit_price)
    if p is None:
        return 0
    return int((cfg.sizing_equity_usd * cfg.position_pct_of_equity / 100.0) // p)


def trading_days_held(entry_date, today, session_dates):
    """Sessions in (entry_date, today] — equals research `t - d0`.

    session_dates come from bot.scanner.calendar.trading_days_between (injected
    so this module stays pure).
    """
    return sum(1 for d in session_dates if entry_date < d <= today)
