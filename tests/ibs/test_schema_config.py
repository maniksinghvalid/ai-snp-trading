#!/usr/bin/env python3
"""tests/ibs/test_schema_config.py — IBS-01 config loader: fail-closed behaviour."""
import copy
import dataclasses
import json
from pathlib import Path

import pytest

from bot.config.loader import ConfigError
from bot.ibs.config import load_ibs_config

REPO_ROOT = Path(__file__).resolve().parents[2]

_LEAVES = {
    "signal": ["ibs_entry_max", "ibs_exit_min", "max_hold_trading_days", "max_snapshot_age_s"],
    "risk": ["sizing_equity_usd", "position_pct_of_equity", "max_concurrent_positions"],
    "execution": ["entry_limit_buffer_usd", "exit_limit_buffer_usd", "order_ttl_seconds",
                  "poll_interval_seconds", "escalation_step_usd", "max_reprices"],
    "service": ["arm_time_et", "decision_before_close_min", "hard_cancel_before_close_min",
                "eod_report_after_close_min", "misfire_grace_s", "watchdog_poll_interval_s",
                "watchdog_reconnect_initial_s", "watchdog_reconnect_cap_s", "state_db",
                "kill_file", "report_dir", "log_file"],
}


def _load(rules, tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    return load_ibs_config(str(p))


def _mut(ibs_rules, block, key, value):
    r = copy.deepcopy(ibs_rules)
    if block is None:
        r[key] = value
    else:
        r[block][key] = value
    return r


def test_loads_expected_values(ibs_cfg, ibs_rules):
    c = ibs_cfg
    assert c.strategy_name == "ibs_etf_mean_reversion"
    assert c.universe == tuple(ibs_rules["universe"]) and len(c.universe) == 17
    assert (c.ibs_entry_max, c.ibs_exit_min) == (0.2, 0.8)
    assert c.max_hold_trading_days == 10 and isinstance(c.max_hold_trading_days, int)
    assert c.max_snapshot_age_s == 900.0
    assert c.sizing_equity_usd == 100000.0 and c.position_pct_of_equity == 10.0
    assert c.max_concurrent_positions == 10 and isinstance(c.max_concurrent_positions, int)
    assert c.entry_limit_buffer_usd == c.exit_limit_buffer_usd == 0.05
    assert c.order_ttl_seconds == 20.0 and c.poll_interval_seconds == 5.0
    assert c.escalation_step_usd == 0.05 and c.max_reprices == 3
    assert c.arm_time_et == "09:00"
    assert (c.decision_before_close_min, c.hard_cancel_before_close_min,
            c.eod_report_after_close_min, c.misfire_grace_s) == (10, 1, 5, 120)
    assert (c.state_db, c.kill_file, c.report_dir, c.log_file) == (
        "data/ibs_state.db", ".bot_kill_ibs", "reports/ibs", "ibs.log")
    assert c.worst_case_order_s == 100.0


def test_frozen(ibs_cfg):
    with pytest.raises(dataclasses.FrozenInstanceError):
        ibs_cfg.ibs_entry_max = 0.3


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_ibs_config(str(tmp_path / "nope.json"))


def test_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_ibs_config(str(p))


@pytest.mark.parametrize("key", ["strategy_name", "universe", "signal", "risk", "execution", "service"])
def test_missing_top_level_key(ibs_rules, tmp_path, key):
    r = copy.deepcopy(ibs_rules)
    del r[key]
    with pytest.raises(ConfigError):
        _load(r, tmp_path)


@pytest.mark.parametrize("block,key", [(b, k) for b, ks in _LEAVES.items() for k in ks])
def test_missing_leaf_key(ibs_rules, tmp_path, block, key):
    r = copy.deepcopy(ibs_rules)
    del r[block][key]
    with pytest.raises(ConfigError):
        _load(r, tmp_path)


@pytest.mark.parametrize("block,key,value", [
    (None, "strategy_name", "other"),
    (None, "universe", []),
    (None, "universe", ["US.SPY", "US.SPY"]),
    (None, "universe", ["SPY"]),
    ("signal", "ibs_entry_max", "0.2"),
    ("signal", "ibs_entry_max", True),
    ("risk", "max_concurrent_positions", 2.5),
    ("signal", "ibs_entry_max", 0.85),   # >= ibs_exit_min
    ("signal", "ibs_entry_max", 0),
    ("signal", "ibs_exit_min", 1),
    ("service", "hard_cancel_before_close_min", 10),
    ("service", "hard_cancel_before_close_min", 0),
    ("execution", "poll_interval_seconds", 3),
    ("execution", "poll_interval_seconds", 20),
    ("execution", "order_ttl_seconds", 200),
    ("service", "arm_time_et", "25:00"),
    ("service", "arm_time_et", "9:00"),
    ("service", "arm_time_et", "12:55"),
    ("service", "watchdog_reconnect_initial_s", 400),
    ("service", "state_db", "data/bot_state.db"),
    ("service", "state_db", "./data/options_state.db"),
    ("service", "kill_file", ".bot_kill"),
    ("service", "kill_file", ".bot_kill_options"),
    ("service", "report_dir", "reports"),
    ("service", "report_dir", "reports/options"),
    ("service", "log_file", "bot.log"),
    ("service", "log_file", "logs/ibs.log"),
    ("service", "log_file", "ibs.txt"),
])
def test_invalid_values_rejected(ibs_rules, tmp_path, block, key, value):
    with pytest.raises(ConfigError):
        _load(_mut(ibs_rules, block, key, value), tmp_path)


def test_leverage_rejected(ibs_rules, tmp_path):
    r = _mut(ibs_rules, "risk", "position_pct_of_equity", 20)
    with pytest.raises(ConfigError, match="unlevered"):
        _load(r, tmp_path)


def test_window_rejected(ibs_rules, tmp_path):
    r = _mut(ibs_rules, "execution", "order_ttl_seconds", 200)
    with pytest.raises(ConfigError, match="window"):
        _load(r, tmp_path)


def test_shipped_rules_ibs_matches_fixture(ibs_rules):
    shipped = json.loads((REPO_ROOT / "rules_ibs.json").read_text(encoding="utf-8"))
    assert shipped == ibs_rules


def test_shipped_rules_ibs_loads(ibs_rules):
    c = load_ibs_config(str(REPO_ROOT / "rules_ibs.json"))
    assert c.universe == tuple(ibs_rules["universe"])


def test_default_path_is_rules_ibs_json(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    assert load_ibs_config().strategy_name == "ibs_etf_mean_reversion"
