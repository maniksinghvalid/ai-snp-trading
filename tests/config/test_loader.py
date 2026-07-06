#!/usr/bin/env python3
"""
tests.config.test_loader — Tests for load_strategy_config, StrategyConfig, ConfigError.

Covers:
- Successful load returns StrategyConfig with canonical PROJECT.md values
- Missing file raises ConfigError naming the path
- Malformed JSON raises ConfigError
- Schema-invalid JSON raises ConfigError naming the failing field
"""
import json
import os
import pytest

from bot.config.loader import load_strategy_config, StrategyConfig, ConfigError


# ============================================================
# Fixtures
# ============================================================

CANONICAL_RULES = {
    "strategy_name": "Trend Join Long",
    "direction": "long_only",
    "trade_timeframe": "5m",
    "universe_filters": {
        "index": "S&P 500",
        "min_price_usd": 3.0
    },
    "daily_filters": {
        "D1_above_prior_day_high": True,
        "D2_prior_close_above_sma200": True,
        "D3_min_gap_pct_from_prior_close": 3.0
    },
    "intraday_filters": {
        "I1_above_premarket_high": True,
        "I2_above_today_hod": True,
        "I3_rvol_min": 2.0,
        "I3_rvol_lookback_days": 14
    },
    "time_filter": {
        "earliest_entry_et": "10:05",
        "latest_entry_et": "15:30",
        "force_close_et": "15:51"
    },
    "exit": {
        "initial_stop_rule": "lod_minus_1pct",
        "partial_profit_trigger_R": 0.75,
        "partial_profit_fraction": 0.3333,
        "breakeven_trigger_R": 1.0,
        "post_breakeven_trail": "swing_low_5m_2_2"
    },
    "risk": {
        "max_risk_per_trade_pct": 1.0,
        "max_position_size_pct_of_portfolio": 10,
        "max_concurrent_positions": 5,
        "max_trades_per_day": 5
    },
    "execution": {
        "entry_limit_buffer_usd": 0.05,
        "entry_ttl_seconds": 20,
        "entry_max_retries": 2,
        "entry_poll_interval_seconds": 5,
        "exit_limit_buffer_usd": 0.05,
        "exit_ttl_seconds": 15,
        "exit_escalation_step_usd": 0.10,
        "exit_escalation_cadence_seconds": 10,
        "force_close_escalation_step_usd": 0.20,
        "force_close_escalation_cadence_seconds": 15
    },
    "service": {
        "premarket_scan_et": "08:30",
        "market_open_et": "09:30",
        "intraday_rescan_interval_min": 30,
        "intraday_rescan_start_et": "09:55",
        "intraday_rescan_end_et": "12:55",
        "eod_report_et": "15:55",
        "watchdog_poll_interval_s": 60,
        "watchdog_reconnect_initial_s": 5,
        "watchdog_reconnect_cap_s": 60,
        "alerts_enabled": True,
        "misfire_grace_scan_s": 3600,
        "misfire_grace_rescan_s": 600,
        "force_close_misfire_grace_s": 300,
        "launchd_throttle_interval_s": 30,
        "crash_loop_alert_threshold": 5
    }
}


@pytest.fixture
def rules_file(tmp_path):
    """Write canonical rules.json to a tmp file and return its path."""
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(CANONICAL_RULES), encoding="utf-8")
    return str(path)


# ============================================================
# Happy-path tests
# ============================================================

class TestLoadStrategyConfigSuccess:
    def test_returns_strategy_config_instance(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert isinstance(cfg, StrategyConfig)

    def test_min_price_usd(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.min_price_usd == 3.0

    def test_d3_min_gap_pct(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.d3_min_gap_pct == 3.0

    def test_rvol_min(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.rvol_min == 2.0

    def test_rvol_lookback_days(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.rvol_lookback_days == 14

    def test_earliest_entry_et(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.earliest_entry_et == "10:05"

    def test_latest_entry_et(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.latest_entry_et == "15:30"

    def test_force_close_et(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.force_close_et == "15:51"

    def test_partial_profit_trigger_r(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.partial_profit_trigger_r == 0.75

    def test_partial_profit_fraction(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert abs(cfg.partial_profit_fraction - 0.3333) < 1e-9

    def test_breakeven_trigger_r(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.breakeven_trigger_r == 1.0

    def test_initial_stop_pct_parsed_from_rule(self, rules_file):
        """exit.initial_stop_rule='lod_minus_1pct' must parse to initial_stop_pct=1.0 (CR-01)."""
        cfg = load_strategy_config(rules_file)
        assert cfg.initial_stop_pct == 1.0

    def test_max_risk_per_trade_pct(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.max_risk_per_trade_pct == 1.0

    def test_max_position_size_pct(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.max_position_size_pct == 10

    def test_max_concurrent_positions(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.max_concurrent_positions == 5

    def test_max_trades_per_day(self, rules_file):
        cfg = load_strategy_config(rules_file)
        assert cfg.max_trades_per_day == 5


# ============================================================
# Failure-path tests
# ============================================================

class TestLoadStrategyConfigFailure:
    def test_missing_file_raises_config_error(self, tmp_path):
        missing = str(tmp_path / "nonexistent.json")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(missing)
        assert missing in str(exc_info.value)

    def test_malformed_json_raises_config_error(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json >>>", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_strategy_config(str(path))

    def test_schema_invalid_wrong_type_raises_config_error(self, tmp_path):
        """rvol_min as string should fail jsonschema validation."""
        bad_data = dict(CANONICAL_RULES)
        bad_data = json.loads(json.dumps(CANONICAL_RULES))  # deep copy
        bad_data["intraday_filters"]["I3_rvol_min"] = "not_a_number"
        path = tmp_path / "bad_schema.json"
        path.write_text(json.dumps(bad_data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        # ConfigError should mention the failing field or validation context
        assert "rvol" in str(exc_info.value).lower() or "I3_rvol_min" in str(exc_info.value)

    def test_schema_invalid_missing_required_key_raises_config_error(self, tmp_path):
        """Missing required key should fail jsonschema validation."""
        bad_data = json.loads(json.dumps(CANONICAL_RULES))
        del bad_data["risk"]
        path = tmp_path / "missing_key.json"
        path.write_text(json.dumps(bad_data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        assert "risk" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    def test_unrecognized_initial_stop_rule_raises_config_error(self, tmp_path):
        """An unknown exit.initial_stop_rule must raise ConfigError — never silently
        fall back to the risk budget (CR-01)."""
        bad_data = json.loads(json.dumps(CANONICAL_RULES))
        bad_data["exit"]["initial_stop_rule"] = "lod_minus_5pct"  # unrecognized
        path = tmp_path / "bad_stop_rule.json"
        path.write_text(json.dumps(bad_data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        assert "initial_stop_rule" in str(exc_info.value)
        assert "lod_minus_5pct" in str(exc_info.value)

    def test_config_error_is_exception(self):
        """ConfigError must be a proper Exception subclass."""
        err = ConfigError("test")
        assert isinstance(err, Exception)

    def test_no_sys_exit_in_loader(self):
        """Loader must raise ConfigError, not call sys.exit."""
        import inspect
        import bot.config.loader as loader_mod
        src = inspect.getsource(loader_mod)
        assert "sys.exit" not in src


# ============================================================
# sizing_equity_usd — RISK-01 / 260702-ick Task 3
# ============================================================

class TestSizingEquityUsd:
    """Tests for risk.sizing_equity_usd optional field in rules.json.

    Default: 100000 when key absent.
    Explicit null: None (signals live-equity fallback).
    """

    def test_sizing_equity_usd_defaults_to_100000_when_absent(self, tmp_path):
        """When risk.sizing_equity_usd is absent from rules.json, cfg.sizing_equity_usd == 100000."""
        rules = dict(CANONICAL_RULES)
        # Ensure sizing_equity_usd is absent from the risk block (current canonical rules ~53-58)
        risk_block = dict(rules["risk"])
        risk_block.pop("sizing_equity_usd", None)  # remove if accidentally present
        rules["risk"] = risk_block

        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")

        cfg = load_strategy_config(str(path))
        assert cfg.sizing_equity_usd == 100_000, (
            f"sizing_equity_usd must default to 100000 when absent; got {cfg.sizing_equity_usd}"
        )

    def test_sizing_equity_usd_explicit_null_maps_to_none(self, tmp_path):
        """When risk.sizing_equity_usd is explicitly null, cfg.sizing_equity_usd is None."""
        rules = dict(CANONICAL_RULES)
        risk_block = dict(rules["risk"])
        risk_block["sizing_equity_usd"] = None
        rules["risk"] = risk_block

        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")

        cfg = load_strategy_config(str(path))
        assert cfg.sizing_equity_usd is None, (
            f"Explicit null must map to None (live-equity fallback); got {cfg.sizing_equity_usd}"
        )

    def test_sizing_equity_usd_explicit_value_loaded(self, tmp_path):
        """When risk.sizing_equity_usd is set to a number, it is loaded correctly."""
        rules = dict(CANONICAL_RULES)
        risk_block = dict(rules["risk"])
        risk_block["sizing_equity_usd"] = 200_000
        rules["risk"] = risk_block

        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")

        cfg = load_strategy_config(str(path))
        assert cfg.sizing_equity_usd == 200_000, (
            f"Explicit value must be loaded; got {cfg.sizing_equity_usd}"
        )


# ============================================================
# Phase 7 config keys — daily_circuit_breaker_r, use_broker_stop_orders,
# rvol_tod_lookback_days (CFG-01, D-02/D-05)
# ============================================================

# CANONICAL_RULES_WITH_PHASE7 extends the existing CANONICAL_RULES with the
# three new Phase 7 keys so tests can load a fully-specified rules.json.
CANONICAL_RULES_WITH_PHASE7 = {
    **CANONICAL_RULES,
    "risk": {**CANONICAL_RULES["risk"], "daily_circuit_breaker_r": 2.0},
    "execution": {**CANONICAL_RULES["execution"], "use_broker_stop_orders": True},
    "intraday_filters": {
        **CANONICAL_RULES["intraday_filters"],
        "I3_rvol_tod_lookback_days": 14,
    },
    "service": {
        **CANONICAL_RULES["service"],
        "premarket_scan_et": "08:30",
        "market_open_et": "09:30",
        "intraday_rescan_interval_min": 30,
        "intraday_rescan_start_et": "09:55",
        "intraday_rescan_end_et": "12:55",
        "eod_report_et": "15:55",
        "watchdog_poll_interval_s": 60,
        "watchdog_reconnect_initial_s": 5,
        "watchdog_reconnect_cap_s": 60,
        "alerts_enabled": True,
        "misfire_grace_scan_s": 3600,
        "misfire_grace_rescan_s": 600,
        "force_close_misfire_grace_s": 300,
        "launchd_throttle_interval_s": 30,
        "crash_loop_alert_threshold": 5,
    },
}


@pytest.fixture
def rules_file_phase7(tmp_path):
    """Write Phase 7 extended rules.json (includes the 3 new keys) to a tmp file."""
    path = tmp_path / "rules_phase7.json"
    path.write_text(json.dumps(CANONICAL_RULES_WITH_PHASE7), encoding="utf-8")
    return str(path)


class TestPhase7ConfigKeys:
    """Tests for the three new Phase 7 StrategyConfig fields (CFG-01, D-02/D-05)."""

    def test_load_real_rules_json_has_daily_circuit_breaker_r(self):
        """load_strategy_config on the real rules.json must yield cfg.daily_circuit_breaker_r == 2.0."""
        cfg = load_strategy_config("rules.json")
        assert cfg.daily_circuit_breaker_r == 2.0, (
            f"Expected daily_circuit_breaker_r=2.0; got {cfg.daily_circuit_breaker_r}"
        )

    def test_load_real_rules_json_has_use_broker_stop_orders(self):
        """load_strategy_config on the real rules.json must yield cfg.use_broker_stop_orders is False.

        UAT (07-UAT.md Test 1, 2026-07-06) empirically confirmed SIMULATE rejects
        Stop-Market orders outright ("Paper trading does not support Stop order"),
        so rules.json was corrected to route stop invalidation through the
        quote-tick fallback (_on_quote) instead of the broker stop path.
        """
        cfg = load_strategy_config("rules.json")
        assert cfg.use_broker_stop_orders is False, (
            f"Expected use_broker_stop_orders=False; got {cfg.use_broker_stop_orders}"
        )

    def test_load_real_rules_json_has_rvol_tod_lookback_days(self):
        """load_strategy_config on the real rules.json must yield cfg.rvol_tod_lookback_days == 14."""
        cfg = load_strategy_config("rules.json")
        assert cfg.rvol_tod_lookback_days == 14, (
            f"Expected rvol_tod_lookback_days=14; got {cfg.rvol_tod_lookback_days}"
        )

    def test_daily_circuit_breaker_r_wrong_type_raises_config_error(self, tmp_path):
        """risk.daily_circuit_breaker_r set to a string must raise ConfigError (type=number)."""
        bad = json.loads(json.dumps(CANONICAL_RULES_WITH_PHASE7))
        bad["risk"]["daily_circuit_breaker_r"] = "two"  # wrong type
        path = tmp_path / "bad_cbr.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_strategy_config(str(path))

    def test_use_broker_stop_orders_wrong_type_raises_config_error(self, tmp_path):
        """execution.use_broker_stop_orders set to a string must raise ConfigError (type=boolean)."""
        bad = json.loads(json.dumps(CANONICAL_RULES_WITH_PHASE7))
        bad["execution"]["use_broker_stop_orders"] = "yes"  # wrong type
        path = tmp_path / "bad_ubso.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_strategy_config(str(path))

    def test_phase7_keys_absent_use_safe_defaults(self, tmp_path):
        """When the three new keys are absent, loader must use safe defaults (no KeyError).

        defaults: daily_circuit_breaker_r=2.0, use_broker_stop_orders=True,
                  rvol_tod_lookback_days=14.
        """
        # CANONICAL_RULES does NOT contain the three new Phase 7 keys
        path = tmp_path / "rules_no_p7.json"
        path.write_text(json.dumps(CANONICAL_RULES), encoding="utf-8")
        cfg = load_strategy_config(str(path))
        assert cfg.daily_circuit_breaker_r == 2.0
        assert cfg.use_broker_stop_orders is True
        assert cfg.rvol_tod_lookback_days == 14


# ============================================================
# Exit-model config tests (CFG-01, EXIT-MODEL, plan 07-04)
# ============================================================

import os as _os
_RULES_JSON = _os.path.join(_os.path.dirname(__file__), "..", "..", "rules.json")


class TestExitModelConfig:
    """Exit-model config surface (exit.model in rules.json, schema enum, StrategyConfig.exit_model).

    Tests cover:
    1. Real rules.json: exit_model == "partial_be_trail" (the explicit default / current behavior)
    2. Config with exit.model omitted: defaults to "partial_be_trail" (no KeyError)
    3. exit.model = "fixed_2r": raises ConfigError referencing Phase 6 / backtester
    4. exit.model = "full_to_1p5r_trail": raises ConfigError (fail-closed)
    5. exit.model = unknown string ("moon"): fails jsonschema enum validation (ConfigError)
    """

    def test_real_rules_json_exit_model_is_partial_be_trail(self):
        """Load the canonical rules.json; cfg.exit_model must equal 'partial_be_trail'."""
        cfg = load_strategy_config(_RULES_JSON)
        assert cfg.exit_model == "partial_be_trail"

    def test_exit_model_omitted_defaults_to_partial_be_trail(self, tmp_path):
        """When exit.model key is absent the loader must default to 'partial_be_trail'."""
        data = json.loads(json.dumps(CANONICAL_RULES))
        data["exit"].pop("model", None)  # ensure key is absent
        path = tmp_path / "no_model.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        cfg = load_strategy_config(str(path))
        assert cfg.exit_model == "partial_be_trail"

    def test_exit_model_fixed_2r_raises_config_error(self, tmp_path):
        """exit.model='fixed_2r' is not yet implemented — loader must fail closed."""
        data = json.loads(json.dumps(CANONICAL_RULES))
        data["exit"]["model"] = "fixed_2r"
        path = tmp_path / "fixed_2r.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        msg = str(exc_info.value)
        # Must mention the model value and point to Phase 6 / plan 07-06
        assert "fixed_2r" in msg
        assert "phase 6" in msg.lower() or "07-06" in msg

    def test_exit_model_full_to_1p5r_trail_raises_config_error(self, tmp_path):
        """exit.model='full_to_1p5r_trail' is not yet implemented — loader must fail closed."""
        data = json.loads(json.dumps(CANONICAL_RULES))
        data["exit"]["model"] = "full_to_1p5r_trail"
        path = tmp_path / "full_trail.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        msg = str(exc_info.value)
        assert "full_to_1p5r_trail" in msg
        assert "phase 6" in msg.lower() or "07-06" in msg

    def test_exit_model_unknown_string_fails_schema_validation(self, tmp_path):
        """An unknown exit.model value (e.g. 'moon') must fail jsonschema enum check."""
        data = json.loads(json.dumps(CANONICAL_RULES))
        data["exit"]["model"] = "moon"
        path = tmp_path / "unknown_model.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_strategy_config(str(path))
        msg = str(exc_info.value)
        # Schema validation error must mention enum or the invalid value
        assert "moon" in msg or "enum" in msg.lower() or "schema" in msg.lower()
