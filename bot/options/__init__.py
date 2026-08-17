#!/usr/bin/env python3
"""
bot.options — the `tasty_credit_spreads` options premium-selling strategy (Phase 8).

Self-contained package (design D4): defined-risk credit spreads (iron condor or
put credit spread) on a small liquid-ETF universe, driven by its own
rules_options.json and its own SQLite DB, so the equity Trend Join Long path is
untouched.

Modules:
  schema    — jsonschema for rules_options.json
  config    — OptionsConfig dataclass + load_options_config()
  strategy  — pure strategy functions (no I/O, no SDK; design D7)

Deliberately no re-exports: store/execution/service land in later waves and
eager re-exports here would break `import bot.options` until then.
"""
