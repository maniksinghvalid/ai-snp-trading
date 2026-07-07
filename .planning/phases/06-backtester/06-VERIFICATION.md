---
phase: 06-backtester
verified: 2026-07-07T08:30:00Z
status: gaps_found
score: 1/4 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 1/4
  gaps_closed:
    - "Entries occur at bar N+1 open with point-in-time gap/SMA200/premarket-high/RVOL (CR-01old cross-day premarket-high clobber, CR-04 session-boundary next_bar crossing, CR-07 full-day-lookahead synthetic TodayPrice all confirmed closed by direct code read + passing multi-day regression test)"
    - "Performance report captures every position at run end (CR-05 EOD/end-of-run force-close wired into replay_day; WR-01 phantom exit_price=None fill removed from manage_exit) — confirmed by direct code read"
    - "yfinance window guard now agrees with the actual fetch at 60 calendar days, and per-day coverage is enforced loudly by day name (CR-02/CR-03 fixed) — confirmed by direct code read"
    - "Watchlist cap/gate now enforced during replay (CR-06 — an unfiltered --symbols code that fails the daily filter can no longer enter) — confirmed by direct code read"
  gaps_remaining:
    - "NEW (not in prior verification scope, found by 2026-07-07 code review and independently reproduced here): _materialize_bars and _load_premarket crash with ValueError on NaN rows, which is the NORMAL shape of a real multi-ticker yf.download(group_by='ticker') result (union-index padding for symbols with staggered/missing timestamps) — any realistic multi-symbol backtest (the tool's explicit BT-04 500-symbol-scale use case) crashes or silently poisons premarket highs to NaN"
    - "NEW: the daily -2R circuit breaker (Gate 7) can never trip during a backtest because nothing writes the `trades` DB table during a replay (`_capture_closed_trades` only appends to an in-memory `trade_log` list) — `get_daily_trade_stats` always reads realized_pnl=0.0, so a live risk rule that is part of the SAME reused StrategyCore/SignalEngine FSM is silently absent from every backtest result, contradicting BT-01's 'exact same ... FSM' claim and the harness's own docstring assertion that Gate 7 'can still block entries'"
  regressions: []
gaps:
  - truth: "The feed correctly loads and handles real yfinance multi-ticker 5m data without crashing or silently corrupting point-in-time values — a prerequisite for BT-02's point-in-time correctness and BT-04's 'handles yfinance's data' claim at realistic (multi-symbol) scale"
    status: failed
    reason: "Confirmed by independent reproduction (not just review prose): a DataFrame with NaN rows (the union-index padding pattern a real multi-ticker yf.download(group_by='ticker') call produces whenever symbols have staggered/missing 5m timestamps — halts, illiquid names, partial premarket coverage) raises `ValueError: cannot convert float NaN to integer` inside `_materialize_bars`'s `cum_volume += int(row[\"volume\"])`. I built this exact reproduction independently: a 3-row frame with one all-NaN row, fed directly through `SimulatedBarFeed._materialize_bars`, crashes with that exact error. `_load_premarket` has the identical unguarded `float(row[\"high\"])` pattern one call away — even where it doesn't crash, it can silently carry a NaN into `premarket_highs()`'s `max(...)`, making Gate 1's `close > premarket_high` evaluate `x > NaN -> False` for every bar, i.e. the symbol silently never trades (indistinguishable from a quiet day — exactly the class of silent failure the Plan 07 coverage guard (CR-03) was built to eliminate, but this path evades that guard because the day DOES have bars, just NaN ones)."
    artifacts:
      - path: "backtester/feed.py"
        issue: "_materialize_bars (lines 204-240) and _load_premarket (lines 242-302) both iterate the raw yfinance-sourced frame without a dropna step; neither the 40-test suite nor the multi-day regression test (tests/backtester/test_harness.py::test_multiday_replay_proves_cr01_cr04_cr05_cr06) exercises a union-index NaN-padded frame, since the mocks all return clean per-symbol frames"
    missing:
      - "frame.dropna(subset=['open','high','low','close','volume']) in _materialize_bars before the per-row loop, and an equivalent dropna in _load_premarket before its per-row loop (both on the CSV-cache-hit path too, since cached files preserve NaN rows written by an unguarded earlier fetch)"
      - "A regression test that mocks yfinance.download to return a realistic union-index multi-ticker frame (one symbol's rows all-NaN at timestamps the other symbol has data for) and asserts the feed loads without raising and without producing a NaN premarket high"
  - truth: "Running a backtest exercises the SAME risk-enforcement behavior as the live bot for the SAME reused StrategyCore/SignalEngine FSM (BT-01's 'exact same ... FSM' claim), including the daily -2R circuit breaker (Gate 7) that live entries are subject to"
    status: failed
    reason: "Confirmed by direct code trace: bot/signal/signal_engine.py's `_is_circuit_breaker_tripped` (lines 402-440) computes `realized = stats.get('realized_pnl', 0.0)` from `self._store.get_daily_trade_stats(session_date_str)`, which is a `SELECT ... FROM trades WHERE DATE(closed_at) = ?` query (bot/state/store.py lines 367-407). I grepped the entire `bot/` and `backtester/` trees for `INTO trades` and found only backtester/report.py's own docstring comment stating the fact — no code anywhere ever INSERTs into the `trades` table during a backtest run; `backtester/harness.py::_capture_closed_trades` (lines 367-407) only appends dicts to the in-memory `self.trade_log` Python list, never to the DB. Therefore `get_daily_trade_stats` always returns `realized_pnl=0.0` for any backtest session date, `_is_circuit_breaker_tripped` can never observe a losing day, and Gate 7 never blocks a single entry in any backtest run, no matter how badly a replayed day performs. `backtester/harness.py`'s own module docstring (lines 41-43) asserts 'SignalEngine's own Gate 7 circuit-breaker check still runs and can still block entries' — this claim is false in effect, confirmed by the same trace the 2026-07-07 code review used (CR-02 in 06-REVIEW.md)."
    artifacts:
      - path: "backtester/harness.py"
        issue: "_capture_closed_trades (lines 367-407) never inserts into the trades table; module docstring (lines 41-43) makes a false claim about Gate 7 remaining active"
      - path: "bot/signal/signal_engine.py"
        issue: "_is_circuit_breaker_tripped (lines 402-440) depends entirely on store.get_daily_trade_stats reading the trades table, which no backtest code path ever populates"
    missing:
      - "_capture_closed_trades inserting each captured trade into the scratch StateStore's trades table (matching the schema in bot/state/migrations.py) so Gate 7's realized-P&L query reflects the backtest's own closed trades"
      - "A regression test: a replay day engineered to lose more than the configured daily_circuit_breaker_r must produce zero new entries for the remainder of that day"
      - "Alternatively, if simulating Gate 7 is explicitly out of scope, the harness docstring's false claim must be corrected and summary.json must loudly flag that the -2R breaker was not simulated, rather than silently reporting results as if it were live-equivalent"
human_verification: []
---

# Phase 6: Backtester Verification Report

**Phase Goal:** An offline CLI tool replays historical 5m data through the exact same StrategyCore and PositionState FSM used by the live bot and produces a performance report, confirming live/backtest code parity.
**Verified:** 2026-07-07T08:30:00Z
**Status:** gaps_found
**Re-verification:** Yes — after gap-closure plans 06-07, 06-08, 06-09

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Running `backtester/run.py` imports `bot/strategy/`/`bot/position/` unchanged and reads the same `rules.json` | ✓ VERIFIED (regression, unchanged since prior verification) | `backtester/harness.py` still imports `TrendJoinLong`, `PositionManager`, `PositionPhase`/`PositionState` directly from `bot.strategy`/`bot.position`; `backtester/run.py` still calls `bot.config.loader.load_strategy_config` |
| 2 | Bar N+1-open entries; gap/SMA200/premarket-high/RVOL computed point-in-time, no look-ahead, across a multi-day replay | ✓ VERIFIED (previously-blocking defects closed) | CR-01(old, premarket-high clobber): `harness.py` now stores per-day highs in `_premarket_highs_by_day` (`setup_day`, line 197) and applies only the current day's highs as the first statement of `replay_day` (line 214) — confirmed by direct read, not the prior global-apply. CR-04 (session-boundary fill): `feed.py::next_bar` (lines 319-332) now returns `None` when the next chronological bar's date differs from `after`'s date — confirmed by direct read. CR-07 (full-day lookahead TodayPrice): `feed.py::synthetic_today_price` (lines 352-374) now builds `TodayPrice` exclusively from `self._premarket_bars_by_code`, never RTH bars — confirmed by direct read. All three closures are additionally proven by the real (only-yfinance-mocked) `test_multiday_replay_proves_cr01_cr04_cr05_cr06` (tests/backtester/test_harness.py:447-515), which passes. **However**, see the NEW gap below: NaN rows from a realistic multi-ticker yfinance download poison this same point-in-time premarket-high path in a way the coverage guard does not catch (see Required Artifacts / Gaps) |
| 3 | Performance report written to disk: win rate, avg R, max drawdown, profit factor, per-trade CSV — capturing EVERY position, including ones open at range end | ✓ VERIFIED (previously-blocking defect closed) | CR-05 (missing EOD/end-of-run force-close): `replay_day` (harness.py lines 205-237) now pins the replay clock to the day's `get_force_close_time_et` moment, toggles `sim_execution._force_close`, awaits the unmodified live `position_manager.force_close_all`, and calls `_capture_closed_trades()` afterward. WR-01 (phantom `exit_price=None` fill): `execution.py::manage_exit`'s normal branch (lines 119-132) now returns `0` with no fill appended when `next_bar` is `None`, instead of fabricating a full fill. Proven by the multi-day regression test's assertion `all(p.phase == PositionPhase.CLOSED ...)` plus a `force_close`-reason trade-log row with a real (non-None) exit price. **However**, this correctness now depends on report inputs that are (a) incomplete for realistic multi-symbol data (NaN crash, see gap 1) and (b) missing the -2R circuit-breaker's effect on entry counts (see gap 2), so a report produced by a real multi-symbol multi-day run cannot be trusted to `confirm live/backtest code parity` on either front |
| 4 | 5m data from yfinance (not Moomoo); loader handles yfinance's ~60-day 5m window + ticker normalization, at realistic (multi-symbol/500-symbol-scale per BT-04) usage | ✗ FAILED (new BLOCKER found post-fix) | CR-02/CR-03 (window mismatch, silent empty-day replay) genuinely closed: `INTRADAY_5M_WINDOW_CALENDAR_DAYS = 60` now matches the real `_download_batch(..., download_kwargs={"period": "60d", ...})` fetch (feed.py lines 62, 144-152), and `_enforce_coverage` (lines 180-202) raises `BacktestWindowError` naming any NYSE trading day with zero bars. **But** a NEW, independently-reproduced defect: `_materialize_bars` (feed.py lines 204-240) and `_load_premarket` (lines 242-302) do not drop NaN rows before converting to `int()`/`float()` — the union-index padding pattern that is the NORMAL shape of a real multi-ticker `yf.download(group_by="ticker")` response (any symbol with a staggered/missing 5m timestamp relative to its peers). I reproduced this directly: a 3-row frame with one all-NaN row, run through `SimulatedBarFeed._materialize_bars`, raises `ValueError: cannot convert float NaN to integer`. This crashes (or, in `_load_premarket`'s case, silently NaN-poisons the premarket-high gate) any realistic multi-symbol backtest — exactly the scale (BT-04: "avoiding broker historical-quota limits at 500-symbol scale") the requirement is written for |

**Score:** Using the 4-truth structure from the prior verification: Truth 1 remains VERIFIED, Truths 2 and 3 are now VERIFIED for their originally-scoped defects but are undermined by two newly-discovered BLOCKER-class defects that fall most directly under Truth 4 (data-loading correctness) and a cross-cutting FSM-parity concern (Gate 7). Net: **1 gap-free truth (1), 2 truths verified for their original scope but with newly-surfaced dependent defects (2, 3), 1 truth failed by a new BLOCKER (4)**. Reported score: 1/4 fully clean; 2 new BLOCKER gaps recorded below (NaN crash, Gate 7 never-trips) that must close before the phase goal — "confirming live/backtest code parity" on realistic data — is genuinely achieved.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backtester/feed.py` | SimulatedBarFeed 5m replay + CSV cache, 60-day window guard, per-day coverage, same-session next_bar, PIT premarket accessors | ⚠️ HOLLOW (NaN path unguarded) | All 7 previously-confirmed defects (CR-01old/02/03/04/07) verified closed by direct read. New: `_materialize_bars`/`_load_premarket` have no `dropna` — crash/poison on realistic multi-ticker NaN-padded frames (independently reproduced) |
| `backtester/execution.py` | SimulatedExecution N+1-open fills + force-close fill mode, no phantom exits | ✓ VERIFIED | `_force_close` branch (lines 105-117) fills at last observed bar close and returns full qty; normal branch (lines 119-132) returns 0 with no fill when `next_bar is None` (WR-01 closed) — both confirmed by direct read |
| `backtester/report.py` | compute_metrics + CSV/JSON writers | ✓ VERIFIED (unchanged, formulas correct) | Module docstring candidly documents that `trades` table is never written and metrics are computed purely from the harness-supplied `trade_log` — an honest design note, not a hidden defect in this file itself |
| `backtester/harness.py` | BacktestHarness: per-day premarket freeze, watchlist gate, EOD/end-of-run force-close, manager clock rebind | ✓ VERIFIED for CR-01/05/06 scope; ⚠️ FSM-parity gap remains | `_premarket_highs_by_day`/`_watchlist_by_day` per-day state, `replay_day`'s force-close block, and dual `now_et` rebind (signal_engine + position.manager) all confirmed by direct read and the multi-day regression test. **But** module docstring's claim that "SignalEngine's own Gate 7 circuit-breaker check still runs and can still block entries" is false in effect — nothing ever writes the `trades` table `_is_circuit_breaker_tripped` reads from, so Gate 7 can never trip in any backtest |
| `backtester/run.py` | CLI entry point | ✓ VERIFIED (unchanged) | argparse, config load + ConfigError→exit(1), live-DB collision guard, V5 input validation, feed→harness→write_report wiring all present |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `backtester/harness.py::replay_day` | `signal_engine.set_premarket_highs` | per-day frozen highs applied first | WIRED | Confirmed `harness.py:214`, `_premarket_highs_by_day.get(day, {})` |
| `backtester/harness.py::replay_day` | `position_manager.force_close_all` | clock-pinned EOD/end-of-run force-close | WIRED | Confirmed `harness.py:224-237` |
| `backtester/harness.py::_process_bar` | `self._watchlist_by_day` | entry branch gated on daily-filter watchlist membership | WIRED | Confirmed `harness.py:310-317`; position management (`position_manager.on_bar`) remains unconditional |
| `bot/signal/signal_engine.py::_is_circuit_breaker_tripped` | `store.get_daily_trade_stats` → `trades` table | daily -2R risk-rule enforcement, reused unmodified from live | **NOT_WIRED (in practice)** | Query target (`trades` table) is never populated by any backtest code path — `grep -rn "INTO trades" bot/ backtester/` matches only a docstring comment. Confirmed by trace: this FSM gate is present in the reused code but structurally starved of the data it needs to ever fire during a replay |
| `backtester/feed.py::_materialize_bars` | raw yfinance multi-ticker frame | union-index NaN-row handling | **NOT_WIRED** | No `dropna` exists on this path; independently reproduced crash (`ValueError: cannot convert float NaN to integer`) with a synthetic NaN-padded frame |

### Anti-Patterns Found

No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers in `backtester/*.py` or `tests/backtester/*.py`. The two BLOCKER-class findings above are logic/data-integrity bugs (missing `dropna`, a risk gate structurally starved of data), not stub markers. The fresh code review (`06-REVIEW.md`, commit `06d8b5b`) additionally lists 7 Warning and 6 Info findings not treated as phase-blocking here (wrong-sign exit slippage execution.py:111,127; TOD-baseline silent methodology fallback past ~30 days; silent per-symbol load failures; DST cache round-trip crash; `--start`>`--end` silently reports 0 trades; a 2026-08-01 test-suite time bomb from hardcoded fixture dates; harness swallowing bar-construction errors silently vs. live's logged version) — these are real but do not block the core phase goal the same way the two Criticals do; they are noted here for the record and should be tracked, not re-litigated as gaps in this report.

| File | Finding | Severity | Confirmed here |
|------|---------|----------|-----------------|
| `backtester/feed.py:204-240,242-302` | NaN rows from real multi-ticker yfinance downloads crash `_materialize_bars` / poison `premarket_highs` | BLOCKER | Yes — independently reproduced (not just read) with a synthetic NaN-padded frame |
| `backtester/harness.py:367-407` + `bot/signal/signal_engine.py:402-440` + `bot/state/store.py:367-407` | -2R circuit breaker (Gate 7) can never trip — nothing writes the `trades` table during a backtest | BLOCKER | Yes — traced the full call chain and grepped for `INTO trades` across `bot/`+`backtester/`; only a docstring comment matches |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|-----------------|--------------|--------|----------|
| BT-01 | 06-01, 06-05, 06-06, 06-08, 06-09 | Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot | ⚠️ PARTIAL | Multi-day replay mechanics (per-day premarket freeze, watchlist gate, force-close) now correctly ported and tested; but the reused FSM's Gate 7 (-2R circuit breaker) is structurally inert in a backtest because nothing writes the `trades` table it reads from — "exact same FSM" does not hold for this gate's actual behavior |
| BT-02 | 06-01, 06-03, 06-05, 06-06, 06-07, 06-09 | Enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time | ✓ SATISFIED for the previously-listed defects (CR-01old/04/07 closed and regression-tested); ⚠️ new risk surfaced by the NaN poisoning of the same premarket-high path for realistic multi-ticker data |
| BT-03 | 06-01, 06-04, 06-06, 06-08, 06-09 | Produces a performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV) | ⚠️ PARTIAL | Trade-capture completeness (CR-05) now correct and tested; but report inputs are compromised by the circuit-breaker gap (over-counts entries a live run would have blocked) and by the NaN crash (report may never be produced at all for realistic multi-symbol data) |
| BT-04 | 06-01, 06-02, 06-06, 06-07 | Backtest historical data sourced from yfinance, not Moomoo, avoiding broker historical-quota limits at 500-symbol scale | ✗ BLOCKED | Window/coverage mismatch (CR-02/CR-03) genuinely fixed; but the loader crashes on the NORMAL shape of a real multi-ticker yfinance response (NaN union-index padding), which is precisely what "500-symbol scale" implies will occur routinely |

No orphaned requirements — all four IDs (BT-01..04) are claimed across the phase's nine plans (06-01 through 06-09); `REQUIREMENTS.md` marks all four `[x]` complete, predating both the original 7-BLOCKER gap-closure cycle and this post-fix code review's 2 new Critical findings.

### Behavioral Spot-Checks

`python3 -m pytest tests/backtester/ -q` → **40 passed in 0.74s** (up from 28 pre-gap-closure). Independently re-ran, not just trusted from SUMMARY.md. All 40 tests use clean, non-NaN mock frames — confirmed by inspecting the mock fixtures (`_mock_yf_download`, `_mock_yf_download_multiday`) — so none of them exercise the union-index NaN-padding shape a real multi-ticker `yf.download` call produces. A passing green suite is not evidence against the NaN-crash gap; it is evidence the suite's mocks are cleaner than reality. I independently reproduced the crash outside the test suite (see Gaps) rather than relying on the code review's prose claim alone.

### Probe Execution

SKIPPED — no `scripts/*/tests/probe-*.sh` convention exists in this project and none is referenced in the phase's PLAN/SUMMARY files.

### Human Verification Required

None. Both new BLOCKER findings were confirmed by direct source-code trace plus, for the NaN issue, an independent runtime reproduction (not by trusting SUMMARY.md or 06-REVIEW.md prose) — deterministic code-path defects requiring no visual/timing/runtime judgment to verify.

### Gaps Summary

The 06-07/06-08/06-09 gap-closure plans genuinely closed all 7 previously-confirmed multi-day BLOCKER defects (CR-01old, CR-02, CR-03, CR-04, CR-05, CR-06, CR-07, WR-01) — I independently re-traced each fix through the actual source and confirmed the closures are real, not just claimed: per-day premarket-high freeze, capped/ranked watchlist entry gate, EOD/end-of-run force-close with a real force-close exit price, session-bounded `next_bar`, a matching 60-calendar-day window/fetch pair, a loud per-day coverage guard, and the removal of the phantom `exit_price=None` fill. A real 2-trading-day regression test (`test_multiday_replay_proves_cr01_cr04_cr05_cr06`) proves these behaviors through the full harness, not just the feed or execution layer in isolation, and the 40-test suite passes.

However, the phase goal is "confirming live/backtest code parity," and a fresh, independent code review (06-REVIEW.md, commit 06d8b5b) found two NEW Critical defects that I independently confirmed (one by direct code trace, one by runtime reproduction) rather than trusting the review's prose:

1. **NaN-row crash/poisoning** (`backtester/feed.py`): the NORMAL shape of a real multi-ticker `yf.download(group_by="ticker")` response — union-index padding with all-NaN rows for symbols lacking a bar at a given timestamp (halts, illiquid names, staggered premarket coverage) — crashes `_materialize_bars` via `int(NaN)`, or silently poisons `premarket_highs()`'s `max(...)` to NaN, silently blocking Gate 1 for that symbol for the rest of the day. I reproduced this directly with a synthetic 3-row NaN-padded frame fed through the actual `_materialize_bars` method. This is exactly the multi-symbol ("500-symbol scale," BT-04's own wording) use case the tool is meant for.

2. **Gate 7 circuit breaker structurally inert** (`backtester/harness.py` + `bot/signal/signal_engine.py` + `bot/state/store.py`): the harness's own module docstring claims "SignalEngine's own Gate 7 circuit-breaker check still runs and can still block entries" — this is false in effect. `_is_circuit_breaker_tripped` reads `store.get_daily_trade_stats`, which queries the `trades` DB table; nothing in `bot/` or `backtester/` ever inserts a row into that table during a backtest (`_capture_closed_trades` only appends to an in-memory Python list). Realized P&L is therefore always `0.0` and the -2R breaker never trips, no matter how badly a replayed day performs — a live risk rule silently absent from every backtest result, using the SAME reused StrategyCore/SignalEngine code the phase goal claims parity with.

Both are BLOCKER-class: the first prevents the tool from running at all (or produces silently-wrong results) on the realistic multi-symbol data the tool exists to process; the second means the "exact same ... FSM" parity claim central to the phase goal does not hold for one of that FSM's own risk gates. Neither is addressed by any later milestone phase — Phase 7 consumes the Phase 6 backtester's output for a decision gate, it does not fix the backtester itself.

---

_Verified: 2026-07-07T08:30:00Z_
_Verifier: Claude (gsd-verifier)_
