---
phase: 10-external-strategy-research
plan: 02
subsystem: testing
tags: [backtesting, research, pandas, vectorized-signals, exit-fsm, replay-engine]

# Dependency graph
requires:
  - phase: 10-external-strategy-research
    plan: "01"
    provides: "backtester/experimental/arms.json (frozen 15-arm defaults), docs/research/2026-08-18-external-strategies-hypotheses.md, package markers for backtester/experimental/ and tests/backtester/experimental/"
provides:
  - "backtester/report.py — side-aware _net_pnl (short flips gross P&L sign) + write_report(extra_fields=...) appending columns after the frozen 8-column _CSV_FIELDS via a local list (never mutated), byte-identical for existing callers"
  - "backtester/experimental/indicators.py — sma/ema/macd/atr/session_vwap/opening_range/prev_session_low_high/htf_ema_bias/bars_since/cross_up/cross_down/weekly_regime/regime_for_day/swing_high_2_2, all causal (rows <= t only)"
  - "backtester/experimental/strategies.py — DEFAULTS (parity with arms.json) + ext2_signals/orb_signals/vwap_pb_signals vectorized, prefix-invariant signal frames"
  - "backtester/experimental/exits.py — pct_ladder/partial_be_trail/fixed_2r exit models, long and short, advance(position, bar, params) -> list[dict]"
  - "backtester/experimental/engine.py — build_frame, group_by_day, Position, Engine(frames, signals, params, feed, slippage, stop_fill, regime_fn=None).run(days) -> list[dict] trade rows, .gap_through_entries counter"
affects: ["10-03-PLAN (same wave, consumes the exact Engine/build_frame/group_by_day/strategies signatures)", "10-04-PLAN", "10-05-PLAN", "10-06-PLAN"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "strategies.py signal functions compute their own indicators internally (call into indicators.py per invocation) rather than consuming pre-attached columns from build_frame — keeps strategies.py independently testable against a hand-built raw frame and preserves prefix-invariance by construction, since every indicators.py function is already causal"
    - "exits.py exit models operate on a plain mutable position dict (side/entry_price/initial_stop/stop/full_quantity/open_quantity/state/bars), returning at most one leg-instruction list per call (mirrors bot.position.state.PositionState.evaluate_close's single-transition-per-bar rule)"
    - "Engine.run(days) consumes the dict group_by_day(feed, day_list) returns (not a plain day list) so feed.replay() is paid once per window and shared across every arm's Engine instance (10-RESEARCH Pitfall 6)"
    - "Engine passes a precomputed swing pivot (new_swing_low/new_swing_high) into exits.py's position dict, mirroring bot.position.manager's own compute-then-pass-in convention, while exits.py keeps a bars-based fallback for its own standalone unit tests"

key-files:
  created:
    - backtester/experimental/indicators.py
    - backtester/experimental/strategies.py
    - backtester/experimental/exits.py
    - backtester/experimental/engine.py
    - tests/backtester/experimental/test_indicators.py
    - tests/backtester/experimental/test_strategies.py
    - tests/backtester/experimental/test_exits.py
    - tests/backtester/experimental/test_engine.py
  modified:
    - backtester/report.py
    - tests/backtester/test_report.py

key-decisions:
  - "strategies.py functions are self-contained (compute indicators internally) rather than consuming pre-attached indicator columns from build_frame — this was necessary because strategies.py (Task 2) is executed and tested before engine.py (Task 3) exists in the same plan; build_frame instead only adds time_key/hod/lod/cum_volume/in_window shaping columns"
  - "engine.py imports bot.strategy.indicators.swing_low_2_2 directly (not just via exits.py) and precomputes the swing pivot per bar, passing it into exits.py's position dict as new_swing_low/new_swing_high — satisfies the plan's explicit key_link (engine.py -> bot.strategy.indicators.swing_low_2_2) and the 'exactly 2 from-bot-imports' acceptance grep, while exits.py keeps its own bars-based fallback so the Task 2 unit tests (calling advance() standalone) stay green unchanged"
  - "_htf_ema_value private helper duplicates indicators.htf_ema_bias's completed-hourly-bar construction but returns the raw EMA series instead of a boolean — negating the boolean (for a 'bear' condition) would silently turn NaN/'unknown' into a false bear signal; orb/vwap_pb need both bull and bear sides to correctly stay false when HTF data is not yet available"
  - "vwap_pb_signals reads p.get('or_bars', 6) (overridable) instead of a hardcoded module constant, even though vwap_pb has no or_bars key in arms.json's DEFAULTS — keeps the function unit-testable with a small OR window while defaulting to the same 30-minute window orb's own base default uses"
  - "Position.trade_recorded guard + explicit del self._positions[code] after a closing _manage_position call (Rule 1 bug fix, found via test): without both, a position that fully closes via the exit-model path stayed in self._positions and was re-processed on the next bar-group, appending duplicate trade rows"

requirements-completed: [XSR-02, XSR-04]

duration: ~35min
completed: 2026-08-18
---

# Phase 10 Plan 02: Engine Core (indicators/strategies/exits/engine + report.py patch) Summary

**Built the pure, testable core of the experimental research backtester — 13 causal indicator functions, three vectorized signal generators (ext2/orb/vwap_pb) proven prefix-invariant, three long-and-short exit models, and a per-bar replay engine producing side-aware harness-convention trade rows — plus the one approved 5-line side-aware patch to `backtester/report.py`.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3
- **Files modified:** 10 (8 created, 2 modified)

## Accomplishments

- Patched `backtester/report.py`: `_net_pnl` is now side-aware (`sign = -1 if trade.get("side") == "short" else 1`, backward compatible for callers with no `side` key) and `write_report` gained an optional `extra_fields` param that appends columns after the frozen `_CSV_FIELDS` via a local list — `_CSV_FIELDS` itself is never mutated (verified: length stays 8 after a full extra-fields write). Diff is 15 changed lines, well under the plan's 20-line surgical-patch bar. Full 1087-test suite green after the patch — no existing TJL/options report test regressed.
- `backtester/experimental/indicators.py`: pure pandas/numpy functions (`sma`, `ema`, `macd`, `atr`, `session_vwap`, `opening_range`, `prev_session_low_high`, `htf_ema_bias`, `bars_since`, `cross_up`, `cross_down`, `weekly_regime`, `regime_for_day`, `swing_high_2_2`) — every one operates on rows `<= t` only (zero `shift(-`, verified by grep); `swing_high_2_2` is the exact mirror of the imported `bot.strategy.indicators.swing_low_2_2` on negated highs.
- `backtester/experimental/strategies.py`: `DEFAULTS` dict verified byte-identical to `arms.json`'s `defaults` block, plus `ext2_signals`/`orb_signals`/`vwap_pb_signals` — each returns `long`/`short`/`stop_long`/`stop_short` columns, proven prefix-invariant across 5 distinct prefix lengths in one shared test loop (`fn(frame.iloc[:k], p).iloc[k-1] == fn(frame, p).iloc[k-1]` for every column).
- `backtester/experimental/exits.py`: `pct_ladder`, `partial_be_trail`, `fixed_2r` — the ladder test hits the design doc's exact literal blended `exit_price=101.5625` (`n_legs=4`, `r_multiple=0.78125`); the short `partial_be_trail` test proves a profitable short yields a positive `r_multiple` (10-RESEARCH Pitfall 3).
- `backtester/experimental/engine.py`: `build_frame`, `group_by_day`, `Position`, `Engine(frames, signals, params, feed, slippage, stop_fill, regime_fn=None)` — the per-bar order (force-close → position management → entry evaluation → N+1-open fill) is proven by 15 dedicated tests: the look-ahead fill proof (103.50, never 105.00), 6-signals-cap-5 alphabetical ordering, the -$2,000 daily breaker (blocks entries only, never force-closes), force-close on both a normal day's 15:50 bar and the 2025-07-03 half-day's 12:50 bar, `stop_fill` close-vs-intrabar, a profitable short's positive `r_multiple`, and regime bear/bull/neutral gating. `grep -c 'from bot' engine.py` returns exactly 2; zero broker/StateStore references.

## Task Commits

Each task was committed atomically:

1. **Task 1: Patch report.py + add indicators.py** — `6857d63` (feat)
2. **Task 2: strategies.py + exits.py** — `6a4383e` (feat)
3. **Task 3: engine.py — per-bar replay engine** — `69b47e9` (feat)

**Plan metadata:** committed separately below (final metadata commit).

## Files Created/Modified

- `backtester/report.py` - `_net_pnl` side-aware sign, `write_report(..., extra_fields=None)`
- `backtester/experimental/indicators.py` - 13 causal pandas/numpy indicator functions
- `backtester/experimental/strategies.py` - `DEFAULTS` + `ext2_signals`/`orb_signals`/`vwap_pb_signals`
- `backtester/experimental/exits.py` - `pct_ladder`/`partial_be_trail`/`fixed_2r`
- `backtester/experimental/engine.py` - `build_frame`/`group_by_day`/`Position`/`Engine`
- `tests/backtester/test_report.py` - 2 new cases (short-sign, extra_fields header/no-mutation)
- `tests/backtester/experimental/test_indicators.py` - hand-computed cases for all 13 functions
- `tests/backtester/experimental/test_strategies.py` - prefix-invariance + positive/negative/boundary cases
- `tests/backtester/experimental/test_exits.py` - ladder/trail/fixed_2r cases incl. the 101.5625 literal
- `tests/backtester/experimental/test_engine.py` - 15 engine-behavior cases + build_frame/group_by_day

## Decisions Made

See `key-decisions` in frontmatter — summarized: strategies.py is self-contained (computes its own indicators, doesn't depend on build_frame's column shape); engine.py directly imports `swing_low_2_2` and precomputes the swing pivot per bar (satisfies the plan's key_link and the "exactly 2 from-bot-imports" grep) while exits.py keeps a bars-based fallback for its own standalone tests; `_htf_ema_value` duplicates `htf_ema_bias`'s construction to expose both bull and bear sides without an unknown-data false-negation bug; `vwap_pb_signals` accepts an overridable `or_bars` param for testability.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Duplicate trade rows when a position closed via the exit-model path**
- **Found during:** Task 3, writing `test_daily_breaker_blocks_further_entries_after_minus_2000` / `test_stop_fill_intrabar_mode_fills_at_exact_stop_price` / `test_short_trade_row_side_and_positive_r_multiple_on_profit`
- **Issue:** `_manage_position` returning `True` (fully closed) was recorded in the bar-group's `just_closed` set, but the position was never deleted from `self._positions` at that call site — so the next bar-group's position-management step re-processed the already-closed position, and `_finalize_if_closed` (no "already recorded" guard) appended a second (and sometimes third) trade row for the same position.
- **Fix:** Added `del self._positions[code]` immediately after a closing `_manage_position` call (mirroring the force-close loop's own explicit delete), plus a `Position.trade_recorded` guard in `_finalize_if_closed` as defense in depth (mirrors `bot/position/state.py`'s own `trade_recorded` flag).
- **Files modified:** `backtester/experimental/engine.py`
- **Verification:** All 15 `test_engine.py` cases pass; full 1087-test suite green.
- **Committed in:** `69b47e9` (part of Task 3's commit)

**2. [Rule 1 - Bug] Intrabar stop-fill qty computed after zeroing open_quantity**
- **Found during:** Task 3, first draft of the intrabar stop-fill branch
- **Issue:** `pos.open_quantity` was set to `0` before capturing the fill quantity, so the exit leg was recorded with `qty=0` and never actually closed the position (caught before any test ran, via a self-review re-read of the intrabar branch).
- **Fix:** Capture `qty = pos.open_quantity` before zeroing it.
- **Files modified:** `backtester/experimental/engine.py`
- **Verification:** `test_stop_fill_intrabar_mode_fills_at_exact_stop_price` passes.
- **Committed in:** `69b47e9` (part of Task 3's commit)

## Known Stubs

None — every module is fully wired and tested; no stubbed/placeholder data paths.

## Issues Encountered

None beyond the two auto-fixed bugs above (both caught by this plan's own test suite before commit).

## User Setup Required

None — no external service configuration required (pure pandas/numpy, no new dependencies).

## Next Phase Readiness

- `backtester.experimental.engine.Engine(frames, signals, params, feed, slippage, stop_fill, regime_fn=None)` with `.run(days) -> list[dict]` and `.gap_through_entries -> int`, `build_frame(feed, code, params) -> DataFrame`, `group_by_day(feed, days) -> dict[str, list[dict]]`, and `strategies.{ext2_signals,orb_signals,vwap_pb_signals}(frame, params) -> DataFrame` / `strategies.DEFAULTS` all verified present with the exact signatures 10-03's `run.py` is contracted to consume (confirmed live via `inspect.signature`).
- `indicators.weekly_regime(spy_daily) -> Series` and `indicators.regime_for_day(labels, day) -> str` are ready for 10-03/10-04's SPY regime-gated arm (`ext2_regime`) and the TJL regime day-filter.
- `bot/`, `rules.json`, `rules_options.json` remain untouched (`git status --porcelain` empty for all three) — the phase's hard production-code boundary is intact.
- Full suite green (1087 passed, 1 skipped) after all three tasks; no network egress in any new test (all engine tests use the hand-rolled `_FakeFeed`, no `MassiveDataSource`).

---
*Phase: 10-external-strategy-research*
*Completed: 2026-08-18*

## Self-Check: PASSED
