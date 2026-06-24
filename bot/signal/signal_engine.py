#!/usr/bin/env python3
"""
bot.signal.signal_engine — SignalEngine: intraday gate orchestrator (SIG-03/SIG-04, RISK-04/RISK-05).

Consumes closed BarEvents from BarAggregator, applies four independent gates:
  1. Premarket-high guard (D-03): code must have a frozen pre_high_price > 0
  2. Intraday filter pass (SIG-03): passes_intraday_filters() — I1/I2/I3
  3. Entry-window gate (SIG-03): now_et() inside [earliest_entry_et, latest_entry_et)
  4. Concurrent-position cap (SIG-04/RISK-04): broker get_positions() count < max_concurrent_positions
  5. Re-entry gate (D-10): code is BOTH broker-flat AND has no live pending_intents PENDING row
  6. Daily-cap gate (RISK-05/D-09): filled_count + pending_count < max_trades_per_day

Emits a SignalEvent when all gates pass. All thresholds come from StrategyConfig (CFG-01).
No numeric or time literals appear in this module — every boundary is config-driven.

Exports: SignalEngine
"""

import sqlite3
from datetime import datetime, time
from typing import Dict, List, Optional

import pandas as pd

from bot.config.loader import StrategyConfig
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.signal.events import BarEvent, SignalEvent
from bot.state.store import StateStore
from bot.strategy.trend_join_long import TrendJoinLong

_logger = get_logger(__name__)

# moomoo SDK RET_OK constant — match the gateway pattern (avoid top-level SDK import
# that would fail in test environments without moomoo installed).
_RET_OK = 0


class SignalEngine:
    """Orchestrates intraday gate evaluation for every closed 5m BarEvent.

    Constructor:
        cfg: StrategyConfig — all thresholds sourced here (CFG-01).
        gateway: MoomooGateway — async broker I/O (get_positions, get_market_snapshot).
        store: StateStore — open SQLite connection (daily_trade_count + pending_intents).

    Session lifecycle:
        1. At ~09:30 ET, the scheduler calls fetch_premarket_highs(codes) once.
           This reads pre_high_price from a single batched get_market_snapshot call,
           applies the D-03 exclusion (pre_high_price > 0 only), and freezes the
           result via set_premarket_highs().
        2. On every closed bar, the subscriber calls on_bar(event). Each gate
           evaluates independently and short-circuits with a logged reason on failure.
        3. When a SignalEvent is emitted, _pending_count is incremented so
           subsequent on_bar() calls see the correct D-09 tally (burst guard).
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        gateway,
        store: StateStore,
    ) -> None:
        """Initialise the SignalEngine.

        Args:
            cfg: Loaded StrategyConfig (all thresholds; CFG-01).
            gateway: MoomooGateway instance (async broker access).
            store: Open StateStore (SQLite connection for daily_trade_count, pending_intents).
        """
        self._cfg = cfg
        self._gateway = gateway
        self._store = store
        self._strategy = TrendJoinLong(cfg)

        # D-01/D-03: premarket highs frozen at session-init; populated via
        # fetch_premarket_highs() / set_premarket_highs(). Only codes with
        # pre_high_price > 0 at freeze time are included.
        self._premarket_highs: Dict[str, float] = {}

        # D-09 burst guard: in-memory tally of OrderIntents emitted this session.
        # Incremented on each emitted SignalEvent; combined with filled_count from
        # StateStore to enforce the daily cap before any fill registers.
        # Only emitted intents consume a slot (D-11).
        self._pending_count: int = 0

    # ============================================================
    # Session Management
    # ============================================================

    def set_premarket_highs(self, mapping: Dict[str, float]) -> None:
        """Freeze the premarket-high dict for this session (D-01).

        Replaces any previously stored highs. Call once at session-init
        (typically ~09:30 ET after fetch_premarket_highs has returned).

        Args:
            mapping: {code: pre_high_price} — only codes with pre_high_price > 0.
        """
        self._premarket_highs = dict(mapping)
        _logger.info(
            "premarket_highs_frozen",
            count=len(self._premarket_highs),
            codes=list(self._premarket_highs.keys()),
        )

    def note_intent_emitted(self) -> None:
        """Increment the session pending tally (D-09 burst guard).

        Called externally by the downstream pipeline (e.g. RiskEngine) when it
        converts a SignalEvent into an emitted OrderIntent. Exposed so the tally
        is adjustable without direct attribute access.
        """
        self._pending_count += 1

    def note_intent_resolved(self) -> None:
        """Decrement the session pending tally when an intent is resolved.

        Called by Phase 4 when an OrderIntent is filled, expired, or cancelled.
        Guards against underflow so _pending_count is never negative.
        """
        if self._pending_count > 0:
            self._pending_count -= 1

    # ============================================================
    # D-01 Session-Init Source
    # ============================================================

    async def fetch_premarket_highs(self, codes: List[str]) -> Dict[str, float]:
        """Fetch, filter, and freeze premarket highs for the watchlist (D-01/D-03).

        Performs ONE batched get_market_snapshot(codes) call near 09:30 ET, reads
        the `pre_high_price` field per row, and applies the D-03 conservative rule:
        include a code ONLY when pre_high_price > 0. Codes with zero, None, or NaN
        values are EXCLUDED — never fall back to prior-day high (D-03).

        On a non-RET_OK snapshot response, returns an empty dict without raising
        (degraded-gracefully design: no entries fire on the missing reference).

        After computing the valid mapping, calls set_premarket_highs() to freeze
        the result for the session. Phase 5's scheduler calls this on a 09:30 timer
        — the wiring lives here (D-01 scopes the source to Phase 3).

        Args:
            codes: Moomoo-format codes to batch-snapshot (e.g. ["US.AAPL"]).
                   Must be <= 20 items (SIG-01 watchlist cap).

        Returns:
            dict: {code: pre_high_price} for codes with valid (> 0) premarket highs.
        """
        ret, data = await self._gateway.get_market_snapshot(codes)

        if ret != _RET_OK:
            _logger.warning(
                "fetch_premarket_highs_snapshot_failed",
                ret=ret,
                reason="non-RET_OK response from get_market_snapshot",
            )
            self.set_premarket_highs({})
            return {}

        valid: Dict[str, float] = {}

        # data is a DataFrame with columns including 'code' and 'pre_high_price'
        if data is None or (hasattr(data, "__len__") and len(data) == 0):
            self.set_premarket_highs({})
            return {}

        for _, row in data.iterrows():
            code = row.get("code") if hasattr(row, "get") else row["code"]
            raw_price = row.get("pre_high_price") if hasattr(row, "get") else row["pre_high_price"]

            # D-03: include only when pre_high_price is a real positive number
            try:
                price = float(raw_price)
                if pd.isna(price) or price <= 0.0:
                    _logger.info(
                        "premarket_high_excluded",
                        code=code,
                        pre_high_price=raw_price,
                        reason="zero_or_missing",
                    )
                    continue
            except (TypeError, ValueError):
                _logger.info(
                    "premarket_high_excluded",
                    code=code,
                    pre_high_price=raw_price,
                    reason="unparseable",
                )
                continue

            valid[code] = price

        _logger.info(
            "premarket_highs_fetched",
            total_requested=len(codes),
            valid_count=len(valid),
        )
        self.set_premarket_highs(valid)
        return valid

    # ============================================================
    # Entry-Window Gate (SIG-03)
    # ============================================================

    def _in_entry_window(self) -> bool:
        """Return True when the current ET time is within the entry window.

        Window is [earliest_entry_et, latest_entry_et):
          - earliest_entry_et is INCLUSIVE (e.g. 10:05 → bar at exactly 10:05:00 passes)
          - latest_entry_et is EXCLUSIVE (e.g. 15:30 → bar at exactly 15:30:00 fails)

        This is the canonical boundary per RESEARCH Pitfall 6 / CONTEXT boundary definition.
        All values parsed from cfg.earliest_entry_et / cfg.latest_entry_et (HH:MM strings);
        no time literals appear in this method (CFG-01).

        Returns:
            bool: True if now_et().time() is within [earliest, latest).
        """
        # Parse "HH:MM" config strings into datetime.time objects (no literals)
        earliest_h, earliest_m = (int(p) for p in self._cfg.earliest_entry_et.split(":"))
        latest_h, latest_m = (int(p) for p in self._cfg.latest_entry_et.split(":"))

        earliest = time(earliest_h, earliest_m, 0)   # 10:05:00 — INCLUSIVE
        latest = time(latest_h, latest_m, 0)          # 15:30:00 — EXCLUSIVE

        current_time = now_et().time()
        return earliest <= current_time < latest

    # ============================================================
    # StateStore Accessors
    # ============================================================

    def _get_filled_count(self, session_date_str: str) -> int:
        """Read filled_count from daily_trade_count for the given session date.

        Returns 0 if no row exists (session not yet started or first trade of day).
        Phase 3 NEVER writes this table — only Phase 4 increments it at fill (RESEARCH Pitfall 5).

        Args:
            session_date_str: ISO date string (e.g. "2026-06-24").

        Returns:
            int: The current filled_count, or 0 if no row exists.
        """
        row = self._store.conn.execute(
            "SELECT filled_count FROM daily_trade_count WHERE session_date = ?",
            (session_date_str,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def has_pending_intent(self, code: str) -> bool:
        """Return True if there is a live PENDING intent for the given code (D-10).

        Checks the pending_intents table for any row with status='PENDING' for
        the given code. Used by the D-10 re-entry gate to block re-signalling on
        a code that already has an unresolved intent (even when broker-flat).

        Args:
            code: Moomoo-format stock code (e.g. "US.AAPL").

        Returns:
            bool: True if a PENDING row exists for this code.
        """
        row = self._store.conn.execute(
            "SELECT 1 FROM pending_intents WHERE code = ? AND status = 'PENDING' LIMIT 1",
            (code,),
        ).fetchone()
        return row is not None

    # ============================================================
    # Main Signal Evaluation (on_bar)
    # ============================================================

    async def on_bar(self, event: BarEvent) -> Optional[SignalEvent]:
        """Evaluate a closed BarEvent through all entry gates.

        Gate evaluation order (each gate short-circuits independently):
          1. Premarket-high present and > 0 (D-03)
          2. passes_intraday_filters() → I1/I2/I3 (SIG-03)
          3. Entry window [earliest_entry_et, latest_entry_et) (SIG-03)
          4. Concurrent-position cap < max_concurrent_positions (SIG-04/RISK-04)
          5. Re-entry: code broker-flat AND no live PENDING intent (D-10)
          6. Daily cap: filled_count + pending_count < max_trades_per_day (RISK-05/D-09)

        Only emitted SignalEvents increment _pending_count; blocked signals do not
        consume any entry slot (D-11).

        Args:
            event: Closed BarEvent from BarAggregator.

        Returns:
            SignalEvent if all gates pass; None otherwise.
        """
        code = event.code

        # --------------------------------------------------------
        # Gate 1: Premarket-high guard (D-03)
        # --------------------------------------------------------
        premarket_high = self._premarket_highs.get(code)
        if premarket_high is None or premarket_high <= 0.0:
            _logger.info(
                "signal_skipped_no_premarket_high",
                code=code,
                premarket_high=premarket_high,
                reason="D-03: missing or zero premarket high — no entry for this code today",
            )
            return None

        # --------------------------------------------------------
        # Gate 2: Intraday filter (SIG-03: I1/I2/I3)
        # --------------------------------------------------------
        # Fetch rvol from the daily_scan table (RESEARCH Pitfall 2: never recompute RVOL).
        # rvol = current_volume / rvol_baseline. If rvol_baseline is missing/zero → no signal.
        session_date_str = now_et().date().isoformat()
        rvol_baseline_row = self._store.conn.execute(
            "SELECT rvol_baseline FROM daily_scan WHERE scan_date = ? AND code = ?",
            (session_date_str, code),
        ).fetchone()

        rvol_baseline = float(rvol_baseline_row[0]) if rvol_baseline_row else 0.0

        if rvol_baseline <= 0.0:
            _logger.info(
                "signal_skipped_no_rvol_baseline",
                code=code,
                reason="no rvol_baseline in daily_scan for today",
            )
            return None

        rvol = event.volume / rvol_baseline

        # Build the single-row DataFrame for passes_intraday_filters (reads iloc[-1]["close"])
        bars_5m = pd.DataFrame([{
            "open": event.open,
            "high": event.high,
            "low": event.low,
            "close": event.close,
            "volume": event.volume,
        }])

        if not self._strategy.passes_intraday_filters(
            code=code,
            bars_5m=bars_5m,
            premarket_high=premarket_high,
            hod=event.hod,
            rvol=rvol,
        ):
            _logger.info(
                "signal_skipped_filters",
                code=code,
                close=event.close,
                premarket_high=premarket_high,
                hod=event.hod,
                rvol=round(rvol, 3),
                reason="I1/I2/I3 filter did not pass",
            )
            return None

        # --------------------------------------------------------
        # Gate 3: Entry-window time gate (SIG-03)
        # --------------------------------------------------------
        if not self._in_entry_window():
            _logger.info(
                "signal_skipped_outside_window",
                code=code,
                reason="current ET time is outside the entry window",
            )
            return None

        # --------------------------------------------------------
        # Gate 4: Concurrent-position cap (SIG-04/RISK-04) — broker truth
        # --------------------------------------------------------
        ret, positions_data = await self._gateway.get_positions()
        if ret != _RET_OK or positions_data is None:
            _logger.warning(
                "signal_skipped_positions_read_failed",
                code=code,
                ret=ret,
                reason="get_positions() failed — skipping to avoid over-limit entry",
            )
            return None

        # Count distinct open codes from broker truth (any non-empty DataFrame row = open)
        open_position_count = len(positions_data) if hasattr(positions_data, "__len__") else 0

        # Also check if US.AAPL itself is already in open positions (re-entry blocking)
        open_codes: set = set()
        if hasattr(positions_data, "iterrows"):
            for _, row in positions_data.iterrows():
                pos_code = row.get("code") if hasattr(row, "get") else row["code"]
                if pos_code:
                    open_codes.add(pos_code)
        else:
            # Fallback if positions_data is a list of dicts
            open_codes = {p.get("code") for p in positions_data if isinstance(p, dict)}

        if open_position_count >= self._cfg.max_concurrent_positions:
            _logger.info(
                "signal_skipped_concurrent_cap",
                code=code,
                open_position_count=open_position_count,
                max_concurrent_positions=self._cfg.max_concurrent_positions,
                reason="concurrent position cap reached (SIG-04/RISK-04)",
            )
            return None

        # --------------------------------------------------------
        # Gate 5: Re-entry gate (D-10)
        # Requires BOTH broker-flat AND no live PENDING intent
        # --------------------------------------------------------
        if code in open_codes:
            _logger.info(
                "signal_skipped_already_in_position",
                code=code,
                reason="D-10: code is already in an open broker position — no double-entry",
            )
            return None

        if self.has_pending_intent(code):
            _logger.info(
                "signal_skipped_pending_intent",
                code=code,
                reason="D-10: code has a live pending intent — re-entry blocked until resolved",
            )
            return None

        # --------------------------------------------------------
        # Gate 6: Daily new-entry cap (RISK-05 / D-09 burst guard)
        # --------------------------------------------------------
        filled_count = self._get_filled_count(session_date_str)
        total_entries = filled_count + self._pending_count

        if total_entries >= self._cfg.max_trades_per_day:
            _logger.info(
                "signal_skipped_daily_cap",
                code=code,
                filled_count=filled_count,
                pending_count=self._pending_count,
                total_entries=total_entries,
                max_trades_per_day=self._cfg.max_trades_per_day,
                reason="daily entry cap reached (RISK-05/D-09) — no further entries this session",
            )
            return None

        # --------------------------------------------------------
        # All gates passed — emit SignalEvent
        # --------------------------------------------------------
        signal = SignalEvent(
            code=code,
            bar=event,
            premarket_high=premarket_high,
            hod=event.hod,
            lod=event.lod,
            rvol=rvol,
            emitted_at=now_et(),
        )

        # Increment the in-memory pending tally (D-09 burst guard).
        # This ensures a burst of bars in the same session cannot exceed the cap
        # before any fill registers. Only emitted signals consume a slot (D-11).
        self._pending_count += 1

        _logger.info(
            "signal_emitted",
            code=code,
            close=event.close,
            premarket_high=premarket_high,
            hod=event.hod,
            lod=event.lod,
            rvol=round(rvol, 3),
            pending_count=self._pending_count,
        )

        return signal
