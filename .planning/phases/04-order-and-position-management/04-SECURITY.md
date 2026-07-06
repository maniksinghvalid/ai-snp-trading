---
phase: 04
slug: order-and-position-management
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-24
---

# Phase 04 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Register authored at plan time (all 4 PLAN files carried a `<threat_model>` block).
> Verified by gsd-security-auditor against the implementation on 2026-06-24.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| config file → FSM | rules.json execution/exit values cross into the strategy brain; wrong/missing must fail validation | strategy tunables |
| persisted state → FSM | StateStore position rows reloaded on restart feed the FSM; a loosened stop on reload is injected risk | position/stop state |
| ExecutionEngine fills → PositionManager | FillEvents (broker truth about money/shares) cross into FSM state | fill qty/price/order_id |
| in-memory FSM → StateStore | every transition must land durably; a crash between memory and disk is a corruption window | position state |
| OrderIntent → broker | first write to the broker; wrong env or order type is a real (paper) money action | order placement |
| broker positions → in-memory FSM (restart) | broker truth must override stale persisted state; a wrong override re-enters or loosens risk | positions, orders |
| force-close clock → exit placement | EOD "flat by close" invariant; a missed/loose force-close carries overnight | exit orders |
| SIGINT/sentinel → state flush | shutdown must not drop in-flight FSM state | position state |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-04-01 | Tampering | trail_stop on restart/trail update | mitigate | `state.py:232-235` `new_stop = max(self.trail_stop, new_swing_low)` ratchet, raised only when `new_stop > trail_stop`; `test_trail_never_loosens` green | closed |
| T-04-02 | Tampering | rules.json execution/exit values | mitigate | `schema.py:29` `"execution"` in SCHEMA["required"], all 10 fields required; `loader.py:186-195` reads via `ex_cfg`; no R-threshold literals in `state.py` executable paths (CFG-01) | closed |
| T-04-03 | Repudiation | FSM phase transitions | accept (deferred) | `state.py` is pure (no asyncio/DB/broker); transition audit present in `manager.py:627-635` `_persist_position` → `append_audit` | closed |
| T-04-04 | Information Disclosure | migration 0004 columns | accept | `migrations.py:196-200` adds entry_order_id/exit_order_id/avg_fill_price only; no credential columns | closed |
| T-04-05 | Tampering | exit fill reconciliation | mitigate | `manager.py:453` `_find_position_by_exit_order_id` matches order_id string only; CLOSED gate at `manager.py:467` `remaining_quantity == 0` (EXEC-05) | closed |
| T-04-06 | Tampering | FSM state on crash mid-transition | mitigate | `store.py:249-278` `upsert_position` commits before return; `manager.py:624` `_persist_position` writes DB-first (Pitfall G); crash-sim test green | closed |
| T-04-07 | Tampering | trail_stop on bar update | mitigate | `state.py:232-235` `max()` ratchet — stop only raised, never lowered (D-11) | closed |
| T-04-08 | DoS | half-exited stop-out left unprotected | mitigate | `manager.py:467` CLOSED only when `remaining_quantity == 0`; partial fill leaves position open (Pitfall E) | closed |
| T-04-09 | Information Disclosure | audit log of transitions | mitigate | `manager.py:627-635` logs event/code/position_id/phase/trail_stop/remaining_quantity/order_ids only — no credentials (SAFE-05) | closed |
| T-04-10 | Elevation of Privilege | order placement env | mitigate | `gateway.py:530-531` `OrderType.NORMAL, trd_env=_parse_trd_env(self.cfg.trd_env)` (defaults SIMULATE, `gateway.py:174-176`); triple paper guard at `gateway.py:291` blocks REAL before any order reachable (EXEC-01/SAFE-01) | closed |
| T-04-11 | Tampering | order type | mitigate | `gateway.py:530` `OrderType.NORMAL` hardcoded; grep `OrderType.MARKET` → 0 hits in engine.py and gateway.py (EXEC-02) | closed |
| T-04-12 | DoS | runaway cancel-replace loop | mitigate | `engine.py:246` entry bounded by `range(entry_max_retries + 1)`; `engine.py:378` exit bounded by `while remaining > 0`; SDK rate limit far exceeds session order count (D-05) | closed |
| T-04-13 | Tampering | fill mis-reconciliation | mitigate | `engine.py:253-255` `str(f.get("order_id","")) == str(order_id)` — order_id-only matching (EXEC-05, Pitfall C) | closed |
| T-04-14 | Spoofing | stale fill data on SIMULATE | mitigate | `gateway.py:584,609` `get_order_fills` default `refresh_cache=True`; `gateway.py:647` hardcoded `refresh_cache=True` in `get_order_status` (Pitfall A/B) | closed |
| T-04-15 | Information Disclosure | order audit log | mitigate | All `engine.py` `append_audit` calls log event/code/order_id/price/qty/intent_id only — no credentials (SAFE-05) | closed |
| T-04-16 | Tampering | restart double-entry | mitigate | `engine.py:131` `get_positions(refresh_cache=True)` + `engine.py:161` `get_order_status()` run in `consume_intent` before `_manage_entry_order`; `gateway.py:884-904` reconciles PENDING intents (POS-05/D-09, Pitfall F) | closed |
| T-04-17 | Tampering | stop loosening on restart | mitigate | `gateway.py:782-821` `startup_reconcile` never modifies trail_stop; only `state.py:232` `max()` ratchet advances it (D-11) | closed |
| T-04-18 | DoS | orphan position left unmanaged | mitigate | `gateway.py:824-879` orphan adopted ACTIVE with `lod * 0.99` stop (`gateway.py:952`), 5m feed re-subscribed, covered by `force_close_all` (D-10) | closed |
| T-04-19 | DoS | position carried overnight / runaway force-close | mitigate | `manager.py:81` cutoff = `get_market_close_et(today)` − 9 min (no hardcoded time); `manager.py:339` `engine.manage_exit` NORMAL path; `manager.py:350-361` `force_close_stuck` audit; calendar-aware (D-08/POS-04); test green | closed |
| T-04-20 | Spoofing | stale broker state during reconciliation | mitigate | `gateway.py:761` and `engine.py:131` `get_positions(refresh_cache=True)` (Pitfall B) | closed |
| T-04-21 | Tampering | in-flight state lost on shutdown | accept (tracked) | `flush_all()` implemented `manager.py:246` (DB-first, tested green); `KillSwitch.register_flush()` exists `kill_switch.py:97`; wiring deferred to Phase 5 `main.py` — see Accepted Risks Log (R-04-01) | closed |
| T-04-SC | Tampering | npm/pip/cargo installs | mitigate | No Phase 4 dependency additions; `requirements.txt` last modified Phase 2 (commit 45d41e6); no install task exists | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| R-04-01 | T-04-21 | Kill-switch state flush is implemented (`PositionManager.flush_all`, DB-first, tested) but not yet registered with `KillSwitch`. Residual exposure is narrow: DB-first persistence already makes every *completed* FSM transition durable; the only unprotected window is a SIGINT arriving between `_persist_position` invocation and its commit. The wiring is documented Phase-5 scope (04-04 PLAN line 147, SUMMARY line 42), not an oversight. **Required Phase-5 action:** `main.py` MUST call `KillSwitch.register_flush(position_manager.flush_all)` before the trading loop starts. | operator | 2026-06-24 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-24 | 22 | 21 verified + 1 accepted | 0 | gsd-security-auditor (sonnet) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-24
