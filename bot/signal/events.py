#!/usr/bin/env python3
"""
bot.signal.events — BarEvent and SignalEvent dataclasses (Phase 3, SIG-02/03).

BarEvent is produced by BarAggregator on every closed 5m bar.
SignalEvent is produced by SignalEngine when all intraday gates pass
(I1: above premarket high, I2: above HOD, I3: RVOL >= 2.0,
 time window: 10:05–15:30 ET inclusive/exclusive).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BarEvent:
    """Closed 5m bar delivered from BarAggregator to SignalEngine.

    Produced when the moomoo SDK push stream advances to a new time_key
    (bar-close detection via timestamp advance — SIG-02 no-repainting invariant).
    Carries both the closed bar's individual OHLCV and the session running
    high-of-day (hod) and low-of-day (lod) accumulated across all bars.

    Fields:
        code: Moomoo-format stock code (e.g. "US.AAPL").
        time_key: ISO-8601 ET timestamp of the closed bar (e.g. "2026-06-24 10:05:00").
        open: Opening price of the closed bar.
        high: Highest price of the closed bar.
        low: Lowest price of the closed bar.
        close: Closing price of the closed bar.
        volume: Volume of the closed bar (single bar — not cumulative).
        hod: Session running max of all pushed highs (mid-bar and bar-close) accumulated
             up to and INCLUDING this bar's last push, EXCLUDING the new bar's first tick.
             Updated eagerly on every K_5M push during bar A's lifetime, then snapshotted
             at bar A's close before bar B's first tick is folded in (D-02, SIG-02).
        lod: Session running min of all pushed bar lows from the first K_5M bar up to and
             INCLUDING this bar's last push, EXCLUDING the new bar's first tick.
             Equivalent to the cumulative session low-of-day at the moment bar A closed
             (D-02, RESEARCH Pitfall 3; feeds compute_initial_stop(lod) in RiskEngine).
        cum_volume: Cumulative session volume through and including this bar (Phase 7 RVOL-TOD).
             Accumulated by BarAggregator._session_volume per code; reset to 0 at reset_session().
             Defaults to 0 so existing BarEvent constructions without this field remain valid.
             Consumed by SignalEngine Gate I3-TOD in plan 07-05.
    """

    code: str
    time_key: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    hod: float
    lod: float
    cum_volume: int = 0   # cumulative session volume through and including this bar (Phase 7 RVOL-TOD)


@dataclass
class SignalEvent:
    """Intraday gates I1/I2/I3 + time-gate all passed — passed to RiskEngine.

    Produced by SignalEngine when a BarEvent satisfies all entry conditions:
      I1: bar.close > premarket_high (D-01 frozen value)
      I2: bar.close > hod (above today's running high-of-day)
      I3: rvol >= cfg.rvol_min (RVOL at least 2.0 over 14-day lookback)
      time gate: 10:05 ET (inclusive) to 15:30 ET (exclusive)
      concurrent cap: open positions < max_concurrent_positions
      daily cap: filled_count + pending_count < max_trades_per_day (D-09)

    Fields:
        code: Moomoo-format stock code.
        bar: The BarEvent that triggered this signal.
        premarket_high: Frozen D-01 pre_high_price value captured near 09:30 ET.
        hod: Session running high-of-day at bar close (same as bar.hod at signal time).
        lod: Session running low-of-day (session running-min from the first K_5M bar).
             This is what flows into compute_initial_stop(lod) in RiskEngine (RESEARCH Pitfall 3).
        rvol: Relative volume ratio (current_volume / rvol_baseline from daily_scan).
        emitted_at: now_et() timestamp at signal emission.
    """

    code: str
    bar: BarEvent
    premarket_high: float
    hod: float
    lod: float
    rvol: float
    emitted_at: datetime
