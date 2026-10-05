#!/usr/bin/env python3
"""
tests/ibs/conftest.py — Shared fixtures for the IBS bot tests.

Provides:
  ibs_rules — the canonical rules_ibs.json content as a plain dict (a LITERAL,
              never derived from loader code, so the shipped-file drift guard
              in test_schema_config.py is not circular)
  ibs_cfg   — a loaded IbsConfig built from ibs_rules in tmp_path
"""
import json

import pytest

from bot.ibs.config import load_ibs_config

UNIVERSE = [
    "US.SPY", "US.QQQ", "US.IWM", "US.DIA", "US.XLK", "US.XLF",
    "US.XLE", "US.XLV", "US.XLI", "US.XLY", "US.XLP", "US.XLU",
    "US.XLB", "US.TLT", "US.GLD", "US.EFA", "US.EEM",
]


@pytest.fixture
def ibs_rules():
    """Return the canonical rules_ibs.json content as a dict (mutate a deepcopy)."""
    return {
        "strategy_name": "ibs_etf_mean_reversion",
        "universe": list(UNIVERSE),
        "signal": {
            "ibs_entry_max": 0.20,
            "ibs_exit_min": 0.80,
            "max_hold_trading_days": 10,
            "max_snapshot_age_s": 900,
        },
        "risk": {
            "sizing_equity_usd": 100000,
            "position_pct_of_equity": 10,
            "max_concurrent_positions": 10,
        },
        "execution": {
            "entry_limit_buffer_usd": 0.05,
            "exit_limit_buffer_usd": 0.05,
            "order_ttl_seconds": 20,
            "poll_interval_seconds": 5,
            "escalation_step_usd": 0.05,
            "max_reprices": 3,
        },
        "service": {
            "arm_time_et": "09:00",
            "decision_before_close_min": 10,
            "hard_cancel_before_close_min": 1,
            "eod_report_after_close_min": 5,
            "misfire_grace_s": 120,
            "watchdog_poll_interval_s": 60,
            "watchdog_reconnect_initial_s": 5,
            "watchdog_reconnect_cap_s": 300,
            "state_db": "data/ibs_state.db",
            "kill_file": ".bot_kill_ibs",
            "report_dir": "reports/ibs",
            "log_file": "ibs.log",
        },
    }


@pytest.fixture
def ibs_cfg(ibs_rules, tmp_path):
    """Return a loaded IbsConfig built from ibs_rules."""
    p = tmp_path / "rules_ibs.json"
    p.write_text(json.dumps(ibs_rules), encoding="utf-8")
    return load_ibs_config(str(p))
