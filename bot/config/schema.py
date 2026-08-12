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
        "execution",
        "service"
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
                "I3_rvol_lookback_days": {"type": "integer"},
                # Phase 7 addition (optional — loader provides default=14)
                "I3_rvol_tod_lookback_days": {"type": "integer"},
                # I2_mode is optional (defaults to "close_at_hod" in the loader);
                # only these two candidate strings are valid. Unknown strings fail
                # here before the implemented-set guard in the loader (mirrors
                # exit.model's own enum + fail-closed pattern).
                "I2_mode": {
                    "type": "string",
                    "enum": ["close_at_hod", "close_above_prior_hod"]
                }
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
                # model is optional (defaults to "partial_be_trail" in the loader);
                # only the three candidate strings from the research phase are valid.
                # Unknown strings fail here before the implemented-set guard in the loader.
                "model": {
                    "type": "string",
                    "enum": ["partial_be_trail", "fixed_2r", "full_to_1p5r_trail"]
                },
                "initial_stop_rule": {"type": "string"},
                "partial_profit_trigger_R": {"type": "number"},
                "partial_profit_fraction": {"type": "number"},
                "breakeven_trigger_R": {"type": "number"},
                "post_breakeven_trail": {"type": "string"},
                # breakeven_buffer_R is optional (defaults to 0.0 in the loader —
                # today's exact-entry breakeven behavior, P2 strategy-audit finding).
                "breakeven_buffer_R": {"type": "number"}
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
                "max_trades_per_day": {"type": "integer"},
                "sizing_equity_usd": {"type": ["number", "null"]},
                # Phase 7 addition (optional — loader provides default=2.0)
                "daily_circuit_breaker_r": {"type": "number"}
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
                "force_close_escalation_cadence_seconds": {"type": "number"},
                # Phase 7 addition (optional — loader provides default=True)
                "use_broker_stop_orders":                 {"type": "boolean"}
            }
        },

        # --------------------------------------------------------
        # service (Phase 5 tunables — CFG-01, D-01/D-03/D-06/D-10)
        # --------------------------------------------------------
        "service": {
            "type": "object",
            "required": [
                "premarket_scan_et",
                "market_open_et",
                "intraday_rescan_interval_min",
                "intraday_rescan_start_et",
                "intraday_rescan_end_et",
                "eod_report_et",
                "watchdog_poll_interval_s",
                "watchdog_reconnect_initial_s",
                "watchdog_reconnect_cap_s",
                "alerts_enabled",
                "misfire_grace_scan_s",
                "misfire_grace_rescan_s",
                "force_close_misfire_grace_s",
                "launchd_throttle_interval_s",
                "crash_loop_alert_threshold"
            ],
            "properties": {
                "premarket_scan_et":              {"type": "string"},
                "market_open_et":                 {"type": "string"},
                "intraday_rescan_interval_min":   {"type": "integer"},
                "intraday_rescan_start_et":       {"type": "string"},
                "intraday_rescan_end_et":         {"type": "string"},
                "eod_report_et":                  {"type": "string"},
                "watchdog_poll_interval_s":       {"type": "number"},
                "watchdog_reconnect_initial_s":   {"type": "number"},
                "watchdog_reconnect_cap_s":       {"type": "number"},
                "alerts_enabled":                 {"type": "boolean"},
                "misfire_grace_scan_s":           {"type": "integer"},
                "misfire_grace_rescan_s":         {"type": "integer"},
                "force_close_misfire_grace_s":    {"type": "integer"},
                "launchd_throttle_interval_s":    {"type": "integer"},
                "crash_loop_alert_threshold":     {"type": "integer"}
            }
        }
    }
}
