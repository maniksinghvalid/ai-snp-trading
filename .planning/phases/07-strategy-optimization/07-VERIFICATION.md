---
phase: 07-strategy-optimization
verified: 2026-07-04T00:30:00Z
status: human_needed
score: 13/13 must-haves verified
overrides_applied: 0
re_verification: false
human_verification:
  - test: "On a live SIMULATE session with OpenD running, place one broker Stop-Market SELL via Gateway.place_stop_order for a held test position and confirm whether it appears in order_list_query and triggers at aux_price."
    expected: "If SIMULATE honors Stop-Market orders, confirm execution.use_broker_stop_orders=true is correct; if rejected/ignored, set to false so the quote-tick fallback path (D-02 _on_quote) is used instead."
    why_human: "Empirical SIMULATE stop-order support cannot be determined by static code inspection (RESEARCH Finding C-3, Open Question 1). Both code paths ship regardless; only the default value is at stake. Declared as human-check in plan 07-03."
---

# Phase 7: Strategy Optimization Verification Report

**Phase Goal:** Ship 4 strategy-optimization features — RVOL-TOD gate, tick-level stop orders, daily circuit breaker, and exit-model config surface — as risk reducers on top of the live Trend Join Long bot, with the exit-model selection explicitly gated on the Phase 6 backtester.
**Verified:** 2026-07-04T00:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Step 0: Previous Verification

No previous VERIFICATION.md found. Initial mode.

---

## Goal Achievement

### Observable Truths

| # | Truth | Plan | Status | Evidence |
|---|-------|------|--------|----------|
| 1 | Migration 0005 creates `tod_baselines` table (composite PK scan_date/code/time_bucket) and adds nullable `broker_stop_order_id` TEXT column to positions — idempotently | 07-01 | VERIFIED | `bot/state/migrations.py` lines 221-284: `_migration_0005` present, `CURRENT_VERSION = 5`, `CREATE TABLE IF NOT EXISTS tod_baselines`, PRAGMA table_info guard on ALTER |
| 2 | `StateStore.get_tod_baseline` returns stored mean or 0.0 when absent; `upsert_tod_baselines` writes a batch of HH:MM→float in one commit | 07-01 | VERIFIED | `bot/state/store.py` lines 801, 819: both methods present, lock-guarded, parameterized SQL |
| 3 | `StateStore` get/set/clear_circuit_breaker_date persist the ET trip date in the meta table and survive a reopen | 07-01 | VERIFIED | `bot/state/store.py` lines 842, 857, 875: all three methods present |
| 4 | `rules.json` carries `risk.daily_circuit_breaker_r=2.0`, `execution.use_broker_stop_orders=true`, `intraday_filters.I3_rvol_tod_lookback_days=14`; schema validates each; `StrategyConfig` exposes them | 07-01 | VERIFIED | `rules.json` lines 22, 32(model), 46, 60; `schema.py` lines 84, 119, 147, 180; `loader.py` lines 151, 155, 159, 165 |
| 5 | A malformed value for any new key fails jsonschema validation with ConfigError; no strategy literal hardcoded in Python | 07-01 | VERIFIED | `loader.py` lines 223-243: `_IMPLEMENTED_EXIT_MODELS`, model guard, and `.get()` defaults; type-check tests in suite (587 pass) |
| 6 | `BarEvent.cum_volume` field exists (default 0); `BarAggregator` accumulates per-code session volume on closed bars and resets on `reset_session()` | 07-02 | VERIFIED | `events.py` line 56: `cum_volume: int = 0`; `bar_aggregator.py` lines 125, 140, 294, 307: accumulator init, clear, update, emit |
| 7 | `fetcher.download_intraday_5m` downloads ~30 calendar days of 5m regular-session bars via the existing batch/degradation contract | 07-02 | VERIFIED | `bot/scanner/fetcher.py` line 546: `def download_intraday_5m` present |
| 8 | At premarket scan, per candidate, a 14-session average cumulative-volume curve is computed and written to `tod_baselines` via `store.upsert_tod_baselines`; `bot/service/bot.py` wires `cum_volume` from bar_data into BarEvent | 07-02 | VERIFIED | `scanner.py` lines 49, 569: `_compute_tod_baselines` def + `upsert_tod_baselines` call site; `bot.py` line 263: `cum_volume=bar_data.get("cum_volume", 0)` |
| 9 | `Gateway.place_stop_order` places a broker-side Stop-Market SELL (`OrderType.STOP`, `price=0.0`, `aux_price=stop_price`) and returns the broker `order_id` | 07-03 | VERIFIED | `gateway.py` line 764: `async def place_stop_order` present |
| 10 | On entry fill, `arm_stop_protection` arms a broker stop or quote-tick fallback (per `use_broker_stop_orders`); called post-fill in `bot._process_bar`; trail ratchet cancel-replaces via `_sync_broker_stop` | 07-03 | VERIFIED | `manager.py` lines 246, 317, 364: `arm_stop_protection`, `_sync_broker_stop`, `_on_quote` present; `bot.py` line 312: post-fill call; `gateway.py` lines 592-626: `subscribe_quote` + `QuoteTickHandler` |
| 11 | The I3 RVOL gate compares `event.cum_volume` against the TOD baseline for the bar's ET HH:MM bucket when a baseline exists; falls back to legacy when absent | 07-05 | VERIFIED | `signal_engine.py` lines 516-517: `time_bucket = event.time_key[11:16]`; `tod_baseline = self._store.get_tod_baseline(session_date_str, code, time_bucket)` inside Gate 2 |
| 12 | The circuit breaker gate (`_is_circuit_breaker_tripped`) blocks all new entries once realized P&L <= -2R; persists to meta; auto-resets prior dates; placed before the broker concurrent-cap gate | 07-05 | VERIFIED | `signal_engine.py` lines 402, 425-444, 576: `_is_circuit_breaker_tripped` def, clear/set/return logic, call site between Gate 3 and Gate 4 |
| 13 | On first trip: PENDING intents abandoned + one Telegram alert; `_breaker_handled` one-shot guard; startup initializes from stored date for restart safety | 07-05 | VERIFIED | `bot.py` lines 116, 286, 323-378, 537-552: `_breaker_handled`, `_handle_circuit_breaker_side_effects`, startup init in `_readiness_gate` Step 6 |

**Score:** 13/13 truths verified

---

### Exit-Model Gate (Plan 07-06)

The EXIT-MODEL success criterion 2 (exit model chosen from backtest comparison) is explicitly deferred. Plan 07-06 verified the block:

- `find . -name "*.py" | grep -i backtest` returns empty (Phase 6 backtester absent)
- `_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)` in `loader.py` line 38 — loader fails closed
- `07-06-SUMMARY.md` records operator decision: **DEFER**
- No source files modified by plan 07-06

This is not a gap — it is the intended and documented outcome of the phase design.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/state/migrations.py` | `_migration_0005`; `CURRENT_VERSION = 5` | VERIFIED | Lines 246-284; `CURRENT_VERSION = 5` at line 284 |
| `bot/state/store.py` | 5 new methods (TOD baseline + circuit breaker) | VERIFIED | Lines 801, 819, 842, 857, 875 |
| `bot/config/loader.py` | `daily_circuit_breaker_r`, `use_broker_stop_orders`, `rvol_tod_lookback_days`, `exit_model`, `_IMPLEMENTED_EXIT_MODELS` | VERIFIED | Lines 38, 149-165, 223-269 |
| `bot/config/schema.py` | Schema entries for 3 new keys + exit.model enum | VERIFIED | Lines 84, 119, 147, 180 |
| `rules.json` | `risk.daily_circuit_breaker_r`, `execution.use_broker_stop_orders`, `intraday_filters.I3_rvol_tod_lookback_days`, `exit.model` | VERIFIED | Lines 22, 32, 46, 60 |
| `bot/signal/events.py` | `BarEvent.cum_volume: int = 0` | VERIFIED | Line 56 |
| `bot/signal/bar_aggregator.py` | `_session_volume` accumulator + reset + emit | VERIFIED | Lines 125, 140, 294, 307 |
| `bot/scanner/fetcher.py` | `download_intraday_5m` | VERIFIED | Line 546 |
| `bot/scanner/scanner.py` | `_compute_tod_baselines` + `upsert_tod_baselines` call | VERIFIED | Lines 49, 569 |
| `bot/gateway/gateway.py` | `place_stop_order`, `subscribe_quote`, `QuoteTickHandler` | VERIFIED | Lines 241, 592, 764 |
| `bot/position/manager.py` | `arm_stop_protection`, `_sync_broker_stop`, `_on_quote` | VERIFIED | Lines 246, 317, 364 |
| `bot/position/state.py` | `broker_stop_order_id: Optional[str] = None` | VERIFIED | Line 127 |
| `bot/service/bot.py` | `arm_stop_protection` post-fill; `_breaker_handled`; `_handle_circuit_breaker_side_effects`; `cum_volume` wiring | VERIFIED | Lines 116, 263, 286, 312, 323 |
| `bot/signal/signal_engine.py` | `_is_circuit_breaker_tripped` Gate 7; TOD `get_tod_baseline` in Gate 2 | VERIFIED | Lines 402, 516, 576 |

---

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `bot/service/bot.py _process_bar` | `bot/position/manager.py arm_stop_protection` | `await self._position_manager.arm_stop_protection(pos)` at line 312 | WIRED |
| `bot/position/manager.py arm_stop_protection / _sync_broker_stop` | `bot/gateway/gateway.py place_stop_order / cancel_order` | `gateway.place_stop_order(...)` and `gateway.cancel_order(old_id)` | WIRED |
| `bot/position/manager.py _on_quote` | quote-tick fallback → `engine.manage_exit` | `_quote_exiting` one-shot guard + manage_exit call | WIRED |
| `bot/signal/signal_engine.py Gate 2` | `bot/state/store.py get_tod_baseline` | `self._store.get_tod_baseline(session_date_str, code, time_bucket)` at line 517 | WIRED |
| `bot/scanner/scanner.py` | `bot/state/store.py upsert_tod_baselines` | `store.upsert_tod_baselines(scan_date_str, code, tod_baselines)` at line 569 | WIRED |
| `bot/scanner/scanner.py _compute_tod_baselines` | `bot/scanner/fetcher.py download_intraday_5m` | `download_intraday_5m` imported and called in `run_daily_scan` | WIRED |
| `bot/signal/signal_engine.py _is_circuit_breaker_tripped` | `bot/state/store.py get/set/clear_circuit_breaker_date` + `get_daily_trade_stats` | Lines 425-444 in signal_engine | WIRED |
| `bot/service/bot.py _handle_circuit_breaker_side_effects` | `bot/state/store.py get_pending_intent_codes / resolve_pending_intent` + `alerter.send` | Lines 344-378 in bot.py | WIRED |
| `bot/service/bot.py _readiness_gate` Step 6 | `bot/state/store.py get_circuit_breaker_date` | Lines 537-552: `_breaker_handled = (stored_breaker == today_et)` | WIRED |
| `bot/config/loader.py` | `rules.json exit.model` | `ex.get("model", "partial_be_trail")` + `_IMPLEMENTED_EXIT_MODELS` guard | WIRED |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `signal_engine.py Gate 2` | `tod_baseline` | `store.get_tod_baseline` reading from `tod_baselines` SQLite table (written at premarket by scanner) | Yes — scanner writes real yfinance 5m data via `_compute_tod_baselines` → `upsert_tod_baselines` | FLOWING |
| `signal_engine.py Gate 2` | `event.cum_volume` | `BarAggregator._session_volume` → `bar_data["cum_volume"]` → `bot.py` BarEvent construction | Yes — per-bar SDK push volume accumulated in `_handle_row` | FLOWING |
| `signal_engine.py Gate 7` | `realized_pnl` | `store.get_daily_trade_stats(session_date_str)` — reads closed trades table | Yes — realized-only from `trades` table (closed_at date filter, Pitfall 3 mitigated) | FLOWING |
| `manager.py arm_stop_protection` | `pos.trail_stop` | `PositionState.trail_stop` maintained by `evaluate_close` max() ratchet | Yes — live position state from DB + bar FSM | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Test suite green | `python3 -m pytest tests/ -q --tb=no` | 587 passed, 1 skipped | PASS |
| Migration 0005 present | `grep -n "CURRENT_VERSION = 5" bot/state/migrations.py` | Line 284: `CURRENT_VERSION = 5` | PASS |
| Config keys in all three files | `grep -n "daily_circuit_breaker_r" rules.json bot/config/schema.py bot/config/loader.py` | Matches in rules.json line 46, schema.py line 147, loader.py line 151 | PASS |
| Gate 7 before Gate 4 (no SDK on tripped day) | `grep -n "_is_circuit_breaker_tripped\|get_positions" bot/signal/signal_engine.py` | Gate 7 at line 576, Gate 4 (get_positions) at higher line — correct order | PASS |

---

### Probe Execution

Step 7c: SKIPPED — no `scripts/*/tests/probe-*.sh` probes defined for this phase; behavioral spot-checks above serve the same gate.

---

### Requirements Coverage

| Requirement | Plans | Description | Status | Evidence |
|-------------|-------|-------------|--------|----------|
| SIG-RVOL-TOD | 07-01, 07-02, 07-05 | TOD cumulative-volume comparison in I3 gate with legacy fallback | SATISFIED | `get_tod_baseline` in signal_engine Gate 2; `cum_volume` in BarEvent; `_compute_tod_baselines` + `upsert_tod_baselines` in scanner |
| RISK-TICK-STOP | 07-01, 07-03 | Broker-side Stop-Market or quote-tick fallback at tick granularity | SATISFIED | `place_stop_order`, `arm_stop_protection`, `_sync_broker_stop`, `_on_quote`, `subscribe_quote` all wired |
| RISK-CIRCUIT | 07-01, 07-05 | -2R realized-loss circuit breaker, persisted, auto-reset, with D-08 side-effects | SATISFIED | `_is_circuit_breaker_tripped` Gate 7; `_handle_circuit_breaker_side_effects`; `_breaker_handled` startup init |
| EXIT-MODEL | 07-04, 07-06 | Config-driven exit model selector; fail-closed for unimplemented variants; selection gated on Phase 6 | SATISFIED (partial per design) | `exit.model` in rules.json + schema enum + `_IMPLEMENTED_EXIT_MODELS` fail-closed; criterion 2 (selection) explicitly deferred in 07-06 |

No orphaned requirements: all 4 Phase 7 requirement IDs declared across plans are accounted for.

---

### Anti-Patterns Found

Scanned key modified files for stub indicators:

| File | Pattern | Severity | Verdict |
|------|---------|----------|---------|
| `bot/gateway/gateway.py` | No TODO/TBD/FIXME/XXX; `place_stop_order` and `subscribe_quote` fully implemented | — | CLEAN |
| `bot/position/manager.py` | No TODO/TBD markers; `arm_stop_protection`, `_sync_broker_stop`, `_on_quote` all substantive | — | CLEAN |
| `bot/signal/signal_engine.py` | No TODO/TBD markers; Gate 7 and TOD gate fully implemented | — | CLEAN |
| `bot/service/bot.py` | No TODO/TBD markers; `_handle_circuit_breaker_side_effects` fully wired | — | CLEAN |
| `bot/scanner/scanner.py` | No TODO/TBD markers; `_compute_tod_baselines` and 5b step fully wired | — | CLEAN |

**Debt marker gate:** No `TBD`, `FIXME`, or `XXX` markers found in any Phase 7 modified files.

---

### Notable Observations (Non-Blocking)

**07-04-SUMMARY.md is missing.** Plan 07-04's SUMMARY file does not exist in the phase directory (all other plans have SUMMARYs). The code artifacts from plan 07-04 (`exit.model` in rules.json, schema enum, `exit_model` + `_IMPLEMENTED_EXIT_MODELS` in loader) are fully present and committed (commit `0f65d9b` references "wave 2 (07-04) — exit model config surface"). The absence is a documentation gap only — no code is missing.

**07-02 deviation: `bot/service/bot.py` was modified by plan 07-02** (not in plan 07-02's `files_modified` front-matter, but added during execution as a correctness fix). The SUMMARY documents this as an auto-fixed Rule 2 bug. The wiring exists and is correct.

---

### Human Verification Required

#### 1. Broker Stop-Market SIMULATE Support (Plan 07-03 human-check)

**Test:** On a live SIMULATE session with OpenD running and a held paper position, call `Gateway.place_stop_order` for that position and verify whether the order appears in `order_list_query` and triggers at `aux_price` when price crosses.

**Expected:** One of two outcomes — (a) SIMULATE honors the Stop-Market order, confirming `execution.use_broker_stop_orders=true` is correct and the broker path (D-01) is active; or (b) the order is rejected/ignored by SIMULATE, in which case set `execution.use_broker_stop_orders=false` in `rules.json` so the quote-tick fallback path (`_on_quote`, D-02) is used instead. Both code paths are fully implemented; only the config default is at stake.

**Why human:** Empirical broker behavior under SIMULATE cannot be determined by static code inspection. This is explicitly marked as a `<human-check>` in plan 07-03 with `human_verify_mode=end-of-phase`.

---

### Gaps Summary

No gaps found. All 13 must-haves are VERIFIED at all three levels (exists, substantive, wired). The 4 requirement IDs (SIG-RVOL-TOD, RISK-TICK-STOP, RISK-CIRCUIT, EXIT-MODEL) are all accounted for. The test suite passes at 587/1 skipped. EXIT-MODEL criterion 2 is correctly gated on Phase 6 per the phase design — this is not a gap.

The sole outstanding item is the human verification for SIMULATE stop-order support, which determines the correct default value for `execution.use_broker_stop_orders`. Both paths are shipped and functional.

---

_Verified: 2026-07-04T00:30:00Z_
_Verifier: Claude (gsd-verifier)_
