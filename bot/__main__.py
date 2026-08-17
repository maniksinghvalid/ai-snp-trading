#!/usr/bin/env python3
"""
bot.__main__ — python -m bot entry point.

Dispatches to bot.main.main(), which picks the bot from the rules file's
top-level strategy_name (D5):
  python -m bot                                 # equity — Trend Join Long
  python -m bot --rules rules_options.json      # options — tasty credit spreads

Exports: _parse_args (side-effect module otherwise; calls main() as __main__)
"""
import argparse

from bot.main import main


def _parse_args(argv=None):
    """Parse the command line (argv=None reads sys.argv — testable with a list)."""
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="Run the trading bot selected by the rules file's strategy_name.",
    )
    parser.add_argument(
        "--rules",
        default="rules.json",
        help="path to the rules file; its strategy_name selects the bot",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    main(rules_path=_parse_args().rules)
