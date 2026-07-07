---
phase: 06-backtester
plan: 05
subsystem: backtester-harness
tags: [backtester, harness, replay-controller, wave-3, BT-01, BT-02]
dependency-graph:
  requires:
    - backtester.feed.SimulatedBarFeed (06-02)
    - backtester.execution.SimulatedExecution / SimulatedGateway (06-03)
    - backtester.report.compute_metrics / write_report (06-04, consumed by 06-06)
  provides:
    - backtester.harness.BacktestHarness
  affects:
    - backtester/run.py (06-06, CLI entry point will construct BacktestHarness and call write_report(harness.trade_log, ...))
tech-stack:
  added: []
  patterns:
    - "Reused live pipeline construction (bot/main.py Pattern 1) with only gateway/execution swapped"
    - "Per-bar clock rebind: bot.signal.signal_engine.now_et monkeypatched to the replay bar's ET time for run()'s duration, restored in finally (T-06-11)"
    - "No-look-ahead TOD baseline: intraday_5m_for_tod data filtered to sessions strictly prior to `day` before _compute_tod_baselines (mirrors _evaluate_symbol's own date < scan_date cutoff)"
key-files:
  created:
    - backtester/harness.py
  modified:
    - tests/backtester/test_harness.py
decisions:
  - "06-05: TOD-baseline input passed to _compute_tod_baselines is filtered to sessions STRICTLY PRIOR to `day` (bar_frame.index.date < day) before persisting -- computing it from the SAME day being replayed is self-referential (baseline == that day's own cumulative volume, ratio always exactly 1.0) and violates BT-02's no-look-ahead requirement; this filter is required for genuine RVOL-TOD signals to ever fire in a backtest."
  - "06-05: _compute_tod_baselines is called with cfg.rvol_tod_lookback_days (the dedicated I3_rvol_tod_lookback_days config field), not cfg.rvol_lookback_days -- matches bot/scanner/scanner.py's own run_daily_scan call site exactly."
  - "06-05: Dropped the D-08 circuit-breaker side-effect gate (_entries_enabled / _handle_circuit_breaker_side_effects) -- there is no kill-switch concept in an offline replay; entries are always enabled in the harness. SignalEngine's own Gate 7 circuit-breaker check still runs and can still block entries; only the TradingBot-specific abandon-PENDING-intents+alert side effect is out of scope for a harness with no alerter."
  - "06-05: PositionState.opened_at/updated_at are set from the fill's own bar time when constructing the AWAITING_FILL position (not left None until on_fill, unlike bot.service.bot._process_bar) -- StateStore's positions table has both columns NOT NULL (migration 0001); against a REAL StateStore (unlike existing unit tests, which always mock the store or hand-set opened_at), leaving them None crashes register_position's DB-first write. apply_entry_fill's later re-assignment of the same value is a no-op."
  - "06-05: FillEvent.fill_time is normalised from SimulatedExecution's raw time_key STRING to a real ET datetime before being handed to position_manager.on_fill() -- bot/position/manager.py's _on_entry_fill calls fill_time.isoformat() when persisting, which raises AttributeError on a plain string. This is a harness-side adapter (mutates the harness's own FillEvent reference); backtester/execution.py itself is unmodified."
  - "06-05: test_harness.py's full end-to-end replay test uses a DEDICATED signal/fill/stop-out/exit dataset (not the shared ahead-only fixture from tests.backtester.fixtures) because a real SignalEngine.on_bar() pass requires I2 (close >= hod) to hold, and bot/signal/bar_aggregator.py's hod snapshot INCLUDES the closing bar's own high (confirmed by reading the snap_hod capture order) -- the ahead-only fixture's breakout bar closes below its own high and would never pass I2 through the real gate, only through hand-built OrderIntents (as 06-01/06-03 already exercise it)."
metrics:
  duration_minutes: 45
  completed: 2026-07-06
---

# Phase 06 Plan 05: BacktestHarness Summary

Built `backtester/harness.py`'s `BacktestHarness` — the replay controller that ports
`bot.service.bot.TradingBot._process_bar` bar-for-bar and wires the reused live pipeline
(`TrendJoinLong`, `SignalEngine`, `RiskEngine`, `PositionManager`) against the simulated
leaves (`SimulatedBarFeed`/`SimulatedExecution`/`SimulatedGateway`) built in 06-02/06-03.
This is the plan realizing Phase 6's "confirming live/backtest code parity" goal: the
strategy/signal/risk/FSM code is imported UNCHANGED — only `gateway`/`execution` differ.

## What Was Built

**Construction (`BacktestHarness.__init__`):**
- Mirrors `bot/main.py`'s construction order exactly, swapping `MoomooGateway`/
  `ExecutionEngine` for `SimulatedGateway`/`SimulatedExecution` and requiring the caller's
  `StateStore` to already be opened at a scratch `db_path` (never `data/bot_state.db`).
- `PositionManager` receives `gateway=None` so `arm_stop_protection()` correctly no-ops (the
  bar-close FSM stop is the sole stop mechanism at 5m granularity) — a harness-owned
  `bar_buffer: Dict[str, deque(maxlen=50)]` is passed in, mirroring `BarAggregator`'s exact
  shape.
- `SimulatedGateway` is constructed over a zero-arg lambda resolving `self.position_manager`
  lazily (the gateway is built before the position manager exists).

**Per-day setup (`setup_day(day, symbols)`):**
- Reuses `bot.scanner.scanner._evaluate_symbol` (SMA200/D1-D3/RVOL, `scan_date=day` cutoff)
  and `_compute_tod_baselines` (RVOL-TOD) directly — never reimplemented. Candidates are
  gap-ranked and persisted via `store.persist_watchlist(day, ranked, "backtest")`.
- **Correctness fix beyond the plan's literal pseudocode:** the 5m frame passed to
  `_compute_tod_baselines` is filtered to sessions **strictly prior to `day`** before the
  call (`_prior_sessions_only`). Computing the TOD baseline from the SAME day being replayed
  is self-referential (the baseline would equal that day's own cumulative-volume trajectory,
  making every RVOL ratio exactly `1.0` and never `>= I3_rvol_min`) — this violates BT-02's
  no-look-ahead requirement and would silently prevent every backtest signal from firing.
  Verified analytically and empirically during test development.
- `signal_engine.set_premarket_highs(feed.premarket_highs(day))` seeds premarket highs
  unconditionally (independent of whether any daily-scan candidate was persisted).

**Replay loop (`replay_day`/`run`/`_process_bar`):**
- Ports `_process_bar`'s exact sequence: `sim_execution.on_bar(bar_data)` (records the
  anchor `manage_exit` needs) and the bar's OHLCV dict appended to `bar_buffer[code]`
  **before** `position_manager.on_bar(bar)` (Pitfall 6); then the entry branch
  (`signal_engine.on_bar` → `risk_engine.on_signal` → `sim_execution.consume_intent` →
  `register_position`/`on_fill`/`arm_stop_protection`, or the abandon path +
  `note_intent_resolved()`).
- `run()` rebinds `bot.signal.signal_engine.now_et` to the harness's per-bar replay clock for
  its whole duration (try/finally — never leaks the patch on early exit), so `_in_entry_window`
  and the `session_date_str` baseline keys are evaluated against the historical bar's ET time,
  never the wall clock (T-06-11).
- Drops the D-08 circuit-breaker side-effect gate (`_entries_enabled`) — no kill-switch
  concept in an offline replay; documented as an explicit choice. `SignalEngine`'s own Gate 7
  circuit-breaker check still runs.
- `_capture_closed_trades()` scans `position_manager._positions` for newly-`CLOSED` positions
  after every bar and appends a trade-log row (entry/exit price, quantity, exit reason,
  r_multiple) built from `SimulatedExecution.exit_fills`, exposed as `self.trade_log`.

## Verification

```
python3 -m pytest tests/backtester/test_harness.py -q   → 6 passed
python3 -m pytest tests/backtester/ -q                    → 21 passed
python3 -m pytest tests/ -q                                → 610 passed, 1 skipped
grep -nE '_evaluate_symbol|_compute_tod_baselines' backtester/harness.py   → both reused
grep -c 'sma200|def.*rvol' backtester/harness.py                          → 0 (no reimplemented math)
grep -nE 'now_et' backtester/harness.py                                    → replay clock bound
grep -n 'resolve_pending_intent|note_intent_resolved|register_position' backtester/harness.py → _process_bar sequence ported
grep -L 'MoomooGateway' backtester/harness.py                              → lists harness.py (no broker import)
wc -l backtester/harness.py                                                → 369 (>= min_lines 150)
```

No file under `bot/` was touched — full-suite stability (610 passed, same 1 pre-existing
skip) confirms no regression.

## Deviations from Plan

**1. [Rule 1 - Bug] TOD-baseline computation restricted to sessions strictly prior to `day`**
- **Found during:** Task 2, writing the full-replay test.
- **Issue:** The plan's construction pseudocode calls `_compute_tod_baselines` on whatever 5m
  frame `feed.intraday_5m_for_tod()` returns, with no explicit date cutoff. In a backtest
  (unlike live, where the fetch happens pre-market and naturally can't include today's
  not-yet-happened bars), the feed already holds the FULL historical range including `day`
  itself — computing the baseline from that unfiltered frame is self-referential and
  produces an RVOL ratio of exactly `1.0` for every bucket, which can never clear the
  `I3_rvol_min` threshold. This would silently make every backtest signal path dead on Gate 2.
- **Fix:** Added `_prior_sessions_only(frame_5m, day)`, filtering to `date < day` before the
  `_compute_tod_baselines` call — mirrors `_evaluate_symbol`'s own `date < scan_date` mask.
- **Files modified:** backtester/harness.py
- **Commit:** 19a33fc

**2. [Rule 1 - Bug] Fixed `_compute_tod_baselines` lookback-days config field**
- **Found during:** writing the setup_day point-in-time-reuse test.
- **Issue:** Initially called `_compute_tod_baselines(frame, self._cfg.rvol_lookback_days)` —
  the wrong config field (that one drives the DAILY RVOL lookback via `_evaluate_symbol`, a
  separate filter). Production's own `run_daily_scan` call site uses
  `cfg.rvol_tod_lookback_days` (`I3_rvol_tod_lookback_days`).
- **Fix:** Corrected the call to use `cfg.rvol_tod_lookback_days`.
- **Files modified:** backtester/harness.py
- **Commit:** 19a33fc

**3. [Rule 1 - Bug] `PositionState.opened_at`/`updated_at` set from the fill's bar time**
- **Found during:** Task 2, first full-replay test run (crashed with
  `sqlite3.IntegrityError: NOT NULL constraint failed: positions.opened_at`, then the same
  for `updated_at`).
- **Issue:** `bot.service.bot._process_bar` constructs the `AWAITING_FILL` `PositionState`
  without `opened_at`/`updated_at` (both default to `None`), only set later by
  `apply_entry_fill`. Against a StateStore with the real migrated schema — where both columns
  are `NOT NULL` — `register_position`'s DB-first write crashes immediately. Every existing
  unit test either mocks the store or hand-sets `opened_at` in its own fixture helper, so this
  path had never been exercised against a real, freshly-migrated SQLite file end-to-end.
- **Fix:** The harness sets both fields from the fill's own bar time when constructing the
  position (harness-side only — `bot/` is unmodified); `apply_entry_fill`'s later
  reassignment of `opened_at` to the same value is a no-op.
- **Files modified:** backtester/harness.py
- **Commit:** 19a33fc

**4. [Rule 1 - Bug] `FillEvent.fill_time` normalised to a real datetime before `on_fill()`**
- **Found during:** Task 2, second full-replay test run
  (`AttributeError: 'str' object has no attribute 'isoformat'`).
- **Issue:** `SimulatedExecution` (06-03) stores `FillEvent.fill_time` as the bar's raw
  `time_key` STRING (matching `feed.py`/`fixtures.py`'s own convention), but
  `bot/position/manager.py::_on_entry_fill` calls `fill_time.isoformat()` when persisting —
  which only works on a real `datetime`.
- **Fix:** The harness parses `fill.fill_time` into a tz-aware ET `datetime` immediately after
  `consume_intent()` returns, before constructing the `PositionState` or calling `on_fill()`.
  This is a harness-side adapter on the harness's own `FillEvent` reference —
  `backtester/execution.py` is unmodified (out of this plan's `files_modified` scope).
- **Files modified:** backtester/harness.py
- **Commit:** 19a33fc

**5. [Test-design] Full end-to-end replay test uses a dedicated fixture, not the shared
   ahead-only dataset**
- **Found during:** Task 2, before any implementation — analysis of `bot/signal/bar_aggregator.py`'s
  `snap_hod` capture order showed `BarEvent.hod` INCLUDES the closing bar's own high (captured
  from `self._hod[code]`, which is updated eagerly on every mid-bar tick of that SAME bar
  before being snapshotted). `tests/backtester/fixtures.make_ahead_only_5m_dataset`'s
  breakout bar (index 3) closes at 105.00 with a high of 105.50 — `close < hod` — so it would
  never pass the real `SignalEngine.on_bar()`'s I2 gate (only ever exercised via hand-built
  `OrderIntent`s in 06-01/06-03's tests, never through the live gate sequence).
- **Resolution:** `tests/backtester/test_harness.py`'s full-replay test builds its own small,
  dedicated 7-bar dataset (signal bar closing AT its own high; 14 prior low-volume sessions at
  matching 5m bucket times so a genuine, non-self-referential RVOL-TOD baseline exists; a
  premarket fixture below the signal bar's close; a crash bar after the fill so the position
  actually reaches `CLOSED` and is captured). The shared ahead-only fixture remains used by
  `test_execution.py` (06-03) for its narrower N+1-open look-ahead proof — untouched.
- **Files modified:** tests/backtester/test_harness.py
- **Commit:** 19a33fc

**Commit note:** both tasks were implemented, debugged, and verified as a single cohesive unit
(`BacktestHarness` is one class where Task 2's fixes — e.g. `opened_at`/`updated_at`
normalization — touch the SAME construction code Task 1 wrote), so they landed in a single
commit (`19a33fc`) rather than two separate ones. Splitting the working, tested diff after the
fact would have required artificially breaking passing tests to fabricate an intermediate
state; the commit message documents both tasks' content explicitly.

## Known Stubs

None. `BacktestHarness` is fully wired against the real reused pipeline and the real
`SimulatedBarFeed`/`SimulatedExecution`/`SimulatedGateway`; no placeholder/mock data paths
remain in `backtester/harness.py`.

## Threat Flags

None — the threat register's three `mitigate` entries (T-06-09 scratch-DB-path, T-06-10
gateway=None, T-06-11 look-ahead clock) are exactly the mitigations this plan implements, not
new unmitigated surface. No new network endpoints, auth paths, or schema changes were
introduced.

## Self-Check: PASSED

- FOUND: backtester/harness.py
- FOUND: tests/backtester/test_harness.py (rewritten with 6 real tests, all passing)
- FOUND commit 19a33fc (Task 1 + Task 2: BacktestHarness construction, setup_day, replay
  loop, clock control, trade-log capture)
