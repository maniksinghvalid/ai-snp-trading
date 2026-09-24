---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 05
subsystem: options-bot-service
tags: [apscheduler, options, bull-call-spread, multi-strategy, service-composition]

# Dependency graph
requires:
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 01
    provides: OptionsConfig (name, universe_source, long_delta, max_debit_to_width, profit_target_pct_of_max, equity_state_db), OptionsBook, load_options_book
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 02
    provides: "pick_strikes('bull_call_spread', cfg), size_debit_position(debit, cfg, open_max_loss_total)"
  - phase: 11-multi-strategy-options-bot-bull-call-spread
    plan: 03
    provides: "read_equity_watchlist(db_path, scan_date_iso), OptionsStore.count_opened_on(date_iso, strategy_name=None), option_positions.strategy_name column"
provides:
  - "OptionsBot(cfg, gateway, store, kill_switch, alerter, watchdog=None, strategies=None) — self._strategies dict (name -> OptionsConfig), self._cfg stays the primary/shared config"
  - "_job_entry_scan(strategy_name=None) / _scan_and_open(cfg, today, opened_today) / _try_open(cfg, code, u_rows, today, open_max_loss_total) — cfg threaded as a parameter, not read from self._cfg"
  - "equity_watchlist universe wiring: _scan_and_open calls read_equity_watchlist for universe_source strategies, fails closed (zero entries) on an empty/missing watchlist"
  - "bull_call_spread entry path: calls-only screen (delta breadth 0.05-0.50), IV gate skipped, size_debit_position sizing, signed credit_per_spread=-debit, max_loss_usd=debit*100*qty, strategy_name=cfg.name on every new position"
  - "_register_jobs per-strategy entry-scan jobs (options_entry_scan_<name>[_2]), ONE options_manage and ONE options_eod job (D-20)"
  - "per-strategy entries/day (count_opened_on strategy_name filter) and concurrent-position caps (Python-side filter over active positions); daily-loss breaker, BP headroom and one-position-per-underlying stay global (D-22)"
  - "_premium_label(credit_per_spread) -> 'credit X'/'debit X'; _fmt_entry shows the strategy name and the signed premium label, HTML-escaped (D-24 entry half)"
affects: [11-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "cfg threaded as an explicit parameter through _scan_and_open/_try_open instead of read from self._cfg, so the same entry-scan body serves every strategy in the book"
    - "Per-strategy counters as a Python-side filter over an already-fetched position list (no new store query) — global counters (busy, open_max_loss_total) computed once, per-strategy counters (open_count) filtered from the same list by strategy_name"
    - "Shared code -> stock_id cache (self._stock_ids) across every strategy in the process; only the codes missing from the cache are re-fetched from the gateway"

key-files:
  created: []
  modified:
    - bot/options/service.py
    - tests/options/test_service.py

key-decisions:
  - "Fixed a latent bug surfaced by the debit path: the options_position_opened audit event read sel['credit'] unconditionally, which would KeyError on a debit sel dict (no 'credit' key, only 'debit'); switched to pos['credit_per_spread'] (already the signed value for both structures) — Rule 1 auto-fix, in-scope (same line this plan's debit branch touches)"
  - "The 'if not codes: skip empty_universe' check applies after either universe branch (fixed list or watchlist read), not just the watchlist path — harmless for the credit book (its universe is never empty) and keeps one code path instead of two"
  - "_job_entry_scan's own per-day cap check (opened_today >= cfg.max_new_positions_per_day) now reads a per-strategy count via count_opened_on(..., strategy_name=cfg.name); the daily-loss breaker check in the same function stays global (reads/writes one shared meta key), per D-22"
  - "main() untouched in this plan by design (still load_options_config, single strategy) — the debit manage path (D-21) is plan 11-06's job, so no bull-call position can go live before it exists"

requirements-completed: [MSO-08, MSO-06, MSO-04]

# Metrics
duration: 25min
completed: 2026-09-24
---

# Phase 11 Plan 05: Multi-strategy entry-scan wiring Summary

**The options process now runs a per-strategy entry scan: `super_bull_call` reads the equity premarket watchlist, screens calls only, opens a signed/sized debit spread tagged `strategy_name`, and gets its own `options_entry_scan_<name>` cron job with per-strategy entries/day and concurrent-position caps — while `tasty_credit_spreads` behavior (screens, order, sizing) is provably unchanged and the global daily-loss breaker/BP-headroom/one-per-underlying rules bind both strategies.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2/2 completed
- **Files modified:** 2 (bot/options/service.py, tests/options/test_service.py)

## Accomplishments
- `OptionsBot.__init__` accepts an optional `strategies` iterable (`self._strategies` dict keyed by name); `self._cfg` stays the primary strategy for the shared execution/service/risk values (LegExecutor, watchdog, breaker, EOD).
- `_job_entry_scan(strategy_name=None)`, `_scan_and_open(cfg, ...)`, `_try_open(cfg, ...)` all take `cfg` as an explicit parameter instead of reading `self._cfg`, so one code path serves every strategy in the book.
- `_scan_and_open` resolves the universe per strategy: a fixed list for `universe is not None`, or `read_equity_watchlist(cfg.equity_state_db, today.isoformat())` for `universe_source == "equity_watchlist"` — an empty result fails closed to zero entries that day, logged via `options_watchlist_loaded`/`options_entry_scan_skipped(reason="empty_universe")`.
- `_try_open` branches on `cfg.structure_type == "bull_call_spread"`: skips the IV gate, sizes with `size_debit_position`, stores `credit_per_spread = -debit` and `max_loss_usd = debit * 100 * qty`, and tags every new position `strategy_name = cfg.name`.
- `_register_jobs` (D-20) registers one entry-scan cron per strategy (`options_entry_scan_<name>`, `_2` when `second_entry_scan_et` is set) while `options_manage`/`options_eod` stay singular.
- D-22: `opened_today` (entries/day) and `open_count` (concurrent positions) are filtered by `strategy_name`; `busy` (one-per-underlying) and `open_max_loss_total` (BP headroom) stay computed over every active position regardless of strategy.
- `_premium_label`/`_fmt_entry` show the strategy name and a signed "credit X"/"debit X" label in the Telegram entry alert, every field HTML-escaped.
- 12 new tests added across both tasks; zero existing test assertions were removed (only the two D-20 job-id literals were updated, as expected).

## Task Commits

1. **Task 1: Strategy-aware entry path — strategies param, watchlist universe, call-only screen, debit `_try_open`, entry alert (D-05, D-14, D-17, D-19, D-23, D-24, D-28)** - `ec76ae7` (feat)
2. **Task 2: Per-strategy job registration + per-strategy caps vs global breaker/BP/one-per-underlying (D-20, D-22)** - `33af4a8` (feat)

_Both tasks used `tdd="true"` in the plan; tests and implementation landed together in each task's commit, verified green (quick + full suite) before each commit._

## Files Created/Modified
- `bot/options/service.py` - `size_debit_position`/`read_equity_watchlist` imports; `OptionsBot.__init__(strategies=None)` + `self._strategies`; `_job_entry_scan(strategy_name=None)`; `_scan_and_open(cfg, today, opened_today)` (universe resolution, shared stock-id cache, calls-only bull screen, per-strategy vs global candidate ordering and caps); `_try_open(cfg, code, u_rows, today, open_max_loss_total)` (debit branch); `_premium_label`; `_fmt_entry` (strategy name + signed premium label); `_register_jobs` (per-strategy loop, D-20); module docstring updated
- `tests/options/test_service.py` - `make_bot` fixture gains `strategies=None`; `_bull_chain`/`_wire_bull` helpers (NVDA worked-example call grid); `test_bull_call_entry_opens_debit_spread_from_watchlist`, `test_bull_call_entry_empty_watchlist_opens_nothing`, `test_bull_call_entry_iterates_watchlist_in_rank_order`, `test_credit_entry_unchanged_with_two_strategy_book` (Task 1); `test_register_jobs_one_entry_scan_per_strategy` + 2 updated job-id literals + 7 D-22 tests (`test_bull_call_per_day_cap_is_per_strategy`, `test_credit_per_day_cap_ignores_other_strategies`, `test_bull_call_concurrent_cap_is_per_strategy`, `test_credit_concurrent_cap_ignores_other_strategies`, `test_one_position_per_underlying_is_global`, `test_bp_headroom_is_global`, `test_daily_breaker_blocks_every_strategy`) (Task 2)

## Decisions Made
- Fixed a latent bug in the `options_position_opened` audit call: it read `sel["credit"]` unconditionally, which only exists on a credit-structure `sel` dict — a debit `sel` (from `_pick_bull_call`) has `sel["debit"]` instead and would have raised `KeyError` the first time a bull-call position filled. Switched to `pos["credit_per_spread"]`, which already holds the correct signed value for both structures (Rule 1 auto-fix — this line is inside the exact branch this plan's task modifies, not an unrelated file).
- The `if not codes: skip("empty_universe")` guard runs after either universe-resolution branch (matches the plan's literal action text), not only the watchlist path — a no-op for the credit book today (its fixed universe is never empty) but keeps one code path instead of a duplicated check per branch.
- `main()` was left untouched (still `load_options_config`, one strategy) exactly as the plan's invariant requires — `_manage_position`'s dispatch on `strategy_name` (D-21) is plan 11-06's job, so a bull-call position can be opened by this plan's code but has no live manage path yet; that is expected and by design (see Next Phase Readiness).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `options_position_opened` audit read `sel["credit"]` unconditionally**
- **Found during:** Task 1 (debit `_try_open` branch)
- **Issue:** The pre-existing line always read `sel["credit"]` for the audit event; a debit `sel` dict (returned by `_pick_bull_call`) has no `"credit"` key, only `"debit"` — this would `KeyError` on the first successful bull-call open.
- **Fix:** Read `pos["credit_per_spread"]` instead, which is set to the correct signed value (`credit` for credit structures, `-debit` for debit structures) earlier in the same function.
- **Files modified:** `bot/options/service.py`
- **Verification:** `test_bull_call_entry_opens_debit_spread_from_watchlist` exercises this exact code path and passes; full suite green.
- **Committed in:** `ec76ae7` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary for correctness — the debit path this plan adds would otherwise crash on its first successful entry. No scope creep (same function, same task's edit).

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Per STATE.md/CLAUDE.md convention, the live options bot process (if running) must be restarted after this change lands on `develop`; `main()` still loads only the first strategy (`load_options_config`) so this plan alone introduces no live behavior change for the operator's running process.

## Next Phase Readiness

- Every entry-side piece MSO-08/MSO-06/MSO-04 needs is now wired: per-strategy jobs, per-strategy caps, global breaker/BP/one-per-underlying, watchlist universe, debit sizing and signed storage, strategy-tagged positions and alerts.
- `_manage_position` still calls `manage_decision`/`self._cfg` unconditionally (D-21 dispatch on `pos["strategy_name"]` to the matching config + `manage_decision`/`manage_decision_debit`) — plan 11-06's job. Until that lands, `main()` deliberately keeps loading a single strategy via `load_options_config` so no bull-call position can open in production without a manage path to close it.
- `OptionsBook`/`load_options_book` (11-01), `_pick_bull_call`/`size_debit_position`/`manage_decision_debit` (11-02), `read_equity_watchlist`/migration 0007 (11-03), and this plan's service wiring are now mutually consistent — plan 11-06 can switch `main()` to `load_options_book` + add the manage-side dispatch and D-29 orphan-strategy reconcile guard.
- Full suite green: 1253 passed, 1 skipped (baseline 1241 passed / 1 skipped + 12 new tests, 0 new failures, 0 new skips). Quick suite (`tests/options tests/backtester/options`): 377 passed.
- Safety: no OpenD connection, no live bot launch, and no Massive/network call were made in this plan's verification — every test runs against the fake gateway/store/alerter test doubles.

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED
