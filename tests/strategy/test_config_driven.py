#!/usr/bin/env python3
"""
tests.strategy.test_config_driven — Tests for StrategyCore ABC, TrendJoinLong,
and D-12 behavioral config-drivenness proof.

Covers:
- StrategyCore is an ABC with 4 abstract methods
- TrendJoinLong passes_daily_filters: True for canonical setup, False when any
  single condition fails
- TrendJoinLong passes_intraday_filters: True when all I1/I2/I3 pass, False when any fails
- TrendJoinLong compute_initial_stop: equals LOD * (1 - risk_pct/100), not hardcoded 0.99
- D-12 config-drivenness: same synthetic bar yields different output under baseline
  vs modified config (proves no hardcoded strategy literal in TrendJoinLong)
- grep-based: TrendJoinLong uses self._cfg. for params, not bare numeric literals

No network calls, no I/O.
"""
import abc
import inspect
import math
from dataclasses import dataclass, replace as dataclass_replace
from typing import Optional

import pandas as pd
import pytest

from bot.config.loader import StrategyConfig
from bot.strategy.core import StrategyCore
from bot.strategy.trend_join_long import TrendJoinLong


# ============================================================
# Execution defaults (Phase 4 — canonical rules.json execution block)
# ============================================================
#
# StrategyConfig gained 10 required execution fields in Phase 4 (04-01).
# These strategy tests don't exercise execution behavior, so every
# StrategyConfig(...) call spreads these canonical defaults to satisfy the
# constructor. Values match the execution block in rules.json exactly.
_EXECUTION_DEFAULTS = {
    "entry_limit_buffer_usd": 0.05,
    "entry_ttl_seconds": 20,
    "entry_max_retries": 2,
    "entry_poll_interval_seconds": 5,
    "exit_limit_buffer_usd": 0.05,
    "exit_ttl_seconds": 15,
    "exit_escalation_step_usd": 0.10,
    "exit_escalation_cadence_seconds": 10,
    "force_close_escalation_step_usd": 0.20,
    "force_close_escalation_cadence_seconds": 15,
}

# ============================================================
# Service defaults (Phase 5 — canonical rules.json service block)
# ============================================================
#
# StrategyConfig gained 15 required service fields in Phase 5 (05-00).
# These tests don't exercise service/scheduler behavior, so every
# StrategyConfig(...) call spreads these canonical defaults to satisfy the
# constructor. Values match the service block in rules.json exactly.
_SERVICE_DEFAULTS = {
    "premarket_scan_et": "08:30",
    "market_open_et": "09:30",
    "intraday_rescan_interval_min": 30,
    "intraday_rescan_start_et": "09:55",
    "intraday_rescan_end_et": "12:55",
    "eod_report_et": "15:55",
    "watchdog_poll_interval_s": 60,
    "watchdog_reconnect_initial_s": 5,
    "watchdog_reconnect_cap_s": 60,
    "alerts_enabled": True,
    "misfire_grace_scan_s": 3600,
    "misfire_grace_rescan_s": 600,
    "force_close_misfire_grace_s": 300,
    "launchd_throttle_interval_s": 30,
    "crash_loop_alert_threshold": 5,
}


# ============================================================
# Fixtures — canonical StrategyConfig (from rules.json values)
# ============================================================

def _make_canonical_config() -> StrategyConfig:
    """Return a StrategyConfig matching the canonical PROJECT.md values."""
    return StrategyConfig(
        min_price_usd=3.0,
        d3_min_gap_pct=3.0,
        rvol_min=2.0,
        rvol_lookback_days=14,
        earliest_entry_et="10:05",
        latest_entry_et="15:30",
        force_close_et="15:51",
        initial_stop_pct=1.0,
        partial_profit_trigger_r=0.75,
        partial_profit_fraction=0.3333,
        breakeven_trigger_r=1.0,
        max_risk_per_trade_pct=1.0,
        max_position_size_pct=10,
        max_concurrent_positions=5,
        max_trades_per_day=5,
        **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
    )


def _make_daily_df_passing(
    gap_pct: float = 4.0,
    price: float = 50.0,
    prior_close: float = 45.0,
    prior_high: float = 44.0,
    sma200: float = 30.0,
) -> tuple:
    """
    Build a minimal 2-row daily DataFrame that passes all D1/D2/D3 filters
    when combined with the given sma200.

    Row[-2] = prior day: high=prior_high, close=prior_close
    Row[-1] = today: open=prior_close*(1+gap_pct/100), close=price

    Returns (daily_data, sma200).
    """
    today_open = prior_close * (1.0 + gap_pct / 100.0)
    daily_data = pd.DataFrame([
        {"open": prior_close - 2.0, "high": prior_high, "low": prior_close - 5.0, "close": prior_close},
        {"open": today_open, "high": price + 1.0, "low": today_open - 0.5, "close": price},
    ])
    return daily_data, sma200


# ============================================================
# StrategyCore ABC structure tests
# ============================================================

class TestStrategyCoreABC:
    def test_strategy_core_is_abc(self):
        assert issubclass(StrategyCore, abc.ABC)

    def test_passes_daily_filters_is_abstract(self):
        assert getattr(StrategyCore.passes_daily_filters, "__isabstractmethod__", False)

    def test_passes_intraday_filters_is_abstract(self):
        assert getattr(StrategyCore.passes_intraday_filters, "__isabstractmethod__", False)

    def test_compute_initial_stop_is_abstract(self):
        assert getattr(StrategyCore.compute_initial_stop, "__isabstractmethod__", False)

    def test_compute_swing_low_2_2_is_abstract(self):
        assert getattr(StrategyCore.compute_swing_low_2_2, "__isabstractmethod__", False)

    def test_cannot_instantiate_strategy_core(self):
        with pytest.raises(TypeError):
            StrategyCore()


# ============================================================
# TrendJoinLong — passes_daily_filters
# ============================================================

class TestPassesDailyFilters:
    def test_passes_when_all_conditions_met(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        daily_data, sma200 = _make_daily_df_passing()
        assert strat.passes_daily_filters("US.TEST", daily_data, sma200) is True

    def test_fails_d1_when_price_below_prior_high(self):
        """D1: today's close must be above prior-day high."""
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        # prior_high=60.0, today close=50.0 → fails D1
        daily_data, sma200 = _make_daily_df_passing(price=50.0, prior_high=60.0)
        assert strat.passes_daily_filters("US.TEST", daily_data, sma200) is False

    def test_fails_d2_when_prior_close_below_sma200(self):
        """D2: prior close must be above SMA200."""
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        # prior_close=45.0, sma200=50.0 → fails D2
        daily_data, sma200 = _make_daily_df_passing(prior_close=45.0, sma200=50.0, price=55.0, prior_high=44.0)
        assert strat.passes_daily_filters("US.TEST", daily_data, sma200) is False

    def test_fails_d3_when_gap_too_small(self):
        """D3: gap must be >= cfg.d3_min_gap_pct (3.0%)."""
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        # gap=1.0% < 3.0% → fails D3
        daily_data, sma200 = _make_daily_df_passing(gap_pct=1.0, price=50.0, prior_high=44.0)
        assert strat.passes_daily_filters("US.TEST", daily_data, sma200) is False

    def test_fails_when_price_below_min(self):
        """Universe filter: price must be >= cfg.min_price_usd (3.0)."""
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        # price=2.0 < 3.0 → fails price filter
        daily_data, sma200 = _make_daily_df_passing(price=2.0, prior_high=1.5, prior_close=1.8, sma200=1.0)
        assert strat.passes_daily_filters("US.TEST", daily_data, sma200) is False


# ============================================================
# TrendJoinLong — passes_intraday_filters
# ============================================================

class TestPassesIntradayFilters:
    def _make_5m_bars(self, last_close: float) -> pd.DataFrame:
        """Minimal 5m bar DataFrame with a single closed bar."""
        return pd.DataFrame([
            {"open": last_close - 0.1, "high": last_close + 0.1, "low": last_close - 0.2, "close": last_close}
        ])

    def test_passes_when_all_intraday_conditions_met(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # close=50.0 > premarket_high=45.0, hod=50.0, rvol=3.0 >= 2.0
        assert strat.passes_intraday_filters("US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=3.0) is True

    def test_fails_i1_when_below_premarket_high(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(40.0)
        # close=40.0 < premarket_high=45.0 → I1 fails
        assert strat.passes_intraday_filters("US.TEST", bars, premarket_high=45.0, hod=40.0, rvol=3.0) is False

    def test_fails_i2_when_below_hod(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # close=50.0 > premarket_high=45.0 BUT hod=55.0 > close → I2 fails
        assert strat.passes_intraday_filters("US.TEST", bars, premarket_high=45.0, hod=55.0, rvol=3.0) is False

    def test_fails_i3_when_rvol_below_min(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # rvol=1.5 < cfg.rvol_min=2.0 → I3 fails
        assert strat.passes_intraday_filters("US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=1.5) is False

    def test_i3_rvol_honors_config_threshold(self):
        """I3 threshold is read from cfg.rvol_min — changing it changes the outcome."""
        baseline_cfg = _make_canonical_config()  # rvol_min=2.0
        strict_cfg = StrategyConfig(
            min_price_usd=3.0, d3_min_gap_pct=3.0,
            rvol_min=5.0,  # stricter than canonical
            rvol_lookback_days=14, earliest_entry_et="10:05",
            latest_entry_et="15:30", force_close_et="15:51",
            initial_stop_pct=1.0,
            partial_profit_trigger_r=0.75, partial_profit_fraction=0.3333,
            breakeven_trigger_r=1.0, max_risk_per_trade_pct=1.0,
            max_position_size_pct=10, max_concurrent_positions=5,
            max_trades_per_day=5,
            **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
        )
        bars = self._make_5m_bars(50.0)
        baseline_strat = TrendJoinLong(baseline_cfg)
        strict_strat = TrendJoinLong(strict_cfg)
        rvol_value = 3.0  # passes rvol_min=2.0 but fails rvol_min=5.0
        assert baseline_strat.passes_intraday_filters("US.TEST", bars, 45.0, 50.0, rvol_value) is True
        assert strict_strat.passes_intraday_filters("US.TEST", bars, 45.0, 50.0, rvol_value) is False


# ============================================================
# TrendJoinLong — I2 gate mode (cfg.i2_mode, strategy-audit finding)
# ============================================================

def _make_config_with_i2_mode(i2_mode: str) -> StrategyConfig:
    return dataclass_replace(_make_canonical_config(), i2_mode=i2_mode)


class TestI2Mode:
    def _make_5m_bars(self, last_close: float) -> pd.DataFrame:
        return pd.DataFrame([
            {"open": last_close - 0.1, "high": last_close + 0.1, "low": last_close - 0.2, "close": last_close}
        ])

    def test_close_at_hod_is_the_default_mode(self):
        assert _make_canonical_config().i2_mode == "close_at_hod"

    def test_close_at_hod_ignores_hod_prev(self):
        """Default mode: hod_prev is accepted but never consulted."""
        cfg = _make_config_with_i2_mode("close_at_hod")
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # close=50.0 >= hod=50.0 passes I2 regardless of hod_prev.
        assert strat.passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=3.0, hod_prev=None,
        ) is True
        assert strat.passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=3.0, hod_prev=999.0,
        ) is True

    def test_close_above_prior_hod_passes_on_a_breakout_close(self):
        cfg = _make_config_with_i2_mode("close_above_prior_hod")
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # close=50.0 > hod_prev=48.0 -> I2 passes, even though close < hod=51.0
        # (this bar's own high) -- proving the two modes are genuinely different.
        assert strat.passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=51.0, rvol=3.0, hod_prev=48.0,
        ) is True

    def test_close_above_prior_hod_fails_when_not_a_new_high(self):
        cfg = _make_config_with_i2_mode("close_above_prior_hod")
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        # close=50.0 == hod_prev=50.0 -> not a NEW high -> I2 fails (strict >).
        assert strat.passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=3.0, hod_prev=50.0,
        ) is False

    def test_close_above_prior_hod_fails_closed_when_hod_prev_is_none(self):
        """No prior-bar hod (first bar of a session / restart) must fail I2, never
        silently pass or fall back to the other mode."""
        cfg = _make_config_with_i2_mode("close_above_prior_hod")
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        assert strat.passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=3.0, hod_prev=None,
        ) is False

    def test_i2_mode_honors_config_default_when_hod_prev_omitted(self):
        """passes_intraday_filters must default hod_prev=None so existing
        positional/keyword call sites that predate this parameter keep working."""
        cfg = _make_config_with_i2_mode("close_above_prior_hod")
        strat = TrendJoinLong(cfg)
        bars = self._make_5m_bars(50.0)
        assert strat.passes_intraday_filters("US.TEST", bars, 45.0, 50.0, 3.0) is False


# ============================================================
# TrendJoinLong — compute_initial_stop
# ============================================================

class TestComputeInitialStop:
    def test_stop_is_lod_minus_1pct(self):
        """With initial_stop_pct=1.0, stop = lod * 0.99 derived from config."""
        cfg = _make_canonical_config()  # initial_stop_pct=1.0
        strat = TrendJoinLong(cfg)
        lod = 100.0
        result = strat.compute_initial_stop(lod)
        expected = lod * 0.99
        assert abs(result - expected) < 1e-9, f"Expected {expected}, got {result}"

    def test_stop_uses_initial_stop_pct_not_hardcoded(self):
        """
        Changing initial_stop_pct from 1.0 to 2.0 must change compute_initial_stop.
        If the value is hardcoded 0.99, this test will fail (proving config-drivenness).
        """
        cfg_1pct = _make_canonical_config()  # initial_stop_pct=1.0
        cfg_2pct = StrategyConfig(
            min_price_usd=3.0, d3_min_gap_pct=3.0, rvol_min=2.0,
            rvol_lookback_days=14, earliest_entry_et="10:05",
            latest_entry_et="15:30", force_close_et="15:51",
            initial_stop_pct=2.0,  # changed — stop rule, not risk budget
            partial_profit_trigger_r=0.75, partial_profit_fraction=0.3333,
            breakeven_trigger_r=1.0, max_risk_per_trade_pct=1.0,
            max_position_size_pct=10, max_concurrent_positions=5,
            max_trades_per_day=5,
            **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
        )
        lod = 100.0
        result_1pct = TrendJoinLong(cfg_1pct).compute_initial_stop(lod)
        result_2pct = TrendJoinLong(cfg_2pct).compute_initial_stop(lod)
        # 1% → lod*0.99=99.0; 2% → lod*0.98=98.0 — must differ
        assert result_1pct != result_2pct, (
            f"compute_initial_stop must use cfg.initial_stop_pct, not a hardcoded value. "
            f"Got {result_1pct} for both 1% and 2% configs."
        )
        assert abs(result_1pct - 99.0) < 1e-9
        assert abs(result_2pct - 98.0) < 1e-9

    def test_stop_decoupled_from_max_risk_per_trade_pct(self):
        """
        CR-01: the stop is derived from exit.initial_stop_rule (initial_stop_pct),
        NOT from risk.max_risk_per_trade_pct. Changing the risk budget alone must
        NOT move the stop price.
        """
        cfg_risk_1 = _make_canonical_config()  # max_risk_per_trade_pct=1.0, initial_stop_pct=1.0
        cfg_risk_2 = StrategyConfig(
            min_price_usd=3.0, d3_min_gap_pct=3.0, rvol_min=2.0,
            rvol_lookback_days=14, earliest_entry_et="10:05",
            latest_entry_et="15:30", force_close_et="15:51",
            initial_stop_pct=1.0,  # unchanged stop rule
            partial_profit_trigger_r=0.75, partial_profit_fraction=0.3333,
            breakeven_trigger_r=1.0, max_risk_per_trade_pct=2.0,  # only risk budget changed
            max_position_size_pct=10, max_concurrent_positions=5,
            max_trades_per_day=5,
            **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
        )
        lod = 100.0
        result_risk_1 = TrendJoinLong(cfg_risk_1).compute_initial_stop(lod)
        result_risk_2 = TrendJoinLong(cfg_risk_2).compute_initial_stop(lod)
        assert result_risk_1 == result_risk_2, (
            "Stop must NOT change when only max_risk_per_trade_pct changes — "
            "the stop is driven by exit.initial_stop_rule, not the risk budget (CR-01)."
        )
        assert abs(result_risk_1 - 99.0) < 1e-9


# ============================================================
# D-12 Behavioral Config-Drivenness Proof
# ============================================================

class TestConfigDrivenness:
    def test_d12_same_bar_different_config_different_daily_filter_output(self):
        """
        D-12 behavioral proof: instantiate TrendJoinLong with a baseline config
        and a modified config (tighten d3_min_gap_pct and rvol_min).
        Feed the SAME synthetic bar. Assert the boolean output CHANGES.

        This proves that every parameter flows from StrategyConfig, not from
        hardcoded Python literals.
        """
        baseline_cfg = _make_canonical_config()  # d3_min_gap_pct=3.0

        modified_cfg = StrategyConfig(
            min_price_usd=3.0,
            d3_min_gap_pct=6.0,   # tightened — same bar (4% gap) should now fail D3
            rvol_min=5.0,         # tightened — rvol=3.0 now fails I3
            rvol_lookback_days=14, earliest_entry_et="10:05",
            latest_entry_et="15:30", force_close_et="15:51",
            initial_stop_pct=1.0,
            partial_profit_trigger_r=0.75, partial_profit_fraction=0.3333,
            breakeven_trigger_r=1.0, max_risk_per_trade_pct=1.0,
            max_position_size_pct=10, max_concurrent_positions=5,
            max_trades_per_day=5,
            **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
        )

        # A bar with 4% gap that passes the baseline (d3=3%) but fails the modified (d3=6%)
        daily_data, sma200 = _make_daily_df_passing(gap_pct=4.0, price=50.0, prior_high=44.0)

        baseline_strat = TrendJoinLong(baseline_cfg)
        modified_strat = TrendJoinLong(modified_cfg)

        baseline_result = baseline_strat.passes_daily_filters("US.TEST", daily_data, sma200)
        modified_result = modified_strat.passes_daily_filters("US.TEST", daily_data, sma200)

        assert baseline_result is True, "Baseline config should pass for 4% gap"
        assert modified_result is False, "Modified config (6% threshold) should fail for 4% gap"
        # The critical assertion: same bar, different outcome
        assert baseline_result != modified_result, "D-12 PROOF: config change must change filter output"

    def test_d12_rvol_config_change_changes_intraday_output(self):
        """
        D-12 intraday flavor: changing rvol_min flips passes_intraday_filters for borderline rvol.
        """
        baseline_cfg = _make_canonical_config()  # rvol_min=2.0
        strict_cfg = StrategyConfig(
            min_price_usd=3.0, d3_min_gap_pct=3.0,
            rvol_min=5.0,  # borderline rvol of 3.0 now fails
            rvol_lookback_days=14, earliest_entry_et="10:05",
            latest_entry_et="15:30", force_close_et="15:51",
            initial_stop_pct=1.0,
            partial_profit_trigger_r=0.75, partial_profit_fraction=0.3333,
            breakeven_trigger_r=1.0, max_risk_per_trade_pct=1.0,
            max_position_size_pct=10, max_concurrent_positions=5,
            max_trades_per_day=5,
            **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
        )

        bars = pd.DataFrame([
            {"open": 49.9, "high": 51.0, "low": 49.5, "close": 50.0}
        ])
        rvol_borderline = 3.0  # passes rvol_min=2.0, fails rvol_min=5.0

        baseline_result = TrendJoinLong(baseline_cfg).passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=rvol_borderline
        )
        strict_result = TrendJoinLong(strict_cfg).passes_intraday_filters(
            "US.TEST", bars, premarket_high=45.0, hod=50.0, rvol=rvol_borderline
        )

        assert baseline_result is True
        assert strict_result is False


# ============================================================
# Source code checks — no hardcoded strategy literals
# ============================================================

class TestNoHardcodedLiterals:
    def test_trend_join_long_uses_self_cfg_for_params(self):
        """
        TrendJoinLong must access parameters via self._cfg — at least 5 accesses.
        This ensures the config dataclass is actually used.
        """
        import bot.strategy.trend_join_long as tjl_mod
        src = inspect.getsource(tjl_mod)
        count = src.count("self._cfg.")
        assert count >= 5, (
            f"Expected >= 5 uses of self._cfg. in TrendJoinLong, found {count}. "
            "Strategy parameters must flow from config, not be hardcoded."
        )

    def test_trend_join_long_has_no_bare_numeric_strategy_literals(self):
        """
        Spot-check: the bare numeric literals 3.0, 2.0, 0.99 must not appear
        as filter threshold values in TrendJoinLong. They may appear in
        unit test files but not in the implementation.
        """
        import bot.strategy.trend_join_long as tjl_mod
        src = inspect.getsource(tjl_mod)
        # These bare numbers would indicate hardcoded strategy thresholds
        # Note: we allow them in docstrings/comments but not as comparison literals
        # Strategy thresholds should be read from self._cfg
        # Accept: the source should not have "3.0" or "2.0" as raw comparison values
        # (The precise check: look for lines like ">= 3.0" or "< 2.0" which would be hardcoded)
        import re
        # Match patterns like ">= 3.0" "> 2.0" "< 0.99" that are comparison literals
        # (not in comments or strings)
        lines = src.split("\n")
        for line in lines:
            stripped = line.strip()
            # Skip comments and docstrings
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # Check for bare strategy thresholds as comparison operators
            if re.search(r"(>=|<=|==|<|>)\s*(3\.0|2\.0|0\.99)\b", stripped):
                assert False, (
                    f"Found bare numeric strategy literal in line: '{stripped}'. "
                    "Use self._cfg.<field> instead."
                )

    def test_strategy_core_has_no_io_imports(self):
        """StrategyCore and TrendJoinLong must have no network/I/O imports."""
        import bot.strategy.core as core_mod
        import bot.strategy.trend_join_long as tjl_mod
        for mod, mod_name in [(core_mod, "core"), (tjl_mod, "trend_join_long")]:
            src = inspect.getsource(mod)
            forbidden = ["sqlite3", "requests", "moomoo", "futu", "urllib", "socket"]
            for name in forbidden:
                assert name not in src, f"bot/strategy/{mod_name}.py must not import '{name}'"


# ============================================================
# compute_swing_low_2_2 delegation test
# ============================================================

class TestComputeSwingLow22:
    def test_delegates_to_indicators_swing_low_2_2(self):
        """
        compute_swing_low_2_2 must delegate to indicators.swing_low_2_2.
        Test with a clean pivot → returns pivot low.
        """
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = pd.DataFrame([
            {"low": 5.0},
            {"low": 4.0},
            {"low": 2.0},
            {"low": 3.0},
            {"low": 6.0},
        ])
        result = strat.compute_swing_low_2_2(bars)
        assert result == 2.0

    def test_returns_none_when_insufficient_bars(self):
        cfg = _make_canonical_config()
        strat = TrendJoinLong(cfg)
        bars = pd.DataFrame([{"low": 1.0}, {"low": 2.0}])
        result = strat.compute_swing_low_2_2(bars)
        assert result is None
