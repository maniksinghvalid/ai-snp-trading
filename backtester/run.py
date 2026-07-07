#!/usr/bin/env python3
"""
backtester.run — CLI entry point for the offline Trend Join Long backtester (BT-01/03/04).

Composition root: loads the SAME rules.json via the SAME loader bot/main.py uses
(CFG-01/BT-01), validates operator CLI input before any yfinance fetch (V5), hard-refuses
to run against the live paper-trading state DB (06-RESEARCH Pitfall 4), then wires
SimulatedBarFeed -> BacktestHarness -> backtester.report.write_report end to end for every
NYSE trading day in [--start, --end].

Never constructs a live broker gateway anywhere in this module or the objects it
builds -- a backtest run is provably broker-free (the harness passes gateway=None into
PositionManager and only a two-method simulated stub into SignalEngine/RiskEngine).

Scope: replays the CURRENT partial_be_trail exit-model FSM only (06-RESEARCH Open-Q2);
the config loader already fail-closes on any other exit.model.

Exports: build_arg_parser, main
"""
import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime

import pandas_market_calendars as mcal

from bot.config.loader import ConfigError, load_strategy_config
from bot.safety.logger import configure_logging, get_logger
from bot.state.store import DEFAULT_DB_PATH, StateStore

from backtester.feed import BacktestWindowError, SimulatedBarFeed
from backtester.harness import BacktestHarness
from backtester.report import write_report

_DATE_FMT = "%Y-%m-%d"
_NYSE = mcal.get_calendar("NYSE")


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI flags: --symbols, --start, --end, --rules-json, --output-dir (CLAUDE.md
    dash-separated long-option convention)."""
    parser = argparse.ArgumentParser(
        prog="backtester.run",
        description="Replay the Trend Join Long strategy against historical yfinance bars.",
    )
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated moomoo codes, e.g. US.AAPL,US.MSFT",
    )
    parser.add_argument("--start", required=True, help="Replay start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Replay end date, YYYY-MM-DD")
    parser.add_argument(
        "--rules-json", default="rules.json",
        help="Path to the strategy config (default: rules.json)",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory to write summary.json + trades.csv",
    )
    return parser


def _parse_date(label: str, value: str) -> datetime:
    """Parse a YYYY-MM-DD CLI arg; raise ValueError (not a stack trace) on bad input."""
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        raise ValueError(f"--{label} must be YYYY-MM-DD, got {value!r}") from None


def _parse_symbols(value: str) -> list:
    """Comma-split --symbols; reject an empty result (V5 input validation)."""
    symbols = [s.strip() for s in value.split(",") if s.strip()]
    if not symbols:
        raise ValueError("--symbols must be a non-empty comma-separated list")
    return symbols


def _scratch_db_path() -> str:
    """A fresh per-invocation scratch path under backtester/runs/ -- never the live DB
    (06-RESEARCH Pitfall 4). One directory per run_id so concurrent/repeat runs never
    collide with each other either."""
    run_id = uuid.uuid4().hex
    return os.path.join("backtester", "runs", run_id, "state.db")


def _trading_days(start: str, end: str) -> list:
    """NYSE trading days in [start, end], inclusive, as "YYYY-MM-DD" strings.

    Reuses pandas_market_calendars (already a project dependency, same library
    bot/scanner/calendar.py wraps) instead of reimplementing holiday/weekend logic.
    """
    valid = _NYSE.valid_days(start_date=start, end_date=end)
    return [d.strftime(_DATE_FMT) for d in valid]


def main(argv=None) -> int:
    """Parse args, load config, guard the DB path, replay, and write the report.

    Returns a process-style exit code (0 success, 1 on any validation/config/window
    failure) rather than calling sys.exit directly, so it is trivially testable.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # V5 input validation -- BEFORE configure_logging/config load/any fetch.
    try:
        _parse_date("start", args.start)
        _parse_date("end", args.end)
        symbols = _parse_symbols(args.symbols)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    configure_logging()
    get_logger(__name__)

    try:
        cfg = load_strategy_config(args.rules_json)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # Pitfall 4 -- the resolved scratch path must never equal the live state DB.
    db_path = _scratch_db_path()
    if os.path.normpath(db_path) == os.path.normpath(DEFAULT_DB_PATH):
        print(
            f"[ERROR] refusing to run backtest against the live state DB ({DEFAULT_DB_PATH})",
            file=sys.stderr,
        )
        return 1

    store = StateStore(db_path=db_path).open()

    # No broker construction anywhere in this module -- a backtest run is provably
    # broker-free (BacktestHarness passes a simulated stub into SignalEngine/RiskEngine
    # and gateway=None into PositionManager).
    try:
        feed = SimulatedBarFeed(symbols, args.start, args.end)
    except BacktestWindowError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    harness = BacktestHarness(cfg, feed, store)
    for day in _trading_days(args.start, args.end):
        harness.setup_day(day, symbols)

    asyncio.run(harness.run())

    write_report(harness.trade_log, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
