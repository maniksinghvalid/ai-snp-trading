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
    partial_profit_trigger_r: float   # exit.partial_profit_trigger_R
    partial_profit_fraction: float    # exit.partial_profit_fraction (~0.3333)
    breakeven_trigger_r: float        # exit.breakeven_trigger_R

    # ---- risk ----
    max_risk_per_trade_pct: float     # risk.max_risk_per_trade_pct
    max_position_size_pct: int        # risk.max_position_size_pct_of_portfolio
    max_concurrent_positions: int     # risk.max_concurrent_positions
    max_trades_per_day: int           # risk.max_trades_per_day


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
        partial_profit_trigger_r=float(ex["partial_profit_trigger_R"]),
        partial_profit_fraction=float(ex["partial_profit_fraction"]),
        breakeven_trigger_r=float(ex["breakeven_trigger_R"]),
        # risk
        max_risk_per_trade_pct=float(rk["max_risk_per_trade_pct"]),
        max_position_size_pct=int(rk["max_position_size_pct_of_portfolio"]),
        max_concurrent_positions=int(rk["max_concurrent_positions"]),
        max_trades_per_day=int(rk["max_trades_per_day"]),
    )
