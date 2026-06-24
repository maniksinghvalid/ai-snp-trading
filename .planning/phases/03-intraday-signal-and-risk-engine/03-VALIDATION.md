---
phase: 3
slug: intraday-signal-and-risk-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-24
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (existing, from Phase 1) |
| **Config file** | none — pytest auto-discovers |
| **Quick run command** | `pytest tests/signal/ tests/risk/ -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds (full suite; quick run < 5s) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/signal/ tests/risk/ -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-xx | 01 | 1 | SIG-02 | — | Bar-close fires only on `time_key` advance, never mid-bar | unit | `pytest tests/signal/test_bar_aggregator.py::test_no_signal_mid_bar -x` | ❌ W0 | ⬜ pending |
| 3-01-xx | 01 | 1 | SIG-02 | — | Session dedup prevents double-fire on reconnect re-push | unit | `pytest tests/signal/test_bar_aggregator.py::test_no_double_fire_on_reconnect -x` | ❌ W0 | ⬜ pending |
| 3-02-xx | 02 | 2 | SIG-03 | — | Signal emits only when all 3 filters + time gate pass | unit | `pytest tests/signal/test_signal_engine.py::test_all_gates_required -x` | ❌ W0 | ⬜ pending |
| 3-02-xx | 02 | 2 | SIG-03 | — | Entry-window boundary: 10:04:59 out, 10:05:00 in, 15:29:59 in, 15:30:00 out | unit | `pytest tests/signal/test_signal_engine.py::test_entry_window_boundaries -x` | ❌ W0 | ⬜ pending |
| 3-02-xx | 02 | 2 | SIG-04 / RISK-04 | — | No signal when concurrent positions ≥ `max_concurrent_positions` | unit | `pytest tests/signal/test_signal_engine.py::test_concurrent_cap -x` | ❌ W0 | ⬜ pending |
| 3-02-xx | 02 | 2 | SIG-03 (I1) / D-01 / D-03 | T-03-12 | Premarket highs frozen via one get_market_snapshot; codes with missing/zero `pre_high_price` excluded (never fall back to prior-day high) | unit (mock gateway) | `pytest tests/signal/test_signal_engine.py -k premarket_highs -x` | ❌ W0 | ⬜ pending |
| 3-02-xx | 02 | 2 | RISK-04 / SIG-04 (D-10) | T-03-11 | Re-entry blocked when code is broker-flat but has a live PENDING `pending_intents` row | unit | `pytest tests/signal/test_signal_engine.py::test_reentry_blocked_by_pending_intent -x` | ❌ W0 | ⬜ pending |
| 3-03-xx | 03 | 3 | RISK-01 | — | Sizing uses live equity, not cached | unit (mock gateway) | `pytest tests/risk/test_risk_engine.py::test_live_equity_called -x` | ❌ W0 | ⬜ pending |
| 3-03-xx | 03 | 3 | RISK-01 | — | $100k fallback when equity query fails or returns implausible value | unit | `pytest tests/risk/test_risk_engine.py::test_equity_fallback -x` | ❌ W0 | ⬜ pending |
| 3-03-xx | 03 | 3 | RISK-02 | — | Notional cap applied when smaller than 1%-risk qty; round shares DOWN | unit | `pytest tests/risk/test_risk_engine.py::test_notional_cap -x` | ❌ W0 | ⬜ pending |
| 3-03-xx | 03 | 3 | RISK-03 | — | OrderIntent carries correct stop_price and quantity | unit | `pytest tests/risk/test_risk_engine.py::test_intent_fields_correct -x` | ❌ W0 | ⬜ pending |
| 3-03-xx | 03 | 3 | RISK-05 | — | No OrderIntent when daily cap reached; closing a position does not reset cap | unit | `pytest tests/risk/test_risk_engine.py::test_daily_cap_independent_of_concurrent -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `bot/signal/__init__.py`, `bot/risk/__init__.py` — new package inits
- [ ] `tests/signal/__init__.py` — package init
- [ ] `tests/signal/test_bar_aggregator.py` — covers SIG-02 (timestamp advance, reconnect dedup, HOD/LOD tracking)
- [ ] `tests/signal/test_signal_engine.py` — covers SIG-03, SIG-04 (gate combinations, boundary times, concurrent cap), D-01/D-03 premarket-high fetch+freeze+exclude, and D-10 re-entry pending-intent block
- [ ] `tests/risk/__init__.py` — package init
- [ ] `tests/risk/test_risk_engine.py` — covers RISK-01..05 (sizing math, notional cap, <1-share guard, equity fallback, intent fields, daily cap)
- [ ] `tests/gateway/test_gateway.py` — ADD stubs for `get_equity` (RISK-01 + fallbacks) and `get_market_snapshot` (D-01 raw broker read) alongside existing gateway tests
- [ ] Migration 0003 test — verify `daily_trade_count` and `pending_intents` tables created idempotently

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `pre_high_price` populated for live ≤20 US codes near 09:30 ET without extended-hours subscription | SIG-03 (I1 reference) | Requires live OpenD session + market hours; cannot be unit-tested offline | During a live premarket window, run the premarket-high snapshot path and confirm a non-zero `pre_high_price` count is logged for actively-traded codes |
| `accinfo_query` `total_assets` maps to SIMULATE net-liquidation value | RISK-01 | Requires live SIMULATE account; offline tests mock the gateway | Query funds on the live SIMULATE account; confirm `total_assets` ≈ cash + open-position market value |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
