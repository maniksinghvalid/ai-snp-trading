#!/usr/bin/env python3
"""
bot.strategy.core — Abstract base class for all strategy implementations.

StrategyCore defines the contract that both the live SignalEngine and the
Phase 6 backtester use. Implementations must be pure (no I/O, no network
calls) — they accept plain data (DataFrames, floats) and return signals.

Per ARCHITECTURE.md Pattern 3: shared ABC for live/backtest parity. Any I/O
in a concrete implementation breaks the backtester.

Exports: StrategyCore
"""
import abc
from typing import Optional

import pandas as pd


# ============================================================
# StrategyCore — Abstract Base Class
# ============================================================

class StrategyCore(abc.ABC):
    """
    Abstract base class for Trend Join Long strategy implementations.

    All concrete subclasses must implement the four hooks below. No I/O
    is permitted inside any hook — they receive only plain data (DataFrames,
    floats) and return simple values (bool, float, None).

    Design notes:
    - All strategy constants come from StrategyConfig (passed at construction).
    - The ABC itself contains no strategy logic or parameters.
    - Both the live bot and the backtester inject the same concrete subclass.
    """

    @abc.abstractmethod
    def passes_daily_filters(
        self,
        code: str,
        daily_data: pd.DataFrame,
        sma200: float,
    ) -> bool:
        """
        Evaluate D1/D2/D3 daily filters for a candidate stock.

        Parameters:
            code:       Stock code (e.g. 'US.AAPL').
            daily_data: DataFrame of daily OHLCV bars (at least 2 rows:
                        row[-2] = prior day, row[-1] = today).
            sma200:     Pre-computed 200-day SMA of prior closes.

        Returns:
            True if all daily filter conditions are met; False otherwise.
        """

    @abc.abstractmethod
    def passes_intraday_filters(
        self,
        code: str,
        bars_5m: pd.DataFrame,
        premarket_high: float,
        hod: float,
        rvol: float,
        hod_prev: Optional[float] = None,
    ) -> bool:
        """
        Evaluate I1/I2/I3 intraday filters on a closed 5m bar.

        Parameters:
            code:           Stock code.
            bars_5m:        DataFrame of closed 5m bars (at least 1 row;
                            row[-1] is the most recently closed bar).
            premarket_high: Premarket high price (before 09:30 ET).
            hod:            Current high-of-day price (includes this bar's own high).
            rvol:           Pre-computed relative volume ratio.
            hod_prev:       High-of-day AS OF THE PRIOR closed bar (excludes this
                            bar's own high), or None on the first bar of a session /
                            when unavailable. Only consulted when
                            cfg.i2_mode == "close_above_prior_hod".

        Returns:
            True if all intraday filter conditions are met; False otherwise.
        """

    @abc.abstractmethod
    def compute_initial_stop(self, lod: float) -> float:
        """
        Compute the initial stop price for a new position.

        Per strategy: initial stop = LOD minus a config-driven percentage.

        Parameters:
            lod: Low-of-day price at entry time.

        Returns:
            float — initial stop price (always below lod).
        """

    @abc.abstractmethod
    def compute_swing_low_2_2(self, bars_5m: pd.DataFrame) -> Optional[float]:
        """
        Identify the most recent 5m swing low using 2-bar-left/2-bar-right confirmation.

        Used for trailing stop updates after breakeven is triggered.

        Parameters:
            bars_5m: DataFrame of closed 5m bars with a 'low' column.

        Returns:
            float — the confirmed swing low price, or None if no pivot is found
            or fewer than 5 bars are available.
        """
