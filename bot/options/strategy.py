#!/usr/bin/env python3
"""
bot.options.strategy — pure strategy core for `tasty_credit_spreads` (Phase 8).

Every function here is PURE: no I/O, no clock reads, no SDK/broker calls, no
logging side effects. `today` is always a parameter, never date.today(). This
is a hard contract (design D7): the Phase 9 options backtester replays these
exact functions over historical chains, so any hidden state would silently
diverge live-vs-backtest results.

Every threshold comes from `cfg` (an OptionsConfig) — the only literals in this
module are the 100 contract multiplier and /100 percent conversions (CFG-01).

Exports: is_monthly_expiry, option_dte, pick_expiry, passes_entry_gate,
         leg_is_liquid, pick_strikes, size_position, mark_spread, manage_decision
"""
import math
from datetime import date
from typing import Optional

# Contract multiplier: one US equity option covers 100 shares. Not a strategy
# knob — it is a property of the instrument, so it stays a literal.
_CONTRACT_MULTIPLIER = 100


# ============================================================
# Expiry selection
# ============================================================

def is_monthly_expiry(expiry: date) -> bool:
    """Return True when `expiry` is the third Friday of its month.

    Monthly (third-Friday) series carry the deepest open interest and tightest
    spreads, so they win ties in pick_expiry when cfg.prefer_monthly is set.
    """
    return expiry.weekday() == 4 and 15 <= expiry.day <= 21


def option_dte(expiry: date, today: date) -> int:
    """Return calendar days to expiry (may be negative for a past expiry)."""
    return (expiry - today).days


def pick_expiry(expiries: list, today: date, cfg) -> Optional[date]:
    """Pick the expiry closest to cfg.target_dte within [min_dte, max_dte].

    Args:
        expiries: list of (expiry_date, dte) tuples as reported by the chain.
                  The dte is taken from the caller, not recomputed, so the
                  broker's own day count is honoured.
        today:    the ET session date (supplied — this module never reads a clock).
        cfg:      OptionsConfig.

    Returns the chosen expiry date, or None when nothing is inside the DTE
    window. When cfg.prefer_monthly is True and at least one monthly
    (third-Friday) expiry sits inside the window, only monthlies are considered
    (live UAT 2026-08-17: the Wed 2026-09-30 series won on |dte-45| but its
    wings had OI 41-74 and 5-7% spreads, while the monthlies were penny/nickel
    wide). Otherwise the closest to target_dte wins; ties keep the earlier date.
    """
    candidates = [
        (expiry, dte) for expiry, dte in expiries
        if expiry >= today and cfg.min_dte <= dte <= cfg.max_dte
    ]
    if not candidates:
        return None
    if cfg.prefer_monthly:
        monthlies = [c for c in candidates if is_monthly_expiry(c[0])]
        if monthlies:
            candidates = monthlies
    best = min(candidates, key=lambda e: (abs(e[1] - cfg.target_dte), e[0]))
    return best[0]


# ============================================================
# Entry gate
# ============================================================

def passes_entry_gate(u: dict, cfg) -> bool:
    """Return True when the underlying's IV regime justifies selling premium.

    Args:
        u:   {"ivr_pct", "ivp_pct", "change_pct"} — IV rank/percentile already in
             percent units (0-100; the x100 conversion from the broker's
             fractional iv_rank is the service's job) and the session change in
             percent.
        cfg: OptionsConfig.

    The IVR threshold is cfg.ivr_min, lowered to cfg.fear_ivr_min on a
    sell-into-fear day (change_pct <= -cfg.fear_drop_pct). When cfg.ivp_min is
    set, IVP must additionally clear it — a dual gate against IVR being skewed
    by a single outlier in its lookback. Missing data fails closed.
    """
    ivr = u.get("ivr_pct")
    if ivr is None:
        return False

    threshold = cfg.ivr_min
    change = u.get("change_pct")
    if change is not None and change <= -cfg.fear_drop_pct:
        threshold = cfg.fear_ivr_min

    if ivr < threshold:
        return False

    if cfg.ivp_min is not None:
        ivp = u.get("ivp_pct")
        if ivp is None or ivp < cfg.ivp_min:
            return False

    return True


# ============================================================
# Liquidity + strike selection
# ============================================================

def leg_is_liquid(row: dict, cfg) -> bool:
    """Return True when a single contract is tradeable without silly slippage.

    Requires a positive bid, open interest >= cfg.min_open_interest, and a
    bid/ask spread no wider than cfg.max_spread_pct_of_mid percent of the mid
    OR no wider than cfg.max_spread_abs_usd in absolute terms (cheap far-OTM
    wings are nickel-wide yet fail a pure %-of-mid gate).
    Fails closed on missing or nonsensical quotes.
    """
    bid = _as_float(row.get("bid"))
    ask = _as_float(row.get("ask"))
    if bid <= 0 or ask < bid:
        return False
    if _as_float(row.get("open_interest")) < cfg.min_open_interest:
        return False
    mid = (bid + ask) / 2
    if mid <= 0:
        return False
    spread = ask - bid
    return (spread / mid * 100 <= cfg.max_spread_pct_of_mid
            or spread <= cfg.max_spread_abs_usd + 1e-9)


def pick_strikes(rows, underlying_px, structure, cfg) -> Optional[dict]:
    """Build a defined-risk credit spread from a chain snapshot.

    Args:
        rows:          per-contract dicts with code, right ('C'/'P'), strike,
                       delta, bid, ask, open_interest.
        underlying_px: last price of the underlying (sets the wing width).
        structure:     "iron_condor" or "put_credit_spread".
        cfg:           OptionsConfig.

    Short strikes are the contracts whose |delta| is closest to cfg.short_delta.
    Each long wing is the LISTED strike closest to short -/+ the wing width and
    at least one listed strike away from the short (never the same strike, which
    would be a zero-width spread with unlimited... nothing, but no risk defined).

    Returns {"legs": [...], "credit": float, "width": float} with legs ordered
    LONG WINGS FIRST, then shorts — the safe OPEN order (buy protection before
    selling risk) that the LegExecutor consumes. Returns None when no valid
    strikes exist, any selected leg is illiquid, or the credit is below
    cfg.min_credit_to_width * width.

    Raises ValueError for an unrecognised structure (fail closed rather than
    silently degrading an iron condor to a one-sided spread).
    """
    if structure not in ("iron_condor", "put_credit_spread"):
        raise ValueError(f"unsupported structure: {structure}")

    # Wing width: a % of price, floored in dollars — 1% of a $60 ETF is one
    # $0.50 strike, which turns a $1k risk budget into a 30+ lot condor (live
    # UAT 2026-08-17: XLE). The floor keeps lot counts sane on cheap underlyings.
    width_target = max(
        underlying_px * cfg.wing_width_pct_of_underlying / 100,
        getattr(cfg, "min_wing_width_usd", 0.0),
    )

    puts = [r for r in rows if r.get("right") == "P"]
    short_put = _closest_delta(puts, cfg.short_delta)
    if short_put is None:
        return None
    long_put = _pick_wing(puts, short_put["strike"], -width_target)
    if long_put is None:
        return None

    # (row, side) pairs — long wings first, then shorts (the safe OPEN order).
    longs = [long_put]
    shorts = [short_put]
    verticals = [short_put["strike"] - long_put["strike"]]
    credit = _mid(short_put) - _mid(long_put)

    if structure == "iron_condor":
        calls = [r for r in rows if r.get("right") == "C"]
        short_call = _closest_delta(calls, cfg.short_delta)
        if short_call is None:
            return None
        long_call = _pick_wing(calls, short_call["strike"], width_target)
        if long_call is None:
            return None
        longs.append(long_call)
        shorts.append(short_call)
        verticals.append(long_call["strike"] - short_call["strike"])
        credit += _mid(short_call) - _mid(long_call)

    if not all(leg_is_liquid(r, cfg) for r in longs + shorts):
        return None

    # Max, not sum: on an unequal snap the wider side is the real max loss.
    width = max(verticals)
    if credit < cfg.min_credit_to_width * width:
        return None

    legs = (
        [_leg(r, "BUY") for r in longs]
        + [_leg(r, "SELL") for r in shorts]
    )
    return {"legs": legs, "credit": credit, "width": width}


# ============================================================
# Sizing
# ============================================================

def size_position(width, credit, cfg, open_max_loss_total) -> int:
    """Return the number of spreads to trade (0 = skip).

    Per-spread risk is (width - credit) * 100. Quantity is the whole number of
    spreads whose combined risk fits cfg.max_risk_per_trade_pct of
    cfg.sizing_equity_usd, then capped so total open max loss stays inside
    cfg.max_bp_usage_pct of that equity. Never negative; floors (never rounds
    up) so the dollar-risk budget is a hard ceiling.
    """
    risk = (width - credit) * _CONTRACT_MULTIPLIER
    if risk <= 0:
        return 0
    qty = math.floor(cfg.sizing_equity_usd * cfg.max_risk_per_trade_pct / 100 / risk)
    cap = math.floor(
        (cfg.sizing_equity_usd * cfg.max_bp_usage_pct / 100 - open_max_loss_total) / risk
    )
    return max(min(qty, cap), 0)


# ============================================================
# Marking + management
# ============================================================

def mark_spread(legs, quotes) -> float:
    """Return the current cost to close one spread.

    Sum of the SELL legs' mids (what it costs to buy them back) minus the BUY
    legs' mids (what selling them recovers). Compare against the credit taken:
    credit - mark is the per-spread open profit.

    Args:
        legs:   leg dicts with "code" and "side".
        quotes: {code: {"bid", "ask"}}.
    """
    total = 0.0
    for leg in legs:
        q = quotes[leg["code"]]
        mid = (_as_float(q.get("bid")) + _as_float(q.get("ask"))) / 2
        total += mid if leg["side"] == "SELL" else -mid
    return total


def manage_decision(mark, credit, dte, cfg) -> Optional[str]:
    """Return the exit reason for an open spread, or None to keep holding.

    Evaluated strictly in this order, first hit wins:
      1. "assignment_guard" — dte <= cfg.assignment_guard_dte. Pin/assignment
         risk outranks everything, including an unrealised profit.
      2. "profit_target"    — captured >= cfg.profit_target_pct_of_credit of the
         credit. Ahead of the stop so a whipsawing mark books the win.
      3. "stop_loss"        — only when cfg.stop_loss_credit_multiple is set
         (defined risk defaults to no hard stop).
      4. "dte_exit"         — dte <= cfg.manage_dte (gamma risk).
    """
    if dte <= cfg.assignment_guard_dte:
        return "assignment_guard"
    if credit - mark >= cfg.profit_target_pct_of_credit / 100 * credit:
        return "profit_target"
    if (cfg.stop_loss_credit_multiple is not None
            and mark - credit >= cfg.stop_loss_credit_multiple * credit):
        return "stop_loss"
    if dte <= cfg.manage_dte:
        return "dte_exit"
    return None


# ============================================================
# Internal helpers
# ============================================================

def _as_float(value) -> float:
    """Coerce a possibly-missing chain field to float.

    None / '' / the SDK's literal 'N/A' (seen live on bid/ask of untraded
    strikes) / anything non-numeric → 0.0, which fails closed downstream
    (leg_is_liquid rejects bid <= 0; a 0.0 mid never passes the credit gate).
    """
    if value is None or value == "":
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out else 0.0   # NaN → 0.0


def _mid(row: dict) -> float:
    """Mid price of a contract row."""
    return (_as_float(row.get("bid")) + _as_float(row.get("ask"))) / 2


def _leg(row: dict, side: str) -> dict:
    """Project a chain row onto the leg dict the executor/store consume."""
    return {
        "code": row["code"],
        "right": row["right"],
        "strike": float(row["strike"]),
        "side": side,
        "mid": _mid(row),
    }


def _closest_delta(rows, target_delta):
    """Return the row whose |delta| is closest to target_delta (None if empty)."""
    if not rows:
        return None
    return min(rows, key=lambda r: abs(abs(_as_float(r.get("delta"))) - target_delta))


def _pick_wing(rows, short_strike, offset):
    """Return the listed strike closest to short_strike + offset, on that side.

    `offset` is signed: negative for the put wing (below the short), positive
    for the call wing (above). Candidates are strictly beyond the short strike,
    so the wing is always at least one listed strike away — a same-strike pick
    would be a zero-width "spread" with no defined risk at all.
    """
    if offset < 0:
        candidates = [r for r in rows if r["strike"] < short_strike]
    else:
        candidates = [r for r in rows if r["strike"] > short_strike]
    if not candidates:
        return None
    target = short_strike + offset
    return min(candidates, key=lambda r: abs(r["strike"] - target))
