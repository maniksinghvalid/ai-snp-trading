# Phase 9: Options backtester - Context

**Gathered:** 2026-08-17
**Status:** Ready for planning
**Source:** PRD Express Path (.planning/phases/09-options-backtester/09-PRD.md)

<domain>
## Phase Boundary

Offline options backtester (`backtester/options/`) that replays the Phase 8 `tasty_credit_spreads`
pure functions from `bot/options/strategy.py` unchanged over Massive historical option data
(contracts reference + `O:…` daily aggregates), derives Black-Scholes IV/delta from closes, computes
IVR from its own rolling ATM-IV series, applies a mid±slippage fill model, and evaluates a
pre-registered hypothesis set (IVR 20 vs 30, 16Δ vs 20Δ, IC vs PCS). No change to `bot/options/*`
or `rules_options.json` inside the phase except a hypothesis-driven change AFTER results.

</domain>

<decisions>
## Implementation Decisions

### Scope / architecture
- D-01 New package `backtester/options/` (sibling of the equity backtester, not inside `bot/`); entry point `python3 -m backtester.options_run --rules rules_options.json --symbols … --start … --end … [--out DIR]`; ≤5 modules (data, greeks, engine, report glue, CLI)
- D-02 Import, don't copy: backtester imports `pick_expiry/passes_entry_gate/pick_strikes/size_position/mark_spread/manage_decision/leg_is_liquid/is_monthly_expiry/option_dte` from `bot/options/strategy.py` and builds config via `bot/options/config.py`; a test asserts the import (OBT-01)
- D-03 Daily resolution only — one decision point per trading day; no intraday bars, no 14:30 second scan
- D-04 Universe = `rules_options.json.universe` (15 ETFs); first real run SPY only, then QQQ/IWM/TLT/GLD/XLE; symbols are a CLI arg

### Data (OBT-02)
- D-05 Massive endpoints: `/v3/reference/options/contracts` (`expired=true|false`, `as_of`, paginate `next_url`), `/v2/aggs/ticker/O:…/range/1/day/…` option bars, `/v2/aggs/ticker/SPY/range/1/day/…` underlying; reuse/extend `MassiveDataSource` in `backtester/massive.py` (`_get_json`, key loading, `backtester/cache/massive/`), do not fork
- D-06 On-disk cache mandatory (per contract+range; key includes endpoint+params); full SPY multi-year run re-runnable offline; back-off reuses `_get_json`
- D-07 Per decision day: only contracts with a bar ON that day and expiry in `[min_dte, max_dte]` (monthly preference from `prefer_monthly`) are fed to `pick_expiry` unchanged
- D-08 No look-ahead: on day *t* the strategy sees only bars dated ≤ *t*; open at day *t* close-derived mid; ahead-only fixture test (Phase 6 BT-02 pattern)

### Greeks / IV (OBT-03, OBT-04)
- D-09 Black-Scholes European, q=0 default, r configurable (default 0.045); IV by bracketed root-find in stdlib `math` (no scipy); delta from that IV; rows to `pick_strikes` use the SAME dict keys as live `screen_options` rows (check `bot/gateway/gateway.py`, `strategy.py::_leg/_mid`)
- D-10 IVR: per-underlying daily ATM-IV series (nearest target-DTE expiry, nearest-ATM strike, mean call+put IV); IVR = min-max normalization over the trailing 252 trading days, `(iv − min)/(max − min) × 100` (the tastylive/moomoo IV Rank definition — NOT percentile rank; percentile = IVP, skipped because `ivp_min` is null); delivered to `passes_entry_gate` in the live `u` dict shape (percent, 0–100)

### Fill model / P&L (OBT-05)
- D-11 Mid = close (OHLC only); fill = mid ± `slippage_usd` per leg (default 0.02, overridable), per-leg commission (default $0.65+fees, configurable), ×100 multiplier; synthesize bid/ask as close ± spread_pct/2 for `leg_is_liquid`; OI/volume from aggregates `v` when present else pass — documented limitation
- D-12 Manage daily with `mark_spread` + `manage_decision` (50% target, 21-DTE, stop multiple if set); expiry settlement at intrinsic vs underlying close; assignment guard mirrors live `assignment_guard_dte`
- D-13 Sizing via live `size_position` with `sizing_equity_usd`; portfolio caps (`max_concurrent_positions`, `max_new_positions_per_day`, `max_bp_usage_pct`, `daily_loss_limit_pct`) enforced in the same gate order as `bot/options/service.py` — no invented caps

### Hypotheses (OBT-06)
- D-14 `docs/research/2026-MM-DD-options-backtest-hypotheses.md` committed BEFORE the first real-data run: H1 IVR 20 vs 30, H2 short delta 0.16 vs 0.20, H3 iron_condor vs put_credit_spread; evidence floor ≥25 closed trades per arm; IS vs OOS windows declared up front (researcher confirms Massive history coverage); metric of record PF + Sortino, tie-break avg credit captured; SUPPORTED only if it wins in BOTH IS and OOS and clears the floor
- D-15 Variants via `--set entry.ivr_min=20` style CLI overrides (or variants JSON), never by editing `rules_options.json`; effective config recorded per run

### Output (OBT-07)
- D-16 `backtester/results/options/<run-id>/` with `trades.csv` (symbol, structure, open/close dates, strikes, credit, exit reason, P&L $, max-loss, DTE open/close), `summary.json` (win rate, PF, avg credit captured %, max DD $, Sortino, trades, per-symbol), `config.json`; reuse `backtester/report.py` metric helpers, no duplicated Sortino/DD math
- D-17 Result doc `docs/research/…-options-backtest-results.md` reports each hypothesis SUPPORTED / REJECTED / INSUFFICIENT-EVIDENCE with numbers

### Testing
- D-18 `tests/backtester/options/`: import-not-copy, look-ahead fixture, BS round-trip (price→IV→price within 1e-4) + delta monotonic in strike, IVR fixture rank, fill/settlement arithmetic on a hand-built 2-leg example, engine caps; full suite green (965 as of b36af60); no live network in tests

### Claude's Discretion
- Exact module names inside `backtester/options/`; whether the IVR series is cached alongside bars; parquet vs JSON cache format (prefer what `backtester/massive.py` already does)
- Missing-bar handling for a leg on a manage day (carry-forward last close vs skip) — pick one, document it

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy being replayed
- `bot/options/strategy.py` — pure functions to import (signatures + row dict keys)
- `bot/options/config.py`, `bot/options/schema.py`, `rules_options.json` — config construction and every strategy knob
- `bot/options/service.py` — portfolio cap gate order to mirror (D-13)
- `bot/gateway/gateway.py` (`screen_options`) — live row dict shape the strategy expects

### Existing backtester patterns to reuse
- `backtester/massive.py` — `MassiveDataSource` HTTP/cache/API-key pattern (extend, don't fork)
- `backtester/report.py` — metric helpers (Sortino, DD, PF)
- `backtester/harness.py`, `backtester/run.py`, `backtester/compare.py` — CLI/run-dir/sweep conventions
- `tests/backtester/` — look-ahead fixture pattern (BT-02)

### Design / research
- `~/.claude/plans/scrape-highly-rated-options-velvety-naur.md` §Follow-ups — Phase 9 sketch + Massive entitlement facts (contracts + option daily aggs = 200; chain snapshot = 403)
- `docs/research/2026-08-17-tastylive-options-research.md` — strategy numbers the hypotheses test
- `.planning/phases/08-options-premium-selling/08-SUMMARY.md` — Phase 8 outcome

</canonical_refs>

<specifics>
## Specific Ideas

- Massive daily option bars have no bid/ask; the whole fill model is close-derived — state that limitation in the results doc
- Live `iv_rank` arrives as a fraction (0.066) and is ×100 in service; the backtester must hand `passes_entry_gate` the same unit it sees live
- Phase 6 lesson (2026-08-12): a backtester that shows IS PF=inf and OOS PF=0.18 is overfitting — the pre-registration + OOS rule exists to prevent repeating that

</specifics>

<deferred>
## Deferred Ideas

- Intraday bars / second entry scan / bid-ask fills (chain snapshot 403)
- Strangles, naked puts (need BP model), rolling, VIX-scaled BP ladder, earnings blackout
- Any `bot/options/*` or `rules_options.json` change before results exist

</deferred>

---

*Phase: 09-options-backtester*
*Context gathered: 2026-08-17 via PRD Express Path*
