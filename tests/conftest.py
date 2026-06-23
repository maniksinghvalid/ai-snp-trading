#!/usr/bin/env python3
"""
tests/conftest.py — Shared pytest fixtures for the AI S&P Trading Bot test suite.

Provides:
  tmp_state_db    — monkeypatches BOT_STATE_DB to a tmp_path-based SQLite path (D-09)
  minimal_rules   — canonical strategy dict from PROJECT.md §"The Strategy"
  mock_trade_ctx  — mock object whose get_acc_list() returns a pandas DataFrame
"""
import os
import pandas as pd
import pytest
from unittest.mock import MagicMock


# ============================================================
# Database Fixtures
# ============================================================

@pytest.fixture
def tmp_state_db(tmp_path, monkeypatch):
    """Monkeypatch BOT_STATE_DB to a tmp_path-based SQLite path (D-09).

    Ensures tests never touch the real data/bot_state.db and each test
    gets a clean, isolated database.
    """
    db_path = str(tmp_path / "bot_state_test.db")
    monkeypatch.setenv("BOT_STATE_DB", db_path)
    return db_path


# ============================================================
# Strategy Config Fixtures
# ============================================================

@pytest.fixture
def minimal_rules():
    """Return the canonical strategy dict from PROJECT.md §"The Strategy".

    This is the authoritative rules.json content (CFG-01). Tests that need
    a valid strategy config should use this fixture rather than hardcoding
    strategy parameters.
    """
    return {
        "strategy_name": "Trend Join Long",
        "direction": "long_only",
        "trade_timeframe": "5m",
        "universe_filters": {
            "index": "S&P 500",
            "min_price_usd": 3.0,
        },
        "daily_filters": {
            "D1_above_prior_day_high": True,
            "D2_prior_close_above_sma200": True,
            "D3_min_gap_pct_from_prior_close": 3.0,
        },
        "intraday_filters": {
            "I1_above_premarket_high": True,
            "I2_above_today_hod": True,
            "I3_rvol_min": 2.0,
            "I3_rvol_lookback_days": 14,
        },
        "time_filter": {
            "earliest_entry_et": "10:05",
            "latest_entry_et": "15:30",
            "force_close_et": "15:51",
        },
        "exit": {
            "initial_stop_rule": "lod_minus_1pct",
            "partial_profit_trigger_R": 0.75,
            "partial_profit_fraction": 0.3333,
            "breakeven_trigger_R": 1.0,
            "post_breakeven_trail": "swing_low_5m_2_2",
        },
        "risk": {
            "max_risk_per_trade_pct": 1.0,
            "max_position_size_pct_of_portfolio": 10,
            "max_concurrent_positions": 5,
            "max_trades_per_day": 5,
        },
    }


# ============================================================
# Mock Broker Fixtures
# ============================================================

@pytest.fixture
def mock_trade_ctx():
    """Return a mock OpenSecTradeContext whose get_acc_list() returns a DataFrame.

    The DataFrame has columns acc_id and trd_env, matching the structure
    returned by the real get_acc_list() SDK call. Used by paper-guard tests
    to simulate different account configurations without a live OpenD connection.
    """
    mock = MagicMock()
    # Default: a single SIMULATE paper account with a known acc_id
    df = pd.DataFrame([
        {"acc_id": 123456789, "trd_env": "SIMULATE"},
    ])
    mock.get_acc_list.return_value = (0, df)  # RET_OK = 0
    return mock
