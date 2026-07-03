# Phase 07: Strategy Optimization - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-03
**Phase:** 07-strategy-optimization
**Areas discussed:** Stop mechanism vs EXEC-02, Circuit breaker semantics

---

## Stop mechanism vs EXEC-02

### Q1 — Implementation mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Broker-side Stop-Market | Amend EXEC-02; guaranteed tick exit; survives bot/OpenD crashes; SIMULATE support needs research | ✓ |
| Broker-side Stop-Limit | Keeps EXEC-02's letter but can fail to fill in a fast drop — the toxic scenario itself | |
| Bot-side tick monitor + limit ladder | No EXEC-02 change, works on SIMULATE, but dies with the process | |

**User's choice:** Broker-side Stop-Market (recommended option)

### Q2 — SIMULATE fallback

| Option | Description | Selected |
|--------|-------------|----------|
| Bot-side monitor on paper | Tick monitor as paper fallback; broker-side path behind config flag; account selects | ✓ |
| Bar-close stays on paper | Only build broker-side for future real account; paper never validates tick exits | |
| Block until supported | Hard requirement; phase stalls if SIMULATE lacks support | |

**User's choice:** Bot-side monitor on paper (recommended option)

### Q3 — Coexistence with 5m bar-close FSM

| Option | Description | Selected |
|--------|-------------|----------|
| Split duties | Tick stop owns invalidation; FSM keeps partial/BE/trail; bar-close stop as backstop | ✓ |
| Tick owns everything | All exit logic to tick granularity — large FSM rewrite, hard to backtest | |
| Broker stop only as disaster hedge | Wider stop (LOD−2%) as crash hedge; bar-close remains primary — keeps fat-tail losses | |

**User's choice:** Split duties (recommended option)

### Q4 — Trail synchronization

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel-replace to follow | Broker stop mirrors trail_stop on every ratchet; finding-1.4 pattern; brief no-stop window accepted | ✓ |
| Stays at initial stop | Disaster insurance only; crash after breakeven exposes back to initial stop | |
| Update at breakeven only | One replace at 1R then frozen; trail gains unprotected on crash | |

**User's choice:** Cancel-replace to follow (recommended option)

---

## Circuit breaker semantics

### Q1 — Trigger accounting

| Option | Description | Selected |
|--------|-------------|----------|
| Realized only | Closed-trade P&L ≤ −$2,000 trips; simple, matches feedback verbatim | ✓ |
| Realized + open drawdown | Includes unrealized MTM; trips earlier in broad reversals but noisy | |
| Realized + stops-at-risk | Halts when possible day loss would exceed 2R; changes semantics to "could lose" | |

**User's choice:** Realized only (recommended option)

### Q2 — Halt scope

| Option | Description | Selected |
|--------|-------------|----------|
| New entries only | No new intents; existing positions fully managed | ✓ |
| Halt entries + tighten exits | Also ratchet open trails on trip | |
| Flatten everything | Liquidate all and go flat | |

**User's choice:** New entries only (recommended option)

### Q3 — Reset and override

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-reset next day, no override | No intraday re-arm; alert + log on trip; state persisted across restarts | ✓ |
| Next day + manual re-arm | Operator can re-enable intraday — reintroduces tilt risk | |
| You decide | Claude's discretion | |

**User's choice:** Auto-reset next day, no override (recommended option)

### Q4 — In-flight entries at trip time

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel in-flight entries | Cancel working entry orders, ABANDON intents; keep landed partial fills | ✓ |
| Let them complete | TTL cycle finishes; only future intents blocked | |
| You decide | Claude's discretion | |

**User's choice:** Cancel in-flight entries (recommended option)

---

## Claude's Discretion

- RVOL-TOD baseline construction: data source (yfinance 5m ~60d candidate), bucket granularity, caching, threshold retune
- Sequencing & backtest gating: which of the four items ship live immediately vs gated behind Phase 6 backtester evidence
- Alerting copy, log event names, config key naming

## Deferred Ideas

None — discussion stayed within phase scope.
