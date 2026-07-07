---
phase: 06
slug: backtester
status: verified
nyquist_compliant: true
wave_0_complete: true
created: 2026-07-06
updated: 2026-07-07
---

# Phase 06 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (632 tests green, 1 skipped) |
| **Config file** | existing pytest configuration at repo root |
| **Quick run command** | `python3 -m pytest tests/backtester/ -q` (43 tests) |
| **Full suite command** | `python3 -m pytest -q` |
| **Estimated runtime** | ~37 seconds (full suite, measured 2026-07-07) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/backtester/ -q`
- **After every plan wave:** Run `python3 -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Requirement Verification Map

Filled retroactively 2026-07-07 after phase completion (12/12 plans, verification passed 4/4).
Mapped at requirement level; all 43 `tests/backtester/` tests green plus repo-wide regression
suite (632 passed, 1 skipped).

| Requirement | Secure Behavior | Covering Tests | Test Type | Automated Command | Status |
|-------------|-----------------|----------------|-----------|-------------------|--------|
| BT-01 | Harness replays through the unmodified live StrategyCore/FSM (gateway=None, SimulatedGateway wired, same rules.json), including risk gates | `test_harness.py`: `gateway_none`, `wires_simulated_gateway`, `store_uses_scratch_db_path`, `full_replay_produces_a_closed_trade`, `multiday_replay_proves_cr01_cr04_cr05_cr06`, `gate7_circuit_breaker_trips_from_backtest_recorded_trades`; `test_run.py`: `never_constructs_a_broker_gateway`, `config_error_exits_1`, `end_to_end_run` | integration | `python3 -m pytest tests/backtester/test_harness.py tests/backtester/test_run.py -q` | ✅ green |
| BT-02 | Entries fill at bar N+1 open; gap/SMA200/premarket-high/RVOL computed point-in-time; no look-ahead (synthetic ahead-only proof) | `test_execution.py`: `fills_at_bar_n_plus_1_open_not_intent_entry_price`, `returns_none_when_signal_is_on_the_last_bar`, `manage_exit_fills_at_next_bar_open`, `exit_slippage_is_adverse`; `test_feed.py`: `premarket_highs_excludes_bars_at_or_after_0930`, `synthetic_today_price_is_premarket_only`, `next_bar_never_crosses_a_session_boundary`; `test_harness.py`: `replay_clock_drives_entry_window_gate`, `setup_day_reuses_scanner_point_in_time_functions` | unit + integration | `python3 -m pytest tests/backtester/test_execution.py tests/backtester/test_feed.py -q` | ✅ green |
| BT-03 | Report written to disk with win rate, avg R, max drawdown, profit factor + per-trade CSV (entry/exit price, qty, exit reason) | `test_report.py`: `compute_metrics_matches_fixture_documented_expected_values`, `empty_trade_list_returns_zeroed_dict`, `write_report_writes_csv_with_contract_columns_and_summary_json`; `test_run.py`: `end_to_end_run_writes_summary_and_nonempty_trades_csv` | unit + E2E | `python3 -m pytest tests/backtester/test_report.py -q` | ✅ green |
| BT-04 | 5m data from yfinance/CSV (not Moomoo); 60-day window guard matches fetch; ticker normalization; NaN union-index hygiene | `test_feed.py`: `window_guard_and_fetch_agree_at_60_calendar_days`, `coverage_guard_names_the_uncovered_trading_day`, `out_of_window_start_raises_backtest_window_error`, `second_load_reads_csv_cache_zero_network_calls`, `moomoo_code_normalization_via_yfinance_to_moomoo`, `union_index_nan_rows_do_not_crash_or_poison_premarket_high`, `traversal_symbol_rejected` | unit | `python3 -m pytest tests/backtester/test_feed.py -q` | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/backtester/` — test package for feed, execution, harness, report (43 tests; plus `test_run.py` added in 06-06)
- [x] Synthetic 5m dataset fixture with a known ahead-only signal (look-ahead proof, success criterion 2) — `tests/backtester/fixtures.py`, dates anchored to `recent_session_days()` (no time bomb, plan 06-12)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live yfinance 5m fetch within rolling ~60-day window | BT-04 | Network-dependent; window is relative to "now" | Run `backtester/run.py` with a recent date range and confirm bars load and report is written |

---

## Validation Audit 2026-07-07

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

Retroactive audit after phase completion: all four requirements (BT-01..BT-04) map to green
automated tests; the one manual-only item (live network fetch) is legitimately environment-bound.
No test generation needed — coverage was built plan-by-plan across the 12 executed plans,
including negative-control-verified regression tests for the two re-verification BLOCKERs
(NaN feed hygiene 06-10, Gate-7 trades persistence 06-11).

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 90s (full suite ~37s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** verified 2026-07-07 — 0 gaps, 4/4 requirements covered by automated tests, 1 documented manual-only item.
