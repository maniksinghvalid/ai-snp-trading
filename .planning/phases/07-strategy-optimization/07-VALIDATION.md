---
phase: 07
slug: strategy-optimization
status: verified
nyquist_compliant: true
wave_0_complete: true
created: 2026-07-03
updated: 2026-07-06
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing — 514 tests green on develop) |
| **Config file** | existing pytest configuration at repo root |
| **Quick run command** | `python3 -m pytest tests/ -x -q --timeout=60 -k "<task-scope>"` |
| **Full suite command** | `python3 -m pytest tests/ -q` |
| **Estimated runtime** | ~60 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run the quick run command scoped to the touched module (each task's `<verify><automated>`).
- **After every plan wave:** Run the full suite command.
- **Before `/gsd-verify-work`:** Full suite must be green.
- **Max feedback latency:** 90 seconds.

---

## Wave 0 handling

No separate Wave 0 scaffold plan. Every code-producing task is `tdd="true"` with a `<behavior>` block and writes its failing regression tests first (RED) against the already-existing test files (all `tests/**` targets exist on develop). This keeps the Nyquist rule satisfied at task granularity — no task ships production code without an automated test authored in the same task.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-T1 | 07-01 | 1 | SIG-RVOL-TOD, RISK-TICK-STOP | T-07-01,02 | Idempotent migration to v5; tod_baselines + broker_stop_order_id | unit | `python3 -m pytest tests/state/test_migrations.py -x -q --timeout=60` | ✅ extend | ✅ green |
| 07-01-T2 | 07-01 | 1 | SIG-RVOL-TOD, RISK-CIRCUIT | T-07-02,03 | Lock-guarded, parameterized store methods; breaker date persists reopen | unit | `python3 -m pytest tests/state/test_store.py -x -q -k "tod_baseline or circuit_breaker"` | ✅ extend | ✅ green |
| 07-01-T3 | 07-01 | 1 | SIG-RVOL-TOD, RISK-CIRCUIT, RISK-TICK-STOP | T-07-01 | New config keys schema-validated; malformed fails closed (CFG-01) | unit | `python3 -m pytest tests/config/test_loader.py -x -q --timeout=60` | ✅ extend | ✅ green (stale assertion fixed 2026-07-06 — see audit trail) |
| 07-02-T1 | 07-02 | 2 | SIG-RVOL-TOD | T-07-07 | cum_volume per-code, reset each session (no carryover) | unit | `python3 -m pytest tests/signal/test_bar_aggregator.py -x -q -k "session_volume or cum_volume"` | ✅ extend | ✅ green |
| 07-02-T2 | 07-02 | 2 | SIG-RVOL-TOD | T-07-05 | 5m batch download via degradation gate | unit | `python3 -m pytest tests/scanner/test_fetcher.py -x -q -k "5m"` | ✅ extend | ✅ green |
| 07-02-T3 | 07-02 | 2 | SIG-RVOL-TOD | T-07-06 | ET-bucketed baseline; missing history degrades to legacy | unit | `python3 -m pytest tests/scanner/test_scanner.py -x -q -k "tod"` | ✅ extend | ✅ green |
| 07-03-T1 | 07-03 | 2 | RISK-TICK-STOP | T-07-09,10,12 | Stop-Market SELL via SIMULATE trd_env; armed only after fill; audited | unit | `python3 -m pytest tests/gateway/test_order_methods.py tests/position/test_manager.py tests/service/test_bot.py -x -q -k "stop_order or arm_stop or broker_stop"` | ✅ extend | ✅ green |
| 07-03-T2 | 07-03 | 2 | RISK-TICK-STOP | T-07-13 | Cancel-replace at current (never lower) trail_stop (D-04/D-11) | unit | `python3 -m pytest tests/position/test_manager.py -x -q -k "trail_sync or sync_broker_stop or cancel_replace"` | ✅ extend | ✅ green |
| 07-03-T3 | 07-03 | 2 | RISK-TICK-STOP | T-07-11 | Quote-tick fallback fires exit once on bid<=trail_stop | unit | `python3 -m pytest tests/gateway/test_order_methods.py tests/position/test_manager.py -x -q -k "quote or fallback"` | ✅ extend | ✅ green |
| 07-04-T1 | 07-04 | 2 | EXIT-MODEL | T-07-14,15 | exit.model enum; unimplemented variants fail closed | unit | `python3 -m pytest tests/config/test_loader.py -x -q -k "model"` | ✅ extend | ✅ green |
| 07-05-T1 | 07-05 | 3 | SIG-RVOL-TOD | T-07-19 | I3 uses TOD baseline (ET bucket); legacy fallback; gate is real | unit | `python3 -m pytest tests/signal/test_signal_engine.py -x -q -k "tod"` | ✅ extend | ✅ green |
| 07-05-T2 | 07-05 | 3 | RISK-CIRCUIT | T-07-16,17,18 | Realized-only -2R halt; persist+auto-reset; before broker call | unit | `python3 -m pytest tests/signal/test_signal_engine.py -x -q -k "circuit_breaker"` | ✅ extend | ✅ green |
| 07-05-T3 | 07-05 | 3 | RISK-CIRCUIT | T-07-16,20 | One-shot abandon+alert; restart-safe; alert failure isolated | unit | `python3 -m pytest tests/service/test_bot.py -x -q -k "breaker"` | ✅ extend | ✅ green |
| 07-06-T1 | 07-06 | 4 | EXIT-MODEL | T-07-21,22 | Selection blocked on Phase 6; no ship-by-default; no source change | manual | guard: `find . -name "*.py" \| grep -i backtest` returns empty | n/a | ✅ resolved — operator decided DEFER (07-06-SUMMARY.md), no source changed |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions | Resolution |
|----------|-------------|------------|-------------------|------------|
| SIMULATE account honors `OrderType.STOP` | RISK-TICK-STOP | Broker-side behavior on the paper account is empirically unverifiable in unit tests (RESEARCH Open Question 1) | On a live SIMULATE session with OpenD running, place one small broker Stop-Market SELL via `Gateway.place_stop_order` for a held test position; observe whether it appears in `order_list_query` and triggers at `aux_price`. If honored, keep `execution.use_broker_stop_orders=true`; if rejected/ignored, set it `false` (quote-tick fallback path). Record the result. (Handled as a `<human-check>` in plan 07-03; both paths ship regardless per D-02.) | **RESOLVED 2026-07-06** (07-UAT.md Test 1): broker returned explicit synchronous rejection "Paper trading does not support Stop order." `execution.use_broker_stop_orders` set to `false` in rules.json; quote-tick fallback (`_on_quote`) is the active stop path on this broker. |
| Exit-model selection requires Phase 6 backtest evidence | EXIT-MODEL | Success criterion 2 requires a backtest comparison; the Phase 6 backtester does not exist yet | Plan 07-06 blocking decision: verify no backtester code exists, present the three candidate models, decide defer (recommended) vs proceed-if-ready. Not auto-approvable. | **RESOLVED** (07-06-SUMMARY.md): operator decided DEFER; live model remains `partial_be_trail`; bounded Phase-6-gated follow-up documented. |

---

## Validation Audit 2026-07-06

| Metric | Count |
|--------|-------|
| Gaps found | 1 |
| Resolved | 1 |
| Escalated | 0 |

Full suite (`python3 -m pytest tests/ -q`) run: 1 failure found —
`tests/config/test_loader.py::TestPhase7ConfigKeys::test_load_real_rules_json_has_use_broker_stop_orders`
hardcoded the pre-UAT expectation (`True`) for the real `rules.json` value. This
went stale earlier in this session when `/gsd-verify-work 07` empirically
determined SIMULATE rejects Stop-Market orders and corrected
`execution.use_broker_stop_orders` to `false`. Root cause was already known
(no subagent needed); fixed the assertion + docstring in place. Re-ran full
suite: **587 passed, 1 skipped**. Also re-ran every per-task command in the
map above individually — all green, all target test files present.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or a checkpoint/manual justification (07-06 is a decision gate with a guard command + human-check)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (only 07-06-T1 lacks automated; it is the terminal checkpoint)
- [x] Wave 0 covers all MISSING references (task-level TDD authors each failing test in-task; all target test files exist)
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter
- [x] Both Manual-Only verifications resolved (broker stop-order support; exit-model defer decision)
- [x] Full suite green post-audit (587 passed, 1 skipped)

**Approval:** verified 2026-07-06
