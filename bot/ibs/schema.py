#!/usr/bin/env python3
"""
bot.ibs.schema — JSON Schema for rules_ibs.json (CFG-01 / IBS-01).

Structural only (types, bounds, required keys); cross-field business rules
live in bot/ibs/config.py. Mirrors bot/options/schema.py.

Exports: IBS_SCHEMA
"""

_HHMM_PATTERN = r"^\d{2}:\d{2}$"


def _int(minimum=1):
    return {"type": "integer", "minimum": minimum}


def _pos():
    return {"type": "number", "exclusiveMinimum": 0}


def _str():
    return {"type": "string", "minLength": 1}


def _block(props):
    return {"type": "object", "required": list(props), "properties": props}


IBS_SCHEMA = {
    "type": "object",
    "required": ["strategy_name", "universe", "signal", "risk", "execution", "service"],
    "additionalProperties": True,
    "properties": {
        "strategy_name": {"const": "ibs_etf_mean_reversion"},
        "universe": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^US\.[A-Z]+$"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "signal": _block({
            "ibs_entry_max": _pos(),
            "ibs_exit_min": _pos(),
            "max_hold_trading_days": _int(),
            "max_snapshot_age_s": _pos(),
        }),
        "risk": _block({
            "sizing_equity_usd": _pos(),
            "position_pct_of_equity": _pos(),
            "max_concurrent_positions": _int(),
        }),
        "execution": _block({
            "entry_limit_buffer_usd": {"type": "number", "minimum": 0},
            "exit_limit_buffer_usd": {"type": "number", "minimum": 0},
            "order_ttl_seconds": _pos(),
            # one order_list_query per poll must stay under moomoo's 10 req / 30 s per acc_id
            "poll_interval_seconds": {"type": "number", "exclusiveMinimum": 3},
            "escalation_step_usd": _pos(),
            "max_reprices": _int(0),
            # the executor gives up this long before the hard-cancel sweep (CR-02)
            "executor_margin_s": _pos(),
        }),
        "service": _block({
            "arm_time_et": {"type": "string", "pattern": _HHMM_PATTERN},
            "decision_before_close_min": _int(),
            "hard_cancel_before_close_min": _int(0),
            "eod_report_after_close_min": _int(),
            "misfire_grace_s": _int(),
            "watchdog_poll_interval_s": _pos(),
            "watchdog_reconnect_initial_s": _pos(),
            "watchdog_reconnect_cap_s": _pos(),
            "state_db": _str(),
            "kill_file": _str(),
            "report_dir": _str(),
            "log_file": _str(),
        }),
    },
}
