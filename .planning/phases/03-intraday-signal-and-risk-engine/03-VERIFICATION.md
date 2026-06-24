---
phase: 03-intraday-signal-and-risk-engine
verified: 2026-06-24T00:00:00Z
status: human_needed
score: 6/6 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run the bot against a live Moomoo SIMULATE session and confirm a closed 5m bar that passes all three intraday filters (price above premarket high, above HOD, RVOL >= 2.0) within 10:05–15:30 ET causes a SignalEvent and then an OrderIntent to appear in the pending_intents table with the correct stop price and quantity, and that no real order is placed."
    expected: "Structlog shows 'signal_emitted' followed by 'order_intent_emitted' for the ticker; pending_intents row exists with status=PENDING; no position appears in the broker account."
    why_human: "Requires a live OpenD connection to a Moomoo SIMULATE account and real intraday market data. The test covers the full BarAggregator → SignalEngine → RiskEngine pipeline under broker I/O — not reproducible in unit tests without a running OpenD service."
  - test: "Trigger a fetch_premarket_highs() call against the live SIMULATE account near 09:30 ET with the actual watchlist and verify that codes with a non-zero pre_high_price are frozen and codes missing pre_high_price (e.g. pre-open before any tick) are excluded."
    expected: "Structlog shows 'premarket_highs_fetched' with valid_count > 0; any code whose pre_high_price is 0 or NaN is absent from the frozen dict and logs 'premarket_high_excluded'."
    why_human: "Requires a live Moomoo session near market open to get real pre_high_price data from the broker snapshot. Cannot be reproduced offline."
---

# Phase 3: Intraday Signal and Risk Engine Verification Report

**Phase Goal:** The bot evaluates closed 5m bars against intraday filters, sizes trades correctly using live account equity, and emits verified OrderIntent events — without placing any real orders.
**Verified:** 2026-06-24
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Bar-close evaluation fires only on closed 5m bars (time_key advance), never mid-bar, never twice on reconnect re-push (SIG-02 no-repaint) | VERIFIED | `BarAggregator._handle_row` buffers the in-progress bar's OHLCV in `_cur_bar[code]` and emits bar A's FINAL snapshot only when `time_key` advances; `_seen_time_keys` dedup set survives reconnects. Six bar_aggregator tests including dedicated no-repaint test (TestBarAggregatorNoRepaint) all pass. CR-01 blocker from review is fixed. |
| 2 | Signal emitted only when I1 (close > premarket_high), I2 (close >= HOD), I3 (RVOL >= rvol_min) all pass AND time is in [10:05, 15:30) ET | VERIFIED | `SignalEngine.on_bar()` delegates I1/I2/I3 to `TrendJoinLong.passes_intraday_filters()`; `_in_entry_window()` parses `cfg.earliest_entry_et`/`cfg.latest_entry_et` with inclusive/exclusive boundaries. 15 signal_engine tests pass including parametrized boundary table (10:04:59 out, 10:05:00 in, 15:29:59 in, 15:30:00 out). |
| 3 | No signal when 5 concurrent positions open, when clock >= 15:30 ET, or when daily cap reached; daily cap independent of concurrent cap | VERIFIED | Concurrent cap gates on `gateway.get_positions()` broker truth. Daily cap gates on `filled_count + _pending_count`. `test_daily_cap_independent_of_concurrent` (both signal and risk test suites) proves closing positions does not reset the daily cap. CR-02 double-count blocker fixed: `on_bar()` does NOT increment `_pending_count`; only `note_intent_emitted()` does, so tally advances by exactly 1 per intent. |
| 4 | Sizing reads LIVE account equity from `gateway.get_equity()` (refresh_cache=True, not cached), risks 1% of equity, caps notional at 10%; worked example produces correct share count | VERIFIED | `RiskEngine.on_signal()` awaits `gateway.get_equity()` per call; `get_equity()` passes `refresh_cache=True` to `accinfo_query`. `test_sizing_worked_example` proves: equity=100000, entry=50.00, lod=48.00 → stop=47.52, risk_qty=403, notional_cap_qty=200, qty=min(403,200)=200. |
| 5 | Initial stop = LOD-1% via compute_initial_stop(lod); OrderIntent logged to structlog contains correct stop_price and quantity | VERIFIED | `risk_engine.py:108` calls `self._strategy.compute_initial_stop(signal.lod)` — does not reimplement the formula. `order_intent_emitted` structlog event at line 177 carries `stop_price` and `quantity`. `test_intent_fields_correct` and `test_intent_logged_to_structlog` both pass. |
| 6 | No real orders placed (Phase 4 owns execution) | VERIFIED | `grep -n "place_order\|PlaceOrder\|trd_ctx\|trade_ctx" bot/risk/risk_engine.py` produces zero matches. RiskEngine emits OrderIntent only and persists to `pending_intents`. 320/320 tests pass with no order-placement path present. |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/signal/bar_aggregator.py` | BarAggregator CurKlineHandlerBase subclass, min 80 lines | VERIFIED | class BarAggregator, 339 lines; `_cur_bar` buffer, `_seen_time_keys` dedup, HOD/LOD running stats, `run_coroutine_threadsafe` bridge with `_log_future_exception` done-callback |
| `bot/signal/events.py` | BarEvent and SignalEvent dataclasses with lod field | VERIFIED | BarEvent (fields: code, time_key, open, high, low, close, volume, hod, lod); SignalEvent (fields: code, bar, premarket_high, hod, lod, rvol, emitted_at) |
| `bot/risk/events.py` | OrderIntent dataclass with stop_price, quantity, intent_id | VERIFIED | OrderIntent with all required fields including stop_price, quantity, intent_id (uuid4) |
| `bot/state/migrations.py` | _migration_0003 callable, CURRENT_VERSION=3, idempotent | VERIFIED | `def _migration_0003` uses `conn.execute()` (not `executescript`); CURRENT_VERSION=3; both tables created with IF NOT EXISTS; 31 migration tests pass including idempotency tests |
| `bot/gateway/gateway.py` | get_market_snapshot() async method | VERIFIED | `async def get_market_snapshot(self, codes)` at line 382; uses `get_running_loop()` + `run_in_executor`; returns raw (ret, data) tuple interpretation-free |
| `bot/gateway/gateway.py` | get_equity() async method with $100k fallback | VERIFIED | `async def get_equity()` at line 323; refresh_cache=True; dual implausible bounds (_IMPLAUSIBLE_LOW=1000, _IMPLAUSIBLE_HIGH=10_000_000); all 6 fallback tests pass |
| `bot/signal/signal_engine.py` | SignalEngine with all 6 gates, min 90 lines | VERIFIED | class SignalEngine, 506 lines; all 6 gates (D-03, SIG-03 filters, entry window, concurrent cap, D-10 re-entry, RISK-05 daily cap) as independent early-returns with structlog events |
| `bot/risk/risk_engine.py` | RiskEngine with math.floor sizing, LOD-1% stop, OrderIntent emission, min 90 lines | VERIFIED | class RiskEngine, 194 lines; math.floor for both risk_qty and notional_cap_qty; compute_initial_stop delegation; parameterized INSERT to pending_intents; structlog order_intent_emitted |
| `tests/signal/test_bar_aggregator.py` | SIG-02 coverage including test_no_double_fire_on_reconnect | VERIFIED | 6 tests pass; TestBarAggregatorNoRepaint::test_closed_bar_carries_closing_bar_ohlcv_not_new_bars_first_tick is the CR-01 regression guard |
| `tests/signal/test_signal_engine.py` | 15 tests including test_entry_window_boundaries | VERIFIED | 15 tests all pass; test_entry_window_boundaries parametrized with 4 boundary cases |
| `tests/risk/test_risk_engine.py` | RISK-01/02/03 coverage including test_equity_fallback | VERIFIED | 12 tests pass (10 original + 2 wired pipeline tests) |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `bot/signal/bar_aggregator.py` | asyncio event loop | `asyncio.run_coroutine_threadsafe(self._on_bar_closed(bar_data), self._loop)` | WIRED | Line 311; future stored and `_log_future_exception` done-callback attached (WR-02 fix) |
| `bot/state/migrations.py` | MIGRATIONS list | `_migration_0003` appended; CURRENT_VERSION=3 | WIRED | Line 183; MIGRATIONS[2] = _migration_0003; CURRENT_VERSION=3 at line 187 |
| `bot/gateway/gateway.py get_market_snapshot()` | moomoo `get_market_snapshot` | `run_in_executor(lambda: self._quote_ctx.get_market_snapshot(codes))` | WIRED | Lines 410-413 |
| `bot/signal/signal_engine.py` | `bot/strategy/trend_join_long.py` | `TrendJoinLong(cfg).passes_intraday_filters(code, bars_5m, premarket_high, hod, rvol)` | WIRED | Line 372 |
| `bot/signal/signal_engine.py` | `bot/gateway/gateway.py` | `await gateway.get_positions()` for concurrent cap | WIRED | Line 404 |
| `bot/signal/signal_engine.py` | `bot/safety/et_helpers.py` | `now_et()` for entry-window gate and date keys | WIRED | Lines 248, 345 |
| `bot/signal/signal_engine.py` | `daily_trade_count` table | `SELECT filled_count FROM daily_trade_count WHERE session_date = ?` | WIRED | Lines 267-270 (read-only; never writes this table) |
| `bot/signal/signal_engine.py` | `pending_intents` table | `SELECT 1 FROM pending_intents WHERE code = ? AND status = 'PENDING'` | WIRED | Lines 286-288 (D-10 re-entry gate) |
| `bot/signal/signal_engine.py fetch_premarket_highs()` | `bot/gateway/gateway.py get_market_snapshot()` | `await gateway.get_market_snapshot(codes)` + D-03 exclusion | WIRED | Lines 155-220 |
| `bot/gateway/gateway.py get_equity()` | moomoo `accinfo_query` | `run_in_executor(lambda: self._trade_ctx.accinfo_query(..., refresh_cache=True))` | WIRED | Lines 342-348 |
| `bot/risk/risk_engine.py` | `bot/gateway/gateway.py` | `equity = await gateway.get_equity()` on every signal | WIRED | Line 122 |
| `bot/risk/risk_engine.py` | `bot/strategy/trend_join_long.py` | `TrendJoinLong(cfg).compute_initial_stop(signal.lod)` | WIRED | Line 108 |
| `bot/risk/risk_engine.py` | `pending_intents` table | `INSERT INTO pending_intents (...) VALUES (?, ...)` + `conn.commit()` | WIRED | Lines 161-174 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|-------------------|--------|
| `bot/signal/signal_engine.py` | `premarket_high` | `fetch_premarket_highs()` → `get_market_snapshot()` → `pre_high_price` field | Yes (real broker snapshot, D-03 filtered) | FLOWING |
| `bot/signal/signal_engine.py` | `rvol_baseline` | `SELECT rvol_baseline FROM daily_scan WHERE scan_date=? AND code=?` | Yes (populated by Phase 2 scanner) | FLOWING |
| `bot/risk/risk_engine.py` | `equity` | `await gateway.get_equity()` → `accinfo_query(refresh_cache=True)` → `total_assets` | Yes (live broker read, fallback to $100k) | FLOWING |
| `bot/risk/risk_engine.py` | `stop_price` | `compute_initial_stop(signal.lod)` where `lod` = session running-min from BarAggregator | Yes (computed from real session data) | FLOWING |
| `bot/risk/risk_engine.py` | `pending_intents` row | Parameterized INSERT with real intent fields | Yes (written to SQLite on every emitted intent) | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 320 tests pass (regression) | `python3 -m pytest -q` | 320 passed in 3.64s | PASS |
| SIG-02 mid-bar + reconnect + no-repaint (bar_aggregator) | `python3 -m pytest tests/signal/test_bar_aggregator.py -v` | 6 passed | PASS |
| Entry-window boundary table (10:04:59/10:05:00/15:29:59/15:30:00) | `python3 -m pytest tests/signal/test_signal_engine.py -v` | 15 passed | PASS |
| Sizing worked example (qty=200 when notional cap binds) | `python3 -m pytest tests/risk/test_risk_engine.py::TestRiskEngineSizingMath -v` | 2 passed | PASS |
| Migration 0003 idempotent at user_version=3 | `python3 -m pytest tests/state/test_migrations.py -v` | 31 passed | PASS |
| get_equity() all fallback paths (6 gateway tests) | `python3 -m pytest tests/gateway/test_gateway.py -k equity -v` | 6 passed | PASS |
| Pending count increments exactly once per intent (wired pipeline) | `python3 -m pytest tests/risk/test_risk_engine.py::TestPendingCountWiredPipeline -v` | 2 passed | PASS |
| No order-placement symbols in risk_engine.py | `grep -n "place_order\|PlaceOrder\|trd_ctx\|trade_ctx" bot/risk/risk_engine.py` | no output | PASS |

---

### Probe Execution

Step 7c: SKIPPED (no probe scripts declared for this phase; `scripts/*/tests/probe-*.sh` not present; phase does not constitute a migration/tooling phase requiring probes).

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| SIG-02 | 03-01 | Entry signals evaluated only on closed 5m bars — never mid-bar | SATISFIED | BarAggregator time_key-advance bar-close detection; `_seen_time_keys` reconnect dedup; 6 passing bar_aggregator tests |
| SIG-03 | 03-02 | Entry when price > premarket high, > HOD, RVOL >= 2.0, within 10:05–15:30 ET | SATISFIED | SignalEngine.on_bar() gates 1-3; passes_intraday_filters delegation; _in_entry_window() config-driven; 15 passing signal_engine tests |
| SIG-04 | 03-02 | No new entries when 5 concurrent positions open or after 15:30 ET cutoff | SATISFIED | Gate 4 (concurrent cap from get_positions() broker truth); Gate 3 (entry window already covers 15:30 cutoff); test_concurrent_cap passes |
| RISK-01 | 03-03 | Each position sized to risk 1% of current account equity (read live, not cached) | SATISFIED | get_equity() passes refresh_cache=True; awaited on every on_signal() call; test_live_equity_called passes |
| RISK-02 | 03-03 | Position notional capped at 10% of portfolio value | SATISFIED | math.floor(notional_cap / entry_price); min(risk_qty, notional_cap_qty) takes smaller; test_notional_cap passes |
| RISK-03 | 03-03 | Initial stop = low-of-day minus 1% | SATISFIED | compute_initial_stop(signal.lod) delegation; stop_price and quantity in OrderIntent and structlog; test_intent_fields_correct passes |
| RISK-04 | 03-02 | Maximum 5 concurrent positions enforced at order-submission time | SATISFIED | SignalEngine concurrent cap gate reads broker truth via get_positions(); test_concurrent_cap passes |
| RISK-05 | 03-02 | Daily new-entry cap enforced independently of concurrent cap | SATISFIED | filled_count + _pending_count gate; closing positions does NOT decrement _pending_count; test_daily_cap_independent_of_concurrent passes in both signal and risk test suites |

All 8 required requirement IDs are accounted for and satisfied.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/signal/signal_engine.py` | 388 | Comment `# Also check if this code is already in an open position` (was `US.AAPL` residue per review IN-03; now corrected in current code) | None | IN-03 was addressed: line 417 reads "Also check if this code is already in an open broker position" |
| `bot/risk/__init__.py` | 6-9 | Docstring claims RiskEngine exported but it is not (IN-01) | Info | Minor doc-vs-reality; no behavioral impact; imports work correctly from submodule |

No TBD, FIXME, or XXX markers found in any Phase 3 files.

No stub patterns found (no `return null`, `return {}`, `return []`, or placeholder strings in implementation files).

---

### Review Blockers — Disposition

The 03-REVIEW.md identified 3 critical blockers and 6 warnings. All 3 blockers are fixed in the current code:

| Blocker | Fix Applied | Verified By |
|---------|------------|-------------|
| CR-01: SIG-02 no-repaint violated — closed BarEvent carried new bar's OHLCV | `_cur_bar[code]` buffers the in-progress bar's OHLCV; on advance, `closed_ohlcv = self._cur_bar.get(code)` emits bar A's final values; HOD/LOD snapshotted before updating with bar B's first tick | TestBarAggregatorNoRepaint test passes |
| CR-02: Daily-cap burst guard double-counted — `_pending_count` incremented twice per intent | `on_bar()` no longer increments `_pending_count`; only `note_intent_emitted()` does; docstring explicitly documents this constraint; TestPendingCountWiredPipeline confirms exactly 1 increment per intent | TestPendingCountWiredPipeline passes |
| CR-03: `_migration_0003` used `executescript` violating WR-03 atomicity | `_migration_0003` now uses `conn.execute()` for both CREATE TABLE statements (not `executescript`); DDL stays in caller's open transaction; atomicity with PRAGMA user_version bump is restored | Migration 0003 idempotency tests pass |

Warning items WR-01 through WR-06 from the review were also addressed:
- WR-01: All async wrappers now use `asyncio.get_running_loop()` (verified: 6 occurrences, all `get_running_loop`)
- WR-02: Future done-callback `_log_future_exception` attached (line 314 of bar_aggregator.py)
- WR-03: Docstring tightened; mutable dicts not exposed off-thread
- WR-04: `_IMPLAUSIBLE_HIGH` documented with rationale in gateway.py lines 54-66
- WR-05: Column existence check before iterrows in `fetch_premarket_highs` (lines 177-187)
- WR-06: ET date convention documented in migration 0001 comment and signal_engine WR-06 note

---

### Human Verification Required

#### 1. End-to-End Signal and Intent Pipeline (Live Broker)

**Test:** Start OpenD connected to a Moomoo SIMULATE account; run the bot's signal/risk pipeline against live intraday 5m bar data; wait for a closed bar that meets all three intraday filters (price above premarket high, above HOD, RVOL >= 2.0) within 10:05–15:30 ET.

**Expected:** Structlog shows `bar_closed`, `signal_emitted`, then `order_intent_emitted` for the same ticker; a row appears in `pending_intents` with `status=PENDING` and the correct `stop_price` and `quantity`; no position appears in the SIMULATE broker account.

**Why human:** Requires a live OpenD connection to a Moomoo SIMULATE account and real intraday market data streaming through the BarAggregator SDK callback path. Unit tests mock the broker; only a live session can validate the full SDK callback thread → asyncio bridge under real market conditions.

#### 2. Premarket High Freeze at Market Open (Live Broker)

**Test:** Near 09:30 ET, call `fetch_premarket_highs(watchlist_codes)` against the live SIMULATE account with the actual watchlist codes.

**Expected:** Structlog shows `premarket_highs_fetched` with `valid_count > 0`; codes with `pre_high_price=0` or NaN log `premarket_high_excluded`; subsequent `on_bar()` calls use the frozen highs for I1 evaluation.

**Why human:** `pre_high_price` is only populated by Moomoo's server near market open; offline tests use mock data and cannot validate the actual field name, availability window, or D-03 zero-exclusion behavior under real broker conditions.

---

### Gaps Summary

No automated gaps were found. All 6 success criteria are verified in the codebase. All 8 requirement IDs are satisfied. All 3 review blockers (CR-01, CR-02, CR-03) were fixed prior to submission. All 320 tests pass.

The only outstanding items require a live Moomoo SIMULATE session and are flagged for human verification above.

---

_Verified: 2026-06-24_
_Verifier: Claude (gsd-verifier)_
