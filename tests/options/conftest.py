#!/usr/bin/env python3
"""
tests/options/conftest.py — Shared fixtures for the options strategy tests.

Provides:
  options_rules — the canonical rules_options.json content as a plain dict
  options_cfg   — a loaded OptionsConfig built from options_rules in tmp_path
"""
import json

import pytest

from bot.options.config import load_options_config


@pytest.fixture
def options_rules():
    """Return the canonical rules_options.json content (CFG-01) as a dict.

    A literal, not a file read (mirrors `minimal_rules` in tests/conftest.py):
    tests stay hermetic and can mutate a copy freely. test_config.py asserts
    this literal has not drifted from the shipped repo-root file.
    """
    return {
        "strategy_name": "tasty_credit_spreads",
        "universe": [
            "US.SPY", "US.QQQ", "US.IWM", "US.DIA", "US.GLD",
            "US.SLV", "US.TLT", "US.XLE", "US.XLF", "US.XLK",
            "US.EEM", "US.EFA", "US.FXI", "US.GDX", "US.USO",
        ],
        "entry": {
            "ivr_min": 30,
            "ivp_min": None,
            "fear_drop_pct": 2.0,
            "fear_ivr_min": 20,
            "max_spread_pct_of_mid": 5.0,
            "max_spread_abs_usd": 0.05,
            "min_open_interest": 500,
            "target_dte": 45,
            "min_dte": 30,
            "max_dte": 60,
            "prefer_monthly": True,
            "entry_scan_et": "10:00",
            "second_entry_scan_et": "14:30",
        },
        "structure": {
            "type": "iron_condor",
            "short_delta": 0.16,
            "wing_width_pct_of_underlying": 1.0,
            "min_credit_to_width": 0.33,
        },
        "sizing": {
            "sizing_equity_usd": 100000,
            "max_risk_per_trade_pct": 1.0,
            "max_bp_usage_pct": 25,
            "max_concurrent_positions": 8,
            "max_new_positions_per_day": 2,
            "daily_loss_limit_pct": 2.0,
        },
        "manage": {
            "manage_interval_min": 5,
            "profit_target_pct_of_credit": 50,
            "manage_dte": 21,
            "stop_loss_credit_multiple": None,
            "assignment_guard_dte": 1,
        },
        "execution": {
            "limit_buffer_usd": 0.02,
            "poll_interval_s": 4.0,
            "ttl_s": 45,
            "escalation_step_usd": 0.03,
            "max_retries": 3,
        },
        "service": {
            "eod_report_et": "16:10",
            "watchdog_poll_interval_s": 60,
            "watchdog_reconnect_initial_s": 5,
            "watchdog_reconnect_cap_s": 300,
            "state_db": "data/options_state.db",
            "kill_file": ".bot_kill_options",
            "report_dir": "reports/options",
        },
    }


@pytest.fixture
def options_cfg(options_rules, tmp_path):
    """Return an OptionsConfig loaded from the canonical fixture dict."""
    path = tmp_path / "rules_options.json"
    path.write_text(json.dumps(options_rules), encoding="utf-8")
    return load_options_config(str(path))
