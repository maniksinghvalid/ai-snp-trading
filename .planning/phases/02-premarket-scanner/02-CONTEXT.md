# Phase 2: Premarket Scanner - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning

<domain>
## Phase Boundary

A runnable premarket scanner (with idempotent intraday re-scans) that produces a top-20-capped daily watchlist and wires live 5m subscriptions for it — the bot's first end-to-end data path. In scope:

- **Universe fetch** — current S&P 500 constituent list as the scan universe (SCAN-01).
- **Daily-bar data** — yfinance daily bars across the ~500-symbol universe (SCAN-06), with ticker-format normalization (`BRK.B`→`BRK-B`) and bounded concurrency (~5 threads).
- **Daily filters** — feed the already-built `TrendJoinLong.passes_daily_filters()` (D1 above prior-day high, D2 prior close > SMA200, D3 gap ≥ 3% & price ≥ $3) (SCAN-02).
- **RVOL baseline** — 14-day baseline from prior completed trading days only, no look-ahead (SCAN-03).
- **Calendar gate** — NYSE trading-day / holiday / half-day awareness via pandas-market-calendars (SCAN-04).
- **Persistence** — idempotent `daily_scan` watchlist write to StateStore (SCAN-05), capped at top-20 by gap % (SCAN-08).
- **Intraday re-scan** — re-scan entrypoint that updates the watchlist idempotently (SCAN-07; scheduling itself is Phase 5).
- **Subscription wiring** — `MoomooGateway.subscribe()` for the capped (top-20) list only, never the full universe (SIG-01).

Requirements in scope: **SCAN-01..08, SIG-01**.

Out of scope this phase: intraday 5m signal evaluation / I1/I2/I3 filters (Phase 3), order placement & position FSM (Phase 4), the APScheduler service that *fires* the scan/re-scan jobs (Phase 5 — Phase 2 delivers the callable scan/re-scan entrypoints, not their schedule), Telegram alert delivery (Phase 5 — the degradation "alert" is a log/surfaced event this phase, promoted to Telegram in Phase 5), and the backtester (Phase 6).
</domain>

<decisions>
## Implementation Decisions

### Universe / Constituent Source
- **D-01:** The S&P 500 **symbol list** is scraped from the Wikipedia S&P 500 constituents page (`pandas.read_html`) on each scan, then **cached to a dated local file**; on scrape failure the scanner **falls back to the last good cached snapshot**. Free, fresh, no broker quota, and survives a bad-network day. (NOT Moomoo `get_plate_stock` — that spends the broker quota the yfinance split exists to avoid.)
- **D-02:** Clarified constraint: **yfinance supplies the daily *bars*, not the membership list** — it has no index-constituents endpoint. The Wikipedia scrape (D-01) produces the symbol list; that list is then fed to yfinance for daily-bar download.
- **D-03:** Cache location lives under the existing gitignored `data/` dir (consistent with `data/bot_state.db` from Phase 1, D-09), filename carries the snapshot date so staleness is visible.

### Intraday Re-scan Merge Semantics
- **D-04:** Each intraday re-scan pass **re-ranks the union** of existing + newly-qualifying candidates by gap % and keeps the top 20 — **but never evicts a candidate that is already subscribed / has a forming or open position**. Best-setups-win without yanking a live 5m feed (or a candidate Phase 3 is mid-evaluation on) out from under the downstream engine.
- **D-05:** Idempotency (SCAN-05) is enforced via the existing `daily_scan` `UNIQUE(scan_date, code)` constraint — a re-scan is a no-op for already-present candidates and an idempotent merge/update for rank changes; re-running a pass does not duplicate rows.

### Data-Degradation Policy
- **D-06:** **Threshold-based** handling of yfinance partial/failed downloads (SCAN-06 criterion #6): while failed symbols are **< 10%** of the universe, **proceed with a partial watchlist and log a warning**; at **≥ 10%** (≈50 of ~500 symbols) **abort the scan and surface an alert** rather than publish a thin/false watchlist. (Threshold confirmed at 10% — tolerant of normal flakiness, aborts on a Yahoo-wide outage.)
- **D-07:** The "alert" in this phase is a **logged / surfaced degradation event** (structlog + audit). Promotion to a Telegram push lands in Phase 5; Phase 2 must not silently emit an empty/partial watchlist.

### Watchlist Persistence Richness
- **D-08:** **Extend the `daily_scan` schema** (new migration) to persist the per-candidate context Phase 3 needs: **prior-day high, prior close, SMA200, gap %, RVOL 14-day baseline, and the originating scan pass** (in addition to the existing `scan_date`, `code`, `gap_pct`, `rank`). Phase 3 reads these from StateStore rather than recomputing/re-fetching — one source of truth, no Phase 2 / Phase 3 disagreement on the same values, fewer yfinance refetches. Explicit typed columns (not a JSON blob) so the watchlist stays queryable.

### Claude's Discretion
Left to research/planner at standard defaults:
- Scanner module decomposition and the exact callable/CLI entrypoint shape for manual/test runs (the *scheduling* of it is Phase 5).
- Exact `MoomooGateway.subscribe()` signature and `SubType.K_5M` wiring for the capped list (SIG-01) — mirror the existing gateway async `run_in_executor` pattern.
- yfinance batch-download mechanics (period/window, retry/backoff per symbol) and the ~5-thread concurrency primitive.
- RVOL-baseline edge cases (insufficient prior history for a symbol) — handle conservatively (exclude / flag), planner to specify.
- Exact dated-cache filename convention and snapshot-staleness warning behavior.
- Whether the schema extension is a fresh migration `0002` vs. amending `0001` (Phase 1 schema is already shipped/used → almost certainly a new ordered migration step per Phase 1 D-08).
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy & Config (source of truth)
- `.planning/PROJECT.md` §"The Strategy — Trend Join Long" — canonical `rules.json` content: universe (`min_price_usd` 3.0), daily filters (D1/D2/D3, `D3_min_gap_pct` 3.0), `I3_rvol_lookback_days` 14. No strategy constant hardcoded in Python.
- `rules.json` (repo root) — the loaded runtime config (CFG-01); the scanner reads filter params from it via the Phase 1 `StrategyConfig` loader, never literals.
- `.planning/REQUIREMENTS.md` §Scanner + §Signals — SCAN-01..08 and SIG-01 acceptance text (this phase's requirement IDs).
- `.planning/ROADMAP.md` §"Phase 2: Premarket Scanner" — goal, 7 success criteria, and the 3 planned plan-slices (02-01/02-02/02-03).

### Architecture & Research
- `.planning/research/SUMMARY.md` §"Phase 2: Premarket Scanner" — Scanner component boundary (snapshot-style daily filter → watchlist), build-order rationale; note the original `get_plate_stock` constituent-source flag is **superseded by D-01** (Wikipedia + cache).
- `.planning/research/PITFALLS.md` — RVOL look-ahead (#4), non-atomic state writes (#5); survivorship-bias note on S&P 500 constituent sourcing (relevant to D-01's dated cache).
- `.planning/research/STACK.md` — pandas-market-calendars 5.x (NYSE calendar), yfinance, pinned deps; APScheduler is Phase 5 not here.

### Phase 1 Foundation (build directly on these)
- `.planning/phases/01-foundation/01-CONTEXT.md` — Phase 1 decisions this phase depends on: `bot/` package layout (D-01), `StateStore` + migration mechanism via `PRAGMA user_version` ordered steps (D-07/D-08), `data/` dir + DB path (D-09), pytest mirror tree (D-11), config-drivenness proven by behavior (D-12).
- `bot/state/migrations.py` — existing `_MIGRATION_0001` incl. the `daily_scan` table (`scan_date, code, gap_pct, rank, created_at`, `UNIQUE(scan_date, code)`); the D-08 extension adds an ordered migration step here.
- `bot/state/store.py` — `StateStore` (`open`/`close`/`conn`, `atomic_write_json`); the scanner persists the watchlist through it.
- `bot/strategy/trend_join_long.py` — `TrendJoinLong.passes_daily_filters()` (D1/D2/D3) + `_check_universe_price`; the scanner *feeds* this, does not reimplement filters.
- `bot/strategy/indicators.py` — `sma()` (SMA200) and `rvol()` (14-day baseline) primitives; the scanner calls these.
- `bot/gateway/gateway.py` — `MoomooGateway` (async `run_in_executor` pattern, `connect`/`get_acc_list`/`get_positions`); **`subscribe()` is NOT yet implemented** — SIG-01 adds it for the capped list.

### Existing Codebase (reuse-by-reference)
- `skills/moomooapi/scripts/common.py` — `SubType` usage, `check_ret`, `safe_*` helpers patterns to mirror for the subscription wiring.
- `skills/moomooapi/docs/API_LIMITS.md` — subscription/quota limits motivating the top-20 cap (SIG-01).
- `CLAUDE.md` — project constraints (Python 3.6+, ET timezone, market-hours awareness, paper-only).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `TrendJoinLong.passes_daily_filters(daily_data, sma200)` already encodes D1/D2/D3 + price floor — Phase 2 builds the DataFrame per symbol and calls it; no filter logic reimplemented.
- `indicators.sma()` and `indicators.rvol()` are pure, config-parameterized primitives — directly callable for SMA200 and the 14-day RVOL baseline (SCAN-03).
- `StateStore` + `daily_scan` table already exist (Phase 1, D-07); the watchlist persists through StateStore. The richer-context decision (D-08) extends this table via a new ordered migration step.
- `StrategyConfig` loader (Phase 1, 01-03) supplies all filter params from `rules.json` — the scanner reads gap %, price floor, RVOL lookback from config, never literals (CFG-01 / Phase 1 D-12).
- `MoomooGateway` async pattern (`run_in_executor`) is the template for the new `subscribe()` method.

### Established Patterns
- Phase 1 D-08: schema changes are **ordered migration steps keyed by `PRAGMA user_version`** — the D-08 column additions are a new migration, not an edit to shipped `_MIGRATION_0001`.
- snake_case files, PascalCase classes, UPPER_SNAKE constants; pytest tree mirrors `bot/` (`tests/scanner/...`).
- ET / market-session correctness via Phase 1 `bot/safety/et_helpers.py` (SVC-04) — the NYSE calendar gate composes with these for half-day awareness.
- Config-drivenness proven behaviorally (Phase 1 D-12) — scanner tests should swap `rules.json` values (e.g., gap threshold) and assert the watchlist changes.

### Integration Points
- New scanner code is a `bot/scanner/` subpackage (sibling to gateway/state/strategy), consistent with Phase 1 D-01 layout.
- yfinance is the new read-only data dependency (add to `requirements.txt`); pandas-market-calendars added for the NYSE gate.
- `MoomooGateway.subscribe()` (new) is the only broker touch this phase — called ONLY for the capped top-20 (SIG-01), after persistence.
- RVOL look-ahead guard (PITFALLS #4): baseline uses only completed prior trading days; verifiable by replaying a known date (criterion #2).

</code_context>

<specifics>
## Specific Ideas

- The operator wants the universe sourced **free and without broker quota** — Wikipedia scrape over `get_plate_stock`, with a **cached fallback** so a single bad-network morning never kills the scan (resilience prioritized).
- On a degraded-data day the operator prefers a **fail-loud abort over a silently-thin watchlist** once degradation is material (≥10%), but tolerates minor flakiness below that — availability *and* correctness, with correctness winning past the threshold.
- The watchlist should be a **rich handoff artifact**, not just a ranked code list — Phase 3 should read prior-day high / SMA200 / RVOL baseline from StateStore rather than recompute, keeping Phase 2 and Phase 3 numerically consistent.
- Re-scans should **protect live candidates** — a candidate already feeding a 5m subscription (or a forming position) must not be churned out by a marginally-higher-gap newcomer.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. (Intraday signal evaluation, order/position management, the APScheduler service that fires the scan jobs, Telegram alert delivery, and the backtester remain mapped to Phases 3–6 per ROADMAP.md.)

</deferred>

---

*Phase: 02-premarket-scanner*
*Context gathered: 2026-06-23*
