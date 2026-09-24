# Multi-strategy options bot: `strategies` array + `bull_call_spread` — design spec

Date: 2026-09-24
Status: awaiting operator review
Owner: operator (manik.singh.valid@gmail.com)

## 1. Intent

Extend the Phase 8 options bot so `rules_options.json` can define **multiple option
strategies** and the ONE options-bot process runs all of them concurrently. The first
new strategy is the **"Super Bull Call Spread"** (bull call debit vertical), sourced
from the video below. The existing `tasty_credit_spreads` book keeps running unchanged
alongside it.

Success looks like: one config file, one process, two live books — each with its own
universe, entry gates, and sizing — sharing one daily-loss breaker and one
buying-power cap, on the paper account, hands-off.

## 2. Strategy provenance

Source: "Super Bull Call Spread: My Favorite Low Risk, High Reward Options Strategy"
— Options With Ravish, published 2026-09-19, 11:05,
https://www.youtube.com/watch?v=VZ1MbM3UQ5Q
(transcript pulled 2026-09-24 via Apify `streamers/youtube-scraper`; the distilled
rules below are the mechanical content of that transcript. A full research note goes
to `docs/research/` during execution, same pattern as the tastylive doc.)

Mechanical rules as stated in the video:

- **Structure**: bull call spread. BUY a call at ~30 delta; SELL a call ~10 points
  further OTM; same expiration. Example uses ~30 DTE (next monthly); he permits
  0DTE–6 months.
- **"1/4 rule" entry price**: target debit ≈ 25% of the width (e.g. $2.50 on a
  $10-wide spread → 3:1 reward:risk); acceptable to ~30% of width, never more.
- **"Position for zero" risk**: NO stop loss, NO adjustments. The debit is the whole
  risk; size the trade to the loss you accept, then leave it alone.
- **Management**: either a fixed profit target (40–50% of max for high win rate) or
  hold to +100%, sell half, ride the rest to 200–400%. Max profit requires the stock
  above the short strike near expiry (position turns theta-positive once ITM).
- **Universe**: high-momentum single names (his examples: NVDA, SNDK, TSM).
- **Entry trigger**: discretionary ("when I'm super bullish") — no mechanical signal
  is given. The bot substitutes one (Decision 2 below).

## 3. Decisions taken (operator-confirmed)

1. **Scope**: new strategy type (`bull_call_spread`) + multi-strategy config
   machinery. It is a debit vertical, so the **D1 defined-risk invariant survives**
   — no margin/BP model change.
2. **Entry signal**: reuse the equity bot's Trend Join Long **premarket scanner
   watchlist** as the bullish universe for the day (read-only cross-process read of
   the equity `state_db`). One definition of "bullish" across the system.
3. **Take-profit**: simple **full close at a configured % of max profit**
   (`profit_target_pct_of_max`). The video's sell-half runner is explicitly
   deferred (out of scope, §12) — it would require qty-mutation across store,
   reconcile, and P&L.
4. **Risk model**: **per-strategy sizing blocks** (risk/trade, entries/day,
   concurrent cap) with a **global daily-loss breaker and global BP/max-loss cap**
   — a bad morning in either book halts all new entries.
5. **Architecture**: Approach A — one `rules_options.json` with a `strategies`
   array, one process, strategy-tagged positions, backward-compatible loader.
   (Rejected: B — multiple rules files, awkward global breaker; C — second bot
   process, violates the ONE-instance invariant and doubles broker quota pressure.)

## 4. Config & schema

New top-level shape of `rules_options.json`:

```json
{
  "strategies": [
    { "name": "tasty_credit_spreads",
      "universe": ["US.SPY", "..."],
      "entry":     { "ivr_min": 30, "...": "as today", "entry_scan_et": "10:00" },
      "structure": { "type": "iron_condor", "...": "as today" },
      "sizing":    { "max_risk_per_trade_pct": 1.0,
                     "max_concurrent_positions": 8,
                     "max_new_positions_per_day": 2 },
      "manage":    { "profit_target_pct_of_credit": 50, "manage_dte": 21,
                     "stop_loss_credit_multiple": null, "assignment_guard_dte": 1 } },
    { "name": "super_bull_call",
      "universe_source": "equity_watchlist",
      "entry":     { "entry_scan_et": "10:05", "second_entry_scan_et": null,
                     "max_spread_pct_of_mid": 5.0, "max_spread_abs_usd": 0.05,
                     "min_open_interest": 500,
                     "target_dte": 30, "min_dte": 21, "max_dte": 45,
                     "prefer_monthly": true },
      "structure": { "type": "bull_call_spread", "long_delta": 0.30,
                     "wing_width_pct_of_underlying": 4.5,
                     "min_wing_width_usd": 2.0,
                     "max_debit_to_width": 0.30 },
      "sizing":    { "max_risk_per_trade_pct": 1.0,
                     "max_concurrent_positions": 4,
                     "max_new_positions_per_day": 2 },
      "manage":    { "profit_target_pct_of_max": 60, "manage_dte": null,
                     "assignment_guard_dte": 1 } }
  ],
  "risk":      { "sizing_equity_usd": 100000, "max_bp_usage_pct": 25,
                 "daily_loss_limit_pct": 2.0 },
  "execution": { "unchanged from today": "limit_buffer_usd, poll_interval_s, ttl_s, escalation_step_usd, max_retries" },
  "service":   { "unchanged from today, plus": "equity_state_db: data/state.db, manage_interval_min: 5" }
}
```

Rules:

- `strategies[].name` unique, non-empty. Exactly one of `universe` /
  `universe_source` per strategy; the only valid `universe_source` is
  `"equity_watchlist"`.
- Global-only knobs move out of the per-strategy blocks: equity basis, BP cap and
  daily breaker to `risk`; `manage_interval_min` to `service` (one manage loop,
  one interval).
- IV-gate keys (`ivr_min`, `ivp_min`, `fear_drop_pct`, `fear_ivr_min`) are required
  for credit structures (`iron_condor`, `put_credit_spread`) and absent for
  `bull_call_spread`, whose entry gate is watchlist membership + the debit,
  liquidity, and DTE gates. Enforced in the loader (schema stays structural;
  per-structure key requirements are loader checks, mirroring the existing
  fail-closed pattern).
- `structure.type` enum gains `"bull_call_spread"`; `_IMPLEMENTED_STRUCTURES` gains
  it too (both gates stay, as today).
- Width defaults intentionally reproduce the video's "$10 on a ~$220 stock" via the
  existing pct-of-underlying + dollar-floor mechanism (4.5% / $2.00 floor).

**Backward compatibility**: a file with no `strategies` key (the current flat shape)
is wrapped by the loader into a one-element `strategies` list, mapping its
`sizing`/`manage` keys to the split shape. The UAT probe, existing tests, and the
Phase 9 options backtester keep loading unmodified files. Both shapes validate; no
config migration is forced.

## 5. Loader (`bot/options/config.py`)

`load_options_config` returns an `OptionsConfig` carrying the shared
`risk`/`execution`/`service` fields plus `strategies: tuple[StrategyConfig, ...]`.
The loader **flattens the shared risk values (`sizing_equity_usd`,
`max_bp_usage_pct`) into each `StrategyConfig`**, so every pure function in
`bot/options/strategy.py` keeps its exact `cfg` parameter contract.

`ConfigError` (fail closed) on: duplicate names, unknown `universe_source`,
structure outside `_IMPLEMENTED_STRUCTURES`, IV keys missing for a credit
structure, both/neither of `universe`/`universe_source`.

## 6. Strategy core (`bot/options/strategy.py` — pure, D7 contract intact)

- `pick_strikes` gains the `bull_call_spread` branch: long call = |delta| closest to
  `cfg.long_delta`; short call = listed strike closest to `long_strike + width`,
  strictly above it (never same-strike); width from the existing pct+floor formula;
  both legs pass `leg_is_liquid`; `debit = mid(long) − mid(short)` must be > 0 and
  `≤ cfg.max_debit_to_width × width` (the 1/4-rule, mirror of
  `min_credit_to_width`). Returns `{"legs", "debit", "width"}` with the BUY leg
  first — the longs-before-shorts open invariant holds with no executor change.
- `size_position` is reused with per-spread `risk = debit × 100` (vs
  `width − credit` for credit spreads); the BP headroom argument stays the global
  open-max-loss total.
- New `manage_decision_debit(mark, debit, width, dte, cfg)`: spread value is
  `−mark` (the existing `mark_spread` returns SELL−BUY mids, which is negative for
  a debit position — the sign is handled exactly once, here). Order of checks:
  1. `assignment_guard` — `dte ≤ cfg.assignment_guard_dte`;
  2. `profit_target` — `(−mark − debit) ≥ cfg.profit_target_pct_of_max/100 × (width − debit)`;
  3. `dte_exit` — only if `cfg.manage_dte` is set (default null = hold, per the
     video's theta-capture logic).
  No stop loss, by design ("position for zero" — sizing is the risk control).
- `pick_expiry`, `option_dte`, `leg_is_liquid`, `mark_spread` unchanged.

## 7. Equity-watchlist universe source

Small helper (plain `sqlite3`, no import of the equity `StateStore` class): open
`service.equity_state_db` with a **read-only URI** (`file:...?mode=ro`) and a busy
timeout, run the same query as `StateStore.get_watchlist_codes(today)`, cap to 20
codes. Missing file, locked DB, or empty watchlist → log + **zero entries for that
strategy today** (fail closed). The options bot never writes to the equity DB.

Timing: the equity premarket scan completes before open; the bull-call entry scan at
10:05 ET reads that day's persisted watchlist.

## 8. Store & migration (`bot/options/store.py`)

`option_positions` gains `strategy_name TEXT NOT NULL DEFAULT
'tasty_credit_spreads'` via a guarded `ALTER TABLE` (PRAGMA table_info check —
idempotent). Existing rows inherit the default, which is historically correct.
Insert/read pass the field through. No other store change.

**Sign convention (no new column needed)**: `credit_per_spread` stores the signed
net premium received per spread — positive for credit structures (as today),
**negative for `bull_call_spread`** (a $1.96 debit stores as −1.96). The existing
close math (`realized_per_spread = credit − net_exit`, where `net_exit` is
SELL-leg buyback cost minus BUY-leg sale proceeds) then produces correct realized
P&L for both kinds with zero changes. `manage_decision_debit` receives
`debit = −credit_per_spread`. The %-of-credit line in exit alerts is formatted as
%-of-max-profit for debit positions (a negative denominator is meaningless).

## 9. Service composition (`bot/options/service.py`)

- `_register_jobs`: per-strategy entry-scan crons, job ids
  `options_entry_scan_<name>` (+ `_2` when `second_entry_scan_et` set); ONE manage
  job on `service.manage_interval_min`; one EOD job.
- Entry scan runs with its strategy's config: `opened_today` and concurrent-position
  counters filter on `strategy_name`; **BP headroom, the daily-loss breaker, and the
  busy-per-underlying check stay global** across all strategies (no stacking two
  positions on one underlying even across books; the ETF and watchlist universes
  are disjoint in practice).
- Chain screening for `bull_call_spread`: calls only, breadth 0.05–0.50 delta,
  same per-underlying screen + 3.5 s throttle discipline as today.
- `_manage_position` dispatches on `pos["strategy_name"]` to that strategy's manage
  params and decision function (credit vs debit).
- Telegram entry/exit/EOD lines and the EOD HTML gain the strategy name.
- Breaker logic itself unchanged: global realized + unrealized vs
  `risk.daily_loss_limit_pct × risk.sizing_equity_usd`, persisted in meta, armed
  once per day.

## 10. Unchanged safety invariants

LIMIT orders only; long legs before shorts on open, shorts first on close
(`LegExecutor` untouched); SAFE-OG-01 reconcile scope (only codes in THIS bot's
`option_legs`); own DB / kill file / report dir (D6); ONE options-bot instance;
`FUTU_TRD_ENV=SIMULATE` only.

## 11. Testing

- Pure functions: bull-call strike selection (incl. no-listed-strike-above edge,
  same-strike rejection), debit 1/4-rule gate, sizing floor with `debit × 100`
  risk, `manage_decision_debit` sign math and check ordering.
- Loader: flat-shape wrap (old file loads identically), strategies shape, duplicate
  names, both/neither universe keys, IV keys required for credit structures,
  fail-closed unknown structure/universe_source.
- Store: migration idempotence; `strategy_name` round-trip; default on legacy rows.
- Watchlist reader: read-only mode, missing DB, empty day.
- Service: per-strategy caps vs global breaker/BP; manage dispatch per strategy;
  job registration ids.

## 12. Out of scope (explicit follow-ups)

- Phase 9 backtester arm for `bull_call_spread` (needs a bull-call leg model and a
  watchlist replay source — its own phase).
- The sell-half runner (partial closes: qty mutation in store/reconcile/P&L).
- 0DTE / multi-expiry variants; calendars; anything undefined-risk.
- Any change to the equity bot.

## 13. Ops notes

- **Push local develop before dispatching execution**: this worktree forked from
  origin/develop and lacks Phase 9 (options backtester); executing from a stale
  base risks divergence.
- Implementation enters through a GSD command (CLAUDE.md workflow enforcement);
  this spec is the input to that planning step.
- New DB column ships with the code that reads it; restart the options bot only
  after merge to develop (bot runs from main-repo develop).
