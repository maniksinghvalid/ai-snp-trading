# Phase 9: Options backtester - Research

**Researched:** 2026-08-17
**Domain:** Offline options backtesting — Massive (Polygon-compatible) historical options data, Black-Scholes IV/delta, credit-spread P&L simulation, reuse of Phase 8 pure strategy functions
**Confidence:** MEDIUM-HIGH (data-layer facts verified live against the real API; strategy-reuse facts verified by reading source; IVR semantic and report.py-reuse facts flagged where genuinely ambiguous)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Scope / architecture**
- D-01 New package `backtester/options/` (sibling of the equity backtester, not inside `bot/`); entry point `python3 -m backtester.options_run --rules rules_options.json --symbols … --start … --end … [--out DIR]`; ≤5 modules (data, greeks, engine, report glue, CLI)
- D-02 Import, don't copy: backtester imports `pick_expiry/passes_entry_gate/pick_strikes/size_position/mark_spread/manage_decision/leg_is_liquid/is_monthly_expiry/option_dte` from `bot/options/strategy.py` and builds config via `bot/options/config.py`; a test asserts the import (OBT-01)
- D-03 Daily resolution only — one decision point per trading day; no intraday bars, no 14:30 second scan
- D-04 Universe = `rules_options.json.universe` (15 ETFs); first real run SPY only, then QQQ/IWM/TLT/GLD/XLE; symbols are a CLI arg

**Data (OBT-02)**
- D-05 Massive endpoints: `/v3/reference/options/contracts` (`expired=true|false`, `as_of`, paginate `next_url`), `/v2/aggs/ticker/O:…/range/1/day/…` option bars, `/v2/aggs/ticker/SPY/range/1/day/…` underlying; reuse/extend `MassiveDataSource` in `backtester/massive.py` (`_get_json`, key loading, `backtester/cache/massive/`), do not fork
- D-06 On-disk cache mandatory (per contract+range; key includes endpoint+params); full SPY multi-year run re-runnable offline; back-off reuses `_get_json`
- D-07 Per decision day: only contracts with a bar ON that day and expiry in `[min_dte, max_dte]` (monthly preference from `prefer_monthly`) are fed to `pick_expiry` unchanged
- D-08 No look-ahead: on day *t* the strategy sees only bars dated ≤ *t*; open at day *t* close-derived mid; ahead-only fixture test (Phase 6 BT-02 pattern)

**Greeks / IV (OBT-03, OBT-04)**
- D-09 Black-Scholes European, q=0 default, r configurable (default 0.045); IV by bracketed root-find in stdlib `math` (no scipy); delta from that IV; rows to `pick_strikes` use the SAME dict keys as live `screen_options` rows (check `bot/gateway/gateway.py`, `strategy.py::_leg/_mid`)
- D-10 IVR: per-underlying daily ATM-IV series (nearest target-DTE expiry, nearest-ATM strike, mean call+put IV); IVR = rank within trailing 252 trading days ×100 (live fraction→percent convention); delivered to `passes_entry_gate` in the live `u` dict shape; IVP skipped (`ivp_min` null)

**Fill model / P&L (OBT-05)**
- D-11 Mid = close (OHLC only); fill = mid ± `slippage_usd` per leg (default 0.02, overridable), per-leg commission (default $0.65+fees, configurable), ×100 multiplier; synthesize bid/ask as close ± spread_pct/2 for `leg_is_liquid`; OI/volume from aggregates `v` when present else pass — documented limitation
- D-12 Manage daily with `mark_spread` + `manage_decision` (50% target, 21-DTE, stop multiple if set); expiry settlement at intrinsic vs underlying close; assignment guard mirrors live `assignment_guard_dte`
- D-13 Sizing via live `size_position` with `sizing_equity_usd`; portfolio caps (`max_concurrent_positions`, `max_new_positions_per_day`, `max_bp_usage_pct`, `daily_loss_limit_pct`) enforced in the same gate order as `bot/options/service.py` — no invented caps

**Hypotheses (OBT-06)**
- D-14 `docs/research/2026-MM-DD-options-backtest-hypotheses.md` committed BEFORE the first real-data run: H1 IVR 20 vs 30, H2 short delta 0.16 vs 0.20, H3 iron_condor vs put_credit_spread; evidence floor ≥25 closed trades per arm; IS vs OOS windows declared up front (researcher confirms Massive history coverage); metric of record PF + Sortino, tie-break avg credit captured; SUPPORTED only if it wins in BOTH IS and OOS and clears the floor
- D-15 Variants via `--set entry.ivr_min=20` style CLI overrides (or variants JSON), never by editing `rules_options.json`; effective config recorded per run

**Output (OBT-07)**
- D-16 `backtester/results/options/<run-id>/` with `trades.csv` (symbol, structure, open/close dates, strikes, credit, exit reason, P&L $, max-loss, DTE open/close), `summary.json` (win rate, PF, avg credit captured %, max DD $, Sortino, trades, per-symbol), `config.json`; reuse `backtester/report.py` metric helpers, no duplicated Sortino/DD math
- D-17 Result doc `docs/research/…-options-backtest-results.md` reports each hypothesis SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with numbers

**Testing**
- D-18 `tests/backtester/options/`: import-not-copy, look-ahead fixture, BS round-trip (price→IV→price within 1e-4) + delta monotonic in strike, IVR fixture rank, fill/settlement arithmetic on a hand-built 2-leg example, engine caps; full suite green (965 as of b36af60); no live network in tests

### Claude's Discretion
- Exact module names inside `backtester/options/`; whether the IVR series is cached alongside bars; parquet vs JSON cache format (prefer what `backtester/massive.py` already does)
- Missing-bar handling for a leg on a manage day (carry-forward last close vs skip) — pick one, document it

### Deferred Ideas (OUT OF SCOPE)
- Intraday bars / second entry scan / bid-ask fills (chain snapshot 403)
- Strangles, naked puts (need BP model), rolling, VIX-scaled BP ladder, earnings blackout
- Any `bot/options/*` or `rules_options.json` change before results exist
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OBT-01 | Import `bot/options/strategy.py` pure functions + `rules_options.json` unchanged, import-not-copy pattern | §Architecture Patterns (import pattern precedent from `backtester/harness.py`); §Code Examples; §Validation Architecture |
| OBT-02 | Massive data layer (contracts reference incl. `expired=true` + `O:…` daily aggregates), on-disk cache, no look-ahead | §Standard Stack, §Massive API — Verified Facts, §Common Pitfalls #1/#6/#7 |
| OBT-03 | Black-Scholes IV and delta from option close + underlying close + DTE + r | §Code Examples (BS pricer/IV/delta), §Common Pitfalls #4 |
| OBT-04 | IVR from own rolling 252-day ATM-IV series matching live semantics | §Common Pitfalls #3, §Open Questions Q1, §Assumptions Log A1 |
| OBT-05 | Fill model = mid ± slippage, per-leg commission, expiry settlement at intrinsic | §Code Examples (fill/settlement), §Common Pitfalls #5 |
| OBT-06 | Pre-registered hypotheses doc before first real-data run, evidence floor, OOS window | §Massive API — Verified Facts (history depth constrains IS/OOS design), §Open Questions Q2 |
| OBT-07 | Per-trade log + summary metrics reusing `backtester/report.py` conventions | §Don't Hand-Roll, §Common Pitfalls #2 |
</phase_requirements>

## Summary

Phase 9 replays the Phase 8 `tasty_credit_spreads` pure functions (`bot/options/strategy.py`
— already written with zero I/O, zero clock reads, so it is import-ready with no
modification) over Massive historical options data, at daily resolution, and evaluates a
pre-registered hypothesis set. The strategy-reuse side is low-risk: every function the
backtester needs already exists, is pure, and is unit-tested in isolation (`tests/options/`).
The genuinely new work is entirely in the data layer and the fill/greeks math, neither of
which exist yet.

The single most important finding from this research is **empirically verified against the
live API today**: `MassiveDataSource`'s existing entitlement covers `/v3/reference/options/contracts`
back to at least 2019 with no restriction, but `/v2/aggs/ticker/O:…/range/1/day/…` (option
daily bars) is gated to roughly the **trailing 24 months** — a request for Jan–Mar 2024 data
returned `403 NOT_AUTHORIZED "Your plan doesn't include this data timeframe"` while a
Sept 2024 request succeeded. This directly constrains OBT-06's hypothesis design: the
IS/OOS window split cannot span multiple years back as the design doc implicitly assumed
("Massive entitled to contracts + option daily aggregates (200)" — that check did not test
timeframe, only that the endpoint responds). The hypotheses doc (D-14) must declare an
IS/OOS split that fits inside roughly Aug 2024–Aug 2026, which is thin for a 45-DTE strategy
(~8-9 full entry-to-exit cycles per underlying per year) — multi-underlying, multi-position
sizing is what gets the evidence floor (≥25 trades/arm), not a long time span.

Second-most important finding: `backtester/report.py`'s public `compute_metrics`/`write_report`/
`build_equity_curve` are hard-wired to a per-share stock P&L formula
(`(exit_price - entry_price) * quantity - commission * quantity * 2`) that is **wrong** for a
credit spread (P&L is `(credit - cost_to_close) * 100 * qty`, quantity is *spreads* not
*shares*, and there can be 2–4 legs). D-16's "reuse metric helpers, don't duplicate Sortino/DD
math" must mean reusing the **module-private ratio functions** (`_sharpe_ratio`,
`_sortino_ratio`, `_calmar_ratio`, `_win_loss_stats` — all take a plain equity curve or a list
of dollar P&Ls, with no equity-specific assumption baked in) via explicit import, while the
options engine computes its own per-trade dollar P&L and its own equity curve using the
options trade-log shape from D-16.

**Primary recommendation:** Build `backtester/options/{data,greeks,engine}.py` +
`backtester/options_run.py` (4 new files, mirrors `backtester/run.py`'s composition-root
style exactly). Extend `backtester/massive.py` with two new methods (`cached_contracts`,
`cached_option_bars`) rather than forking it. Reuse `backtester/report.py`'s private ratio
functions, not its public per-trade-P&L pipeline. Write the hypotheses doc's IS/OOS window
only after confirming exact Massive coverage per underlying (the 24-month cutoff was only
probed for SPY).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Strategy decision logic (expiry/gate/strikes/sizing/manage) | Pure logic (imported, no tier) | — | `bot/options/strategy.py` has zero I/O; the backtester is a caller, not a reimplementation |
| Historical data fetch + cache | Data / Storage (local) | — | `backtester/massive.py` HTTP + CSV cache under `backtester/cache/massive/`, offline-replayable |
| Black-Scholes IV/delta + IVR series | Pure logic (new module) | — | Derived math over cached closes; no I/O of its own |
| Daily replay loop / portfolio caps | Backtest engine (offline process) | — | Analogous to `backtester/harness.py`'s role for the equity backtester, but far simpler (no FSM, no bar loop) |
| Report / metrics | Reporting (offline) | — | `backtester/report.py` ratio functions + a new options-shaped trade-log writer |
| CLI / composition root | CLI (offline process) | — | `backtester/options_run.py`, mirrors `backtester/run.py` |

No browser/frontend/API tiers apply — this is an entirely offline, single-process CLI tool,
same category as the existing equity backtester.

## Standard Stack

No new external packages are required. Everything OBT-01..07 needs is either already a
pinned project dependency or stdlib.

### Core (already installed — verified via `requirements.txt` + local repo)
| Library | Version | Purpose | Why reused |
|---------|---------|---------|--------------|
| `math` (stdlib) | — | Black-Scholes price/delta, `erf`-based `N(x)`, bisection IV root-find | D-09 explicitly forbids adding scipy; stdlib is sufficient for a bisection solver |
| `pandas` | >=2.0,<4.0 (pinned) | Cache frames, per-day contract slicing | Already how `backtester/massive.py`/`backtester/feed.py` represent bar data |
| `numpy` | ==2.5.0 (pinned) | Optional vectorized IVR/percentile math | Available but not required — pure-Python loops are fine at this data scale (hundreds of contracts × hundreds of days, not millions) |
| `pandas-market-calendars` | ==5.4.0 (pinned) | NYSE trading-day stepping for the daily replay loop and the 252-day IVR window | `bot/scanner/calendar.py`, `backtester/report.py`, `backtester/run.py` all already use `mcal.get_calendar("NYSE")` — reuse the same calendar object pattern |
| `jsonschema` | >=4.0,<5.0 (pinned) | N/A directly — `bot/options/config.py` already validates `rules_options.json` via this; the backtester imports `load_options_config`, does not re-validate | Reuse, not a new use |

### Supporting (existing project modules to import, not new deps)
| Module | Purpose | When to use |
|--------|---------|-------------|
| `backtester.massive.MassiveDataSource` | HTTP + `Authorization: Bearer` auth + 429 backoff + CSV cache | Extend with 2 new methods (§Architecture Patterns) rather than a parallel HTTP client |
| `bot.options.config.load_options_config` / `OptionsConfig` | Same config the live bot reads | D-02 — never hand-parse `rules_options.json` |
| `bot.options.strategy.*` (9 named functions, D-02) | The strategy itself | Import, never copy-paste |
| `bot.safety.et_helpers.ET` / `bot.scanner.calendar.is_trading_day` / `get_prior_n_trading_days` | ET timezone + NYSE calendar helpers | Reuse instead of a new date-math module |
| `backtester.report._sharpe_ratio` / `_sortino_ratio` / `_calmar_ratio` / `_win_loss_stats` | Ratio math over a plain `[(date, equity), ...]` curve or a dollar-P&L list | Import explicitly (Python allows importing underscore-prefixed names) — see §Don't Hand-Roll |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Bisection IV solver | Newton-Raphson (faster, needs vega) | Bisection is simpler, always converges given a valid bracket (BS price is monotonic in σ), and 200 contracts/day is not a performance-sensitive workload — bisection is the right lazy choice here |
| Reusing `backtester.report.compute_metrics()` verbatim | Writing an options-native `compute_metrics` | `compute_metrics`'s `_net_pnl` formula is stock-share-shaped (see §Common Pitfalls #2) — reusing it verbatim on spread trades silently produces wrong P&L. Reuse only the ratio helpers. |

**Installation:** none — no `pip install` needed for this phase.

## Package Legitimacy Audit

Not applicable. This phase introduces zero new external packages (confirmed against
`requirements.txt` and D-09's explicit "no scipy" constraint). Every dependency used is
already installed and pinned. No `slopcheck`/registry verification is required.

## Massive API — Verified Facts (live-probed 2026-08-17, HIGH confidence)

These were confirmed with a handful of direct, authenticated requests against
`https://api.massive.com` using the repo's own `MASSIVE_API_KEY` (`.env`) — not training
data, not the design doc's earlier "200" check (which apparently didn't test timeframe).

1. **`/v3/reference/options/contracts?underlying_ticker=SPY&expired=true&expiration_date.gte=…&expiration_date.lte=…&limit=…`** — `[VERIFIED: massive.com API, live probe]` returns `200` with results as far back as tested (2019-01, 2024-01, 2025-06 all returned data). Pagination is `next_url` with an opaque `cursor` query param (same `while url:` pattern `MassiveDataSource.fetch_bars` already implements). Response shape per contract:
   ```json
   {"cfi": "OCASPS", "contract_type": "call", "exercise_style": "american",
    "expiration_date": "2024-01-02", "primary_exchange": "BATO",
    "shares_per_contract": 100, "strike_price": 402,
    "ticker": "O:SPY240102C00402000", "underlying_ticker": "SPY"}
   ```
   `contract_type` is the string `"call"`/`"put"` — **must be mapped to `"C"`/`"P"`** before
   the row reaches `pick_strikes`/`leg_is_liquid` (which read `row["right"]` as `"C"`/`"P"`,
   `bot/options/strategy.py:176,191`). No `expired=false&as_of=<date>` combination was
   needed to be tested separately — `expired=true` + `expiration_date.gte/lte` bracketing
   the decision-day window is sufficient for D-07's per-day contract selection.

2. **`/v2/aggs/ticker/O:{ticker}/range/1/day/{from}/{to}`** — `[VERIFIED: massive.com API, live probe]` **is gated to roughly a trailing 24-month window on the current plan**, independent of the contracts-reference endpoint:
   - `O:SPY250620C00600000`, range `2025-05-01..2025-06-20` → `200`, 35 daily bars (fields: `v, vw, o, c, h, l, t, n` — **no `open_interest`, no bid/ask**, confirming D-11's "OHLC only" assumption).
   - Same ticker, range `2024-09-01..2024-09-05` → `200`, 3 bars.
   - Same ticker, range `2024-01-01..2024-03-01` → `403 NOT_AUTHORIZED: "Your plan doesn't include this data timeframe. Please upgrade your plan at https://polygon.io/pricing"`.
   - An earlier contract, range `2023-11-01..2024-01-02` → same `403`.
   - A 2019-expiry contract's aggs were not tested directly (contracts-reference call for that period succeeded; the aggs call was skipped to conserve API budget) but is very likely to also 403 given the pattern.
   - **Conclusion: option daily aggregates on this entitlement cover approximately the last 24 months from "today" (2026-08-17), i.e. roughly Aug 2024 onward.** This is a plan-tier limit, not a per-contract or per-underlying limit (not verified across all 15 ETFs, but Polygon-style plans gate by data age uniformly across tickers — TREAT AS HIGH confidence, not exhaustively re-verified per underlying).
   - `429` (rate limit) was hit on the very next request after four successful calls in quick succession — confirms `backtester/massive.py`'s existing `_get_json` 429-retry-with-backoff is necessary, not optional, and that the "free tier ~5 req/min" comment in `massive.py`'s docstring is realistic for a fast test loop (not just under sustained load).

3. Underlying-ticker daily aggregates (`/v2/aggs/ticker/SPY/range/1/day/…`, no `O:` prefix) are **already fully supported by the existing `MassiveDataSource.fetch_bars`/`cached_bars`** — no new code needed for the third D-05 endpoint; only the two options-specific endpoints (contracts reference, `O:` aggs) are new.

4. Rate-limit backoff: `_get_json` already retries `429` using the `Retry-After` header (falls back to a 15s sleep) — this is exactly what a bulk historical options fetch needs and requires zero changes.

## Architecture Patterns

### System Architecture Diagram

```
                        rules_options.json  (CFG-01, unchanged)
                                │
                                ▼
                    bot.options.config.load_options_config()
                                │
   --symbols/--start/--end/--set   │
   backtester/options_run.py ─────┤
                                │
                                ▼
                 backtester/options/data.py
   ┌─────────────────────────────────────────────────────┐
   │ Massive contracts reference (per underlying, ONE     │
   │ fetch spanning the whole [start,end] window, cached) │
   │           +                                          │
   │ Massive O:… daily aggs (per contract, cached)        │
   │           +                                          │
   │ Massive underlying daily aggs (existing fetch_bars)  │
   └─────────────────────────────────────────────────────┘
                                │  per decision-day t: only
                                │  contracts with a bar ≤ t (D-08)
                                ▼
                 backtester/options/greeks.py
   ┌─────────────────────────────────────────────────────┐
   │ Black-Scholes IV (bisection) + delta, from            │
   │ option close + underlying close + DTE + r             │
   │           +                                            │
   │ Daily ATM-IV series → 252-day IVR                     │
   └─────────────────────────────────────────────────────┘
                                │  rows shaped like live
                                │  screen_options output
                                ▼
                 backtester/options/engine.py  (daily loop)
   ┌─────────────────────────────────────────────────────┐
   │ for each trading day t (pandas_market_calendars):    │
   │   reconstruct u dict → passes_entry_gate (IMPORTED)  │
   │   pick_expiry / pick_strikes / size_position (IMPORTED)│
   │   portfolio caps in service.py's gate order (D-13)   │
   │   mark_spread / manage_decision for open positions   │
   │           (IMPORTED, unchanged)                      │
   │   expiry settlement at intrinsic vs underlying close │
   └─────────────────────────────────────────────────────┘
                                │  trade_log (options-shaped)
                                ▼
                 backtester/options/report glue
   ┌─────────────────────────────────────────────────────┐
   │ own trades.csv writer (D-16 columns) +               │
   │ imports report._sharpe_ratio/_sortino_ratio/         │
   │         _calmar_ratio/_win_loss_stats (NOT           │
   │         compute_metrics/write_report verbatim)       │
   └─────────────────────────────────────────────────────┘
                                │
                                ▼
        backtester/results/options/<run-id>/{trades.csv,summary.json,config.json}
```

A reader can trace SPY end-to-end: `options_run.py` loads config → `data.py` fetches/caches
contracts + bars for SPY → `engine.py` walks each trading day, feeding day-t-only rows
through the **unmodified** `bot/options/strategy.py` functions → closed/expired positions
become trade-log rows → report glue writes `trades.csv`/`summary.json`.

### Recommended Project Structure
```
backtester/
├── options_run.py         # CLI entry point (D-01); mirrors run.py's structure exactly:
│                           #   argparse, --set override parsing, config load, engine
│                           #   construction, report write. Composition root — no logic.
├── options/
│   ├── __init__.py
│   ├── data.py             # MassiveDataSource extension calls + per-day contract
│   │                        #   selection (D-05/D-06/D-07/D-08); OCC ticker parse/format
│   ├── greeks.py            # BS price/delta/IV (stdlib math, D-09); IVR series (D-10)
│   └── engine.py            # Daily replay loop, portfolio caps (D-13), fill model (D-11),
│                            #   manage/settlement (D-12), trade-log + metrics glue (D-16)
tests/backtester/options/
├── test_data.py             # cache round-trip, no-network, OCC ticker parse/format,
│                            #   look-ahead fixture (D-08)
├── test_greeks.py           # BS round-trip, delta monotonicity, IVR fixture rank
├── test_engine.py           # import-not-copy assertion (OBT-01), fill/settlement
│                            #   arithmetic, portfolio-cap gate order
└── test_options_run.py      # CLI --set override parsing, run-dir output shape
```
This is 4 new files under `backtester/` (options_run.py + 3 in `backtester/options/`) —
inside D-01's "≤5 modules" budget with one file to spare if a fifth (e.g. a dedicated
`report.py` under `backtester/options/`) turns out cleaner than folding report glue into
`engine.py`.

### Pattern 1: Import, don't copy (precedent already in this repo)
**What:** `backtester/harness.py` already does this for the equity strategy —
`from bot.strategy.trend_join_long import TrendJoinLong` and
`from bot.position.manager import PositionManager, get_force_close_time_et` are imported
directly, never reimplemented. `tests/backtester/test_harness.py` proves the harness "reuses
`bot.scanner.scanner._evaluate_symbol` / `_compute_tod_baselines`" by asserting behavior
against the real functions, not a parallel copy.
**When to use:** Exactly OBT-01 — import all 9 named functions from `bot.options.strategy`
plus `bot.options.config.load_options_config`.
**Example:**
```python
# backtester/options/engine.py — Source: bot/options/strategy.py (D-02)
from bot.options.strategy import (
    is_monthly_expiry, option_dte, pick_expiry, passes_entry_gate,
    leg_is_liquid, pick_strikes, size_position, mark_spread, manage_decision,
)
from bot.options.config import load_options_config, OptionsConfig
```
A dedicated test (per D-02/D-18) should assert these are literally the same function objects
as `bot.options.strategy`'s (e.g. `backtester.options.engine.pick_strikes is
bot.options.strategy.pick_strikes`), which is a much stronger "not copied" proof than a
behavioral test alone.

### Pattern 2: Row-dict shape parity with the live gateway
**What:** `bot/options/strategy.py`'s functions read specific dict keys, verified by reading
the source (line numbers below) and the strategy's own test fixtures
(`tests/options/test_strategy.py::_grid`, lines 60-71):

| Function | Reads (from source) | Exact keys |
|---|---|---|
| `pick_strikes` rows | `bot/options/strategy.py:141-215` | `code`, `right` (`"C"`/`"P"`), `strike`, `delta`, `bid`, `ask`, `open_interest` |
| `leg_is_liquid` | `bot/options/strategy.py:118-138` | `bid`, `ask`, `open_interest` (per-row) |
| `_leg` (internal, called by `pick_strikes`) | `bot/options/strategy.py:313-321` | projects `code`, `right`, `strike`, `side`, `mid` — `mid` computed from `bid`/`ask` via `_mid` (`308-310`) |
| `mark_spread` | `bot/options/strategy.py:245-261` | `legs`: `code`, `side`; `quotes`: `{code: {"bid","ask"}}` |
| `passes_entry_gate` | `bot/options/strategy.py:79-111` | `u` dict: `ivr_pct`, `ivp_pct`, `change_pct` — **already in 0-100 percent units**, the fraction→percent ×100 conversion is the CALLER's job (done live in `bot/options/service.py:85-115`, functions `_ivr_pct`/`_ivp_pct`/`_change_pct`) |

The live gateway (`bot/gateway/gateway.py:701-805`, `screen_options`) produces exactly this
shape via `_normalise_option_row` (`gateway.py:273-...`): flattens the nested `underlying`
cell into `u_stock_id`/`u_price`/`u_iv`/`u_iv_rank`/`u_iv_percentile`/`u_change_ratio`,
aliases `strike_price`→`strike`, derives `expiry`/`dte` from the option code
(`_expiry_from_option_code`, `gateway.py:264-270`) or `strike_date`. The live option **code**
format is `US.{ROOT}{YYMMDD}{C|P}{strike*1000:08d}` (e.g. `US.SPY260918P00450000`,
zero-padded 8-digit strike×1000 — confirmed by `_OPTION_CODE_EXPIRY_RE` at `gateway.py:261`
and by `tests/options/test_service.py`-adjacent fixture construction
`f"US.SPY260918{right}{int(strike * 1000):08d}"`). Massive's `O:` ticker format is
**structurally identical minus the `US.` vs `O:` prefix and dot separator**:
`O:{ROOT}{YYMMDD}{C|P}{strike*1000:08d}` (verified live, e.g. `O:SPY240102C00402000`).
This means the backtester's OCC ticker parser/formatter and the live gateway's option-code
parser are the same algorithm with a different prefix — worth writing as one small shared
regex-based helper (see §Code Examples) rather than two.

### Pattern 3: Live entry-gate ordering to mirror (D-13)
`bot/options/service.py`'s `_job_entry_scan` (`467-483`) → `_scan_and_open` (`485-540`) run,
in this exact order:
1. `not is_trading_day(today)` → skip (backtester: trivially true, only trading days are
   replayed)
2. `not self._entries_enabled` → skip (N/A offline — no readiness gate concept)
3. `self._kill_switch.triggered` → skip (N/A offline)
4. `await self._check_daily_breaker(today, 0.0)` (`796-824`) — realized-only daily-loss check
   FIRST, before the per-day cap, so a breaker trip on a bad morning blocks the rest of that
   day even on restart
5. `self._store.get_meta(_BREAKER_META_KEY) == today.isoformat()` → skip (breaker already
   tripped today)
6. `opened_today >= cfg.max_new_positions_per_day` → skip whole scan
7. Inside `_scan_and_open`'s per-underlying loop (`515-541`):
   a. `break` if `opened_today >= max_new_positions_per_day` **or**
      `open_count >= max_concurrent_positions`
   b. `continue` if `code in busy` (already has an active position — one-per-underlying)
   c. `passes_entry_gate` → `pick_expiry` → `pick_strikes` → `size_position` (which itself
      folds the BP cap via `open_max_loss_total` vs `cfg.max_bp_usage_pct`, `strategy.py:222-238`)

**Important gap:** live iterates underlyings via
`sorted(_group_rows_by_underlying(rows).items())` (`service.py:515`), which sorts by an
opaque broker `stock_id` integer — **meaningless and unreproducible offline** (the backtester
has no `stock_id` concept). Recommend the backtester iterate underlyings in a **deterministic,
documented** order (e.g. `sorted(symbols)` by ticker string) and note this as an intentional
divergence from live (same class of documented harness deviation as
`backtester/harness.py`'s several `T-06-xx` notes) — not a "no invented caps" violation since
D-13 is about the caps/thresholds, not encounter order, but iteration order does affect *which*
underlyings get capacity on a day when the concurrent-position cap binds, so it must be
picked and documented, not left accidental.

### Pattern 4: Reusing report.py's math without its P&L assumption
```python
# backtester/options/engine.py (or a small report-glue module)
# Source: backtester/report.py — importing the module-private ratio helpers,
# NOT compute_metrics/write_report (see Pitfall #2 — those assume per-share stock P&L).
from backtester.report import _sharpe_ratio, _sortino_ratio, _calmar_ratio, _win_loss_stats

def build_options_equity_curve(trades: list, starting_capital: float, start: str, end: str):
    """trades: options trade-log dicts with 'pnl_usd' and 'closed_date' (D-16 shape).
    Same NYSE-day-walk convention as backtester.report.build_equity_curve, but the
    per-trade dollar P&L comes from the caller (credit-spread math), not _net_pnl."""
    import pandas_market_calendars as mcal
    from collections import defaultdict
    nyse = mcal.get_calendar("NYSE")
    pnl_by_day = defaultdict(float)
    for t in trades:
        pnl_by_day[t["closed_date"]] += t["pnl_usd"]
    days = [d.strftime("%Y-%m-%d") for d in nyse.valid_days(start_date=start, end_date=end)]
    curve, equity = [], float(starting_capital)
    for day in days:
        equity += pnl_by_day.get(day, 0.0)
        curve.append((day, equity))
    return curve
```

### Anti-Patterns to Avoid
- **Calling `backtester.report.compute_metrics()`/`write_report()` directly on options
  trades:** silently computes wrong dollar P&L because `_net_pnl` assumes
  `(exit_price - entry_price) * quantity` semantics for a single long stock position, not a
  multi-leg credit spread. Reuse only the ratio functions (Pattern 4).
- **Fetching the contracts-reference endpoint once per decision day:** the endpoint is a
  reference list (which contracts existed / when they expired), not a time series — fetch it
  ONCE per underlying for the whole `[start, end]` window (using `expired=true` and
  `expired=false` unioned, or `as_of` bracketing) and slice in memory per day. Fetching it
  per-day multiplies API calls by the number of trading days for no benefit and risks hitting
  the ~5 req/min rate limit on every run.
- **Assuming `open_interest`/`volume` are meaningful liquidity signals in the backtest:**
  Massive's `O:` daily aggs have no `open_interest` field at all, and `v` (volume) is real
  but does not mean what live OI means. D-11 already documents "OI/volume from aggregates `v`
  when present else pass" as a limitation — do not silently upgrade this to "OI gate is
  enforced" language in the results doc.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Expiry/strike/sizing/manage decisions | A second copy of the strategy logic | `bot.options.strategy.*` (imported, D-02) | Already pure, already tested; a second copy is exactly the live/backtest divergence risk `strategy.py`'s own docstring calls out ("this is a hard contract... any hidden state would silently diverge live-vs-backtest results") |
| NYSE trading-day stepping / holiday calendar | A hand-rolled date-skip loop | `pandas_market_calendars.get_calendar("NYSE")` (already imported 3+ places) | Half-days and holidays are easy to get subtly wrong; the project already standardized on this library |
| Sharpe/Sortino/Calmar/win-rate math | A second implementation for options | `backtester.report._sharpe_ratio` / `_sortino_ratio` / `_calmar_ratio` / `_win_loss_stats` (import explicitly) | D-16 says so directly; these functions take a plain curve/pnl-list and have zero equity-specific assumptions baked in — only `_net_pnl`/`compute_metrics`/`build_equity_curve`'s callers do |
| HTTP + auth + 429 backoff + cache-file convention | A parallel Massive client | Extend `backtester.massive.MassiveDataSource` with 2 new methods | `_get_json` already handles `Authorization: Bearer`, pagination, and 429 retry correctly (verified live) |
| Black-Scholes N(x) | A hand-rolled polynomial approximation of the normal CDF | `math.erf` (`0.5 * (1 + math.erf(x / sqrt(2)))`) | Exact (to float precision), stdlib, no numerical-approximation error budget to reason about |

**Key insight:** the strategy-reuse half of this phase (OBT-01) is close to zero-risk because
`bot/options/strategy.py` was written in Phase 8 with Phase 9 explicitly in mind (its own
module docstring says so). The real engineering risk is entirely in the data layer (Massive
entitlement limits, just discovered) and in not accidentally reusing report.py's
equity-shaped P&L math for a fundamentally different instrument.

## Common Pitfalls

### Pitfall 1: Assuming Massive option aggregates have the same history depth as the contracts reference
**What goes wrong:** A multi-year IS/OOS hypothesis design (e.g. "IS 2021-2023, OOS
2024-2025", mirroring the equity Phase 6 style) silently fails on the option-aggs endpoint
partway through backfilling, or (worse) the researcher only tests the endpoint against a
recent date, believes it "works," and only discovers the ~24-month ceiling mid-implementation.
**Why it happens:** The two endpoints are governed by *different* entitlement rules on the
same plan — contracts reference is a lightweight reference/metadata list (cheap to serve
indefinitely); the aggregates endpoint is the actual paid tick/bar data (gated by plan tier).
**How to avoid:** Confirmed live 2026-08-17: aggs for a contract with July 2024 data succeed,
January 2024 data 403s. Design the D-14 hypotheses doc's IS/OOS window entirely inside
roughly `[today - 24mo, today]`. Re-verify the exact cutoff date (not just "somewhere between
Jan and Sept 2024") during planning/Wave 0 with 1-2 more probes, since it may be a rolling
window (moves forward with "today") rather than a fixed date.
**Warning signs:** Any `403 NOT_AUTHORIZED` with `"Your plan doesn't include this data
timeframe"` in the message — this is Massive's explicit signal, not an auth/key problem;
`MassiveApiError` already carries the HTTP code and can be pattern-matched on.

### Pitfall 2: Reusing `backtester/report.py`'s public API verbatim
**What goes wrong:** `compute_metrics(trades, ...)` computes `_net_pnl` as
`(exit_price - entry_price) * quantity - commission * quantity * 2`. Fed an options
trade shaped with `entry_price`=credit received and `exit_price`=cost to close, this produces
a number that happens to run without error but is not the strategy's actual dollar P&L
(wrong sign convention for a credit position, wrong multiplier — no ×100 contract multiplier,
wrong `quantity` semantics if `quantity` means "number of spreads" not "number of shares").
**Why it happens:** `report.py`'s docstring itself calls this "report-layer only" and
documents its assumptions for the *equity* backtester; nothing in that file signals it is
unsafe for options.
**How to avoid:** Compute options P&L in `backtester/options/engine.py` directly (the formula
already exists, verified working, in `bot/options/service.py:770-776`:
`net_exit = sum(SELL exits) - sum(BUY exits); realized = (credit - net_exit) * 100 * qty` —
mirror this exactly for consistency with live), then feed only a bare `[(date, equity)]`
curve and a bare `[pnl, ...]` list into the imported ratio helpers.
**Warning signs:** A `trades.csv` where `entry_price`/`exit_price` columns don't obviously
mean "credit received"/"cost to close" — this is the tell that the equity-shaped writer was
reused unmodified.

### Pitfall 3: Live's underlying-iteration order (`sorted(stock_id)`) has no offline analog
**What goes wrong:** Silently picking `list(rows)` iteration order (dict insertion order,
whatever Massive happened to return) makes the backtest's outcome non-deterministic across
runs / non-reproducible when the fixture/cache changes, and specifically affects *which*
underlyings get filled first on a day when `max_concurrent_positions` or
`max_new_positions_per_day` binds.
**Why it happens:** Live's `stock_id` sort key is a broker-internal integer that exists only
because `get_stock_ids()` resolved it; the backtester never resolves stock_ids.
**How to avoid:** Pick a documented deterministic order (`sorted(symbols)` by ticker string is
simplest) and state the divergence from live explicitly in the engine's module docstring
(matches the project's existing convention of documenting harness deviations, e.g.
`backtester/harness.py`'s several `T-06-xx`/`CR-0x` notes).
**Warning signs:** Two runs of the same backtest with the same cache produce different trade
counts/order — a correctness bug either way, but doubly so if it's from unstated iteration
order.

### Pitfall 4: The IVR formula in D-10 is ambiguous between "percentile rank" and "min-max normalized"
**What goes wrong:** D-10's literal text — "IVR = rank within trailing 252 trading days ×100"
— can be implemented as either (a) a percentile rank (what fraction of the trailing 252 days
had a lower ATM-IV than today), which is closer to industry "IV Percentile", or (b) the
classic tastytrade/broker "IV Rank" = `(IV_today - min(IV_252d)) / (max(IV_252d) -
min(IV_252d)) × 100` (min-max normalized). These produce materially different numbers on the
same data and will pass different underlyings through `entry.ivr_min=30`.
**Why it happens:** "Rank" is colloquially used for both in options literature; the design
doc's provenance is tastylive transcripts, which use the min-max-normalized convention almost
universally (it's the industry-standard "IV Rank" as popularized by tastytrade/thinkorswim).
**How to avoid:** Recommend implementing the min-max-normalized formula (industry-standard IV
Rank, matches what moomoo's `OptUnderlyingIndicator.IV_RANK` almost certainly also computes,
though this was not independently verified against moomoo's own docs — flagged `[ASSUMED]`
below) rather than a percentile rank, and state the exact formula in the hypotheses doc so H1
(IVR 20 vs 30) is reproducible. This should be confirmed as a locked decision during planning,
not left implicit in code.
**Warning signs:** If SPY's IVR from the backtester never crosses 30 despite periods of
obviously elevated IV (e.g. a volatility spike), a percentile-rank implementation is likely
the (wrong) one in use — min-max normalization is far more sensitive to a single high-IV
day inside the window.

### Pitfall 5: Deep ITM/near-expiry/stale-price contracts have no valid implied vol
**What goes wrong:** A bisection IV solver crashes or returns garbage when the observed
option close is below intrinsic value (common for illiquid far-ITM contracts with a stale
last-trade price) or when DTE is 0/negative (an expiring or already-expired contract's "close"
on its last trading day may reflect intrinsic-only pricing with no time value left to solve
for σ).
**Why it happens:** Black-Scholes price is a monotonically increasing function of σ only
within the bracket `[intrinsic, ∞)`; a price below intrinsic has no solution, and the
function is degenerate at `T=0`.
**How to avoid:** Fail closed (mirrors the strategy's own convention throughout
`bot/options/strategy.py` — e.g. `_as_float` returning `0.0` on garbage, `leg_is_liquid`
rejecting `bid <= 0`): return `None` for IV/delta when `price < intrinsic` or `T <= 0`, and
exclude that contract from that day's `pick_strikes` candidate rows rather than propagating
a `NaN`/exception into the strategy layer.
**Warning signs:** `pick_strikes` silently returning `None` far more often for near-the-money,
near-expiry strikes than expected — check whether it's the IV solver failing closed correctly
vs. a bug elsewhere.

### Pitfall 6: A missing `O:` aggregate bar for a day likely means "no trade that day," not "carry forward the prior close"
**What goes wrong:** Treating an absent bar as "use yesterday's close" fabricates price
history for illiquid far-OTM strikes that may not trade for days/weeks, which would corrupt
both the day-t contract-selection filter (D-07 already says "only contracts with a bar ON
that day" — correct) and the daily ATM-IV series if the ATM-strike-selection logic ever
falls back to a non-traded strike's stale close.
**Why it happens:** Polygon-family aggregate APIs (which Massive is built on/compatible with)
are trade-based: a day with zero trades simply produces no row, not a zero-volume row with a
carried-forward close. This is standard behavior for this API family but was not
independently re-verified against Massive's own docs in this session — treat as `[ASSUMED]`
(commonly true for Polygon-compatible aggs APIs; cross-check during Wave 0 by pulling a known
illiquid far-OTM contract and confirming days with `v: 0`/missing rows behave as expected,
rather than assuming).
**How to avoid:** D-07 is already correctly specified — implement contract selection as "must
have a bar dated exactly `t`," never "most recent bar at or before `t`."
**Warning signs:** ATM-IV series with suspiciously smooth day-to-day values on an illiquid
underlying — a sign a carry-forward crept in somewhere.

### Pitfall 7: Rate limiting (429) is real and immediate, not theoretical
**What goes wrong:** A naive full-universe (15 ETFs), multi-year backfill issuing one
contracts-reference call + N per-contract aggs calls with no delay hits 429 within the first
handful of requests (observed directly in this session: 4 successful calls, then an
immediate 429 on the 5th).
**Why it happens:** Free/basic Massive tier is ~5 req/min (documented in
`backtester/massive.py`'s own docstring, now independently confirmed).
**How to avoid:** D-06's mandatory on-disk cache mitigates this for *repeat* runs, but the
*first* full-universe multi-year run will still need to survive many 429s during the initial
backfill — `_get_json`'s existing retry-with-backoff handles this correctness-wise, but the
first cold run of the full 15-ETF universe across 24 months could take a long time
wall-clock. Recommend the first real run really is SPY-only (per D-04) before scaling out,
and that Wave 0 tests exercise the 429-retry path against a mock (already the pattern in
`tests/backtester/test_massive.py::test_429_retries_then_succeeds`) rather than relying on
hitting the live limit.
**Warning signs:** A backfill run that appears to hang — likely sleeping through a
Retry-After-driven backoff, not actually stuck.

## Code Examples

### Black-Scholes price/delta/IV (stdlib `math` only, D-09)
```python
# backtester/options/greeks.py — no external source; standard BS formulas,
# bisection root-find chosen because BS price is strictly monotonic in sigma
# for T>0 (vega > 0 always), so bisection is guaranteed to converge given a
# valid bracket, with no need for a vega-based Newton step.
import math
from datetime import date

_SQRT_2PI = math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, t_years, r, sigma, right, q=0.0):
    """European BS price. right: 'C' or 'P'. Returns intrinsic value at t<=0 or sigma<=0
    (fails closed to a defined number, never raises, matching strategy.py convention)."""
    if t_years <= 0 or sigma <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "C":
        return spot * math.exp(-q * t_years) * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * math.exp(-q * t_years) * _norm_cdf(-d1)


def bs_delta(spot, strike, t_years, r, sigma, right, q=0.0):
    if t_years <= 0 or sigma <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    disc = math.exp(-q * t_years)
    return disc * _norm_cdf(d1) if right == "C" else disc * (_norm_cdf(d1) - 1.0)


def implied_vol(price, spot, strike, t_years, r, right, q=0.0,
                 lo=1e-4, hi=5.0, tol=1e-6, max_iter=100):
    """Bisection IV solve. Returns None (fail closed) when price is below intrinsic,
    t_years<=0, or no root exists in [lo, hi] sigma."""
    intrinsic = max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    if t_years <= 0 or price < intrinsic - 1e-9:
        return None
    f_lo = bs_price(spot, strike, t_years, r, lo, right, q) - price
    f_hi = bs_price(spot, strike, t_years, r, hi, right, q) - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = bs_price(spot, strike, t_years, r, mid, right, q) - price
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def dte_to_years(expiry: date, today: date) -> float:
    """Matches bot.options.strategy.option_dte's calendar-day convention (/365.25)."""
    return max((expiry - today).days, 0) / 365.25
```

### OCC-style option ticker parse/format (shared with the live gateway's algorithm, Pattern 2)
```python
# backtester/options/data.py — algorithm mirrors bot/gateway/gateway.py's
# _expiry_from_option_code / _OPTION_CODE_EXPIRY_RE, adapted for Massive's "O:" prefix
# instead of moomoo's "US." prefix. Verified live against Massive's real tickers
# (e.g. "O:SPY240102C00402000").
import re
from datetime import date

_MASSIVE_TICKER_RE = re.compile(r"^O:([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_massive_ticker(ticker: str) -> dict:
    m = _MASSIVE_TICKER_RE.match(ticker)
    if not m:
        raise ValueError(f"unparseable Massive option ticker: {ticker!r}")
    root, yy, mm, dd, right, strike8 = m.groups()
    return {
        "root": root,
        "expiry": date(2000 + int(yy), int(mm), int(dd)),
        "right": right,
        "strike": int(strike8) / 1000.0,
    }


def format_massive_ticker(root: str, expiry: date, right: str, strike: float) -> str:
    return f"O:{root}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"
```

### Fill model + expiry settlement (D-11/D-12)
```python
# backtester/options/engine.py — mirrors bot/options/service.py's realized-P&L formula
# (service.py:770-776) exactly, so backtest and live compute P&L the same way.
def synthesize_bid_ask(close: float, spread_pct: float) -> tuple:
    """D-11: no bid/ask in Massive aggs — synthesize close ± spread_pct/2 so
    leg_is_liquid() can run unmodified."""
    half = close * spread_pct / 200.0
    return max(close - half, 0.01), close + half


def leg_fill_price(mid: float, side: str, slippage_usd: float) -> float:
    """Adverse slippage: paying more to BUY, receiving less to SELL (matches
    backtester/execution.py's SimulatedExecution adverse-slippage convention)."""
    return mid + slippage_usd if side == "BUY" else mid - slippage_usd


def settle_at_expiry(legs: list, underlying_close: float) -> float:
    """Intrinsic-value settlement (D-12). legs: {"right","strike","side"}.
    Returns net cost to close (same sign convention as mark_spread's SELL-minus-BUY)."""
    total = 0.0
    for leg in legs:
        intrinsic = (max(underlying_close - leg["strike"], 0.0) if leg["right"] == "C"
                     else max(leg["strike"] - underlying_close, 0.0))
        total += intrinsic if leg["side"] == "SELL" else -intrinsic
    return total
```

## State of the Art

| Old assumption | Current reality (verified 2026-08-17) | When discovered | Impact |
|--------------|------------------|--------------|--------|
| "Massive entitled to options contracts reference + option daily aggregates (200)" (design doc, Phase 8) | Both endpoints return 200 for *recent* data, but option daily aggregates are gated to ~24 months of history while contracts reference is not | This research session | D-14's IS/OOS window design must be re-scoped to fit inside ~24 months, not the multi-year window the equity Phase 6 backtester enjoys |

No other "old vs current" framework/library shifts apply — this phase uses only stdlib and
already-pinned dependencies with no version drift concerns.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | IVR should be implemented as min-max normalized (industry "IV Rank"), not a percentile rank, despite D-10's literal "rank" wording | Common Pitfalls #4 | H1 (IVR 20 vs 30) tests a different quantity than the live bot's own IVR gate; a SUPPORTED/REJECTED verdict could be an artifact of the formula choice, not the threshold |
| A2 | Missing Massive `O:` aggregate bars mean "no trade that day," not a carry-forward of the prior close | Common Pitfalls #6 | If wrong, illiquid-strike day-selection (D-07) and the ATM-IV series could both be corrupted by fabricated price history |
| A3 | The ~24-month aggregates cutoff is a uniform plan-tier limit across all 15 universe ETFs, not just SPY (only SPY was live-probed) | Massive API — Verified Facts item 2 | If a less-liquid ETF has a *tighter* effective window (e.g. thinner contract listings near the cutoff), the multi-underlying IS/OOS design in D-14 could have gaps that need per-underlying confirmation before the hypotheses doc is finalized |
| A4 | moomoo's live `OptUnderlyingIndicator.IV_RANK` uses the same min-max-normalized convention this research recommends (A1) | Common Pitfalls #4 | Not independently verified against moomoo SDK docs in this session; if moomoo's live IVR is actually a percentile, "matching the live iv_rank fraction→percent convention" (D-10) would be satisfied on units but not on formula |

## Open Questions

1. **Exact Massive option-aggregates history cutoff date, per underlying**
   - What we know: SPY aggs work for Sept 2024+, 403 for Jan-Mar 2024 and Nov 2023-Jan 2024 (live-verified). Contracts reference itself has no such restriction (works back to 2019).
   - What's unclear: The exact day the cutoff falls on (only bracketed to a ~8-month window), whether it's a fixed date or rolls forward with "today," and whether all 15 universe ETFs share the identical cutoff.
   - Recommendation: Wave 0 of the plan should do a small, budgeted (2-3 requests) binary search per a couple of representative underlyings (SPY + one lower-liquidity name like GDX or USO) to pin the exact boundary before the hypotheses doc (D-14) commits to specific IS/OOS dates.

2. **IS/OOS window size given the ~24-month ceiling**
   - What we know: D-14 requires ≥25 closed trades per arm, in BOTH an IS and an OOS window, for a 45-DTE strategy (so each position ties up capital for 30-60+ days).
   - What's unclear: Whether a ~12-month IS / ~12-month OOS split (the natural 50/50 cut of a 24-month history) produces enough trades across the SPY-first, then-multi-ETF universe to clear the evidence floor in both windows, especially per-hypothesis-arm (H1/H2/H3 each need their own ≥25/arm, and running the SAME data through 3 separate hypothesis backtests doesn't create more independent trades).
   - Recommendation: The planner/researcher-of-hypotheses (D-14 is written by "the researcher" at hypothesis-registration time, not this phase) should do a rough trade-count estimate (universe size × entries/year/underlying at the historical average IVR gate pass rate) before committing to a specific IS/OOS split, and be prepared to widen the universe (D-04's "first SPY, then QQQ/IWM/TLT/GLD/XLE") sooner rather than later if 12/12 months proves too thin.

3. **Does Massive expose a grouped/batch daily options endpoint that would collapse per-contract calls?**
   - What we know: D-05 locks in the per-contract `O:…` aggs endpoint as the data source; this was not challenged in this research (out of scope to change a locked decision), but Polygon-family APIs sometimes expose a "grouped daily" endpoint (`/v2/aggs/grouped/locale/us/market/options/{date}`) that returns all contracts' bars for one day in one call, which could drastically cut request volume for a multi-year, multi-underlying backfill.
   - What's unclear: Whether this endpoint exists on Massive, and whether it's in-entitlement — was NOT probed in this session to conserve the request budget.
   - Recommendation: Not required for Phase 9 to work (per-contract calls + caching is sufficient, just slower on first run) — worth a 1-request probe during Wave 0 if the first full-universe backfill proves impractically slow, but do not block planning on this.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `MASSIVE_API_KEY` | OBT-02 (all Massive fetches) | Yes — present in `.env`, live-verified this session | — | None needed; already the only data source D-05 specifies |
| Network access to `api.massive.com` | OBT-02 | Yes — confirmed via live probes in this session | — | On-disk cache (D-06) makes subsequent runs network-free |
| `pandas`, `numpy`, `jsonschema`, `pandas-market-calendars` | Throughout | Yes — pinned in `requirements.txt`, already installed for the equity backtester | 2.x/2.5.0/4.x/5.4.0 | — |
| `math` (stdlib) | OBT-03 | Yes | stdlib | — |

No missing dependencies. No fallback strategy needed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 1.4.0 (already the project standard; `tests/backtester/`, `tests/options/` both use it) |
| Config file | none dedicated — project-root `pytest.ini`/`setup.cfg` conventions already govern `tests/backtester/options/` the same as `tests/backtester/` |
| Quick run command | `pytest tests/backtester/options/ -x -q` |
| Full suite command | `pytest -q` (965 tests baseline as of `b36af60`, per D-18) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OBT-01 | Backtester imports `bot/options/strategy.py` functions unchanged (identity check, not just behavioral) | unit | `pytest tests/backtester/options/test_engine.py::test_imports_not_copies -x` | ❌ Wave 0 |
| OBT-02 | Massive contracts + `O:` aggs fetch/cache, no-network in tests, no-look-ahead | unit | `pytest tests/backtester/options/test_data.py -x` | ❌ Wave 0 |
| OBT-03 | BS price→IV→price round-trip within 1e-4; delta monotonic in strike | unit | `pytest tests/backtester/options/test_greeks.py::test_iv_roundtrip test_greeks.py::test_delta_monotonic -x` | ❌ Wave 0 |
| OBT-04 | IVR matches hand-computed rank on a fixture | unit | `pytest tests/backtester/options/test_greeks.py::test_ivr_fixture -x` | ❌ Wave 0 |
| OBT-05 | Fill/settlement arithmetic on a hand-built 2-leg example | unit | `pytest tests/backtester/options/test_engine.py::test_fill_and_settlement -x` | ❌ Wave 0 |
| OBT-06 | Hypotheses doc exists and is committed before first real-data run | manual-only (git-history check, not a pytest assertion) | `git log --follow --diff-filter=A -- 'docs/research/*options-backtest-hypotheses.md'` predates the first real-data results commit | ❌ Wave 0 (doc, not code) |
| OBT-07 | Trade-log/summary output shape (D-16 columns), reused ratio math produces sane values on a fixture | unit | `pytest tests/backtester/options/test_engine.py::test_report_output_shape -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/backtester/options/ -x -q`
- **Per wave merge:** `pytest -q` (full suite)
- **Phase gate:** Full suite green before `/gsd-verify-work`; additionally, the first
  real-Massive-data run must happen only AFTER `docs/research/2026-MM-DD-options-backtest-hypotheses.md`
  is committed (D-14 — a process check, not a pytest check; verify via `git log`)

### Wave 0 Gaps
- [ ] `tests/backtester/options/__init__.py`, `conftest.py` (option-chain-grid fixture, likely
      adapted from `tests/options/test_strategy.py`'s `_grid` helper for row-shape consistency)
- [ ] `tests/backtester/options/test_data.py` — no-network Massive-mock pattern, copy
      `tests/backtester/test_massive.py`'s `monkeypatch.setattr(src, "_get_json", ...)` style
- [ ] `tests/backtester/options/test_greeks.py`
- [ ] `tests/backtester/options/test_engine.py`
- [ ] `tests/backtester/options/test_options_run.py`
- [ ] `backtester/options/__init__.py`, `data.py`, `greeks.py`, `engine.py`,
      `backtester/options_run.py` — none exist yet (this is Wave 0 of the phase itself)

## Security Domain

Not applicable — `security_enforcement` is not referenced in `.planning/config.json` for this
project (no ASVS-relevant surface: no auth, no user input beyond CLI args, no network-facing
service; the only "credential" is `MASSIVE_API_KEY`, already handled correctly by
`backtester/massive.py`'s existing `Authorization: Bearer` header pattern — never a URL query
param, never logged). No new threat surface is introduced by this phase.

## Project Constraints (from CLAUDE.md)

- Python 3.6+, reuse existing `moomoo-api` SDK / project modules — no rewrite. This phase adds
  zero broker access; it is provably network-only-to-Massive, matching the equity backtester's
  "never constructs a live broker gateway" invariant (`backtester/run.py`'s own docstring).
- `rules_options.json` is the single source of truth (CFG-01) — the backtester must read it
  via `bot/options/config.py`'s `load_options_config`, never hand-parse or duplicate schema
  validation.
- Timezone: US Eastern for all trading-day/DTE semantics — reuse `bot.safety.et_helpers.ET`
  and `bot.scanner.calendar` rather than a new date-math module.
- GSD workflow enforcement: this phase's implementation must go through `/gsd-execute-phase`,
  not direct edits — not a research-content concern, noted for completeness.
- `LIMIT orders only` / `long wings before shorts on open, shorts first on close` invariants
  (Phase 8 execution-layer rules) are **not applicable offline** — the backtester never places
  orders; it only replays the pure decision functions and computes fill prices directly. Do
  not build an order-simulation layer that re-derives these execution invariants; they're
  properties of `LegExecutor` (untouched, un-imported by this phase).

## Sources

### Primary (HIGH confidence)
- Live API probes against `https://api.massive.com` using the repo's real `MASSIVE_API_KEY`,
  this session, 2026-08-17 — `/v3/reference/options/contracts` (multiple `expired=true/false`
  + date-range combinations) and `/v2/aggs/ticker/O:…/range/1/day/…` (5 distinct
  ticker/range combinations bracketing the ~24-month cutoff)
- `bot/options/strategy.py` (full file read) — exact function signatures, docstrings, and
  dict-key contracts
- `bot/options/config.py`, `bot/options/schema.py`, `rules_options.json` — config shape
- `bot/options/service.py` (full file read) — live entry-gate order, fraction→percent
  conversion functions, realized-P&L formula
- `bot/gateway/gateway.py` (`get_stock_ids`, `screen_options`, `get_option_positions`,
  `_normalise_option_row`, `_expiry_from_option_code`, `_OPTION_CODE_EXPIRY_RE`) — live row
  shape and option code format
- `backtester/massive.py`, `backtester/report.py`, `backtester/harness.py`, `backtester/run.py`,
  `backtester/compare.py`, `backtester/execution.py`, `backtester/README.md` (all read in full)
- `tests/backtester/test_massive.py`, `tests/backtester/fixtures.py`,
  `tests/backtester/test_harness.py`, `tests/options/conftest.py`, `tests/options/test_strategy.py`
  (read for existing test patterns and row-shape fixtures)
- `.planning/phases/09-options-backtester/09-CONTEXT.md`, `09-PRD.md`,
  `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`
- `requirements.txt` — confirms no scipy, confirms pinned versions

### Secondary (MEDIUM confidence)
- `~/.claude/plans/scrape-highly-rated-options-velvety-naur.md` §Follow-ups and Decisions
  table — the original "Massive entitled to contracts + option daily aggregates (200)" check,
  which this research found to be incomplete (did not test timeframe depth)

### Tertiary (LOW confidence)
- Assumption that missing `O:` aggregate rows mean "no trade" rather than a gap needing
  carry-forward (A2) — inferred from general Polygon-family API conventions, not verified
  against Massive's own documentation in this session
- Assumption that moomoo's live `IV_RANK` indicator is min-max normalized like tastytrade's
  (A4) — inferred from the strategy's tastylive provenance, not verified against moomoo SDK
  docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every dependency already installed/pinned, verified via
  `requirements.txt` and direct source reads
- Massive data-layer facts: HIGH — live-verified against the real API this session, not
  training data
- Strategy-reuse pattern (OBT-01): HIGH — read the actual pure-function source, confirmed
  zero I/O, confirmed an existing import-not-copy precedent (`backtester/harness.py`)
- Architecture (engine/report-glue design): MEDIUM-HIGH — grounded in reading
  `backtester/report.py`'s actual formulas (found the P&L-shape mismatch empirically, not by
  assumption), but the engine module itself is new design, not verified against a working
  implementation
- IVR semantics (OBT-04): MEDIUM — the min-max-normalization recommendation is well-grounded
  in tastytrade/industry convention but not independently confirmed against moomoo's own
  `IV_RANK` documentation; flagged as A1/A4 for planner/user confirmation
- Pitfalls: HIGH for data-layer pitfalls (empirically discovered), MEDIUM for greeks/IV edge
  cases (standard numerical-methods reasoning, not empirically triggered against real illiquid
  Massive data in this session)

**Research date:** 2026-08-17
**Valid until:** ~30 days for the code-level findings (stable stdlib/pinned-deps facts); the
Massive entitlement/history-depth finding should be **re-verified at Wave 0 of implementation**
regardless of elapsed time, since it is plan-tier-dependent and could change with account/plan
changes independent of calendar time.
