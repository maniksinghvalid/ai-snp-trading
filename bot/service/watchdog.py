#!/usr/bin/env python3
"""
bot.service.watchdog — OpenDWatchdog: broker health monitor and reconnect handler (SVC-02).

Polls gateway.get_global_state() every ~60s (config-driven, CFG-01). On a detected
OpenD disconnect, pauses new-entry placement within one poll cycle (D-09) while
keeping exits armed. Reconnects with capped exponential backoff (D-10), re-runs
startup_reconcile + re-subscribes feeds before re-enabling entries (D-11), and
fires fire-and-forget Telegram alerts on both disconnect and reconnect (D-12).

Exports: OpenDWatchdog
"""
import asyncio

from bot.safety.audit_log import append_audit
from bot.safety.logger import get_logger

# ============================================================
# Module-level logger
# ============================================================

_logger = get_logger(__name__)

# ============================================================
# OpenDWatchdog
# ============================================================


class OpenDWatchdog:
    """Poll get_global_state() every ~60s; manage bot._entries_enabled on disconnect/reconnect.

    Implements SVC-02:
      - D-09: disconnect pauses entries within one poll cycle (never disables exits)
      - D-10: reconnect uses capped exponential backoff (initial→cap from rules.json)
      - D-11: _on_reconnect awaits startup_reconcile + re-subscribe BEFORE re-enabling entries
      - D-12: fire-and-forget Telegram alert on disconnect AND reconnect

    gateway:  MoomooGateway — provides get_global_state() (async), connect() (sync),
              startup_reconcile() (async), subscribe() (async).
    bot:      TradingBot — watchdog toggles bot._entries_enabled (D-09/D-11).
    alerter:  TelegramAlerter — fire-and-forget send() dispatched via create_task (D-12).
    cfg:      StrategyConfig — watchdog_poll_interval_s, watchdog_reconnect_initial_s,
              watchdog_reconnect_cap_s (CFG-01).
    """

    def __init__(self, gateway, bot, alerter, cfg) -> None:
        """Initialise OpenDWatchdog with all injected dependencies.

        Optimistic initial state: assumes OpenD is connected at startup (the
        _readiness_gate in TradingBot.run already verified connectivity before
        the watchdog starts polling).

        gateway:  MoomooGateway
        bot:      TradingBot — reference to toggle _entries_enabled
        alerter:  TelegramAlerter
        cfg:      StrategyConfig with watchdog_* timing fields
        """
        self._gateway = gateway
        self._bot = bot
        self._alerter = alerter
        self._cfg = cfg
        self._connected = True  # optimistic: _readiness_gate succeeded before run() starts

    # ============================================================
    # Main poll loop
    # ============================================================

    async def run(self) -> None:
        """Main watchdog coroutine — runs for the lifetime of the bot.

        Mirrors the reconciliation_loop shape from bot/gateway/gateway.py:
        while True → sleep → try _check_once / except CancelledError re-raise /
        except Exception log-not-fatal (D-01/D-02).

        Cancelled by TradingBot._shutdown() (asyncio.Task.cancel).
        """
        poll_interval = self._cfg.watchdog_poll_interval_s
        while True:
            await asyncio.sleep(poll_interval)
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error("watchdog_poll_error", exc_info=True)

    # ============================================================
    # State detection
    # ============================================================

    async def _check_once(self) -> None:
        """Single watchdog poll cycle.

        Reads current OpenD state from gateway.get_global_state() (never raises).
        Transitions:
          connected → disconnected:  calls _on_disconnect()
          disconnected → connected:  calls _on_reconnect()
          no change:                 no-op
        """
        state = await self._gateway.get_global_state()
        currently_connected = bool(state.get("connected", False))

        if currently_connected and not self._connected:
            # Transition: disconnected → connected
            await self._on_reconnect()
        elif not currently_connected and self._connected:
            # Transition: connected → disconnected
            await self._on_disconnect()

    # ============================================================
    # Disconnect handler (D-09, D-12)
    # ============================================================

    async def _on_disconnect(self) -> None:
        """Handle OpenD disconnect event.

        D-09: sets bot._entries_enabled = False within one poll cycle.
              Exits are NOT disabled — only the entry flag is toggled.
        D-12: fires fire-and-forget Telegram alert via asyncio.create_task.
        D-10: starts exponential backoff reconnect loop via asyncio.create_task.

        Alert text uses a fixed template — no raw exceptions or credentials
        are interpolated (T-05-02-04, Pitfall 4).
        """
        _logger.warning("opend_disconnect_detected")
        self._connected = False

        # D-09: pause new entries; exits stay armed
        self._bot._entries_enabled = False

        # D-12: fire-and-forget disconnect alert
        asyncio.create_task(
            self._alerter.send(
                "<b>OpenD DISCONNECTED</b> — new entries paused. Reconnecting..."
            )
        )

        # Audit (lifecycle event — never blocks)
        try:
            append_audit({"event": "opend_disconnect"})
        except Exception:
            pass

        # D-10: start reconnect loop (non-blocking)
        asyncio.create_task(self._reconnect_loop())

    # ============================================================
    # Reconnect loop (D-10)
    # ============================================================

    async def _reconnect_loop(self) -> None:
        """Exponential backoff reconnect loop (D-10).

        delay starts at cfg.watchdog_reconnect_initial_s; each failed attempt
        doubles delay up to cfg.watchdog_reconnect_cap_s. On a successful
        gateway.connect() (sync → run_in_executor) calls _on_reconnect() and stops.

        Reconnect is attempted indefinitely until the connection is restored or
        the watchdog task is cancelled (bot shutdown).
        """
        delay = self._cfg.watchdog_reconnect_initial_s
        cap = self._cfg.watchdog_reconnect_cap_s

        while not self._connected:
            await asyncio.sleep(delay)
            try:
                loop = asyncio.get_running_loop()
                # gateway.connect() is synchronous — offload to thread executor (D-02)
                await loop.run_in_executor(None, self._gateway.connect)
                # Connection succeeded — run reconcile + re-enable entries
                await self._on_reconnect()
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = min(delay * 2, cap)
                _logger.warning(
                    "reconnect_attempt_failed",
                    next_delay_s=delay,
                    exc_info=True,
                )

    # ============================================================
    # Reconnect handler (D-11, D-12)
    # ============================================================

    async def _on_reconnect(self) -> None:
        """Handle OpenD reconnect event.

        D-11 ordering (mandatory — entries MUST remain disabled until all steps pass):
          1. startup_reconcile(store, position_manager) — broker truth wins
          2. re-subscribe active watchlist feeds
          3. bot._entries_enabled = True

        D-12: fires fire-and-forget Telegram reconnect alert via asyncio.create_task.

        Alert text uses a fixed template (T-05-02-04, Pitfall 4).
        """
        _logger.info("opend_reconnect_detected")
        self._connected = True

        # D-11 Step 1: re-run startup reconcile — broker truth wins (stale state prevention)
        await self._gateway.startup_reconcile(
            self._bot._store, self._bot._position_manager
        )

        # D-11 Step 2: re-subscribe active feeds (if gateway supports it)
        try:
            store = self._bot._store
            if hasattr(store, "get_watchlist_codes"):
                from bot.safety.et_helpers import now_et
                today = now_et().date()
                codes = store.get_watchlist_codes(today)
                if codes:
                    await self._gateway.subscribe(codes)
                    _logger.info("resubscribed_feeds_after_reconnect", count=len(codes))
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("resubscribe_failed_after_reconnect", exc_info=True)

        # D-11 Step 3: re-enable entries AFTER reconcile + re-subscribe (ordering enforced)
        self._bot._entries_enabled = True

        # D-12: fire-and-forget reconnect alert
        asyncio.create_task(
            self._alerter.send(
                "<b>OpenD RECONNECTED</b> — entries re-enabled."
            )
        )

        # Audit (lifecycle event — never blocks)
        try:
            append_audit({"event": "opend_reconnect"})
        except Exception:
            pass

        _logger.info("reconnect_complete_entries_enabled")
