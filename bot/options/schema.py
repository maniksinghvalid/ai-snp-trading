#!/usr/bin/env python3
"""
bot.options.schema — JSON Schema definitions for rules_options.json (CFG-01).

OPTIONS_SCHEMA validates the legacy flat single-strategy shape (still
supported forever, D-06). STRATEGIES_SCHEMA validates the newer multi-
strategy shape (`strategies` array + shared `risk`/`execution`/`service`
blocks, D-01). Both are structural only — jsonschema has zero conditional-
keyword precedent in this codebase (this file never branches its shape on
another field's value); per-structure business rules (e.g. IV-gate keys
required only for credit structures) are plain-Python fail-closed checks in
bot/options/config.py, mirroring `_IMPLEMENTED_EXIT_MODELS` in
bot/config/loader.py.

Mirrors bot/config/schema.py in structure and style.

Exports: OPTIONS_SCHEMA, STRATEGIES_SCHEMA
"""

# HH:MM time-of-day strings (ET). jsonschema only applies "pattern" to strings,
# so a nullable time key can carry the same pattern safely.
_HHMM_PATTERN = r"^\d{2}:\d{2}$"

# Only defined-risk structures the strategy code actually implements may run.
# Shared by both schemas' structure.type enum (D-11).
_STRUCTURE_TYPES = ["iron_condor", "put_credit_spread", "bull_call_spread"]


# ============================================================
# JSON Schema for rules_options.json
# ============================================================

OPTIONS_SCHEMA = {
    "type": "object",
    "required": [
        "strategy_name",
        "universe",
        "entry",
        "structure",
        "sizing",
        "manage",
        "execution",
        "service"
    ],
    "additionalProperties": True,
    "properties": {
        "strategy_name": {"type": "string"},

        # --------------------------------------------------------
        # universe — the tradeable underlyings (liquid ETFs, D2)
        # --------------------------------------------------------
        "universe": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1
        },

        # --------------------------------------------------------
        # entry — IV/liquidity/expiry gates and scan times
        # --------------------------------------------------------
        "entry": {
            "type": "object",
            "required": [
                "ivr_min",
                "ivp_min",
                "fear_drop_pct",
                "fear_ivr_min",
                "max_spread_pct_of_mid",
                "max_spread_abs_usd",
                "min_open_interest",
                "target_dte",
                "min_dte",
                "max_dte",
                "prefer_monthly",
                "entry_scan_et",
                "second_entry_scan_et"
            ],
            "properties": {
                "ivr_min":               {"type": "number"},
                # null = IVP dual gate disabled (IVR alone decides).
                "ivp_min":               {"type": ["number", "null"]},
                "fear_drop_pct":         {"type": "number"},
                "fear_ivr_min":          {"type": "number"},
                "max_spread_pct_of_mid": {"type": "number"},
                # A leg passes if spread <= pct-of-mid OR spread <= this absolute
                # floor (cheap far-OTM wings are penny/nickel-wide but fail a % gate).
                "max_spread_abs_usd":    {"type": "number"},
                "min_open_interest":     {"type": "integer"},
                "target_dte":            {"type": "integer"},
                "min_dte":               {"type": "integer"},
                "max_dte":               {"type": "integer"},
                "prefer_monthly":        {"type": "boolean"},
                "entry_scan_et":         {"type": "string", "pattern": _HHMM_PATTERN},
                # null = no second daily entry scan.
                "second_entry_scan_et":  {"type": ["string", "null"], "pattern": _HHMM_PATTERN}
            }
        },

        # --------------------------------------------------------
        # structure — which defined-risk spread, and its geometry
        # --------------------------------------------------------
        "structure": {
            "type": "object",
            "required": [
                "type",
                "short_delta",
                "wing_width_pct_of_underlying",
                "min_wing_width_usd",
                "min_credit_to_width"
            ],
            "properties": {
                # Only defined-risk structures are valid (D1). Unknown strings
                # fail here, before the implemented-set guard in the loader
                # (mirrors exit.model's enum + fail-closed pattern).
                "type": {
                    "type": "string",
                    "enum": _STRUCTURE_TYPES
                },
                "short_delta":                  {"type": "number"},
                "wing_width_pct_of_underlying": {"type": "number"},
                # Dollar floor on the wing width (1% of a $60 ETF is one $0.50
                # strike → 30+ lot condors for a $1k risk budget).
                "min_wing_width_usd":           {"type": "number"},
                "min_credit_to_width":          {"type": "number"}
            }
        },

        # --------------------------------------------------------
        # sizing — dollar risk, BP usage, and occurrence caps
        # --------------------------------------------------------
        "sizing": {
            "type": "object",
            "required": [
                "sizing_equity_usd",
                "max_risk_per_trade_pct",
                "max_bp_usage_pct",
                "max_concurrent_positions",
                "max_new_positions_per_day",
                "daily_loss_limit_pct"
            ],
            "properties": {
                "sizing_equity_usd":         {"type": "number"},
                "max_risk_per_trade_pct":    {"type": "number"},
                "max_bp_usage_pct":          {"type": "number"},
                "max_concurrent_positions":  {"type": "integer"},
                "max_new_positions_per_day": {"type": "integer"},
                "daily_loss_limit_pct":      {"type": "number"}
            }
        },

        # --------------------------------------------------------
        # manage — exit triggers (profit / DTE / assignment guard)
        # --------------------------------------------------------
        "manage": {
            "type": "object",
            "required": [
                "manage_interval_min",
                "profit_target_pct_of_credit",
                "manage_dte",
                "stop_loss_credit_multiple",
                "assignment_guard_dte"
            ],
            "properties": {
                "manage_interval_min":         {"type": "integer"},
                "profit_target_pct_of_credit": {"type": "number"},
                "manage_dte":                  {"type": "integer"},
                # null = no hard P&L stop (defined risk: take profit or hold).
                "stop_loss_credit_multiple":   {"type": ["number", "null"]},
                "assignment_guard_dte":        {"type": "integer"}
            }
        },

        # --------------------------------------------------------
        # execution — per-leg limit order pricing and escalation
        # --------------------------------------------------------
        "execution": {
            "type": "object",
            "required": [
                "limit_buffer_usd",
                "poll_interval_s",
                "ttl_s",
                "escalation_step_usd",
                "max_retries"
            ],
            "properties": {
                "limit_buffer_usd":    {"type": "number"},
                "poll_interval_s":     {"type": "number"},
                "ttl_s":               {"type": "number"},
                "escalation_step_usd": {"type": "number"},
                "max_retries":         {"type": "integer"}
            }
        },

        # --------------------------------------------------------
        # service — scheduler, watchdog, and the options bot's own
        # DB / kill file / report dir (D6 coexistence)
        # --------------------------------------------------------
        "service": {
            "type": "object",
            "required": [
                "eod_report_et",
                "watchdog_poll_interval_s",
                "watchdog_reconnect_initial_s",
                "watchdog_reconnect_cap_s",
                "state_db",
                "kill_file",
                "report_dir"
            ],
            "properties": {
                "eod_report_et":                {"type": "string", "pattern": _HHMM_PATTERN},
                "watchdog_poll_interval_s":     {"type": "number"},
                "watchdog_reconnect_initial_s": {"type": "number"},
                "watchdog_reconnect_cap_s":     {"type": "number"},
                "state_db":                     {"type": "string"},
                "kill_file":                    {"type": "string"},
                "report_dir":                   {"type": "string"}
            }
        }
    }
}


# ============================================================
# JSON Schema for the strategies-shape rules_options.json (D-01, D-08)
# ============================================================
#
# Structural only. Per-structure requiredness (IV-gate keys for credit
# structures, long_delta/max_debit_to_width for bull_call_spread, the
# universe XOR universe_source rule, etc.) is enforced in
# bot/options/config.py's _check_strategy — see module docstring above.

STRATEGIES_SCHEMA = {
    "type": "object",
    "required": ["strategies", "risk", "execution", "service"],
    "additionalProperties": True,
    "properties": {
        "strategies": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "entry", "structure", "sizing", "manage"],
                "additionalProperties": True,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    # Exactly one of universe / universe_source is required —
                    # a loader-side XOR check (D-01/D-04), not expressible
                    # here without a jsonschema conditional.
                    "universe": OPTIONS_SCHEMA["properties"]["universe"],
                    "universe_source": {"type": "string"},
                    "entry": {
                        "type": "object",
                        "required": [
                            "max_spread_pct_of_mid",
                            "max_spread_abs_usd",
                            "min_open_interest",
                            "target_dte",
                            "min_dte",
                            "max_dte",
                            "prefer_monthly",
                            "entry_scan_et",
                            "second_entry_scan_et",
                        ],
                        # Reused by reference so IV-gate keys (credit-only,
                        # required by _check_strategy) stay typed when present.
                        "properties": OPTIONS_SCHEMA["properties"]["entry"]["properties"],
                    },
                    "structure": {
                        "type": "object",
                        "required": [
                            "type",
                            "wing_width_pct_of_underlying",
                            "min_wing_width_usd",
                        ],
                        "properties": {
                            "type": {"type": "string", "enum": _STRUCTURE_TYPES},
                            "short_delta": {"type": "number"},
                            "wing_width_pct_of_underlying": {"type": "number"},
                            "min_wing_width_usd": {"type": "number"},
                            "min_credit_to_width": {"type": "number"},
                            "long_delta": {"type": "number"},
                            "max_debit_to_width": {"type": "number"},
                        },
                    },
                    "sizing": {
                        "type": "object",
                        "required": [
                            "max_risk_per_trade_pct",
                            "max_concurrent_positions",
                            "max_new_positions_per_day",
                        ],
                        "properties": {
                            "max_risk_per_trade_pct": {"type": "number"},
                            "max_concurrent_positions": {"type": "integer"},
                            "max_new_positions_per_day": {"type": "integer"},
                        },
                    },
                    "manage": {
                        "type": "object",
                        "required": ["manage_dte", "assignment_guard_dte"],
                        "properties": {
                            "manage_dte": {"type": ["integer", "null"]},
                            "assignment_guard_dte": {"type": "integer"},
                            "profit_target_pct_of_credit": {"type": "number"},
                            "stop_loss_credit_multiple": {"type": ["number", "null"]},
                            "profit_target_pct_of_max": {"type": "number"},
                        },
                    },
                },
            },
        },
        "risk": {
            "type": "object",
            "required": ["sizing_equity_usd", "max_bp_usage_pct", "daily_loss_limit_pct"],
            "properties": {
                "sizing_equity_usd": {"type": "number"},
                "max_bp_usage_pct": {"type": "number"},
                "daily_loss_limit_pct": {"type": "number"},
            },
        },
        # Unchanged from today (D-02) — reused by reference.
        "execution": OPTIONS_SCHEMA["properties"]["execution"],
        "service": {
            "type": "object",
            "required": list(OPTIONS_SCHEMA["properties"]["service"]["required"]) + ["manage_interval_min"],
            "properties": {
                **OPTIONS_SCHEMA["properties"]["service"]["properties"],
                "manage_interval_min": {"type": "integer"},
                "equity_state_db": {"type": "string"},
            },
        },
    },
}
