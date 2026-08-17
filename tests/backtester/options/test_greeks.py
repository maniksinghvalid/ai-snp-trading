#!/usr/bin/env python3
"""tests.backtester.options.test_greeks — Black-Scholes IV/delta + IVR unit
tests (no network, pure arithmetic, D-18)."""
from datetime import date

import pytest

from bot.options.config import load_options_config
from bot.options.strategy import option_dte, passes_entry_gate
import backtester.options.greeks as greeks

_R = 0.045
_SPOT = 100.0
_STRIKES = [80.0, 90.0, 100.0, 110.0, 120.0]
_T_YEARS = [7 / 365.25, 45 / 365.25, 180 / 365.25]
_SIGMAS = [0.10, 0.25, 0.60]
_RIGHTS = ["C", "P"]


# ============================================================
# T-09-04: bs_price / bs_delta / implied_vol / dte_to_years
# ============================================================

def test_iv_roundtrip():
    for strike in _STRIKES:
        for t_years in _T_YEARS:
            for sigma in _SIGMAS:
                for right in _RIGHTS:
                    price = greeks.bs_price(_SPOT, strike, t_years, _R, sigma, right)
                    iv = greeks.implied_vol(price, _SPOT, strike, t_years, _R, right)
                    assert iv is not None, (strike, t_years, sigma, right)
                    repriced = greeks.bs_price(_SPOT, strike, t_years, _R, iv, right)
                    assert abs(repriced - price) < 1e-4, (strike, t_years, sigma, right)


def test_delta_monotonic():
    sigma, t_years = 0.25, 45 / 365.25
    call_deltas = [
        greeks.bs_delta(_SPOT, k, t_years, _R, sigma, "C") for k in _STRIKES
    ]
    put_deltas = [
        greeks.bs_delta(_SPOT, k, t_years, _R, sigma, "P") for k in _STRIKES
    ]
    assert call_deltas == sorted(call_deltas, reverse=True)  # strictly decreasing in strike
    assert put_deltas == sorted(put_deltas)  # strictly increasing (toward 0) in strike
    assert len(set(call_deltas)) == len(call_deltas)
    assert len(set(put_deltas)) == len(put_deltas)


def test_delta_bounds():
    for strike in _STRIKES:
        for t_years in _T_YEARS:
            for sigma in _SIGMAS:
                call_delta = greeks.bs_delta(_SPOT, strike, t_years, _R, sigma, "C")
                put_delta = greeks.bs_delta(_SPOT, strike, t_years, _R, sigma, "P")
                assert 0.0 < call_delta < 1.0, (strike, t_years, sigma)
                assert -1.0 < put_delta < 0.0, (strike, t_years, sigma)


def test_iv_fails_closed_below_intrinsic():
    # price below intrinsic - 1e-9
    assert greeks.implied_vol(0.5, 100.0, 80.0, 45 / 365.25, _R, "C") is None  # intrinsic=20
    # t_years <= 0
    assert greeks.implied_vol(5.0, 100.0, 100.0, 0.0, _R, "C") is None
    assert greeks.implied_vol(5.0, 100.0, 100.0, -1.0, _R, "C") is None
    # no sign change in [1e-4, 5.0] bracket -- price far above any achievable BS price
    assert greeks.implied_vol(999.0, 100.0, 100.0, 45 / 365.25, _R, "C") is None


def test_bs_price_intrinsic_at_expiry():
    assert greeks.bs_price(110.0, 100.0, 0.0, _R, 0.25, "C") == 10.0
    assert greeks.bs_price(90.0, 100.0, 0.0, _R, 0.25, "C") == 0.0
    assert greeks.bs_price(90.0, 100.0, 0.0, _R, 0.25, "P") == 10.0
    assert greeks.bs_price(110.0, 100.0, 0.0, _R, 0.25, "P") == 0.0
    # sigma <= 0 also falls back to intrinsic, never raises
    assert greeks.bs_price(110.0, 100.0, 45 / 365.25, _R, 0.0, "C") == 10.0
    assert greeks.bs_price(110.0, 100.0, 45 / 365.25, _R, -0.1, "C") == 10.0


def test_dte_to_years_matches_option_dte():
    expiry = date(2026, 9, 18)
    today = date(2026, 8, 17)
    assert greeks.dte_to_years(expiry, today) == option_dte(expiry, today) / 365.25
    # floors at 0 for a past expiry
    past_expiry = date(2026, 1, 1)
    assert greeks.dte_to_years(past_expiry, today) == 0.0
    assert option_dte(past_expiry, today) < 0


# ============================================================
# T-09-05: atm_iv + iv_rank (D-10)
# ============================================================

def _row(right, strike, expiry, dte, close):
    return {"right": right, "strike": strike, "expiry": expiry, "dte": dte, "close": close}


def test_atm_iv_picks_closest_expiry_and_averages_call_put():
    today = date(2026, 8, 17)
    near_expiry = date(2026, 10, 1)  # dte ~45
    far_expiry = date(2026, 12, 1)  # dte ~106
    near_dte = (near_expiry - today).days
    far_dte = (far_expiry - today).days
    t_years = greeks.dte_to_years(near_expiry, today)
    sigma = 0.30
    call_price = greeks.bs_price(_SPOT, 100.0, t_years, _R, sigma, "C")
    put_price = greeks.bs_price(_SPOT, 100.0, t_years, _R, sigma, "P")
    rows = [
        _row("C", 100.0, near_expiry, near_dte, call_price),
        _row("P", 100.0, near_expiry, near_dte, put_price),
        # far expiry present too -- must be ignored since near is closer to target_dte=45
        _row("C", 100.0, far_expiry, far_dte, 50.0),
        _row("P", 100.0, far_expiry, far_dte, 50.0),
    ]
    iv = greeks.atm_iv(rows, _SPOT, today.isoformat(), _R, target_dte=45)
    assert iv is not None
    assert abs(iv - sigma) < 1e-4


def test_atm_iv_returns_none_on_missing_side_or_empty():
    today = date(2026, 8, 17)
    expiry = date(2026, 10, 1)
    dte = (expiry - today).days
    # only a call present, no put -- fails closed
    rows = [_row("C", 100.0, expiry, dte, 3.0)]
    assert greeks.atm_iv(rows, _SPOT, today.isoformat(), _R, target_dte=45) is None
    assert greeks.atm_iv([], _SPOT, today.isoformat(), _R, target_dte=45) is None


def test_ivr_fixture():
    # hand-built 60-observation series (meets IV_RANK_MIN_OBS), min=0.10 max=0.50
    series = [0.10 + 0.40 * (i / 59) for i in range(60)]  # ramps 0.10 -> 0.50
    expected = (series[-1] - min(series)) / (max(series) - min(series)) * 100.0
    assert greeks.iv_rank(series) == pytest.approx(expected)


def test_ivr_zero_at_trailing_min_hundred_at_trailing_max():
    series = [0.30] * 59 + [0.10]  # today is the trailing minimum
    assert greeks.iv_rank(series) == pytest.approx(0.0)
    series = [0.30] * 59 + [0.50]  # today is the trailing maximum
    assert greeks.iv_rank(series) == pytest.approx(100.0)


def test_ivr_none_below_min_obs():
    series = [0.20 + 0.01 * i for i in range(greeks.IV_RANK_MIN_OBS - 1)]
    assert greeks.iv_rank(series) is None


def test_ivr_none_when_flat():
    series = [0.25] * 100
    assert greeks.iv_rank(series) is None


def test_ivr_only_trailing_window_counts():
    # an extreme value older than `window` back must not affect the result
    old_extreme = [5.0] + [0.20] * (greeks.IV_RANK_WINDOW - 1)
    within_window = old_extreme + [0.30]  # window=252 -> the 5.0 just fell out
    assert len(within_window) == greeks.IV_RANK_WINDOW + 1
    ivr = greeks.iv_rank(within_window, window=greeks.IV_RANK_WINDOW)
    trailing = within_window[-greeks.IV_RANK_WINDOW:]
    expected = (trailing[-1] - min(trailing)) / (max(trailing) - min(trailing)) * 100.0
    assert ivr == pytest.approx(expected)
    assert 5.0 not in trailing


def test_ivr_matches_live_passes_entry_gate_units():
    cfg = load_options_config("rules_options.json")
    assert cfg.ivr_min == 30
    # Build a min-max series whose final value normalizes to exactly 31 and 29.
    base = [0.0] * (greeks.IV_RANK_MIN_OBS - 1) + [1.0]  # min=0, max=1
    series_31 = base + [0.31]
    series_29 = base + [0.29]
    ivr_31 = greeks.iv_rank(series_31)
    ivr_29 = greeks.iv_rank(series_29)
    assert ivr_31 == pytest.approx(31.0)
    assert ivr_29 == pytest.approx(29.0)
    assert passes_entry_gate({"ivr_pct": ivr_31, "ivp_pct": None, "change_pct": 0.0}, cfg) is True
    assert passes_entry_gate({"ivr_pct": ivr_29, "ivp_pct": None, "change_pct": 0.0}, cfg) is False
