# Phase 4: Order and Position Management - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Turn Phase 3's verified `OrderIntent`s (persisted in `pending_intents`, status `PENDING`) into **real broker orders on the SIMULATE (paper) account**, run the full per-position lifecycle FSM, and recover correctly from a mid-session restart. In scope:

- **ExecutionEngine** — translate `OrderIntent` → moomoo API orders; place entries, manage TTL + cancel-replace, reconcile fills **by `order_id`** (EXEC-05), emit `FillEvent`s. Adds the gateway order methods that don't exist yet (place/modify/cancel). (EXEC-01..05)
- **PositionState FSM** — `AWAITING_FILL → ACTIVE → PARTIAL_TAKEN (⅓ off at 0.75R) → BREAKEVEN (stop→entry at 1.0R) → TRAILING (5m swing-low) → CLOSED`; each transition unit-tested with synthetic Fill/Bar events. (POS-01/02/03)
- **PositionManager** — owns all `PositionState` objects, processes fills + closed-bar events, persists every transition to StateStore.
- **EOD force-close** — flatten all open positions at **15:51 ET**, calendar-aware for half-days. (POS-04)
- **Startup reconciliation + duplicate guard + kill-switch flush** — reconstruct positions from StateStore against broker truth, re-subscribe feeds, resume stop management without re-entering; broker-verified per-symbol duplicate-order guard. (EXEC-04, POS-05, SAFE-02/03/04)

Requirements in scope: **EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-05, POS-01, POS-02, POS-03, POS-04, POS-05**.

Out of scope this phase: the APScheduler service + OpenD watchdog + Telegram alerts + HTML dashboard (Phase 5 — Phase 4 may emit alert-worthy events/logs but does not own the scheduler or the Telegram transport); the backtester (Phase 6). Phase 4 is the **first phase that places real (paper) orders and sees fills**.

**ROADMAP research flag (must resolve in research before planning tasks):** paper-account order-flow behavior — push reliability, fill model, and **whether the SIMULATE account supports native stop orders** — must be validated empirically. (Note: the stop-model decision below makes the bot NOT depend on native stops, but research should still confirm fill/push behavior for limit orders and partial fills.)
</domain>

<decisions>
## Implementation Decisions

### Stop-loss execution model (the keystone)
- **D-01:** **Synthetic, bot-monitored stop** — the bot holds **no resting stop order** on the broker. On each closed 5m bar it checks the current stop and, when triggered, submits an aggressive marketable-limit exit (see D-07). Chosen over a native broker stop because (a) it matches how the 5m swing-low trail already works, (b) it gives full control over the partial/breakeven/trail sequence, and (c) it sidesteps the unverified paper-account native-stop support. Accepted trade-off: **no protection if the bot or OpenD is down between bars** — mitigated by the Phase-5 OpenD watchdog and the 15:51 force-close.
- **D-02:** **Stop triggers on bar CLOSE**, not the bar low/wick: exit only when a closed 5m bar has `close <= current_stop`. Deliberately avoids getting wicked out by an intrabar spike that recovers. Accepted trade-off: a bar that dips well below the stop but closes near it exits **late and below** the stop level (larger, more variable losses).
- **D-03:** **Profit-side triggers are also judged on bar CLOSE** — the 0.75R partial (POS-01) and the 1.0R breakeven (POS-02) fire only when a bar **closes** at/above the level. Symmetric with D-02: **everything in the FSM is judged on the close**, one coherent rule. Trade-off: a bar that spikes through 1.0R intrabar but closes below does not trigger yet.

### Entry pricing & chase policy (EXEC-03)
- **D-04:** **Marketable-limit entry (chase the breakout)** — price the entry limit at/through the current ask (ask + a small buffer) so it fills immediately; the limit cap still prevents a runaway market-order fill on the paper account. Chosen over a passive limit at the signal-bar close (which would miss real breakouts). Accepted trade-off: some slippage above the signal close.
- **D-05:** **TTL + bounded re-price retry** — if not (fully) filled within a short TTL (≈15–30s, **tunable via rules.json**), cancel and re-submit at the fresh marketable price, up to a small max number of attempts (≈2–3). After the cap, **abandon the entry** and release the slot + resolve the pending intent. Exact TTL/attempt-count/buffer numbers → rules.json + research.
- **D-06:** **Partial entry fill → keep the partial as the position.** If the entry only partially fills by the TTL/retry cap, cancel the unfilled remainder and manage the filled shares as a live position (`AWAITING_FILL → ACTIVE` on first fill). Under-fills simply **under-risk** vs the 1% target (conservative; never over-risk). The reconciled filled qty becomes `full_quantity` and the avg fill price becomes `entry_price` for all R math.

### Exit pricing & force-close (EXEC-02, POS-04)
- **D-07:** **Marketable-limit exits with retry-until-flat** — every exit (partial, breakeven, trail, stop-out) is an aggressive limit priced through the bid (bid − buffer, crosses the spread); if unfilled within its TTL, cancel-replace at a progressively **more aggressive** price until `remaining_quantity == 0`. Exits must complete — a half-exited stop-out is a risk hole. No reliance on paper market-order fills (EXEC-02). Fills reconciled by `order_id` (EXEC-05) so a ⅓ partial is never misread as a full stop-out.
- **D-08:** **15:51 force-close: escalate + alert, never silently carry, never a market order.** Begin the aggressive-retry exit at 15:51 ET (calendar-aware — earlier on half-days); escalate the price each retry through the 15:51→16:00 window. If a position is **still** not flat as the close nears, fire a loud alert/log (Phase-5 Telegram) and keep retrying to the bell. "Flat by close" is a core safety invariant — surface the failure rather than carry overnight. EXEC-02's no-market-order rule is upheld even here.

### Restart reconciliation policy (POS-05, SAFE-02/03, success criterion #6)
- **D-09:** **Broker truth wins, then reconcile** (consistent with the Phase-1 SAFE-02/03 principle that broker truth overrides in-memory state). On restart, for each code in `union(StateStore, get_positions())`:
  - StateStore shows a position the broker says is **flat** (closed during downtime) → mark CLOSED, record the trade, reconcile via `order_id` fills.
  - Quantities differ → **adopt the broker quantity**.
  - Both present → reconstruct the FSM around broker-confirmed holdings (see D-11).
- **D-10:** **Orphan broker position (no StateStore record) → adopt & protect.** Treat it as a recoverable bot position: re-subscribe its 5m feed, derive a conservative initial stop (LOD−1% rule) from current data, set it `ACTIVE` at the broker avg cost, and manage normally (including the 15:51 force-close). Alert that an orphan was adopted. Chosen over force-closing (which can dump a mid-trade winner) and over leave-untouched (which leaves unmanaged risk + breaks unattended operation). Accepted trade-off: the reconstructed stop may differ from the original.
- **D-11:** **Known position → restore persisted stage, stop only ever ratchets up.** Trust the persisted `phase` (PARTIAL_TAKEN / BREAKEVEN / TRAILING), `remaining_quantity`, and `trail_stop` exactly as saved; resume where it left off. Re-subscribe the 5m feed and rebuild swing-low history **forward**, but the stop is **never loosened** — `new_stop = max(persisted_stop, new_swing_low)`. The persisted `trail_stop` is the tightest known protection; never replace it with a looser recomputed value.

**Cross-phase invariant that emerged:** *judged-on-close (D-02/D-03) + stops that never loosen (D-11) + never silently leave risk unmanaged (D-08/D-10).*

### Claude's Discretion
Left to research/planner at standard defaults:
- **Exact FSM/dataclass shapes** — `PositionState`, `FillEvent`, and the state-enum values; module decomposition across the four planned slices (04-01 FSM, 04-02 PositionManager, 04-03 ExecutionEngine, 04-04 reconciliation/guard/force-close/kill-switch).
- **Gateway order methods** — `place_order` / `modify_order` / `cancel_order` wrappers (mirror the existing async `run_in_executor` pattern; deferred SDK imports per the `subscribe`/`unsubscribe` precedent) and which fields map order status/fills (`get_order_fill_list` / `get_orders`).
- **OrderIntent → order consumption transport** — how Phase 4 consumes `pending_intents` (in-process event/queue vs. StateStore poll) and the pending-intent lifecycle reconciliation (emitted → filled/abandoned/expired) against the fill-authoritative daily counter (Phase-3 D-08/D-09).
- **Schema migration** — the `positions` table lacks `order_id` / FSM-state-specific / avg-fill columns; almost certainly a **new ordered migration (0004)**, never editing a shipped migration (Phase-1/2/3 D-08 pattern).
- **Numeric tunables** — entry/exit limit buffers, entry TTL + max retries (D-05), exit escalation step + cadence (D-07), force-close escalation schedule (D-08) — all belong in `rules.json` (CFG-01), no hardcoded literals.
- **Empirical SIMULATE validation** (ROADMAP research flag) — limit-order fill model, push reliability, partial-fill behavior, and confirmation that native stops are unneeded given D-01.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy & Config (source of truth)
- `.planning/PROJECT.md` §"The Strategy — Trend Join Long" — canonical `rules.json` content: `exit` (`initial_stop_rule` `lod_minus_1pct`, `partial_profit_trigger_R` 0.75, `partial_profit_fraction` 0.3333, `breakeven_trigger_R` 1.0, `post_breakeven_trail` `swing_low_5m_2_2`), `time_filter.force_close_et` 15:51, `risk.max_concurrent_positions` 5. No strategy constant hardcoded in Python.
- `rules.json` (repo root) — the loaded runtime config (CFG-01); Phase 4's new tunables (limit buffers, TTLs, retry caps, escalation schedule) are added here, read via the Phase-1 `StrategyConfig` loader.
- `.planning/REQUIREMENTS.md` §Order Execution + §Position Lifecycle — EXEC-01..05 and POS-01..05 acceptance text (this phase's requirement IDs).
- `.planning/ROADMAP.md` §"Phase 4: Order and Position Management" — goal, 7 success criteria, the 4 planned plan-slices (04-01..04-04), and the **paper-order-flow research flag**.

### Architecture & Research
- `.planning/research/SUMMARY.md` — position/execution component boundary and build-order rationale (Phase 4 = highest-complexity phase).
- `.planning/research/PITFALLS.md` — order_id-vs-quantity fill matching (EXEC-05), non-atomic state writes, repainting / mid-bar evaluation (relevant to the close-based D-02/D-03 triggers and FSM persistence).
- `.planning/research/STACK.md` — moomoo SDK trade/order classes, `OrderType`/`TrdSide`/`ModifyOrderOp` enums, pinned deps.

### Phase 1–3 artifacts (build directly on these)
- `bot/risk/events.py` — **`OrderIntent`** dataclass (`code`, `entry_price`, `stop_price`, `quantity`, `equity_used`, `risk_dollars`, `notional`, `emitted_at`, `source_signal`, `intent_id`) — the Phase-3→4 handoff object the ExecutionEngine consumes.
- `bot/signal/events.py` — `BarEvent` / `SignalEvent` shapes; the FSM's `on_bar()` consumes closed-bar events.
- `bot/signal/bar_aggregator.py` — `BarAggregator` (`CurKlineHandlerBase`, time_key-advance bar-close detection, HOD/LOD running max/min, reconnect dedup). Phase-4 FSM stop/partial/breakeven/trail evaluation is driven by its closed-bar events (D-02/D-03) and its swing-low history (re-subscribed on restart per D-11).
- `bot/strategy/trend_join_long.py` — `compute_initial_stop(lod)` (LOD−1%; used for the orphan-adoption stop in D-10) and `compute_swing_low_2_2()` (the trail in POS-03 / D-11). Phase 4 **feeds** these, does not reimplement.
- `bot/gateway/gateway.py` — `MoomooGateway`: persistent `_quote_ctx` + `_trade_ctx`, async `run_in_executor` pattern, `connect`/`close`, `get_positions()` (broker truth for the duplicate guard EXEC-04, concurrent cap, and D-09/D-10 reconciliation), `get_equity()`, `subscribe([SubType.K_5M])`/`unsubscribe()`, `reconcile_once()` skeleton. **No order methods yet** — Phase 4 adds `place_order`/`modify_order`/`cancel_order` + fill/order queries.
- `bot/state/store.py` + `bot/state/migrations.py` — `StateStore`, `atomic_write_json`, and the `PRAGMA user_version` ordered-migration mechanism. Existing tables: `positions` (`position_id`, `code`, `phase`, `entry_price`, `initial_stop`, `trail_stop`, `full_quantity`, `remaining_quantity`, `opened_at`, `updated_at`), `trades` (closed-trade record incl. `exit_reason`, `r_multiple`), `pending_intents` (`intent_id`, `code`, `status` PENDING, `entry_price`, `stop_price`, `quantity`, `resolved_at`), `daily_trade_count` (`session_date`, `filled_count`). Phase 4 likely adds a **migration 0004** (order_id / avg-fill / FSM-state columns) — never edit a shipped migration.
- `bot/safety/et_helpers.py` — ET / market-session correctness (SVC-04); the 15:51 force-close gate (calendar-aware half-days, D-08) composes with these.
- `bot/scanner/calendar.py` — NYSE holiday/half-day schedule (half-day early close → earlier force-close per POS-04/D-08).
- `bot/safety/audit_log.py`, `bot/safety/logger.py` — structlog + JSONL audit patterns; every order/fill/transition mirrors these (success criterion #1 records FillEvents in StateStore + JSONL audit).
- `bot/safety/kill_switch.py` — file-touch + SIGINT kill switch (SAFE-04) that the 04-04 state-flush wiring extends.
- `.planning/phases/03-intraday-signal-and-risk-engine/03-CONTEXT.md` — Phase-3 decisions Phase 4 depends on: **D-08** (daily counter incremented at fill — Phase 4 writes it), **D-09** (burst-safe pending-intent gating), **D-10** (re-entry only when broker-flat + no pending intent), **D-12** (pending-intent record shape/lifecycle).

### Existing Codebase (reuse-by-reference, NOT imported — D-02 wrap-not-import)
- `skills/moomooapi/scripts/trade/place_order.py` — limit-order placement field shapes (`OrderType`, `TrdSide`, price/qty/code, SIMULATE env), audit-log pattern.
- `skills/moomooapi/scripts/trade/modify_order.py` — cancel-replace / modify semantics (`ModifyOrderOp`) for the TTL re-price (D-05) and stop modifications.
- `skills/moomooapi/scripts/trade/cancel_order.py` — order cancellation for TTL expiry / abandon (D-05) and force-close retries.
- `skills/moomooapi/scripts/trade/get_order_fill_list.py` + `get_orders.py` — **order_id-keyed fill reconciliation** (EXEC-05, D-07/D-09) — the canonical source for matching fills by `order_id`, never by quantity.
- `skills/moomooapi/docs/API_LIMITS.md` / `FIELD_MAPPING.md` — order/fill field meanings, order-status enums, quota limits.
- `CLAUDE.md` — project constraints (Python 3.6+, ET timezone, market-hours awareness, **paper-only / SIMULATE**, trade-unlock forbidden via SDK).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`OrderIntent`** (`bot/risk/events.py`) is the ready-made input contract — the ExecutionEngine consumes it directly; no re-deriving sizing/stop.
- **`MoomooGateway`** already owns persistent quote+trade contexts and the async `run_in_executor` template — the new `place_order`/`modify_order`/`cancel_order` methods mirror it exactly (deferred SDK imports per the `subscribe`/`unsubscribe` precedent so the test env without `moomoo-api` still imports).
- **`get_positions()`** is the broker-truth source already used in Phase 3 — reused verbatim for the EXEC-04 duplicate guard and the D-09/D-10 restart reconciliation.
- **`BarAggregator`** closed-bar events + running HOD/LOD + `compute_swing_low_2_2()` drive every FSM transition (D-02/D-03 close-based triggers, POS-03 trail).
- **`StateStore`** + ordered-migration mechanism is the durable home for the FSM (`positions`), closed trades (`trades`), and the fill-authoritative `daily_trade_count`.
- **`et_helpers.py` + `scanner/calendar.py`** supply the calendar-aware 15:51 force-close timing (D-08).
- **`compute_initial_stop(lod)`** provides the conservative orphan-adoption stop (D-10).

### Established Patterns
- Schema changes are **new ordered migration steps keyed by `PRAGMA user_version`** (Phase 1/2/3 D-08) — never edit a shipped migration. Phase 4 adds migration 0004 for order_id / avg-fill / FSM-state columns.
- **Config-drivenness proven behaviorally** (Phase 1 D-12) — Phase 4 tests should swap `rules.json` values (force-close time, R triggers, new TTL/buffer tunables) and assert order/FSM behavior changes; no strategy/execution literals hardcoded (CFG-01).
- **Atomic writes** (`atomic_write_json`, temp-file + `os.replace`) for every persisted FSM transition (POS-05 survives restart).
- snake_case files, PascalCase classes, UPPER_SNAKE constants; pytest tree mirrors `bot/` (new `tests/position/`, `tests/execution/` per planner).
- SDK imports deferred inside methods so the test env imports without `moomoo-api`.

### Integration Points
- The ExecutionEngine is the **first component that writes to the broker** (place/modify/cancel) — all prior phases only read. Every order + fill goes to StateStore + the JSONL audit log (success criterion #1).
- The PositionManager subscribes to `BarAggregator` closed-bar events (for stop/partial/breakeven/trail) and to `FillEvent`s from the ExecutionEngine (for AWAITING_FILL→ACTIVE, partial-take, stop-out).
- On fill, Phase 4 writes the fill-authoritative `daily_trade_count` (Phase-3 D-08) and resolves the matching `pending_intents` row (Phase-3 D-12).
- Restart reconciliation re-subscribes 5m feeds for reconstructed/adopted positions (D-09/D-10/D-11) — the only new restart-time broker interaction.
- Kill-switch (SAFE-04) flush extends to persist all in-flight FSM state on shutdown (04-04).

</code_context>

<specifics>
## Specific Ideas

- The operator wants a **synthetic stop they fully control** over a broker-native stop — the bot's 5m-bar logic is the single source of truth for exits, accepting downtime exposure as the explicit trade-off (covered by the Phase-5 watchdog + force-close) (D-01).
- **Don't get wicked out**: stops and profit milestones are judged on the **bar close**, not intrabar wicks — one symmetric rule across the whole FSM (D-02/D-03).
- **Chase the breakout** on entry (marketable limit) but bound the chase (TTL + limited retries, abandon if it runs) — get in, don't over-pay forever (D-04/D-05).
- **Exits must finish**: aggressive limits that escalate until flat; the 15:51 force-close never silently carries overnight and never falls back to a market order (D-07/D-08).
- **Never loosen a stop on restart** and **never leave a broker position unmanaged**: known positions resume with their ratcheted stop intact; orphan positions are adopted and protected, not ignored (D-10/D-11).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope. The APScheduler service, OpenD watchdog, Telegram alert transport, and HTML dashboard remain Phase 5; the backtester remains Phase 6, per ROADMAP.md. (Phase 4 emits alert-worthy events/logs — e.g. force-close-stuck, orphan-adopted — but Phase 5 owns delivery.)

</deferred>

---

*Phase: 04-order-and-position-management*
*Context gathered: 2026-06-24*
