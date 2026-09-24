#!/usr/bin/env python3
"""
bot.options.config — Load and validate rules_options.json into OptionsConfig(s).

Reads rules_options.json (CFG-01 — single source of truth for every options
strategy parameter), validates it with jsonschema, and maps it to flat
OptionsConfig dataclass(es). The file may be either shape:
  - legacy flat (today's format, no top-level "strategies" key) — always
    supported (D-06), wrapped into a one-strategy book via _wrap_legacy so
    there is ONE mapping path, not two that can drift.
  - strategies-shape: a top-level "strategies" array plus shared
    "risk"/"execution"/"service" blocks (D-01).

Raises ConfigError on missing file, malformed JSON, schema violation, or a
schema-valid-but-semantically-wrong shape (duplicate names, unimplemented
structure, missing IV-gate keys for a credit structure, etc. — D-11). The
caller (bot/main.py, bot/options/service.py) handles process exit; this
module never calls sys.exit.

ConfigError is deliberately re-used from bot.config.loader: one config
exception type for the whole bot, so callers need a single except clause.

Exports: load_options_config, load_options_book, legacy_view, OptionsConfig, OptionsBook
"""
import copy
import json
import os
from dataclasses import dataclass
from typing import Optional

import jsonschema

from bot.config.loader import ConfigError
from bot.options.schema import OPTIONS_SCHEMA, STRATEGIES_SCHEMA


# ============================================================
# Structure constants (CFG-01, D1, D-11)
# ============================================================

# Only defined-risk structures the strategy code actually implements may run.
# Mirrors _IMPLEMENTED_EXIT_MODELS in bot/config/loader.py: the schema enum
# rejects unknown garbage early, but passing schema validation alone must NEVER
# be sufficient — a future enum addition (e.g. "strangle", which needs a
# buying-power model that does not exist) has to be opted into here explicitly.
# The loader FAILS CLOSED for any value outside this set.
_IMPLEMENTED_STRUCTURES = ("iron_condor", "put_credit_spread", "bull_call_spread")

# Mirrors bot.state.store.DEFAULT_DB_PATH ("data/bot_state.db") — asserted
# equal to it by a test rather than imported (bot/options/ must never import
# bot.state.store, D-17). Used when a legacy file (or a strategies-shape
# file's service block) omits service.equity_state_db.
_DEFAULT_EQUITY_STATE_DB = "data/bot_state.db"

# The ONE relocation table walked by both _wrap_legacy (legacy -> strategies
# shape) and legacy_view (strategies -> legacy shape), so there is a single
# mapping that cannot drift into two independently-maintained ones (research
# Open Question 3). Rows are (per-strategy block, key, shared top-level block).
_RELOCATED = (
    ("sizing", "sizing_equity_usd", "risk"),
    ("sizing", "max_bp_usage_pct", "risk"),
    ("sizing", "daily_loss_limit_pct", "risk"),
    ("manage", "manage_interval_min", "service"),
)


# ============================================================
# OptionsConfig Dataclass
# ============================================================

@dataclass
class OptionsConfig:
    """
    Typed, flat representation of ONE strategy's validated config.

    All nested JSON groups (entry, structure, sizing, manage, execution,
    service) are flattened into clearly named fields so callers never navigate
    raw dicts. Field names are the JSON leaf names verbatim, with a single
    rename: structure.type -> structure_type (`type` shadows the builtin, and
    `structure` is already a pick_strikes parameter name).

    A strategies-shape rules file (D-01) produces one of these per strategy,
    with the shared risk/service globals (sizing_equity_usd, max_bp_usage_pct,
    daily_loss_limit_pct, manage_interval_min, equity_state_db) copied into
    EVERY strategy's config. A legacy flat file produces exactly one, with
    the same shared globals read from its own sizing/manage blocks (D-06).

    Credit-only fields (ivr_min, fear_drop_pct, fear_ivr_min, short_delta,
    min_credit_to_width, profit_target_pct_of_credit, stop_loss_credit_multiple)
    and debit-only fields (long_delta, max_debit_to_width,
    profit_target_pct_of_max) are Optional — None on the structure they do not
    apply to (D-09).
    """

    # ---- top level ----
    strategy_name: str                     # strategy_name (legacy) / strategies[i].name
    universe: tuple                        # universe (tuple: config is immutable)

    # ---- entry ----
    ivr_min: Optional[float]               # entry.ivr_min (credit only)
    ivp_min: Optional[float]               # entry.ivp_min (None = IVP dual gate off)
    fear_drop_pct: Optional[float]         # entry.fear_drop_pct (credit only)
    fear_ivr_min: Optional[float]          # entry.fear_ivr_min (credit only)
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
    short_delta: Optional[float]           # structure.short_delta (credit only)
    wing_width_pct_of_underlying: float    # structure.wing_width_pct_of_underlying
    min_wing_width_usd: float               # structure.min_wing_width_usd (dollar floor on wing width)
    min_credit_to_width: Optional[float]   # structure.min_credit_to_width (credit only)

    # ---- sizing ----
    sizing_equity_usd: float               # risk.sizing_equity_usd (legacy: sizing.sizing_equity_usd) — global (D-02)
    max_risk_per_trade_pct: float          # sizing.max_risk_per_trade_pct — per-strategy (D-03)
    max_bp_usage_pct: float                # risk.max_bp_usage_pct (legacy: sizing.max_bp_usage_pct) — global (D-02)
    max_concurrent_positions: int          # sizing.max_concurrent_positions — per-strategy (D-03)
    max_new_positions_per_day: int         # sizing.max_new_positions_per_day — per-strategy (D-03)
    daily_loss_limit_pct: float            # risk.daily_loss_limit_pct (legacy: sizing.daily_loss_limit_pct) — global (D-02)

    # ---- manage ----
    manage_interval_min: int               # service.manage_interval_min (legacy: manage.manage_interval_min) — global (D-02)
    profit_target_pct_of_credit: Optional[float]  # manage.profit_target_pct_of_credit (credit only)
    manage_dte: Optional[int]              # manage.manage_dte (credit: required int; debit: optional, None = no DTE exit)
    stop_loss_credit_multiple: Optional[float]  # manage.stop_loss_credit_multiple (credit only; None = off)
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

    # ---- multi-strategy additions (D-09), appended last, no dataclass default:
    # both loader paths always pass every field explicitly. ----
    name: Optional[str]                    # strategies[i].name (== strategy_name)
    universe_source: Optional[str]         # strategies[i].universe_source (None when universe is set)
    long_delta: Optional[float]            # structure.long_delta (debit only)
    max_debit_to_width: Optional[float]    # structure.max_debit_to_width (debit only)
    profit_target_pct_of_max: Optional[float]  # manage.profit_target_pct_of_max (debit only)
    equity_state_db: Optional[str]         # service.equity_state_db (default: mirrors bot.state.store.DEFAULT_DB_PATH)


@dataclass(frozen=True)
class OptionsBook:
    """
    Every strategy in a rules_options.json file, flattened, plus the shared
    top-level blocks (D-08). `strategies` is in file/config order — a legacy
    file always produces a 1-tuple. Only bot/options/service.py's composition
    root uses this; every other consumer keeps using load_options_config.
    """

    strategies: tuple          # tuple[OptionsConfig, ...], config order
    risk: dict                 # raw risk block (sizing_equity_usd, max_bp_usage_pct, daily_loss_limit_pct)
    execution: dict            # raw execution block (shared, unchanged shape)
    service: dict              # raw service block (manage_interval_min, equity_state_db, + legacy service keys)


# ============================================================
# Private helpers
# ============================================================

def _read_json(path: str) -> dict:
    """Read and parse a JSON config file; raise ConfigError on I/O or syntax errors."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise ConfigError(f"rules_options.json not found: {path}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"rules_options.json is not valid JSON: {exc}") from exc


def _validate(data: dict, schema: dict) -> None:
    """Validate data against a jsonschema schema; raise ConfigError with field context."""
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        # Include both the failing field path and the human-readable message
        field_path = " -> ".join(str(p) for p in exc.absolute_path) if exc.absolute_path else exc.validator_value
        raise ConfigError(
            f"rules_options.json schema validation failed: {exc.message} "
            f"(path: {field_path if field_path else exc.json_path})"
        ) from exc


def _opt(value, cast):
    """Return None when value is None, else cast(value)."""
    return None if value is None else cast(value)


def _wrap_legacy(data: dict) -> dict:
    """
    Reshape a validated legacy flat dict into the strategies shape (D-06).

    Legacy files are WRAPPED, not special-cased: they flow through the exact
    same _check_strategy/_flatten path as a strategies-shape file, so there
    is one mapping, not two that can drift (research Open Question 3). Never
    mutates the input.
    """
    data = copy.deepcopy(data)
    blocks = {"sizing": dict(data["sizing"]), "manage": dict(data["manage"])}
    risk: dict = {}
    service = dict(data["service"])
    for block, key, dest in _RELOCATED:
        value = blocks[block].pop(key)
        (risk if dest == "risk" else service)[key] = value

    strategy = {
        "name": data["strategy_name"],
        "universe": data["universe"],
        "entry": data["entry"],
        "structure": data["structure"],
        "sizing": blocks["sizing"],
        "manage": blocks["manage"],
    }
    return {
        "strategies": [strategy],
        "risk": risk,
        "execution": data["execution"],
        "service": service,
    }


def _check_strategy(s: dict) -> None:
    """
    Fail-closed guard for the implemented-structure set (T-11-05).

    Schema validation already rejected unknown strings via enum. This guard
    is defense-in-depth for any structure added to the enum before
    pick_strikes/size_position learn to build it. Read at call time (tests
    monkeypatch _IMPLEMENTED_STRUCTURES) — a schema-valid but unimplemented
    structure still fails closed.
    """
    structure_type = str(s["structure"]["type"])
    if structure_type not in _IMPLEMENTED_STRUCTURES:
        raise ConfigError(
            f"structure.type '{structure_type}' is not implemented; "
            f"expected one of: {', '.join(_IMPLEMENTED_STRUCTURES)}"
        )


def _flatten(s: dict, risk: dict, execution: dict, service: dict) -> OptionsConfig:
    """Build one flat OptionsConfig from a strategy block plus the shared blocks."""
    en = s.get("entry", {})
    st = s.get("structure", {})
    sz = s.get("sizing", {})
    mg = s.get("manage", {})
    name = str(s["name"])

    return OptionsConfig(
        # top level
        strategy_name=name,
        universe=tuple(str(c) for c in s.get("universe", ())),
        # entry
        ivr_min=_opt(en.get("ivr_min"), float),
        ivp_min=_opt(en.get("ivp_min"), float),
        fear_drop_pct=_opt(en.get("fear_drop_pct"), float),
        fear_ivr_min=_opt(en.get("fear_ivr_min"), float),
        max_spread_pct_of_mid=float(en["max_spread_pct_of_mid"]),
        max_spread_abs_usd=float(en["max_spread_abs_usd"]),
        min_open_interest=int(en["min_open_interest"]),
        target_dte=int(en["target_dte"]),
        min_dte=int(en["min_dte"]),
        max_dte=int(en["max_dte"]),
        prefer_monthly=bool(en["prefer_monthly"]),
        entry_scan_et=str(en["entry_scan_et"]),
        second_entry_scan_et=_opt(en.get("second_entry_scan_et"), str),
        # structure
        structure_type=str(st["type"]),
        short_delta=_opt(st.get("short_delta"), float),
        wing_width_pct_of_underlying=float(st["wing_width_pct_of_underlying"]),
        min_wing_width_usd=float(st["min_wing_width_usd"]),
        min_credit_to_width=_opt(st.get("min_credit_to_width"), float),
        # sizing (per-strategy sizing + relocated global risk block)
        sizing_equity_usd=float(risk["sizing_equity_usd"]),
        max_risk_per_trade_pct=float(sz["max_risk_per_trade_pct"]),
        max_bp_usage_pct=float(risk["max_bp_usage_pct"]),
        max_concurrent_positions=int(sz["max_concurrent_positions"]),
        max_new_positions_per_day=int(sz["max_new_positions_per_day"]),
        daily_loss_limit_pct=float(risk["daily_loss_limit_pct"]),
        # manage (per-strategy manage + relocated global service key)
        manage_interval_min=int(service["manage_interval_min"]),
        profit_target_pct_of_credit=_opt(mg.get("profit_target_pct_of_credit"), float),
        manage_dte=_opt(mg.get("manage_dte"), int),
        stop_loss_credit_multiple=_opt(mg.get("stop_loss_credit_multiple"), float),
        assignment_guard_dte=int(mg["assignment_guard_dte"]),
        # execution (shared)
        limit_buffer_usd=float(execution["limit_buffer_usd"]),
        poll_interval_s=float(execution["poll_interval_s"]),
        ttl_s=float(execution["ttl_s"]),
        escalation_step_usd=float(execution["escalation_step_usd"]),
        max_retries=int(execution["max_retries"]),
        # service (shared)
        eod_report_et=str(service["eod_report_et"]),
        watchdog_poll_interval_s=float(service["watchdog_poll_interval_s"]),
        watchdog_reconnect_initial_s=float(service["watchdog_reconnect_initial_s"]),
        watchdog_reconnect_cap_s=float(service["watchdog_reconnect_cap_s"]),
        state_db=str(service["state_db"]),
        kill_file=str(service["kill_file"]),
        report_dir=str(service["report_dir"]),
        # multi-strategy additions
        name=name,
        universe_source=s.get("universe_source"),
        long_delta=_opt(st.get("long_delta"), float),
        max_debit_to_width=_opt(st.get("max_debit_to_width"), float),
        profit_target_pct_of_max=_opt(mg.get("profit_target_pct_of_max"), float),
        equity_state_db=str(service.get("equity_state_db", _DEFAULT_EQUITY_STATE_DB)),
    )


# ============================================================
# Loaders
# ============================================================

def load_options_book(path: str = "rules_options.json") -> OptionsBook:
    """
    Load and validate rules_options.json (either shape), returning every
    strategy as a flat OptionsConfig plus the shared risk/execution/service
    blocks (D-08).

    Raises ConfigError on:
      - missing file (see _read_json)
      - malformed JSON (see _read_json)
      - jsonschema violation of STRATEGIES_SCHEMA (strategies-shape) or
        OPTIONS_SCHEMA (legacy shape) — includes field context
      - a structure.type that is schema-valid but not implemented (_check_strategy)

    Legacy files (no top-level "strategies" key) are wrapped via _wrap_legacy
    into a one-strategy book and flow through the identical mapping path
    (D-06) — never a second, independently-maintained mapping.
    """
    data = _read_json(path)

    if "strategies" in data:
        _validate(data, STRATEGIES_SCHEMA)
    else:
        _validate(data, OPTIONS_SCHEMA)
        data = _wrap_legacy(data)

    for s in data["strategies"]:
        _check_strategy(s)

    return OptionsBook(
        strategies=tuple(
            _flatten(s, data["risk"], data["execution"], data["service"])
            for s in data["strategies"]
        ),
        risk=data["risk"],
        execution=data["execution"],
        service=data["service"],
    )


def load_options_config(path: str = "rules_options.json", strategy: Optional[str] = None) -> OptionsConfig:
    """
    Load rules_options.json and return ONE flat OptionsConfig (D-07).

    strategy=None selects the first strategy in the file (today's only
    strategy for a legacy file). An unknown name raises ConfigError. Name,
    positional path parameter, and return type are unchanged from before the
    multi-strategy split (MSO-02) — every existing caller (backtester,
    scripts/uat_options_probe.py, bot/options/service.py, all tests) keeps
    working unmodified.

    Per PATTERNS.md "Raise, don't exit": raises ConfigError; caller handles exit.
    """
    book = load_options_book(path)

    if strategy is None:
        return book.strategies[0]

    for cfg in book.strategies:
        if cfg.name == strategy:
            return cfg

    available = ", ".join(c.name for c in book.strategies)
    raise ConfigError(f"strategy '{strategy}' not found in {path}; available: {available}")


def legacy_view(raw: dict, name: Optional[str] = None) -> dict:
    """
    Project one strategy of a strategies-shape dict to the legacy flat shape
    (D-10). Identity (deep copy) for a legacy-shape input. Exact inverse of
    _wrap_legacy — driven by the same _RELOCATED table so there is one
    mapping, not two that can drift.

    name=None selects the first strategy (or, for a legacy input, means "no
    name check"). Pure: never mutates raw. Raises ConfigError only (never
    sys.exit) — the caller decides how to fail.
    """
    if "strategies" not in raw:
        if name is not None and name != raw.get("strategy_name"):
            raise ConfigError(
                f"strategy '{name}' not found in rules file; "
                f"available: {raw.get('strategy_name')}"
            )
        return copy.deepcopy(raw)

    strategies = raw["strategies"]
    names = [s["name"] for s in strategies]
    if name is None:
        s = strategies[0]
    else:
        matches = [s for s in strategies if s["name"] == name]
        if not matches:
            raise ConfigError(
                f"strategy '{name}' not found in rules file; "
                f"available: {', '.join(names)}"
            )
        s = matches[0]

    s = copy.deepcopy(s)
    risk = copy.deepcopy(raw["risk"])
    service = copy.deepcopy(raw["service"])
    sizing = dict(s.get("sizing", {}))
    manage = dict(s.get("manage", {}))
    for block, key, dest in _RELOCATED:
        src = risk if dest == "risk" else service
        value = src.pop(key)
        (sizing if block == "sizing" else manage)[key] = value

    legacy: dict = {"strategy_name": s["name"]}
    if "universe" in s:
        legacy["universe"] = list(s["universe"])
    else:
        legacy["universe_source"] = s.get("universe_source")
    legacy["entry"] = s["entry"]
    legacy["structure"] = s["structure"]
    legacy["sizing"] = sizing
    legacy["manage"] = manage
    legacy["execution"] = copy.deepcopy(raw["execution"])
    legacy["service"] = service
    return legacy
