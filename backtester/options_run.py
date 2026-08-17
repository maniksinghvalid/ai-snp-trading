#!/usr/bin/env python3
"""
backtester.options_run — CLI entry point for the offline options backtester (D-01, OBT-01).

Composition root only: loads rules_options.json via the SAME loader bot/main.py uses
(load_options_config, CFG-01), applies operator `--set KEY=VALUE` overrides to an
IN-MEMORY copy only (rules_options.json on disk is never rewritten — D-15), builds one
OptionChainSource per symbol against real Massive data, and wires
OptionsBacktestEngine -> backtester.options.report.write_options_report end to end for
every NYSE trading day in [--start, --end].

Never constructs a live broker gateway, the options execution/order layer, or a
StateStore anywhere in this module — a backtest run is provably broker-free (mirrors
backtester/run.py's stated invariant). The options backtester keeps its state in memory
only; there is no live options DB for a run to collide with.

Warm-up handling (D-10): `--iv-warmup-days` NYSE trading days immediately before
--start are used ONLY to prime each underlying's ATM-IV series (`_prime_iv_series`,
mirroring OptionsBacktestEngine.run_day's own IV-update loop) so `iv_rank` has already
cleared its IV_RANK_MIN_OBS warm-up by day 1 of the replay window — those warm-up days
are never fed through engine.run(), so they can never open a position. The engine's own
decision-day loop (`engine.run(trading_days(start, end))`) covers only [--start, --end].

Exports: build_arg_parser, apply_overrides, main
"""
import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

from bot.config.loader import ConfigError
from bot.options.config import load_options_config

from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.options.data import OptionChainSource, trading_days
from backtester.options.engine import OptionsBacktestEngine
from backtester.options.report import write_options_report

_DATE_FMT = "%Y-%m-%d"


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI flags (CLAUDE.md dash-separated long-option convention)."""
    parser = argparse.ArgumentParser(
        prog="backtester.options_run",
        description="Replay tasty_credit_spreads against historical Massive option data.",
    )
    parser.add_argument("--rules", default="rules_options.json", help="Path to rules_options.json")
    parser.add_argument("--symbols", required=True, help="Comma-separated US.XXX codes, e.g. US.SPY,US.QQQ")
    parser.add_argument("--start", required=True, help="Replay start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Replay end date, YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="Run directory (default: backtester/results/options/<run-id>)")
    parser.add_argument(
        "--set", dest="set_args", action="append", default=[], metavar="KEY=VALUE",
        help="Override one rules_options.json leaf via a dotted path, e.g. entry.ivr_min=20 "
             "(repeatable; never rewrites rules_options.json on disk — D-15)",
    )
    parser.add_argument("--risk-free-rate", type=float, default=0.045)
    parser.add_argument("--slippage-usd", type=float, default=0.02)
    parser.add_argument("--commission-per-leg", type=float, default=0.65)
    parser.add_argument("--spread-pct", type=float, default=2.0)
    parser.add_argument("--iv-warmup-days", type=int, default=60)
    parser.add_argument(
        "--strike-band-pct", type=float, default=10.0,
        help="OTM-side strike band for the lazy per-decision-day fetch (default 10.0)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel contract-bar fetch workers (default 1 -- the free Massive tier "
             "is a hard 5 req/min cap, keep 1; a paid tier can use up to 8)",
    )
    parser.add_argument("--oi-source", choices=["volume", "neutral"], default="volume")
    parser.add_argument("--label", default=None, help="Free-text run label recorded in summary.json assumptions")
    return parser


def apply_overrides(raw: dict, set_args: list) -> dict:
    """Apply `--set KEY=VALUE` dotted-path overrides to an IN-MEMORY copy of `raw`.

    Splits each arg on the FIRST `=`, walks the dotted path into a deep copy of `raw`,
    and sets the leaf via `json.loads(value)` with a plain-string fallback. Raises
    ValueError naming the offending argument when the path does not already exist in
    the file — a typo must fail loudly, never silently add a key the schema ignores.
    Never mutates `raw` itself (D-15 — rules_options.json on disk is never rewritten).
    """
    result = json.loads(json.dumps(raw))
    for arg in set_args:
        if "=" not in arg:
            raise ValueError(f"--set {arg!r} must be KEY=VALUE")
        key, value = arg.split("=", 1)
        parts = key.split(".")
        node = result
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise ValueError(f"--set {arg!r}: path {key!r} does not exist in the config")
            node = node[part]
        leaf = parts[-1]
        if not isinstance(node, dict) or leaf not in node:
            raise ValueError(f"--set {arg!r}: path {key!r} does not exist in the config")
        try:
            node[leaf] = json.loads(value)
        except json.JSONDecodeError:
            node[leaf] = value
    return result


def _parse_date(label: str, value: str) -> datetime:
    """Parse a YYYY-MM-DD CLI arg; raise ValueError (not a stack trace) on bad input."""
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        raise ValueError(f"--{label} must be YYYY-MM-DD, got {value!r}") from None


def _parse_symbols(value: str) -> list:
    """Comma-split --symbols; reject an empty result or a non-US.XXX code (V5)."""
    symbols = [s.strip() for s in value.split(",") if s.strip()]
    if not symbols:
        raise ValueError("--symbols must be a non-empty comma-separated list")
    bad = [s for s in symbols if not s.startswith("US.")]
    if bad:
        raise ValueError(f"--symbols must be US.XXX codes, got: {', '.join(bad)}")
    return symbols


def _validate_numeric_flags(args) -> None:
    if args.risk_free_rate < 0:
        raise ValueError("--risk-free-rate must be >= 0")
    if args.slippage_usd < 0:
        raise ValueError("--slippage-usd must be >= 0")
    if args.commission_per_leg < 0:
        raise ValueError("--commission-per-leg must be >= 0")
    if args.spread_pct <= 0:
        raise ValueError("--spread-pct must be > 0")
    if args.iv_warmup_days < 1:
        raise ValueError("--iv-warmup-days must be >= 1")
    if args.strike_band_pct <= 0:
        raise ValueError("--strike-band-pct must be > 0")
    if args.workers < 1:
        raise ValueError("--workers must be >= 1")


def _run_id(start: str, end: str, symbols: list) -> str:
    slug = "-".join(s.replace(".", "") for s in symbols)
    return f"{start}_{end}_{slug}_{uuid.uuid4().hex[:8]}"


def _warmup_start(start: str, warmup_days: int) -> str:
    """First NYSE trading day such that exactly `warmup_days` NYSE trading days
    fall in [warmup_start, start) — i.e. strictly before --start."""
    anchor = date.fromisoformat(start)
    lookback_start = (anchor - timedelta(days=int(warmup_days * 2.5) + 15)).isoformat()
    day_before = (anchor - timedelta(days=1)).isoformat()
    days = trading_days(lookback_start, day_before)
    if len(days) < warmup_days:
        raise ValueError(
            f"--iv-warmup-days {warmup_days} exceeds available NYSE trading history before {start}"
        )
    return days[-warmup_days]


def _prime_iv_series(engine, warmup_days: list) -> None:
    """Feed each underlying's ATM-IV series for warm-up days ONLY, via
    `engine.update_iv()` — the single source of truth for the IV-update half
    of a decision day (T-09-13; no duplicated copy of that loop here). Never
    runs manage/entry, so a warm-up day can never open a position (module
    docstring).
    """
    for day in warmup_days:
        engine.update_iv(day)


def main(argv=None) -> int:
    """Parse args, validate, apply overrides, load config, replay, write the report.

    Returns a process-style exit code (0 success, 1 on any validation/config/network
    failure) rather than calling sys.exit directly, so it is trivially testable.

    Ordering (WR-05): every argument, the effective config (schema-validated
    via a throwaway temp file), and the Massive API key are all checked
    BEFORE the run directory is created — a rejected `--set` override or a
    missing API key must never leave a half-populated
    `backtester/results/options/<run-id>/` behind. Chain load, replay and
    report writing are wrapped in one exception boundary so a malformed
    cached CSV or any other unexpected failure exits 1 with `[ERROR]` rather
    than a raw stack trace.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # V5 input validation -- BEFORE any config load, filesystem write or network call.
    try:
        start_dt = _parse_date("start", args.start)
        end_dt = _parse_date("end", args.end)
        if start_dt > end_dt:
            raise ValueError(f"--start ({args.start}) must be <= --end ({args.end})")
        symbols = _parse_symbols(args.symbols)
        _validate_numeric_flags(args)

        with open(args.rules, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        effective = apply_overrides(raw, args.set_args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # Schema-validate the EFFECTIVE config through a throwaway temp file --
    # BEFORE any run dir exists (WR-05) -- so a rejected override never
    # leaves a half-populated directory behind.
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(effective, f)
            cfg = load_options_config(tmp_path)
        finally:
            os.unlink(tmp_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        massive = MassiveDataSource(load_massive_api_key())
    except MassiveApiError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # Config + API key both succeeded -- only now is it safe to create the run dir.
    run_id = _run_id(args.start, args.end, symbols)
    out_dir = args.out or os.path.join("backtester", "results", "options", run_id)
    os.makedirs(out_dir, exist_ok=True)
    config_path = os.path.join(out_dir, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(effective, f, indent=2)

    try:
        warmup_start = _warmup_start(args.start, args.iv_warmup_days)
        chains = {}
        for sym in symbols:
            root = sym.split(".", 1)[1] if "." in sym else sym
            chains[sym] = OptionChainSource(
                massive, root, warmup_start, args.end, workers=args.workers
            ).load(cfg.min_dte, cfg.max_dte, cfg.prefer_monthly)

        engine = OptionsBacktestEngine(
            cfg, chains, r=args.risk_free_rate, slippage_usd=args.slippage_usd,
            commission_per_leg=args.commission_per_leg, spread_pct=args.spread_pct,
            oi_source=args.oi_source, strike_band_pct=args.strike_band_pct,
        )

        warmup_days = trading_days(warmup_start, args.start)[:-1]
        _prime_iv_series(engine, warmup_days)
        decision_days = trading_days(args.start, args.end)
        engine.run(decision_days)
        engine.close_open_at_end(decision_days[-1])  # CR-01: settle every residual open position

        for sym, chain in chains.items():
            print(f"[fetch_stats] {sym}: {chain.fetch_stats}", file=sys.stderr)

        end_of_window_pnl_usd = sum(
            t["pnl_usd"] for t in engine.trade_log if t["exit_reason"] == "end_of_window"
        )
        metrics = write_options_report(
            engine.trade_log, out_dir, starting_capital=cfg.sizing_equity_usd,
            start=args.start, end=args.end,
            extra_assumptions={
                "rules_json": args.rules,
                "label": args.label,
                "risk_free_rate": args.risk_free_rate,
                "slippage_usd": args.slippage_usd,
                "commission_per_leg_usd": args.commission_per_leg,
                "spread_pct": args.spread_pct,
                "iv_warmup_days": args.iv_warmup_days,
                "strike_band_pct": args.strike_band_pct,
                "workers": args.workers,
                "oi_source": args.oi_source,
                "warmup_start": warmup_start,
                "run_id": run_id,
                "open_positions_at_end": engine.open_positions_at_end,
                "end_of_window_pnl_usd": end_of_window_pnl_usd,
                "fetch_stats": {sym: chain.fetch_stats for sym, chain in chains.items()},
            },
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(metrics, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
