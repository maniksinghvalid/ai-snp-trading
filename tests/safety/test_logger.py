#!/usr/bin/env python3
"""
tests.safety.test_logger — Unit tests for bot.safety.logger.

Verifies:
- configure_logging() creates log file under log_dir
- Emitting info/warning events produces JSON entries in the log file
- Log entries contain correct 'level' fields
- configure_logging() is idempotent (safe to call twice)
- get_logger() returns a usable structlog logger
"""
import json
import os

import pytest
import structlog

# Reset structlog configuration state before each test module import
# to avoid conflicts between test runs that call configure_logging.


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_logger_state():
    """Reset the module-level _configured flag before each test."""
    import bot.safety.logger as logger_mod
    original = logger_mod._configured
    logger_mod._configured = False
    # Also clear structlog's internal cache
    structlog.reset_defaults()
    yield
    # Restore after test
    logger_mod._configured = original
    structlog.reset_defaults()


# ============================================================
# configure_logging() tests
# ============================================================

def test_configure_logging_creates_log_file(tmp_path):
    """configure_logging() must create the log file under log_dir."""
    from bot.safety.logger import configure_logging
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    expected_log = os.path.join(log_dir, "bot.log")
    assert os.path.isfile(expected_log), f"Log file not found: {expected_log}"


def test_configure_logging_idempotent(tmp_path):
    """Calling configure_logging() twice must not raise and must not duplicate handlers."""
    from bot.safety.logger import configure_logging
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir)
    # Second call should be a no-op (guarded by _configured flag)
    configure_logging(log_dir=log_dir)  # must not raise


def test_configure_logging_creates_directory(tmp_path):
    """configure_logging() must create log_dir if it does not exist."""
    from bot.safety.logger import configure_logging
    log_dir = str(tmp_path / "nested" / "logs")
    assert not os.path.exists(log_dir)
    configure_logging(log_dir=log_dir)
    assert os.path.isdir(log_dir)


# ============================================================
# Log output tests
# ============================================================

def test_info_event_written_to_log_file(tmp_path):
    """Emitting an info event must produce an entry in the rotating log file."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    logger = get_logger("test")
    logger.info("test_info_event", value=42)

    # Flush handlers
    import logging
    logging.getLogger().handlers[0].flush()

    log_file = os.path.join(log_dir, "bot.log")
    with open(log_file, encoding="utf-8") as f:
        content = f.read()
    assert content.strip(), "Log file is empty after emitting an info event"


def test_info_event_has_correct_level(tmp_path):
    """The JSON log entry for an info event must contain level='info'."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    logger = get_logger("test_level")
    logger.info("level_check_info")

    import logging
    logging.getLogger().handlers[0].flush()

    log_file = os.path.join(log_dir, "bot.log")
    with open(log_file, encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    assert lines, "No log lines found"
    # Find an info entry
    info_lines = []
    for line in lines:
        try:
            entry = json.loads(line)
            if entry.get("level", "").lower() == "info":
                info_lines.append(entry)
        except json.JSONDecodeError:
            pass  # skip non-JSON lines (e.g. console renderer output)

    assert info_lines, f"No JSON info entries found in log. Lines: {lines}"


def test_warning_event_has_correct_level(tmp_path):
    """The JSON log entry for a warning event must contain level='warning'."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    logger = get_logger("test_warn")
    logger.warning("level_check_warning")

    import logging
    logging.getLogger().handlers[0].flush()

    log_file = os.path.join(log_dir, "bot.log")
    with open(log_file, encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    warning_lines = []
    for line in lines:
        try:
            entry = json.loads(line)
            if entry.get("level", "").lower() in ("warning", "warn"):
                warning_lines.append(entry)
        except json.JSONDecodeError:
            pass

    assert warning_lines, f"No JSON warning entries found in log. Lines: {lines}"


def test_log_entries_contain_timestamp(tmp_path):
    """JSON log entries must contain a timestamp field."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    logger = get_logger("test_ts")
    logger.info("timestamp_check")

    import logging
    logging.getLogger().handlers[0].flush()

    log_file = os.path.join(log_dir, "bot.log")
    with open(log_file, encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    json_entries = []
    for line in lines:
        try:
            entry = json.loads(line)
            json_entries.append(entry)
        except json.JSONDecodeError:
            pass

    assert json_entries, "No JSON entries found in log"
    for entry in json_entries:
        assert "timestamp" in entry, f"Entry missing 'timestamp': {entry}"


def test_log_entries_contain_event_field(tmp_path):
    """JSON log entries must include the 'event' key with the emitted message."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir, level="DEBUG")
    logger = get_logger("test_event_field")
    logger.info("my_unique_event_marker")

    import logging
    logging.getLogger().handlers[0].flush()

    log_file = os.path.join(log_dir, "bot.log")
    with open(log_file, encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    found = False
    for line in lines:
        try:
            entry = json.loads(line)
            if "my_unique_event_marker" in str(entry.get("event", "")):
                found = True
                break
        except json.JSONDecodeError:
            pass

    assert found, "Event marker not found in JSON log entries"


# ============================================================
# get_logger() tests
# ============================================================

def test_get_logger_returns_logger(tmp_path):
    """get_logger() must return a usable structlog logger object."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir)
    logger = get_logger("mymodule")
    assert logger is not None
    # Must be callable with standard log methods
    assert callable(getattr(logger, "info", None))
    assert callable(getattr(logger, "warning", None))
    assert callable(getattr(logger, "error", None))


def test_get_logger_no_name(tmp_path):
    """get_logger() with no name must not raise."""
    from bot.safety.logger import configure_logging, get_logger
    log_dir = str(tmp_path / "logs")
    configure_logging(log_dir=log_dir)
    logger = get_logger()  # no name
    assert logger is not None
