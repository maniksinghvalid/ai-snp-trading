---
phase: 03-intraday-signal-and-risk-engine
plan: "02"
subsystem: signal-engine
tags: [signal, signal-engine, SIG-03, SIG-04, RISK-04, RISK-05, D-01, D-03, D-08, D-09, D-10, D-11]
dependency_graph:
  requires:
    - bot/signal/events.py (BarEvent, SignalEvent — 03-01)
    - bot/strategy/trend_join_long.py (passes_intraday_filters — Phase 1)
    - bot/config/loader.py (StrategyConfig — Phase 1)
    - bot/safety/et_helpers.py (now_et — Phase 1)
    - bot/state/store.py (StateStore, daily_trade_count, pending_intents — Phase 1/03-01)
    - bot/gateway/gateway.py (get_positions, get_market_snapshot — Phase 1/03-01)
    - bot/safety/logger.py (get_logger — Phase 1)
  provides:
    - bot/signal/signal_engine.py (SignalEngine)
    - bot/signal/signal_engine.py#SignalEngine.fetch_premarket_highs (D-01 session-init source)
    - bot/signal/signal_engine.py#SignalEngine.has_pending_intent (D-10 re-entry gate helper)
    - bot/signal/signal_engine.py#SignalEngine.note_intent_emitted / note_intent_resolved (D-09 tally API)
  affects:
    - tests/signal/test_signal_engine.py (fully fleshed — 15 tests)
    - bot/risk/risk_engine.py (03-03 — consumes SignalEvent; calls note_intent_emitted after sizing)
tech_stack:
  added: []
  patterns:
    - TDD: RED (failing import) → GREEN (all 15 tests pass) → no REFACTOR needed
    - Config-driven gate boundaries (CFG-01): all thresholds from StrategyConfig, zero literals
    - Independent gate short-circuit with specific structlog event per gate (operator visibility)
    - D-09 burst guard: in-memory _pending_count + persisted filled_count prevents burst over-cap
    - Parameterized SQL for has_pending_intent and _get_filled_count (no injection surface)
    - asyncio.run() in tests (Python 3.14 removed implicit default event loop in main thread)
key_files:
  created:
    - bot/signal/signal_engine.py
  modified:
    - tests/signal/test_signal_engine.py
decisions:
  - "CFG-01 enforced: _in_entry_window() parses cfg.earliest_entry_et/latest_entry_et HH:MM strings; no time literals"
  - "D-03 conservative: pre_high_price <= 0 OR None/NaN excluded in fetch_premarket_highs; never falls back to prior-day high"
  - "D-09 burst guard: _pending_count incremented immediately on SignalEvent emit; fills/cancels tracked via note_intent_resolved()"
  - "D-10 re-entry gate: BOTH broker-flat (not in get_positions()) AND has_pending_intent() == False required before re-signal"
  - "D-11 upheld: blocked signals (any gate) return None without touching _pending_count"
  - "RESEARCH Pitfall 2: rvol = event.volume / rvol_baseline from daily_scan — never recomputed in SignalEngine"
  - "RESEARCH Pitfall 5: SignalEngine never writes daily_trade_count — Phase 4 owns that counter at fill"
  - "asyncio.run() used in tests instead of get_event_loop() (Python 3.14 behavioural change)"
metrics:
  duration_seconds: 272
  completed_date: "2026-06-24"
  tasks_completed: 2
  files_created: 1
  files_modified: 1
---

# Phase 03 Plan 02: SignalEngine — Intraday Gate Orchestrator Summary

**One-liner:** SignalEngine wires passes_intraday_filters() (I1/I2/I3) through four independent gates — premarket-high guard (D-03), entry-window (CFG-01 config-driven inclusive/exclusive boundaries), concurrent-position cap (broker truth via get_positions()), D-10 re-entry guard (broker-flat AND no pending intent), and daily-cap burst guard (filled_count + pending_count via D-09) — emitting SignalEvents only when all gates pass, with no numeric literals and blocked signals consuming no entry slots.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 (RED) | Failing test suite for SignalEngine | 0559814 | tests/signal/test_signal_engine.py (551 insertions) |
| 1+2 (GREEN) | SignalEngine implementation — all gates | 21818b6 | bot/signal/signal_engine.py (new, 476 lines), tests/signal/test_signal_engine.py (asyncio fix) |

## Verification Results

- `pytest tests/signal/test_signal_engine.py::test_all_gates_required -x` → **1 passed** (I1/I2/I3 + window all required)
- `pytest tests/signal/test_signal_engine.py::test_entry_window_boundaries -x` → **4 passed** (10:04:59 out, 10:05:00 in, 15:29:59 in, 15:30:00 out)
- `pytest tests/signal/test_signal_engine.py -k premarket_highs -x` → **2 passed** (freeze + D-03 exclusion)
- `pytest tests/signal/test_signal_engine.py::test_concurrent_cap -x` → **1 passed** (broker truth cap)
- `pytest tests/signal/test_signal_engine.py -x` → **15 passed**
- `pytest tests/signal/ -x` → **20 passed** (no regression in bar_aggregator)
- `pytest tests/ -x -q` → **301 passed, 16 skipped** (no regression in Phase 1/2 suites; 16 skips are Wave 0 stubs for 03-03)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] asyncio.run() instead of get_event_loop().run_until_complete() in tests**
- **Found during:** Task 1 GREEN phase — first test run after implementing SignalEngine
- **Issue:** Python 3.14 removed the implicit default event loop in the main thread; `asyncio.get_event_loop()` raises `RuntimeError: There is no current event loop in thread 'MainThread'`. The original `run()` helper in the test file used `asyncio.get_event_loop().run_until_complete(coro)`.
- **Fix:** Changed the `run()` helper to use `asyncio.run(coro)`, which creates a fresh event loop per call — the correct Python 3.10+ pattern.
- **Files modified:** tests/signal/test_signal_engine.py
- **Commit:** 21818b6 (included in GREEN phase commit)

## Key Decisions Made

1. **CFG-01 enforced via HH:MM string parsing:** `_in_entry_window()` parses `cfg.earliest_entry_et` and `cfg.latest_entry_et` strings into `datetime.time` objects with no hardcoded values. Changing `rules.json` changes the window — behavioral proof is `test_config_driven_window`.

2. **D-03 conservative exclusion in fetch_premarket_highs:** Codes with `pre_high_price <= 0`, `None`, or `NaN` are excluded from the frozen dict. A non-RET_OK gateway response returns an empty dict without raising — all entries for that day have no premarket reference and are safely skipped.

3. **RVOL read from daily_scan.rvol_baseline (RESEARCH Pitfall 2):** `on_bar()` queries `daily_scan WHERE scan_date=? AND code=?` to get `rvol_baseline`, then computes `rvol = event.volume / rvol_baseline`. The signal engine never recomputes or re-fetches RVOL from a broker call.

4. **D-09 burst guard is in-memory + persistent:** `_pending_count` increments immediately when a SignalEvent is emitted, so a burst of bars hitting the engine in rapid succession cannot all slip through before any fill registers. `filled_count` from `daily_trade_count` is the persistent authoritative counter (Phase 4 writes it at fill); together they form the D-09 `filled + pending` gate.

5. **D-10 re-entry requires BOTH conditions:** The re-entry gate checks (a) `code not in open_codes` from `get_positions()` AND (b) `has_pending_intent(code)` == False. A code that is broker-flat but has a live PENDING intent row is blocked — this prevents double-intent emission during the Phase 4 fill latency window.

6. **Phase 3 NEVER writes daily_trade_count (RESEARCH Pitfall 5):** `_get_filled_count()` is read-only. No UPDATE or INSERT on `daily_trade_count` exists anywhere in `signal_engine.py`. Source assertion passes.

## Artifact Verification

All `must_haves.artifacts` verified:

| Artifact | Check | Status |
|----------|-------|--------|
| bot/signal/signal_engine.py | class SignalEngine, 476 lines (min 90) | PASS |
| bot/signal/signal_engine.py | fetch_premarket_highs() present | PASS |
| bot/signal/signal_engine.py | has_pending_intent() present | PASS |
| tests/signal/test_signal_engine.py | test_entry_window_boundaries present | PASS |
| tests/signal/test_signal_engine.py | 15 tests all green | PASS |

## Self-Check: PASSED

Files exist:
- bot/signal/signal_engine.py ✓ (476 lines)
- tests/signal/test_signal_engine.py ✓ (670 lines, 15 tests)

Commits exist:
- 0559814 (RED phase test stub expansion) ✓
- 21818b6 (GREEN phase implementation) ✓

Source assertions:
- No UPDATE/INSERT on daily_trade_count in signal_engine.py ✓
- No hardcoded time/threshold literals in _in_entry_window() ✓
- get_positions() used for concurrent count (not in-memory list) ✓
- pending_intents queried with parameterized SQL (? binding) ✓
