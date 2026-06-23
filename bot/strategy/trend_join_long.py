#!/usr/bin/env python3
"""
bot.strategy.trend_join_long — Concrete Trend Join Long strategy implementation.

TrendJoinLong reads EVERY parameter from a StrategyConfig instance (cfg).
No strategy constant is hardcoded — all thresholds (gap %, price floor, RVOL
minimum, stop percentage, etc.) flow from self._cfg.<field>.

This is the config-drivenness requirement (CFG-01, D-12): swapping the
StrategyConfig changes filter outputs without any Python code edits.

Exports: TrendJoinLong
"""
from typing import Optional

import pandas as pd

from bot.config.loader import StrategyConfig
from bot.strategy.core import StrategyCore
from bot.strategy.indicators import swing_low_2_2 as _swing_low_2_2


# ============================================================
# TrendJoinLong
# ============================================================

class TrendJoinLong(StrategyCore):
    """
    Trend Join Long strategy — D1/D2/D3 + I1/I2/I3 + LOD-based stop + swing-low trail.

    Every parameter is read from self._cfg (a StrategyConfig). The strategy has
    no Python-level numeric literals for any threshold; changing rules.json changes
    behavior (D-12 behavioral proof, CONTEXT.md).

    Private decomposition:
        _check_d1: today close > prior-day high (D1_above_prior_day_high)
        _check_d2: prior close > SMA200 (D2_prior_close_above_sma200)
        _check_d3: gap >= cfg.d3_min_gap_pct (D3_min_gap_pct_from_prior_close)
        _check_universe_price: today close >= cfg.min_price_usd
    """

    def __init__(self, cfg: StrategyConfig) -> None:
        """
        Initialise TrendJoinLong with a validated StrategyConfig.

        Parameters:
            cfg: Loaded StrategyConfig (from load_strategy_config).
        """
        self._cfg = cfg

    # ============================================================
    # Daily Filter — private decomposition
    # ============================================================

    def _check_d1(self, daily_data: pd.DataFrame) -> bool:
        """D1: today close must be strictly above prior-day high."""
        today_close = daily_data.iloc[-1]["close"]
        prior_high = daily_data.iloc[-2]["high"]
        return bool(today_close > prior_high)

    def _check_d2(self, daily_data: pd.DataFrame, sma200: float) -> bool:
        """D2: prior close must be strictly above SMA200."""
        prior_close = daily_data.iloc[-2]["close"]
        return bool(prior_close > sma200)

    def _check_d3(self, daily_data: pd.DataFrame) -> bool:
        """D3: gap from prior close must be >= cfg.d3_min_gap_pct."""
        today_open = daily_data.iloc[-1]["open"]
        prior_close = daily_data.iloc[-2]["close"]
        if prior_close == 0.0:
            return False
        gap_pct = (today_open - prior_close) / prior_close * 100.0
        return bool(gap_pct >= self._cfg.d3_min_gap_pct)

    def _check_universe_price(self, daily_data: pd.DataFrame) -> bool:
        """Universe filter: today close must be >= cfg.min_price_usd."""
        today_close = daily_data.iloc[-1]["close"]
        return bool(today_close >= self._cfg.min_price_usd)

    # ============================================================
    # Public ABC implementations
    # ============================================================

    def passes_daily_filters(
        self,
        code: str,
        daily_data: pd.DataFrame,
        sma200: float,
    ) -> bool:
        """
        Evaluate D1/D2/D3 + universe price filter.

        All four conditions must hold — short-circuits on first failure.
        All thresholds read from self._cfg (no hardcoded values).

        Parameters:
            code:       Stock code (unused in filter math, available for logging).
            daily_data: DataFrame with at least 2 rows (prior + today), columns:
                        open, high, low, close.
            sma200:     200-day SMA computed from prior closes.

        Returns:
            True if all daily conditions are satisfied; False otherwise.
        """
        return (
            self._check_d1(daily_data)
            and self._check_d2(daily_data, sma200)
            and self._check_d3(daily_data)
            and self._check_universe_price(daily_data)
        )

    def passes_intraday_filters(
        self,
        code: str,
        bars_5m: pd.DataFrame,
        premarket_high: float,
        hod: float,
        rvol: float,
    ) -> bool:
        """
        Evaluate I1/I2/I3 intraday filters on a closed 5m bar.

        I1: close > premarket_high (above premarket high)
        I2: close >= hod (at or above high-of-day)
        I3: rvol >= self._cfg.rvol_min (relative volume threshold from config)

        All thresholds read from self._cfg.

        Parameters:
            code:           Stock code (for logging).
            bars_5m:        DataFrame of closed 5m bars; uses iloc[-1]["close"].
            premarket_high: Premarket high (before 09:30 ET).
            hod:            Current session high-of-day.
            rvol:           Pre-computed relative volume ratio.

        Returns:
            True if all intraday conditions are satisfied; False otherwise.
        """
        current_close = float(bars_5m.iloc[-1]["close"])
        return (
            current_close > premarket_high          # I1
            and current_close >= hod                # I2
            and rvol >= self._cfg.rvol_min          # I3 — threshold from config
        )

    def compute_initial_stop(self, lod: float) -> float:
        """
        Compute the initial stop price as LOD minus the config-driven stop percentage.

        initial_stop = lod * (1 - cfg.initial_stop_pct / 100)

        The stop distance is derived from exit.initial_stop_rule (parsed into
        cfg.initial_stop_pct at load time) — the dedicated stop parameter — and
        is intentionally decoupled from risk.max_risk_per_trade_pct, which is the
        position-sizing budget, not a stop rule (CR-01). With
        initial_stop_rule="lod_minus_1pct": stop = lod * 0.99.

        The stop percentage is ALWAYS derived from self._cfg — never hardcoded.

        Parameters:
            lod: Low-of-day price at signal time.

        Returns:
            float — stop price below lod.
        """
        stop_fraction = self._cfg.initial_stop_pct / 100.0
        return float(lod * (1.0 - stop_fraction))

    def compute_swing_low_2_2(self, bars_5m: pd.DataFrame) -> Optional[float]:
        """
        Delegate to indicators.swing_low_2_2 using the 'low' column.

        Parameters:
            bars_5m: DataFrame of closed 5m bars with a 'low' column.

        Returns:
            float — confirmed swing low, or None if unavailable.
        """
        if bars_5m is None or len(bars_5m) == 0:
            return None
        return _swing_low_2_2(bars_5m["low"].tolist())
