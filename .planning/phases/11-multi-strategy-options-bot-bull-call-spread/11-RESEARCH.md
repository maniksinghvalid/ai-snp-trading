# Phase 11: Multi-strategy options bot (bull_call_spread) - Research

**Researched:** 2026-09-24
**Domain:** Config-schema refactor + strategy-pattern dispatch inside an existing async options trading bot (Python 3, stdlib sqlite3, jsonschema, APScheduler)
**Confidence:** HIGH (every claim below is grounded in a direct file read, grep, or a live interpreter probe run in this repo — no new external library research was needed; this phase adds zero new dependencies)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

D-01..D-29, verbatim from `11-CONTEXT.md`:

- **D-01**: `rules_options.json` top level = `strategies` (array) + shared `risk`, `execution`, `service` blocks. Each strategy entry has `name` (unique, non-empty), `entry`, `structure`, `sizing`, `manage`, and exactly one of `universe` (list of codes) or `universe_source`.
- **D-02**: Global-only knobs live outside the strategies: `risk.sizing_equity_usd`, `risk.max_bp_usage_pct`, `risk.daily_loss_limit_pct`; `service.manage_interval_min` (one manage loop, one interval); `service.equity_state_db` (default `data/bot_state.db`). `execution` and the rest of `service` are unchanged from today.
- **D-03**: Per-strategy `sizing` = `max_risk_per_trade_pct`, `max_concurrent_positions`, `max_new_positions_per_day`.
- **D-04**: The only valid `universe_source` value is `"equity_watchlist"`.
- **D-05**: IV-gate keys (`ivr_min`, `ivp_min`, `fear_drop_pct`, `fear_ivr_min`) are required for credit structures (`iron_condor`, `put_credit_spread`) and absent for `bull_call_spread`. Enforced as loader checks (the JSON schema stays structural), mirroring the existing fail-closed `_IMPLEMENTED_STRUCTURES` pattern.
- **D-06**: The legacy flat file shape (no `strategies` key — today's format) still loads, treated as a one-strategy book.
- **D-07**: `load_options_config(path="rules_options.json", strategy=None) -> OptionsConfig` KEEPS its name and return type: the flat per-strategy view with shared `risk`/`execution`/`service` values flattened in. `strategy=None` selects the first strategy in the list (`tasty_credit_spreads` in the shipped file); an unknown name raises `ConfigError`.
- **D-08**: New `load_options_book(path) -> OptionsBook` returns every strategy as a flat `OptionsConfig` (tuple, config order) plus the shared blocks. Only the service uses it.
- **D-09**: `OptionsConfig` gains defaulted fields: `name`, `universe_source` (None when `universe` is set), `long_delta`, `max_debit_to_width`, `profit_target_pct_of_max`, `equity_state_db`. The IV-gate fields and credit-only fields become Optional (None for `bull_call_spread`). Every pure function in `bot/options/strategy.py` keeps its exact `cfg` parameter contract.
- **D-10**: New `bot.options.config.legacy_view(raw: dict, name: str) -> dict` projects one strategy of a `strategies`-shape dict to the legacy flat shape (identity for a legacy-shape input). `backtester/options_run.py` gains `--strategy NAME` (default: first strategy), calls `legacy_view` BEFORE `apply_overrides`, so every documented arm command (`--set entry.ivr_min=20`, `structure.short_delta=0.16`, `structure.type=put_credit_spread`) keeps working verbatim, and rejects a debit structure (`bull_call_spread`) with a clear `[ERROR]` and exit 1.
- **D-11**: `ConfigError` (fail closed) on: duplicate strategy names, both/neither of `universe`/`universe_source`, unknown `universe_source`, `structure.type` outside `_IMPLEMENTED_STRUCTURES`, IV-gate keys missing for a credit structure. `structure.type` enum AND `_IMPLEMENTED_STRUCTURES` both gain `"bull_call_spread"`.
- **D-12**: `pick_strikes(rows, underlying_px, "bull_call_spread", cfg)`: long call = call row whose |delta| is closest to `cfg.long_delta`; width target = existing formula `max(px × wing_width_pct_of_underlying/100, min_wing_width_usd)`; short call = listed call strike closest to `long_strike + width`, strictly above the long strike (never same-strike); both legs pass `leg_is_liquid`. Returns `{"legs", "debit", "width"}` with the BUY leg first (the longs-before-shorts open invariant holds with no `LegExecutor` change). `width = short_strike − long_strike`.
- **D-13**: The "1/4 rule" gate: reject unless `0 < debit ≤ cfg.max_debit_to_width × width`, where `debit = mid(long) − mid(short)`. Shipped `max_debit_to_width = 0.30`.
- **D-14**: Sizing uses the existing `size_position` floor + BP-cap logic with per-spread risk `debit × 100` (credit structures keep `(width − credit) × 100`). Exact refactor shape is Claude's discretion but the credit-path results must be unchanged.
- **D-15**: New `manage_decision_debit(mark, debit, width, dte, cfg) -> Optional[str]`. Spread value = `−mark` (`mark_spread` returns SELL−BUY mids, negative for a debit position — the sign is handled exactly once, here). Order, first hit wins: (1) `"assignment_guard"` when `dte ≤ cfg.assignment_guard_dte`; (2) `"profit_target"` when `(−mark − debit) ≥ cfg.profit_target_pct_of_max/100 × (width − debit)`; (3) `"dte_exit"` only when `cfg.manage_dte` is not None and `dte ≤ cfg.manage_dte`. No stop loss, by design ("position for zero" — sizing is the risk control).
- **D-16**: Shipped `super_bull_call` values: `long_delta 0.30`, `wing_width_pct_of_underlying 4.5`, `min_wing_width_usd 2.0`, `max_debit_to_width 0.30`, `target_dte 30`, `min_dte 21`, `max_dte 45`, `prefer_monthly true`, liquidity gates as tasty (`max_spread_pct_of_mid 5.0`, `max_spread_abs_usd 0.05`, `min_open_interest 500`), `entry_scan_et "10:05"`, `second_entry_scan_et null`, sizing `max_risk_per_trade_pct 1.0 / max_concurrent_positions 4 / max_new_positions_per_day 2`, manage `profit_target_pct_of_max 60 / manage_dte null / assignment_guard_dte 1`.
- **D-17**: A small reader (plain `sqlite3`; must NOT import `bot.state.store.StateStore`) opens `service.equity_state_db` via read-only URI `file:<path>?mode=ro` with a busy timeout, runs `SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC` for today's ET date, and returns at most the first 20 codes. Missing file, locked/unreadable DB, or empty result → log a structured warning and return `[]` → zero bull-call entries that day (fail closed). No write path to the equity DB exists anywhere in `bot/options/`.
- **D-18**: `option_positions` gains `strategy_name TEXT NOT NULL DEFAULT 'tasty_credit_spreads'` via a guarded, idempotent `ALTER TABLE` (PRAGMA table_info check). Existing rows inherit the default (historically correct). Insert/read pass the field through.
- **D-19**: Signed net-premium convention, no new column: `credit_per_spread` is positive for credit structures (as today) and NEGATIVE for `bull_call_spread` (a $1.96 debit stores as −1.96). The existing close math (`realized_per_spread = credit − net_exit`) then yields correct realized P&L for both kinds with no change. `manage_decision_debit` receives `debit = −credit_per_spread`. `max_loss_usd` for a debit position = `debit × 100 × qty`. Exit alerts show %-of-max-profit for debit positions (a negative denominator is meaningless).
- **D-20**: `_register_jobs` registers per-strategy entry-scan crons with ids `options_entry_scan_<name>` (and `options_entry_scan_<name>_2` when that strategy's `second_entry_scan_et` is set), ONE `options_manage` job on `service.manage_interval_min`, ONE `options_eod` job.
- **D-21**: `_manage_position` dispatches on `pos["strategy_name"]` to that strategy's config and decision function (`manage_decision` for credit structures, `manage_decision_debit` for `bull_call_spread`).
- **D-22**: Per-strategy counters (`opened_today`, open/concurrent count) filter on `strategy_name`; the daily-loss breaker, BP headroom (`open_max_loss_total`) and the busy-per-underlying check stay GLOBAL across all strategies.
- **D-23**: Chain screening for `bull_call_spread`: calls only, delta breadth 0.05–0.50, same per-underlying screen + throttle discipline as today (1000-row screen cap handled per underlying).
- **D-24**: Telegram entry/exit/EOD lines and the EOD HTML report include the strategy name (HTML-escaped like every other field).
- **D-25**: Unchanged invariants: LIMIT orders only; long legs before shorts on open, shorts first on close (`LegExecutor` untouched); SAFE-OG-01 reconcile scope (only codes in THIS bot's `option_legs`); own DB / kill file / report dir (D6); ONE options-bot instance; `FUTU_TRD_ENV=SIMULATE` only.
- **D-26**: The shipped `rules_options.json` is converted to the `strategies` shape containing `tasty_credit_spreads` (values identical to today) and `super_bull_call` (D-16). A test asserts `load_options_config("rules_options.json")` (default strategy) equals the pre-change flat config field-for-field on every pre-existing `OptionsConfig` field.
- **D-27**: Strategy provenance doc `docs/research/2026-09-24-super-bull-call-spread.md`: source URL, channel, publish date, transcript retrieval method (Apify `streamers/youtube-scraper`, 2026-09-24), the distilled mechanical rules, and every deviation the bot makes from the video. Summarised rules only — do not reproduce the transcript.
- **D-28**: `super_bull_call` entry scan runs at 10:05 ET, after the equity premarket scan has persisted the day's watchlist.
- **D-29** *(planning-time addition)*: At startup reconcile, any OPEN/OPENING/CLOSING position whose `strategy_name` is not in the loaded book is set `NEEDS_ATTENTION` with a Telegram alert + audit event (same pattern as `options_reconcile_incomplete`) — never silently orphaned, never managed with another strategy's parameters. The manage loop skips `NEEDS_ATTENTION` rows as today.

### Claude's Discretion

- How `size_position` is generalised for debit risk (e.g. a per-spread-risk parameter or a thin `size_debit_position` wrapper) — as long as credit-path outputs are unchanged and existing tests pass.
- Module placement of the watchlist reader (e.g. `bot/options/universe.py`) and of `OptionsBook`.
- Test file organisation under `tests/options/` and `tests/backtester/options/`.
- Whether `scripts/uat_options_probe.py` gets a `--strategy` flag (not required; its default-strategy behavior must not break).

### Deferred Ideas (OUT OF SCOPE)

- Phase 9 backtester arm for `bull_call_spread` (needs a bull-call leg model and a watchlist replay source) — own future phase; `options_run` rejects debit structures meanwhile (D-10).
- The video's sell-half runner (partial closes → qty mutation in store/reconcile/P&L).
- 0DTE / multi-expiry variants, calendars/diagonals, undefined-risk structures (strangles, naked puts), covered calls/wheel.
- Any change to the equity bot (`bot/service/bot.py`, scanner, `rules.json`).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MSO-01 | `rules_options.json` supports a `strategies` array (unique `name`; per-strategy `universe` XOR `universe_source`, `entry`, `structure`, `sizing`, `manage`) plus shared `risk`/`execution`/`service` blocks; `load_options_book(path)` returns every strategy as a flat `OptionsConfig` with shared values flattened in; legacy flat shape still loads | Architecture Pattern 1/2 (loader-side dispatch, field relocation); Common Pitfall 2 (current schema location of the four global knobs); Code sources: `bot/options/config.py`, `bot/options/schema.py` |
| MSO-02 | `load_options_config(path, strategy=None)` keeps its name/return type for the Phase 9 backtester/UAT probe/existing tests; `backtester.options_run` gains `--strategy` + `legacy_view(raw, name)` before `--set` overrides, rejecting debit structures | Architecture Pattern 1; `backtester/options_run.py` full read (exact `main()` ordering, where `--strategy`/`legacy_view` slot in); Open Question 3 |
| MSO-03 | Config fails closed (`ConfigError`) on duplicate names, both/neither universe keys, unknown `universe_source`, unimplemented `structure.type`, IV-gate keys missing for a credit structure | Architecture Pattern 1 (loader-side checks, no jsonschema conditionals precedent); Architecture Pattern 4 + Common Pitfall 3 (manage-block requiredness also varies by structure, not just entry) |
| MSO-04 | `bull_call_spread` structure in the pure strategy core: strike selection, liquidity, 1/4-rule gate, BUY-first legs, `size_position` with `debit × 100` risk | `bot/options/strategy.py` full read (current `pick_strikes`/`size_position` exact implementation to extend) |
| MSO-05 | `manage_decision_debit` exit order (assignment guard → profit target → optional DTE exit), no stop loss, sign convention handled once | Code Examples worked NVDA computation (verified `mark_spread` sign math needs zero changes) |
| MSO-06 | `equity_watchlist` universe source: read-only SQLite URI into `data/bot_state.db`, capped at 20 by `rank ASC`; missing/locked/empty → zero entries, fail closed | Code Examples (live `sqlite3` probes: missing-file behavior, WAL concurrent-read behavior, WAL companion-file creation caveat); Common Pitfall 4; Don't Hand-Roll row 1 |
| MSO-07 | `option_positions.strategy_name` idempotent guarded migration; legacy rows default `tasty_credit_spreads`; debit positions store negative `credit_per_spread` | Anti-Pattern "editing shipped migrations" (D-08 rule); Code Examples guarded-ALTER pattern; `bot/state/migrations.py` partial read (`_migration_0006`, `MIGRATIONS` list, existing guard precedent at 0002/0004) |
| MSO-08 | ONE options process runs every strategy: per-strategy entry-scan jobs, one manage job dispatching per `strategy_name`, per-strategy caps, global breaker/BP/one-per-underlying; alerts + EOD show strategy name | `bot/options/service.py` full read (`_register_jobs`, `_scan_and_open`, `_manage_position`, `_check_daily_breaker`); Common Pitfall 5 + Open Question 1 (per-strategy counter filter gap not covered by current `count_opened_on`) |
| MSO-09 | Shipped `rules_options.json` converted to `strategies` shape, `tasty_credit_spreads` behavior proven field-for-field, `super_bull_call` shipped, provenance doc committed, safety invariants unchanged | Common Pitfall 1 (**critical, newly discovered**: `bot/main.py` dispatch breaks on the new shape); Runtime State Inventory (restart-after-merge, D-26 equivalence test as the regression gate); Security Domain threat table |
</phase_requirements>

## Summary

This phase is a pure internal refactor of `bot/options/` plus one small new
module, not a new-technology integration. All decisions are locked in
`11-CONTEXT.md`; the job of this research is to nail down the exact current
shapes of the six files the plan will touch (`config.py`, `schema.py`,
`strategy.py`, `store.py`, `service.py`, and — **critically, not previously
called out in the design docs** — `bot/main.py`) so the planner can write
tasks against real line numbers and real field lists instead of the design
spec's illustrative JSON.

The single most important finding: **`bot/main.py`'s dispatch check
(`json.load(f).get("strategy_name", "") == "tasty_credit_spreads"`) will stop
matching the moment the shipped `rules_options.json` becomes a `strategies`
array** (the new shape has no top-level `strategy_name` key). Today this
falls through into the equity bot's config loader, which is very likely to
raise `ConfigError` and exit 1 (loud, not silent) — but it is a straight-up
launch-path break that no canonical-ref file in the CONTEXT explicitly names.
This must be a plan task, not an afterthought.

The second finding worth flight-planning around: the *current*
`rules_options.json`/`OPTIONS_SCHEMA` puts `sizing_equity_usd`,
`max_bp_usage_pct`, `daily_loss_limit_pct` inside `sizing`, and
`manage_interval_min` inside `manage` — **not** where the design spec's §4
example shows them (top-level `risk`/`service`). The loader-side flattening
(`load_options_config`) must explicitly relocate these four keys so the
legacy flat view (`OptionsConfig`) is unaffected while the on-disk shape
moves them to the new shared blocks. This is a real diff, not a cosmetic one.

Third: `manage_decision_debit`'s sign math was hand-verified against the
worked NVDA example in the CONTEXT and it closes exactly — `mark_spread`
already returns the correct signed value for a debit position with **zero
changes to `mark_spread` itself**. See Code Examples.

Fourth: a live interpreter probe of `sqlite3` confirms `mode=ro` URI connect
raises `OperationalError: unable to open database file` on a missing file
(good — that is the fail-closed path D-17 wants), a read-only connection can
read a WAL-mode DB concurrently with an open writer transaction, but **a
read-only connection against a DB whose header says `journal_mode=WAL` will
still create `-wal`/`-shm` companion files on the read-only connection's
first touch** if they are not already present — i.e. it needs the directory
to be writable even though it writes no *content*. In production this is a
non-issue (the equity bot's `data/` dir is already writable, WAL files
already exist while the bot is running), but it means the "no write path"
claim in D-17 is about data, not about zero filesystem I/O, and the doc
comment should say so precisely.

**Primary recommendation:** Treat this as five separate, sequentially
dependent surfaces — (1) schema/config split + `legacy_view` + backtester
`--strategy`, (2) `bot/main.py` dispatch fix, (3) `strategy.py` bull-call
pure functions, (4) `store.py` migration + strategy-tagged reads, (5)
`service.py` multi-job composition + per-strategy dispatch — and land the
D-26 field-for-field equivalence test (Task in surface 1) before touching
`service.py`, since every later surface depends on `load_options_config`
still returning byte-for-byte the same `OptionsConfig` for
`tasty_credit_spreads`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Config parsing/validation (`strategies` + legacy shapes) | Backend process (bot/options/config.py) | — | Single process, no client/server split; config lives entirely in-process |
| Strategy pure-function core (strike selection, sizing, exits) | Backend process (bot/options/strategy.py) | — | D7 purity contract: no I/O, replayed identically by the Phase 9 backtester |
| Cross-process read of equity watchlist | Backend process (new `bot/options/universe.py` or similar) | Database (SQLite, read-only) | Same machine, same filesystem; no network hop — a raw `sqlite3` read-only URI connection into a sibling process's DB file |
| Position/leg persistence + migration | Database (SQLite via `OptionsStore`) | Backend process | `OptionsStore` owns schema; `service.py` is the only writer |
| Job scheduling / dispatch per strategy | Backend process (`bot/options/service.py`, APScheduler) | — | In-process cron, no external scheduler |
| Order execution (LegExecutor) | Backend process → Broker (Moomoo OpenD) | — | Unchanged; generic leg/side list, structure-agnostic |
| Backtester config projection | Backend process (`backtester/options_run.py`) | — | Offline CLI, no broker, no shared state with the live bot |
| Process entry / strategy-file dispatch | Backend process (`bot/main.py`) | — | Decides which of two disjoint composition roots (equity vs. options) to build |

This phase has no browser, CDN, or client tier — it is 100% backend/process/DB.

## Standard Stack

### Core

No new libraries. Every dependency this phase needs is already installed and
already imported by the files it touches:

| Library | Version (installed) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `jsonschema` | 4.26.0 `[VERIFIED: pip3 show jsonschema]` | Structural validation of `rules_options.json` | Already the project's only config-schema library (`bot/config/schema.py`, `bot/options/schema.py`) |
| `apscheduler` (`APScheduler`) | 3.11.2 `[VERIFIED: pip3 show apscheduler]` | Cron/interval jobs for per-strategy entry scans | Already used by `bot/options/service.py` and the equity `bot/service/bot.py` |
| `sqlite3` (stdlib) | Python 3.x builtin | Options DB + read-only cross-process watchlist read | Zero new dependency; `bot/state/store.py` already the pattern for read-only URI style access is new but the module itself is stdlib |

### Supporting

None. This phase does not add a package.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain `sqlite3` read-only URI for the watchlist reader | Importing `bot.state.store.StateStore` and calling `get_watchlist_codes()` | Explicitly rejected by D-17 — importing `StateStore` would pull in the full equity state machinery and its own migration runner into the options process; a five-line stdlib query is enough and keeps the "options bot never writes to the equity DB" invariant trivially true by construction (no `StateStore.open()` call, ever) |
| Loader-side fail-closed checks (D-11) | `jsonschema` `if/then/else` or `oneOf` conditional schema for IV-gate-required-per-structure | This codebase has **no existing precedent** for conditional JSON Schema (`_IMPLEMENTED_EXIT_MODELS` in `bot/config/loader.py` is the established pattern: schema stays structural/type-only, business-rule fail-closed checks are plain Python in the loader) `[VERIFIED: bot/config/loader.py:38,276; bot/options/config.py:36]`. Follow the existing pattern, don't introduce `jsonschema` conditionals as a first in this codebase. |

**Installation:** none required.

## Package Legitimacy Audit

Not applicable — this phase installs zero external packages. No `pip install`
or `npm install` step exists in any plan for this phase.

## Architecture Patterns

### System Architecture Diagram

```
                         rules_options.json (on disk)
                                    │
                    ┌───────────────┴───────────────┐
                    │  bot/options/config.py         │
                    │  - detects "strategies" key     │
                    │    vs legacy flat shape          │
                    │  - jsonschema structural check    │
                    │  - loader fail-closed checks      │
                    │    (dup names, XOR universe,      │
                    │     IV-gate-required-for-credit)  │
                    └───────────┬───────────┬─────────┘
                                │           │
                 load_options_config()   load_options_book()
                 (flat OptionsConfig,    (OptionsBook: tuple of
                  ONE strategy, default   flat OptionsConfig per
                  = first in list)        strategy + shared blocks)
                                │           │
              ┌─────────────────┘           └───────────────┐
              ▼                                              ▼
  backtester/options_run.py            bot/main.py ──dispatch fix──▶ bot/options/service.py
  scripts/uat_options_probe.py                                        (OptionsBot)
  (via legacy_view(raw,name)                                           │
   BEFORE apply_overrides)                                             │
                                                                        ▼
                                                     ┌──────────────────────────────────┐
                                                     │ per-strategy entry-scan jobs       │
                                                     │  tasty_credit_spreads:             │
                                                     │    universe = fixed ETF list       │
                                                     │  super_bull_call:                  │
                                                     │    universe_source=equity_watchlist│
                                                     │    ──▶ bot/options/universe.py     │
                                                     │        (new, read-only sqlite3     │
                                                     │        URI into data/bot_state.db) │
                                                     └───────────┬────────────────────────┘
                                                                 ▼
                                            bot/options/strategy.py (pure core)
                                            pick_strikes(structure="bull_call_spread")
                                            manage_decision_debit(...)
                                                                 ▼
                                            bot/options/execution.py (LegExecutor)
                                            — UNCHANGED, generic leg/side list
                                                                 ▼
                                            bot/options/store.py (OptionsStore)
                                            option_positions.strategy_name (new column)
                                                                 ▼
                                          ONE manage job, dispatches on
                                          pos["strategy_name"] to the right
                                          cfg + manage_decision function
                                                                 ▼
                                     global daily-loss breaker / BP cap / one-per-underlying
                                     (never per-strategy — D-22)
```

### Recommended Project Structure

No new top-level packages. One new module, placement per the CONTEXT's
"Claude's Discretion" note:

```
bot/options/
├── config.py       # load_options_config (unchanged signature+return type),
│                   # NEW load_options_book, NEW legacy_view
├── schema.py       # OPTIONS_SCHEMA (legacy, frozen) + NEW schema for the
│                   # `strategies` shape (structural only)
├── strategy.py     # + bull_call_spread branch in pick_strikes, +
│                   # manage_decision_debit
├── store.py        # + strategy_name in _POSITION_COLUMNS, insert/read pass-through
├── service.py      # per-strategy job registration + dispatch
├── execution.py    # UNCHANGED (D-25)
└── universe.py     # NEW — equity_watchlist reader (plain sqlite3, no StateStore import)

bot/state/migrations.py   # NEW _migration_0007 (guarded ALTER TABLE, mirrors 0002/0004)
bot/main.py                # dispatch check updated to also match `"strategies"` key
backtester/options_run.py  # + --strategy flag, legacy_view() call before apply_overrides
```

### Pattern 1: Loader-side dispatch on file shape, not a schema `oneOf`

**What:** `load_options_config`/`load_options_book` read the raw dict once,
check `"strategies" in data`, and branch to one of two code paths — the
existing flat-schema path (byte-identical to today, D-06) or a new
strategies-schema path. Every fail-closed business rule (duplicate names,
XOR of `universe`/`universe_source`, IV-gate keys required only for credit
structures) is a plain `if` in Python, exactly mirroring the existing
`_IMPLEMENTED_STRUCTURES` guard.

**When to use:** Any time a config file must support two structurally
different shapes without regressing existing callers — this is the
established idiom in this codebase (`bot/config/loader.py`'s
`_IMPLEMENTED_EXIT_MODELS`), not something to reinvent with `jsonschema`
`anyOf`/`oneOf`.

**Example:**
```python
# Source: bot/config/loader.py:38, bot/config/loader.py:276 (existing pattern)
_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)
...
if model_raw not in _IMPLEMENTED_EXIT_MODELS:
    raise ConfigError(f"exit.model '{model_raw}' is not implemented; ...")
```
The bull-call phase's `_IMPLEMENTED_STRUCTURES` guard in
`bot/options/config.py:36,167` already does exactly this for
`structure.type`; D-11's new checks (duplicate names, universe XOR, IV-gate
requiredness) are the same shape of check, just new call sites in the same
function.

### Pattern 2: Field relocation during flattening (risk/service globals)

**What:** The *shipped* `OPTIONS_SCHEMA` today nests
`sizing_equity_usd`/`max_bp_usage_pct`/`daily_loss_limit_pct` inside
`sizing`, and `manage_interval_min` inside `manage`
`[VERIFIED: bot/options/schema.py:122-140,145-162]`. The design's `strategies`
shape (§4/D-02) moves these four keys to a shared top-level `risk`/`service`
block, once, for the whole file. `load_options_config`'s flattening step for
the strategies-shape path must read them from `data["risk"]`/`data["service"]`
and copy them onto every per-strategy `OptionsConfig`, while the legacy-shape
path keeps reading them from `sizing`/`manage` exactly as it does today
(unchanged code path, D-06). This is not a rename with global reach — it is a
JSON-key relocation inside one function's mapping logic.

**Example (current legacy mapping, do not change this branch):**
```python
# Source: bot/options/config.py:200,205,207 (verified current behavior)
sizing_equity_usd=float(sz["sizing_equity_usd"]),
daily_loss_limit_pct=float(sz["daily_loss_limit_pct"]),
manage_interval_min=int(mg["manage_interval_min"]),
```

### Pattern 3: Dataclass field addition without breaking positional/kwarg construction

**What:** `OptionsConfig` is currently a `@dataclass` with **zero
Python-level defaults on any field** — every field is constructed via an
explicit keyword argument in `load_options_config`'s single `return
OptionsConfig(...)` call `[VERIFIED: bot/options/config.py:43-110,173-229]`.
Python requires all dataclass fields *without* a default to precede fields
*with* a default (unless `kw_only=True`, not used here). D-09 says
`OptionsConfig` "gains defaulted fields" — the safe way to satisfy this
without hunting for field-order bugs is: **do not give the new fields a
`dataclass`-level default value at all.** Declare them as plain
`Optional[X]` (or `str`/etc.) fields with no `= ...` default, appended at the
end of the class, and have *both* loader code paths (legacy and
strategies-shape) always pass an explicit value for every field — `None`
where the design says "None for `bull_call_spread`" — in the single
`return OptionsConfig(...)` call each path builds. This avoids field-ordering
errors entirely and matches the existing 100%-explicit-kwargs construction
style.

**When to use:** Any time a dataclass with no existing defaults needs new
optional fields and you want to avoid `TypeError: non-default argument
'x' follows default argument`.

### Pattern 4: Structure-dependent `manage` block, not just `entry`

**What:** The CONTEXT's D-05 only calls out the `entry` block's IV-gate keys
as conditionally required. But comparing the design spec §4's two example
strategy blocks shows the **`manage` block also differs by structure type**:
`tasty_credit_spreads.manage` has `profit_target_pct_of_credit`,
`manage_dte`, `stop_loss_credit_multiple`, `assignment_guard_dte`; the
`super_bull_call.manage` example has only `profit_target_pct_of_max`,
`manage_dte`, `assignment_guard_dte` — **no** `profit_target_pct_of_credit`,
**no** `stop_loss_credit_multiple`. The loader's per-structure requiredness
check (D-11/D-05) must therefore cover **both** the `entry` IV-gate keys
*and* the `manage` profit-target key pair, or a `bull_call_spread` strategy
entry missing `profit_target_pct_of_credit` will raise `KeyError`/schema
error instead of the intended `ConfigError` with a clear message. Likewise
`structure.short_delta` / `min_credit_to_width` are credit-only
(`long_delta`/`max_debit_to_width` are debit-only).

**Fields that become structure-conditional (present for credit, absent/None
for `bull_call_spread`), consolidated from D-05/D-09/D-16 + the spec §4
example diff:**

| Field | Present for | Home block |
|---|---|---|
| `ivr_min`, `fear_drop_pct`, `fear_ivr_min` | credit only (required) | `entry` |
| `ivp_min` | credit only (optional even there) | `entry` |
| `short_delta`, `min_credit_to_width` | credit only | `structure` |
| `long_delta`, `max_debit_to_width` | debit only | `structure` |
| `profit_target_pct_of_credit`, `stop_loss_credit_multiple` | credit only | `manage` |
| `profit_target_pct_of_max` | debit only | `manage` |
| `manage_dte`, `assignment_guard_dte` | both | `manage` |

### Anti-Patterns to Avoid

- **Editing shipped migration `_migration_0006`:** The migrations module's
  own docstring states the rule explicitly: *"D-08: never edit shipped
  migrations 0001-0005 — new objects always in 0006"* `[VERIFIED:
  bot/state/migrations.py:323]`. By the same logic, `_migration_0006` (which
  created `option_positions`/`option_legs` for Phase 8) must **not** be
  edited either — add a new `_migration_0007` with a guarded
  `ALTER TABLE option_positions ADD COLUMN strategy_name TEXT NOT NULL
  DEFAULT 'tasty_credit_spreads'`, following the exact `PRAGMA
  table_info(...)` guard pattern already used twice (`_migration_0002` on
  `daily_scan`, a positions-table ALTER at `bot/state/migrations.py:214-217`)
  `[VERIFIED: bot/state/migrations.py:95-126,214-217]`.
- **Importing `bot.state.store.StateStore` from `bot/options/`:** D-17
  explicitly forbids this — it would give the options process a write-capable
  handle (even if unused) into the equity DB, and pulls the equity migration
  runner into a process that must never touch that schema.
- **Trusting `os.path.exists()` before opening the read-only URI connection:**
  A TOCTOU race is possible (file removed between check and open), and more
  importantly a *locked* or *permission-denied* DB does not fail
  `os.path.exists` — it only fails at `sqlite3.connect(...)`. Catch
  `sqlite3.OperationalError` around the actual connect+query, not a
  pre-flight existence check (see Code Examples for the confirmed exception
  message).
- **Assuming a JSON Schema `oneOf`/conditional will express D-05's
  per-structure requiredness:** see Pattern 1 — this codebase has zero
  precedent for that jsonschema feature; don't introduce it here.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-process read of another bot's SQLite state | A custom file-watcher, polling loop, or IPC socket | `sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=...)` (stdlib) | SQLite's own WAL mode already supports concurrent readers safely; a read-only URI connection is the minimal correct primitive, already proven to work against a live WAL writer in this repo's own probe (see Code Examples) |
| Guarded/idempotent schema migration | A version-string comparison or a custom "has this run" table | `PRAGMA table_info(table)` existing-columns set, guarding each `ALTER TABLE` | Already the house pattern, used twice in `bot/state/migrations.py`; SQLite's `ALTER TABLE ADD COLUMN` has no `IF NOT EXISTS` clause, so this guard is the only idempotent option and it is already battle-tested in this codebase |
| Per-structure config validation | A generic multi-schema/plugin validation framework | Plain Python `if structure_type == ...: raise ConfigError(...)` in the loader | One-off business rules for two structure types do not need a rules engine; the codebase already rejected this complexity for `_IMPLEMENTED_EXIT_MODELS`/`_IMPLEMENTED_STRUCTURES` |

**Key insight:** Every "hand-roll or not" question in this phase resolves to
"use the pattern already living three lines away in this same package." There
is no case here where a third-party library would help — the whole phase is
glue between things the codebase already has.

## Runtime State Inventory

This phase is not a rename/rebrand, but it does ship a live schema migration
and a config-file format change to a bot that the operator restarts
(per STATE.md: *"New DB column ships with the code that reads it; restart the
options bot only after merge to develop"* and *"Bot runs from main repo
develop"*). Applying the same discipline:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `option_positions` rows written before this phase have no `strategy_name` value | Migration default `'tasty_credit_spreads'` (D-18) is historically correct for every existing row — no data migration script needed beyond the `ALTER TABLE ... DEFAULT` itself |
| Live service config | The running options-bot process (if any) holds the OLD flat `rules_options.json` in memory via its already-loaded `OptionsConfig` | None — the bot must be **restarted** after merge for the new file shape/loader to take effect (same "restart after merge" rule already in STATE.md for Phase 8 fixes); no hot-reload exists or is expected |
| OS-registered state | None found — no Task Scheduler / launchd / pm2 registration references `rules_options.json`'s shape, only its path | None |
| Secrets/env vars | None — no env var name references the config shape; `FUTU_ACC_ID`, `TELEGRAM_*` etc. are unaffected | None |
| Build artifacts | None — no compiled/installed artifact caches the old schema | None |

**Also flag:** the repo-root `rules_options.json` itself is checked into
git and will be rewritten by this phase's plan (D-26). This is a **config
content change**, not a migration, but it is the one file every consumer in
"Consumers that must keep working" reads directly from disk — get the D-26
field-for-field equivalence test green before merging, since it is the only
thing standing between "refactor" and "silent live-trading behavior change."

## Common Pitfalls

### Pitfall 1: `bot/main.py`'s strategy-name dispatch breaks silently for the new file shape

**What goes wrong:** `bot/main.py:67-80` peeks
`json.load(f).get("strategy_name", "")` and only routes to
`bot.options.service.main` when that value equals
`"tasty_credit_spreads"` exactly `[VERIFIED: bot/main.py:67-80]`. The moment
`rules_options.json` becomes `{"strategies": [...], "risk": {...}, ...}` this
key is absent (`.get(..., "")` returns `""`), the check fails, and execution
falls through into the **equity** bot's construction path with
`rules_options.json` as the rules file.

**Why it happens:** The Phase 8 dispatch mechanism was designed around a
single flat file with one canonical marker key; nobody updated it when the
multi-strategy shape (which drops that key) was designed. It is not listed
in `11-CONTEXT.md`'s canonical refs or decisions at all.

**How to avoid:** Add a plan task to update the dispatch check to also match
`"strategies" in data` (in addition to the existing `strategy_name` check,
which must keep working for any operator who still has an old flat
`rules_options.json` on disk per D-06). Add a regression test in
`tests/options/test_dispatch.py` (existing file, already covers exactly this
dispatch surface) asserting a `{"strategies": [...]}` payload routes to
`bot.options.service.main`.

**Warning signs:** `python3 -m bot --rules rules_options.json` after this
phase ships would attempt to construct `TrendJoinLong`/`PositionManager`
against an options config file — `load_strategy_config` will almost
certainly raise `ConfigError` (different required top-level keys in
`bot/config/schema.py`) and exit 1, so the failure is loud, not silent — but
it is the wrong error message pointing the operator at the wrong subsystem,
and it is a hard launch-blocker for the live options bot restart the CONTEXT
already anticipates.

### Pitfall 2: Shared "global" knobs live in the wrong nested block today

**What goes wrong:** A plan that writes the loader's strategies-shape
flattening by analogy to the *legacy* mapping (`sz["sizing_equity_usd"]`,
`mg["manage_interval_min"]`) will read from the wrong dict once those keys
move to top-level `risk`/`service` blocks in the new shape.

**Why it happens:** The design spec's §4 JSON example already shows the
target shape, but the current *implemented* schema (verified above) still
has them nested under `sizing`/`manage` — a plan author skimming only the
design spec (not the current `schema.py`) will not notice the relocation is
required.

**How to avoid:** Two separate mapping code paths in `load_options_config`
(legacy vs. strategies), each reading these four fields from their own
correct location; a unit test constructing a strategies-shape fixture with
different `risk.sizing_equity_usd` than any per-strategy `sizing` block value
would catch a copy-paste of the legacy mapping.

**Warning signs:** `sizing_equity_usd`/`max_bp_usage_pct`/
`daily_loss_limit_pct`/`manage_interval_min` silently missing or `None` when
loading a strategies-shape file (a `KeyError` at load time is actually the
*good* outcome here — the bad outcome is these values coming back `0`/`None`
and passing size-position math with a zero equity base).

### Pitfall 3: `manage` block requiredness also varies by structure, not just `entry`

See Architecture Pattern 4 above — `profit_target_pct_of_credit`/
`stop_loss_credit_multiple` vs `profit_target_pct_of_max` is a second
structure-conditional requiredness surface the CONTEXT's D-05 only mentions
for `entry`. Missing this makes the fail-closed checks incomplete: a
`bull_call_spread` strategy block that omits `profit_target_pct_of_max`
would currently only be caught by a raw `KeyError` inside `service.py` at
manage time (a runtime crash on the first manage tick after an entry), not a
config-load-time `ConfigError`.

### Pitfall 4: A read-only SQLite connection into a WAL-mode DB still needs directory write access

**What goes wrong:** A plan or reviewer might assume `mode=ro` means "the
options bot never writes anything to the filesystem for this read," and
therefore skip checking directory permissions in the fail-closed error
handling.

**Why it happens:** `mode=ro` genuinely prevents writing table *content*, but
a live interpreter probe in this repo confirms SQLite still creates
`-wal`/`-shm` companion files on a read-only connection's first touch of a
DB whose header declares `journal_mode=WAL`, if those files are not already
present (see Code Examples for the exact probe and output).

**How to avoid:** In production this is a non-issue — the equity bot keeps
`data/bot_state.db-wal`/`-shm` present and the directory writable as long as
it is running. Document this precisely in the D-27-style provenance/decision
comment (say "no writes to the DB's *data*", not "zero filesystem I/O") so a
future reviewer does not file a false-positive security concern, and so the
options bot's own directory permissions are never accidentally locked down
in a way that would break the reader.

**Warning signs:** `sqlite3.OperationalError: attempt to write a readonly
database` or `unable to open database file` if the `data/` directory itself
is ever made read-only for the options-bot process while the equity bot has
not yet created the WAL files (e.g. equity bot not yet started that day).

### Pitfall 5: `get_option_positions` / EOD queries do not yet filter or group by `strategy_name`

**What goes wrong:** `OptionsStore.get_option_positions(statuses)` returns
every position matching a status tuple, with no `strategy_name` filter
`[VERIFIED: bot/options/store.py:143-171]`. `_scan_and_open`'s busy-underlying
set, `open_max_loss_total`, and `open_count` in `service.py:508-513` are
computed once per entry-scan call from **all** active positions regardless
of strategy — which is *correct* per D-22 (BP headroom and
one-per-underlying stay global) but means the **per-strategy** counters
(`opened_today` via `count_opened_on`, `max_concurrent_positions`) need a
**new** filtered query or a Python-side filter on the already-fetched list,
not a blind reuse of `count_opened_on(date_iso)` (which counts positions
opened that day across **all** strategies today, with no `strategy_name`
argument) `[VERIFIED: bot/options/store.py:183-190]`.

**How to avoid:** Either add a `strategy_name` parameter to
`count_opened_on`/a new per-strategy concurrent-count helper in
`OptionsStore`, or filter the already-fetched `active`/positions list in
Python by `p["strategy_name"] == cfg.name` inside `service.py`. The CONTEXT's
"Claude's Discretion" section does not call this out explicitly, but D-22
requires it functionally — flag as an Open Question for the planner to
decide the store-vs-Python-filter split.

## Code Examples

### Guarded idempotent ALTER TABLE (the pattern to mirror for `strategy_name`)

```python
# Source: bot/state/migrations.py:214-217 (verified current pattern, used twice)
existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
for col, decl in _NEW_COLUMNS:
    if col not in existing:
        conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
```
Apply the identical shape to `option_positions` in a **new** `_migration_0007`
(never edit `_migration_0006` — see Anti-Patterns), and append it to the
`MIGRATIONS` list at `bot/state/migrations.py:362-369`.

### Read-only SQLite URI: confirmed behavior (live probe run in this repo, 2026-09-24)

```python
# Missing file -> fails closed with OperationalError (NOT silently created):
>>> sqlite3.connect("file:/tmp/missing.db?mode=ro", uri=True, timeout=5)
sqlite3.OperationalError: unable to open database file

# WAL-mode DB, reader opened WHILE a writer holds an open write transaction:
# read succeeds and sees the last COMMITTED state (correct MVCC behavior) —
# reader is never blocked by a concurrent writer in this repo's actual usage
# pattern (equity bot writes short transactions, options bot reads once/day).
```
Recommended reader shape for `bot/options/universe.py`:
```python
import sqlite3

def read_equity_watchlist(db_path: str, scan_date_iso: str, cap: int = 20) -> list:
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=5,
        )
        rows = conn.execute(
            "SELECT code FROM daily_scan WHERE scan_date=? ORDER BY rank ASC",
            (scan_date_iso,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows][:cap]
    except sqlite3.OperationalError:
        # missing file, locked DB, permission error -- all fail closed to []
        return []
```
This mirrors the exact query already proven correct in
`bot.state.store.StateStore.get_watchlist_codes`
`[VERIFIED: bot/state/store.py:692-714]` — D-17 requires re-implementing the
query with a plain `sqlite3` connection rather than importing `StateStore`,
which this shape satisfies (no import of `bot.state.store` at all).

### `manage_decision_debit` sign math — worked example verified against the CONTEXT's NVDA numbers

```
Entry: BUY 225-call @ mid 3.58 (long leg), SELL 235-call @ mid 1.62 (short leg)
debit = mid(long) - mid(short) = 3.58 - 1.62 = 1.96   (D-13 gate: 1.96 <= 0.30*10 = 3.0 -> passes)
Stored: credit_per_spread = -1.96                      (D-19 sign convention)

mark_spread(legs, quotes) = sum(mid if side=="SELL" else -mid for leg in legs)
                           = mid(short SELL leg) - mid(long BUY leg)

At entry (same quotes): mark = 1.62 - 3.58 = -1.96
manage_decision_debit's spread value = -mark = 1.96 (== debit paid; 0 profit yet -- correct)

At max value (stock far above short strike, spread worth ~width=10 at expiry):
  short call mid ~= (underlying - 235), long call mid ~= (underlying - 225)
  mark = mid(short) - mid(long) = (underlying-235) - (underlying-225) = -10
  spread value = -mark = 10
  profit = (-mark - debit) = 10 - 1.96 = 8.04   <-- matches the CONTEXT's
                                                      "max profit 8.04 per spread" exactly
```
This confirms `manage_decision_debit`'s design formula
(`(-mark - debit) >= profit_target_pct_of_max/100 * (width - debit)`) is
internally consistent with the existing, unmodified `mark_spread` function —
**no change to `mark_spread` itself is needed**, only the new
`manage_decision_debit` caller-side function per D-15.

## State of the Art

Not applicable in the usual sense (no external library/API evolved here).
The one internal "old vs new" worth naming:

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Single-strategy flat `rules_options.json`, `OptionsConfig` = the whole file | `strategies` array + shared `risk`/`execution`/`service`, `load_options_config` = one flattened strategy view, `load_options_book` = all of them | This phase (2026-09-24) | Every existing consumer (`backtester/options_run.py`, `scripts/uat_options_probe.py`, `bot/options/service.py`'s single-cfg construction) keeps working via `load_options_config`; only `service.py`'s composition root changes to use `load_options_book` |

**Deprecated/outdated:** None — `load_options_config`'s signature and return
type are explicitly preserved (D-07/MSO-02), not deprecated.

## Assumptions Log

Every factual claim above was verified by direct file read, grep, or a live
interpreter probe run in this repository during this research session — none
rely on training-data knowledge of an external library or API. The two items
below are architectural *recommendations* (not facts) that the planner should
treat as proposals, not locked decisions, since they go slightly beyond what
`11-CONTEXT.md` explicitly decided:

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | New `OptionsConfig` fields should carry no Python-level dataclass default (values always passed explicitly by both loader paths) rather than using `= None` defaults | Architecture Pattern 3 | Low — if the planner instead gives the new fields `= None` defaults and puts them last, that also works (dataclass field-ordering rule is only violated if a defaulted field precedes a non-defaulted one); either approach is valid, this is a style recommendation, not a fail-closed requirement |
| A2 | Per-strategy `opened_today`/concurrent-position counters need a new store method or a Python-side filter (not identified as a concrete gap in `11-CONTEXT.md`) | Common Pitfall 5 | Medium — if unaddressed, D-22's "per-strategy caps" requirement (MSO-08) silently degrades to a global cap shared across both books, which is a functional bug, not just a style issue; flagged as an Open Question below for the planner to resolve explicitly |

**If this table is empty:** N/A — see above, both entries are
recommendations flagged for planner attention, not unverified facts.

## Open Questions (RESOLVED)

1. **Where does the per-strategy `opened_today`/concurrent-count filter live — a new `OptionsStore` method, or a Python-side filter over `get_option_positions()`'s existing full result?**
   - What we know: `count_opened_on(date_iso)` has no `strategy_name` parameter today `[VERIFIED: bot/options/store.py:183-190]`; `get_option_positions(statuses)` returns everything active, un-filtered by strategy.
   - What's unclear: whether the planner should add `count_opened_on(date_iso, strategy_name=None)` (extending the existing method, backward compatible since `None` = today's behavior) or do a one-line Python filter in `service.py` (`[p for p in active if p["strategy_name"] == cfg.name]`) since the position list is already fully in memory for the busy-underlying/BP-headroom pass.
   - Recommendation: the Python-side filter is the smaller diff (zero store-schema/SQL changes, no new method to test in isolation) and `get_option_positions` already returns the full row including the new `strategy_name` column (D-18) — prefer this unless the position count ever grows large enough that a SQL `COUNT` becomes worth it (not the case here: this is a handful of ETFs + a 20-code-capped watchlist).
   - RESOLVED: Python-side filter on `strategy_name` over the already-loaded active positions (plan 11-05 T2).

2. **Exact wording/placement of the `bot/main.py` dispatch fix — new elif branch, or broaden the existing condition?**
   - What we know: current check is `if strategy_name == "tasty_credit_spreads":` at `bot/main.py:75` — a single string equality.
   - What's unclear: whether to change the peek itself (read `data.get("strategy_name", "") or ("strategies" in data and "multi")`-style sentinel) or keep the peek simple and add a second top-level check (`if strategy_name == "tasty_credit_spreads" or "strategies" in data:`).
   - Recommendation: the second form (`or "strategies" in data`) is the smallest diff and keeps the existing D5 test file (`tests/options/test_dispatch.py`) mostly intact — just add a new test case for the `strategies`-shape payload alongside the existing `strategy_name` one.
   - RESOLVED: `if strategy_name == "tasty_credit_spreads" or "strategies" in data:` in the same task as the `rules_options.json` conversion (plan 11-04 T2).

3. **Whether `legacy_view(raw, name)` needs to also express the `manage`-block structure-conditional keys (Pitfall 3) when a strategies-shape file's chosen strategy is a credit structure with the moved `risk`/`service` globals.**
   - What we know: A2/D-10 says `legacy_view` output must itself be a valid legacy file so `apply_overrides` + `load_options_config`'s existing temp-file validation path (`backtester/options_run.py:218-228`) is unchanged.
   - What's unclear: exactly which manage/entry keys `legacy_view` must synthesize back into the flat shape (`sizing_equity_usd` back into `sizing`, `manage_interval_min` back into `manage`) for a chosen credit strategy, since the legacy schema still requires them nested there.
   - Recommendation: `legacy_view` should be the exact inverse of `load_options_config`'s strategies-shape flattening step — build it by literally reusing that same field-relocation logic (do not write two independent mapping tables that can drift from each other).
   - RESOLVED: `legacy_view` and `_wrap_legacy` share one relocation table `_RELOCATED` (plan 11-01 T1).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `jsonschema` | Schema validation of both file shapes | Yes | 4.26.0 | n/a — already required |
| `APScheduler` | Per-strategy cron jobs | Yes | 3.11.2 | n/a — already required |
| `sqlite3` (stdlib) | Options store + equity-watchlist reader | Yes | Python builtin | n/a |
| Moomoo OpenD (127.0.0.1:11111) | Live entry/manage/reconcile (not exercised by unit tests) | Not probed in this research session (research is offline/code-only) | — | Tests mock the gateway; live UAT is a separate operator step per D-27/CONTEXT scope |

No missing dependencies. This phase can be planned and unit-tested with zero
new installs.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (installed, already the project standard) |
| Config file | none dedicated — project root `pytest.ini`/`pyproject.toml` not found; tests discovered via `tests/` package layout |
| Quick run command | `python3 -m pytest -q tests/options tests/backtester/options` (261 tests, 3.7s, verified this session) |
| Full suite command | `python3 -m pytest -q` (1134 passed, 1 skipped, 64.5s, verified this session — this is the regression baseline for this phase) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MSO-01 | `strategies` array loads; `load_options_book` returns every strategy flattened; legacy shape still loads | unit | `pytest tests/options/test_config.py -x` | ✅ (extend) |
| MSO-02 | `load_options_config(path, strategy=None)` unchanged contract; `--strategy` + `legacy_view` in `options_run` before `apply_overrides`; debit structure rejected | unit | `pytest tests/backtester/options/test_options_run.py -x` | ✅ (extend) |
| MSO-03 | `ConfigError` on dup names, both/neither universe keys, unknown `universe_source`, unimplemented structure, missing IV-gate keys for credit | unit | `pytest tests/options/test_config.py -k fails_closed -x` | ✅ (extend `TestLoadOptionsConfigFailsClosed`) |
| MSO-04 | `bull_call_spread` strike selection, 1/4-rule gate, sizing with `debit*100` | unit | `pytest tests/options/test_strategy.py -k bull_call -x` | ✅ (extend, existing file 598 lines) |
| MSO-05 | `manage_decision_debit` order + sign math (worked NVDA example as a literal test case) | unit | `pytest tests/options/test_strategy.py -k manage_decision_debit -x` | ✅ (extend) |
| MSO-06 | `equity_watchlist` reader: read-only, cap 20, missing/locked/empty → `[]` | unit | `pytest tests/options/test_universe.py -x` | ❌ Wave 0 — new file, new module |
| MSO-07 | `strategy_name` column migration idempotent; legacy rows default; debit `credit_per_spread` negative round-trips | unit | `pytest tests/options/test_store.py -x` | ✅ (extend, existing file 232 lines) |
| MSO-08 | Per-strategy entry-scan job ids; one manage job dispatches per `strategy_name`; per-strategy caps vs. global breaker/BP/one-per-underlying | unit + integration | `pytest tests/options/test_service.py -x` | ✅ (extend, existing file 657 lines) |
| MSO-09 | Shipped `rules_options.json` converted; D-26 field-for-field equivalence to pre-change fixture; safety invariants unchanged; `bot/main.py` dispatch still routes correctly | unit + regression | `pytest tests/options/test_config.py::TestShippedRulesOptionsJson tests/options/test_dispatch.py -x` | ✅ (extend both) |

### Sampling Rate
- **Per task commit:** `python3 -m pytest -q tests/options tests/backtester/options` (~4s)
- **Per wave merge:** `python3 -m pytest -q` (full suite, ~65s)
- **Phase gate:** Full suite green (1134+ passed, 0 new failures, 0 new skips beyond the 1 pre-existing) before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/options/test_universe.py` — new file covering MSO-06 (read-only reader: missing file, locked DB via a held write lock, empty result, cap-at-20, happy path against a real WAL-mode fixture DB)
- [ ] `bot/options/universe.py` — the module itself does not exist yet (module placement is Claude's discretion per CONTEXT)
- [ ] `_migration_0007` in `bot/state/migrations.py` — does not exist yet; needs its own idempotence test alongside the existing `_migration_0002`/`_migration_0004` pattern tests (check `tests/` for an existing migrations test file to extend, e.g. wherever `_migration_0006` itself is tested)
- [ ] Framework install: none — pytest, jsonschema, APScheduler all already present

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No new auth surface; this is an internal config/process refactor |
| V3 Session Management | No | n/a |
| V4 Access Control | Yes | The equity-watchlist reader must be structurally incapable of writing to the equity DB — enforced by never importing `bot.state.store.StateStore` and only ever opening `mode=ro` URI connections (D-17); this is the phase's one real cross-boundary access-control surface |
| V5 Input Validation | Yes | `jsonschema` structural validation (already in place) + new loader-side fail-closed business-rule checks (D-11) for the `strategies` config shape |
| V6 Cryptography | No | No secrets/crypto touched by this phase |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Options bot silently writes to / corrupts the equity bot's shared paper-account DB | Tampering | D-17: read-only URI connection only, no `StateStore` import, no write methods anywhere in the new `universe.py` module; verified via `sqlite3.OperationalError` on any write attempt against a `mode=ro` connection |
| A malformed/partial `strategies` config silently loads with wrong risk parameters (e.g. `sizing_equity_usd=0`) instead of failing closed | Tampering / Denial of Service (against the operator's capital) | `ConfigError` fail-closed on every structurally-valid-but-semantically-wrong shape (D-11); D-26's field-for-field equivalence test as the specific regression guard for the shipped file |
| `bot/main.py` dispatch misroute sends the options config through the equity loader (Pitfall 1) | Tampering (wrong process acts on wrong config) | Update the dispatch check to also match `"strategies" in data`; regression test in `tests/options/test_dispatch.py` |
| Orphaned positions whose `strategy_name` is not in the loaded book get silently managed with the wrong strategy's exit parameters | Tampering / Repudiation (wrong money decisions attributed to wrong strategy) | D-29: startup reconcile sets any such row `NEEDS_ATTENTION` + Telegram alert + audit event; manage loop skips `NEEDS_ATTENTION` rows (existing pattern, `bot/options/service.py:309-346`) |
| A future `structure.type` enum addition passes schema but has no `pick_strikes`/`size_position` support | Tampering (fail-open into unimplemented behavior) | `_IMPLEMENTED_STRUCTURES` gains `"bull_call_spread"` explicitly (D-11); the existing defense-in-depth pattern (schema enum is necessary but not sufficient) is unchanged |

## Sources

### Primary (HIGH confidence — direct file reads / greps / live probes, this session)
- `bot/options/config.py` (full read) — current `OptionsConfig` field list, `load_options_config` mapping, `_IMPLEMENTED_STRUCTURES`
- `bot/options/schema.py` (full read) — current `OPTIONS_SCHEMA`, current location of `sizing_equity_usd`/`max_bp_usage_pct`/`daily_loss_limit_pct`/`manage_interval_min`
- `bot/options/strategy.py` (full read) — `pick_strikes`, `size_position`, `mark_spread`, `manage_decision` exact current implementations
- `bot/options/store.py` (full read) — `_POSITION_COLUMNS`, `get_option_positions`, `get_realized_pnl_on`, `count_opened_on` exact signatures
- `bot/options/service.py` (full read) — `_register_jobs`, `_scan_and_open`, `_try_open`, `_manage_position`, `reconcile`, `_check_daily_breaker`, `main()`
- `bot/options/execution.py` (partial read) — `LegExecutor` open/close leg ordering, confirmed structure-agnostic
- `bot/main.py` (partial read, lines 40-85) — the strategy-name dispatch check and its exact failure mode
- `bot/state/store.py` (partial read) — `get_watchlist_codes`, `DEFAULT_DB_PATH`, `daily_scan` query shape
- `bot/state/migrations.py` (partial read) — `_migration_0002`/`_migration_0004`/`_migration_0006` guarded-ALTER pattern, `MIGRATIONS` list, D-08 "never edit shipped migrations" rule
- `backtester/options_run.py` (full read) — `apply_overrides`, `main()` ordering, exactly where `--strategy`/`legacy_view` must slot in
- `bot/config/loader.py`, `bot/config/schema.py` (grep) — confirmed no `oneOf`/`anyOf` precedent; `_IMPLEMENTED_EXIT_MODELS` as the established loader-side-check pattern
- `tests/options/conftest.py`, `tests/options/test_config.py`, `tests/options/test_dispatch.py`, `tests/options/test_service.py` (full/partial reads) — exact drift-test assertion that will break (`test_shipped_file_matches_fixture`), existing dispatch test shape, existing service test coverage surface
- `tests/backtester/options/test_options_run.py` (grep) — confirmed `load_options_config`/`apply_overrides` usage surface
- Live `python3` interpreter probes (this session) — `sqlite3` `mode=ro` on missing file, on a WAL-mode DB during an open writer transaction, and WAL companion-file creation behavior on a read-only connection
- `python3 -m pytest -q` and scoped `tests/options tests/backtester/options` (this session) — regression baseline: 1134 passed / 1 skipped full suite (64.5s), 261 passed scoped (3.7s)
- `.planning/config.json` (read) — confirmed `nyquist_validation: true`, `security_enforcement: true`, `security_asvs_level: 1`

### Secondary (MEDIUM confidence)
- None used — no WebSearch/Context7 lookups were needed for this phase; it is entirely internal-codebase research.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new dependencies, all versions confirmed installed via `pip3 show`
- Architecture: HIGH — every pattern cited traces to a specific file:line in this repo, cross-checked against the design spec and CONTEXT for discrepancies (two found: field relocation, `bot/main.py` dispatch)
- Pitfalls: HIGH — five pitfalls identified, three from direct code discrepancy (not from the design docs), two from live interpreter probes

**Research date:** 2026-09-24
**Valid until:** No expiry driver — this is internal-codebase research tied to the current commit (`1980c71`); re-verify only if `develop` moves significantly before this phase is planned/executed (check `git log bot/options/ bot/main.py bot/state/migrations.py` for drift since 2026-09-24)
