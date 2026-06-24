#!/usr/bin/env python3
"""
bot.signal.bar_aggregator — CurKlineHandlerBase subclass for bar-close detection.

Bridges moomoo SDK push thread → asyncio event loop for the signal engine.
Bar-close detected via timestamp-advance + session-level dedup (SIG-02).

The moomoo SDK fires on_recv_rsp on EVERY intrabar tick, not just at bar close.
BarAggregator tracks _last_time_key per code and fires the on_bar_closed
coroutine only when time_key advances (a new bar opened → previous bar closed).
A session-level _seen_time_keys set prevents double-firing on reconnect re-push.
"""

import asyncio
from collections import deque
from typing import Callable, Dict, Optional, Set

# moomoo SDK imported directly (D-02 wrap-not-import; mirrors gateway.py pattern).
# try/except guard so the import error message is clear; tests stub the base class.
try:
    from moomoo import CurKlineHandlerBase, RET_OK
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "moomoo-api is not installed. Run: pip install 'moomoo-api>=10.4.6408,<11.0'"
    ) from exc

from bot.safety.logger import get_logger

_logger = get_logger(__name__)

# Maximum closed bars to keep per code in the rolling buffer.
# ~4 hours of 5m bars + headroom; consumed by SignalEngine for passes_intraday_filters.
_BAR_BUFFER_MAX = 50


def _log_future_exception(fut) -> None:
    """Done-callback: log any exception captured in an asyncio Future.

    Attached to every run_coroutine_threadsafe future so exceptions in
    on_bar_closed (e.g. StateStore write error, gateway exception, sizing bug)
    surface in the structured log rather than being swallowed silently (WR-02).
    The SDK thread remains non-blocking — this callback executes on the
    concurrent.futures machinery, not the SDK thread itself.
    """
    exc = fut.exception()
    if exc is not None:
        _logger.error(
            "bar_aggregator_callback_error",
            exc_info=exc,
            reason="exception in on_bar_closed coroutine (async pipeline)",
        )


class BarAggregator(CurKlineHandlerBase):
    """Bar-close detection via timestamp advance with session dedup (SIG-02).

    Subclasses CurKlineHandlerBase to receive moomoo SDK push events for
    subscribed K_5M codes. Fires the on_bar_closed async coroutine exactly
    once per closed bar, exclusively when time_key advances — never mid-bar,
    never twice for a reconnect re-push.

    Design decisions (RESEARCH.md):
      - SIG-02 no-repaint: the emitted closed BarEvent carries the OHLCV of
        the bar that just CLOSED (bar A's FINAL values), NOT the new bar's
        (bar B's) first push. _cur_bar[code] buffers the in-progress bar's
        latest OHLCV per code; on time_key advance it provides the snapshot
        for the closing bar.
      - HOD/LOD snapshot: HOD/LOD are captured BEFORE updating with the new
        bar B's first tick, so the closed bar's session stats exclude bar B.
        HOD/LOD are then updated with bar B's values for the next advance.
      - Pitfall 1: _seen_time_keys persists across SDK reconnects for the
        session lifetime; reset ONLY at reset_session() (start of day).
      - Pitfall 3: _lod tracks the session running-min of all pushed bar lows
        from the very first K_5M bar — NOT the individual bar's low.
      - Anti-Pattern: never call asyncio.get_event_loop() from on_recv_rsp
        (the SDK push thread). The loop is obtained at construction time and
        passed in via __init__.
      - Anti-Pattern: never block in on_recv_rsp. The asyncio bridge uses
        asyncio.run_coroutine_threadsafe (non-blocking fire-and-forget).
      - T-03-01: malformed rows are swallowed by try/except; no crash, no fire.
      - T-03-03: SDK thread is never blocked — bridge call is non-blocking.
      - WR-02: the Future returned by run_coroutine_threadsafe has a
        done-callback (_log_future_exception) so on_bar_closed exceptions are
        logged rather than silently dropped.

    Per-code state dicts (written ONLY from the SDK push thread; single-writer
    so no locking is needed for writes. Thread-safety note: the snapshot dict
    passed to run_coroutine_threadsafe is a freshly constructed plain dict —
    immutable after construction — so the asyncio loop thread sees a consistent
    view. Never expose the mutable _hod/_lod/_cur_bar dicts to off-thread
    readers; always pass snapshots):
      _last_time_key: last seen time_key per code
      _seen_time_keys: all time_keys that have fired evaluation (dedup set)
      _hod: running max of all pushed bar highs per code (D-02)
      _lod: running min of all pushed bar lows per code from first bar (Pitfall 3)
      _cur_bar: in-progress bar's latest OHLCV per code (open/high/low/close/volume)
      _bar_buffer: rolling deque of closed-bar dicts per code (maxlen=50)
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_bar_closed: Callable,
    ) -> None:
        """Initialise BarAggregator with the running asyncio loop and callback.

        Args:
            loop: The asyncio event loop owned by the main bot thread.
                  Must be obtained BEFORE creating BarAggregator (e.g. via
                  asyncio.get_running_loop() in the main asyncio context).
                  Never call asyncio.get_event_loop() inside on_recv_rsp.
            on_bar_closed: Async coroutine to invoke with bar_data dict on
                  each bar close. Signature: async def f(bar_data: dict) -> None.
        """
        super().__init__()
        self._loop: asyncio.AbstractEventLoop = loop
        self._on_bar_closed: Callable = on_bar_closed
        # Per-code mutable state — only written from SDK push thread (GIL protects).
        self._last_time_key: Dict[str, str] = {}
        self._seen_time_keys: Dict[str, Set[str]] = {}
        self._hod: Dict[str, float] = {}
        self._lod: Dict[str, float] = {}
        self._cur_bar: Dict[str, dict] = {}  # in-progress bar snapshot (SIG-02 no-repaint)
        self._bar_buffer: Dict[str, deque] = {}

    def reset_session(self) -> None:
        """Clear all per-session state at market open (called once at start of day).

        DO NOT call on SDK reconnect — the _seen_time_keys dedup set must survive
        reconnects to prevent double-firing an already-processed bar (Pitfall 1).
        Only reset when starting a genuinely new trading session.
        """
        self._last_time_key.clear()
        self._seen_time_keys.clear()
        self._hod.clear()
        self._lod.clear()
        self._cur_bar.clear()
        self._bar_buffer.clear()
        _logger.info("bar_aggregator_session_reset")

    def on_recv_rsp(self, rsp_pb):
        """Handle a K_5M push tick from the moomoo SDK push thread.

        Called on the SDK's background thread for every intrabar tick for every
        subscribed code. Detects bar close via time_key advance, deduplicates
        across reconnects, maintains HOD/LOD running stats, and bridges closed-bar
        events to the asyncio loop non-blocking (T-03-03).

        Args:
            rsp_pb: Raw protobuf response from the moomoo SDK.

        Returns:
            (ret, data) tuple from super().on_recv_rsp — mirrors CurKlineHandlerBase
            return contract (push_kline.py pattern).
        """
        ret, data = super().on_recv_rsp(rsp_pb)  # ALWAYS call super first
        if ret != RET_OK or data is None or len(data) == 0:
            return ret, data

        row = data.iloc[0] if hasattr(data, "iloc") else data[0]

        try:
            self._handle_row(row)
        except Exception:
            # T-03-01: Malformed/corrupt push rows are swallowed so one bad push
            # never crashes the SDK thread or blocks subsequent pushes.
            _logger.warning("bar_aggregator_row_error", exc_info=True)

        return ret, data

    def _handle_row(self, row) -> None:
        """Parse one push row and fire on_bar_closed if a bar just closed.

        SIG-02 no-repaint invariant: the BarEvent emitted for closed bar A
        carries bar A's FINAL OHLCV (the last mid-bar push for bar A) and the
        session HOD/LOD up to and including bar A — NOT bar B's first tick values.

        Algorithm:
          1. Parse code, time_key, OHLCV from current row (may be bar A mid-bar
             or bar B's first push).
          2. Update _cur_bar[code] with the new OHLCV (always; this is bar A's
             evolving snapshot, or bar B's initial state after the advance).
          3. If this is the FIRST push for the code: initialise state and return.
          4. If same time_key (mid-bar update for bar A): also update HOD/LOD
             eagerly and return without firing.
          5. If time_key advanced (bar B's first push):
             a. Snapshot HOD/LOD BEFORE updating with bar B's values (so the
                closed bar A's session stats exclude bar B's first tick).
             b. Retrieve bar A's FINAL OHLCV from _cur_bar[code] (set in step 2,
                which captured the PREVIOUS row — bar A's last push — because
                _cur_bar is updated BEFORE the advance check).
             c. Update HOD/LOD with bar B's values (for the next advance).
             d. Emit the closed-bar event for bar A using the OHLCV and HOD/LOD
                snapshots captured in steps 5a–5b.
        """
        code = str(row.get("code", "") or "")
        time_key = str(row.get("time_key", "") or "")
        if not code or not time_key:
            return

        # Parse OHLCV — default to 0 on any missing or non-numeric value.
        high = float(row.get("high", 0) or 0)
        low = float(row.get("low", 0) or 0)
        open_ = float(row.get("open", 0) or 0)
        close = float(row.get("close", 0) or 0)
        volume = int(float(row.get("volume", 0) or 0))

        prev_time_key = self._last_time_key.get(code)

        if prev_time_key is None:
            # First push for this code — initialise per-code state.
            # No bar has closed yet; we need two distinct time_keys to confirm.
            self._last_time_key[code] = time_key
            self._cur_bar[code] = {
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }
            if code not in self._seen_time_keys:
                self._seen_time_keys[code] = set()
            if code not in self._bar_buffer:
                self._bar_buffer[code] = deque(maxlen=_BAR_BUFFER_MAX)
            # Seed HOD/LOD from this bar's first push.
            self._hod[code] = high
            self._lod[code] = low
            return

        if time_key == prev_time_key:
            # Same bar — mid-bar update. Never fire strategy evaluation.
            # SIG-02: evaluation fires ONLY on time_key advance.
            # Update _cur_bar with the latest OHLCV so when bar A closes,
            # _cur_bar[code] holds bar A's FINAL values (not an earlier mid-bar).
            self._cur_bar[code] = {
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }
            # Update HOD/LOD eagerly on every mid-bar push so the running
            # stats are up-to-date at bar close.
            self._hod[code] = max(self._hod.get(code, 0.0), high)
            if code in self._lod:
                self._lod[code] = min(self._lod[code], low)
            else:
                self._lod[code] = low
            return

        # ----------------------------------------------------------------
        # time_key advanced → bar at prev_time_key (bar A) has just closed.
        # Current row is bar B's first push — do NOT use its OHLCV for bar A.
        # ----------------------------------------------------------------
        closed_time_key = prev_time_key

        if code not in self._seen_time_keys:
            self._seen_time_keys[code] = set()

        if closed_time_key not in self._seen_time_keys[code]:
            # Not yet processed — fire strategy evaluation for the closed bar.
            # T-03-02: the _seen_time_keys set survives reconnects so a
            # reconnect re-push of an already-seen bar is silently ignored.
            self._seen_time_keys[code].add(closed_time_key)

            # SIG-02 no-repaint: retrieve bar A's FINAL OHLCV from _cur_bar.
            # _cur_bar[code] was last written during bar A's last mid-bar push,
            # so it holds bar A's final open/high/low/close/volume.
            closed_ohlcv = self._cur_bar.get(code, {
                "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0,
            })

            # Snapshot HOD/LOD BEFORE updating with bar B's first tick.
            # BarEvent.hod = session running max of all ticks UP TO AND INCLUDING
            # bar A's last push (excludes bar B's first tick).
            # BarEvent.lod = session running min of all pushed lows from the first
            # K_5M bar up to and including bar A (RESEARCH Pitfall 3).
            snap_hod = self._hod.get(code, 0.0)
            snap_lod = self._lod.get(code, 0.0)

            # Now update HOD/LOD with bar B's first tick (for the next advance).
            self._hod[code] = max(snap_hod, high)
            if code in self._lod:
                self._lod[code] = min(self._lod[code], low)
            else:
                self._lod[code] = low

            # Store bar B's initial OHLCV as the new in-progress bar state.
            self._cur_bar[code] = {
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }

            # Build the closed-bar event dict using bar A's FINAL values.
            bar_data = {
                "code": code,
                "time_key": closed_time_key,
                "open": closed_ohlcv["open"],
                "high": closed_ohlcv["high"],
                "low": closed_ohlcv["low"],
                "close": closed_ohlcv["close"],
                "volume": closed_ohlcv["volume"],
                "hod": snap_hod,  # session max EXCLUDING bar B's first tick
                "lod": snap_lod,  # session min EXCLUDING bar B's first tick
            }

            # Add to per-code rolling bar buffer (consumed by SignalEngine).
            if code not in self._bar_buffer:
                self._bar_buffer[code] = deque(maxlen=_BAR_BUFFER_MAX)
            self._bar_buffer[code].append(bar_data)

            # Bridge SDK push thread → asyncio event loop (non-blocking).
            # run_coroutine_threadsafe returns a concurrent.futures.Future.
            # Attach a done-callback so exceptions in on_bar_closed surface in
            # the structured log rather than being silently dropped (WR-02).
            # T-03-03: this call is non-blocking — never stalls the SDK thread.
            fut = asyncio.run_coroutine_threadsafe(
                self._on_bar_closed(bar_data), self._loop
            )
            fut.add_done_callback(_log_future_exception)

            _logger.debug(
                "bar_closed",
                code=code,
                time_key=closed_time_key,
                close=closed_ohlcv["close"],
                hod=snap_hod,
                lod=snap_lod,
            )

        else:
            # Already seen (reconnect re-push): still need to update state for
            # the new bar B so subsequent ticks are tracked correctly.
            self._hod[code] = max(self._hod.get(code, 0.0), high)
            if code in self._lod:
                self._lod[code] = min(self._lod[code], low)
            else:
                self._lod[code] = low
            self._cur_bar[code] = {
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }

        # Update last_time_key to the new (current) bar's time_key.
        self._last_time_key[code] = time_key
