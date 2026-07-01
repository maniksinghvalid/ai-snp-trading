# Phase 3: Intraday Signal and Risk Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 03-intraday-signal-and-risk-engine
**Areas discussed:** Premarket-high & HOD source, Live-equity basis for sizing, Daily-cap & re-signal policy

Areas offered but not selected: OrderIntent handoff to Phase 4 (defaulted by Claude — see Claude's Discretion).

---

## Premarket-high & HOD source

### Premarket high source
| Option | Description | Selected |
|--------|-------------|----------|
| Snapshot once at the open | Batched Moomoo get_market_snapshot for the ≤20 watchlist near 09:30 ET, read pre-market high, freeze for session. Minimal quota, no extended-hours subscription. | ✓ |
| Derive from extended-hours 5m feed | Subscribe K_5M with extended_time=True, track 04:00–09:30 ET high in BarAggregator. Changes subscribe() defaults. | |

### HOD tracking
| Option | Description | Selected |
|--------|-------------|----------|
| Running max in BarAggregator | Track session high as running max of regular-session 5m bar highs. Self-contained, point-in-time, no extra broker calls. | ✓ |
| Moomoo snapshot per evaluation | Re-query snapshot high_price each bar-close. Broker-truth but quota + latency per candidate per bar. | |

### Missing premarket high
| Option | Description | Selected |
|--------|-------------|----------|
| Skip until defined | No premarket high → emit no entry for that code that session. Conservative. | ✓ |
| Fall back to prior-day high | Use watchlist prior_day_high as I1 reference. Keeps code tradeable, changes breakout level. | |
| You decide | Planner picks safest behavior. | |

**User's choice:** Snapshot-once + freeze; running-max HOD; skip on missing premarket high.
**Notes:** Premarket high and HOD are not in the Phase-2 watchlist (only prior_day_high is) → must be sourced intraday. Choices keep the design self-contained and correctness-first.

---

## Live-equity basis for sizing

### Equity figure
| Option | Description | Selected |
|--------|-------------|----------|
| Total net assets | Net liquidation value (cash + market value of positions). Standard equity definition. | ✓ |
| Available cash / funds | Uncommitted buying power; shrinks as positions fill; diverges from backtest. | |
| You decide | Planner picks reliably-populated field. | |

### Live vs $100k basis
| Option | Description | Selected |
|--------|-------------|----------|
| Always read live | Query SIMULATE current equity every sizing decision. $100k = funded starting balance. Honors RISK-01. | ✓ |
| Fixed $100k basis | Constant $100k; deterministic, matches backtester, but contradicts RISK-01. | |
| Live with $100k fallback | Read live; fall back to $100k only on query failure/garbage. | (folded in) |

### Sizing edges
| Option | Description | Selected |
|--------|-------------|----------|
| Round down; skip if <1 share or cap binds | Round qty down; smaller of 1%-risk vs 10%-notional cap; <1 share → no intent + log. | ✓ |
| Round down; allow 1-share floor | Same rounding but always ≥1 share even if over-risk. | |
| You decide | Planner specifies. | |

**User's choice:** Total net assets; always read live; round-down + skip on sub-1-share / cap conflict.
**Notes:** "Live with $100k fallback" was a separate option on the live-vs-$100k question; the operator chose "always read live," and the fallback-on-failure behavior was folded into CONTEXT D-05 as a crash-safety measure (read live, fall back to $100k only if the query fails). A new gateway funds/equity query is required (none exists today).

---

## Daily-cap & re-signal policy

### When an entry counts
| Option | Description | Selected |
|--------|-------------|----------|
| At OrderIntent emission | Increment persisted counter when intent emitted; Phase 4 reconciles at fill. | |
| Defer to fill (Phase 4) | Counter incremented at fill; Phase 3 reads to gate. | ✓ |

### Re-signal same day
| Option | Description | Selected |
|--------|-------------|----------|
| One entry per code per day | Code done for the day after its entry intent. Strongest over-trading guard. | |
| Re-entry if flat | Stopped-out/flat code may re-qualify, bounded by daily cap. | ✓ |
| You decide | Planner picks. | |

### Blocked / risk-rejected signals
| Option | Description | Selected |
|--------|-------------|----------|
| No — only emitted intents count | Blocked-by-cap / risk-rejected signals burn no daily entry. | ✓ |
| Yes — any triggered signal counts | Every filter-passing signal counts even if blocked downstream. | |

### Cap gating reconciliation (follow-up — resolving tension between "defer to fill" and "only emitted intents count")
| Option | Description | Selected |
|--------|-------------|----------|
| Filled + pending intents | Authoritative counter = fills (Phase 4); Phase 3 gates on filled + already-emitted unresolved intents < max_trades_per_day. Re-entry only when broker shows flat + no pending intent. Honors all three prior picks. | ✓ |
| Broker-position count only | Gate purely on open positions; risk of intent burst exceeding daily cap before any fill. | |
| You decide | Planner specifies. | |

**User's choice:** Fill-authoritative counter; Phase 3 gates on filled + pending intents; re-entry only when broker-flat and no pending intent; only emitted intents count.
**Notes:** Claude surfaced a tension — "defer to fill" alone leaves Phase 3 (which sees no fills) unable to trip the cap within a session, risking unbounded intent emission. The follow-up reconciliation (filled + emitted-but-unresolved pending intents) resolves it while keeping the counter fill-authoritative. Captured as CONTEXT D-08..D-11.

---

## Claude's Discretion

- **OrderIntent handoff (offered, not selected by user):** Defaulted to **log-to-structlog AND persist-to-StateStore** as an auditable "pending intent" record (CONTEXT D-12). Persistence is required anyway for the D-09 pending-intent gating and survives restart. Exact Phase-4 consumption transport (event/queue vs StateStore poll) and the pending-intent record shape/lifecycle left to research/planner.
- **BarAggregator internals** — CurKlineHandlerBase subclass, SDK-thread→asyncio bridge, and bar-close detection on reconnect mid-bar (ROADMAP research flag).
- **Snapshot field availability** for premarket-high/HOD at candidate-list scale (ROADMAP research flag).
- **SignalEvent / OrderIntent dataclass shapes**, module decomposition across 03-01/03-02/03-03.
- **Entry-window time-gate boundary handling** (10:05 / 15:30 ET inclusive/exclusive), table-driven.
- **Gateway funds-query field mapping** to "total net assets" and the implausible-value threshold for the $100k fallback.

## Deferred Ideas

None — discussion stayed within phase scope. Order placement / position FSM / fill matching / reconciliation drift → Phase 4; scheduler / watchdog / Telegram → Phase 5; backtester → Phase 6.
