# Phase 11: Multi-strategy options bot (bull_call_spread) - Context

**Gathered:** 2026-09-24
**Status:** Ready for planning
**Source:** PRD Express Path (`docs/superpowers/specs/2026-09-24-multi-strategy-options-design.md`, operator-approved 2026-09-24 via superpowers:brainstorming; §12a amendment = planning-time findings after rebase onto develop)

<domain>
## Phase Boundary

`rules_options.json` gains a `strategies` array and the ONE options-bot process
(`python3 -m bot --rules rules_options.json`) runs every configured strategy
concurrently:

- `tasty_credit_spreads` — the existing Phase 8 credit book (iron condor / put credit
  spread on liquid ETFs). Behavior must be identical after the change.
- `super_bull_call` — NEW bull call debit spread ("Super Bull Call Spread", Options With
  Ravish) whose bullish universe for the day is the equity bot's Trend Join Long
  premarket watchlist, read-only from the equity DB.

Per-strategy sizing; global daily-loss breaker, global BP cap, global
one-position-per-underlying. The loader keeps its existing flat
`load_options_config` contract so the Phase 9 options backtester, the UAT probe and all
existing tests keep working without call-site changes.

NOT in this phase: a backtester arm for bull calls, the sell-half runner, any equity-bot
change (see Deferred).

</domain>

<decisions>
## Implementation Decisions

### Config shape (MSO-01, MSO-03)
- **D-01**: `rules_options.json` top level = `strategies` (array) + shared `risk`, `execution`, `service` blocks. Each strategy entry has `name` (unique, non-empty), `entry`, `structure`, `sizing`, `manage`, and exactly one of `universe` (list of codes) or `universe_source`.
- **D-02**: Global-only knobs live outside the strategies: `risk.sizing_equity_usd`, `risk.max_bp_usage_pct`, `risk.daily_loss_limit_pct`; `service.manage_interval_min` (one manage loop, one interval); `service.equity_state_db` (default `data/bot_state.db`). `execution` and the rest of `service` are unchanged from today.
- **D-03**: Per-strategy `sizing` = `max_risk_per_trade_pct`, `max_concurrent_positions`, `max_new_positions_per_day`.
- **D-04**: The only valid `universe_source` value is `"equity_watchlist"`.
- **D-05**: IV-gate keys (`ivr_min`, `ivp_min`, `fear_drop_pct`, `fear_ivr_min`) are required for credit structures (`iron_condor`, `put_credit_spread`) and absent for `bull_call_spread`. Enforced as loader checks (the JSON schema stays structural), mirroring the existing fail-closed `_IMPLEMENTED_STRUCTURES` pattern.
- **D-06**: The legacy flat file shape (no `strategies` key — today's format) still loads, treated as a one-strategy book.
- **D-11**: `ConfigError` (fail closed) on: duplicate strategy names, both/neither of `universe`/`universe_source`, unknown `universe_source`, `structure.type` outside `_IMPLEMENTED_STRUCTURES`, IV-gate keys missing for a credit structure. `structure.type` enum AND `_IMPLEMENTED_STRUCTURES` both gain `"bull_call_spread"`.

### Loader API (MSO-01, MSO-02) — spec §12a A1/A2
- **D-07**: `load_options_config(path="rules_options.json", strategy=None) -> OptionsConfig` KEEPS its name and return type: the flat per-strategy view with shared `risk`/`execution`/`service` values flattened in. `strategy=None` selects the first strategy in the list (`tasty_credit_spreads` in the shipped file); an unknown name raises `ConfigError`.
- **D-08**: New `load_options_book(path) -> OptionsBook` returns every strategy as a flat `OptionsConfig` (tuple, config order) plus the shared blocks. Only the service uses it.
- **D-09**: `OptionsConfig` gains defaulted fields: `name`, `universe_source` (None when `universe` is set), `long_delta`, `max_debit_to_width`, `profit_target_pct_of_max`, `equity_state_db`. The IV-gate fields and credit-only fields become Optional (None for `bull_call_spread`). Every pure function in `bot/options/strategy.py` keeps its exact `cfg` parameter contract.
- **D-10**: New `bot.options.config.legacy_view(raw: dict, name: str) -> dict` projects one strategy of a `strategies`-shape dict to the legacy flat shape (identity for a legacy-shape input). `backtester/options_run.py` gains `--strategy NAME` (default: first strategy), calls `legacy_view` BEFORE `apply_overrides`, so every documented arm command (`--set entry.ivr_min=20`, `structure.short_delta=0.16`, `structure.type=put_credit_spread`) keeps working verbatim, and rejects a debit structure (`bull_call_spread`) with a clear `[ERROR]` and exit 1.

### Bull call strategy core (MSO-04, MSO-05) — pure, D7 purity contract intact
- **D-12**: `pick_strikes(rows, underlying_px, "bull_call_spread", cfg)`: long call = call row whose |delta| is closest to `cfg.long_delta`; width target = existing formula `max(px × wing_width_pct_of_underlying/100, min_wing_width_usd)`; short call = listed call strike closest to `long_strike + width`, strictly above the long strike (never same-strike); both legs pass `leg_is_liquid`. Returns `{"legs", "debit", "width"}` with the BUY leg first (the longs-before-shorts open invariant holds with no `LegExecutor` change). `width = short_strike − long_strike`.
- **D-13**: The "1/4 rule" gate: reject unless `0 < debit ≤ cfg.max_debit_to_width × width`, where `debit = mid(long) − mid(short)`. Shipped `max_debit_to_width = 0.30`.
- **D-14**: Sizing uses the existing `size_position` floor + BP-cap logic with per-spread risk `debit × 100` (credit structures keep `(width − credit) × 100`). Exact refactor shape is Claude's discretion (see below) but the credit-path results must be unchanged.
- **D-15**: New `manage_decision_debit(mark, debit, width, dte, cfg) -> Optional[str]`. Spread value = `−mark` (`mark_spread` returns SELL−BUY mids, negative for a debit position — the sign is handled exactly once, here). Order, first hit wins: (1) `"assignment_guard"` when `dte ≤ cfg.assignment_guard_dte`; (2) `"profit_target"` when `(−mark − debit) ≥ cfg.profit_target_pct_of_max/100 × (width − debit)`; (3) `"dte_exit"` only when `cfg.manage_dte` is not None and `dte ≤ cfg.manage_dte`. No stop loss, by design ("position for zero" — sizing is the risk control).
- **D-16**: Shipped `super_bull_call` values: `long_delta 0.30`, `wing_width_pct_of_underlying 4.5`, `min_wing_width_usd 2.0`, `max_debit_to_width 0.30`, `target_dte 30`, `min_dte 21`, `max_dte 45`, `prefer_monthly true`, liquidity gates as tasty (`max_spread_pct_of_mid 5.0`, `max_spread_abs_usd 0.05`, `min_open_interest 500`), `entry_scan_et "10:05"`, `second_entry_scan_et null`, sizing `max_risk_per_trade_pct 1.0 / max_concurrent_positions 4 / max_new_positions_per_day 2`, manage `profit_target_pct_of_max 60 / manage_dte null / assignment_guard_dte 1`.

### Equity-watchlist universe (MSO-06)
- **D-17**: A small reader (plain `sqlite3`; must NOT import `bot.state.store.StateStore`) opens `service.equity_state_db` via read-only URI `file:<path>?mode=ro` with a busy timeout, runs `SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC` for today's ET date, and returns at most the first 20 codes (the live table held 23 rows on 2026-09-17 — the cap is load-bearing). Missing file, locked/unreadable DB, or empty result → log a structured warning and return `[]` → zero bull-call entries that day (fail closed). No write path to the equity DB exists anywhere in `bot/options/`.
- **D-28**: `super_bull_call` entry scan runs at 10:05 ET, after the equity premarket scan has persisted the day's watchlist.

### Store (MSO-07)
- **D-18**: `option_positions` gains `strategy_name TEXT NOT NULL DEFAULT 'tasty_credit_spreads'` via a guarded, idempotent `ALTER TABLE` (PRAGMA table_info check). Existing rows inherit the default (historically correct). Insert/read pass the field through.
- **D-19**: Signed net-premium convention, no new column: `credit_per_spread` is positive for credit structures (as today) and NEGATIVE for `bull_call_spread` (a $1.96 debit stores −1.96). The existing close math (`realized_per_spread = credit − net_exit`) then yields correct realized P&L for both kinds with no change. `manage_decision_debit` receives `debit = −credit_per_spread`. `max_loss_usd` for a debit position = `debit × 100 × qty`. Exit alerts show %-of-max-profit for debit positions (a negative denominator is meaningless).

### Service composition (MSO-08)
- **D-20**: `_register_jobs` registers per-strategy entry-scan crons with ids `options_entry_scan_<name>` (and `options_entry_scan_<name>_2` when that strategy's `second_entry_scan_et` is set), ONE `options_manage` job on `service.manage_interval_min`, ONE `options_eod` job.
- **D-21**: `_manage_position` dispatches on `pos["strategy_name"]` to that strategy's config and decision function (`manage_decision` for credit structures, `manage_decision_debit` for `bull_call_spread`).
- **D-22**: Per-strategy counters (`opened_today`, open/concurrent count) filter on `strategy_name`; the daily-loss breaker, BP headroom (`open_max_loss_total`) and the busy-per-underlying check stay GLOBAL across all strategies.
- **D-23**: Chain screening for `bull_call_spread`: calls only, delta breadth 0.05–0.50, same per-underlying screen + throttle discipline as today (1000-row screen cap handled per underlying).
- **D-24**: Telegram entry/exit/EOD lines and the EOD HTML report include the strategy name (HTML-escaped like every other field).
- **D-29** *(planning-time addition)*: At startup reconcile, any OPEN/OPENING/CLOSING position whose `strategy_name` is not in the loaded book is set `NEEDS_ATTENTION` with a Telegram alert + audit event (same pattern as `options_reconcile_incomplete`) — never silently orphaned, never managed with another strategy's parameters. The manage loop skips `NEEDS_ATTENTION` rows as today.

### Safety + shipping (MSO-09)
- **D-25**: Unchanged invariants: LIMIT orders only; long legs before shorts on open, shorts first on close (`LegExecutor` untouched); SAFE-OG-01 reconcile scope (only codes in THIS bot's `option_legs`); own DB / kill file / report dir (D6); ONE options-bot instance; `FUTU_TRD_ENV=SIMULATE` only.
- **D-26**: The shipped `rules_options.json` is converted to the `strategies` shape containing `tasty_credit_spreads` (values identical to today) and `super_bull_call` (D-16). A test asserts `load_options_config("rules_options.json")` (default strategy) equals the pre-change flat config field-for-field on every pre-existing `OptionsConfig` field.
- **D-27**: Strategy provenance doc `docs/research/2026-09-24-super-bull-call-spread.md`: source URL, channel, publish date, transcript retrieval method (Apify `streamers/youtube-scraper`, 2026-09-24), the distilled mechanical rules (see Specific Ideas), and every deviation the bot makes from the video (watchlist signal substitution, full-close instead of sell-half, pct+floor width instead of fixed 10 points). Summarised rules only — do not reproduce the transcript.

### Claude's Discretion
- How `size_position` is generalised for debit risk (e.g. a per-spread-risk parameter or a thin `size_debit_position` wrapper) — as long as credit-path outputs are unchanged and existing tests pass.
- Module placement of the watchlist reader (e.g. `bot/options/universe.py`) and of `OptionsBook`.
- Test file organisation under `tests/options/` and `tests/backtester/options/`.
- Whether `scripts/uat_options_probe.py` gets a `--strategy` flag (not required; its default-strategy behavior must not break).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Design
- `docs/superpowers/specs/2026-09-24-multi-strategy-options-design.md` — approved design incl. §12a amendment (A1 loader split, A2 backtester projection, A3 equity DB path, A4 no-drift test)

### Options bot (code being changed)
- `rules_options.json` — current flat config (becomes `strategies` shape)
- `bot/options/schema.py` — `OPTIONS_SCHEMA` (structure.type enum)
- `bot/options/config.py` — `OptionsConfig`, `load_options_config`, `_IMPLEMENTED_STRUCTURES`
- `bot/options/strategy.py` — pure core: `pick_expiry`, `passes_entry_gate`, `leg_is_liquid`, `pick_strikes`, `size_position`, `mark_spread`, `manage_decision` (D7 purity contract in module docstring)
- `bot/options/store.py` — `OptionsStore` (positions/legs/meta; migration pattern)
- `bot/options/service.py` — `OptionsBot` jobs, `_scan_and_open`, `_try_open`, `_manage_position`, `reconcile`, breaker, EOD
- `bot/options/execution.py` — `LegExecutor` (must stay untouched)
- `bot/state/store.py` — `DEFAULT_DB_PATH`, `get_watchlist_codes` query (READ the query; do not import StateStore)

### Consumers that must keep working
- `backtester/options_run.py` — `apply_overrides`, temp-file validation via `load_options_config`
- `backtester/options/engine.py`, `backtester/options/data.py`, `backtester/options/greeks.py`
- `scripts/uat_options_probe.py`
- `tests/options/conftest.py`, `tests/options/test_config.py` (drift test vs repo-root file), `tests/backtester/options/*.py`

### Prior phase context
- `.planning/phases/08-options-premium-selling/08-SUMMARY.md` — Phase 8 invariants (D1–D7)
- `docs/research/2026-08-17-tastylive-options-research.md` — provenance-doc pattern
- `docs/research/2026-08-17-options-backtest-results.md` — the documented `--set` arm commands that must keep working

</canonical_refs>

<specifics>
## Specific Ideas

Distilled mechanical rules from the source video (Options With Ravish, "Super Bull Call
Spread: My Favorite Low Risk, High Reward Options Strategy", 2026-09-19,
https://www.youtube.com/watch?v=VZ1MbM3UQ5Q) — the basis for D-12..D-16 and the D-27 doc:

- Buy a ~30-delta call; sell a call ~10 points further OTM; same expiration (example ~30 DTE; he permits 0DTE–6 months).
- "1/4 rule": pay about 25% of the width (e.g. $2.50 on a $10-wide spread → 3:1); acceptable up to ~30%, never more.
- "Position for zero": no stop loss, no adjustments — the debit is the whole risk; size to the loss you accept.
- Management: fixed profit target (40–50% for a high win rate) OR hold to +100%, sell half, ride the rest. Max profit needs the stock above the short strike near expiry (theta turns positive once ITM).
- Universe: high-momentum single names (NVDA, SNDK, TSM in his examples). Entry trigger is discretionary ("when I'm super bullish") — replaced here by the equity scanner watchlist (operator decision).

Worked example for tests (from the video): NVDA 225/235 call spread, buy 225 @ 3.58, sell 235 @ 1.62 → debit 1.96, width 10, max profit 8.04 per spread, max loss 196 per spread; debit/width = 0.196 ≤ 0.30 passes the 1/4-rule gate.

</specifics>

<deferred>
## Deferred Ideas

- Phase 9 backtester arm for `bull_call_spread` (needs a bull-call leg model and a watchlist replay source) — own future phase; `options_run` rejects debit structures meanwhile (D-10).
- The video's sell-half runner (partial closes → qty mutation in store/reconcile/P&L).
- 0DTE / multi-expiry variants, calendars/diagonals, undefined-risk structures (strangles, naked puts), covered calls/wheel.
- Any change to the equity bot (`bot/service/bot.py`, scanner, `rules.json`).

</deferred>

---

*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Context gathered: 2026-09-24 via PRD Express Path*
