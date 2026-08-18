#!/usr/bin/env python3
"""
backtester.experimental.engine — per-bar replay engine for the external-strategy
research package.

This module never constructs a broker gateway, an execution layer, or a
StateStore, and never issues a network fetch (mirrors backtester/run.py and
backtester/options_run.py's own stated invariant) — cache-only replay against
an already-constructed `feed` (e.g. backtester.feed.SimulatedBarFeed).

`build_frame`/`group_by_day` are pure data-shaping helpers; `Engine` drives the
per-bar loop (force-close -> position management -> entry evaluation -> N+1-
open fill) and emits harness-convention trade rows (backtester/harness.py:
400-416), extended for shorts (side-aware R, extra columns side/strategy/arm/
n_legs/regime).

`Engine.run(days)` takes the dict `group_by_day(feed, day_list)` returns
(keyed by day string) rather than a plain day list — group_by_day is computed
ONCE per window and shared by every arm's Engine (feed.replay() is O(all bars
in the window) per call; calling it once per arm per day would multiply that
cost by the arm count, 10-RESEARCH Pitfall 6).

Exports: build_frame, group_by_day, Position, Engine
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, time as _time
from typing import Dict, Optional

import pandas as pd

from bot.position.manager import get_force_close_time_et
from bot.strategy.indicators import swing_low_2_2

from backtester.experimental import exits
from backtester.experimental.indicators import swing_high_2_2

_EXIT_MODELS = {
    "pct_ladder": exits.pct_ladder,
    "partial_be_trail": exits.partial_be_trail,
    "fixed_2r": exits.fixed_2r,
}

_SWING_TRAIL_LOOKBACK_BARS = 50


# ============================================================
# build_frame / group_by_day
# ============================================================

def build_frame(feed, code: str, params: dict) -> pd.DataFrame:
    """Padded RTH 5m frame for `code`, from `feed.intraday_5m_for_tod()` (the
    ~30-session-padded frame `SimulatedBarFeed` already loads for RVOL-TOD
    baselines — the same warm-up window seeds SMA/MACD/ATR/EMA100 here).

    massive source returns Title-Case Open/High/Low/Close/Volume columns on a
    tz-aware ET index (mirrors get_ticker_frame's lowercasing convention) --
    lowercased here. Adds `time_key` ("%Y-%m-%d %H:%M:%S" ET), per-session
    running `hod`/`lod`/`cum_volume` (INCLUDING the current bar), and a
    boolean `in_window` column marking rows inside [feed.start, feed.end] (the
    padding-only rows before feed.start exist purely for indicator warm-up).

    Returns an empty DataFrame if the feed has no data for `code` (unknown
    symbol / zero-coverage window) -- callers must handle this, never crash.
    """
    sym = code.removeprefix("US.") if hasattr(code, "removeprefix") else code[len("US."):]
    raw = feed.intraday_5m_for_tod().get(sym)
    if raw is None or raw.empty:
        return pd.DataFrame()

    frame = raw.copy()
    frame.columns = [c.lower() for c in frame.columns]
    frame = frame.sort_index()
    frame = frame.dropna(subset=["open", "high", "low", "close", "volume"])
    if frame.empty:
        return frame

    frame["time_key"] = [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in frame.index]
    session = pd.Series(frame.index.date, index=frame.index)
    frame["hod"] = frame["high"].groupby(session).cummax()
    frame["lod"] = frame["low"].groupby(session).cummin()
    frame["cum_volume"] = frame["volume"].groupby(session).cumsum()

    day_str = frame["time_key"].str.slice(0, 10)
    frame["in_window"] = (day_str >= feed.start) & (day_str <= feed.end)
    return frame


def group_by_day(feed, days) -> Dict[str, list]:
    """{day_str: [bar dict, ...]} for every day in `days`, ONE pass over
    `feed.replay(day)` per day -- computed once per window, shared by every
    arm's Engine.run() call (see module docstring)."""
    return {str(day): list(feed.replay(day)) for day in days}


# ============================================================
# Position
# ============================================================

@dataclass
class Position:
    """In-flight (or just-closed) position state for one symbol."""

    code: str
    side: str  # "long" | "short"
    entry_price: float
    initial_stop: float
    stop: float
    full_quantity: int
    open_quantity: int
    exit_notional: float = 0.0
    exit_filled_qty: int = 0
    n_legs: int = 0
    opened_at: str = ""
    updated_at: str = ""
    strategy: str = ""
    arm: str = ""
    regime: str = "none"
    exit_reason: str = ""
    state: str = "active"
    risk_denominator: float = 0.0
    legs_taken: int = 0
    trade_recorded: bool = False


# ============================================================
# Engine
# ============================================================

class Engine:
    """Per-bar replay engine: force-close -> position management -> entry
    evaluation -> N+1-open fill, for one arm (one strategy + resolved params).

    frames: {code: DataFrame} from build_frame.
    signals: {code: DataFrame} from strategies.{ext2,orb,vwap_pb}_signals(frame, params)
        -- same index as frames[code], columns long/short/stop_long/stop_short.
    params: resolved arm params dict (sizing/caps/exit_model/regime_gate, plus
        optional "strategy"/"arm" tags recorded on every trade row).
    feed: object exposing next_bar(code, after) -> bar dict | None.
    slippage: adverse $/share slippage (entries/exits always slip against you).
    stop_fill: "close" (production-parity default) | "intrabar" (sensitivity
        mode: fills exactly at the stop price when the bar's low/high crosses
        it, stop wins ties).
    regime_fn: Optional callable(day_str) -> "bull"|"bear"|"neutral", read
        only when params["regime_gate"] == "weekly_spy".
    """

    def __init__(self, frames: dict, signals: dict, params: dict, feed,
                 slippage: float, stop_fill: str, regime_fn=None):
        self.frames = frames
        self.signals = signals
        self.params = params
        self.feed = feed
        self.slippage = slippage
        self.stop_fill = stop_fill
        self.regime_fn = regime_fn
        self.gap_through_entries = 0

        self._trades: list = []
        self._positions: Dict[str, Position] = {}
        self._bars_since_open: Dict[str, list] = {}
        self._day_entry_count = 0
        self._day_realized_pnl = 0.0
        self._day_first_side: Dict[str, str] = {}
        self._entries_today: Dict[str, int] = {}

        # O(1) per-(code, time_key) signal-row lookups.
        self._signal_by_tk: Dict[str, pd.DataFrame] = {}
        for code, sig in signals.items():
            frame = frames.get(code)
            if frame is None or frame.empty:
                continue
            indexed = sig.copy()
            indexed.index = frame["time_key"].values
            self._signal_by_tk[code] = indexed

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def run(self, days: Dict[str, list]) -> list:
        """`days`: the dict group_by_day(feed, day_list) returns. Returns the
        accumulated trade-row list (harness convention, side-aware, extended)."""
        for day in sorted(days.keys()):
            self._run_day(day, days[day])
        return self._trades

    # --------------------------------------------------------
    # Per-day replay
    # --------------------------------------------------------

    def _run_day(self, day: str, bars: list) -> None:
        self._day_entry_count = 0
        self._day_realized_pnl = 0.0
        self._day_first_side = {}
        self._entries_today = {}
        self._bars_since_open = {}

        today = datetime.strptime(day, "%Y-%m-%d").date()
        force_close_t = get_force_close_time_et(today)
        force_close_floor = _time(force_close_t.hour, (force_close_t.minute // 5) * 5)

        gate_active = self.params.get("regime_gate") == "weekly_spy"
        regime = "none"
        if gate_active and self.regime_fn is not None:
            regime = self.regime_fn(day)

        grouped: Dict[str, list] = {}
        for b in bars:
            grouped.setdefault(b["time_key"], []).append(b)

        for time_key, bar_group in grouped.items():
            bar_time = datetime.strptime(time_key, "%Y-%m-%d %H:%M:%S").time()
            is_force_close_bar = bar_time >= force_close_floor
            just_closed: set = set()

            # -------- (1) force-close --------
            if is_force_close_bar:
                for bar in bar_group:
                    pos = self._positions.get(bar["code"])
                    if pos is None:
                        continue
                    self._close_position(pos, bar, reason="force_close")
                    just_closed.add(bar["code"])
                    del self._positions[bar["code"]]

            # Record bar history for the swing trail (mirrors PositionManager's
            # bar_buffer wiring: append BEFORE evaluating this bar's exits).
            for bar in bar_group:
                hist = self._bars_since_open.setdefault(bar["code"], [])
                hist.append(bar)
                if len(hist) > _SWING_TRAIL_LOOKBACK_BARS:
                    del hist[: len(hist) - _SWING_TRAIL_LOOKBACK_BARS]

            # -------- (2) position management --------
            for bar in bar_group:
                code = bar["code"]
                if code in just_closed:
                    continue
                pos = self._positions.get(code)
                if pos is None:
                    continue
                if self._manage_position(pos, bar, time_key):
                    just_closed.add(code)
                    del self._positions[code]

            # -------- (3) entry evaluation --------
            candidates = []
            for bar in bar_group:
                code = bar["code"]
                if code in self._positions or code in just_closed:
                    continue
                sig_frame = self._signal_by_tk.get(code)
                if sig_frame is None or time_key not in sig_frame.index:
                    continue
                row = sig_frame.loc[time_key]
                long_sig, short_sig = bool(row["long"]), bool(row["short"])
                if self.params.get("trend_lock") and code in self._day_first_side:
                    if self._day_first_side[code] == "long":
                        short_sig = False
                    else:
                        long_sig = False
                if not long_sig and not short_sig:
                    continue
                side = "long" if long_sig else "short"
                stop = row["stop_long"] if side == "long" else row["stop_short"]
                if stop is None or (isinstance(stop, float) and math.isnan(stop)):
                    continue
                candidates.append((time_key, code, side, float(stop), bar))

            candidates.sort(key=lambda c: (c[0], c[1]))  # time-first, then alphabetical

            max_per_day = self.params.get("max_entries_per_day")
            max_concurrent = self.params.get("max_concurrent")
            breaker = self.params.get("daily_breaker_usd")
            max_per_symbol = self.params.get("max_entries_per_symbol_per_day")
            sizing = self.params.get("sizing", "risk1pct")

            # -------- (4) fill --------
            for tk, code, side, stop, bar in candidates:
                if max_per_day is not None and self._day_entry_count >= max_per_day:
                    continue
                if max_concurrent is not None and len(self._positions) >= max_concurrent:
                    continue
                if breaker is not None and self._day_realized_pnl <= breaker:
                    continue
                if max_per_symbol is not None and self._entries_today.get(code, 0) >= max_per_symbol:
                    continue

                qty_mult = 1.0
                if gate_active:
                    if regime == "bear" and side == "long":
                        continue
                    if regime == "bull" and side == "short":
                        continue
                    if regime == "neutral":
                        qty_mult = 0.5

                close = bar["close"]
                qty = self._size(close, stop, sizing)
                if qty is None:
                    continue
                if qty_mult != 1.0:
                    qty = math.floor(qty * qty_mult)
                    if qty < 1:
                        continue

                next_bar = self.feed.next_bar(code, after=tk)
                if next_bar is None:
                    continue  # no next bar -- unfilled

                sign = 1 if side == "long" else -1
                fill_price = next_bar["open"] + sign * self.slippage

                gap_through = (
                    (side == "long" and fill_price <= stop)
                    or (side == "short" and fill_price >= stop)
                )
                if gap_through:
                    self.gap_through_entries += 1
                    risk_denominator = abs(close - stop)
                else:
                    risk_denominator = abs(fill_price - stop)

                pos = Position(
                    code=code, side=side, entry_price=fill_price,
                    initial_stop=stop, stop=stop,
                    full_quantity=qty, open_quantity=qty,
                    opened_at=next_bar["time_key"], updated_at=next_bar["time_key"],
                    strategy=self.params.get("strategy", ""), arm=self.params.get("arm", ""),
                    regime=regime if gate_active else "none",
                    risk_denominator=risk_denominator,
                )
                self._positions[code] = pos
                self._day_entry_count += 1
                self._entries_today[code] = self._entries_today.get(code, 0) + 1
                self._day_first_side.setdefault(code, side)

        # -------- end of day: orphaned open positions (no further bars) --------
        for code, pos in list(self._positions.items()):
            hist = self._bars_since_open.get(code)
            if not hist:
                continue
            self._close_position(pos, hist[-1], reason="eod_no_bars")
            del self._positions[code]

    # --------------------------------------------------------
    # Position management (stop-fill mode + exit model)
    # --------------------------------------------------------

    def _manage_position(self, pos: Position, bar: dict, time_key: str) -> bool:
        """Returns True if the position is now fully closed."""
        if self.stop_fill == "intrabar":
            hit = (
                (pos.side == "long" and bar["low"] <= pos.stop)
                or (pos.side == "short" and bar["high"] >= pos.stop)
            )
            if hit:
                qty = pos.open_quantity
                pos.open_quantity = 0
                self._apply_exit_leg(pos, qty, pos.stop, "stop", time_key)
                return self._finalize_if_closed(pos)

        model_fn = _EXIT_MODELS[self.params.get("exit_model", "pct_ladder")]

        recent = self._bars_since_open.get(pos.code, [])[-_SWING_TRAIL_LOOKBACK_BARS:]
        new_swing_low = swing_low_2_2([b["low"] for b in recent]) if recent else None
        new_swing_high = swing_high_2_2([b["high"] for b in recent]) if recent else None

        position_dict = {
            "side": pos.side, "entry_price": pos.entry_price, "initial_stop": pos.initial_stop,
            "stop": pos.stop, "full_quantity": pos.full_quantity, "open_quantity": pos.open_quantity,
            "state": pos.state, "legs_taken": pos.legs_taken, "bars": recent,
            "new_swing_low": new_swing_low, "new_swing_high": new_swing_high,
        }
        legs = model_fn(position_dict, bar, self.params)

        pos.stop = position_dict["stop"]
        pos.open_quantity = position_dict["open_quantity"]
        pos.state = position_dict.get("state", pos.state)
        pos.legs_taken = position_dict.get("legs_taken", pos.legs_taken)

        for leg in legs:
            next_bar = self.feed.next_bar(pos.code, after=time_key)
            sign = -1 if pos.side == "long" else 1  # a long's exit is a SELL (slips down)
            if next_bar is not None:
                price = next_bar["open"] + sign * self.slippage
                fill_time = next_bar["time_key"]
            else:
                price = bar["close"] + sign * self.slippage
                fill_time = bar["time_key"]
            self._apply_exit_leg(pos, leg["qty"], price, leg["reason"], fill_time)

        return self._finalize_if_closed(pos)

    def _close_position(self, pos: Position, bar: dict, reason: str) -> None:
        """force_close / eod_no_bars: exit ALL remaining qty at this bar's
        close, adverse slippage (long: -slip, short: +slip)."""
        sign = -1 if pos.side == "long" else 1
        price = bar["close"] + sign * self.slippage
        qty = pos.open_quantity
        pos.open_quantity = 0
        self._apply_exit_leg(pos, qty, price, reason, bar["time_key"])
        self._finalize_if_closed(pos)

    def _apply_exit_leg(self, pos: Position, qty: int, price: float, reason: str,
                        time_key: str) -> None:
        if qty <= 0:
            return
        pos.exit_notional += qty * price
        pos.exit_filled_qty += qty
        pos.n_legs += 1
        pos.exit_reason = reason
        pos.updated_at = time_key
        sign = 1 if pos.side == "long" else -1
        self._day_realized_pnl += sign * (price - pos.entry_price) * qty

    def _finalize_if_closed(self, pos: Position) -> bool:
        if pos.open_quantity > 0:
            return False
        if pos.trade_recorded:  # defensive: never emit a second row for one position
            return True
        pos.trade_recorded = True
        exit_price = (
            pos.exit_notional / pos.exit_filled_qty if pos.exit_filled_qty > 0
            else pos.entry_price
        )
        sign = 1 if pos.side == "long" else -1
        risk = pos.risk_denominator or abs(pos.entry_price - pos.initial_stop)
        r_multiple = sign * (exit_price - pos.entry_price) / risk if risk != 0 else 0.0
        self._trades.append({
            "code": pos.code, "opened_at": pos.opened_at, "entry_price": pos.entry_price,
            "exit_price": exit_price, "quantity": pos.full_quantity,
            "exit_reason": pos.exit_reason, "r_multiple": r_multiple, "closed_at": pos.updated_at,
            "side": pos.side, "strategy": pos.strategy, "arm": pos.arm,
            "n_legs": pos.n_legs, "regime": pos.regime,
        })
        return True

    # --------------------------------------------------------
    # Sizing (parity re-implementation of bot/risk/risk_engine.py -- not an
    # import, RiskEngine is async/gateway-coupled)
    # --------------------------------------------------------

    @staticmethod
    def _size(close: float, stop: float, sizing: str) -> Optional[int]:
        if sizing == "notional10":
            qty = math.floor(10_000.0 / close) if close > 0 else 0
            return qty if qty >= 1 else None
        stop_distance = abs(close - stop)
        if stop_distance <= 0 or close <= 0:
            return None
        risk_qty = math.floor(1_000.0 / stop_distance)
        notional_cap_qty = math.floor(10_000.0 / close)
        qty = min(risk_qty, notional_cap_qty)
        return qty if qty >= 1 else None
