# Phase 9 PRD — Options backtester (`tasty_credit_spreads`)

**Source of truth:** ROADMAP.md §Phase 9 (goal, OBT-01..07, success criteria) and the approved
Phase 8 design `~/.claude/plans/scrape-highly-rated-options-velvety-naur.md` §Follow-ups.
Everything below is a locked decision unless marked *discretion*.

## Purpose

Produce offline evidence for/against the Phase 8 strategy before it earns real capital. The
live bot's pure functions in `bot/options/strategy.py` are replayed unchanged over Massive
historical option data; a pre-registered hypothesis set is evaluated; `rules_options.json`
changes only if a hypothesis is SUPPORTED with out-of-sample confirmation.

## Locked decisions

### Scope / architecture
- D-01 New package `backtester/options/` (sibling of the equity backtester, NOT inside `bot/`). Entry point `python3 -m backtester.options_run --rules rules_options.json --symbols US.SPY,… --start YYYY-MM-DD --end YYYY-MM-DD [--out DIR]`. Ponytail: fewest files that work — target ≤5 modules (data, greeks, engine/harness, report glue, CLI).
- D-02 Import, don't copy: the backtester imports `pick_expiry`, `passes_entry_gate`, `pick_strikes`, `size_position`, `mark_spread`, `manage_decision`, `leg_is_liquid`, `is_monthly_expiry`, `option_dte` from `bot/options/strategy.py` and builds config via `bot/options/config.py` from `rules_options.json`. No strategy logic re-implemented; a test asserts the import (OBT-01).
- D-03 Daily resolution only. One decision point per trading day (the "10:00 ET entry scan" is modeled as that day's close-derived data being available — see D-08 for look-ahead rule). No intraday bars, no 14:30 second scan.
- D-04 Universe: same 15 ETFs as `rules_options.json.universe`; first real run on SPY only, then QQQ/IWM/TLT/GLD/XLE. Symbol list is a CLI arg.

### Data (OBT-02)
- D-05 Massive endpoints: `/v3/reference/options/contracts?underlying_ticker=SPY&expired=true|false&as_of=…` for the contract universe (paginated via `next_url`), `/v2/aggs/ticker/O:SPY…/range/1/day/{start}/{end}` for option daily bars, `/v2/aggs/ticker/SPY/range/1/day/…` for the underlying. Reuse `MassiveDataSource` in `backtester/massive.py` (`_get_json`, API key loading, cache dir convention `backtester/cache/massive/`); extend rather than fork.
- D-06 On-disk cache is mandatory (JSON/parquet per contract+range); a full SPY multi-year run must be re-runnable offline. Cache key includes endpoint + params. Rate-limit/back-off reuses whatever `_get_json` already does.
- D-07 Contract selection per decision day: only contracts with an aggregate bar ON the decision day, and expiries within `[min_dte, max_dte]` (monthly preference from `prefer_monthly`) — feed them to `pick_expiry` unchanged.
- D-08 No look-ahead: on decision day *t*, the strategy sees only bars with date ≤ *t*; the trade is opened at day *t*'s close-derived mid (D-11) — no bar after *t* is read for entry/strike/size decisions. Test with an ahead-only fixture (Phase 6 BT-02 pattern).

### Greeks / IV (OBT-03, OBT-04)
- D-09 Black-Scholes (European, continuous dividend yield q=0 default, r configurable, default 0.045). IV solved from option close by bracketed root-find (bisection/Brent from stdlib `math`, no scipy dependency added). Delta from that IV. Rows fed to `pick_strikes` carry `delta`, `bid`, `ask`, `open_interest`, `strike`, `expiry` etc. in the SAME dict shape the live gateway `screen_options` rows use — check `bot/gateway/gateway.py` and `bot/options/strategy.py::_leg/_mid` for the exact keys.
- D-10 IVR: for each underlying, build a daily ATM-IV series (nearest-to-target-DTE expiry, nearest-ATM strike, mean of call+put IV); IVR = rank of today's ATM-IV within the trailing 252-trading-day window, ×100, matching the live `iv_rank` fraction→percent convention. `passes_entry_gate` receives it in the same `u` dict shape as live. IVP optional (`ivp_min` is null in config → skip).

### Fill model / P&L (OBT-05)
- D-11 Bars carry OHLC only (no bid/ask); mid = close. Fill = mid ± `slippage_usd` per leg (default 0.02, CLI/config override), commissions per leg (default $0.65 + fees, configurable), 100 multiplier. Entry credit = sum(short mids) − sum(long mids) − slippage. Synthesize `bid`/`ask` for `leg_is_liquid` as close ± spread_pct/2 so the liquidity gate can run; OI/volume come from the aggregates when present (`v`), else treat as passing — document this limitation.
- D-12 Manage each open position daily with `mark_spread` + `manage_decision` (profit target 50%, 21-DTE exit, stop multiple if set). Expiry settlement at intrinsic vs the underlying's close on expiry day. Assignment guard mirrors live: close at `assignment_guard_dte`.
- D-13 Sizing uses `size_position` from live with `sizing_equity_usd`; portfolio caps (`max_concurrent_positions`, `max_new_positions_per_day`, `max_bp_usage_pct`, `daily_loss_limit_pct`) enforced by the engine exactly as `bot/options/service.py` does — read it, mirror the gate order, do not invent new caps.

### Hypotheses (OBT-06)
- D-14 `docs/research/2026-MM-DD-options-backtest-hypotheses.md` is written and committed BEFORE the first real-data run (git history proves ordering). Contents: H1 IVR gate 20 vs 30, H2 short delta 0.16 vs 0.20, H3 iron_condor vs put_credit_spread; evidence floor ≥25 closed trades per arm; in-sample window vs out-of-sample window declared up front (e.g. IS 2021-01-01..2023-12-31, OOS 2024-01-01..2025-12-31 — the researcher confirms what Massive history actually covers); metric of record = profit factor + Sortino, tie-break avg credit captured; a hypothesis is SUPPORTED only if it wins in BOTH IS and OOS and clears the floor.
- D-15 Variants are run by overriding rules keys through a `--set entry.ivr_min=20` style CLI override (or a variants JSON), never by editing `rules_options.json`; the runner records the effective config in the run output.

### Output (OBT-07)
- D-16 Per-run directory under `backtester/results/options/<run-id>/` with `trades.csv` (one row per closed position: symbol, structure, open/close dates, strikes, credit, exit reason, P&L $, max-loss, DTE at open/close), `summary.json` (win rate, PF, avg credit captured %, max DD $, Sortino, trades, per-symbol breakdown), and `config.json` (effective rules). Reuse `backtester/report.py` metric helpers where they fit; don't duplicate Sortino/DD math.
- D-17 Result doc `docs/research/…-options-backtest-results.md` reports each hypothesis SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with the numbers.

### Testing
- D-18 Tests under `tests/backtester/options/`: import-not-copy, look-ahead fixture, BS round-trip (price→IV→price within 1e-4) + delta monotonic in strike, IVR fixture rank, fill/settlement arithmetic on a hand-built 2-leg example, engine caps. Full suite must stay green (965 as of b36af60). No live network in tests (fixtures/mocks for Massive).

## Claude's discretion
- Exact module names inside `backtester/options/`; whether IVR series is cached alongside bars; parquet vs JSON cache format (prefer what `backtester/massive.py` already does).
- Handling of days with no bar for a leg (carry-forward last close vs skip manage that day) — pick one, document it.

## Out of scope / deferred
- Intraday bars, second entry scan, bid/ask-based fills (Massive chain snapshot is 403).
- Strangles/naked puts (need BP model), rolling, VIX-scaled BP ladder, earnings blackout — later knobs from the design doc.
- Any change to `bot/options/*` or `rules_options.json` inside this phase except a hypothesis-driven change AFTER results (separate commit, explicitly labeled).
