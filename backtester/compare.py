#!/usr/bin/env python3
"""
backtester.compare — run N rules.json variants over ONE fetched dataset, side-by-side
(strategy-audit plan, P0-A).

Sweeping strategy parameters against Massive naively (backtester.run once per config)
refetches or re-walks the same historical bars every time. SimulatedBarFeed is
read-only after construction (replay()/next_bar()/daily_bars()/etc. are pure
accessors over data loaded in __init__ — verified against backtester/feed.py), so
one feed safely serves any number of BacktestHarness runs. compare.py builds that
one feed, then for each --rules-json variant runs a FRESH scratch StateStore +
BacktestHarness against it, writing each variant's report to its own subdirectory
and printing one comparison table.

Reuses backtester.run's already-tested CLI plumbing (_parse_date, _parse_symbols,
_trading_days, _scratch_db_path) rather than re-deriving it.

Never constructs a live broker gateway -- same broker-free guarantee as
backtester.run (a fresh SimulatedGateway/SimulatedExecution per variant via
BacktestHarness; PositionManager still receives gateway=None).

Exports: build_arg_parser, main
"""
import argparse
import asyncio
import os
import sys

from bot.config.loader import ConfigError, load_strategy_config
from bot.safety.logger import configure_logging, get_logger
from bot.state.store import DEFAULT_DB_PATH, StateStore

from backtester.feed import BacktestWindowError, SimulatedBarFeed
from backtester.harness import BacktestHarness
from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.report import write_report
from backtester.run import _parse_date, _parse_symbols, _scratch_db_path, _trading_days

_TABLE_COLUMNS = [
    ("variant", "variant"),
    ("total_trades", "trades"),
    ("net_pnl_usd", "net_pnl"),
    ("win_rate", "win_rate"),
    ("avg_r_multiple", "avg_r"),
    ("profit_factor", "profit_factor"),
    ("sharpe_ratio", "sharpe"),
    ("sortino_ratio", "sortino"),
    ("calmar_ratio", "calmar"),
    ("max_drawdown_pct", "max_dd_pct"),
]


def build_arg_parser() -> argparse.ArgumentParser:
    """Same knobs as backtester.run, plus --rules-json accepting a comma-separated
    list of "label=path" (or bare "path", labeled by basename)."""
    parser = argparse.ArgumentParser(
        prog="backtester.compare",
        description="Run multiple rules.json variants over one fetched dataset and compare.",
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated moomoo codes")
    parser.add_argument("--start", required=True, help="Replay start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Replay end date, YYYY-MM-DD")
    parser.add_argument(
        "--rules-json", required=True,
        help='Comma-separated variants: "label=path.json,label2=path2.json" '
             '(a bare "path.json" is labeled by its basename)',
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory; each variant writes to <output-dir>/<label>/",
    )
    parser.add_argument("--source", choices=["yfinance", "massive"], default="yfinance")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--starting-capital", type=float, default=None)
    parser.add_argument("--commission-per-share", type=float, default=0.0)
    parser.add_argument("--slippage-usd", type=float, default=0.0)
    return parser


def _parse_variants(value: str) -> list:
    """"label=path,label2=path2" -> [(label, path), ...]; bare "path" -> basename label."""
    variants = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            label, path = part.split("=", 1)
        else:
            label, path = os.path.splitext(os.path.basename(part))[0], part
        variants.append((label.strip(), path.strip()))
    return variants


def _print_table(rows: list) -> None:
    """Fixed-width side-by-side comparison table over _TABLE_COLUMNS."""
    headers = [header for _, header in _TABLE_COLUMNS]
    widths = [
        max(len(header), *(len(str(row.get(key, ""))) for row in rows))
        for (key, header) in _TABLE_COLUMNS
    ]
    def fmt_row(values):
        return "  ".join(str(v).ljust(w) for v, w in zip(values, widths))
    print(fmt_row(headers))
    print(fmt_row(["-" * w for w in widths]))
    for row in rows:
        values = [_fmt_metric(key, row.get(key)) for key, _ in _TABLE_COLUMNS]
        print(fmt_row(values))


def _fmt_metric(key: str, value) -> str:
    if key == "variant" or value is None:
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main(argv=None) -> int:
    """Parse args, load one feed, replay it against every rules-json variant, print
    a comparison table. Returns a process-style exit code (0 success, 1 on any
    validation/config/window failure)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        _parse_date("start", args.start)
        _parse_date("end", args.end)
        symbols = _parse_symbols(args.symbols)
        variants = _parse_variants(args.rules_json)
        if not variants:
            raise ValueError("--rules-json must list at least one label=path.json variant")
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.interval != "5m":
        print(f"[ERROR] --interval must be 5m, got {args.interval!r}", file=sys.stderr)
        return 1
    if args.commission_per_share < 0 or args.slippage_usd < 0:
        print("[ERROR] --commission-per-share and --slippage-usd must be >= 0", file=sys.stderr)
        return 1
    if args.starting_capital is not None and args.starting_capital <= 0:
        print("[ERROR] --starting-capital must be > 0", file=sys.stderr)
        return 1
    if args.slippage_usd == 0:
        print(
            "[WARN] --slippage-usd is 0 -- these numbers are an upper bound, not "
            "comparable to live cost-adjusted performance.",
            file=sys.stderr,
        )

    configure_logging()
    get_logger(__name__)

    # One feed for every variant (SimulatedBarFeed is read-only after construction).
    try:
        if args.source == "massive":
            massive = MassiveDataSource(load_massive_api_key())
            feed = SimulatedBarFeed(
                symbols, args.start, args.end, source="massive", massive=massive
            )
        else:
            feed = SimulatedBarFeed(symbols, args.start, args.end)
    except (BacktestWindowError, MassiveApiError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    trading_days = _trading_days(args.start, args.end)
    rows = []
    for label, rules_path in variants:
        try:
            cfg = load_strategy_config(rules_path)
        except ConfigError as exc:
            print(f"[ERROR] variant {label!r} ({rules_path}): {exc}", file=sys.stderr)
            return 1

        if args.starting_capital is not None:
            cfg.sizing_equity_usd = args.starting_capital
        starting_capital = cfg.sizing_equity_usd or 100_000.0

        db_path = _scratch_db_path()
        if os.path.normpath(db_path) == os.path.normpath(DEFAULT_DB_PATH):
            print(f"[ERROR] refusing to run against the live state DB ({DEFAULT_DB_PATH})",
                  file=sys.stderr)
            return 1
        store = StateStore(db_path=db_path).open()

        # No broker construction -- BacktestHarness passes gateway=None into
        # PositionManager and a simulated stub into SignalEngine/RiskEngine.
        harness = BacktestHarness(cfg, feed, store, slippage_usd=args.slippage_usd)
        for day in trading_days:
            harness.setup_day(day, symbols)
        asyncio.run(harness.run())

        variant_dir = os.path.join(args.output_dir, label)
        metrics = write_report(
            harness.trade_log, variant_dir,
            starting_capital=starting_capital,
            commission_per_share=args.commission_per_share,
            start=args.start, end=args.end,
            extra_assumptions={"slippage_usd": args.slippage_usd, "rules_json": rules_path},
        )
        rows.append({"variant": label, **metrics})

    _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
