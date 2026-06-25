#!/usr/bin/env python3
"""
tests/service/conftest.py — Shared fixtures for bot.service unit tests.

Provides:
  telegram_env  — monkeypatches TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to
                  empty strings so TelegramAlerter is disabled by default in
                  all unit tests (D-12/D-13: bot no-ops if tokens are unset).

All fixtures from tests/conftest.py (tmp_state_db, minimal_rules, etc.)
are automatically available to tests in this directory via pytest's conftest
inheritance.
"""
import pytest


@pytest.fixture(autouse=True)
def telegram_env(monkeypatch):
    """Disable TelegramAlerter in unit tests by setting tokens to empty strings.

    Ensures no real Telegram POST is ever attempted during the test suite.
    Applied automatically to all tests in tests/service/ via autouse=True.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
