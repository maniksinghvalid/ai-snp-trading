# Phase 4: Order and Position Management - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 4-order-and-position-management
**Areas discussed:** Stop-loss execution model, Entry pricing & chase policy, Exit pricing & force-close, Restart reconciliation policy

---

## Stop-loss execution model

| Option | Description | Selected |
|--------|-------------|----------|
| Synthetic (bot-monitored) | No resting broker stop; on each closed 5m bar, submit aggressive marketable-limit exit when stop breached. Full control, matches 5m trail, sidesteps unverified paper-stop support; no protection during downtime. | ✓ |
| Native broker stop | Resting STOP/STOP-LIMIT triggered by the broker; modify on breakeven/trail. Protects during downtime; depends on unverified paper stop support + intrabar trigger semantics. | |
| Hybrid | Synthetic primary + wide native disaster stop backstop. Best protection, most complex (double-exit risk, two stops to reconcile). | |

**User's choice:** Synthetic (bot-monitored)
**Notes:** Keystone decision. Accepts downtime exposure as the explicit trade-off, mitigated by Phase-5 OpenD watchdog + 15:51 force-close.

### Stop trigger condition

| Option | Description | Selected |
|--------|-------------|----------|
| Bar LOW breaches stop | Exit if closed bar low <= stop (price traded through). Most protective; wicks stop you out. | |
| Bar CLOSE breaches stop | Exit only if bar closes at/below stop. Avoids wick shake-outs; can exit late and below the level. | ✓ |

**User's choice:** Bar CLOSE breaches stop
**Notes:** Deliberately favors not getting wicked out, accepting larger/variable losses on a dip-and-recover bar.

### Profit-side (R) trigger condition

| Option | Description | Selected |
|--------|-------------|----------|
| Bar CLOSE reaches R level | 0.75R partial / 1.0R breakeven fire only on a close at/above the level. Symmetric with the close-based stop. | ✓ |
| Bar HIGH reaches R level | Lock in partial/breakeven as soon as price trades through intrabar. Earlier capture; asymmetric with stop. | |

**User's choice:** Bar CLOSE reaches R level
**Notes:** Produces one coherent rule — every FSM transition judged on the close.

---

## Entry pricing & chase policy

| Option | Description | Selected |
|--------|-------------|----------|
| Marketable limit (chase) | Limit at/through current ask + buffer; fills now, capped against runaways. Accepts slippage above signal close. | ✓ |
| Limit at signal close | Passive limit at signal-bar close; best price but high miss rate on real breakouts. | |
| Limit capped at max slippage | Marketable but skip if required price > X% above signal close. Tighter control, lower fill rate. | |

**User's choice:** Marketable limit (chase)
**Notes:** Prioritizes getting into the momentum breakout.

### TTL / cancel-replace policy

| Option | Description | Selected |
|--------|-------------|----------|
| Re-price & retry, bounded | Short TTL, cancel-replace at fresh price up to ~2–3 attempts, then abandon + release intent/slot. | ✓ |
| Re-price within slippage budget | Same retry but abandon immediately if fresh price exceeds a slippage ceiling. | |
| Single shot, no retry | One marketable limit, one TTL, then abandon. Simplest/safest; misses fills. | |

**User's choice:** Re-price & retry, bounded
**Notes:** Exact TTL (~15–30s) / attempt-count (~2–3) / buffer → rules.json + research.

### Partial entry fill

| Option | Description | Selected |
|--------|-------------|----------|
| Keep partial as the position | Cancel remainder, manage filled shares (under-risk OK; never over-risk). Filled qty/avg price drive R math. | ✓ |
| Flatten the partial | Sell filled shares back out, end flat. Avoids odd size; pays spread twice for nothing. | |

**User's choice:** Keep partial as the position
**Notes:** Conservative — under-fills simply under-risk vs the 1% target.

---

## Exit pricing & force-close

| Option | Description | Selected |
|--------|-------------|----------|
| Marketable + retry to fill | Aggressive limit through bid; cancel-replace progressively more aggressive until flat. order_id reconciliation. | ✓ |
| Marketable, single attempt | One aggressive limit per exit, no retry; risky for stop-outs that don't fill. | |
| Match entry buffer only | Same fixed buffer both sides, no escalation. Simplest; least adaptive. | |

**User's choice:** Marketable + retry to fill
**Notes:** Exits must complete — a half-exited stop-out is a risk hole. EXEC-05 order_id matching so ⅓ partial ≠ full stop-out.

### Force-close (15:51 ET) fallback

| Option | Description | Selected |
|--------|-------------|----------|
| Escalate, alert if stuck | Escalate price each retry through 15:51–16:00; if still open near close, loud alert + keep trying to the bell. Never silent, never market order. | ✓ |
| Escalate to market order | Drop the no-market rule for force-close only past a cutoff. Contradicts EXEC-02. | |
| Start force-close earlier | Move trigger earlier than 15:51 for more runway. Changes a spec'd time. | |

**User's choice:** Escalate, alert if stuck
**Notes:** "Flat by close" stays a core safety invariant; surface the failure rather than carry overnight.

---

## Restart reconciliation policy

| Option | Description | Selected |
|--------|-------------|----------|
| Broker truth wins, reconcile | Broker authoritative; state-only positions closed + trade recorded; qty diff adopts broker qty; FSM rebuilt around broker truth. | ✓ |
| StateStore wins | Trust persisted FSM, broker fills gaps. Contradicts SAFE-02/03. | |
| Alert and halt on any conflict | Freeze + manual intervention on any diff. Safe but breaks unattended core value. | |

**User's choice:** Broker truth wins, reconcile
**Notes:** Consistent with the Phase-1 SAFE-02/03 broker-truth-overrides principle.

### Orphan broker position (no StateStore record)

| Option | Description | Selected |
|--------|-------------|----------|
| Adopt & protect | Reconstruct conservative FSM (re-subscribe, LOD−1% stop, ACTIVE), manage + force-close, alert. | ✓ |
| Force-close orphan | Flatten immediately + alert. Safest but can dump a mid-trade winner. | |
| Leave it untouched, alert | Don't manage/close, alert operator. Leaves unmanaged risk; breaks unattended. | |

**User's choice:** Adopt & protect
**Notes:** Keeps the account managed and unattended; reconstructed stop may differ from original.

### Known-position FSM rebuild

| Option | Description | Selected |
|--------|-------------|----------|
| Restore persisted, never loosen | Trust persisted phase/qty/trail_stop; resume; stop only ratchets up (max of persisted and new swing low). | ✓ |
| Conservative re-derive | Re-establish stop from scratch (LOD−1%), ignoring persisted trail_stop. Can loosen a trailed stop. | |

**User's choice:** Restore persisted, never loosen
**Notes:** Persisted trail_stop is the tightest known protection; never replace with a looser recomputed value.

---

## Claude's Discretion

Deferred to research/planner at standard defaults: exact FSM/dataclass shapes + module decomposition (04-01..04-04); gateway `place_order`/`modify_order`/`cancel_order` wrappers + fill/order queries; OrderIntent→order consumption transport + pending-intent lifecycle reconciliation; migration 0004 (order_id / avg-fill / FSM-state columns); all numeric tunables in rules.json (limit buffers, entry TTL/retries, exit escalation, force-close schedule); empirical SIMULATE order-flow validation (ROADMAP research flag).

## Deferred Ideas

None — discussion stayed within phase scope. Scheduler/watchdog/Telegram/dashboard remain Phase 5; backtester remains Phase 6.
