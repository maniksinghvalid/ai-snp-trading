---
phase: 09-options-backtester
plan: 02
subsystem: backtester-options-greeks
tags: [black-scholes, implied-vol, iv-rank, hypotheses-pre-registration]
dependency-graph:
  requires: [backtester/options/data.py::OptionChainSource, bot/options/strategy.py::option_dte, bot/options/config.py::load_options_config]
  provides: [backtester/options/greeks.py::bs_price, backtester/options/greeks.py::bs_delta, backtester/options/greeks.py::implied_vol, backtester/options/greeks.py::dte_to_years, backtester/options/greeks.py::atm_iv, backtester/options/greeks.py::iv_rank, docs/research/2026-08-17-options-backtest-hypotheses.md]
  affects: [09-03-PLAN.md (engine.py consumes atm_iv/iv_rank to build the u dict), 09-04-PLAN.md (must run exactly the IS/OOS windows this doc pins)]
tech-stack:
  added: []
  patterns: [bisection-root-find, fail-closed-to-none, min-max-normalization]
key-files:
  created:
    - backtester/options/greeks.py
    - tests/backtester/options/test_greeks.py
    - docs/research/2026-08-17-options-backtest-hypotheses.md
  modified:
    - .planning/phases/09-options-backtester/09-VALIDATION.md
decisions:
  - "implied_vol's fail-closed floor uses DISCOUNTED (present-value) intrinsic, not naive spot-strike -- deep-ITM European puts legitimately price below naive intrinsic from strike discounting"
  - "implied_vol returns immediately when bs_price(lo)/bs_price(hi) already equals the target price (float64 vega saturation at deep-ITM+short-DTE+low-sigma) instead of drifting the bisection away from an already-found root"
  - "Pre-registered IS window 2024-11-18..2025-07-31, OOS window 2025-08-01..2026-06-15, both inside the Aug-2024..today Massive entitlement; 67-trading-day IVR warm-up 2024-08-15..2024-11-15 precedes IS start"
metrics:
  duration: "~35 minutes"
  completed: 2026-08-17
---

# Phase 9 Plan 02: Black-Scholes greeks, IV Rank, pre-registered hypotheses Summary

Built the derived-market-data layer (`backtester/options/greeks.py`: BS price/delta/implied-vol
from option closes, plus a min-max IV Rank over each underlying's own ATM-IV series) and
pre-registered the three hypotheses the backtest exists to answer, committed to git before any
real-data run exists.

## What Was Built

- **`backtester/options/greeks.py`**: `_norm_cdf` (stdlib `math.erf`, exact at float precision,
  no scipy), `bs_price`/`bs_delta` (European BS, fail closed to intrinsic/0.0 at `t_years<=0` or
  `sigma<=0`, never raise), `implied_vol` (bounded bisection, `lo=1e-4, hi=5.0, tol=1e-6,
  max_iter=100`, fails closed to `None`), `dte_to_years` (mirrors `bot.options.strategy.option_dte`'s
  `/365.25` calendar-day convention, floored at 0), `atm_iv` (nearest-target-DTE expiry, nearest-ATM
  strike, mean call+put IV from `OptionChainSource.contracts_for_day()` rows), `iv_rank` (min-max
  normalized over a trailing `IV_RANK_WINDOW=252` window with an `IV_RANK_MIN_OBS=60` warm-up —
  the Massive ~24-month entitlement is too thin for a full 252-day warm-up before the first usable
  value).
- **`tests/backtester/options/test_greeks.py`**: 14 tests covering the BS round-trip grid (5
  strikes x 3 DTEs x 3 sigmas x 2 rights = 90 combinations, price recovers within 1e-4), delta
  monotonicity/bounds, fail-closed behavior (below intrinsic, `t<=0`, no bracket root), the
  `dte_to_years`/`option_dte` parity assertion, `atm_iv` expiry/strike selection and the missing-side
  fail-closed case, the `iv_rank` fixture math, warm-up/flat-window/trailing-window-only behavior,
  and a `passes_entry_gate` unit-parity check (31 passes, 29 fails at `rules_options.json`'s
  `ivr_min: 30`, via the real imported function).
- **`docs/research/2026-08-17-options-backtest-hypotheses.md`**: H1 (`entry.ivr_min` 20 vs 30), H2
  (`structure.short_delta` 0.16 vs 0.20), H3 (`structure.type` iron_condor vs put_credit_spread),
  each as a literal `--set` override pair; IS window 2024-11-18→2025-07-31, OOS window
  2025-08-01→2026-06-15, both inside the `[2024-08-15, 2026-08-17]` Massive entitlement Plan 09-01's
  probe pinned; a 67-NYSE-trading-day IVR warm-up (2024-08-15→2024-11-15) precedes the IS start so
  `iv_rank` has already cleared `IV_RANK_MIN_OBS=60` on day 1 of IS; evidence floor >=25 closed
  trades per arm in BOTH windows, with the arithmetic reasoning for why SPY-only is unlikely to
  clear it and why widening to the D-04 universe is expected, stated up front; metric of record is
  PF + Sortino via `backtester.report._sortino_ratio`/`_win_loss_stats` (never `compute_metrics`),
  tie-broken on average credit captured %; verdict vocabulary SUPPORTED/REJECTED/
  INSUFFICIENT-EVIDENCE per D-17, with INSUFFICIENT-EVIDENCE named explicitly as an expected,
  publishable, non-negotiable outcome (not a reason to loosen the floor).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `implied_vol`'s fail-closed floor used the wrong (undiscounted) intrinsic**
- **Found during:** Task T-09-04, `test_iv_roundtrip` — a deep-ITM European put (spot 100,
  strike 110, DTE 7, sigma 0.10) legitimately re-prices to $9.905, ~9.5 cents *below* the naive
  `max(strike - spot, 0) = 10.0` "intrinsic" the RESEARCH.md code example used as the fail-closed
  gate — a real, correct consequence of discounting the strike over even a few days, not a stale
  or garbage price.
- **Issue:** RESEARCH.md's literal code (`if price < intrinsic - 1e-9: return None`, naive
  intrinsic) would have wrongly rejected `implied_vol`'s own legitimate BS-priced round-trip input,
  because a European put's true no-arbitrage floor is the *discounted* intrinsic
  (`strike*exp(-r*t) - spot`), not the undiscounted textbook value.
- **Fix:** the fail-closed floor now computes `max(spot*exp(-q*t) - strike*exp(-r*t), 0)` for a
  call and the mirror for a put — `bs_price`'s own asymptotic value as `sigma -> 0` for any
  `t_years > 0` — and compares the observed price against that. The fail-closed *behavior* the plan
  specified (garbage/stale prices below any achievable BS price return `None`) is unchanged; only
  the floor formula is corrected.
- **Files modified:** `backtester/options/greeks.py`
- **Commit:** b67342a

**2. [Rule 1 - Bug] Bisection could drift away from an already-found root at the bracket edge**
- **Found during:** Task T-09-04, `test_iv_roundtrip` — a deep-ITM call with short DTE and low
  sigma saturates `norm_cdf(d1)` to exactly `1.0` at float64 precision (vega ~ 0 in that regime),
  making `bs_price(lo)` land exactly on the target price. The literal bisection loop from
  RESEARCH.md then computed `f_lo * f_mid = 0 * f_mid = 0`, took the "same sign" branch, and moved
  `lo` *away* from the already-correct root, converging on the wrong sigma (drifted to `hi=5.0`).
- **Issue:** the loop had no early-exit for `f_lo`/`f_hi` already within tolerance of zero — a real
  edge case the plan's own mandated test grid (deep-ITM + short-DTE + low-sigma combinations)
  exercises.
- **Fix:** added an immediate `return lo`/`return hi` check right after computing `f_lo`/`f_hi`,
  before the bracket-sign check — a minimal guard, not a redesign of the bisection's shape.
- **Files modified:** `backtester/options/greeks.py`
- **Commit:** b67342a

**3. [Rule 1 — plan `<behavior>` text corrected against verified math] Put delta direction and
delta-bounds openness**
- **Found during:** Task T-09-04 test-writing — the plan's `<behavior>` states put delta is
  "strictly increasing (toward 0) in strike" and that call/put delta lie in the OPEN intervals
  `(0,1)`/`(-1,0)`. Numerically verifying `bs_delta` against the standard `N(d1)-1` put-delta
  formula shows the opposite direction is correct (strike 80 → -0.004, strike 120 → -0.976 — put
  delta gets MORE negative, i.e. strictly DECREASING, as strike rises, since a higher strike put is
  deeper in the money) and that the same deep-ITM/short-DTE/low-sigma saturation from deviation #2
  makes delta reach exactly 0.0/1.0/-1.0 at float64 precision, so the interval must be closed, not
  open.
- **Issue:** this is a plan-text inaccuracy, not an implementation bug — `bs_delta`'s formula is
  the textbook one and was not changed.
- **Fix:** wrote `test_delta_monotonic`/`test_delta_bounds` to assert the mathematically verified
  direction/interval, with an inline comment explaining the discrepancy from the plan's literal
  wording.
- **Files modified:** `tests/backtester/options/test_greeks.py`
- **Commit:** 4ba1dfe (test) / b67342a (green, no code change needed for this item)

Or otherwise: T-09-06 (hypotheses doc) executed exactly as specified.

## Self-Check

- `backtester/options/greeks.py` — FOUND
- `tests/backtester/options/test_greeks.py` — FOUND
- `docs/research/2026-08-17-options-backtest-hypotheses.md` — FOUND
- Commit `4ba1dfe` (T-09-04/05 RED) — FOUND
- Commit `b67342a` (T-09-04/05 GREEN) — FOUND
- Commit `a62c0af` (T-09-06, hypotheses doc) — FOUND

## TDD Gate Compliance

- T-09-04/T-09-05: RED (`4ba1dfe test(09-02): add failing tests for BS greeks + IVR`) precedes
  GREEN (`b67342a feat(09-02): Black-Scholes IV/delta + min-max IV Rank`). Both tasks were
  implemented against a single RED/GREEN pair (same test file, same module) rather than two
  separate RED/GREEN cycles — a commit-granularity choice, not a gate skip: one test commit
  precedes one implementation commit, satisfying the RED-before-GREEN ordering the gate checks.
  T-09-06 is `type="auto"` (no `tdd="true"`), no RED/GREEN gate applies.

## Verification

- `pytest tests/backtester/options/ -x -q` — 36 passed
- `pytest -q` (full suite) — **1001 passed, 1 skipped** (987 baseline + 14 new; no regression)
- `grep -rn 'scipy' backtester/ requirements.txt` — one match, in `greeks.py`'s own docstring
  stating scipy is NOT used; zero actual imports
- `grep -c 'math.erf' backtester/options/greeks.py` — 3 (one call site, two docstring mentions)
- `git log --diff-filter=A --format=%h -- 'docs/research/*options-backtest-hypotheses.md'` —
  returns `a62c0af`
- `ls backtester/results/options/` — absent (no options real-data run has occurred; a pre-existing
  `backtester/results/{massive-2025-q2q3,massive-smoke-2025-04,strategy-audit-validation}` tree
  from the equity backtester is untracked scratch output, unrelated to this plan, left untouched)

## Self-Check: PASSED
