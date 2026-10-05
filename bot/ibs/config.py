#!/usr/bin/env python3
"""
bot.ibs.config — Load and validate rules_ibs.json into a frozen IbsConfig (IBS-01).

rules_ibs.json is the single source of truth for every IBS strategy number; no
strategy value is defaulted in Python. Raises ConfigError (re-used from
bot.config.loader: one config exception type for the whole bot) on a missing
file, bad JSON, schema violation or semantic error. Never terminates the process.

Exports: IbsConfig, load_ibs_config
"""
import json
import os
from dataclasses import dataclass

import jsonschema

from bot.config.loader import ConfigError
from bot.ibs.schema import IBS_SCHEMA

# D-13: paths owned by the equity / options bots — the IBS bot must never share them.
_FOREIGN_STATE_DBS = ("data/bot_state.db", "data/options_state.db")
_FOREIGN_KILL_FILES = (".bot_kill", ".bot_kill_options")
_FOREIGN_REPORT_DIRS = ("reports", "reports/options")
_FOREIGN_LOG_FILES = ("bot.log",)

# NYSE early close is 13:00 ET (market-calendar fact, not a strategy knob).
_EARLIEST_CLOSE_MIN = 13 * 60


@dataclass(frozen=True)
class IbsConfig:
    strategy_name: str
    universe: tuple
    ibs_entry_max: float
    ibs_exit_min: float
    max_hold_trading_days: int
    max_snapshot_age_s: float
    sizing_equity_usd: float
    position_pct_of_equity: float
    max_concurrent_positions: int
    entry_limit_buffer_usd: float
    exit_limit_buffer_usd: float
    order_ttl_seconds: float
    poll_interval_seconds: float
    escalation_step_usd: float
    max_reprices: int
    executor_margin_s: float
    arm_time_et: str
    decision_before_close_min: int
    hard_cancel_before_close_min: int
    eod_report_after_close_min: int
    misfire_grace_s: int
    decision_read_retries: int
    decision_read_retry_s: float
    watchdog_poll_interval_s: float
    watchdog_reconnect_initial_s: float
    watchdog_reconnect_cap_s: float
    state_db: str
    kill_file: str
    report_dir: str
    log_file: str

    @property
    def worst_case_order_s(self) -> float:
        """Longest one order can be worked by LegExecutor: every attempt waits
        one TTL plus at most one poll."""
        return (self.order_ttl_seconds + self.poll_interval_seconds) * (self.max_reprices + 1)


def _read_json(path: str) -> dict:
    """Read and parse a JSON config file; raise ConfigError on I/O or syntax errors."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise ConfigError(f"rules_ibs.json not found: {path}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"rules_ibs.json is not valid JSON: {exc}") from exc


def _validate(data: dict, schema: dict) -> None:
    """Validate data against a jsonschema schema; raise ConfigError with field context."""
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        field_path = " -> ".join(str(p) for p in exc.absolute_path) if exc.absolute_path else exc.validator_value
        raise ConfigError(
            f"rules_ibs.json schema validation failed: {exc.message} "
            f"(path: {field_path if field_path else exc.json_path})"
        ) from exc


def _abs(paths):
    return {os.path.abspath(p) for p in paths}


def _check(cfg: IbsConfig) -> None:
    """Cross-field fail-closed checks; every message names the offending key."""
    if not 0 < cfg.ibs_entry_max < cfg.ibs_exit_min < 1:
        raise ConfigError("signal: require 0 < ibs_entry_max < ibs_exit_min < 1")
    if cfg.position_pct_of_equity * cfg.max_concurrent_positions > 100:
        raise ConfigError(
            "risk: position_pct_of_equity x max_concurrent_positions must stay unlevered (OD-1, <= 100)")
    if not cfg.decision_before_close_min > cfg.hard_cancel_before_close_min >= 1:
        raise ConfigError(
            "service: require decision_before_close_min > hard_cancel_before_close_min >= 1")
    if cfg.poll_interval_seconds >= cfg.order_ttl_seconds:
        raise ConfigError("execution: poll_interval_seconds must be < order_ttl_seconds")
    window_s = (cfg.decision_before_close_min - cfg.hard_cancel_before_close_min) * 60
    if cfg.executor_margin_s >= window_s:
        raise ConfigError(
            f"execution: executor_margin_s must be < the decision -> hard-cancel window {window_s}s")
    if cfg.worst_case_order_s >= window_s - cfg.executor_margin_s:
        raise ConfigError(
            f"execution: worst-case order time {cfg.worst_case_order_s}s does not fit the "
            f"decision window {window_s}s less executor_margin_s "
            f"(order_ttl_seconds/poll_interval_seconds/max_reprices)")
    retry_s = cfg.decision_read_retries * cfg.decision_read_retry_s
    if cfg.worst_case_order_s + retry_s >= window_s - cfg.executor_margin_s:
        raise ConfigError(
            f"service: decision_read_retries x decision_read_retry_s ({retry_s}s) leaves no "
            f"room for one worst-case order in the decision window {window_s}s")
    hh, mm = int(cfg.arm_time_et[:2]), int(cfg.arm_time_et[3:])
    if hh > 23 or mm > 59:
        raise ConfigError(f"service: arm_time_et {cfg.arm_time_et!r} is not a valid HH:MM")
    if hh * 60 + mm >= _EARLIEST_CLOSE_MIN - cfg.decision_before_close_min:
        raise ConfigError(
            f"service: arm_time_et {cfg.arm_time_et!r} is not before the earliest half-day decision time")
    if cfg.watchdog_reconnect_initial_s > cfg.watchdog_reconnect_cap_s:
        raise ConfigError("service: watchdog_reconnect_initial_s must be <= watchdog_reconnect_cap_s")
    if (os.path.basename(cfg.log_file) != cfg.log_file or not cfg.log_file.endswith(".log")
            or cfg.log_file in _FOREIGN_LOG_FILES):
        raise ConfigError(
            f"service: log_file {cfg.log_file!r} must be a bare *.log name distinct from the other bots'")
    for key, val, foreign in (
        ("state_db", cfg.state_db, _FOREIGN_STATE_DBS),
        ("kill_file", cfg.kill_file, _FOREIGN_KILL_FILES),
        ("report_dir", cfg.report_dir, _FOREIGN_REPORT_DIRS),
    ):
        if os.path.abspath(val) in _abs(foreign):
            raise ConfigError(f"service: {key} {val!r} collides with the equity/options bot (D-13)")


def load_ibs_config(path: str = "rules_ibs.json") -> IbsConfig:
    """Load, validate and cross-check rules_ibs.json; raise ConfigError on any problem."""
    data = _read_json(path)
    _validate(data, IBS_SCHEMA)
    s, r, e, v = data["signal"], data["risk"], data["execution"], data["service"]
    cfg = IbsConfig(
        strategy_name=str(data["strategy_name"]),
        universe=tuple(str(c) for c in data["universe"]),
        ibs_entry_max=float(s["ibs_entry_max"]),
        ibs_exit_min=float(s["ibs_exit_min"]),
        max_hold_trading_days=int(s["max_hold_trading_days"]),
        max_snapshot_age_s=float(s["max_snapshot_age_s"]),
        sizing_equity_usd=float(r["sizing_equity_usd"]),
        position_pct_of_equity=float(r["position_pct_of_equity"]),
        max_concurrent_positions=int(r["max_concurrent_positions"]),
        entry_limit_buffer_usd=float(e["entry_limit_buffer_usd"]),
        exit_limit_buffer_usd=float(e["exit_limit_buffer_usd"]),
        order_ttl_seconds=float(e["order_ttl_seconds"]),
        poll_interval_seconds=float(e["poll_interval_seconds"]),
        escalation_step_usd=float(e["escalation_step_usd"]),
        max_reprices=int(e["max_reprices"]),
        executor_margin_s=float(e["executor_margin_s"]),
        arm_time_et=str(v["arm_time_et"]),
        decision_before_close_min=int(v["decision_before_close_min"]),
        hard_cancel_before_close_min=int(v["hard_cancel_before_close_min"]),
        eod_report_after_close_min=int(v["eod_report_after_close_min"]),
        misfire_grace_s=int(v["misfire_grace_s"]),
        decision_read_retries=int(v["decision_read_retries"]),
        decision_read_retry_s=float(v["decision_read_retry_s"]),
        watchdog_poll_interval_s=float(v["watchdog_poll_interval_s"]),
        watchdog_reconnect_initial_s=float(v["watchdog_reconnect_initial_s"]),
        watchdog_reconnect_cap_s=float(v["watchdog_reconnect_cap_s"]),
        state_db=str(v["state_db"]),
        kill_file=str(v["kill_file"]),
        report_dir=str(v["report_dir"]),
        log_file=str(v["log_file"]),
    )
    _check(cfg)
    return cfg
