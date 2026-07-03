---
phase: 07-strategy-optimization
plan: 05
subsystem: signal-engine, bot-orchestrator
tags: [rvol-tod, circuit-breaker, SIG-RVOL-TOD, RISK-CIRCUIT, D-05, D-06, D-07, D-08, tdd]
dependency_graph:
  requires: [07-01, 07-02]
  provides:
    - TOD-normalized I3 RVOL gate (SIG-RVOL-TOD) in signal_engine.py
    - -2R daily circuit breaker gate (RISK-CIRCUIT) in signal_engine.py
    - One-shot breaker side-effects with restart persistence in bot.py (D-08)
  affects:
    - bot/signal/signal_engine.py
    - bot/service/bot.py
    - tests/signal/test_signal_engine.py
    - tests/service/test_bot.py
tech_stack:
  added: []
  patterns:
    - TOD cumulative-volume normalization with graceful legacy fallback
    - Fast-path circuit-breaker via meta table persistence (skip DB re-query when already tripped)
    - Auto-reset circuit breaker on new session date (clear_circuit_breaker)
    - One-shot side-effect guard via _breaker_handled flag (idempotent across bars)
    - Startup restart persistence: _breaker_handled initialized from stored date in _readiness_gate
    - ALERT-04: alerter.send failures swallowed in try/except, never propagated to bar loop
key_files:
  created: []
  modified:
    - bot/signal/signal_engine.py
    - bot/service/bot.py
    - tests/signal/test_signal_engine.py
    - tests/service/test_bot.py
decisions:
  - "Gate 7 (circuit breaker) is placed before Gate 4 (get_positions concurrent-cap) so a tripped session incurs zero broker SDK round-trips (T-07-18 mitigation)"
  - "_handle_circuit_breaker_side_effects lives in bot.py (not SignalEngine) to keep SignalEngine dependency-light — alerter and store.get_pending_intent_codes belong to the orchestrator layer"
  - "Realized-only P&L source: get_daily_trade_stats uses closed trades (closed_at date) — open positions never contribute (Pitfall 3, T-07-17 mitigation)"
  - "_breaker_handled initialized in _readiness_gate (Step 6) so a mid-session restart sets it True from stored date — no re-alert, no re-cancel (Pitfall 6, T-07-16 mitigation)"
  - "TOD baseline uses time_key[11:16] ET HH:MM bucket and session_date = now_et().date().isoformat() — never UTC (Pitfall 4, T-07-19 mitigation)"
metrics:
  duration_seconds: 3600
  completed_date: "2026-07-03"
  tasks_completed: 3
  tasks_total: 3
  files_changed: 4
---

# Phase 7 Plan 05: TOD-Normalized I3 Gate + Circuit Breaker Summary

**One-liner:** TOD cumulative-volume RVOL gate with legacy fallback + -2R realized-loss circuit breaker that persists across restart and triggers one-shot PENDING-intent abandonment and Telegram alert.

## Objective

Wire two signal-engine gates consuming Phase 7 foundation infrastructure (07-01 StateStore methods and 07-02 RVOL-TOD data path):

1. **SIG-RVOL-TOD**: Replace the I3 single-bar RVOL ratio with TOD-normalized cumulative volume (fair comparison at any time of day); graceful fallback to legacy ratio when no baseline exists.
2. **RISK-CIRCUIT**: Add Gate 7 (circuit breaker) blocking new entries once realized daily P&L <= -2R; persisted to meta table; auto-resets next session; no mark-to-market (realized-only).
3. **D-08**: Bot orchestrator one-shot side-effects on first trip: abandon PENDING entry intents + one Telegram alert; idempotent across bars; restart-safe via `_breaker_handled` startup init.

## Tasks Completed

| Task | Name | RED Commit | GREEN Commit | Tests Added |
|------|------|------------|--------------|-------------|
| 1 | TOD-normalized I3 RVOL gate (SIG-RVOL-TOD) | `592c45f` | `fb6933d` | 4 |
| 2 | Circuit-breaker Gate 7 in SignalEngine (RISK-CIRCUIT) | `c818683` | `e6660a4` | 5 |
| 3 | Bot orchestrator breaker side-effects (D-08) | `f81b5f6` | `4e162e9` | 4 |

**Total:** 13 new tests. Suite: 587 passed, 1 skipped (was 574 before this plan).

## Implementation Details

### Task 1: TOD-Normalized I3 Gate

**Location:** `bot/signal/signal_engine.py` — Gate 2 (passes_intraday_filters block)

Replaced the single `rvol = event.volume / rvol_baseline` line with:

```python
time_bucket = event.time_key[11:16]  # "HH:MM" in ET
tod_baseline = self._store.get_tod_baseline(session_date_str, code, time_bucket)
if tod_baseline > 0.0:
    rvol = event.cum_volume / tod_baseline   # TOD-normalized primary path
else:
    if rvol_baseline <= 0.0:
        _logger.info("signal_skipped_no_rvol_baseline", ...)
        return None
    rvol = event.volume / rvol_baseline   # legacy fallback
```

The `session_date_str = now_et().date().isoformat()` line already present is reused (Pitfall 4: ET date, not UTC).

### Task 2: Circuit Breaker Gate 7

**Location:** `bot/signal/signal_engine.py` — new `_is_circuit_breaker_tripped` method + Gate 7 call in `on_bar`

Gate ordering: Gate 3 (entry window) → **Gate 7 (circuit breaker)** → Gate 4 (get_positions broker call)

```python
def _is_circuit_breaker_tripped(self, session_date_str: str) -> bool:
    stored_date = self._store.get_circuit_breaker_date()
    if stored_date == session_date_str:
        return True  # already tripped — fast path, no P&L re-query (T-07-18)
    if stored_date is not None and stored_date < session_date_str:
        self._store.clear_circuit_breaker()  # auto-reset prior date (D-07)
    stats = self._store.get_daily_trade_stats(session_date_str)
    realized = stats.get("realized_pnl", 0.0)
    one_r = (self._cfg.max_risk_per_trade_pct / 100.0) * (
        self._cfg.sizing_equity_usd if self._cfg.sizing_equity_usd else 100_000.0
    )
    threshold = -self._cfg.daily_circuit_breaker_r * one_r
    if realized <= threshold:
        self._store.set_circuit_breaker_date(session_date_str)
        _logger.warning("circuit_breaker_tripped", ...)
        return True
    return False
```

### Task 3: Bot Orchestrator Side-Effects (D-08)

**Location:** `bot/service/bot.py`

- `self._breaker_handled: bool = False` in `__init__` (docstring: D-07/Pitfall 6 one-shot guard)
- Step 6 in `_readiness_gate`: sets `self._breaker_handled = (stored_breaker == today_et)` so a mid-session restart does not re-fire alert or re-cancel intents
- `async _handle_circuit_breaker_side_effects(session_date_str)`: checks breaker date == today and `not self._breaker_handled`; marks all PENDING intents ABANDONED; sends one Telegram alert (ALERT-04: failures swallowed); sets `self._breaker_handled = True`
- Called from `_process_bar` after `signal = await self._signal_engine.on_bar(bar)` unconditionally (fires even when gate returned None)

## Verification

```
python3 -m pytest tests/signal/test_signal_engine.py tests/service/test_bot.py -q
# 50 passed

python3 -m pytest tests/ -q --tb=short
# 587 passed, 1 skipped
```

Acceptance criteria verified:

- `grep -n "get_tod_baseline" bot/signal/signal_engine.py` → line 517 (inside Gate 2)
- `grep -n "_is_circuit_breaker_tripped" bot/signal/signal_engine.py` → def at line 402, call at line 576
- `grep -n "_breaker_handled" bot/service/bot.py` → `__init__` (line 116), `_readiness_gate` (lines 537-552), `_handle_circuit_breaker_side_effects` (lines 346, 376)

## Deviations from Plan

None — plan executed exactly as written.

The plan specified an optional `gateway.cancel_order(...)` call for any live broker entry order tied to a PENDING intent. The implementation omits this (intents store only `intent_id` and `code`, not a live broker order ID), consistent with the existing resolve_pending_intent pattern elsewhere in bot.py which marks intents ABANDONED without a broker cancel round-trip. This matches the plan's parenthetical "(if a live broker entry order id is tracked for that intent)" — no such tracking exists in the current data model.

## Threat Mitigations Implemented

| Threat ID | Status | Notes |
|-----------|--------|-------|
| T-07-16 | Mitigated | ET date-string auto-reset + restart persistence via _readiness_gate Step 6 |
| T-07-17 | Mitigated | get_daily_trade_stats is realized-only (closed trades, closed_at date filter) |
| T-07-18 | Mitigated | Gate 7 before Gate 4; tripped fast-path returns None, no SDK round-trip |
| T-07-19 | Mitigated | time_key[11:16] ET HH:MM; session_date_str from now_et().date().isoformat() |
| T-07-20 | Mitigated | alerter.send wrapped in try/except; failure logged, _breaker_handled still set True |

## Known Stubs

None.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced. All new state access uses existing store methods from 07-01.

## Self-Check: PASSED

Files exist:
- FOUND: bot/signal/signal_engine.py
- FOUND: bot/service/bot.py
- FOUND: tests/signal/test_signal_engine.py
- FOUND: tests/service/test_bot.py

Commits exist:
- 592c45f (test RED Task 1)
- fb6933d (feat GREEN Task 1)
- c818683 (test RED Task 2)
- e6660a4 (feat GREEN Task 2)
- f81b5f6 (test RED Task 3)
- 4e162e9 (feat GREEN Task 3)
