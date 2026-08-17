#!/usr/bin/env python3
"""
tests/options/test_strategy.py — Tests for bot/options/strategy.py

One class per exported function. Every threshold is varied with
dataclasses.replace(options_cfg, ...) rather than by editing the fixture file,
which proves the values are genuinely config-driven (CFG-01) and not literals
baked into strategy.py.

The chain fixtures are a hand-built 1.0-spaced strike grid around an underlying
at 100 with explicit deltas and mids, so the 0.16-delta pick and the
"at least one strike away" wing are unambiguous by construction rather than by
floating-point luck.
"""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from bot.options.strategy import (
    is_monthly_expiry,
    leg_is_liquid,
    manage_decision,
    mark_spread,
    option_dte,
    passes_entry_gate,
    pick_expiry,
    pick_strikes,
    size_position,
)


# ============================================================
# Chain grid helpers (local to this module — no I/O, no fixtures file)
# ============================================================

UNDERLYING_PX = 100.0

# |delta| falls off as the strike moves away from the money. 0.16 sits alone at
# 95 (puts) / 105 (calls); its nearest rivals are 0.05 away.
_PUT_DELTAS = {
    99: -0.45, 98: -0.38, 97: -0.30, 96: -0.23, 95: -0.16,
    94: -0.11, 93: -0.07, 92: -0.05, 91: -0.03, 90: -0.02,
}
_CALL_DELTAS = {
    101: 0.45, 102: 0.38, 103: 0.30, 104: 0.23, 105: 0.16,
    106: 0.11, 107: 0.07, 108: 0.05, 109: 0.03, 110: 0.02,
}
# Mids chosen so the default structure clears the min-credit filter:
# shorts 0.50 + 0.50, longs 0.20 + 0.20 -> credit 0.60 on a 1.0-wide spread.
_MIDS = {
    99: 2.00, 98: 1.60, 97: 1.20, 96: 0.85, 95: 0.50,
    94: 0.20, 93: 0.12, 92: 0.08, 91: 0.05, 90: 0.03,
    101: 2.00, 102: 1.60, 103: 1.20, 104: 0.85, 105: 0.50,
    106: 0.20, 107: 0.12, 108: 0.08, 109: 0.05, 110: 0.03,
}
_HALF_SPREAD = 0.01


def _row(strike, right, oi=1000, mid=None):
    """Build one chain-row dict for the given listed strike."""
    mid = _MIDS[strike] if mid is None else mid
    delta = _PUT_DELTAS[strike] if right == "P" else _CALL_DELTAS[strike]
    return {
        "code": f"US.SPY260918{right}{int(strike * 1000):08d}",
        "right": right,
        "strike": float(strike),
        "delta": delta,
        "bid": round(mid - _HALF_SPREAD, 4),
        "ask": round(mid + _HALF_SPREAD, 4),
        "open_interest": oi,
    }


def _grid(overrides=None):
    """Full 90-110 chain. overrides maps (strike, right) -> field overrides."""
    overrides = overrides or {}
    rows = [_row(k, "P") for k in _PUT_DELTAS] + [_row(k, "C") for k in _CALL_DELTAS]
    for row in rows:
        row.update(overrides.get((int(row["strike"]), row["right"]), {}))
    return rows


@pytest.fixture
def grid_cfg(options_cfg):
    """The shipped config with the spread gate relaxed to fit the test grid.

    The grid's fixed $0.04 spreads are ~8-20% of mid at the strikes that matter,
    which real penny-wide SPY contracts beat easily but the shipped 5% gate does
    not. Only max_spread_pct_of_mid is relaxed; every other knob is the default.
    """
    # The grid was designed around a 0.16-delta short and a 1-point wing on a
    # $100 underlying, so those two geometry knobs are pinned here too (the
    # shipped defaults moved to 0.20 delta / $2 wing floor after the live UAT).
    return replace(
        options_cfg, max_spread_pct_of_mid=30.0,
        short_delta=0.16, min_wing_width_usd=0.0, min_credit_to_width=0.33,
    )


def _by_side(result):
    return [leg["side"] for leg in result["legs"]]


# ============================================================
# is_monthly_expiry / option_dte
# ============================================================

class TestIsMonthlyExpiry:
    """Third Friday detection (monthlies win pick_expiry ties)."""

    def test_third_friday_is_monthly(self):
        assert date(2026, 9, 18).weekday() == 4  # sanity: it really is a Friday
        assert is_monthly_expiry(date(2026, 9, 18)) is True

    def test_fourth_friday_weekly_is_not_monthly(self):
        assert is_monthly_expiry(date(2026, 9, 25)) is False

    def test_first_friday_is_not_monthly(self):
        assert is_monthly_expiry(date(2026, 9, 4)) is False

    def test_non_friday_is_not_monthly(self):
        assert is_monthly_expiry(date(2026, 9, 17)) is False  # Thursday


class TestOptionDte:
    def test_calendar_days_to_expiry(self):
        assert option_dte(date(2026, 9, 18), date(2026, 8, 4)) == 45

    def test_expired_contract_is_negative(self):
        assert option_dte(date(2026, 8, 1), date(2026, 8, 4)) == -3


# ============================================================
# pick_expiry
# ============================================================

class TestPickExpiry:
    """Closest to target DTE inside the window; monthlies break ties."""

    TODAY = date(2026, 8, 4)

    def _candidates(self, dtes):
        return [(self.TODAY + timedelta(days=d), d) for d in dtes]

    def test_picks_dte_closest_to_target(self, options_cfg):
        """From 20/38/45/52/75 with min 30 / max 60 / target 45 -> the 45."""
        chosen = pick_expiry(self._candidates([20, 38, 45, 52, 75]), self.TODAY, options_cfg)
        assert chosen == self.TODAY + timedelta(days=45)

    def test_out_of_window_candidates_ignored(self, options_cfg):
        """Only 38 is in [30, 60] even though 20 is nearer nothing in particular."""
        chosen = pick_expiry(self._candidates([20, 38, 75]), self.TODAY, options_cfg)
        assert chosen == self.TODAY + timedelta(days=38)

    def test_returns_none_when_nothing_in_window(self, options_cfg):
        assert pick_expiry(self._candidates([10, 20, 90]), self.TODAY, options_cfg) is None

    def test_returns_none_for_empty_chain(self, options_cfg):
        assert pick_expiry([], self.TODAY, options_cfg) is None

    def test_past_expiries_dropped(self, options_cfg):
        """A stale row with a perfect DTE must not be selected."""
        stale = (date(2026, 7, 1), 45)
        good = (self.TODAY + timedelta(days=40), 40)
        assert pick_expiry([stale, good], self.TODAY, options_cfg) == good[0]

    def test_monthly_wins_tie_when_prefer_monthly(self, options_cfg):
        """Both 1 day from target: the third Friday wins, despite being listed second.

        The dte comes from the caller (the broker's own day count), so it is set
        explicitly here rather than derived from the dates.
        """
        weekly = (date(2026, 10, 2), 46)
        monthly = (date(2026, 9, 18), 44)
        assert pick_expiry([weekly, monthly], self.TODAY, options_cfg) == monthly[0]

    def test_earlier_date_wins_tie_when_prefer_monthly_off(self, options_cfg):
        """With prefer_monthly False the monthly has no privilege — pure config knob;
        an exact |dte-target| tie is broken by the earlier date (deterministic)."""
        weekly = (date(2026, 10, 2), 46)
        monthly = (date(2026, 9, 18), 44)
        cfg = replace(options_cfg, prefer_monthly=False)
        assert pick_expiry([weekly, monthly], self.TODAY, cfg) == monthly[0]
        later_weekly = (date(2026, 10, 2), 44)
        earlier_weekly = (date(2026, 9, 16), 46)
        assert pick_expiry([later_weekly, earlier_weekly], self.TODAY, cfg) == earlier_weekly[0]

    def test_monthly_preferred_even_when_a_weekly_is_closer_to_target(self, options_cfg):
        """Live UAT 2026-08-17: Wed 2026-09-30 (dte 44) beat the monthlies on
        |dte-45| but its wings were illiquid (OI 41-74). With prefer_monthly a
        monthly inside the window must win outright, closest monthly to target."""
        wed = (date(2026, 9, 30), 44)
        sep_monthly = (date(2026, 9, 18), 32)
        oct_monthly = (date(2026, 10, 16), 60)
        assert pick_expiry([wed, sep_monthly, oct_monthly], self.TODAY, options_cfg) == sep_monthly[0]

    def test_falls_back_to_closest_when_no_monthly_in_window(self, options_cfg):
        wed = (date(2026, 9, 30), 44)
        fri_weekly = (date(2026, 9, 25), 39)
        assert pick_expiry([wed, fri_weekly], self.TODAY, options_cfg) == wed[0]

    def test_window_bounds_are_inclusive(self, options_cfg):
        chosen = pick_expiry(self._candidates([30, 61]), self.TODAY, options_cfg)
        assert chosen == self.TODAY + timedelta(days=30)


# ============================================================
# passes_entry_gate
# ============================================================

class TestPassesEntryGate:
    """Hard IVR gate, with the sell-into-fear override and optional IVP dual gate."""

    def test_ivr_above_min_passes(self, options_cfg):
        assert passes_entry_gate({"ivr_pct": 35, "ivp_pct": 40, "change_pct": 0.5}, options_cfg) is True

    def test_ivr_below_min_fails(self, options_cfg):
        assert passes_entry_gate({"ivr_pct": 25, "ivp_pct": 40, "change_pct": 0.5}, options_cfg) is False

    def test_ivr_exactly_at_min_passes(self, options_cfg):
        assert passes_entry_gate({"ivr_pct": 30, "ivp_pct": 40, "change_pct": 0.0}, options_cfg) is True

    def test_fear_drop_lowers_the_threshold(self, options_cfg):
        """A -2.5% day drops the gate to fear_ivr_min (20), so IVR 25 now passes."""
        u = {"ivr_pct": 25, "ivp_pct": 40, "change_pct": -2.5}
        assert passes_entry_gate(u, options_cfg) is True

    def test_fear_threshold_still_has_a_floor(self, options_cfg):
        u = {"ivr_pct": 15, "ivp_pct": 40, "change_pct": -2.5}
        assert passes_entry_gate(u, options_cfg) is False

    def test_small_drop_does_not_trigger_fear_knob(self, options_cfg):
        u = {"ivr_pct": 25, "ivp_pct": 40, "change_pct": -1.0}
        assert passes_entry_gate(u, options_cfg) is False

    def test_fear_drop_pct_is_config_driven(self, options_cfg):
        u = {"ivr_pct": 25, "ivp_pct": 40, "change_pct": -1.0}
        assert passes_entry_gate(u, replace(options_cfg, fear_drop_pct=0.5)) is True

    def test_ivp_dual_gate_can_veto_a_passing_ivr(self, options_cfg):
        cfg = replace(options_cfg, ivp_min=50)
        u = {"ivr_pct": 35, "ivp_pct": 40, "change_pct": 0.0}
        assert passes_entry_gate(u, cfg) is False

    def test_ivp_dual_gate_passes_when_both_clear(self, options_cfg):
        cfg = replace(options_cfg, ivp_min=50)
        u = {"ivr_pct": 35, "ivp_pct": 55, "change_pct": 0.0}
        assert passes_entry_gate(u, cfg) is True

    def test_missing_ivp_fails_closed_when_dual_gate_on(self, options_cfg):
        cfg = replace(options_cfg, ivp_min=50)
        assert passes_entry_gate({"ivr_pct": 35, "ivp_pct": None, "change_pct": 0.0}, cfg) is False
        assert passes_entry_gate({"ivr_pct": 35, "change_pct": 0.0}, cfg) is False

    def test_missing_ivp_is_fine_when_dual_gate_off(self, options_cfg):
        assert passes_entry_gate({"ivr_pct": 35, "ivp_pct": None, "change_pct": 0.0}, options_cfg) is True

    def test_missing_ivr_fails_closed(self, options_cfg):
        assert passes_entry_gate({"ivp_pct": 90, "change_pct": -5.0}, options_cfg) is False


# ============================================================
# leg_is_liquid
# ============================================================

class TestLegIsLiquid:
    """Bid > 0, OI floor, and a spread no wider than the configured percent."""

    def _row(self, bid, ask, oi=1000):
        return {"bid": bid, "ask": ask, "open_interest": oi}

    def test_liquid_row_passes(self, options_cfg):
        assert leg_is_liquid(self._row(1.00, 1.04), options_cfg) is True

    def test_zero_bid_fails(self, options_cfg):
        assert leg_is_liquid(self._row(0.0, 0.10), options_cfg) is False

    def test_negative_bid_fails(self, options_cfg):
        assert leg_is_liquid(self._row(-0.5, 0.10), options_cfg) is False

    def test_open_interest_below_min_fails(self, options_cfg):
        assert leg_is_liquid(self._row(1.00, 1.04, oi=499), options_cfg) is False

    def test_open_interest_exactly_at_min_passes(self, options_cfg):
        assert leg_is_liquid(self._row(1.00, 1.04, oi=500), options_cfg) is True

    def test_wide_spread_fails(self, options_cfg):
        """0.20 wide on a 1.10 mid is ~18% — well over the 5% gate."""
        assert leg_is_liquid(self._row(1.00, 1.20), options_cfg) is False

    def test_spread_gate_is_config_driven(self, options_cfg):
        row = self._row(1.00, 1.20)
        assert leg_is_liquid(row, replace(options_cfg, max_spread_pct_of_mid=25.0)) is True

    def test_min_open_interest_is_config_driven(self, options_cfg):
        row = self._row(1.00, 1.04, oi=100)
        assert leg_is_liquid(row, replace(options_cfg, min_open_interest=50)) is True

    def test_cheap_wing_passes_via_absolute_floor(self, options_cfg):
        """0.05 wide on a 0.325 mid is ~15% — fails the % gate but is nickel-wide,
        which the absolute floor (0.05) accepts. Far-OTM wings live here."""
        assert leg_is_liquid(self._row(0.30, 0.35), options_cfg) is True

    def test_cheap_wing_fails_when_wider_than_absolute_floor(self, options_cfg):
        assert leg_is_liquid(self._row(0.30, 0.36), options_cfg) is False

    def test_absolute_floor_is_config_driven(self, options_cfg):
        row = self._row(0.30, 0.36)
        assert leg_is_liquid(row, replace(options_cfg, max_spread_abs_usd=0.06)) is True

    def test_crossed_quote_fails(self, options_cfg):
        assert leg_is_liquid(self._row(1.10, 1.00), options_cfg) is False

    def test_missing_fields_fail_closed(self, options_cfg):
        assert leg_is_liquid({}, options_cfg) is False


# ============================================================
# pick_strikes
# ============================================================

class TestPickStrikesIronCondor:
    """Four legs, delta-picked shorts, listed-strike wings, long wings first."""

    def test_returns_four_legs_with_wings_first(self, grid_cfg):
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        assert result is not None
        assert len(result["legs"]) == 4
        assert _by_side(result) == ["BUY", "BUY", "SELL", "SELL"]

    def test_shorts_are_the_sixteen_delta_strikes(self, grid_cfg):
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        shorts = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "SELL"}
        assert shorts == {"P": 95.0, "C": 105.0}

    def test_short_delta_is_config_driven(self, grid_cfg):
        """Asking for 0.30 delta moves the shorts out to the 97 / 103 strikes."""
        cfg = replace(grid_cfg, short_delta=0.30, min_credit_to_width=0.0)
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg)
        shorts = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "SELL"}
        assert shorts == {"P": 97.0, "C": 103.0}

    def test_wings_are_one_width_from_the_shorts(self, grid_cfg):
        """1% of 100 = 1.0 wide; on a 1.0 grid that is the adjacent strike."""
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        wings = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "BUY"}
        assert wings == {"P": 94.0, "C": 106.0}

    def test_wing_width_is_config_driven(self, grid_cfg):
        """3% of 100 = 3.0 wide -> 92 / 108 wings (a 3x wider spread needs 3x
        the credit to clear the shipped ratio, so that gate is relaxed here)."""
        cfg = replace(grid_cfg, wing_width_pct_of_underlying=3.0, min_credit_to_width=0.0)
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg)
        wings = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "BUY"}
        assert wings == {"P": 92.0, "C": 108.0}

    def test_wing_width_dollar_floor_wins_over_percent(self, grid_cfg):
        """1% of 100 = 1.0, but a $3 floor makes the wings 3 away (92 / 108).
        Live UAT 2026-08-17: XLE at $62 → 1% = one $0.50 strike → 34-lot condors."""
        cfg = replace(grid_cfg, wing_width_pct_of_underlying=1.0, min_wing_width_usd=3.0,
                      min_credit_to_width=0.0)
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg)
        wings = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "BUY"}
        assert wings == {"P": 92.0, "C": 108.0}

    def test_wing_is_never_the_short_strike_itself(self, grid_cfg):
        """A sub-tick wing width must still snap at least one strike away."""
        cfg = replace(grid_cfg, wing_width_pct_of_underlying=0.1, min_credit_to_width=0.0)
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg)
        wings = {leg["right"]: leg["strike"] for leg in result["legs"] if leg["side"] == "BUY"}
        assert wings == {"P": 94.0, "C": 106.0}

    def test_credit_is_shorts_minus_longs(self, grid_cfg):
        """(0.50 + 0.50) - (0.20 + 0.20) = 0.60."""
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        assert result["credit"] == pytest.approx(0.60)

    def test_width_is_the_max_vertical(self, grid_cfg):
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        assert result["width"] == pytest.approx(1.0)

    def test_legs_carry_code_right_strike_side_mid(self, grid_cfg):
        result = pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", grid_cfg)
        for leg in result["legs"]:
            assert set(leg) == {"code", "right", "strike", "side", "mid"}
            assert leg["code"].startswith("US.SPY")


class TestPickStrikesRejects:
    """The None paths: thin credit and illiquid legs."""

    def test_none_when_credit_below_min_credit_to_width(self, grid_cfg):
        """0.60 credit on a 1.0 width is 0.60 — under a 0.90 requirement."""
        cfg = replace(grid_cfg, min_credit_to_width=0.90)
        assert pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg) is None

    def test_credit_exactly_at_the_floor_is_accepted(self, grid_cfg):
        cfg = replace(grid_cfg, min_credit_to_width=0.60)
        assert pick_strikes(_grid(), UNDERLYING_PX, "iron_condor", cfg) is not None

    def test_none_when_short_leg_is_illiquid(self, grid_cfg):
        """Thin OI on the chosen short put kills the whole structure."""
        rows = _grid({(95, "P"): {"open_interest": 10}})
        assert pick_strikes(rows, UNDERLYING_PX, "iron_condor", grid_cfg) is None

    def test_none_when_long_wing_is_illiquid(self, grid_cfg):
        rows = _grid({(106, "C"): {"bid": 0.0, "ask": 0.40}})
        assert pick_strikes(rows, UNDERLYING_PX, "iron_condor", grid_cfg) is None

    def test_illiquid_unselected_strike_is_harmless(self, grid_cfg):
        """A thin strike nobody picked must not veto a valid structure."""
        rows = _grid({(90, "P"): {"open_interest": 1}})
        assert pick_strikes(rows, UNDERLYING_PX, "iron_condor", grid_cfg) is not None

    def test_none_when_no_puts_available(self, grid_cfg):
        calls_only = [r for r in _grid() if r["right"] == "C"]
        assert pick_strikes(calls_only, UNDERLYING_PX, "iron_condor", grid_cfg) is None

    def test_none_when_no_call_wing_above_the_short(self, grid_cfg):
        """Truncated chain: the 0.16-delta call is the highest listed strike."""
        rows = [r for r in _grid() if not (r["right"] == "C" and r["strike"] > 105)]
        assert pick_strikes(rows, UNDERLYING_PX, "iron_condor", grid_cfg) is None

    def test_none_when_no_put_wing_below_the_short(self, grid_cfg):
        rows = [r for r in _grid() if not (r["right"] == "P" and r["strike"] < 95)]
        assert pick_strikes(rows, UNDERLYING_PX, "iron_condor", grid_cfg) is None

    def test_unknown_structure_raises(self, grid_cfg):
        with pytest.raises(ValueError):
            pick_strikes(_grid(), UNDERLYING_PX, "strangle", grid_cfg)


class TestPickStrikesPutCreditSpread:
    """Two legs only, BUY wing before SELL short, no call side."""

    def test_two_legs_buy_then_sell(self, grid_cfg):
        cfg = replace(grid_cfg, min_credit_to_width=0.25)
        result = pick_strikes(_grid(), UNDERLYING_PX, "put_credit_spread", cfg)
        assert result is not None
        assert len(result["legs"]) == 2
        assert _by_side(result) == ["BUY", "SELL"]

    def test_only_puts_are_used(self, grid_cfg):
        cfg = replace(grid_cfg, min_credit_to_width=0.25)
        result = pick_strikes(_grid(), UNDERLYING_PX, "put_credit_spread", cfg)
        assert {leg["right"] for leg in result["legs"]} == {"P"}
        assert [leg["strike"] for leg in result["legs"]] == [94.0, 95.0]

    def test_credit_and_width(self, grid_cfg):
        cfg = replace(grid_cfg, min_credit_to_width=0.25)
        result = pick_strikes(_grid(), UNDERLYING_PX, "put_credit_spread", cfg)
        assert result["credit"] == pytest.approx(0.30)
        assert result["width"] == pytest.approx(1.0)

    def test_none_when_credit_below_floor(self, grid_cfg):
        """0.30 credit on a 1.0 width fails the shipped 0.33 requirement."""
        assert pick_strikes(_grid(), UNDERLYING_PX, "put_credit_spread", grid_cfg) is None


# ============================================================
# size_position
# ============================================================

class TestSizePosition:
    """Whole spreads only, floored by the risk budget and capped by BP usage."""

    def test_floors_to_whole_spreads(self, options_cfg):
        """$100k x 1% = $1,000 budget; risk (5 - 1.7) x 100 = $330 -> 3 spreads."""
        assert size_position(5, 1.7, options_cfg, 0) == 3

    def test_zero_when_one_spread_exceeds_the_budget(self, options_cfg):
        """Risk (20 - 0.5) x 100 = $1,950 > the $1,000 budget."""
        assert size_position(20, 0.5, options_cfg, 0) == 0

    def test_zero_when_risk_is_not_positive(self, options_cfg):
        assert size_position(5, 5.0, options_cfg, 0) == 0
        assert size_position(5, 7.0, options_cfg, 0) == 0

    def test_bp_cap_reduces_qty_below_the_risk_derived_number(self, options_cfg):
        """$25k BP ceiling with $24.4k already at risk leaves room for 1 of the 3."""
        assert size_position(5, 1.7, options_cfg, 24_400) == 1

    def test_bp_cap_never_returns_negative(self, options_cfg):
        assert size_position(5, 1.7, options_cfg, 30_000) == 0

    def test_bp_cap_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, max_bp_usage_pct=50)
        assert size_position(5, 1.7, cfg, 24_400) == 3

    def test_risk_budget_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, max_risk_per_trade_pct=2.0)
        assert size_position(5, 1.7, cfg, 0) == 6

    def test_sizing_equity_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, sizing_equity_usd=10_000)
        assert size_position(5, 1.7, cfg, 0) == 0


# ============================================================
# mark_spread
# ============================================================

class TestMarkSpread:
    """Cost to close = short mids bought back minus long mids sold."""

    LEGS = [
        {"code": "P94", "side": "BUY"},
        {"code": "C106", "side": "BUY"},
        {"code": "P95", "side": "SELL"},
        {"code": "C105", "side": "SELL"},
    ]

    def _quotes(self, p94, c106, p95, c105):
        return {
            "P94": {"bid": p94 - 0.02, "ask": p94 + 0.02},
            "C106": {"bid": c106 - 0.02, "ask": c106 + 0.02},
            "P95": {"bid": p95 - 0.02, "ask": p95 + 0.02},
            "C105": {"bid": c105 - 0.02, "ask": c105 + 0.02},
        }

    def test_mark_is_shorts_minus_longs(self):
        mark = mark_spread(self.LEGS, self._quotes(0.20, 0.20, 0.50, 0.50))
        assert mark == pytest.approx(0.60)

    def test_mark_falls_as_the_spread_decays(self):
        """Half the premium gone -> mark is half the opening credit."""
        mark = mark_spread(self.LEGS, self._quotes(0.10, 0.10, 0.25, 0.25))
        assert mark == pytest.approx(0.30)

    def test_two_leg_spread(self):
        legs = [{"code": "P94", "side": "BUY"}, {"code": "P95", "side": "SELL"}]
        quotes = {"P94": {"bid": 0.18, "ask": 0.22}, "P95": {"bid": 0.48, "ask": 0.52}}
        assert mark_spread(legs, quotes) == pytest.approx(0.30)


# ============================================================
# manage_decision
# ============================================================

class TestManageDecision:
    """Strict priority: assignment_guard > profit_target > stop_loss > dte_exit."""

    def test_none_when_nothing_is_met(self, options_cfg):
        assert manage_decision(mark=0.55, credit=0.60, dte=40, cfg=options_cfg) is None

    def test_assignment_guard_outranks_profit_target(self, options_cfg):
        """At 1 DTE the guard fires even though the 50% target is also met."""
        assert manage_decision(mark=0.10, credit=0.60, dte=1, cfg=options_cfg) == "assignment_guard"

    def test_assignment_guard_outranks_dte_exit(self, options_cfg):
        assert manage_decision(mark=0.60, credit=0.60, dte=0, cfg=options_cfg) == "assignment_guard"

    def test_profit_target_at_exactly_fifty_percent(self, options_cfg):
        assert manage_decision(mark=0.30, credit=0.60, dte=40, cfg=options_cfg) == "profit_target"

    def test_profit_target_not_met_just_short(self, options_cfg):
        assert manage_decision(mark=0.31, credit=0.60, dte=40, cfg=options_cfg) is None

    def test_profit_target_outranks_stop_loss(self, options_cfg):
        """A degenerate config where both fire: the win books first."""
        cfg = replace(options_cfg, stop_loss_credit_multiple=-1.0)
        assert manage_decision(mark=0.30, credit=0.60, dte=40, cfg=cfg) == "profit_target"

    def test_stop_loss_when_enabled(self, options_cfg):
        """Mark at 3x credit clears a 2x stop; dte_exit would also apply."""
        cfg = replace(options_cfg, stop_loss_credit_multiple=2.0)
        assert manage_decision(mark=1.80, credit=0.60, dte=21, cfg=cfg) == "stop_loss"

    def test_stop_loss_never_fires_when_disabled(self, options_cfg):
        """Shipped default is None — the loss rides to the DTE exit instead."""
        assert manage_decision(mark=1.80, credit=0.60, dte=21, cfg=options_cfg) == "dte_exit"

    def test_stop_loss_disabled_falls_through_to_none(self, options_cfg):
        assert manage_decision(mark=1.80, credit=0.60, dte=40, cfg=options_cfg) is None

    def test_stop_loss_multiple_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, stop_loss_credit_multiple=4.0)
        assert manage_decision(mark=1.80, credit=0.60, dte=40, cfg=cfg) is None

    def test_dte_exit_at_manage_dte(self, options_cfg):
        assert manage_decision(mark=0.55, credit=0.60, dte=21, cfg=options_cfg) == "dte_exit"

    def test_dte_exit_not_yet_at_22(self, options_cfg):
        assert manage_decision(mark=0.55, credit=0.60, dte=22, cfg=options_cfg) is None

    def test_manage_dte_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, manage_dte=30)
        assert manage_decision(mark=0.55, credit=0.60, dte=30, cfg=cfg) == "dte_exit"

    def test_assignment_guard_dte_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, assignment_guard_dte=5)
        assert manage_decision(mark=0.55, credit=0.60, dte=5, cfg=cfg) == "assignment_guard"

    def test_profit_target_pct_is_config_driven(self, options_cfg):
        cfg = replace(options_cfg, profit_target_pct_of_credit=25)
        assert manage_decision(mark=0.55, credit=0.60, dte=40, cfg=options_cfg) is None
        assert manage_decision(mark=0.40, credit=0.60, dte=40, cfg=cfg) == "profit_target"
