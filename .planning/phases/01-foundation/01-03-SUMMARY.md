---
phase: 01-foundation
plan: "03"
subsystem: strategy-layer
tags: [config, strategy, indicators, jsonschema, tdd, pure-functions, config-driven]
dependency_graph:
  requires:
    - bot._utils (safe_float, safe_get from plan 01-01)
  provides:
    - rules.json (CFG-01 canonical strategy config)
    - bot.config.StrategyConfig
    - bot.config.ConfigError
    - bot.config.load_strategy_config
    - bot.strategy.indicators.sma
    - bot.strategy.indicators.rvol
    - bot.strategy.indicators.swing_low_2_2
    - bot.strategy.StrategyCore (ABC)
    - bot.strategy.TrendJoinLong
  affects:
    - Phase 2 (Scanner uses TrendJoinLong.passes_daily_filters)
    - Phase 3 (SignalEngine uses TrendJoinLong.passes_intraday_filters)
    - Phase 6 (Backtester imports bot.strategy.* unchanged)
tech_stack:
  added:
    - jsonschema>=4.0 (schema validation for rules.json)
  patterns:
    - TDD: RED/GREEN per task (6 commits: 3 RED + 3 GREEN)
    - StrategyCore ABC (ARCHITECTURE.md Pattern 3: live/backtest parity)
    - config-driven parameters (D-12: no hardcoded strategy literals)
    - RVOL look-ahead guard (Pitfall #4: date < signal_date strict cutoff)
    - jsonschema fail-fast validation (T-01-09 threat mitigation)
key_files:
  created:
    - rules.json
    - bot/config/__init__.py
    - bot/config/schema.py
    - bot/config/loader.py
    - bot/strategy/__init__.py
    - bot/strategy/indicators.py
    - bot/strategy/core.py
    - bot/strategy/trend_join_long.py
    - tests/config/__init__.py
    - tests/config/test_loader.py
    - tests/strategy/__init__.py
    - tests/strategy/test_indicators.py
    - tests/strategy/test_config_driven.py
  modified: []
decisions:
  - "CFG-01: rules.json at repo root is the single source of truth for all strategy params"
  - "jsonschema validation at load (typed + required-key enforcement); ConfigError on any failure"
  - "StrategyConfig flattens nested JSON groups into clearly named typed fields"
  - "RVOL denominator: date < signal_date strict; today's bar never enters denominator (Pitfall #4)"
  - "D-12 behavioral proof via baseline-vs-modified config swap test (not AST scan)"
  - "compute_initial_stop: lod * (1 - cfg.max_risk_per_trade_pct/100) — no hardcoded 0.99"
  - "swing_low_2_2: searches from second-to-last candidate (index n-3) for most recent pivot"
  - "ConfigError docstring rephrased to avoid 'sys.exit' substring (same fix as plan 01-01 deviation #1)"
metrics:
  duration: "7 minutes"
  completed_date: "2026-06-23"
  tasks_completed: 3
  tasks_total: 3
  files_created: 13
  files_modified: 0
  tests_added: 64
---

# Phase 01 Plan 03: Strategy Layer (rules.json + indicators + StrategyCore + TrendJoinLong) Summary

**One-liner:** jsonschema-validated rules.json (CFG-01) loads into a typed StrategyConfig; pure SMA/RVOL/swing_low_2_2 indicators with strict no-look-ahead RVOL; StrategyCore ABC + TrendJoinLong reading every threshold from config — D-12 behavioral proof via config-swap test — all green under 64 pytest tests with zero network calls.

## What Was Built

This plan implements the pure strategy layer that both the live bot and the Phase 6 backtester share:

1. **rules.json (CFG-01):** Canonical Trend Join Long config at repo root — all 6 parameter groups (universe_filters, daily_filters, intraday_filters, time_filter, exit, risk) matching PROJECT.md verbatim. Single source of truth; no strategy constant lives in Python code.

2. **bot/config/schema.py:** `SCHEMA` — a jsonschema dict that requires all 6 top-level groups with typed fields (numbers as "number", booleans, strings) and `required` lists so a removed key fails validation immediately.

3. **bot/config/loader.py:** `load_strategy_config(path) -> StrategyConfig` — reads rules.json, parses JSON, runs jsonschema.validate, maps to a flat typed StrategyConfig dataclass. Raises `ConfigError` (not sys.exit) on missing file, malformed JSON, or schema violation. `StrategyConfig` has 14 clearly named fields (e.g. `d3_min_gap_pct`, `rvol_min`, `force_close_et`) — all nested JSON groups are flattened.

4. **bot/strategy/indicators.py:** Three pure indicator functions:
   - `sma(series, period)`: trailing mean; returns NaN for insufficient data
   - `rvol(volume_frame, signal_date, lookback_days, today_volume)`: denominator uses ONLY rows where `date < signal_date` (strict no look-ahead, Pitfall #4); returns None if fewer than `lookback_days` prior sessions
   - `swing_low_2_2(lows)`: 2-bar-left/2-bar-right pivot confirmation per ARCHITECTURE.md Pattern 3; returns None if <5 bars or no confirmed pivot

5. **bot/strategy/core.py:** `StrategyCore(abc.ABC)` with 4 `@abstractmethod` hooks: `passes_daily_filters`, `passes_intraday_filters`, `compute_initial_stop`, `compute_swing_low_2_2`. No I/O, no parameters — pure contract.

6. **bot/strategy/trend_join_long.py:** `TrendJoinLong(StrategyCore)` reading EVERY parameter from `self._cfg` (7 cfg accesses):
   - `_check_d1/d2/d3/_check_universe_price` private methods per PATTERNS.md naming
   - `compute_initial_stop`: `lod * (1 - cfg.max_risk_per_trade_pct / 100)` — not hardcoded 0.99
   - `compute_swing_low_2_2`: delegates to `indicators.swing_low_2_2`
   - D-12: no bare numeric strategy literals (3.0, 2.0, 0.99) as filter thresholds

7. **Test suite (64 tests):** TDD RED/GREEN per task:
   - `tests/config/test_loader.py`: 21 tests (all StrategyConfig fields, missing/malformed/schema-invalid error cases, no-sys.exit check)
   - `tests/strategy/test_indicators.py`: 18 tests (sma correctness and NaN, rvol no-lookahead proof with large current-day volume, swing_low_2_2 pivot/no-pivot/insufficient-bars)
   - `tests/strategy/test_config_driven.py`: 25 tests (StrategyCore ABC structure, D1/D2/D3 filter pass/fail, I1/I2/I3 filter pass/fail, compute_initial_stop config-driven, D-12 baseline-vs-modified behavioral swap, no bare literals in source)

## Task Commits

| Task | Phase | Commit | Description |
|------|-------|--------|-------------|
| 1 | RED | 01141d2 | Failing tests for loader + StrategyConfig + ConfigError |
| 1 | GREEN | ba11809 | rules.json + schema + loader implementation |
| 2 | RED | 6c778fe | Failing tests for sma, rvol, swing_low_2_2 |
| 2 | GREEN | d242181 | Pure indicators implementation |
| 3 | RED | 00f2965 | Failing tests for StrategyCore ABC + TrendJoinLong D-12 proof |
| 3 | GREEN | f4fe000 | StrategyCore ABC + TrendJoinLong config-driven implementation |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] "sys.exit" in docstring caused test assertion failure**
- **Found during:** Task 1 (first GREEN test run — test_no_sys_exit_in_loader)
- **Issue:** Module docstring in `bot/config/loader.py` said "Raises ConfigError (not sys.exit) on..." — the substring "sys.exit" triggered the source-level assertion check (identical to plan 01-01 deviation #1).
- **Fix:** Rephrased to "Raises ConfigError on... the caller handles process exit." No actual sys.exit() calls exist.
- **Files modified:** `bot/config/loader.py`
- **Commit:** ba11809 (inline with GREEN commit)

## TDD Gate Compliance

- [x] RED gate: test commits (01141d2, 6c778fe, 00f2965) exist before implementation commits
- [x] GREEN gate: feat commits (ba11809, d242181, f4fe000) follow each RED commit
- [x] All tests pass at GREEN: 64/64

## Known Stubs

None. All functions are fully implemented. `rvol` accepts a `today_volume` parameter that the caller (Scanner in Phase 2, SignalEngine in Phase 3) will supply from live/historical data.

## Threat Flags

No new threat surface beyond the plan's threat model:
- `bot/config/loader.py` reads a local file (rules.json) → T-01-09 (mitigated by jsonschema validation — malformed/schema-invalid config fails ConfigError before any strategy decision)
- `bot/strategy/` is pure logic — no network, no file I/O, no credentials

## Self-Check: PASSED

- [x] `rules.json` exists and contains `"strategy_name": "Trend Join Long"` (`grep` confirmed)
- [x] `bot/config/loader.py` contains `def load_strategy_config(`, `class StrategyConfig`, `class ConfigError`
- [x] `bot/config/loader.py` contains `jsonschema.validate` and no `sys.exit`
- [x] `bot/strategy/indicators.py` defines `sma`, `rvol`, `swing_low_2_2`; no forbidden imports
- [x] `bot/strategy/core.py` contains `class StrategyCore(abc.ABC)` with 4 `@abstractmethod`
- [x] `bot/strategy/trend_join_long.py` contains `class TrendJoinLong(StrategyCore)` with 7 `self._cfg.` accesses
- [x] `python3 -m pytest tests/config tests/strategy -q` exits 0: 64 passed
- [x] Commits 01141d2, ba11809, 6c778fe, d242181, 00f2965, f4fe000 all exist in git log
