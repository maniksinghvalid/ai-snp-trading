---
phase: 07
slug: strategy-optimization
status: verified
threats_open: 0
asvs_level: 1
created: 2026-07-06
---

# Phase 07 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| rules.json (operator-authored) → loader | Config values cross into runtime; malformed input must fail closed | Strategy parameters (numbers, booleans, enums) |
| SQLite DB file → StateStore | Persisted state (TOD baselines, breaker date) read back and drives trading gates | Baseline floats, ET date strings |
| yfinance (external market data) → scanner | Untrusted third-party OHLCV; partial/degraded data must not silently corrupt baselines | 5m OHLCV bars |
| K_5M broker push → BarAggregator | Volume field semantics drive the RVOL numerator | Bar volume ints |
| scanner → tod_baselines table | Computed baselines drive a live trading gate downstream | HH:MM → float mean volume |
| bot → Moomoo/OpenD order API | Every order (including the new stop) crosses into the broker; paper-only invariant must hold | Order requests (STOP type) |
| broker quote push → PositionManager | Untrusted tick data drives an immediate exit decision in the fallback path | Bid/ask ticks |
| PositionState.broker_stop_order_id → DB | Order-id state must survive restart to avoid orphaned/duplicate stops | Broker order_id string |
| rules.json exit.model → loader → live FSM | An exit-model value selects position exit behavior; unvalidated value must never silently drive live exits | Enum string |
| tod_baselines / meta (DB) → signal gates | Persisted data drives whether entries are allowed | Baseline floats, breaker date |
| trades table realized P&L → breaker | Determines session halt; must be realized-only | Realized R multiple |
| bot orchestrator → alerter/broker | Trip side effects (cancel, alert) must not crash the trade loop | Telegram alert payloads |
| Operator decision → shipped exit model | Selection controls live position exits; must be backed by backtest evidence | Human decision record |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-07-01 | Tampering | Malformed `daily_circuit_breaker_r`/`use_broker_stop_orders` in rules.json | mitigate | jsonschema `type` validation (schema.py); loader raises `ConfigError`; `bot/main.py` hard-exits | closed |
| T-07-02 | Tampering | SQL injection via scan_date/code/time_bucket into store methods | mitigate | All new `store.py` methods use `?` parameterized placeholders exclusively | closed |
| T-07-03 | Spoofing | Stale circuit-breaker date from a prior session read as current | mitigate | ET date-string comparison auto-resets (`clear_circuit_breaker`); persistence layer is write/read/delete only | closed |
| T-07-04 | Information Disclosure | `broker_stop_order_id` column leaks broker internals | accept | Local single-user SQLite on operator's machine; no external surface | accepted |
| T-07-05 | Tampering | Degraded/partial yfinance 5m batch skews TOD baseline | mitigate | `download_intraday_5m` reuses existing degradation gate; per-candidate try/except skips bad symbols, not stored | closed |
| T-07-06 | Spoofing | UTC vs ET bucket mismatch stores baselines under wrong clock time | mitigate | Index tz-converted to ET before `strftime("%H:%M")` bucketing; test asserts ET bucketing | closed |
| T-07-07 | Tampering | Cross-session volume carryover inflates cum_volume | mitigate | `reset_session()` clears `_session_volume`; test proves reset | closed |
| T-07-08 | Denial of Service | Per-candidate 5m downloads exhaust yfinance rate limits | accept | Bounded concurrency (threads=5) reused; runs once at premarket only | accepted |
| T-07-09 | Elevation | EXEC-02 amendment could leak a real-money or wrong-type order path | mitigate | `place_stop_order` reuses `self.cfg.trd_env` (SIMULATE); single `OrderType.STOP` path; no `TrdEnv.REAL` literal introduced | closed |
| T-07-10 | Tampering | Wrong `trd_side` on protective stop opens exposure instead of closing | mitigate | Long-only strategy: callers pass `TrdSide.SELL`; asserted in tests | closed |
| T-07-11 | Spoofing | Spoofed/erroneous quote tick triggers false exit in fallback path | mitigate | Fires only on `bid<=trail_stop` for a broker-verified open position; one-shot guard; bar-close FSM is corroborating backstop | closed |
| T-07-12 | Repudiation | Stop placement not auditable | mitigate | `place_stop_order` writes `append_audit(event="stop_order_placed")` (`gateway.py:804-805`) — confirmed by direct code read | closed |
| T-07-13 | Denial of Service | Cancel-replace no-stop window exploited by a flash move | accept | Bar-close stop check is redundant backstop; window is sub-second | accepted |
| T-07-14 | Tampering | `exit.model` set to unimplemented/unbacktested variant silently changes live exits | mitigate | Loader fails closed: only `partial_be_trail` loads; others raise `ConfigError` → `bot/main.py` hard-exits before trading. Confirmed directly: `bot/config/loader.py:218-224`, `_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)` | closed |
| T-07-15 | Tampering | Unknown/garbage `exit.model` string | mitigate | jsonschema enum rejects any value outside the three candidates. Confirmed directly: `bot/config/schema.py:119` `"enum": ["partial_be_trail", "fixed_2r", "full_to_1p5r_trail"]` | closed |
| T-07-16 | Spoofing | Stale breaker date halts trading, or a today trip fails to persist | mitigate | ET date-string comparison auto-resets stale entries; trip persisted via `set_circuit_breaker_date` before returning True; restart re-reads the flag | closed |
| T-07-17 | Tampering | Breaker trips on unrealized (mark-to-market) loss | mitigate | Realized-only source `get_daily_trade_stats` (closed trades only) — open positions never contribute | closed |
| T-07-18 | Denial of Service | Circuit-breaker check issues an SDK call every bar on a tripped day | mitigate | Gate placed before the `get_positions` concurrent-cap call; tripped path returns `None` with no broker round-trip | closed |
| T-07-19 | Spoofing | UTC/ET mismatch disables the TOD gate or mis-buckets | mitigate | `session_date_str = now_et().date().isoformat()`; ET-clock bucket; tests assert ET bucketing | closed |
| T-07-20 | Denial of Service | Alert send failure crashes the bar loop | mitigate | `alerter.send` wrapped in try/except; failure logged, never propagated | closed |
| T-07-21 | Tampering | An exit model shipped without backtest evidence drives live exits | mitigate | Blocking gate forbids auto-approval; loader fails closed for unimplemented variants; selection requires Phase 6 comparison. Operator recorded decision: DEFER, no source changed | closed |
| T-07-22 | Repudiation | The Phase-6 dependency is silently dropped and criterion 2 forgotten | mitigate | EXIT-MODEL remains an open requirement tracked by 07-06-SUMMARY.md + ROADMAP follow-up note | closed |
| T-07-SC | Tampering | Supply-chain: dependency installs (recurring across all 6 plans) | accept | No new packages installed in any plan this phase; reuses already-vetted moomoo-api/yfinance/pandas/sqlite3 | accepted |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-07-01 | T-07-04 | `broker_stop_order_id` is an internal SQLite column on the operator's own machine; no external/network surface exposes it (V4 not applicable) | Plan 07-01 | 2026-07-06 |
| AR-07-02 | T-07-08 | Bounded concurrency (threads=5) already caps yfinance load; TOD download runs once at premarket, not per intraday rescan | Plan 07-02 | 2026-07-06 |
| AR-07-03 | T-07-13 | Cancel-replace no-stop window is sub-second; bar-close FSM stop check is a redundant backstop (D-04) | Plan 07-03 | 2026-07-06 |
| AR-07-04 | T-07-SC | No new packages installed across any of the 6 plans this phase | Plans 07-01..06 | 2026-07-06 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-07-06 | 23 | 19 mitigated + 4 accepted | 0 | Claude (orchestrator, plan-time register verification — short-circuit path, `register_authored_at_plan_time: true`) |

Verification method: cross-referenced each `mitigate`-disposition threat against its plan's SUMMARY.md "Threat Flags" self-report and the existing `07-VERIFICATION.md` (13/13 truths independently verified with file+line evidence). Two threats (T-07-14, T-07-15) lacked a SUMMARY self-report because `07-04-SUMMARY.md` is missing from disk; these were independently re-confirmed by direct code inspection (`bot/config/loader.py:218-224`, `bot/config/schema.py:119`). T-07-12's audit-log mitigation was independently confirmed by direct code read (`bot/gateway/gateway.py:804-805`).

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-07-06
