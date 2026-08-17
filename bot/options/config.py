#!/usr/bin/env python3
"""
bot.options.config — Load and validate rules_options.json into an OptionsConfig.

Reads rules_options.json (CFG-01 — single source of truth for every options
strategy parameter), validates it with jsonschema, and maps it to a flat
OptionsConfig dataclass. Raises ConfigError on missing file, malformed JSON,
schema violation, or a schema-valid-but-unimplemented structure — the caller
(bot/main.py) handles process exit.

ConfigError is deliberately re-used from bot.config.loader: one config
exception type for the whole bot, so callers need a single except clause.

Exports: load_options_config, OptionsConfig
"""
import json
from dataclasses import dataclass
from typing import Optional

import jsonschema

from bot.config.loader import ConfigError
from bot.options.schema import OPTIONS_SCHEMA


# ============================================================
# Structure constants (CFG-01, D1)
# ============================================================

# Only defined-risk structures the strategy code actually implements may run.
# Mirrors _IMPLEMENTED_EXIT_MODELS in bot/config/loader.py: the schema enum
# rejects unknown garbage early, but passing schema validation alone must NEVER
# be sufficient — a future enum addition (e.g. "strangle", which needs a
# buying-power model that does not exist) has to be opted into here explicitly.
# The loader FAILS CLOSED for any value outside this set.
_IMPLEMENTED_STRUCTURES = ("iron_condor", "put_credit_spread")


# ============================================================
# OptionsConfig Dataclass
# ============================================================

@dataclass
class OptionsConfig:
    """
    Typed, flat representation of the validated rules_options.json content.

    All nested JSON groups (entry, structure, sizing, manage, execution,
    service) are flattened into clearly named fields so callers never navigate
    raw dicts. Field names are the JSON leaf names verbatim, with a single
    rename: structure.type -> structure_type (`type` shadows the builtin, and
    `structure` is already a pick_strikes parameter name).
    """

    # ---- top level ----
    strategy_name: str                     # strategy_name
    universe: tuple                        # universe (tuple: config is immutable)

    # ---- entry ----
    ivr_min: float                         # entry.ivr_min
    ivp_min: Optional[float]               # entry.ivp_min (None = IVP dual gate off)
    fear_drop_pct: float                   # entry.fear_drop_pct
    fear_ivr_min: float                    # entry.fear_ivr_min
    max_spread_pct_of_mid: float           # entry.max_spread_pct_of_mid
    max_spread_abs_usd: float              # entry.max_spread_abs_usd (absolute floor, OR'd with pct gate)
    min_open_interest: int                 # entry.min_open_interest
    target_dte: int                        # entry.target_dte
    min_dte: int                           # entry.min_dte
    max_dte: int                           # entry.max_dte
    prefer_monthly: bool                   # entry.prefer_monthly
    entry_scan_et: str                     # entry.entry_scan_et ("HH:MM" ET)
    second_entry_scan_et: Optional[str]    # entry.second_entry_scan_et (None = no 2nd scan)

    # ---- structure ----
    structure_type: str                    # structure.type (renamed from `type`)
    short_delta: float                     # structure.short_delta
    wing_width_pct_of_underlying: float    # structure.wing_width_pct_of_underlying
    min_credit_to_width: float             # structure.min_credit_to_width

    # ---- sizing ----
    sizing_equity_usd: float               # sizing.sizing_equity_usd
    max_risk_per_trade_pct: float          # sizing.max_risk_per_trade_pct
    max_bp_usage_pct: float                # sizing.max_bp_usage_pct
    max_concurrent_positions: int          # sizing.max_concurrent_positions
    max_new_positions_per_day: int         # sizing.max_new_positions_per_day
    daily_loss_limit_pct: float            # sizing.daily_loss_limit_pct

    # ---- manage ----
    manage_interval_min: int               # manage.manage_interval_min
    profit_target_pct_of_credit: float     # manage.profit_target_pct_of_credit
    manage_dte: int                        # manage.manage_dte
    stop_loss_credit_multiple: Optional[float]  # manage.stop_loss_credit_multiple (None = off)
    assignment_guard_dte: int              # manage.assignment_guard_dte

    # ---- execution ----
    limit_buffer_usd: float                # execution.limit_buffer_usd
    poll_interval_s: float                 # execution.poll_interval_s
    ttl_s: float                           # execution.ttl_s
    escalation_step_usd: float             # execution.escalation_step_usd
    max_retries: int                       # execution.max_retries

    # ---- service ----
    eod_report_et: str                     # service.eod_report_et ("HH:MM" ET)
    watchdog_poll_interval_s: float        # service.watchdog_poll_interval_s
    watchdog_reconnect_initial_s: float    # service.watchdog_reconnect_initial_s
    watchdog_reconnect_cap_s: float        # service.watchdog_reconnect_cap_s
    state_db: str                          # service.state_db (own DB — D6)
    kill_file: str                         # service.kill_file (own sentinel — D6)
    report_dir: str                        # service.report_dir (own reports — D6)


# ============================================================
# Loader
# ============================================================

def load_options_config(path: str = "rules_options.json") -> OptionsConfig:
    """
    Load and validate rules_options.json, returning a typed OptionsConfig.

    Raises ConfigError on:
      - FileNotFoundError (missing file — includes path in message)
      - json.JSONDecodeError (malformed/invalid JSON syntax)
      - jsonschema.ValidationError (schema violation — includes field context)
      - a structure.type that is schema-valid but not implemented (fail closed)

    Per PATTERNS.md "Raise, don't exit": raises ConfigError; caller handles exit.
    """

    # --- Step 1: Read the file ---
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise ConfigError(f"rules_options.json not found: {path}")

    # --- Step 2: Parse JSON ---
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"rules_options.json is not valid JSON: {exc}") from exc

    # --- Step 3: jsonschema validation ---
    try:
        jsonschema.validate(data, OPTIONS_SCHEMA)
    except jsonschema.ValidationError as exc:
        # Include both the failing field path and the human-readable message
        field_path = " -> ".join(str(p) for p in exc.absolute_path) if exc.absolute_path else exc.validator_value
        raise ConfigError(
            f"rules_options.json schema validation failed: {exc.message} "
            f"(path: {field_path if field_path else exc.json_path})"
        ) from exc

    # --- Step 4: Map validated dict to OptionsConfig ---
    en = data["entry"]
    st = data["structure"]
    sz = data["sizing"]
    mg = data["manage"]
    ex = data["execution"]
    svc = data["service"]

    # --- Step 4a: Structure fail-closed guard (T-0ph-01) ---
    # Schema validation (Step 3) already rejected unknown strings via enum.
    # This guard is defense-in-depth for any structure added to the enum before
    # pick_strikes/size_position learn to build it.
    structure_type = str(st["type"])
    if structure_type not in _IMPLEMENTED_STRUCTURES:
        raise ConfigError(
            f"structure.type '{structure_type}' is not implemented; "
            f"expected one of: {', '.join(_IMPLEMENTED_STRUCTURES)}"
        )

    return OptionsConfig(
        # top level
        strategy_name=str(data["strategy_name"]),
        universe=tuple(str(c) for c in data["universe"]),
        # entry
        ivr_min=float(en["ivr_min"]),
        ivp_min=float(en["ivp_min"]) if en["ivp_min"] is not None else None,
        fear_drop_pct=float(en["fear_drop_pct"]),
        fear_ivr_min=float(en["fear_ivr_min"]),
        max_spread_pct_of_mid=float(en["max_spread_pct_of_mid"]),
        max_spread_abs_usd=float(en["max_spread_abs_usd"]),
        min_open_interest=int(en["min_open_interest"]),
        target_dte=int(en["target_dte"]),
        min_dte=int(en["min_dte"]),
        max_dte=int(en["max_dte"]),
        prefer_monthly=bool(en["prefer_monthly"]),
        entry_scan_et=str(en["entry_scan_et"]),
        second_entry_scan_et=(
            str(en["second_entry_scan_et"]) if en["second_entry_scan_et"] is not None else None
        ),
        # structure
        structure_type=structure_type,
        short_delta=float(st["short_delta"]),
        wing_width_pct_of_underlying=float(st["wing_width_pct_of_underlying"]),
        min_credit_to_width=float(st["min_credit_to_width"]),
        # sizing
        sizing_equity_usd=float(sz["sizing_equity_usd"]),
        max_risk_per_trade_pct=float(sz["max_risk_per_trade_pct"]),
        max_bp_usage_pct=float(sz["max_bp_usage_pct"]),
        max_concurrent_positions=int(sz["max_concurrent_positions"]),
        max_new_positions_per_day=int(sz["max_new_positions_per_day"]),
        daily_loss_limit_pct=float(sz["daily_loss_limit_pct"]),
        # manage
        manage_interval_min=int(mg["manage_interval_min"]),
        profit_target_pct_of_credit=float(mg["profit_target_pct_of_credit"]),
        manage_dte=int(mg["manage_dte"]),
        stop_loss_credit_multiple=(
            float(mg["stop_loss_credit_multiple"])
            if mg["stop_loss_credit_multiple"] is not None else None
        ),
        assignment_guard_dte=int(mg["assignment_guard_dte"]),
        # execution
        limit_buffer_usd=float(ex["limit_buffer_usd"]),
        poll_interval_s=float(ex["poll_interval_s"]),
        ttl_s=float(ex["ttl_s"]),
        escalation_step_usd=float(ex["escalation_step_usd"]),
        max_retries=int(ex["max_retries"]),
        # service
        eod_report_et=str(svc["eod_report_et"]),
        watchdog_poll_interval_s=float(svc["watchdog_poll_interval_s"]),
        watchdog_reconnect_initial_s=float(svc["watchdog_reconnect_initial_s"]),
        watchdog_reconnect_cap_s=float(svc["watchdog_reconnect_cap_s"]),
        state_db=str(svc["state_db"]),
        kill_file=str(svc["kill_file"]),
        report_dir=str(svc["report_dir"]),
    )
