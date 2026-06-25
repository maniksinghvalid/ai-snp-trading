#!/usr/bin/env python3
"""
bot.config.loader — Load and validate rules.json into a typed StrategyConfig.

Reads rules.json (CFG-01 — single source of truth for all strategy parameters),
validates it with jsonschema, and maps it to a StrategyConfig dataclass.
Raises ConfigError on missing file, malformed JSON, or schema validation failure
— the caller (bot/main.py) handles process exit.

Exports: load_strategy_config, StrategyConfig, ConfigError
"""
import json
from dataclasses import dataclass

import jsonschema

from bot.config.schema import SCHEMA


# ============================================================
# Public Exceptions
# ============================================================

class ConfigError(Exception):
    """Raised when rules.json is missing, malformed, or schema-invalid."""


# ============================================================
# Stop-rule parsing (CFG-01 / D-12)
# ============================================================

# Maps each recognised exit.initial_stop_rule string to the stop distance
# below the low-of-day, expressed as a percentage. The initial stop is the
# SOURCE OF TRUTH for stop placement and is deliberately decoupled from
# risk.max_risk_per_trade_pct (a position-sizing budget). See CR-01.
_STOP_RULE_PCT = {
    "lod_minus_1pct": 1.0,
}


def parse_initial_stop_rule(rule: str) -> float:
    """Parse exit.initial_stop_rule into a stop percentage below LOD.

    "lod_minus_1pct" -> 1.0 (stop placed 1% below the low-of-day).

    Raises:
        ConfigError: if the rule string is not a recognised stop rule. The
            stop must NEVER silently fall back to the risk budget (CR-01).
    """
    pct = _STOP_RULE_PCT.get(rule)
    if pct is None:
        raise ConfigError(
            f"exit.initial_stop_rule '{rule}' is not a recognised stop rule "
            f"(expected one of: {', '.join(sorted(_STOP_RULE_PCT))})"
        )
    return float(pct)


# ============================================================
# StrategyConfig Dataclass
# ============================================================

@dataclass
class StrategyConfig:
    """
    Typed, flat representation of the validated rules.json content.

    All nested JSON groups (universe_filters, daily_filters, intraday_filters,
    time_filter, exit, risk) are flattened into clearly named fields so callers
    never navigate raw dicts. Every field corresponds directly to a value in the
    canonical rules.json (CFG-01) block in PROJECT.md.
    """

    # ---- universe_filters ----
    min_price_usd: float           # universe_filters.min_price_usd

    # ---- daily_filters ----
    d3_min_gap_pct: float          # daily_filters.D3_min_gap_pct_from_prior_close

    # ---- intraday_filters ----
    rvol_min: float                # intraday_filters.I3_rvol_min
    rvol_lookback_days: int        # intraday_filters.I3_rvol_lookback_days

    # ---- time_filter ----
    earliest_entry_et: str         # time_filter.earliest_entry_et  (HH:MM ET string)
    latest_entry_et: str           # time_filter.latest_entry_et
    force_close_et: str            # time_filter.force_close_et

    # ---- exit ----
    initial_stop_pct: float           # exit.initial_stop_rule parsed to a percentage
    partial_profit_trigger_r: float   # exit.partial_profit_trigger_R
    partial_profit_fraction: float    # exit.partial_profit_fraction (~0.3333)
    breakeven_trigger_r: float        # exit.breakeven_trigger_R

    # ---- risk ----
    max_risk_per_trade_pct: float     # risk.max_risk_per_trade_pct
    max_position_size_pct: int        # risk.max_position_size_pct_of_portfolio
    max_concurrent_positions: int     # risk.max_concurrent_positions
    max_trades_per_day: int           # risk.max_trades_per_day

    # ---- execution (Phase 4 tunables — CFG-01, D-05/D-07/D-08) ----
    entry_limit_buffer_usd: float           # execution.entry_limit_buffer_usd
    entry_ttl_seconds: float                # execution.entry_ttl_seconds
    entry_max_retries: int                  # execution.entry_max_retries (int — retry count)
    entry_poll_interval_seconds: float      # execution.entry_poll_interval_seconds
    exit_limit_buffer_usd: float            # execution.exit_limit_buffer_usd
    exit_ttl_seconds: float                 # execution.exit_ttl_seconds
    exit_escalation_step_usd: float         # execution.exit_escalation_step_usd
    exit_escalation_cadence_seconds: float  # execution.exit_escalation_cadence_seconds
    force_close_escalation_step_usd: float           # execution.force_close_escalation_step_usd
    force_close_escalation_cadence_seconds: float    # execution.force_close_escalation_cadence_seconds

    # ---- service (Phase 5 tunables — CFG-01, D-01/D-03/D-06/D-10) ----
    premarket_scan_et: str                  # service.premarket_scan_et  ("HH:MM" ET)
    market_open_et: str                     # service.market_open_et
    intraday_rescan_interval_min: int       # service.intraday_rescan_interval_min
    intraday_rescan_start_et: str           # service.intraday_rescan_start_et
    intraday_rescan_end_et: str             # service.intraday_rescan_end_et (SCAN-07 window end)
    eod_report_et: str                      # service.eod_report_et
    watchdog_poll_interval_s: float         # service.watchdog_poll_interval_s (D-10)
    watchdog_reconnect_initial_s: float     # service.watchdog_reconnect_initial_s
    watchdog_reconnect_cap_s: float         # service.watchdog_reconnect_cap_s (D-10)
    alerts_enabled: bool                    # service.alerts_enabled (D-13 non-secret toggle)
    misfire_grace_scan_s: int               # service.misfire_grace_scan_s (D-03/Pitfall 7)
    misfire_grace_rescan_s: int             # service.misfire_grace_rescan_s
    force_close_misfire_grace_s: int        # service.force_close_misfire_grace_s (D-03/Pitfall 7)
    launchd_throttle_interval_s: int        # service.launchd_throttle_interval_s (D-06)
    crash_loop_alert_threshold: int         # service.crash_loop_alert_threshold (D-06)


# ============================================================
# Loader
# ============================================================

def load_strategy_config(path: str = "rules.json") -> StrategyConfig:
    """
    Load and validate rules.json, returning a typed StrategyConfig.

    Raises ConfigError on:
      - FileNotFoundError (missing file — includes path in message)
      - json.JSONDecodeError (malformed/invalid JSON syntax)
      - jsonschema.ValidationError (schema violation — includes field context)

    Per PATTERNS.md "Raise, don't exit": raises ConfigError; caller handles exit.
    """

    # --- Step 1: Read the file ---
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise ConfigError(f"rules.json not found: {path}")

    # --- Step 2: Parse JSON ---
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"rules.json is not valid JSON: {exc}") from exc

    # --- Step 3: jsonschema validation ---
    try:
        jsonschema.validate(data, SCHEMA)
    except jsonschema.ValidationError as exc:
        # Include both the failing field path and the human-readable message
        field_path = " -> ".join(str(p) for p in exc.absolute_path) if exc.absolute_path else exc.validator_value
        raise ConfigError(
            f"rules.json schema validation failed: {exc.message} "
            f"(path: {field_path if field_path else exc.json_path})"
        ) from exc

    # --- Step 4: Map validated dict to StrategyConfig ---
    uf = data["universe_filters"]
    df = data["daily_filters"]
    inf = data["intraday_filters"]
    tf = data["time_filter"]
    ex = data["exit"]
    rk = data["risk"]
    ex_cfg = data.get("execution", {})
    svc_cfg = data.get("service", {})

    return StrategyConfig(
        # universe
        min_price_usd=float(uf["min_price_usd"]),
        # daily
        d3_min_gap_pct=float(df["D3_min_gap_pct_from_prior_close"]),
        # intraday
        rvol_min=float(inf["I3_rvol_min"]),
        rvol_lookback_days=int(inf["I3_rvol_lookback_days"]),
        # time
        earliest_entry_et=str(tf["earliest_entry_et"]),
        latest_entry_et=str(tf["latest_entry_et"]),
        force_close_et=str(tf["force_close_et"]),
        # exit
        initial_stop_pct=parse_initial_stop_rule(str(ex["initial_stop_rule"])),
        partial_profit_trigger_r=float(ex["partial_profit_trigger_R"]),
        partial_profit_fraction=float(ex["partial_profit_fraction"]),
        breakeven_trigger_r=float(ex["breakeven_trigger_R"]),
        # risk
        max_risk_per_trade_pct=float(rk["max_risk_per_trade_pct"]),
        max_position_size_pct=int(rk["max_position_size_pct_of_portfolio"]),
        max_concurrent_positions=int(rk["max_concurrent_positions"]),
        max_trades_per_day=int(rk["max_trades_per_day"]),
        # execution (Phase 4 tunables — CFG-01, D-05/D-07/D-08)
        entry_limit_buffer_usd=float(ex_cfg["entry_limit_buffer_usd"]),
        entry_ttl_seconds=float(ex_cfg["entry_ttl_seconds"]),
        entry_max_retries=int(ex_cfg["entry_max_retries"]),
        entry_poll_interval_seconds=float(ex_cfg["entry_poll_interval_seconds"]),
        exit_limit_buffer_usd=float(ex_cfg["exit_limit_buffer_usd"]),
        exit_ttl_seconds=float(ex_cfg["exit_ttl_seconds"]),
        exit_escalation_step_usd=float(ex_cfg["exit_escalation_step_usd"]),
        exit_escalation_cadence_seconds=float(ex_cfg["exit_escalation_cadence_seconds"]),
        force_close_escalation_step_usd=float(ex_cfg["force_close_escalation_step_usd"]),
        force_close_escalation_cadence_seconds=float(ex_cfg["force_close_escalation_cadence_seconds"]),
        # service (Phase 5 tunables — CFG-01, D-01/D-03/D-06/D-10)
        premarket_scan_et=str(svc_cfg["premarket_scan_et"]),
        market_open_et=str(svc_cfg["market_open_et"]),
        intraday_rescan_interval_min=int(svc_cfg["intraday_rescan_interval_min"]),
        intraday_rescan_start_et=str(svc_cfg["intraday_rescan_start_et"]),
        intraday_rescan_end_et=str(svc_cfg["intraday_rescan_end_et"]),
        eod_report_et=str(svc_cfg["eod_report_et"]),
        watchdog_poll_interval_s=float(svc_cfg["watchdog_poll_interval_s"]),
        watchdog_reconnect_initial_s=float(svc_cfg["watchdog_reconnect_initial_s"]),
        watchdog_reconnect_cap_s=float(svc_cfg["watchdog_reconnect_cap_s"]),
        alerts_enabled=bool(svc_cfg["alerts_enabled"]),
        misfire_grace_scan_s=int(svc_cfg["misfire_grace_scan_s"]),
        misfire_grace_rescan_s=int(svc_cfg["misfire_grace_rescan_s"]),
        force_close_misfire_grace_s=int(svc_cfg["force_close_misfire_grace_s"]),
        launchd_throttle_interval_s=int(svc_cfg["launchd_throttle_interval_s"]),
        crash_loop_alert_threshold=int(svc_cfg["crash_loop_alert_threshold"]),
    )
