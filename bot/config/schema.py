#!/usr/bin/env python3
"""
bot.config.schema — JSON Schema definition for rules.json (CFG-01).

SCHEMA is a jsonschema dict that validates the canonical Trend Join Long
strategy config. Every top-level group is required; nested fields are typed
(numbers as "number", time strings as "string") with required lists so a
removed key fails validation.

Exports: SCHEMA
"""

# ============================================================
# JSON Schema for rules.json
# ============================================================

SCHEMA = {
    "type": "object",
    "required": [
        "strategy_name",
        "direction",
        "trade_timeframe",
        "universe_filters",
        "daily_filters",
        "intraday_filters",
        "time_filter",
        "exit",
        "risk",
        "execution"
    ],
    "additionalProperties": True,
    "properties": {
        "strategy_name": {"type": "string"},
        "direction": {"type": "string"},
        "trade_timeframe": {"type": "string"},

        # --------------------------------------------------------
        # universe_filters
        # --------------------------------------------------------
        "universe_filters": {
            "type": "object",
            "required": ["index", "min_price_usd"],
            "properties": {
                "index": {"type": "string"},
                "min_price_usd": {"type": "number"}
            }
        },

        # --------------------------------------------------------
        # daily_filters
        # --------------------------------------------------------
        "daily_filters": {
            "type": "object",
            "required": [
                "D1_above_prior_day_high",
                "D2_prior_close_above_sma200",
                "D3_min_gap_pct_from_prior_close"
            ],
            "properties": {
                "D1_above_prior_day_high": {"type": "boolean"},
                "D2_prior_close_above_sma200": {"type": "boolean"},
                "D3_min_gap_pct_from_prior_close": {"type": "number"}
            }
        },

        # --------------------------------------------------------
        # intraday_filters
        # --------------------------------------------------------
        "intraday_filters": {
            "type": "object",
            "required": [
                "I1_above_premarket_high",
                "I2_above_today_hod",
                "I3_rvol_min",
                "I3_rvol_lookback_days"
            ],
            "properties": {
                "I1_above_premarket_high": {"type": "boolean"},
                "I2_above_today_hod": {"type": "boolean"},
                "I3_rvol_min": {"type": "number"},
                "I3_rvol_lookback_days": {"type": "integer"}
            }
        },

        # --------------------------------------------------------
        # time_filter
        # --------------------------------------------------------
        "time_filter": {
            "type": "object",
            "required": ["earliest_entry_et", "latest_entry_et", "force_close_et"],
            "properties": {
                "earliest_entry_et": {"type": "string"},
                "latest_entry_et": {"type": "string"},
                "force_close_et": {"type": "string"}
            }
        },

        # --------------------------------------------------------
        # exit
        # --------------------------------------------------------
        "exit": {
            "type": "object",
            "required": [
                "initial_stop_rule",
                "partial_profit_trigger_R",
                "partial_profit_fraction",
                "breakeven_trigger_R",
                "post_breakeven_trail"
            ],
            "properties": {
                "initial_stop_rule": {"type": "string"},
                "partial_profit_trigger_R": {"type": "number"},
                "partial_profit_fraction": {"type": "number"},
                "breakeven_trigger_R": {"type": "number"},
                "post_breakeven_trail": {"type": "string"}
            }
        },

        # --------------------------------------------------------
        # risk
        # --------------------------------------------------------
        "risk": {
            "type": "object",
            "required": [
                "max_risk_per_trade_pct",
                "max_position_size_pct_of_portfolio",
                "max_concurrent_positions",
                "max_trades_per_day"
            ],
            "properties": {
                "max_risk_per_trade_pct": {"type": "number"},
                "max_position_size_pct_of_portfolio": {"type": "number"},
                "max_concurrent_positions": {"type": "integer"},
                "max_trades_per_day": {"type": "integer"}
            }
        },

        # --------------------------------------------------------
        # execution (Phase 4 tunables — CFG-01, D-05/D-07/D-08)
        # --------------------------------------------------------
        "execution": {
            "type": "object",
            "required": [
                "entry_limit_buffer_usd",
                "entry_ttl_seconds",
                "entry_max_retries",
                "entry_poll_interval_seconds",
                "exit_limit_buffer_usd",
                "exit_ttl_seconds",
                "exit_escalation_step_usd",
                "exit_escalation_cadence_seconds",
                "force_close_escalation_step_usd",
                "force_close_escalation_cadence_seconds"
            ],
            "properties": {
                "entry_limit_buffer_usd":                 {"type": "number"},
                "entry_ttl_seconds":                      {"type": "number"},
                "entry_max_retries":                      {"type": "number"},
                "entry_poll_interval_seconds":            {"type": "number"},
                "exit_limit_buffer_usd":                  {"type": "number"},
                "exit_ttl_seconds":                       {"type": "number"},
                "exit_escalation_step_usd":               {"type": "number"},
                "exit_escalation_cadence_seconds":        {"type": "number"},
                "force_close_escalation_step_usd":        {"type": "number"},
                "force_close_escalation_cadence_seconds": {"type": "number"}
            }
        }
    }
}
