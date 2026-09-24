#!/usr/bin/env python3
"""
tests/options/test_config.py — Tests for bot/options/config.py + schema.py

Verifies:
  (a) the shipped defaults map onto OptionsConfig with the right types/values
  (b) fail-closed behaviour: unknown structure, missing group, bad file/JSON
  (c) the repo-root rules_options.json has not drifted from the test fixture
"""
import copy
import dataclasses
import json
import os
from pathlib import Path

import pytest

from bot.config.loader import ConfigError
from bot.options.config import load_options_config, OptionsConfig
from bot.options.config import OptionsBook, legacy_view, load_options_book


REPO_ROOT = Path(__file__).resolve().parents[2]

# The six fields OptionsConfig gained for the multi-strategy split (D-09).
# Everything else is a pre-existing field whose value/type must be identical
# between a legacy-loaded config and a strategies-shape-loaded config (D-26).
_NEW_FIELDS = {
    "name", "universe_source", "long_delta", "max_debit_to_width",
    "profit_target_pct_of_max", "equity_state_db",
}
_PRE_EXISTING_FIELDS = [
    f.name for f in dataclasses.fields(OptionsConfig) if f.name not in _NEW_FIELDS
]


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

    def test_shipped_file_matches_fixture(self, options_book_rules):
        """D-26: the shipped file is now the strategies shape; the pre-change
        legacy content is guarded by test_shipped_legacy_view_is_the_pre_change_file."""
        shipped = json.loads(
            (REPO_ROOT / "rules_options.json").read_text(encoding="utf-8")
        )
        assert shipped == options_book_rules

    def test_default_path_is_repo_root_file(self):
        """load_options_config() with no argument reads rules_options.json."""
        cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            cfg = load_options_config()
        finally:
            os.chdir(cwd)
        assert cfg.universe[0] == "US.SPY"

    def test_shipped_tasty_view_equals_pre_change_config_field_for_field(self, options_cfg):
        """D-26: the converted shipped file's default (tasty_credit_spreads)
        view is field-for-field identical to the pre-change legacy config on
        every pre-existing OptionsConfig field (value AND type)."""
        pre = options_cfg
        post = load_options_config(str(REPO_ROOT / "rules_options.json"))
        assert len(_PRE_EXISTING_FIELDS) == 43
        for name in _PRE_EXISTING_FIELDS:
            pre_value = getattr(pre, name)
            post_value = getattr(post, name)
            assert post_value == pre_value, f"field {name!r}: {post_value!r} != {pre_value!r}"
            assert type(post_value) is type(pre_value), (
                f"field {name!r}: type {type(post_value)!r} != {type(pre_value)!r}"
            )

    def test_shipped_legacy_view_is_the_pre_change_file(self, options_rules):
        """legacy_view(shipped, "tasty_credit_spreads") == the pre-change
        legacy fixture, plus service.equity_state_db (new in the shared block)."""
        shipped = json.loads(
            (REPO_ROOT / "rules_options.json").read_text(encoding="utf-8")
        )
        projected = legacy_view(shipped, "tasty_credit_spreads")
        expected = copy.deepcopy(options_rules)
        expected["service"]["equity_state_db"] = "data/bot_state.db"
        assert projected == expected

    def test_shipped_book_has_both_strategies_in_order(self):
        book = load_options_book(str(REPO_ROOT / "rules_options.json"))
        assert [c.name for c in book.strategies] == ["tasty_credit_spreads", "super_bull_call"]

    def test_shipped_super_bull_call_values(self):
        cfg = load_options_config(str(REPO_ROOT / "rules_options.json"), strategy="super_bull_call")
        assert cfg.structure_type == "bull_call_spread"
        assert cfg.long_delta == 0.30
        assert cfg.wing_width_pct_of_underlying == 4.5
        assert cfg.min_wing_width_usd == 2.0
        assert cfg.max_debit_to_width == 0.30
        assert cfg.target_dte == 30
        assert cfg.min_dte == 21
        assert cfg.max_dte == 45
        assert cfg.prefer_monthly is True
        assert cfg.max_spread_pct_of_mid == 5.0
        assert cfg.max_spread_abs_usd == 0.05
        assert cfg.min_open_interest == 500
        assert cfg.entry_scan_et == "10:05"
        assert cfg.second_entry_scan_et is None
        assert cfg.max_risk_per_trade_pct == 1.0
        assert cfg.max_concurrent_positions == 4
        assert cfg.max_new_positions_per_day == 2
        assert cfg.profit_target_pct_of_max == 60.0
        assert cfg.manage_dte is None
        assert cfg.assignment_guard_dte == 1
        assert cfg.universe_source == "equity_watchlist"
        assert cfg.equity_state_db == "data/bot_state.db"


# ============================================================
# Multi-strategy book (D-01, D-02, D-03, D-06, D-07, D-08, D-09)
# ============================================================

class TestLoadOptionsBook:
    """load_options_book loads a strategies-shape file into flat configs."""

    def test_pre_existing_field_count_is_43(self):
        assert len(_PRE_EXISTING_FIELDS) == 43

    def test_two_strategies_in_config_order(self, options_book):
        assert [c.name for c in options_book.strategies] == [
            "tasty_credit_spreads", "super_bull_call",
        ]
        assert [c.strategy_name for c in options_book.strategies] == [
            "tasty_credit_spreads", "super_bull_call",
        ]

    def test_bull_config_fields(self, options_book):
        bull = options_book.strategies[1]
        assert bull.structure_type == "bull_call_spread"
        assert bull.long_delta == 0.30
        assert bull.max_debit_to_width == 0.30
        assert bull.profit_target_pct_of_max == 60.0
        assert bull.manage_dte is None
        assert bull.universe == ()
        assert bull.universe_source == "equity_watchlist"
        for field in (
            "ivr_min", "ivp_min", "fear_drop_pct", "fear_ivr_min", "short_delta",
            "min_credit_to_width", "profit_target_pct_of_credit",
            "stop_loss_credit_multiple",
        ):
            assert getattr(bull, field) is None, field
        assert bull.target_dte == 30
        assert bull.min_dte == 21
        assert bull.max_dte == 45
        assert bull.entry_scan_et == "10:05"
        assert bull.second_entry_scan_et is None
        assert bull.max_concurrent_positions == 4

    def test_shared_values_flattened_into_every_config(self, options_book):
        for cfg in options_book.strategies:
            assert cfg.sizing_equity_usd == 100000.0
            assert cfg.max_bp_usage_pct == 25.0
            assert cfg.daily_loss_limit_pct == 2.0
            assert cfg.manage_interval_min == 5
            assert cfg.equity_state_db == "data/bot_state.db"
            assert cfg.state_db == "data/options_state.db"
            assert cfg.kill_file == ".bot_kill_options"
            assert cfg.report_dir == "reports/options"
            assert cfg.limit_buffer_usd == 0.02

    def test_relocation_proof(self, options_book_rules, tmp_path):
        """Pitfall 2: risk.sizing_equity_usd must be read from risk, not per-strategy sizing."""
        rules = copy.deepcopy(options_book_rules)
        rules["risk"]["sizing_equity_usd"] = 50000
        book = load_options_book(_write(tmp_path, rules))
        for cfg in book.strategies:
            assert cfg.sizing_equity_usd == 50000.0

    def test_legacy_file_loads_as_one_strategy_book(self, options_rules, tmp_path):
        book = load_options_book(_write(tmp_path, options_rules))
        assert len(book.strategies) == 1
        cfg = book.strategies[0]
        assert cfg.name == cfg.strategy_name == "tasty_credit_spreads"
        assert cfg.universe_source is None
        assert cfg.long_delta is None
        assert cfg.equity_state_db == "data/bot_state.db"

    def test_legacy_and_book_default_strategy_equal_on_43_fields(
        self, options_rules, options_book_rules, tmp_path
    ):
        legacy_path = tmp_path / "legacy.json"
        legacy_path.write_text(json.dumps(options_rules), encoding="utf-8")
        book_path = tmp_path / "book.json"
        book_path.write_text(json.dumps(options_book_rules), encoding="utf-8")

        legacy_cfg = load_options_config(str(legacy_path))
        book_cfg = load_options_config(str(book_path))

        for field in _PRE_EXISTING_FIELDS:
            a, b = getattr(legacy_cfg, field), getattr(book_cfg, field)
            assert a == b, field
            assert type(a) is type(b), field

    def test_strategy_selection_by_name(self, options_book_rules, tmp_path):
        path = _write(tmp_path, options_book_rules)
        cfg = load_options_config(path, strategy="super_bull_call")
        assert cfg.structure_type == "bull_call_spread"

    def test_unknown_strategy_name_raises(self, options_book_rules, tmp_path):
        path = _write(tmp_path, options_book_rules)
        with pytest.raises(ConfigError, match="nope"):
            load_options_config(path, strategy="nope")

    def test_strategy_kwarg_on_legacy_file_raises(self, options_rules, tmp_path):
        path = _write(tmp_path, options_rules)
        with pytest.raises(ConfigError):
            load_options_config(path, strategy="super_bull_call")

    def test_load_options_config_signature_unchanged_shape(self):
        """MSO-02: default() still returns the flat OptionsConfig type."""
        cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            cfg = load_options_config()
        finally:
            os.chdir(cwd)
        assert isinstance(cfg, OptionsConfig)

    def test_load_options_book_returns_options_book(self, options_book):
        assert isinstance(options_book, OptionsBook)
        assert isinstance(options_book.strategies, tuple)


# ============================================================
# legacy_view (D-10)
# ============================================================

class TestLegacyView:
    """legacy_view projects one strategy back to the legacy flat shape."""

    def test_legacy_input_is_identity(self, options_rules):
        result = legacy_view(options_rules)
        assert result == options_rules
        assert result is not options_rules

    def test_legacy_input_with_matching_name(self, options_rules):
        assert legacy_view(options_rules, "tasty_credit_spreads") == options_rules

    def test_legacy_input_with_wrong_name_raises(self, options_rules):
        with pytest.raises(ConfigError):
            legacy_view(options_rules, "other")

    def test_book_default_strategy_matches_legacy_plus_equity_state_db(
        self, options_book_rules, options_rules
    ):
        result = legacy_view(options_book_rules)
        expected = copy.deepcopy(options_rules)
        expected["service"]["equity_state_db"] = "data/bot_state.db"
        assert result == expected

    def test_book_named_strategy_same_as_default(self, options_book_rules):
        assert legacy_view(options_book_rules, "tasty_credit_spreads") == legacy_view(
            options_book_rules
        )

    def test_unknown_strategy_raises(self, options_book_rules):
        with pytest.raises(ConfigError, match="nope"):
            legacy_view(options_book_rules, "nope")

    def test_inputs_not_mutated(self, options_book_rules, options_rules):
        before_book = copy.deepcopy(options_book_rules)
        before_legacy = copy.deepcopy(options_rules)
        legacy_view(options_book_rules, "tasty_credit_spreads")
        legacy_view(options_rules)
        assert options_book_rules == before_book
        assert options_rules == before_legacy

    def test_round_trip_through_load_options_config(self, options_book_rules, options_book, tmp_path):
        raw = legacy_view(options_book_rules)
        path = tmp_path / "roundtrip.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        roundtrip_cfg = load_options_config(str(path))
        book_cfg = options_book.strategies[0]
        for field in _PRE_EXISTING_FIELDS:
            assert getattr(roundtrip_cfg, field) == getattr(book_cfg, field), field


# ============================================================
# Fail-closed rules for the strategies shape (D-04, D-05, D-11)
# ============================================================

class TestStrategiesShapeFailsClosed:
    """Every D-11 misconfiguration raises ConfigError with the offending key named."""

    def test_duplicate_names_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][1]["name"] = "tasty_credit_spreads"
        with pytest.raises(ConfigError, match="duplicate"):
            load_options_book(_write(tmp_path, rules))

    def test_both_universe_and_universe_source_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["universe_source"] = "equity_watchlist"
        with pytest.raises(ConfigError, match="exactly one"):
            load_options_book(_write(tmp_path, rules))

    def test_neither_universe_nor_universe_source_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][0]["universe"]
        with pytest.raises(ConfigError, match="exactly one"):
            load_options_book(_write(tmp_path, rules))

    def test_unknown_universe_source_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][1]["universe_source"] = "sp500"
        with pytest.raises(ConfigError, match="sp500"):
            load_options_book(_write(tmp_path, rules))

    def test_unimplemented_structure_monkeypatched_raises(self, options_book_rules, tmp_path, monkeypatch):
        """Defense-in-depth: a schema-valid bull_call_spread not yet implemented still fails."""
        import bot.options.config as config_mod
        monkeypatch.setattr(config_mod, "_IMPLEMENTED_STRUCTURES", ("iron_condor", "put_credit_spread"))
        with pytest.raises(ConfigError, match="bull_call_spread"):
            load_options_book(_write(tmp_path, options_book_rules))

    def test_unknown_structure_type_in_strategies_shape_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["structure"]["type"] = "strangle"
        with pytest.raises(ConfigError):
            load_options_book(_write(tmp_path, rules))

    @pytest.mark.parametrize("key", ["ivr_min", "ivp_min", "fear_drop_pct", "fear_ivr_min"])
    def test_credit_missing_entry_key_raises(self, options_book_rules, tmp_path, key):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][0]["entry"][key]
        with pytest.raises(ConfigError, match=key):
            load_options_book(_write(tmp_path, rules))

    @pytest.mark.parametrize("key", ["short_delta", "min_credit_to_width"])
    def test_credit_missing_structure_key_raises(self, options_book_rules, tmp_path, key):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][0]["structure"][key]
        with pytest.raises(ConfigError, match=key):
            load_options_book(_write(tmp_path, rules))

    @pytest.mark.parametrize("key", ["profit_target_pct_of_credit", "stop_loss_credit_multiple"])
    def test_credit_missing_manage_key_raises(self, options_book_rules, tmp_path, key):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][0]["manage"][key]
        with pytest.raises(ConfigError, match=key):
            load_options_book(_write(tmp_path, rules))

    def test_credit_manage_dte_null_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["manage"]["manage_dte"] = None
        with pytest.raises(ConfigError, match="manage_dte"):
            load_options_book(_write(tmp_path, rules))

    def test_credit_carrying_long_delta_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["structure"]["long_delta"] = 0.30
        with pytest.raises(ConfigError, match="long_delta"):
            load_options_book(_write(tmp_path, rules))

    @pytest.mark.parametrize("key", ["long_delta", "max_debit_to_width"])
    def test_bull_missing_structure_key_raises(self, options_book_rules, tmp_path, key):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][1]["structure"][key]
        with pytest.raises(ConfigError, match=key):
            load_options_book(_write(tmp_path, rules))

    def test_bull_missing_profit_target_pct_of_max_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        del rules["strategies"][1]["manage"]["profit_target_pct_of_max"]
        with pytest.raises(ConfigError, match="profit_target_pct_of_max"):
            load_options_book(_write(tmp_path, rules))

    @pytest.mark.parametrize("block,key,value", [
        ("entry", "ivr_min", 30),
        ("structure", "short_delta", 0.20),
        ("manage", "stop_loss_credit_multiple", None),
    ])
    def test_bull_carrying_credit_key_raises(self, options_book_rules, tmp_path, block, key, value):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][1][block][key] = value
        with pytest.raises(ConfigError, match=key):
            load_options_book(_write(tmp_path, rules))

    def test_sizing_equity_usd_inside_strategy_sizing_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["sizing"]["sizing_equity_usd"] = 50000
        with pytest.raises(ConfigError, match="risk"):
            load_options_book(_write(tmp_path, rules))

    def test_manage_interval_min_inside_strategy_manage_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["manage"]["manage_interval_min"] = 5
        with pytest.raises(ConfigError, match="service"):
            load_options_book(_write(tmp_path, rules))

    def test_missing_top_level_risk_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        del rules["risk"]
        with pytest.raises(ConfigError, match="risk"):
            load_options_book(_write(tmp_path, rules))

    def test_empty_strategies_list_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"] = []
        with pytest.raises(ConfigError):
            load_options_book(_write(tmp_path, rules))

    def test_empty_name_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["strategies"][0]["name"] = ""
        with pytest.raises(ConfigError):
            load_options_book(_write(tmp_path, rules))

    def test_equity_state_db_equal_to_state_db_raises(self, options_book_rules, tmp_path):
        rules = copy.deepcopy(options_book_rules)
        rules["service"]["equity_state_db"] = rules["service"]["state_db"]
        with pytest.raises(ConfigError, match="equity_state_db"):
            load_options_book(_write(tmp_path, rules))

    def test_valid_book_fixture_still_loads(self, options_book_rules, tmp_path):
        book = load_options_book(_write(tmp_path, options_book_rules))
        assert len(book.strategies) == 2

    def test_valid_legacy_fixture_still_loads(self, options_rules, tmp_path):
        book = load_options_book(_write(tmp_path, options_rules))
        assert len(book.strategies) == 1
