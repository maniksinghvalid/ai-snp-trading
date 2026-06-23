#!/usr/bin/env python3
"""
bot.safety.kill_switch — Graceful shutdown via sentinel file and SIGINT.

KillSwitch monitors a configurable sentinel file and handles SIGINT. When
triggered, it: sets a threading.Event, emits a structured shutdown log line,
invokes all registered state-flush callbacks, and appends an append-only audit
entry via bot.safety.audit_log.append_audit (SAFE-04, SAFE-05).

The trigger is idempotent — registering the same KillSwitch twice or sending
two SIGINT signals flushes state only once.

Exports: KillSwitch
"""
import os
import signal
import threading
from typing import Callable, List

from bot.safety.audit_log import append_audit
from bot.safety.logger import get_logger

# ============================================================
# Constants
# ============================================================

# Default sentinel file path — can be overridden by BOT_KILL_FILE env var
_DEFAULT_SENTINEL = os.path.join(os.getcwd(), ".bot_kill")


# ============================================================
# KillSwitch
# ============================================================

class KillSwitch:
    """Graceful shutdown controller.

    Monitors a sentinel file path and a SIGINT signal. On trigger:
    - Sets the internal shutdown Event (accessible via .event / .triggered).
    - Logs a structured shutdown line.
    - Invokes all registered state-flush callbacks exactly once.
    - Writes an append-only audit entry with event='kill_switch'.

    Usage:
        ks = KillSwitch()
        ks.register_flush(state_store.flush)
        ks.install()                  # wire SIGINT handler
        while not ks.triggered:
            if ks.check_file():
                ks.trigger("sentinel_file")
            ...
    """

    def __init__(self, sentinel_path: str = None) -> None:
        """Initialise the kill switch.

        sentinel_path: str — path to the sentinel file. When this file exists,
            check_file() returns True and the orchestration loop should call
            trigger(). If None, falls back to the BOT_KILL_FILE environment
            variable, then to ./.bot_kill.
        """
        if sentinel_path is not None:
            self._sentinel_path = sentinel_path
        else:
            self._sentinel_path = os.environ.get("BOT_KILL_FILE", _DEFAULT_SENTINEL)

        self._event = threading.Event()
        # Reentrant lock (WR-01): SIGINT is delivered to the main thread between
        # bytecodes, so _handle_signal -> _trigger can re-enter while the same
        # thread already holds this lock (e.g. inside register_flush). A
        # non-reentrant Lock would deadlock the shutdown path; RLock lets the
        # same thread re-acquire it safely.
        self._lock = threading.RLock()
        self._flush_callbacks: List[Callable] = []
        self._triggered_once = False
        self._logger = get_logger(__name__)

    # ---- Public properties ----

    @property
    def event(self) -> threading.Event:
        """The shutdown Event; set when the kill switch is triggered."""
        return self._event

    @property
    def triggered(self) -> bool:
        """True if the kill switch has been triggered."""
        return self._event.is_set()

    @property
    def sentinel_path(self) -> str:
        """The configured sentinel file path."""
        return self._sentinel_path

    # ---- Registration ----

    def register_flush(self, callback: Callable) -> None:
        """Register a state-flush callback.

        The callback is invoked once when the kill switch triggers. Multiple
        callbacks may be registered; they are called in registration order.

        callback: callable — zero-argument callable invoked at shutdown.
        """
        with self._lock:
            self._flush_callbacks.append(callback)

    # ---- Signal installation ----

    def install(self) -> None:
        """Register the SIGINT handler.

        After calling install(), pressing Ctrl-C or sending SIGINT to the
        process will invoke _handle_signal, which converges on _trigger.
        """
        signal.signal(signal.SIGINT, self._handle_signal)

    # ---- Sentinel file check ----

    def check_file(self) -> bool:
        """Return True if the sentinel file exists.

        The orchestration loop polls this; when True, it should call
        trigger('sentinel_file') to initiate graceful shutdown.
        """
        return os.path.isfile(self._sentinel_path)

    # ---- Trigger paths ----

    def trigger(self, reason: str = "manual") -> None:
        """Trigger the kill switch explicitly.

        Typically called by the orchestration loop when check_file() is True.
        Idempotent: a second call does nothing.

        reason: str — human-readable reason for shutdown (used in audit log).
        """
        self._trigger(reason)

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        """SIGINT signal handler.

        Invoked by the OS when SIGINT is received. Converges on _trigger.
        """
        self._trigger("SIGINT")

    # ---- Core trigger (idempotent) ----

    def _trigger(self, reason: str) -> None:
        """Core trigger — idempotent, thread-safe.

        Sets the shutdown Event, logs the shutdown line, runs flush callbacks,
        and appends an audit entry. All steps happen at most once.

        reason: str — shutdown reason string for audit and log.
        """
        with self._lock:
            if self._triggered_once:
                return  # idempotent — do not double-flush
            self._triggered_once = True

        # Set the event so is_set() / triggered returns True
        self._event.set()

        # Structured shutdown log (SAFE-04)
        try:
            self._logger.warning(
                "kill_switch_triggered",
                reason=reason,
                sentinel_path=self._sentinel_path,
            )
        except Exception:
            pass  # logging must not block shutdown

        # Invoke state-flush callbacks
        for cb in self._flush_callbacks:
            try:
                cb()
            except Exception:
                pass  # flush failures must not block audit write

        # Append-only audit entry (SAFE-05)
        append_audit({
            "event": "kill_switch",
            "reason": reason,
            "sentinel_path": self._sentinel_path,
        })
