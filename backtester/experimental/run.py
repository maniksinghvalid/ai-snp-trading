#!/usr/bin/env python3
"""
backtester.experimental.run — CLI entry point for the external-strategy research
backtester (XSR-02, XSR-04).

Composition root only: never constructs a live broker gateway, an execution/order
layer, or any live-bot persistence layer anywhere in this module (mirrors
backtester/run.py and backtester/options_run.py's own stated invariant) -- a run
of this CLI is provably broker-free. This module's OWN additional invariant (T-10-03): cache-only. Refuses
to run against Massive whenever any required 5m/1d cache file is missing, unless
--allow-fetch is explicitly passed (never used by this phase's run matrix) -- the
Phase 9 warm-cache-pool background process owns the shared 5 req/min Massive
free-tier budget and this CLI must never compete with it for quota. As a second
line of defence, even --allow-fetch aside, the default (no --allow-fetch) run
constructs MassiveDataSource with an empty api_key, so an unexpected cache miss
401s instead of silently pulling data (T-10-03).

Two factory seams -- `_build_engine(...)` and `_load_regime_labels(csv_path)` --
reach backtester.experimental.{engine,indicators} through function-local imports
only, never a module-level import. Same discipline applies to the per-arm loop's
own use of backtester.experimental.{engine,strategies}: every reference is a
function-local import. This is the plan's parallelism contract: plan 10-02 lands
those modules in the SAME wave, and a module-level import would make this file
unimportable until it merges. Tests patch the underlying functions at their
SOURCE module (e.g. "backtester.experimental.engine.group_by_day") so the
function-local `from ... import ...` picks up the patched callable at call time,
or patch this module's own seam names (_build_engine, _load_regime_labels)
directly.

Exports: WINDOWS, MEGA24, COST_PROFILES, build_arg_parser, main
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from backtester.feed import _MASSIVE_DAILY_PAD_DAYS, _MASSIVE_TOD_PAD_DAYS, SimulatedBarFeed
from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.report import write_report

_DATE_FMT = "%Y-%m-%d"

# ============================================================
# Locked single-source-of-truth tables
# ============================================================

# Window C's start is 2024-09-02, NOT 2024-09-03 -- the padded Massive cache
# key derived from it is 2024-08-03 (see _required_cache_paths).
WINDOWS = {
    "A": ("2025-05-01", "2025-07-31"),
    "B": ("2025-04-01", "2025-04-30"),
    "C": ("2024-09-02", "2024-12-31"),
    "D": ("2025-08-01", "2026-07-31"),
    "E": ("2023-07-03", "2024-08-30"),
}

MEGA24 = [
    "US.AAPL", "US.MSFT", "US.NVDA", "US.AMD", "US.AVGO", "US.META", "US.GOOGL",
    "US.AMZN", "US.NFLX", "US.TSLA", "US.HD", "US.COST", "US.WMT", "US.JPM",
    "US.GS", "US.BAC", "US.XOM", "US.CVX", "US.CAT", "US.DE", "US.BA", "US.UNH",
    "US.LLY", "US.JNJ",
]

# {profile: (commission_per_share, slippage_per_share)}.
COST_PROFILES = {
    "base": (0.005, 0.03),
    "stress": (0.005, 0.05),
    "zero": (0.0, 0.0),
}

_MASSIVE_CACHE_DIR = "backtester/cache/massive"


# ============================================================
# Arg parsing
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    """CLI flags (CLAUDE.md dash-separated long-option convention)."""
    parser = argparse.ArgumentParser(
        prog="backtester.experimental.run",
        description="Cache-only replay CLI for the external-strategy research backtester.",
    )
    parser.add_argument("--window", choices=sorted(WINDOWS), default=None,
                        help="One of the five locked windows A-E")
    parser.add_argument("--start", default=None, help="Explicit start date, YYYY-MM-DD "
                        "(use instead of --window)")
    parser.add_argument("--end", default=None, help="Explicit end date, YYYY-MM-DD "
                        "(use instead of --window)")
    parser.add_argument("--symbols", default=None,
                        help=f"Comma-separated moomoo codes (default: the {len(MEGA24)}-symbol "
                             "MEGA24 universe)")
    parser.add_argument("--arms", default="backtester/experimental/arms.json",
                        help="Path to the frozen arm definitions")
    parser.add_argument("--only", default=None,
                        help="Comma-separated arm names to run (default: every arm)")
    parser.add_argument("--set", dest="set_args", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="Override one resolved arm param (repeatable)")
    parser.add_argument("--cost", choices=sorted(COST_PROFILES), default="base",
                        help="Commission/slippage profile")
    parser.add_argument("--stop-fill", choices=["close", "intrabar"], default="close",
                        help="Stop-fill mode passed to the Engine")
    parser.add_argument("--out", required=True, help="Run output root directory")
    parser.add_argument("--allow-fetch", action="store_true", default=False,
                        help="Permit a live Massive fetch on a cache miss (never used by this "
                             "phase's run matrix -- the shared free-tier budget belongs to "
                             "Phase 9's warm-cache-pool)")
    parser.add_argument("--tjl-regime", action="store_true", default=False,
                        help="Day-filter the existing TJL baseline instead of running arms")
    parser.add_argument("--baseline", default=None,
                        help="Existing TJL results dir (required with --tjl-regime)")
    parser.add_argument("--regime-csv", default="backtester/cache/SPY_1d_regime.csv",
                        help="SPY daily-bar CSV feeding the weekly regime gate")
    return parser


# ============================================================
# Window / cache-guard helpers
# ============================================================

def _resolve_window(args) -> tuple:
    """(start, end) from --window OR explicit --start/--end. Raises ValueError
    (never sys.exit) when neither or both are supplied."""
    has_window = args.window is not None
    has_explicit = args.start is not None or args.end is not None
    if has_window and has_explicit:
        raise ValueError("--window and --start/--end are mutually exclusive")
    if has_window:
        return WINDOWS[args.window]
    if args.start is None or args.end is None:
        raise ValueError("either --window or both --start and --end are required")
    return args.start, args.end


def _yf_symbol(code: str) -> str:
    """Moomoo code -> yfinance symbol (strip "US." prefix, matches feed.py's
    own SimulatedBarFeed._yf_symbol convention)."""
    return code.removeprefix("US.") if hasattr(code, "removeprefix") else code[len("US."):]


def _required_cache_paths(symbols: list, start: str, end: str, cache_dir: str) -> list:
    """The exact padded Massive cache paths `SimulatedBarFeed._load_massive` will
    read for `symbols` -- never hand-type a padded date literal (10-RESEARCH
    Pitfall 1); derive tod_start/daily_start the same way _load_massive does
    (calendar-day subtraction from `start`)."""
    start_dt = datetime.strptime(start, _DATE_FMT)
    tod_start = (start_dt - timedelta(days=_MASSIVE_TOD_PAD_DAYS)).date().isoformat()
    daily_start = (start_dt - timedelta(days=_MASSIVE_DAILY_PAD_DAYS)).date().isoformat()
    paths = []
    for code in symbols:
        sym = _yf_symbol(code)
        paths.append(os.path.join(cache_dir, f"{sym}_5m_{tod_start}_{end}.csv"))
        paths.append(os.path.join(cache_dir, f"{sym}_1d_{daily_start}_{end}.csv"))
    return paths


def _assert_cache(symbols: list, start: str, end: str, cache_dir: str, allow_fetch: bool) -> bool:
    """True when it is safe to proceed. When any required cache file is missing
    and --allow-fetch was not passed, prints every missing path to stderr and
    returns False BEFORE anything else (data source, feed, ...) is constructed."""
    if allow_fetch:
        return True
    missing = [p for p in _required_cache_paths(symbols, start, end, cache_dir) if not os.path.exists(p)]
    if not missing:
        return True
    print(
        "[ERROR] missing cache files (pass --allow-fetch to permit a live Massive fetch -- "
        "never used by this phase's run matrix):",
        file=sys.stderr,
    )
    for p in missing:
        print(f"  {p}", file=sys.stderr)
    return False


# ============================================================
# Arm resolution
# ============================================================

def _load_arms(arms_path: str, only: str) -> tuple:
    """(defaults, arms) from arms.json, `arms` optionally reduced by --only.
    Filtering preserves arms.json's own order (not the order names were typed
    in --only). Raises ValueError naming any unknown --only arm."""
    with open(arms_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    defaults = spec["defaults"]
    arms = spec["arms"]
    if only:
        names = [n.strip() for n in only.split(",") if n.strip()]
        known = {a["name"] for a in arms}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"--only names unknown arm(s): {', '.join(unknown)}")
        wanted = set(names)
        arms = [a for a in arms if a["name"] in wanted]
    return defaults, arms


def _parse_set_overrides(set_args: list) -> dict:
    """{key: value} from repeatable `--set KEY=VALUE`, value JSON-decoded with a
    plain-string fallback (mirrors backtester/options_run.py's own convention)."""
    overrides = {}
    for arg in set_args:
        if "=" not in arg:
            raise ValueError(f"--set {arg!r} must be KEY=VALUE")
        key, value = arg.split("=", 1)
        try:
            overrides[key] = json.loads(value)
        except json.JSONDecodeError:
            overrides[key] = value
    return overrides


def _resolve_params(defaults: dict, arm: dict, overrides: dict) -> dict:
    """arms.json defaults[strategy] overlaid with the arm's own params overlaid
    with `--set` overrides, tagged with strategy/arm for trade-row/report use."""
    strategy = arm["strategy"]
    params = {**defaults[strategy], **arm.get("params", {}), **overrides}
    params["strategy"] = strategy
    params["arm"] = arm["name"]
    return params


def _git_sha() -> str:
    """Best-effort `git rev-parse HEAD`; "unknown" (never a crash) when git is
    unavailable -- a long run's params.json must stay writable regardless."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


# ============================================================
# Factory seams (function-local imports only -- see module docstring)
# ============================================================

def _build_engine(frames: dict, signals: dict, params: dict, feed, slippage: float,
                  stop_fill: str, regime_fn=None):
    """Seam over backtester.experimental.engine.Engine -- function-local import
    so this module stays importable independently of plan 10-02's package
    (parallelism contract). test_run.py monkeypatches this name directly."""
    from backtester.experimental.engine import Engine
    return Engine(frames, signals, params, feed, slippage, stop_fill, regime_fn=regime_fn)


def _load_regime_labels(csv_path: str):
    """Seam over backtester.experimental.indicators.weekly_regime -- reads the
    SPY daily-bar CSV (backtester/cache/SPY_1d_regime.csv convention: a "Date"
    index column + lowercase OHLCV columns) and returns the weekly regime label
    Series. Function-local import (parallelism contract); test_run.py
    monkeypatches this name directly."""
    import pandas as pd
    from backtester.experimental.indicators import weekly_regime

    spy_daily = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    spy_daily.columns = [c.lower() for c in spy_daily.columns]
    return weekly_regime(spy_daily)


# ============================================================
# main
# ============================================================

def main(argv=None) -> int:
    """Parse args, resolve the window/arm list, enforce the cache guard, then
    (Task 2) run every selected arm against one shared feed, or (Task 3) run
    the --tjl-regime day-filter mode. Returns a process-style exit code (never
    sys.exit directly except under the __main__ guard)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        start, end = _resolve_window(args)
        symbols = (
            [s.strip() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else list(MEGA24)
        )
        if not symbols:
            raise ValueError("--symbols must be a non-empty comma-separated list")
        commission, slippage = COST_PROFILES[args.cost]
        defaults, arms = _load_arms(args.arms, args.only)
        overrides = _parse_set_overrides(args.set_args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if not _assert_cache(symbols, start, end, _MASSIVE_CACHE_DIR, args.allow_fetch):
        return 1

    try:
        if args.allow_fetch:
            massive = MassiveDataSource(load_massive_api_key())
        else:
            massive = MassiveDataSource(api_key="", cache_dir=_MASSIVE_CACHE_DIR)
    except MassiveApiError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        return _run_arms(
            args, start, end, symbols, defaults, arms, overrides, commission, slippage, massive,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def _run_arms(args, start, end, symbols, defaults, arms, overrides, commission, slippage,
              massive) -> int:
    """One shared feed + one shared day-grouping pass, every selected arm run
    against them (10-RESEARCH Pitfall 6). Function-local imports of
    backtester.experimental.{engine,strategies} only (parallelism contract)."""
    from backtester.run import _trading_days
    from backtester.experimental.engine import build_frame, group_by_day
    from backtester.experimental.strategies import ext2_signals, orb_signals, vwap_pb_signals

    strategy_fns = {"ext2": ext2_signals, "orb": orb_signals, "vwap_pb": vwap_pb_signals}

    feed = SimulatedBarFeed(symbols, start, end, source="massive", massive=massive)
    days = _trading_days(start, end)
    day_groups = group_by_day(feed, days)

    resolved_arms = [(arm, _resolve_params(defaults, arm, overrides)) for arm in arms]

    regime_labels = None
    if any(p.get("regime_gate") == "weekly_spy" for _, p in resolved_arms):
        regime_labels = _load_regime_labels(args.regime_csv)

    window_label = args.window or f"{start}_{end}"
    git_sha = _git_sha()

    # build_frame(feed, code, params) does not consume `params` today -- cache
    # by code alone gives every arm maximal frame reuse without ever risking a
    # stale frame for a future params-sensitive build_frame.
    frame_cache: dict = {}

    for arm, params in resolved_arms:
        frames = {}
        for code in symbols:
            if code not in frame_cache:
                frame_cache[code] = build_frame(feed, code, params)
            frame = frame_cache[code]
            if not frame.empty:
                frames[code] = frame

        signal_fn = strategy_fns[arm["strategy"]]
        signals = {code: signal_fn(frame, params) for code, frame in frames.items()}

        regime_fn = None
        if params.get("regime_gate") == "weekly_spy" and regime_labels is not None:
            from backtester.experimental.indicators import regime_for_day

            def regime_fn(day, _labels=regime_labels):
                return regime_for_day(_labels, day)

        engine = _build_engine(frames, signals, params, feed, slippage, args.stop_fill, regime_fn)
        trades = engine.run(day_groups)

        out_dir = os.path.join(args.out, window_label, args.cost, arm["name"])
        metrics = write_report(
            trades, out_dir, starting_capital=100_000.0, commission_per_share=commission,
            start=start, end=end,
            extra_assumptions={
                "slippage_per_share_usd": slippage, "cost_profile": args.cost,
                "stop_fill": args.stop_fill, "arm": arm["name"], "strategy": arm["strategy"],
                "window": window_label, "gap_through_entries": engine.gap_through_entries,
            },
            extra_fields=["side", "strategy", "arm", "n_legs", "regime"],
        )

        params_doc = {
            "arm": arm["name"], "strategy": arm["strategy"], "params": params,
            "window": window_label, "start": start, "end": end,
            "cost_profile": args.cost, "commission_per_share_usd": commission,
            "slippage_per_share_usd": slippage, "stop_fill": args.stop_fill,
            "symbols": symbols, "git_sha": git_sha,
            "gap_through_entries": engine.gap_through_entries,
        }
        with open(os.path.join(out_dir, "params.json"), "w", encoding="utf-8") as f:
            json.dump(params_doc, f, indent=2)

        print(f"{arm['name']}: trades={metrics['total_trades']} profit_factor={metrics['profit_factor']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
