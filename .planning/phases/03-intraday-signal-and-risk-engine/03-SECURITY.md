---
phase: 3
slug: intraday-signal-and-risk-engine
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-24
---

# Phase 3 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

**Plans audited:** 03-01, 03-02, 03-03
**Block-on policy:** high (open threats block ship)
**Threats closed:** 13/13 · **Threats open:** 0/13
**Register origin:** authored at plan time (all 3 PLANs carried parseable `<threat_model>` blocks) — auditor verified mitigations exist; did not scan for net-new threats.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| moomoo SDK push thread → BarAggregator | Untrusted/possibly-malformed DataFrame rows cross from the SDK callback thread into application state | Raw kline push rows (OHLC, time_key) |
| SDK push thread → asyncio event loop | Cross-thread scheduling; a blocking or wrong-loop call stalls all subscribed-code processing | Coroutine scheduling handles |
| SignalEngine → broker (get_positions) | Concurrent-cap decision depends on broker-truth position count; a stale/empty read could under-count | Open position list |
| SignalEngine → StateStore (daily_trade_count / pending_intents) | Over-trading and re-entry guards read persisted counters; a burst of intents must not blow past the cap before any fill registers | Trade counters, pending intent rows |
| RiskEngine → broker (get_equity) | Sizing math is driven by the live equity read; a corrupt/implausible value could drive an outsized position | Account total assets |
| RiskEngine → StateStore (pending_intents write) | Each emitted intent is persisted; a non-atomic/partial write would desync the D-09 pending tally from the durable record | Pending intent INSERT |
| Phase 3 → Phase 4 (OrderIntent) | The intent is an instruction boundary — it must NOT itself reach any real-money order path (paper-only invariant) | OrderIntent dataclass |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation (evidence) | Status |
|-----------|----------|-----------|-------------|-----------------------|--------|
| T-03-01 | Tampering | `BarAggregator.on_recv_rsp` malformed/corrupt push row | mitigate | `bot/signal/bar_aggregator.py:162-167` — `try/except Exception` wraps `_handle_row(row)`; malformed rows fire nothing and never crash the SDK thread | closed |
| T-03-02 | Tampering | Bar-close dedup integrity across reconnect | mitigate | `bot/signal/bar_aggregator.py:120` (`__init__`), `134` (`reset_session` only), `251-258` — `_seen_time_keys` persists across reconnects; already-seen `time_key` skipped silently before firing | closed |
| T-03-03 | DoS | SDK push thread blocked inside `on_recv_rsp` | mitigate | `bot/signal/bar_aggregator.py:311-314` — `asyncio.run_coroutine_threadsafe(..., self._loop)` fire-and-forget; no blocking I/O inside callback | closed |
| T-03-04 | Tampering/Integrity | Daily-cap over-trading guard (burst in one bar, D-09) | mitigate | `bot/signal/signal_engine.py:87,118` (`_pending_count`, `note_intent_emitted`), `462` (`total_entries = filled_count + self._pending_count`); `bot/risk/risk_engine.py:192` increments at emission — burst bounded before any fill | closed |
| T-03-05 | Spoofing/Integrity | Concurrent-cap bypass via stale position read | mitigate | `bot/signal/signal_engine.py:404` — concurrent count from `await self._gateway.get_positions()` (broker truth), not an in-memory guard | closed |
| T-03-06 | EoP | Phase 3 corrupting fill-authoritative counter | mitigate | No `UPDATE`/`INSERT` on `daily_trade_count` in `bot/signal/` or `bot/risk/`; `signal_engine.py:265-271` is `SELECT` only | closed |
| T-03-07 | Tampering/Integrity | Live-equity read driving sizing (corrupt value, D-05) | mitigate | `bot/gateway/gateway.py:51-66` (named bound constants); `362` (both bounds applied); `372` (`max(total_assets, 1.0)` zero-divide guard); `374-380` (try/except → $100k fallback) | closed |
| T-03-08 | Tampering | Over-sizing via un-rounded/over-risked quantity | mitigate | `bot/risk/risk_engine.py:126,130` (`math.floor` on both qtys), `133` (`min(...)`), `135-144` (qty < 1 → no intent) | closed |
| T-03-09 | Repudiation/Integrity | Non-atomic pending_intents write desyncing tally | mitigate | `bot/risk/risk_engine.py:162-174` — parameterized INSERT (no f-string SQL) + `conn.commit()`; WAL mode at `bot/state/store.py:197` | closed |
| T-03-10 | EoP | Real-money order path under OrderIntent handoff | mitigate | `bot/risk/risk_engine.py` has no `place_order`/`PlaceOrder`/`trd_ctx`/`trade_ctx`; `gateway.py:345` uses `_parse_trd_env(self.cfg.trd_env)` defaulting to `TrdEnv.SIMULATE` | closed |
| T-03-11 | Integrity | Duplicate entry via re-signal on code with live pending intent (D-10) | mitigate | `bot/signal/signal_engine.py:286-290` (parameterized PENDING query); `442-456` requires BOTH broker-flat AND `has_pending_intent(code)` | closed |
| T-03-12 | Integrity | Trading on stale/absent premarket-high reference (D-01/D-03) | mitigate | `bot/signal/signal_engine.py:155-221` — single batched snapshot; `196` excludes `price <= 0.0` and `pd.isna(price)`; no prior-day fallback | closed |
| T-03-SC | Tampering | npm/pip/cargo installs | accept | `requirements.txt` holds only Phase 1/2 packages; all 3 SUMMARYs assert `tech_stack.added: []`; no install task in any 03-0x plan | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-03-01 | T-03-SC | Supply-chain tampering via new pip/npm/cargo installs. No new packages added in Phase 3; all dependencies (moomoo-api, pandas, structlog, yfinance, pandas-market-calendars, etc.) were vetted in Phase 1/2; no install task exists in any Phase 3 plan. | Phase operator | 2026-06-24 |

*Accepted risks do not resurface in future audit runs.*

---

## Unregistered Flags

None. No `## Threat Flags` section was present in any of the three SUMMARY files (03-01, 03-02, 03-03). Deviations documented were operational TDD bug-fixes, not new attack surface.

---

## Audit Notes

**T-03-04 (daily-cap burst guard) — increment wiring verified:** `_pending_count` is incremented by `note_intent_emitted()` called from `RiskEngine.on_signal()` (`risk_engine.py:192`) after a successful `OrderIntent` emission, not from `on_bar()` directly. This bounds the tally at intent emission (not signal emission) and prevents double-counting per intent (`signal_engine.py:489-493` documents the invariant).

**T-03-06 (fill-counter write isolation) — source assertion confirmed:** Grep across `bot/signal/` and `bot/risk/` produces zero matches for `UPDATE`/`INSERT` on `daily_trade_count`. `_get_filled_count()` (`signal_engine.py:255-271`) is read-only `SELECT`.

**T-03-10 (paper-only invariant) — source assertion confirmed:** `bot/risk/risk_engine.py` imports only `math`, `uuid`, `StrategyConfig`, `OrderIntent`, `now_et`, `get_logger`, `SignalEvent`, `StateStore`, `TrendJoinLong`. No broker execution symbol appears anywhere in the file.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-24 | 13 | 13 | 0 | gsd-security-auditor (sonnet) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-24
