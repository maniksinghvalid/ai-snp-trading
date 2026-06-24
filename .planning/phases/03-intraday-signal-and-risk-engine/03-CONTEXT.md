# Phase 3: Intraday Signal and Risk Engine - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Turn live closed 5m bars + the Phase-2 watchlist into **verified, correctly-sized `OrderIntent` events** — with **no order placement**. In scope:

- **Bar-close detection** — a `BarAggregator` (`CurKlineHandlerBase` subclass) that bridges the SDK push thread to asyncio and fires strategy evaluation **only on timestamp advance**, never mid-bar (SIG-02, no repainting).
- **Signal engine** — feed the already-built `TrendJoinLong.passes_intraday_filters()` (I1 above premarket high, I2 above HOD, I3 RVOL ≥ 2.0) gated by the 10:05–15:30 ET entry window, the 5-concurrent-position cap, and the daily new-entry cap (SIG-03, SIG-04, RISK-04, RISK-05); emit `SignalEvent`s.
- **Risk engine** — read **live** account equity from the gateway, size to 1% risk/trade, cap notional at 10% of portfolio, compute the LOD−1% initial stop, and emit verified `OrderIntent`s (RISK-01, RISK-02, RISK-03).

Requirements in scope: **SIG-02, SIG-03, SIG-04, RISK-01, RISK-02, RISK-03, RISK-04, RISK-05**.

Out of scope this phase: order placement / position FSM / fill matching / broker reconciliation drift logic (Phase 4 — Phase 3 emits intents only), the APScheduler service + OpenD watchdog + Telegram alerts (Phase 5), the backtester (Phase 6). Phase 3 **places no orders and sees no fills**.
</domain>

<decisions>
## Implementation Decisions

### Premarket-high & HOD source (I1 / I2 reference levels)
- **D-01:** **Premarket high** is captured via **one batched Moomoo `get_market_snapshot`** for the ≤20 watchlist codes **near 09:30 ET**, reading the pre-market high field, then **frozen for the session**. Cheap (one snapshot for the capped list), no extended-hours subscription, leaves `subscribe()`'s current `extended_time=False` / `Session.NONE` defaults untouched. (Premarket high is NOT in the Phase-2 watchlist — only `prior_day_high` is — so it must be sourced intraday.)
- **D-02:** **HOD** (I2) is tracked as a **running max of regular-session 5m bar highs** inside `BarAggregator`, updated as each bar closes. Self-contained, point-in-time by construction, no extra broker calls / quota.
- **D-03:** If a watchlist code has **no/thin premarket trading** (premarket high undefined, zero, or unavailable from the snapshot), the signal engine **emits no entry signal for that code that session** — never enters on a missing breakout reference. (Conservative: do NOT fall back to prior-day high.)

### Live-equity basis for sizing (RISK-01 / RISK-02)
- **D-04:** "Equity" for the 1%-risk math = **total net assets** (net liquidation value = cash + market value of open positions) — the standard account-equity definition; 1% scales with the whole account.
- **D-05:** **Always read live** — query the SIMULATE account's actual current equity on every sizing decision (RISK-01 "read live, not cached"). The $100k from the constraints is the *assumed starting balance*: the operator funds the paper account to ~$100k. **Fallback:** if the funds query fails or returns an implausible value, fall back to $100k rather than mis-size or crash.
- **D-06:** A **new gateway funds/equity query method is required** — the gateway currently exposes only `get_positions()` / `get_acc_list()`, no funds/`accinfo` query. RISK-01 depends on adding it (mirror the existing async `run_in_executor` pattern).
- **D-07:** **Sizing edges:** round share qty **DOWN** to whole shares (never over-risk). When 1%-risk sizing and the 10%-notional cap disagree, **take the smaller**. If the result is **<1 share** (stop too wide for the budget), **emit no `OrderIntent`** and log the reason.

### Daily-cap & re-signal policy (RISK-05 + over-trading guard)
- **D-08:** The **authoritative** daily-entry counter is incremented at **fill (Phase 4)**, persisted in StateStore — the cap measures *entries taken*, not setups seen.
- **D-09:** To still enforce the cap **inside a Phase-3 session** (where no fills exist yet), Phase 3 gates on **`(filled count) + (intents already emitted but not yet resolved this session)` < `max_trades_per_day`**. This prevents a burst of intents in one bar from blowing past the daily cap before any fill registers, while keeping the counter fill-authoritative.
- **D-10:** **Re-entry** is allowed **only when broker truth shows the code is flat** (not in `get_positions()`) **AND** it has no unresolved pending intent — so a stopped-out code may qualify again later the same session, still bounded by the daily cap.
- **D-11:** **Blocked-by-concurrent-cap or risk-rejected signals count toward neither** counter (not the daily cap, not the pending tally). Only **emitted `OrderIntent`s** consume an entry.

### OrderIntent handoff (Phase 3 → Phase 4 boundary)
- **D-12 (Claude's discretion — defaulted, not user-selected):** Each `OrderIntent` is **both logged to structlog** (satisfies RISK-03 criterion #5: the logged intent carries stop price + quantity) **and persisted to StateStore** as an auditable "pending intent" record. Persistence is needed anyway for the D-09 pending-intent gating and makes intents survive a restart. The exact transport for Phase 4 to *consume* these (in-process event/queue vs. StateStore poll) is left to research/planner. **Research note:** confirm the pending-intent record shape and lifecycle (emitted → resolved/filled/expired) so Phase 4 can reconcile D-08's fill-authoritative counter against it.

### Claude's Discretion
Left to research/planner at standard defaults:
- **BarAggregator internals** — `CurKlineHandlerBase` subclass mechanics, the SDK-thread→asyncio bridge (thread-safe queue / `call_soon_threadsafe`), and **bar-close detection during subscription reconnect mid-bar** (ROADMAP research flag — prefer conservative handling that never double-fires or replays a partial bar).
- **Snapshot field availability** for premarket-high/HOD at candidate-list scale (ROADMAP research flag) — confirm the exact `get_market_snapshot` field names and that the pre-market high is populated for the ≤20 codes near 09:30 ET.
- Exact `SignalEvent` / `OrderIntent` dataclass shapes and module decomposition across the three planned slices (03-01 BarAggregator, 03-02 SignalEngine, 03-03 RiskEngine).
- The entry-window time-gate boundary handling (10:05 / 15:30 ET inclusive/exclusive) — table-driven test across boundary times (ROADMAP success criterion #2); compose with `bot/safety/et_helpers.py`.
- Which gateway funds-query field maps to "total net assets" on the SIMULATE account (D-04/D-06) and the implausible-value threshold for the $100k fallback (D-05).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy & Config (source of truth)
- `.planning/PROJECT.md` §"The Strategy — Trend Join Long" — canonical `rules.json` content: `intraday_filters` (I1/I2/I3, `I3_rvol_min` 2.0, `I3_rvol_lookback_days` 14), `time_filter` (`earliest_entry_et` 10:05, `latest_entry_et` 15:30, `force_close_et` 15:51), `exit.initial_stop_rule` `lod_minus_1pct`, `risk` (`max_risk_per_trade_pct` 1.0, `max_position_size_pct_of_portfolio` 10, `max_concurrent_positions` 5, `max_trades_per_day` 5). No strategy constant hardcoded in Python.
- `rules.json` (repo root) — the loaded runtime config (CFG-01); the engine reads filter/time/risk params from it via the Phase-1 `StrategyConfig` loader, never literals.
- `.planning/REQUIREMENTS.md` §Signals + §Risk — SIG-02/03/04 and RISK-01..05 acceptance text (this phase's requirement IDs).
- `.planning/ROADMAP.md` §"Phase 3: Intraday Signal and Risk Engine" — goal, 5 success criteria, the 3 planned plan-slices (03-01/03-02/03-03), and the **two research flags** (reconnect bar-close; snapshot field availability).

### Architecture & Research
- `.planning/research/SUMMARY.md` — signal/risk-engine component boundary and build-order rationale.
- `.planning/research/PITFALLS.md` — repainting / mid-bar evaluation, non-atomic state writes, look-ahead — directly relevant to SIG-02 and the daily-cap persistence.
- `.planning/research/STACK.md` — moomoo SDK push-handler classes, pinned deps.

### Phase 1 & 2 (build directly on these)
- `bot/strategy/trend_join_long.py` — **already implements** `passes_intraday_filters(code, bars_5m, premarket_high, hod, rvol)` (I1/I2/I3, thresholds from cfg), `compute_initial_stop(lod)` (LOD × (1 − `initial_stop_pct`/100); decoupled from risk %, see CR-01 note), and `compute_swing_low_2_2()`. Phase 3 **feeds** these — does not reimplement filter/stop math.
- `bot/strategy/indicators.py` — `sma()`, `rvol()`, `swing_low_2_2()` primitives.
- `bot/strategy/core.py` / `bot/config/schema.py` / `bot/config/loader.py` — `StrategyConfig` fields (`rvol_min`, `initial_stop_pct`, entry-window times, `max_concurrent_positions`, `max_trades_per_day`, `max_risk_per_trade_pct`, `max_position_size_pct_of_portfolio`).
- `bot/gateway/gateway.py` — `MoomooGateway`: async `run_in_executor` pattern, `connect`/`close`, `get_positions()`, `get_acc_list()`, `subscribe([SubType.K_5M])`, `unsubscribe()`, `reconcile_once()` skeleton. **No funds/equity query yet** — D-06 adds it. `get_positions()` is the broker-truth source for the concurrent cap (RISK-04 / SIG-04) and D-10 re-entry check.
- `bot/state/store.py` + `bot/state/migrations.py` — `StateStore` (`conn`, `atomic_write_json`, context-manager) and the `PRAGMA user_version` ordered-migration mechanism; the daily-entry counter (D-08) and pending-intent record (D-12) persist here, almost certainly via a **new ordered migration step** (Phase 1 D-08 / Phase 2 D-08 pattern — never edit shipped migrations).
- `bot/scanner/scanner.py` — persists the watchlist with `prior_day_high`, `prior_close`, `sma200`, `rvol_baseline`, `scan_pass` per code; Phase 3 reads these from StateStore (Phase 2 D-08) rather than recomputing. Note: `premarket_high` and `hod` are **not** persisted — sourced intraday per D-01/D-02.
- `bot/safety/et_helpers.py` — ET / market-session correctness (SVC-04); the entry-window and force-close-time gates compose with these.
- `bot/safety/audit_log.py`, `bot/safety/logger.py` — structlog + audit patterns the OrderIntent logging (D-12, RISK-03 #5) mirrors.
- `.planning/phases/02-premarket-scanner/02-CONTEXT.md` — Phase 2 decisions Phase 3 depends on (D-04 protect live candidates, D-08 rich watchlist handoff).

### Existing Codebase (reuse-by-reference)
- `skills/moomooapi/scripts/subscribe/push_kline.py` — K-line push handler pattern (`CurKlineHandlerBase` usage) to mirror for `BarAggregator`.
- `skills/moomooapi/scripts/quote/get_snapshot.py` — `get_market_snapshot` field shapes for the premarket-high snapshot (D-01).
- `skills/moomooapi/scripts/trade/get_portfolio.py` / `get_accounts.py` — account funds/`accinfo_query` field names for the new equity query (D-06).
- `skills/moomooapi/docs/API_LIMITS.md` / `FIELD_MAPPING.md` — snapshot field meanings and quota limits.
- `CLAUDE.md` — project constraints (Python 3.6+, ET timezone, market-hours awareness, paper-only).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `TrendJoinLong.passes_intraday_filters()`, `compute_initial_stop()`, `compute_swing_low_2_2()` already exist and are pure + config-parameterized — Phase 3 orchestrates around them, no filter/stop logic reimplemented.
- `MoomooGateway` async `run_in_executor` pattern is the template for the new funds/equity query (D-06); `get_positions()` already returns broker-truth for the concurrent cap and re-entry check.
- `StateStore` + ordered-migration mechanism is the durable home for the daily counter (D-08) and pending-intent records (D-12).
- `StrategyConfig` loader supplies every threshold/time/risk param from `rules.json` — engine reads from config, never literals (CFG-01 / Phase 1 D-12).
- `et_helpers.py` supplies the ET-session correctness the entry-window gate needs.

### Established Patterns
- Schema changes are **new ordered migration steps keyed by `PRAGMA user_version`** (Phase 1 D-08, Phase 2 D-08) — never edit a shipped migration.
- Config-drivenness proven **behaviorally** (Phase 1 D-12) — Phase 3 tests should swap `rules.json` values (e.g., `rvol_min`, entry-window times, `max_trades_per_day`) and assert signal/sizing behavior changes.
- snake_case files, PascalCase classes, UPPER_SNAKE constants; pytest tree mirrors `bot/` (`tests/strategy/...`, `tests/gateway/...`, new `tests/signal/` or per planner).
- SDK imports deferred inside methods (gateway `subscribe`/`unsubscribe` pattern) so the test env without `moomoo-api` still imports.

### Integration Points
- `BarAggregator` is a new `CurKlineHandlerBase` subclass registered on the quote context's push handler — the only *new* broker-push touch this phase; bridges SDK thread → asyncio.
- The premarket-high snapshot (D-01) is a new one-shot `get_market_snapshot` call near 09:30 ET for the ≤20 codes.
- The new funds/equity gateway method (D-06) is the only new broker *query* this phase.
- All persistence (daily counter, pending intents) goes through `StateStore` via a new migration; no order placement, no broker writes.

</code_context>

<specifics>
## Specific Ideas

- The operator wants **correctness-first conservatism** on missing data: a code with no premarket high simply isn't traded that session (D-03), and an under-budget setup emits no intent (D-07) — never force a trade on an incomplete picture.
- Equity is the **live SIMULATE net-liquidation value**, not a cached or fixed number (D-04/D-05) — sizing tracks real account P&L, with $100k only as the funded starting point and a crash-safety fallback.
- The daily cap should measure **entries actually taken** (fills, Phase 4), but Phase 3 must still self-limit within a session so it can't over-emit before fills exist (D-08/D-09) — fill-authoritative *and* burst-safe.
- Re-entry is **opportunistic but bounded**: a stopped-out, now-flat code may re-qualify, but only within the daily cap and only when broker truth confirms it's flat (D-10).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. Order placement, the position lifecycle FSM, fill matching, and broker-reconciliation drift logic remain Phase 4; the scheduler/watchdog/Telegram remain Phase 5; the backtester remains Phase 6, per ROADMAP.md.

</deferred>

---

*Phase: 03-intraday-signal-and-risk-engine*
*Context gathered: 2026-06-24*
