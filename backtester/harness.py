#!/usr/bin/env python3
"""
backtester.harness — BacktestHarness: the replay controller (BT-01/BT-02).

Constructs the exact same reused pipeline bot/main.py builds --
TrendJoinLong, SignalEngine, RiskEngine, PositionManager -- swapping only
the two simulated leaves: SimulatedGateway (into SignalEngine/RiskEngine)
and SimulatedExecution (as PositionManager's engine). PositionManager itself
receives gateway=None so arm_stop_protection() correctly no-ops -- a 5m bar
replay has no tick-level stop mechanism (06-RESEARCH Anti-Pattern; matches
production's use_broker_stop_orders=false path).

setup_day(day, symbols) seeds per-day point-in-time SMA200/RVOL/RVOL-TOD
baselines and premarket highs by calling bot.scanner.scanner._evaluate_symbol
and _compute_tod_baselines directly -- never reimplementing that math
(06-RESEARCH Don't Hand-Roll). Baselines are persisted keyed by the SAME
session date `day` the replay clock reports, so SignalEngine's
now_et().date()-keyed StateStore reads hit them.

replay_day(day)/run() port bot.service.bot.TradingBot._process_bar bar-for-bar
(06-PATTERNS "Core per-bar pipeline"):
  - the closed bar's OHLCV dict is appended to the harness-owned bar_buffer
    BEFORE position_manager.on_bar(bar) (Pitfall 6 -- swing-low trail wiring)
  - sim_execution.on_bar(bar) is called so manage_exit has an anchor for its
    own next-bar lookup (06-03 deviation)
  - the replay clock is rebound to bot.signal.signal_engine.now_et for the
    duration of run() (try/finally) so the entry-window gate and the
    session-date baseline keys evaluate against the REPLAYED bar's ET time,
    never the wall clock (T-06-11) -- a runtime module-attribute rebind, not
    a bot/ source edit.
  - closed positions are captured into self.trade_log (entry/exit price,
    quantity, exit reason, r_multiple) since nothing in bot/ ever writes the
    `trades` DB table for a backtest run.

Scope: replays the CURRENT partial_be_trail exit-model FSM only (06-RESEARCH
Open-Q2) -- fixed_2r/full_to_1p5r_trail remain Phase 7's job (07-06).

Drops the D-08 circuit-breaker side-effect gate (_entries_enabled /
_handle_circuit_breaker_side_effects) -- there is no kill-switch concept in
an offline replay; entries are always enabled here (documented choice, see
SUMMARY). SignalEngine's own Gate 7 circuit-breaker check still runs and can
still block entries -- only the TradingBot-specific abandon+alert side
effect is out of scope.

Exports: BacktestHarness
"""
from collections import deque
from typing import Dict, List, Optional
from uuid import uuid4

import bot.signal.signal_engine as _signal_engine_module
from bot.position.manager import PositionManager
from bot.position.state import PositionPhase, PositionState
from bot.risk.risk_engine import RiskEngine
from bot.safety.et_helpers import ET
from bot.scanner.scanner import _WATCHLIST_CAP, _compute_tod_baselines, _evaluate_symbol
from bot.signal.events import BarEvent
from bot.signal.signal_engine import SignalEngine
from bot.strategy.trend_join_long import TrendJoinLong

from backtester.execution import SimulatedExecution, SimulatedGateway

# Mirrors BarAggregator._bar_buffer's maxlen (bot/signal/bar_aggregator.py
# _BAR_BUFFER_MAX) -- the swing-low trail reads the same rolling window a
# live run would.
_BAR_BUFFER_MAX = 50


class BacktestHarness:
    """Replay controller: reused live pipeline + simulated feed/execution/gateway."""

    def __init__(self, cfg, feed, store) -> None:
        """cfg: StrategyConfig (rules.json, same loader as bot/main.py).
        feed: SimulatedBarFeed -- replay()/next_bar()/daily_bars()/etc.
        store: StateStore opened at a SCRATCH db_path (never data/bot_state.db --
               06-RESEARCH Pitfall 4; caller's responsibility, mirrors bot/main.py's
               StateStore(db_path=...).open() call-site discipline).
        """
        self._cfg = cfg
        self._feed = feed
        self._store = store

        self.strategy = TrendJoinLong(cfg)
        self.sim_execution = SimulatedExecution(feed)
        self.sim_gateway = SimulatedGateway(lambda: self.position_manager)

        self.signal_engine = SignalEngine(cfg=cfg, gateway=self.sim_gateway, store=store)
        self.risk_engine = RiskEngine(
            cfg=cfg, gateway=self.sim_gateway, store=store, signal_engine=self.signal_engine
        )

        # Harness-owned bar_buffer -- mirrors BarAggregator._bar_buffer's exact shape
        # (Dict[str, deque(maxlen=50)] of the raw bar_data dict). Appended BEFORE
        # position_manager.on_bar() every closed bar (06-RESEARCH Pitfall 6).
        self._bar_buffer: Dict[str, deque] = {}

        # gateway=None -- arm_stop_protection() correctly no-ops (bar-close FSM
        # stop is the sole stop mechanism at 5m granularity).
        self.position_manager = PositionManager(
            store=store,
            engine=self.sim_execution,
            cfg=cfg,
            strategy=self.strategy,
            bar_buffer=self._bar_buffer,
            gateway=None,
        )

        # Per-day premarket-high freeze (CR-01): setup_day STORES each day's highs here
        # (never applies them); replay_day applies ONLY the day being replayed as the
        # first statement, so a later day's setup_day can never clobber an earlier,
        # not-yet-replayed day's highs via the shared signal_engine._premarket_highs dict
        # (run.py calls setup_day for every day BEFORE run() replays any of them).
        self._premarket_highs_by_day: Dict = {}

        # Per-day capped/ranked watchlist codes (CR-06): setup_day slices candidates to
        # _WATCHLIST_CAP (mirrors run_daily_scan's own top-N cap); _process_bar gates the
        # ENTRY branch on membership -- position management always runs regardless.
        self._watchlist_by_day: Dict[str, set] = {}

        # Per-day setup order, populated by setup_day(); replayed in this order by run().
        self._days: List = []

        # Trade-log capture bookkeeping (nothing in bot/ writes the `trades` DB table
        # for a backtest run -- 06-RESEARCH override of the get_closed_trades note).
        self.trade_log: List[dict] = []
        self._captured_position_ids: set = set()
        self._exit_fills_consumed: Dict[str, int] = {}

        # Replay clock: rebound onto bot.signal.signal_engine.now_et for the
        # duration of run() (T-06-11). Defaults to None until run() starts.
        self._replay_clock = None

    # ============================================================
    # Per-day setup (BT-02 point-in-time correctness)
    # ============================================================

    def setup_day(self, day, symbols: List[str]) -> None:
        """Seed point-in-time SMA200/RVOL/RVOL-TOD baselines + premarket highs for `day`.

        Reuses bot.scanner.scanner._evaluate_symbol (SMA200/RVOL, date < scan_date
        cutoff) and _compute_tod_baselines (RVOL-TOD) -- never reimplemented here
        (06-RESEARCH Don't Hand-Roll). Persisted keyed by `day` so the replay
        clock's now_et().date() reads in SignalEngine hit these rows.
        """
        daily_data = self._feed.daily_bars(symbols)
        intraday_5m_data = self._feed.intraday_5m_for_tod(symbols)
        day_ts = self._parse_day(day)

        candidates = []
        for code in symbols:
            yf_symbol = code.removeprefix("US.")

            today_price = self._feed.synthetic_today_price(code, day)
            candidate = _evaluate_symbol(yf_symbol, daily_data, self._cfg, day, today_price)
            if candidate is not None:
                candidates.append(candidate)

            # RVOL-TOD baseline: _compute_tod_baselines expects the RAW per-ticker
            # frame (capital "Volume") -- never routed through get_ticker_frame
            # (06-02 decision; that lowercasing is only for _evaluate_symbol's path).
            try:
                frame_5m = intraday_5m_data[yf_symbol]
            except (KeyError, TypeError):
                frame_5m = None
            if frame_5m is not None and not frame_5m.empty:
                # No-look-ahead (BT-02): restrict to sessions STRICTLY prior to
                # `day`, mirroring _evaluate_symbol's own date < scan_date cutoff
                # -- a live production fetch (run pre-market) naturally excludes
                # today's own bars; a backtest replaying historical data must
                # exclude them explicitly or the baseline is self-referential.
                frame_5m = self._prior_sessions_only(frame_5m, day_ts)
                if not frame_5m.empty:
                    baselines = _compute_tod_baselines(frame_5m, self._cfg.rvol_tod_lookback_days)
                    if baselines:
                        self._store.upsert_tod_baselines(str(day), code, baselines)

        if candidates:
            # persist_watchlist requires a "rank" key per candidate (gap-ranked,
            # capped at _WATCHLIST_CAP BEFORE ranking -- mirrors run_daily_scan's own
            # sort -> cap -> rank sequence, SCAN-08/CR-06). Only capped codes may enter;
            # an uncapped --symbols code that failed the daily filter never trades.
            ranked = sorted(candidates, key=lambda c: c["gap_pct"], reverse=True)
            capped = ranked[:_WATCHLIST_CAP]
            for i, c in enumerate(capped):
                c["rank"] = i + 1
            self._store.persist_watchlist(day, capped, "backtest")
            self._watchlist_by_day[str(day)] = {c["code"] for c in capped}
        else:
            self._watchlist_by_day[str(day)] = set()

        # CR-01: STORE the day's premarket highs -- do NOT apply them here. Applying
        # immediately clobbers signal_engine's single shared dict for every other day
        # already setup (run.py's driver calls setup_day for ALL days before run()
        # replays any of them); replay_day applies the correct day's highs instead.
        self._premarket_highs_by_day[day] = self._feed.premarket_highs(day)

        self._days.append(day)

    # ============================================================
    # Replay loop (ports bot.service.bot.TradingBot._process_bar bar-for-bar)
    # ============================================================

    async def replay_day(self, day) -> None:
        """Replay every bar of `day` chronologically through the reused pipeline."""
        # CR-01: apply THIS day's own frozen premarket highs before any bar of this
        # day is processed -- Gate 1 must never see a later/earlier day's highs.
        self.signal_engine.set_premarket_highs(self._premarket_highs_by_day.get(day, {}))

        for bar_data in self._feed.replay(day):
            await self._process_bar(bar_data)

    async def run(self) -> None:
        """Replay every day seeded via setup_day, in order.

        Rebinds bot.signal.signal_engine.now_et to the harness's replay clock for
        the duration of the run (T-06-11) -- restored in finally so the patch
        never leaks to other code in-process.
        """
        original_now_et = _signal_engine_module.now_et
        _signal_engine_module.now_et = lambda: self._replay_clock
        try:
            for day in self._days:
                await self.replay_day(day)
        finally:
            _signal_engine_module.now_et = original_now_et

    async def _process_bar(self, bar_data: dict) -> None:
        """Single-bar pipeline -- ports bot.service.bot.TradingBot._process_bar.

        Sequence (matches _process_bar's ordering exactly):
          1. Set the replay clock to this bar's ET time BEFORE any gate/lookup
             reads now_et() (entry-window gate + session_date_str baseline keys).
          2. sim_execution.on_bar(bar_data) -- records the anchor manage_exit
             needs for its own next-bar lookup (06-03 deviation), and MUST run
             before position_manager.on_bar since a stop-out triggered on this
             bar needs "after" = THIS bar's time_key, not the prior bar's.
          3. Append bar_data to bar_buffer[code] BEFORE position_manager.on_bar
             (Pitfall 6 -- swing-low trail wiring).
          4. position_manager.on_bar(bar) -- ALWAYS runs (management is never
             gated by an entries-enabled flag; D-06 anti-pattern avoided).
          5. Entry branch: signal_engine.on_bar -> risk_engine.on_signal ->
             sim_execution.consume_intent -> register_position/on_fill/
             arm_stop_protection (no-ops); abandon path resolves the intent.
          6. Capture any position newly CLOSED by this bar into trade_log.

        No _entries_enabled / circuit-breaker side-effect gate -- no kill-switch
        concept in an offline replay (documented choice; see module docstring).
        """
        try:
            bar = BarEvent(
                code=bar_data["code"],
                time_key=bar_data["time_key"],
                open=bar_data["open"],
                high=bar_data["high"],
                low=bar_data["low"],
                close=bar_data["close"],
                volume=bar_data["volume"],
                hod=bar_data["hod"],
                lod=bar_data["lod"],
                cum_volume=bar_data.get("cum_volume", 0),
            )
        except Exception:
            return

        self._replay_clock = self._parse_time_key_et(bar.time_key)

        # Anchor for manage_exit's own next_bar lookup -- must be recorded
        # before position_manager.on_bar may trigger a stop-out this same bar.
        self.sim_execution.on_bar(bar_data)

        buf = self._bar_buffer.setdefault(bar.code, deque(maxlen=_BAR_BUFFER_MAX))
        buf.append(bar_data)

        # Position management always runs first (mirrors _process_bar D-06), and for
        # EVERY bar regardless of watchlist membership -- only entries are gated below.
        await self.position_manager.on_bar(bar)

        # CR-06: only a code in THIS DAY's capped/ranked watchlist may enter -- an
        # unfiltered --symbols code that failed the daily filter never reaches the
        # signal->risk->execution entry chain (mirrors live's watchlist-scoped
        # subscriptions, SIG-01; management above is never gated).
        day_key = bar.time_key[:10]
        if bar.code not in self._watchlist_by_day.get(day_key, set()):
            self._capture_closed_trades()
            return

        signal = await self.signal_engine.on_bar(bar)
        if signal is not None:
            intent = await self.risk_engine.on_signal(signal)
            if intent is not None:
                fill = await self.sim_execution.consume_intent(intent)
                if fill is not None:
                    # SimulatedExecution stores FillEvent.fill_time as the bar's raw
                    # time_key STRING (06-03 decision, matches feed.py/fixtures.py's own
                    # convention) even though the dataclass annotates it `datetime`.
                    # bot.position.manager._on_entry_fill later calls
                    # fill_time.isoformat() when persisting -- normalise to a real ET
                    # datetime here so that still works (harness-side adapter; does not
                    # touch backtester/execution.py).
                    fill.fill_time = self._parse_time_key_et(fill.fill_time)

                    pos = PositionState(
                        position_id=str(uuid4()),
                        code=intent.code,
                        phase=PositionPhase.AWAITING_FILL,
                        entry_price=intent.entry_price,
                        initial_stop=intent.stop_price,
                        trail_stop=intent.stop_price,
                        full_quantity=intent.quantity,
                        remaining_quantity=intent.quantity,
                        entry_order_id=fill.order_id,
                        # StateStore's positions.opened_at column is NOT NULL (migration
                        # 0001) but bot.service.bot._process_bar's own construction leaves
                        # it None until _on_entry_fill sets it later -- against a REAL
                        # (migrated) StateStore that ordering crashes register_position's
                        # DB-first write. Set it here from the fill's own bar time (the
                        # actual entry moment) so the harness never hits that constraint;
                        # apply_entry_fill's later assignment of the SAME value is a no-op.
                        opened_at=fill.fill_time,
                        updated_at=fill.fill_time,
                    )
                    self.position_manager.register_position(pos)
                    self.position_manager.on_fill(fill)
                    await self.position_manager.arm_stop_protection(pos)  # no-op, gateway=None
                else:
                    self._store.resolve_pending_intent(intent.intent_id, "ABANDONED")
                self.signal_engine.note_intent_resolved()

        self._capture_closed_trades()

    # ============================================================
    # Trade-log capture (no `trades` DB table is ever written for a backtest)
    # ============================================================

    def _capture_closed_trades(self) -> None:
        """Append a trade-log row for any position newly reaching CLOSED.

        exit_price is the qty-weighted average of the exit fills SimulatedExecution
        recorded for that code since the last capture (partial + final stop/force
        legs); r_multiple = (exit_price - entry_price) / (entry_price - initial_stop).
        """
        for pos in self.position_manager._positions.values():
            if pos.phase != PositionPhase.CLOSED:
                continue
            if pos.position_id in self._captured_position_ids:
                continue

            code = pos.code
            all_code_fills = [f for f in self.sim_execution.exit_fills if f["code"] == code]
            start = self._exit_fills_consumed.get(code, 0)
            new_fills = all_code_fills[start:]
            self._exit_fills_consumed[code] = len(all_code_fills)

            total_qty = sum(f["qty"] for f in new_fills if f.get("exit_price") is not None)
            if total_qty > 0:
                exit_price = (
                    sum(f["exit_price"] * f["qty"] for f in new_fills if f["exit_price"] is not None)
                    / total_qty
                )
            else:
                exit_price = pos.entry_price  # fallback: no recorded fill (defensive)

            risk = pos.entry_price - pos.initial_stop
            r_multiple = (exit_price - pos.entry_price) / risk if risk != 0 else 0.0

            self.trade_log.append({
                "code": code,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "quantity": pos.full_quantity,
                "exit_reason": pos.pending_exit_reason,
                "r_multiple": r_multiple,
                "closed_at": pos.updated_at,
            })
            self._captured_position_ids.add(pos.position_id)

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _parse_time_key_et(time_key: str):
        """Parse a "YYYY-MM-DD HH:MM:SS" time_key (feed.py convention) into a
        tz-aware ET datetime -- the value bot.signal.signal_engine.now_et() must
        return while this bar is being processed (T-06-11)."""
        from datetime import datetime
        return datetime.strptime(time_key, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)

    @staticmethod
    def _parse_day(day):
        """Normalise a `day` (date object or "YYYY-MM-DD" string) to a date."""
        from datetime import date, datetime
        if isinstance(day, date):
            return day
        return datetime.strptime(str(day), "%Y-%m-%d").date()

    @staticmethod
    def _prior_sessions_only(frame_5m, day):
        """Return only the rows of frame_5m with an ET session date STRICTLY
        before `day` (no-look-ahead cutoff, mirrors _evaluate_symbol's own
        date < scan_date mask)."""
        idx = frame_5m.index
        idx_et = idx.tz_convert(ET) if idx.tzinfo is not None else idx.tz_localize("UTC").tz_convert(ET)
        mask = idx_et.date < day
        return frame_5m.loc[mask]
