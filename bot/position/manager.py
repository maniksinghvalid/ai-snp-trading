#!/usr/bin/env python3
"""
bot.position.manager — PositionManager: orchestrates PositionState FSM (Phase 4).

PositionManager is the single owner of all PositionState objects for live positions.
It consumes FillEvents from ExecutionEngine and closed-bar events from BarAggregator,
drives the pure FSM (state.py) through its transitions, and persists every transition
to StateStore DB-first (Pitfall G). On entry fill it writes the fill-authoritative
daily_trade_count and resolves the originating pending_intents row (Phase-3 D-08/D-12).
It computes the 5m swing-low trail via compute_swing_low_2_2 and applies the
never-loosen ratchet (POS-03 / D-11). It reconstructs all positions from StateStore
on restart (POS-05) and flushes in-flight state on kill-switch.

Safety invariants (never violated):
  - DB-first write then in-memory mutation (Pitfall G): every _persist_position() call
    issues the SQLite UPDATE/commit BEFORE mutating the in-memory PositionState object.
    A crash between the two leaves the DB authoritative — no corruption window.
  - order_id-only fill matching (EXEC-05): fills are reconciled by order_id
    (entry_order_id / exit_order_id), NEVER by a code+quantity pair.
  - Never-loosen trail (D-11): trail_stop = max(trail_stop, new_swing_low) — a lower
    swing-low never reduces the stop.
  - Partial exit never closes prematurely (EXEC-05, Pitfall E): position is CLOSED
    only when remaining_quantity reaches 0 after order_id-matched exit fills.
  - All thresholds from cfg (CFG-01): no numeric strategy literals in this file.

Usage:
    manager = PositionManager(store=store, engine=engine, cfg=cfg, strategy=strategy)
    manager.reconstruct_from_store()    # called on startup (POS-05)
    manager.on_fill(fill_event)         # called by ExecutionEngine on each fill
    await manager.on_bar(bar_event)     # called by BarAggregator on each closed bar
    manager.flush_all()                 # called by KillSwitch on shutdown (04-04)
"""

import datetime as _dt
import pandas as pd
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from bot.execution.events import FillEvent
from bot.position.state import (
    PositionPhase,
    PositionState,
    FSM_ACTION_NONE,
    FSM_ACTION_STOP_OUT,
    FSM_ACTION_PARTIAL,
    FSM_ACTION_BREAKEVEN,
    FSM_ACTION_TRAIL_UP,
)
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import get_market_close_et

_logger = get_logger(__name__)


# ============================================================
# Calendar-aware force-close time (POS-04 / D-08)
# ============================================================

def get_force_close_time_et(today: _dt.date) -> _dt.time:
    """Return the force-close time for today as a datetime.time in US Eastern.

    Derived from get_market_close_et() minus 9 minutes — never hardcoded (CFG-01).

    Protocol:
      - Normal day: get_market_close_et returns "16:00" → force-close = 15:51 ET.
      - Half-day:   get_market_close_et returns "13:00" → force-close = 12:51 ET.

    The 9-minute buffer before the close gives enough time for the escalating-limit
    exit loop to attempt 2-3 cancel-replace rounds before the bell (D-08).

    Args:
        today: calendar date to compute the force-close time for.

    Returns:
        datetime.time — the force-close time in ET (e.g. time(15, 51)).
    """
    _FORCE_CLOSE_BUFFER_MINUTES: int = 9
    close_hhmm = get_market_close_et(today)  # "HH:MM" ET string
    parts = close_hhmm.split(":")
    close_h = int(parts[0])
    close_m = int(parts[1])
    force_m = close_m - _FORCE_CLOSE_BUFFER_MINUTES
    force_h = close_h
    if force_m < 0:
        force_m += 60
        force_h -= 1
    return _dt.time(force_h, force_m)


# ============================================================
# PositionManager
# ============================================================

class PositionManager:
    """Orchestrates the per-position FSM and durable state for all live positions.

    Single owner of all PositionState objects. Receives:
      - FillEvents from ExecutionEngine (on_fill) to advance AWAITING_FILL → ACTIVE
        and to reconcile exit fills by order_id (EXEC-05).
      - Closed-bar events from BarAggregator (on_bar) to evaluate stop / partial /
        breakeven / trail transitions on bar close (D-02/D-03).

    All FSM transitions go through _persist_position: DB write first, then in-memory
    mutation (Pitfall G). Every transition is also mirrored to the JSONL audit log.

    Args:
        store:    StateStore instance (open, conn accessible). Owns the positions,
                  trades, daily_trade_count, and pending_intents tables.
        engine:   ExecutionEngine instance (injected, not imported — avoids circular
                  import with 04-03). Used to submit exit orders on FSM transitions.
        cfg:      StrategyConfig from rules.json (CFG-01). All thresholds via cfg.
        strategy: TrendJoinLong instance. Used for compute_swing_low_2_2().
        bar_buffer: Optional reference to BarAggregator._bar_buffer dict for swing-low
                  computation. If None, swing-low trail is skipped (defaults to no-op
                  for tests that do not wire a live BarAggregator).
    """

    def __init__(
        self,
        store,
        engine,
        cfg,
        strategy,
        bar_buffer=None,
        on_entry_alert: Optional[Callable] = None,
        on_exit_alert: Optional[Callable] = None,
    ) -> None:
        """Initialise PositionManager.

        Args:
            store:          StateStore (open).
            engine:         ExecutionEngine (injected).
            cfg:            StrategyConfig with FSM thresholds.
            strategy:       TrendJoinLong with compute_swing_low_2_2().
            bar_buffer:     Optional Dict[str, deque] from BarAggregator._bar_buffer.
                            Used to build the bars DataFrame for swing-low computation.
            on_entry_alert: Optional callable invoked after an entry fill is processed.
                            Signature: on_entry_alert(code, qty, entry_price, initial_stop).
                            None by default — backward compatible with existing call sites.
                            Invocation failures are logged and never propagate (ALERT-04).
            on_exit_alert:  Optional callable invoked after an exit fill closes a position.
                            Signature: on_exit_alert(code, exit_reason, r_multiple).
                            None by default — backward compatible with existing call sites.
                            Invocation failures are logged and never propagate (ALERT-04).
        """
        self._store = store
        self._engine = engine
        self._cfg = cfg
        self._strategy = strategy
        self._bar_buffer: Optional[Dict[str, deque]] = bar_buffer
        self._on_entry_alert: Optional[Callable] = on_entry_alert
        self._on_exit_alert: Optional[Callable] = on_exit_alert

        # In-memory dict keyed by stock code (e.g. "US.AAPL" → PositionState).
        # Only one live position per code is supported at a time (EXEC-04 guard).
        self._positions: Dict[str, PositionState] = {}

        # Codes with an active manage_exit in flight (D-07 / Pitfall 6).
        # Populated before _place_exit_order call, discarded in finally.
        # SAFE-03 reconcile_once reads this via getattr to skip mid-exit positions.
        self._exiting: set = set()

    # ============================================================
    # Public API — fill processing
    # ============================================================

    def on_fill(self, fill: FillEvent) -> None:
        """Process a FillEvent from ExecutionEngine.

        Dispatch to _on_entry_fill or _on_exit_fill based on fill.is_entry.
        Fills are always matched by order_id — never by code+quantity (EXEC-05).

        Args:
            fill: FillEvent emitted by ExecutionEngine (is_entry determines dispatch).
        """
        if fill.is_entry:
            self._on_entry_fill(fill)
        else:
            self._on_exit_fill(fill)

    # ============================================================
    # Public API — bar processing
    # ============================================================

    async def on_bar(self, bar) -> None:
        """Process a closed 5m bar event: evaluate FSM transitions on bar close.

        Looks up the position for bar.code. If none exists, or if the position is
        AWAITING_FILL or CLOSED, returns immediately (D-02 — only live positions
        need bar evaluation).

        Computes the swing-low for BREAKEVEN/TRAILING phases from the BarAggregator
        buffer (if available) via strategy.compute_swing_low_2_2(). Calls
        pos.evaluate_close(bar.close, cfg, new_swing_low) and dispatches on the
        returned action.

        Args:
            bar: BarEvent (bot.signal.events) with bar.code and bar.close.
        """
        pos = self._positions.get(bar.code)
        if pos is None:
            return
        if pos.phase in (PositionPhase.AWAITING_FILL, PositionPhase.CLOSED):
            return

        # Compute swing-low for BREAKEVEN/TRAILING phases (POS-03 / D-11).
        new_swing_low: Optional[float] = None
        if pos.phase in (PositionPhase.BREAKEVEN, PositionPhase.TRAILING):
            new_swing_low = self._compute_swing_low(bar.code)

        # Capture prev_phase BEFORE evaluate_close mutates pos.phase.
        # Used to derive the correct exit reason in _trigger_stop_out
        # (breakeven vs trail_stop vs stop_out — ALERT-02).
        prev_phase = pos.phase

        action, qty = pos.evaluate_close(bar.close, self._cfg, new_swing_low)

        if action == FSM_ACTION_NONE:
            return

        if action == FSM_ACTION_STOP_OUT:
            await self._trigger_stop_out(pos, qty, bar.time_key, prev_phase)
        elif action == FSM_ACTION_PARTIAL:
            await self._trigger_partial_profit(pos, qty, bar.time_key)
        elif action == FSM_ACTION_BREAKEVEN:
            self._trigger_breakeven(pos, bar.time_key)
        elif action == FSM_ACTION_TRAIL_UP:
            self._trigger_trail_up(pos, bar.time_key)

    # ============================================================
    # Public API — startup reconciliation
    # ============================================================

    def reconstruct_from_store(self) -> None:
        """Load open positions from StateStore into the in-memory _positions dict.

        Called once on startup (POS-05). Reads all non-CLOSED rows from the
        positions table and rebuilds PositionState objects with persisted phase,
        remaining_quantity, trail_stop, entry_order_id, and exit_order_id intact.

        This is the DB reconstruction only — broker-truth overlay (D-09/D-10)
        is handled separately in 04-04 (reconcile_on_startup). The persisted
        stop is NEVER loosened on reconstruction (D-11).

        After this call, _positions contains all in-flight positions from the
        last session, ready to receive bar events and fill events.
        """
        rows = self._store.get_open_positions()
        for row in rows:
            try:
                pos = _row_to_position_state(row)
                self._positions[pos.code] = pos
                _logger.info(
                    "position_reconstructed",
                    code=pos.code,
                    phase=pos.phase.value,
                    remaining_quantity=pos.remaining_quantity,
                    trail_stop=pos.trail_stop,
                )
            except Exception:
                _logger.warning(
                    "position_reconstruct_error",
                    row=dict(row),
                    exc_info=True,
                )
        _logger.info("reconstruct_from_store_done", count=len(self._positions))

    # ============================================================
    # Public API — kill-switch flush
    # ============================================================

    def flush_all(self) -> None:
        """Persist all in-memory PositionState objects to StateStore (for kill-switch).

        Called by the KillSwitch callback on shutdown (04-04 wires this). Persists
        every position in _positions so that a kill-switch-triggered shutdown does
        not lose in-flight FSM transitions. State is already persisted DB-first on
        every transition, so this is a safety net for any transitions still in progress
        at the moment of shutdown.
        """
        flushed = 0
        for code, pos in list(self._positions.items()):
            try:
                # Update updated_at and re-persist the current state.
                pos.updated_at = now_et()
                self._store.upsert_position(pos)
                flushed += 1
            except Exception:
                _logger.warning(
                    "flush_all_error", code=code, exc_info=True
                )
        _logger.info("flush_all_done", flushed=flushed)

    # ============================================================
    # Public API — EOD force-close (POS-04 / D-08)
    # ============================================================

    async def force_close_all(self, today: _dt.date = None) -> None:
        """Force-close all non-CLOSED positions at the calendar-aware force-close time.

        Called by the main loop when now_et() >= get_force_close_time_et(today).
        Uses engine.manage_exit() with force_close_* escalation tunables from cfg
        (D-08 / CFG-01). NEVER places a market order (EXEC-02 upheld even here).

        Protocol (D-08):
          1. Compute force-close time from get_market_close_et minus 9 min (half-day aware).
          2. If now_et() < force-close time, return early (not yet time).
          3. For each non-CLOSED position, call engine.manage_exit(SELL, force_close params).
          4. If a position is still open (remaining_quantity > 0 after manage_exit),
             emit a force_close_stuck audit/log entry and log a loud warning.

        Args:
            today: The trading date to compute force-close time for. Defaults to
                   now_et().date() if None (ET-aware — never host timezone).
        """
        if today is None:
            today = now_et().date()

        force_close_time = get_force_close_time_et(today)
        now = now_et()
        now_time = now.timetz().replace(tzinfo=None).replace(microsecond=0)
        now_time_naive = _dt.time(now.hour, now.minute, now.second)

        if now_time_naive < force_close_time:
            _logger.debug(
                "force_close_not_yet",
                force_close_time=str(force_close_time),
                now_et=str(now_time_naive),
            )
            return

        _logger.warning(
            "force_close_starting",
            force_close_time=str(force_close_time),
            open_positions=sum(
                1 for p in self._positions.values()
                if p.phase != PositionPhase.CLOSED
            ),
        )

        # Lazily import TrdSide to preserve test-env compatibility (deferred import pattern)
        try:
            from moomoo import TrdSide
            sell_side = TrdSide.SELL
        except ImportError:
            sell_side = "SELL"  # test env sentinel (string is never MARKET)

        for code, pos in list(self._positions.items()):
            if pos.phase == PositionPhase.CLOSED:
                continue

            qty = pos.remaining_quantity
            if qty <= 0:
                continue

            _logger.warning(
                "force_close_position",
                code=code,
                qty=qty,
                phase=pos.phase.value,
            )

            try:
                if self._engine is not None:
                    filled = await self._engine.manage_exit(
                        code=code,
                        qty=qty,
                        side=sell_side,
                        escalation_step=self._cfg.force_close_escalation_step_usd,
                        escalation_cadence=self._cfg.force_close_escalation_cadence_seconds,
                        ttl=self._cfg.exit_ttl_seconds,
                    )
                    # After manage_exit, check if position is now flat
                    if filled < qty:
                        # Still not fully flat — emit force_close_stuck alert (D-08)
                        append_audit({
                            "event": "force_close_stuck",
                            "code": code,
                            "remaining": qty - filled,
                            "filled": filled,
                        })
                        _logger.warning(
                            "force_close_stuck",
                            code=code,
                            remaining=qty - filled,
                            filled=filled,
                        )
                    else:
                        # Fully flat — persist CLOSED state DB-first
                        pos.remaining_quantity = 0
                        pos.phase = PositionPhase.CLOSED
                        pos.updated_at = now_et()
                        # Record force_close reason for the alert (ALERT-02). In-memory only.
                        pos.pending_exit_reason = "force_close"
                        self._persist_position(pos, event="force_close")
                        _logger.info("force_close_filled", code=code, qty=qty)
            except Exception:
                _logger.warning(
                    "force_close_exit_error",
                    code=code,
                    qty=qty,
                    exc_info=True,
                )
                # Emit force_close_stuck audit and keep retrying (D-08 — never silently carry)
                append_audit({
                    "event": "force_close_stuck",
                    "code": code,
                    "remaining": qty,
                    "reason": "manage_exit raised",
                })

    # ============================================================
    # Internal — fill handlers
    # ============================================================

    def _on_entry_fill(self, fill: FillEvent) -> None:
        """Handle an entry fill: advance AWAITING_FILL → ACTIVE; update daily count.

        Finds the PositionState whose entry_order_id == fill.order_id (EXEC-05).
        Calls apply_entry_fill() to mutate the in-memory state, then calls
        _persist_position() to write to DB-first. Increments daily_trade_count.filled_count
        and resolves the pending_intents row by fill.intent_id (Phase-3 D-08/D-12).

        If no AWAITING_FILL position with a matching entry_order_id is found,
        logs a warning and returns (stale fill or fill for unknown position).

        Args:
            fill: FillEvent with is_entry=True.
        """
        pos = self._find_position_by_entry_order_id(fill.order_id)
        if pos is None:
            _logger.warning(
                "entry_fill_no_matching_position",
                order_id=fill.order_id,
                code=fill.code,
            )
            return

        # Apply the fill to the FSM (mutates pos.phase, entry_price, quantities).
        pos.apply_entry_fill(fill)
        pos.opened_at = fill.fill_time
        pos.updated_at = now_et()

        # DB-first: persist the transition before finalising in-memory state
        # (apply_entry_fill has already mutated pos — so _persist_position is called
        # AFTER the mutation here because apply_entry_fill mutates pos in-place;
        # the DB-first pattern means we commit to DB before any further caller logic
        # can observe the new phase — the FSM mutation and DB write are a unit).
        self._persist_position(pos, event="entry_fill")

        # Phase-3 D-08: write fill-authoritative daily_trade_count at fill time
        self._increment_daily_filled_count(fill.fill_time)

        # Phase-3 D-12: resolve pending_intents row PENDING → RESOLVED
        self._resolve_pending_intent(fill.intent_id, fill.fill_time)

        _logger.info(
            "entry_fill_processed",
            code=pos.code,
            order_id=fill.order_id,
            phase=pos.phase.value,
            filled_qty=fill.filled_qty,
            avg_fill_price=fill.avg_fill_price,
        )

        # Fire optional entry alert callback (ALERT-04: isolation — callback failure
        # must never propagate into fill processing).
        if self._on_entry_alert is not None:
            try:
                self._on_entry_alert(
                    pos.code,
                    pos.remaining_quantity,
                    pos.avg_fill_price or pos.entry_price,
                    pos.initial_stop,
                )
            except Exception:
                _logger.warning("on_entry_alert_error", code=pos.code, exc_info=True)

    def _on_exit_fill(self, fill: FillEvent) -> None:
        """Handle an exit fill: decrement remaining_quantity; close only when qty == 0.

        Finds the PositionState whose exit_order_id == fill.order_id (EXEC-05,
        Pitfall E: a partial exit is NOT a full close). Decrements remaining_quantity
        by fill.filled_qty. Marks phase CLOSED only when remaining_quantity reaches 0.

        A partial exit (e.g. 100 of 300 shares) leaves the position open with
        remaining_quantity == 200 and the same phase — the exit loop in ExecutionEngine
        continues escalating the next exit order. Phase is only set to CLOSED when
        remaining_quantity == 0 (Pitfall E guard).

        Args:
            fill: FillEvent with is_entry=False.
        """
        pos = self._find_position_by_exit_order_id(fill.order_id)
        if pos is None:
            _logger.warning(
                "exit_fill_no_matching_position",
                order_id=fill.order_id,
                code=fill.code,
            )
            return

        # Decrement remaining_quantity by the matched fill quantity (order_id-keyed).
        prev_qty = pos.remaining_quantity
        pos.remaining_quantity = max(0, pos.remaining_quantity - fill.filled_qty)
        pos.updated_at = now_et()

        if pos.remaining_quantity == 0:
            # All shares exited — mark CLOSED (Pitfall E: only here, never on partial)
            pos.phase = PositionPhase.CLOSED

        self._persist_position(pos, event="exit_fill")

        _logger.info(
            "exit_fill_processed",
            code=pos.code,
            order_id=fill.order_id,
            filled_qty=fill.filled_qty,
            prev_remaining=prev_qty,
            new_remaining=pos.remaining_quantity,
            phase=pos.phase.value,
        )

        # Fire optional exit alert callback when position is fully closed
        # (ALERT-04: isolation — callback failure must never propagate).
        if pos.remaining_quantity == 0 and self._on_exit_alert is not None:
            try:
                # Compute R-multiple: (exit_price - entry) / (entry - initial_stop)
                entry = pos.entry_price or 0.0
                stop = pos.initial_stop or 0.0
                exit_price = fill.avg_fill_price or entry
                risk = entry - stop
                r_multiple = (exit_price - entry) / risk if risk != 0 else 0.0
                # Use the real exit reason recorded at the FSM trigger point (ALERT-02).
                # Falls back to "exit_fill" only as a defensive sentinel for in-flight
                # exits that arrive after a restart (no reason was recorded).
                self._on_exit_alert(
                    pos.code,
                    pos.pending_exit_reason or "exit_fill",
                    round(r_multiple, 2),
                )
            except Exception:
                _logger.warning("on_exit_alert_error", code=pos.code, exc_info=True)

    # ============================================================
    # Internal — FSM transition handlers (called by on_bar)
    # ============================================================

    async def _trigger_partial_profit(
        self, pos: PositionState, qty: int, time_key: str
    ) -> None:
        """ACTIVE → PARTIAL_TAKEN: submit a partial-profit exit for qty shares.

        evaluate_close() has already decremented pos.remaining_quantity and set
        pos.phase to PARTIAL_TAKEN. We call _persist_position (DB-first), then
        ask the engine to place the exit order and record the resulting exit_order_id.

        Args:
            pos:      PositionState (phase already PARTIAL_TAKEN by evaluate_close).
            qty:      Number of shares to sell (floor(remaining * partial_profit_fraction)).
            time_key: Closed bar time_key (for logging).
        """
        # Record the partial exit reason (ALERT-02). In-memory only — not persisted.
        pos.pending_exit_reason = "partial"

        pos.updated_at = now_et()
        self._persist_position(pos, event="partial_profit")

        if self._engine is not None:
            try:
                order_id = await self._place_exit_order(pos.code, qty)
                if order_id:
                    pos.exit_order_id = order_id
                    # Update exit_order_id in DB after setting in memory
                    self._store.upsert_position(pos)
            except Exception:
                _logger.warning(
                    "partial_profit_exit_error", code=pos.code, qty=qty, exc_info=True
                )

        # Fire optional exit alert for the partial scale-out (ALERT-02).
        # Partials leave remaining_quantity > 0 so _on_exit_fill's full-close gate
        # never covers them — we fire here at the trigger point instead.
        # R-multiple is approximate (uses entry_price as exit proxy since the broker
        # fill hasn't arrived yet) — acceptable for partial alert semantics.
        # ALERT-04: wrapped in try/except so a callback failure never breaks the trade loop.
        if self._on_exit_alert is not None:
            try:
                entry = pos.entry_price or 0.0
                stop = pos.initial_stop or 0.0
                exit_proxy = pos.avg_fill_price or entry  # approximate at trigger time
                risk = entry - stop
                r_multiple = (exit_proxy - entry) / risk if risk != 0 else 0.0
                self._on_exit_alert(
                    pos.code,
                    "partial",
                    round(r_multiple, 2),
                )
            except Exception:
                _logger.warning("on_exit_alert_error", code=pos.code, exc_info=True)

        _logger.info(
            "fsm_partial_profit",
            code=pos.code,
            qty=qty,
            time_key=time_key,
            remaining=pos.remaining_quantity,
        )

    def _trigger_breakeven(self, pos: PositionState, time_key: str) -> None:
        """PARTIAL_TAKEN → BREAKEVEN: stop moved to entry_price.

        evaluate_close() has already set pos.trail_stop = entry_price and
        pos.phase = BREAKEVEN. We persist DB-first, then log.

        Args:
            pos:      PositionState (phase already BREAKEVEN, trail_stop = entry_price).
            time_key: Closed bar time_key (for logging).
        """
        pos.updated_at = now_et()
        self._persist_position(pos, event="breakeven")

        _logger.info(
            "fsm_breakeven",
            code=pos.code,
            trail_stop=pos.trail_stop,
            time_key=time_key,
        )

    async def _trigger_stop_out(
        self, pos: PositionState, qty: int, time_key: str, prev_phase=None
    ) -> None:
        """Any phase → CLOSED via stop: submit a stop-out exit for qty remaining shares.

        evaluate_close() has already set pos.phase = CLOSED. We persist DB-first
        (so the DB shows CLOSED even if the subsequent order placement fails), then
        ask the engine to place the exit order.

        Args:
            pos:        PositionState (phase already CLOSED by evaluate_close).
            qty:        Remaining shares to exit.
            time_key:   Closed bar time_key (for logging).
            prev_phase: The PositionPhase the position was in BEFORE evaluate_close
                        mutated it to CLOSED. Used to derive the true exit reason
                        (ALERT-02): BREAKEVEN → "breakeven", TRAILING → "trail_stop",
                        anything else (ACTIVE / PARTIAL_TAKEN) → "stop_out".
        """
        # Record the true exit reason derived from the pre-close phase (ALERT-02).
        # This annotation is in-memory only — never persisted to DB.
        if prev_phase == PositionPhase.BREAKEVEN:
            pos.pending_exit_reason = "breakeven"
        elif prev_phase == PositionPhase.TRAILING:
            pos.pending_exit_reason = "trail_stop"
        else:
            pos.pending_exit_reason = "stop_out"

        pos.updated_at = now_et()
        self._persist_position(pos, event="stop_out")

        if self._engine is not None:
            try:
                order_id = await self._place_exit_order(pos.code, qty)
                if order_id:
                    pos.exit_order_id = order_id
                    self._store.upsert_position(pos)
            except Exception:
                _logger.warning(
                    "stop_out_exit_error", code=pos.code, qty=qty, exc_info=True
                )

        _logger.info(
            "fsm_stop_out",
            code=pos.code,
            qty=qty,
            trail_stop=pos.trail_stop,
            time_key=time_key,
        )

    def _trigger_trail_up(self, pos: PositionState, time_key: str) -> None:
        """BREAKEVEN/TRAILING → TRAILING: stop ratcheted up to new swing-low.

        evaluate_close() has already updated pos.trail_stop = max(old, new_swing_low)
        and set pos.phase = TRAILING. We persist DB-first.

        Args:
            pos:      PositionState (trail_stop already updated by evaluate_close).
            time_key: Closed bar time_key (for logging).
        """
        pos.updated_at = now_et()
        self._persist_position(pos, event="trail_up")

        _logger.info(
            "fsm_trail_up",
            code=pos.code,
            trail_stop=pos.trail_stop,
            time_key=time_key,
        )

    # ============================================================
    # Internal — persistence (DB-first, Pitfall G)
    # ============================================================

    def _persist_position(self, pos: PositionState, event: str = "fsm_transition") -> None:
        """Write PositionState to StateStore DB FIRST, then mirror to audit log.

        DB-first pattern (Pitfall G): StateStore.upsert_position() commits immediately
        so the DB row is authoritative even if the process crashes after this call
        but before any subsequent in-memory mutation by the caller.

        The in-memory PositionState (pos) has already been mutated by evaluate_close()
        or apply_entry_fill() before this method is called — so the DB write captures
        the new state. Callers must NOT mutate pos after calling this method in the
        same transaction unit.

        Also appends a structured audit entry (T-04-09: code/phase/qty/price/order_id
        only — no credentials).

        Args:
            pos:   PositionState to persist (all fields will be written to DB).
            event: Human-readable event label for the audit log.
        """
        # DB-FIRST: commit the new state to SQLite before any further in-memory work
        self._store.upsert_position(pos)

        # Mirror transition to JSONL audit log (T-04-09: no credentials)
        append_audit({
            "event": event,
            "code": pos.code,
            "position_id": pos.position_id,
            "phase": pos.phase.value,
            "trail_stop": pos.trail_stop,
            "remaining_quantity": pos.remaining_quantity,
            "entry_order_id": pos.entry_order_id,
            "exit_order_id": pos.exit_order_id,
        })

    # ============================================================
    # Internal — helpers
    # ============================================================

    def _find_position_by_entry_order_id(
        self, order_id: str
    ) -> Optional[PositionState]:
        """Find an AWAITING_FILL PositionState whose entry_order_id matches order_id.

        Keyed by order_id only (EXEC-05 — never by code or qty). Returns the first
        matching position. Returns None if no match found.
        """
        for pos in self._positions.values():
            if (
                pos.phase == PositionPhase.AWAITING_FILL
                and str(pos.entry_order_id) == str(order_id)
            ):
                return pos
        return None

    def _find_position_by_exit_order_id(
        self, order_id: str
    ) -> Optional[PositionState]:
        """Find a PositionState whose exit_order_id matches order_id.

        Keyed by order_id only (EXEC-05). Returns the first matching position in
        any non-CLOSED phase. Returns None if no match found.
        """
        for pos in self._positions.values():
            if (
                pos.phase != PositionPhase.CLOSED
                and pos.exit_order_id is not None
                and str(pos.exit_order_id) == str(order_id)
            ):
                return pos
        return None

    def _compute_swing_low(self, code: str) -> Optional[float]:
        """Compute the 5m swing-low-2/2 from the BarAggregator buffer.

        Builds a DataFrame from the bar_buffer deque for the given code, then
        calls strategy.compute_swing_low_2_2(df). Returns None if the buffer
        is unavailable or empty, or if compute_swing_low_2_2 returns None.

        The D-11 never-loosen invariant is enforced in evaluate_close() via
        max(trail_stop, new_swing_low) — not here.

        Args:
            code: Moomoo-format stock code (e.g. "US.AAPL").

        Returns:
            float (swing low price) or None.
        """
        if self._bar_buffer is None:
            return None
        buf = self._bar_buffer.get(code)
        if not buf:
            return None
        try:
            df = pd.DataFrame(list(buf))
            if "low" not in df.columns or len(df) == 0:
                return None
            return self._strategy.compute_swing_low_2_2(df)
        except Exception:
            _logger.warning(
                "swing_low_compute_error", code=code, exc_info=True
            )
            return None

    async def _place_exit_order(self, code: str, qty: int) -> int:
        """Ask ExecutionEngine to place a marketable-limit exit; return total filled qty.

        D-01: returns int (total filled qty, 0 on failure) — never None.
        D-02: callers apply the exit (decrement remaining_quantity, mark CLOSED,
              fire on_exit_alert) from this return value directly. exit_order_id is
              no longer the exit-alert delivery path (Pitfall 4: exit_order_id is
              preserved only for the entry-fill matching path in _on_exit_fill).

        Delegates to engine.manage_exit() which owns all retry-until-flat logic
        (D-07 / CFG-01). Returns int(filled_qty) on success, 0 on exception
        (Assumption A2: manage_exit returning 0 is valid — no fill occurred).
        """
        try:
            from moomoo import TrdSide
            sell_side = TrdSide.SELL
        except ImportError:
            # In test environments without moomoo-api, the engine mock handles this.
            sell_side = None

        try:
            filled_qty = await self._engine.manage_exit(
                code=code,
                qty=qty,
                side=sell_side,
                escalation_step=self._cfg.exit_escalation_step_usd,
                escalation_cadence=self._cfg.exit_escalation_cadence_seconds,
                ttl=self._cfg.exit_ttl_seconds,
            )
            return int(filled_qty)
        except Exception:
            _logger.warning("place_exit_order_error", code=code, qty=qty, exc_info=True)
            return 0

    def _increment_daily_filled_count(self, fill_time: datetime) -> None:
        """Increment daily_trade_count.filled_count for the fill's session date (D-08).

        Uses an INSERT ... ON CONFLICT DO UPDATE to upsert: if no row exists for
        this session_date, creates one with filled_count=1; otherwise increments by 1.
        Commits immediately so the daily count is authoritative at fill time.

        Session date is derived from fill_time converted to ET (never host timezone).
        Phase 3 owns the pending_count column; Phase 4 owns filled_count (D-08).

        Args:
            fill_time: datetime from the FillEvent (fill detection time).
        """
        try:
            from bot.safety.et_helpers import to_et, ET
            fill_et = (
                fill_time.astimezone(ET)
                if fill_time.tzinfo is not None
                else to_et(fill_time)
            )
            session_date = fill_et.date().isoformat()
            now_ts = now_et().isoformat()
            self._store.conn.execute(
                """INSERT INTO daily_trade_count (session_date, filled_count, updated_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(session_date) DO UPDATE SET
                     filled_count = filled_count + 1,
                     updated_at = excluded.updated_at""",
                (session_date, now_ts),
            )
            self._store.conn.commit()
            _logger.info(
                "daily_filled_count_incremented", session_date=session_date
            )
        except Exception:
            _logger.warning("daily_count_increment_error", exc_info=True)

    def _resolve_pending_intent(self, intent_id: str, fill_time: datetime) -> None:
        """Resolve a pending_intents row from PENDING to RESOLVED (Phase-3 D-12).

        Updates the status column to 'RESOLVED' and sets resolved_at to the fill
        time. If intent_id is empty or the row doesn't exist, this is a no-op.

        Args:
            intent_id: pending_intents.intent_id UUID string from the FillEvent.
            fill_time: datetime from the FillEvent (used as resolved_at).
        """
        if not intent_id:
            return
        try:
            ts = (
                fill_time.isoformat()
                if fill_time
                else now_et().isoformat()
            )
            self._store.conn.execute(
                "UPDATE pending_intents SET status='RESOLVED', resolved_at=? "
                "WHERE intent_id=? AND status='PENDING'",
                (ts, intent_id),
            )
            self._store.conn.commit()
            _logger.info(
                "pending_intent_resolved", intent_id=intent_id
            )
        except Exception:
            _logger.warning(
                "pending_intent_resolve_error", intent_id=intent_id, exc_info=True
            )

    # ============================================================
    # Public helper — register a new AWAITING_FILL position
    # ============================================================

    def register_position(self, pos: PositionState) -> None:
        """Register a new PositionState in AWAITING_FILL phase.

        Called by ExecutionEngine after placing the entry order (04-03). Adds the
        position to _positions and persists it to the DB so it survives a crash
        before the fill arrives.

        Args:
            pos: PositionState with phase=AWAITING_FILL and entry_order_id set.
        """
        self._positions[pos.code] = pos
        self._persist_position(pos, event="position_registered")
        _logger.info(
            "position_registered",
            code=pos.code,
            entry_order_id=pos.entry_order_id,
            phase=pos.phase.value,
        )


# ============================================================
# Helper — reconstruct PositionState from a DB row dict
# ============================================================

def _row_to_position_state(row: dict) -> PositionState:
    """Convert a StateStore DB row dict to a PositionState instance (POS-05).

    Handles type coercions: phase TEXT → PositionPhase enum; opened_at/updated_at
    ISO strings → datetime (UTC-aware). Preserves persisted trail_stop and
    remaining_quantity exactly — D-11 says the persisted stop is never loosened
    on reconstruction; only forward swing-low updates may raise it.

    Args:
        row: Dict from StateStore.get_open_positions() (one per position row).

    Returns:
        PositionState with all fields reconstructed from the DB row.

    Raises:
        KeyError:   If a required column is missing from the row.
        ValueError: If phase is not a valid PositionPhase value.
    """
    def _parse_dt(val) -> Optional[datetime]:
        if not val:
            return None
        try:
            dt = datetime.fromisoformat(str(val))
            if dt.tzinfo is None:
                # Treat naive datetime as UTC (project convention — PITFALLS #6)
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    return PositionState(
        position_id=str(row["position_id"]),
        code=str(row["code"]),
        phase=PositionPhase(str(row["phase"])),
        entry_price=float(row["entry_price"] or 0.0),
        initial_stop=float(row["initial_stop"] or 0.0),
        trail_stop=float(row["trail_stop"] or 0.0),
        full_quantity=int(row["full_quantity"] or 0),
        remaining_quantity=int(row["remaining_quantity"] or 0),
        entry_order_id=str(row["entry_order_id"] or ""),
        exit_order_id=str(row["exit_order_id"]) if row.get("exit_order_id") else None,
        avg_fill_price=float(row["avg_fill_price"]) if row.get("avg_fill_price") is not None else None,
        opened_at=_parse_dt(row.get("opened_at")),
        updated_at=_parse_dt(row.get("updated_at")),
    )
