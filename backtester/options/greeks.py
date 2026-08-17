#!/usr/bin/env python3
"""
backtester.options.greeks — Black-Scholes IV/delta + IV Rank (Phase 9, D-09/D-10).

European Black-Scholes only, `q=0.0` (dividend yield) default, `r` supplied by
the caller (CLI default 0.045, see backtester/options_run.py). Every function
here is pure (no I/O, no clock reads) — the `today`/`day` argument is always
supplied by the caller, matching bot/options/strategy.py's contract.

Stdlib `math` only (D-09) -- no scipy, no numpy. `math.erf` gives the exact
normal CDF at float precision, so no hand-rolled polynomial approximation is
needed. Bisection is used for the IV solve rather than Newton-Raphson: BS
price is strictly monotonic in sigma for T>0 (vega > 0 always), so bisection
is guaranteed to converge given a valid bracket, and the data volumes here
(hundreds of contracts x hundreds of days) are not performance-sensitive.

Fail-closed contract (mirrors bot/options/strategy.py's `_as_float`/
`leg_is_liquid` discipline throughout): a degenerate input (price below
intrinsic, T<=0, no root in the search bracket) returns `None`, meaning "drop
this contract from today's candidate rows" -- never a NaN or an exception
propagated into the strategy layer (RESEARCH Pitfall 5).

Exports: bs_price, bs_delta, implied_vol, dte_to_years, atm_iv, iv_rank,
         IV_RANK_WINDOW, IV_RANK_MIN_OBS
"""
import math
from datetime import date

# IV_RANK_MIN_OBS is set below the classic 252-day warm-up because the
# Massive option-aggregates entitlement is only ~24 months deep (Plan
# 09-01's live probe pinned the boundary inside August 2024) -- demanding a
# full 252-observation warm-up before ANY iv_rank value is available would
# burn roughly half the usable history before the backtest can even start
# evaluating entries. The window still GROWS toward IV_RANK_WINDOW as more
# observations accumulate (see iv_rank's trailing-slice behaviour); this
# warm-up rule must be restated verbatim in the hypotheses doc (T-09-06) so
# H1's threshold comparison is reproducible.
IV_RANK_WINDOW = 252
IV_RANK_MIN_OBS = 60


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf -- exact at float precision (D-09)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, t_years, r, sigma, right, q=0.0):
    """European Black-Scholes price. `right`: 'C' or 'P'.

    Returns intrinsic value (never raises) at `t_years <= 0` or `sigma <= 0`
    -- both are degenerate for the log-normal BS formula, so the fail-closed
    answer is the model's own limit as either variable approaches zero.
    """
    if t_years <= 0 or sigma <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "C":
        return spot * math.exp(-q * t_years) * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * math.exp(-q * t_years) * _norm_cdf(-d1)


def bs_delta(spot, strike, t_years, r, sigma, right, q=0.0):
    """European BS delta, derived from the SAME d1 as bs_price (never assumed).

    Call delta in (0, 1), put delta in (-1, 0) for any finite t_years>0,
    sigma>0. Returns 0.0 (never raises) at the same degenerate inputs as
    bs_price.
    """
    if t_years <= 0 or sigma <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    disc = math.exp(-q * t_years)
    return disc * _norm_cdf(d1) if right == "C" else disc * (_norm_cdf(d1) - 1.0)


def implied_vol(price, spot, strike, t_years, r, right, q=0.0,
                 lo=1e-4, hi=5.0, tol=1e-6, max_iter=100):
    """Bisection IV solve. Fails closed to None (RESEARCH Pitfall 5) when:
      - price is below the no-arbitrage floor (no solution exists below it)
      - t_years <= 0 (expiring/expired contract, no time value to solve for)
      - no sign change in [lo, hi] sigma (price outside the achievable range)

    The floor is the DISCOUNTED (present-value) intrinsic --
    `max(spot*exp(-q*t) - strike*exp(-r*t), 0)` for a call, the mirror for a
    put -- not the naive `max(spot-strike, 0)`/`max(strike-spot, 0)`. A
    European option's price approaches this discounted floor as sigma->0
    for any t_years>0 (bs_price's own limit); the naive undiscounted
    intrinsic is only correct exactly AT t_years<=0 (bs_price's other
    branch). Deep-ITM European puts with meaningful DTE routinely trade a
    few cents below the naive intrinsic purely from discounting the strike
    -- using the naive floor here would wrongly fail-close a genuine BS
    price (found via this module's own round-trip test).
    """
    if t_years <= 0:
        return None
    disc_r, disc_q = math.exp(-r * t_years), math.exp(-q * t_years)
    floor = (max(spot * disc_q - strike * disc_r, 0.0) if right == "C"
             else max(strike * disc_r - spot * disc_q, 0.0))
    if price < floor - 1e-9:
        return None
    f_lo = bs_price(spot, strike, t_years, r, lo, right, q) - price
    f_hi = bs_price(spot, strike, t_years, r, hi, right, q) - price
    # Deep-ITM + short-DTE + low-sigma inputs can saturate norm_cdf(d1) to
    # 1.0 at float64 precision, making bs_price(lo) land exactly on the
    # target price (vega ~ 0 in this regime) -- return immediately rather
    # than let the loop below drift away from an already-found root.
    if abs(f_lo) < tol:
        return lo
    if abs(f_hi) < tol:
        return hi
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = bs_price(spot, strike, t_years, r, mid, right, q) - price
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def dte_to_years(expiry: date, today: date) -> float:
    """Calendar-day T in years, matching bot.options.strategy.option_dte's
    convention (`(expiry - today).days`), floored at 0 for a past expiry."""
    from bot.options.strategy import option_dte
    return max(option_dte(expiry, today), 0) / 365.25


def atm_iv(rows, underlying_px, day, r, target_dte):
    """Daily ATM-IV point for one underlying (D-10).

    `rows`: OptionChainSource.contracts_for_day() output -- dicts with
    right, strike, expiry, dte, close. Picks the expiry whose dte is closest
    to `target_dte`, then the LISTED strike closest to `underlying_px` on
    each side, solves IV for the call and the put at that strike, and
    returns their mean. Returns None when rows is empty, no candidate
    expiry exists, or either side's IV fails to solve (fails closed).
    """
    if not rows:
        return None
    expiry_dte = {}
    for r_ in rows:
        expiry_dte.setdefault(r_["expiry"], r_["dte"])
    best_expiry = min(expiry_dte, key=lambda e: abs(expiry_dte[e] - target_dte))
    at_expiry = [r_ for r_ in rows if r_["expiry"] == best_expiry]
    calls = [r_ for r_ in at_expiry if r_["right"] == "C"]
    puts = [r_ for r_ in at_expiry if r_["right"] == "P"]
    if not calls or not puts:
        return None
    call = min(calls, key=lambda r_: abs(r_["strike"] - underlying_px))
    put = min(puts, key=lambda r_: abs(r_["strike"] - underlying_px))
    today = date.fromisoformat(day)
    t_years = dte_to_years(best_expiry, today)
    iv_c = implied_vol(call["close"], underlying_px, call["strike"], t_years, r, "C")
    iv_p = implied_vol(put["close"], underlying_px, put["strike"], t_years, r, "P")
    if iv_c is None or iv_p is None:
        return None
    return (iv_c + iv_p) / 2.0


def iv_rank(series, window=IV_RANK_WINDOW, min_obs=IV_RANK_MIN_OBS):
    """IV Rank: min-max normalization over the trailing `window` observations,
    `(iv - min) / (max - min) * 100`, in 0-100 PERCENT units -- the
    industry/tastytrade "IV Rank" convention (D-10 as amended).

    This is NOT a percentile rank (IVP) -- percentile rank would be "what
    fraction of the trailing window had a lower IV than today," a different
    quantity that gives materially different verdicts on H1 (RESEARCH
    Pitfall 4). `ivp_min` is null in rules_options.json, so the IVP dual
    gate in passes_entry_gate is off; this module never computes IVP.

    `series`: chronological list of daily ATM-IV values (oldest first,
    latest = today's observation, i.e. series[-1]). Only the trailing
    `window` observations count -- an extreme value older than `window`
    entries back does not change the result. Returns None (warm-up not
    satisfied) when fewer than `min_obs` observations exist, and None when
    max == min over the window (degenerate flat window -- no signal to
    rank against).
    """
    if not series:
        return None
    trailing = series[-window:]
    if len(trailing) < min_obs:
        return None
    lo, hi = min(trailing), max(trailing)
    if hi == lo:
        return None
    return (trailing[-1] - lo) / (hi - lo) * 100.0


if __name__ == "__main__":
    # ponytail: smallest runnable self-check for this module's core math,
    # per non-trivial-logic-needs-a-check discipline. Full coverage lives in
    # tests/backtester/options/test_greeks.py.
    _t = 45 / 365.25
    _price = bs_price(100.0, 100.0, _t, 0.045, 0.30, "C")
    _iv = implied_vol(_price, 100.0, 100.0, _t, 0.045, "C")
    assert _iv is not None and abs(_iv - 0.30) < 1e-4
    assert bs_delta(100.0, 90.0, _t, 0.045, 0.30, "C") > bs_delta(100.0, 110.0, _t, 0.045, 0.30, "C")
    assert implied_vol(0.01, 100.0, 60.0, _t, 0.045, "C") is None  # below intrinsic
    assert iv_rank([0.2] * 59) is None  # below IV_RANK_MIN_OBS
    print("greeks self-check OK")
