#!/usr/bin/env python3
"""
tests.safety.test_kill_switch — Unit tests for bot.safety.kill_switch.

Verifies:
- check_file() returns True when sentinel file exists
- trigger() sets the shutdown Event
- Registered flush callbacks are invoked on trigger
- Triggering appends a 'kill_switch' JSONL audit entry
- SIGINT handler (_handle_signal) produces same behavior as trigger()
- Idempotency: triggering twice calls flush once and writes one audit entry
- KillSwitch imports append_audit from bot.safety.audit_log (no redefinition)
"""
import json
import os
import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture()
def sentinel_file(tmp_path):
    """A path inside tmp_path for the sentinel file (does not exist initially)."""
    return str(tmp_path / "kill_signal.txt")


@pytest.fixture()
def audit_path(tmp_path):
    """A temporary audit log path for tests that need to inspect audit entries."""
    return str(tmp_path / "test_audit.jsonl")


@pytest.fixture()
def ks(sentinel_file):
    """A fresh KillSwitch with a tmp-dir sentinel path."""
    from bot.safety.kill_switch import KillSwitch
    return KillSwitch(sentinel_path=sentinel_file)


# ============================================================
# Sentinel file tests
# ============================================================

def test_check_file_false_before_touch(ks, sentinel_file):
    """check_file() returns False before the sentinel file is created."""
    assert not os.path.isfile(sentinel_file)
    assert ks.check_file() is False


def test_check_file_true_after_touch(ks, sentinel_file):
    """check_file() returns True after the sentinel file is created (touched)."""
    open(sentinel_file, "w").close()  # touch
    assert ks.check_file() is True


# ============================================================
# trigger() tests
# ============================================================

def test_trigger_sets_event(ks):
    """trigger() must set the shutdown threading.Event."""
    assert not ks.triggered
    ks.trigger("test_reason")
    assert ks.triggered
    assert ks.event.is_set()


def test_trigger_runs_registered_callback(ks):
    """trigger() must invoke the registered flush callback."""
    mock_cb = MagicMock()
    ks.register_flush(mock_cb)
    ks.trigger("test_flush")
    mock_cb.assert_called_once()


def test_trigger_runs_multiple_callbacks(ks):
    """trigger() must invoke all registered flush callbacks in order."""
    calls = []
    ks.register_flush(lambda: calls.append("cb1"))
    ks.register_flush(lambda: calls.append("cb2"))
    ks.trigger("multi_flush")
    assert calls == ["cb1", "cb2"]


def test_trigger_writes_audit_entry(ks, audit_path):
    """trigger() must append a JSONL audit entry with event='kill_switch'."""
    import bot.safety.audit_log as audit_mod
    original_path = audit_mod.AUDIT_LOG_PATH
    try:
        audit_mod.AUDIT_LOG_PATH = audit_path
        ks.trigger("audit_test")
        assert os.path.isfile(audit_path), "Audit file was not created"
        with open(audit_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        assert lines, "Audit file is empty"
        entry = json.loads(lines[-1])
        assert entry.get("event") == "kill_switch"
        assert "reason" in entry
        assert entry["reason"] == "audit_test"
    finally:
        audit_mod.AUDIT_LOG_PATH = original_path


def test_trigger_audit_preserves_prior_entries(ks, audit_path, tmp_path):
    """trigger() must append to existing audit content — not overwrite."""
    import bot.safety.audit_log as audit_mod
    original_path = audit_mod.AUDIT_LOG_PATH
    try:
        audit_mod.AUDIT_LOG_PATH = audit_path
        # Pre-populate with an existing entry
        prior_entry = {"event": "prior_event", "data": "keep me"}
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(prior_entry) + "\n")

        ks.trigger("append_test")

        with open(audit_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        assert len(lines) >= 2, f"Expected at least 2 entries, got {len(lines)}"
        first = json.loads(lines[0])
        assert first.get("event") == "prior_event", "Prior entry was overwritten!"
    finally:
        audit_mod.AUDIT_LOG_PATH = original_path


# ============================================================
# SIGINT handler tests
# ============================================================

def test_sigint_handler_sets_event(ks):
    """Invoking _handle_signal directly must set the shutdown Event."""
    assert not ks.triggered
    ks._handle_signal(signal.SIGINT, None)
    assert ks.triggered


def test_sigint_handler_runs_callback(ks):
    """Invoking _handle_signal directly must run the registered flush callback."""
    mock_cb = MagicMock()
    ks.register_flush(mock_cb)
    ks._handle_signal(signal.SIGINT, None)
    mock_cb.assert_called_once()


def test_sigint_handler_writes_audit_entry(ks, audit_path):
    """Invoking _handle_signal directly must write an audit entry with reason='SIGINT'."""
    import bot.safety.audit_log as audit_mod
    original_path = audit_mod.AUDIT_LOG_PATH
    try:
        audit_mod.AUDIT_LOG_PATH = audit_path
        ks._handle_signal(signal.SIGINT, None)
        with open(audit_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        assert lines
        entry = json.loads(lines[-1])
        assert entry.get("event") == "kill_switch"
        assert entry.get("reason") == "SIGINT"
    finally:
        audit_mod.AUDIT_LOG_PATH = original_path


def test_install_registers_sigint_handler(sentinel_file):
    """install() must register the instance's handler for SIGINT."""
    from bot.safety.kill_switch import KillSwitch
    ks = KillSwitch(sentinel_path=sentinel_file)
    # Store original handler to restore after test
    original = signal.getsignal(signal.SIGINT)
    try:
        ks.install()
        registered = signal.getsignal(signal.SIGINT)
        assert registered == ks._handle_signal
    finally:
        signal.signal(signal.SIGINT, original)


# ============================================================
# Idempotency tests
# ============================================================

def test_trigger_twice_calls_flush_once(ks):
    """Triggering twice must invoke the flush callback exactly once."""
    mock_cb = MagicMock()
    ks.register_flush(mock_cb)
    ks.trigger("first")
    ks.trigger("second")
    mock_cb.assert_called_once()


def test_trigger_twice_writes_one_audit_entry(ks, audit_path):
    """Triggering twice must write exactly one audit entry (not two)."""
    import bot.safety.audit_log as audit_mod
    original_path = audit_mod.AUDIT_LOG_PATH
    try:
        audit_mod.AUDIT_LOG_PATH = audit_path
        ks.trigger("first_idempotent")
        ks.trigger("second_idempotent")
        with open(audit_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        kill_switch_entries = [
            l for l in lines
            if json.loads(l).get("event") == "kill_switch"
        ]
        assert len(kill_switch_entries) == 1, (
            f"Expected 1 kill_switch audit entry, got {len(kill_switch_entries)}"
        )
    finally:
        audit_mod.AUDIT_LOG_PATH = original_path


def test_trigger_sets_event_only_once(ks):
    """Triggering twice must leave Event set (but not cause errors or double-calls)."""
    ks.trigger("once")
    ks.trigger("twice")
    assert ks.event.is_set()  # still set; no error


# ============================================================
# Import check
# ============================================================

def test_kill_switch_imports_append_audit():
    """KillSwitch must import append_audit from bot.safety.audit_log (no redefinition)."""
    import bot.safety.kill_switch as ks_mod
    # append_audit must be imported, not defined locally in the kill_switch module
    import inspect
    source = inspect.getsource(ks_mod)
    # Must contain an import of append_audit from the audit module
    assert "from bot.safety.audit_log import append_audit" in source, (
        "kill_switch.py does not import append_audit from bot.safety.audit_log"
    )
    # Must not define its own append_audit
    assert "def append_audit" not in source, (
        "kill_switch.py redefines append_audit — it must import from audit_log"
    )


def test_kill_switch_references_sigint():
    """kill_switch.py must reference signal.SIGINT."""
    import inspect
    import bot.safety.kill_switch as ks_mod
    source = inspect.getsource(ks_mod)
    assert "SIGINT" in source, "kill_switch.py does not reference signal.SIGINT"


# ============================================================
# Sentinel path from env var
# ============================================================

def test_sentinel_path_from_env_var(tmp_path, monkeypatch):
    """KillSwitch uses BOT_KILL_FILE env var as default sentinel path."""
    env_path = str(tmp_path / "env_sentinel.txt")
    monkeypatch.setenv("BOT_KILL_FILE", env_path)
    from bot.safety.kill_switch import KillSwitch
    # Force reimport to pick up env var (constructor reads it)
    ks = KillSwitch()  # no explicit sentinel_path
    assert ks.sentinel_path == env_path


def test_explicit_sentinel_path_overrides_env(tmp_path, monkeypatch):
    """Explicit sentinel_path must override BOT_KILL_FILE env var."""
    env_path = str(tmp_path / "env_sentinel.txt")
    explicit_path = str(tmp_path / "explicit_sentinel.txt")
    monkeypatch.setenv("BOT_KILL_FILE", env_path)
    from bot.safety.kill_switch import KillSwitch
    ks = KillSwitch(sentinel_path=explicit_path)
    assert ks.sentinel_path == explicit_path
