# Phase 4: Order and Position Management — Specification

**Created:** 2026-06-24
**Ambiguity score:** 0.12 (gate: ≤ 0.20)
**Requirements:** 12 locked

## Goal

The bot turns Phase 3's verified `OrderIntent`s into real orders on the SIMULATE (paper) account, drives each position through the full lifecycle FSM (AWAITING_FILL → ACTIVE → PARTIAL_TAKEN at 0.75R → BREAKEVEN at 1.0R → TRAILING on 5m swing-low → CLOSED), force-closes every open position at 15:51 ET (calendar-aware), and reconstructs all position state after a mid-session restart without re-entering — all judged on the 5m bar close.

## Background

Phases 1–3 are complete: the gateway, durable SQLite state, strategy core, scanner, bar aggregator, signal engine, and risk engine all exist and emit verified `OrderIntent`s persisted to the `pending_intents` table (status `PENDING`). Phase 4 is the **first phase that writes to the broker** (place/modify/cancel) and the first that sees fills.

Confirmed current state in code:
- **No `bot/position/` or `bot/execution/` package exists** — `PositionState`, `PositionManager`, and `ExecutionEngine` are not written.
- `MoomooGateway` (`bot/gateway/gateway.py`) has `get_positions()`, `get_equity()`, `subscribe()`, and a `reconcile_once()` skeleton, but **no `place_order` / `modify_order` / `cancel_order` / order-and-fill query methods**.
- `bot/state/migrations.py` stops at **migration 0003**; the `positions` table lacks `order_id`, average-fill, and FSM-state-specific columns.
- `OrderIntent` exists (`bot/risk/events.py`); **no `FillEvent` or `PositionState`** type exists.
- Implementation decisions D-01…D-11 are locked in `04-CONTEXT.md` (synthetic close-judged stop, marketable-limit chase entry, retry-until-flat exits, broker-truth-wins restart reconciliation, never-loosen stops, adopt-and-protect orphans).

## Requirements

1. **Paper-only order placement (EXEC-01)**: Entries are submitted as orders against the SIMULATE account only.
   - Current: Gateway has no order methods; no order has ever been placed
   - Target: `ExecutionEngine` consumes a `PENDING` `OrderIntent` and places a buy order via a new `MoomooGateway.place_order()`; the order is asserted to be on the SIMULATE (paper) account before submission
   - Acceptance: An integration test places one entry from an `OrderIntent` against the paper account and a corresponding record appears in both StateStore and the `~/.futu_trade_audit.jsonl` audit log; no code path reaches `place_order` when the paper guard is not satisfied

2. **Limit-order-only execution, no market orders (EXEC-02)**: Every entry and every exit is a (marketable) limit order — the bot never submits a market order.
   - Current: No order code exists
   - Target: Entries price a limit at/through the ask + buffer (D-04); exits price a limit through the bid − buffer (D-07); force-close uses the same escalating-limit mechanism (D-08), never a market order
   - Acceptance: A grep/AST or behavioral test confirms no order is placed with a market `OrderType`; force-close test confirms escalation stays on limit orders even at the 16:00 boundary

3. **TTL with bounded cancel-replace on entries (EXEC-03)**: A pending entry that is unfilled within its TTL is cancelled and re-submitted at a fresh marketable price, up to a bounded retry count, then abandoned.
   - Current: No TTL or cancel-replace logic exists
   - Target: After a configurable TTL (from `rules.json`), an unfilled entry is cancel-replaced at the fresh price up to a configurable max attempts; after the cap the entry is abandoned, the unfilled remainder cancelled, the concurrent slot released, and the `pending_intents` row resolved
   - Acceptance: A test simulating no-fill within TTL confirms the cancel-replace fires; a test exhausting the retry cap confirms abandonment, slot release, and `pending_intents` resolution; TTL and attempt-count are read from `rules.json` (config-swap test changes behavior)

4. **Partial entry fill kept as position (EXEC-03 / D-06)**: A partially-filled entry is managed as a live position sized to the filled shares; the unfilled remainder is cancelled.
   - Current: No fill handling exists
   - Target: On partial fill at the TTL/retry cap, cancel the remainder; the reconciled filled quantity becomes `full_quantity` and the average fill price becomes `entry_price` for all R math; the position transitions AWAITING_FILL → ACTIVE on first fill
   - Acceptance: A test with a partial fill confirms the position's `full_quantity` equals the filled shares (under-risk, never over-risk) and `entry_price` equals the average fill price; remainder is cancelled

5. **Broker-verified duplicate-order guard (EXEC-04)**: A second entry for a symbol that already has an open position is blocked by a broker `get_positions()` check, not just an in-memory guard.
   - Current: No duplicate guard exists in the order path (Phase-3 re-entry gate is signal-side only)
   - Target: Before placing any entry, `ExecutionEngine` calls `get_positions()`; if the symbol already has an open broker position, the entry is blocked and logged
   - Acceptance: A test that injects an existing broker position for a symbol confirms a new `OrderIntent` for that symbol is blocked even when the in-memory state is empty

6. **Fill reconciliation by order_id (EXEC-05)**: Stop-out and exit-fill detection matches broker fills by `order_id`, never by quantity.
   - Current: No fill reconciliation exists
   - Target: Every fill is matched to its originating order by `order_id`; a ⅓ partial exit is attributed to the partial-take order, never misread as a full stop-out
   - Acceptance: A test that takes a ⅓ partial then inspects the remaining position confirms the remaining stop is intact and the partial is not recorded as a full exit; reconciliation keys on `order_id` only

7. **Partial profit at 0.75R (POS-01)**: ⅓ of the position is sold when a 5m bar closes at/above 0.75R.
   - Current: No FSM or profit-taking logic exists
   - Target: On a closed bar with close ≥ entry + 0.75 × initial-risk, the FSM transitions ACTIVE → PARTIAL_TAKEN and submits a marketable-limit sell for `round(full_quantity × partial_profit_fraction)` shares (fraction from `rules.json`, default 0.3333)
   - Acceptance: A synthetic-bar test confirms the ⅓ partial fires only on a bar **close** at/above 0.75R (not on an intrabar high), the correct share count is sold, and the transition is persisted

8. **Breakeven at 1.0R (POS-02)**: The stop moves to entry when a 5m bar closes at/above 1.0R.
   - Current: No breakeven logic exists
   - Target: On a closed bar with close ≥ entry + 1.0 × initial-risk, the FSM transitions PARTIAL_TAKEN → BREAKEVEN and sets the working stop to the entry price
   - Acceptance: A synthetic-bar test confirms breakeven fires only on a bar **close** at/above 1.0R and the persisted working stop equals `entry_price`

9. **5m swing-low trailing stop, never loosened (POS-03)**: After breakeven, the stop trails on 5m swing lows (2/2 pattern) and only ever ratchets up.
   - Current: No trailing logic exists
   - Target: In TRAILING, on each closed bar the candidate stop is `compute_swing_low_2_2()`; the working stop becomes `max(current_stop, new_swing_low)` — never decreased
   - Acceptance: A synthetic-bar sequence confirms the stop rises with new swing lows and is never lowered when a later swing low is below the current stop

10. **Synthetic close-judged stop-out (POS-03 / D-01 / D-02)**: Stop exits are bot-monitored and triggered only on a bar close at/below the working stop; the exit completes (retry-until-flat).
    - Current: No stop monitoring or stop exit exists; no resting broker stop is used
    - Target: The bot holds no resting broker stop; on each closed 5m bar, if `close ≤ working_stop`, it submits an aggressive marketable-limit sell and cancel-replaces at progressively more aggressive prices until `remaining_quantity == 0`
    - Acceptance: A test where a bar closes below the stop confirms a marketable-limit exit is submitted and retried until flat; a test where a bar's low pierces the stop but the **close** is above it confirms **no** exit fires (no wick-out)

11. **EOD force-close at 15:51 ET, calendar-aware (POS-04 / D-08)**: All open positions are force-closed starting 15:51 ET (earlier on half-days), via escalating limit orders, never a market order, with a loud alert/log if still open near the bell.
    - Current: No force-close exists
    - Target: At the calendar-aware force-close time (15:51 ET on full days, earlier on half-days), every open position begins an escalating marketable-limit exit through the 15:51→16:00 window; if still open as the close nears, a force-close-stuck event is logged/emitted (Phase 5 delivers the alert) and retries continue to the bell
    - Acceptance: A simulated half-day test confirms force-close begins at the earlier close-derived time; a test confirms the exit escalates and stays on limit orders; a still-open-at-close test confirms a force-close-stuck event is emitted

12. **Restart reconstruction without re-entry (POS-05 / SAFE-02/03 / D-09/D-10/D-11)**: After a mid-session restart, the bot reconstructs every position from broker truth + StateStore, re-subscribes 5m feeds, and resumes stop management without re-entering any position.
    - Current: `reconcile_once()` is a skeleton; FSM state is not yet persisted or reconstructed
    - Target: On restart, for each code in `union(StateStore, get_positions())`: broker-flat-but-state-open → mark CLOSED + record trade; quantity mismatch → adopt broker quantity; known position → restore persisted phase/qty/`trail_stop` and resume (stop only ratchets up); orphan broker position with no StateStore record → adopt with a conservative LOD−1% stop, set ACTIVE at broker avg cost, manage + force-close; every reconstructed/adopted code re-subscribes its 5m feed; no position is re-entered
    - Acceptance: A simulated-restart test with positions in StateStore confirms all `PositionState` objects are reconstructed, 5m feeds re-subscribed, and no new entry order is placed; an orphan-position test confirms adoption with a derived stop; a known-position test confirms the persisted `trail_stop` is never loosened

## Boundaries

**In scope:**
- `ExecutionEngine` — `OrderIntent` → paper order translation; entry placement; TTL + bounded cancel-replace; `order_id`-keyed fill reconciliation; `FillEvent` emission
- New gateway order methods — `place_order` / `modify_order` / `cancel_order` and order/fill queries (mirroring the existing async `run_in_executor` + deferred-SDK-import pattern)
- `PositionState` FSM — five-stage lifecycle with all transitions judged on bar close, unit-tested with synthetic Fill/Bar events
- `PositionManager` — owns all `PositionState` objects, processes fills + closed-bar events, persists every transition (atomic writes)
- EOD force-close at 15:51 ET (calendar-aware half-days), escalating limit, never market order
- Startup reconciliation, broker-verified duplicate-order guard, kill-switch state flush (extends SAFE-04)
- Migration 0004 (new ordered migration) for `order_id` / average-fill / FSM-state columns
- New `rules.json` tunables (limit buffers, entry TTL + retry cap, exit escalation step/cadence, force-close escalation schedule)

**Out of scope:**
- APScheduler service / daily job scheduling — Phase 5 (Phase 4 emits alert-worthy events but does not own scheduling)
- OpenD connectivity watchdog — Phase 5 (the mitigation for D-01's downtime exposure lives there)
- Telegram alert transport — Phase 5 (Phase 4 emits events/logs; Phase 5 delivers them)
- HTML dashboard / daily P&L report — Phase 5
- Backtester / historical replay — Phase 6
- Native broker stop orders — explicitly rejected by D-01 (synthetic bot-monitored stop instead)
- Real-money / `TrdEnv.REAL` order path — out of scope for the entire milestone (paper-only)
- Short selling — strategy is long-only by definition

## Constraints

- **Paper-only (SAFE-01 / CLAUDE.md):** every order path is gated behind the existing hard paper guard; SIMULATE-only, no `TrdEnv.REAL` code path.
- **No market orders ever (EXEC-02):** all entries/exits/force-closes are limit orders, including the 15:51 escalation.
- **Fill matching by `order_id` only (EXEC-05):** never by quantity — quantity matching produces false stop-outs after a partial exit.
- **Config-driven (CFG-01):** all numeric tunables (limit buffers, entry TTL ≈15–30s, retry cap ≈2–3, exit escalation, force-close schedule, R triggers, partial fraction, force-close time) read from `rules.json`; no strategy/execution literals hardcoded — proven behaviorally via config-swap tests.
- **ET timezone + market-calendar (SVC-04):** force-close timing is ET and calendar-aware (half-day early close).
- **Schema migrations are append-only:** Phase 4 adds migration 0004 keyed by `PRAGMA user_version`; never edit a shipped migration.
- **Durable, atomic state (STATE-01 / POS-05):** every FSM transition is persisted via `atomic_write_json` (temp-file + `os.replace`) so state survives restart.
- **Build-on-not-rewrite (D-02 wrap-not-import):** the gateway wraps the moomoo SDK directly; `skills/moomooapi/...` is reference-only, never imported.
- **Empirical SIMULATE validation (ROADMAP research flag):** before task planning, paper-account limit-order fill model, push reliability, and partial-fill behavior must be validated empirically (research-phase); native-stop support need not be validated given D-01.

## Acceptance Criteria

- [ ] An `OrderIntent` is placed as a limit buy on the SIMULATE account; the fill is recorded in StateStore and `~/.futu_trade_audit.jsonl`
- [ ] No order — entry, exit, or force-close — is ever submitted as a market order
- [ ] The `PositionState` FSM transitions AWAITING_FILL → ACTIVE → PARTIAL_TAKEN → BREAKEVEN → TRAILING → CLOSED, each transition unit-tested with synthetic events
- [ ] The ⅓ partial (0.75R), breakeven (1.0R), and stop-out all fire only on a bar **close** crossing the level — never on an intrabar wick/high
- [ ] The trailing stop only ever ratchets up; a later lower swing low never loosens it
- [ ] An unfilled entry is cancel-replaced within its TTL and abandoned (slot released, `pending_intents` resolved) after the retry cap
- [ ] A partial entry fill is kept as a position sized to the filled shares (under-risk, never over-risk)
- [ ] A duplicate entry for a symbol with an existing broker position is blocked by a `get_positions()` check, not just in-memory
- [ ] A ⅓ partial exit is reconciled by `order_id` and not misread as a full stop-out
- [ ] All open positions are force-closed starting at the calendar-aware time (15:51 ET full day, earlier half-day); a still-open position near the bell emits a force-close-stuck event
- [ ] After a simulated restart, all positions are reconstructed (known restored without loosening the stop; orphan adopted with a derived stop), 5m feeds re-subscribed, and no position re-entered
- [ ] Every new numeric tunable is read from `rules.json` (config-swap test changes behavior); no execution/strategy literal is hardcoded

## Ambiguity Report

| Dimension          | Score | Min  | Status | Notes                                                        |
|--------------------|-------|------|--------|--------------------------------------------------------------|
| Goal Clarity       | 0.90  | 0.75 | ✓      | 7 ROADMAP success criteria; precise 5-stage lifecycle FSM    |
| Boundary Clarity   | 0.92  | 0.70 | ✓      | Explicit Phase 5/6 carve-outs; native-stop rejected (D-01)   |
| Constraint Clarity | 0.82  | 0.65 | ✓      | Paper-only, no-market, order_id matching, config-driven      |
| Acceptance Criteria| 0.85  | 0.70 | ✓      | 12 falsifiable pass/fail criteria + EXEC/POS acceptance text |
| **Ambiguity**      | 0.12  | ≤0.20| ✓      | discuss-phase pre-locked 11 decisions (D-01…D-11)            |

Status: ✓ = met minimum, ⚠ = below minimum (planner treats as assumption)

## Interview Log

This SPEC.md was written **after** a full discuss-phase had already locked the phase's HOW decisions (D-01…D-11 in `04-CONTEXT.md`, alternatives in `04-DISCUSSION-LOG.md`). The initial ambiguity assessment from ROADMAP + REQUIREMENTS + those artifacts already passed the gate (0.12), so no additional Socratic rounds were run — the user chose "Write SPEC.md now."

| Round | Perspective | Question summary                       | Decision locked                                              |
|-------|-------------|----------------------------------------|--------------------------------------------------------------|
| 0     | Researcher  | Current code state vs. phase goal      | No position/execution pkg, no order methods, migrations at 0003 — all of Phase 4 is greenfield on top of Phases 1–3 |
| pre   | (discuss)   | Stop-loss execution model              | Synthetic bot-monitored, close-judged (D-01/D-02/D-03)       |
| pre   | (discuss)   | Entry pricing & chase policy           | Marketable-limit chase + bounded TTL retry + keep partial (D-04/D-05/D-06) |
| pre   | (discuss)   | Exit pricing & force-close             | Retry-until-flat limits; 15:51 escalate + alert, never market (D-07/D-08) |
| pre   | (discuss)   | Restart reconciliation                 | Broker-truth-wins; adopt-and-protect orphans; never loosen stop (D-09/D-10/D-11) |

---

*Phase: 04-order-and-position-management*
*Spec created: 2026-06-24*
*Next step: /gsd-discuss-phase 4 — discuss-phase will detect this SPEC.md and treat these requirements as locked (CONTEXT.md already exists with the HOW decisions).*
