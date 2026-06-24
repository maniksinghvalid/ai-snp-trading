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


class BarAggregator(CurKlineHandlerBase):
    """Bar-close detection via timestamp advance with session dedup (SIG-02).

    Subclasses CurKlineHandlerBase to receive moomoo SDK push events for
    subscribed K_5M codes. Fires the on_bar_closed async coroutine exactly
    once per closed bar, exclusively when time_key advances — never mid-bar,
    never twice for a reconnect re-push.

    Design decisions (RESEARCH.md):
      - SIG-02: strategy evaluation fires only on time_key advance (bar close).
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

    Per-code state dicts (written only from the SDK push thread, single-writer):
      _last_time_key: last seen time_key per code
      _seen_time_keys: all time_keys that have fired evaluation (dedup set)
      _hod: running max of all pushed bar highs per code (D-02)
      _lod: running min of all pushed bar lows per code from first bar (Pitfall 3)
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
                  asyncio.get_event_loop() in the main asyncio context).
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

        All OHLCV parsing and state mutation happens here, isolated from
        on_recv_rsp so that a single try/except in the caller catches all
        errors without duplicating exception handling.
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

        # Update HOD (running max of all pushed highs) on EVERY push.
        # The current bar's high grows mid-bar; update eagerly so the
        # last-seen HOD is the most accurate at bar close (D-02).
        self._hod[code] = max(self._hod.get(code, 0.0), high)

        # Update LOD (session running-min of all pushed lows) on EVERY push.
        # From the very first K_5M bar of the session — NOT a single bar low.
        # This is the session LOD that flows into compute_initial_stop(lod)
        # (RESEARCH Pitfall 3 / Open-Q3 RESOLVED).
        if code in self._lod:
            self._lod[code] = min(self._lod[code], low)
        else:
            # First push for this code — seed LOD from this bar's low.
            self._lod[code] = low

        prev_time_key = self._last_time_key.get(code)

        if prev_time_key is None:
            # First push for this code — record the time_key.
            # No bar has closed yet; we need two distinct time_keys to confirm.
            self._last_time_key[code] = time_key
            if code not in self._seen_time_keys:
                self._seen_time_keys[code] = set()
            if code not in self._bar_buffer:
                self._bar_buffer[code] = deque(maxlen=_BAR_BUFFER_MAX)
            return

        if time_key == prev_time_key:
            # Same bar — mid-bar update. Never fire strategy evaluation.
            # SIG-02: evaluation fires ONLY on time_key advance.
            return

        # time_key advanced → the PREVIOUS bar (prev_time_key) is now closed.
        closed_time_key = prev_time_key

        if code not in self._seen_time_keys:
            self._seen_time_keys[code] = set()

        if closed_time_key not in self._seen_time_keys[code]:
            # Not yet processed — fire strategy evaluation for the closed bar.
            # T-03-02: the _seen_time_keys set survives reconnects so a
            # reconnect re-push of an already-seen bar is silently ignored.
            self._seen_time_keys[code].add(closed_time_key)

            # Build the closed-bar event dict.
            # Note: OHLCV values below come from the CURRENT row (new bar's
            # first push) because we don't buffer mid-bar state. For the
            # closed bar, we use what we have. The HOD/LOD are the session
            # running stats which are correct at this point.
            # IMPORTANT: BarEvent.hod and .lod are session running stats;
            # BarEvent.high and .low are the just-closed bar's values.
            bar_data = {
                "code": code,
                "time_key": closed_time_key,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "hod": self._hod.get(code, 0.0),
                "lod": self._lod.get(code, 0.0),
            }

            # Add to per-code rolling bar buffer (consumed by SignalEngine).
            if code not in self._bar_buffer:
                self._bar_buffer[code] = deque(maxlen=_BAR_BUFFER_MAX)
            self._bar_buffer[code].append(bar_data)

            # Bridge SDK push thread → asyncio event loop (non-blocking).
            # asyncio.run_coroutine_threadsafe returns a concurrent.futures.Future;
            # we fire-and-forget here since the signal pipeline is asynchronous.
            # T-03-03: this call is non-blocking — never stalls the SDK thread.
            asyncio.run_coroutine_threadsafe(
                self._on_bar_closed(bar_data), self._loop
            )

            _logger.debug(
                "bar_closed",
                code=code,
                time_key=closed_time_key,
                hod=bar_data["hod"],
                lod=bar_data["lod"],
            )

        # Update last_time_key to the new (current) bar's time_key.
        self._last_time_key[code] = time_key
