#!/usr/bin/env python3
"""
tests/options/test_config.py — Tests for bot/options/config.py + schema.py

Verifies:
  (a) the shipped defaults map onto OptionsConfig with the right types/values
  (b) fail-closed behaviour: unknown structure, missing group, bad file/JSON
  (c) the repo-root rules_options.json has not drifted from the test fixture
"""
import json
import os
from pathlib import Path

import pytest

from bot.config.loader import ConfigError
from bot.options.config import load_options_config, OptionsConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(tmp_path, rules):
    """Write a rules dict to tmp_path and return the string path."""
    path = tmp_path / "rules_options.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return str(path)


# ============================================================
# Happy path
# ============================================================

class TestLoadOptionsConfigDefaults:
    """The shipped defaults load into a fully populated OptionsConfig."""

    def test_returns_options_config(self, options_cfg):
        assert isinstance(options_cfg, OptionsConfig)

    def test_top_level_and_structure_defaults(self, options_cfg):
        assert options_cfg.strategy_name == "tasty_credit_spreads"
        assert options_cfg.structure_type == "iron_condor"
        assert options_cfg.short_delta == 0.20
        assert options_cfg.wing_width_pct_of_underlying == 1.0
        assert options_cfg.min_credit_to_width == 0.25
        assert options_cfg.min_wing_width_usd == 2.0

    def test_entry_defaults(self, options_cfg):
        assert options_cfg.ivr_min == 30
        assert options_cfg.ivp_min is None
        assert options_cfg.fear_drop_pct == 2.0
        assert options_cfg.fear_ivr_min == 20
        assert options_cfg.max_spread_pct_of_mid == 5.0
        assert options_cfg.min_open_interest == 500
        assert (options_cfg.target_dte, options_cfg.min_dte, options_cfg.max_dte) == (45, 30, 60)
        assert options_cfg.prefer_monthly is True
        assert options_cfg.entry_scan_et == "10:00"
        assert options_cfg.second_entry_scan_et == "14:30"

    def test_sizing_manage_execution_service_defaults(self, options_cfg):
        assert options_cfg.sizing_equity_usd == 100000
        assert options_cfg.max_risk_per_trade_pct == 1.0
        assert options_cfg.max_bp_usage_pct == 25
        assert options_cfg.max_concurrent_positions == 8
        assert options_cfg.max_new_positions_per_day == 2
        assert options_cfg.daily_loss_limit_pct == 2.0
        assert options_cfg.manage_interval_min == 5
        assert options_cfg.profit_target_pct_of_credit == 50
        assert options_cfg.manage_dte == 21
        assert options_cfg.stop_loss_credit_multiple is None
        assert options_cfg.assignment_guard_dte == 1
        assert options_cfg.limit_buffer_usd == 0.02
        assert options_cfg.max_retries == 3
        assert options_cfg.eod_report_et == "16:10"
        assert options_cfg.state_db == "data/options_state.db"
        assert options_cfg.kill_file == ".bot_kill_options"
        assert options_cfg.report_dir == "reports/options"

    def test_universe_is_a_tuple_of_fifteen_etfs(self, options_cfg):
        """universe must be an immutable tuple of the 15 liquid ETFs (D2)."""
        assert isinstance(options_cfg.universe, tuple)
        assert len(options_cfg.universe) == 15
        assert options_cfg.universe[0] == "US.SPY"

    def test_counts_are_ints_not_floats(self, options_cfg):
        """Count-like keys are coerced to int so range()/floor math is safe."""
        for value in (
            options_cfg.min_open_interest, options_cfg.target_dte,
            options_cfg.max_concurrent_positions, options_cfg.manage_dte,
            options_cfg.assignment_guard_dte, options_cfg.max_retries,
        ):
            assert isinstance(value, int)

    def test_optional_values_survive_when_set(self, options_rules, tmp_path):
        """ivp_min / stop_loss_credit_multiple parse as floats when not null."""
        options_rules["entry"]["ivp_min"] = 40
        options_rules["manage"]["stop_loss_credit_multiple"] = 2.0
        cfg = load_options_config(_write(tmp_path, options_rules))
        assert cfg.ivp_min == 40.0
        assert cfg.stop_loss_credit_multiple == 2.0

    def test_null_second_entry_scan_is_none(self, options_rules, tmp_path):
        options_rules["entry"]["second_entry_scan_et"] = None
        cfg = load_options_config(_write(tmp_path, options_rules))
        assert cfg.second_entry_scan_et is None


# ============================================================
# Fail-closed paths
# ============================================================

class TestLoadOptionsConfigFailsClosed:
    """Bad config must raise ConfigError, never load a half-valid strategy."""

    def test_unknown_structure_type_raises(self, options_rules, tmp_path):
        """'strangle' is undefined-risk and has no BP model — must be rejected (T-0ph-01)."""
        options_rules["structure"]["type"] = "strangle"
        with pytest.raises(ConfigError):
            load_options_config(_write(tmp_path, options_rules))

    def test_unimplemented_structure_passing_schema_still_raises(self, options_rules, tmp_path, monkeypatch):
        """Defense-in-depth: a schema-valid structure not in _IMPLEMENTED_STRUCTURES fails.

        Simulates a future enum addition landing in schema.py before the strategy
        code can build it — the loader guard must still fail closed.
        """
        import bot.options.config as config_mod
        monkeypatch.setattr(config_mod, "_IMPLEMENTED_STRUCTURES", ("put_credit_spread",))
        with pytest.raises(ConfigError, match="iron_condor"):
            load_options_config(_write(tmp_path, options_rules))

    def test_missing_sizing_block_raises(self, options_rules, tmp_path):
        del options_rules["sizing"]
        with pytest.raises(ConfigError, match="sizing"):
            load_options_config(_write(tmp_path, options_rules))

    def test_missing_nested_key_raises(self, options_rules, tmp_path):
        del options_rules["manage"]["manage_dte"]
        with pytest.raises(ConfigError, match="manage_dte"):
            load_options_config(_write(tmp_path, options_rules))

    def test_empty_universe_raises(self, options_rules, tmp_path):
        options_rules["universe"] = []
        with pytest.raises(ConfigError):
            load_options_config(_write(tmp_path, options_rules))

    def test_wrong_type_raises(self, options_rules, tmp_path):
        options_rules["entry"]["min_open_interest"] = "lots"
        with pytest.raises(ConfigError):
            load_options_config(_write(tmp_path, options_rules))

    def test_bad_time_format_raises(self, options_rules, tmp_path):
        options_rules["entry"]["entry_scan_et"] = "10am"
        with pytest.raises(ConfigError):
            load_options_config(_write(tmp_path, options_rules))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_options_config(str(tmp_path / "nope.json"))

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "rules_options.json"
        path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_options_config(str(path))


# ============================================================
# Shipped-file drift guard
# ============================================================

class TestShippedRulesOptionsJson:
    """The repo-root rules_options.json must load and match the test fixture."""

    def test_shipped_file_loads(self):
        cfg = load_options_config(str(REPO_ROOT / "rules_options.json"))
        assert cfg.strategy_name == "tasty_credit_spreads"
        assert cfg.structure_type == "iron_condor"
        assert len(cfg.universe) == 15

    def test_shipped_file_matches_fixture(self, options_rules):
        """Catches fixture drift: shipped defaults and the fixture must agree."""
        shipped = json.loads(
            (REPO_ROOT / "rules_options.json").read_text(encoding="utf-8")
        )
        assert shipped == options_rules

    def test_default_path_is_repo_root_file(self):
        """load_options_config() with no argument reads rules_options.json."""
        cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            cfg = load_options_config()
        finally:
            os.chdir(cwd)
        assert cfg.universe[0] == "US.SPY"
