---
phase: 07-strategy-optimization
plan: "02"
subsystem: signal,scanner
tags: [rvol-tod, cum-volume, bar-aggregator, fetcher, scanner, tod-baselines]

# Dependency graph
requires:
  - phase: 07-01
    provides: StateStore.upsert_tod_baselines, cfg.rvol_tod_lookback_days, tod_baselines migration 0005
provides:
  - BarEvent.cum_volume field (Phase 7 RVOL-TOD numerator field)
  - BarAggregator._session_volume accumulator (per-code, reset each session)
  - fetcher.download_intraday_5m (30-day 5m regular-session batch downloader)
  - scanner._compute_tod_baselines (ET-bucketed 14-session cumulative-volume baseline)
  - premarket scan TOD baseline persistence (store.upsert_tod_baselines per candidate)
affects:
  - 07-05 (RVOL-TOD gate — reads cum_volume from BarEvent + tod_baselines via get_tod_baseline)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-code Dict[str, int] session accumulator in BarAggregator — single-writer (SDK thread), reset at reset_session() (T-07-07 mitigation)"
    - "ET-localise index before strftime('%H:%M') in _compute_tod_baselines (T-07-06 / Pitfall 4 mitigation)"
    - "download_intraday_5m mirrors download_intraday_1m exactly — same _download_batch delegate, distinct event names (tod_baseline_*)"
    - "Per-candidate 5m fetch in run_daily_scan with per-candidate try/except for graceful degradation"
    - "data_5m.get(yf_sym) used directly (not via get_ticker_frame) to preserve 'Volume' column case required by _compute_tod_baselines"

key-files:
  created: []
  modified:
    - bot/signal/events.py
    - bot/signal/bar_aggregator.py
    - bot/service/bot.py
    - bot/scanner/fetcher.py
    - bot/scanner/scanner.py
    - tests/signal/test_bar_aggregator.py
    - tests/scanner/test_fetcher.py
    - tests/scanner/test_scanner.py

key-decisions:
  - "cum_volume placed at END of BarEvent fields (after lod) due to Python dataclass rule: fields with defaults must follow non-default fields. PATTERNS.md suggested between volume and hod but that would require making hod/lod optional too. All BarEvent construction uses keyword args so field order doesn't matter."
  - "bot/service/bot.py updated to wire cum_volume from bar_data into BarEvent (Rule 2 — without this the cumulative volume computed by BarAggregator would never reach BarEvent even though the data path was correctly producing it)"
  - "cfg.rvol_tod_lookback_days used at run_daily_scan call site (PATTERNS.md had typo: rvol_lookback_days — wrong field name)"
  - "Per-candidate 5m download via batch download_intraday_5m of top-20 yf symbols; frame lookup via data_5m.get(yf_sym) not get_ticker_frame (to preserve capital-V Volume column)"

patterns-established:
  - "Phase 7 _session_volume pattern: Dict[str, int] per-code accumulator written only on bar close, cleared at reset_session()"
  - "Phase 7 TOD bucket pattern: df.index.tz_convert(ET) then df.index.strftime('%H:%M') — always ET regardless of input tz"
  - "Phase 7 5m download pattern: download_intraday_5m mirrors 1m pattern with prepost=False and tod_baseline_* event names"

requirements-completed: [SIG-RVOL-TOD]

# Metrics
duration: 55min
completed: 2026-07-03
---

# Phase 07 Plan 02: RVOL-TOD Data Production Path Summary

**BarEvent.cum_volume carries the per-code running session volume (numerator); premarket scan writes a 14-session ET-bucketed cumulative-volume baseline per candidate to tod_baselines (denominator); plan 07-05 will wire the fair I3 gate using both**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-07-03T22:00:00Z
- **Completed:** 2026-07-03T22:55:00Z
- **Tasks:** 3 (each with TDD RED + GREEN commits)
- **Files modified:** 8

## Accomplishments

- `BarEvent.cum_volume: int = 0` field added (backward-compatible optional field at end of dataclass, default 0)
- `BarAggregator._session_volume: Dict[str, int]` accumulator added to `__init__` and cleared in `reset_session()` (T-07-07 Pitfall 1 mitigation)
- On every closed bar in `_handle_row`, `_session_volume[code]` is incremented by the bar's volume BEFORE building `bar_data`, and `cum_volume` is included in the emitted dict
- `bot/service/bot.py` wired to pass `cum_volume=bar_data.get("cum_volume", 0)` into BarEvent (Rule 2 data wiring)
- `download_intraday_5m` added to `bot/scanner/fetcher.py`, mirroring `download_intraday_1m` with `period="30d"`, `interval="5m"`, `prepost=False`, and `tod_baseline_*` event names
- `_compute_tod_baselines(frame_5m, lookback_days)` added to `bot/scanner/scanner.py`: ET-localises index, groups by date+HH:MM, cumsums Volume per session, averages across most-recent lookback_days sessions
- `run_daily_scan` Step 5b: batch downloads 5m data for top-20 yf symbols, computes and stores TOD baselines per candidate via `store.upsert_tod_baselines`; per-candidate failures log and continue (graceful degradation)
- 547 tests pass (up from 535 baseline); 12 new tests across 3 test files

## Task Commits

Each task used TDD (RED then GREEN):

1. **Task 1 RED: cumulative session volume tests** — `c442382` (test)
2. **Task 1 GREEN: BarEvent.cum_volume + BarAggregator accumulator + bot.py wiring** — `fe638c7` (feat)
3. **Task 2 RED: download_intraday_5m tests** — `d6d5ac2` (test)
4. **Task 2 GREEN: download_intraday_5m implementation** — `6090907` (feat)
5. **Task 3 RED: TOD baseline tests** — `0fb2b09` (test)
6. **Task 3 GREEN: _compute_tod_baselines + premarket scan wiring** — `aa8f519` (feat)

## Files Created/Modified

- `bot/signal/events.py` — Added `cum_volume: int = 0` field to BarEvent dataclass; updated docstring
- `bot/signal/bar_aggregator.py` — Added `_session_volume` to `__init__` and `reset_session`; accumulator logic in `_handle_row`; `cum_volume` in bar_data dict
- `bot/service/bot.py` — Wire `cum_volume=bar_data.get("cum_volume", 0)` into BarEvent construction
- `bot/scanner/fetcher.py` — Added `download_intraday_5m` function (30-day 5m regular-session batch downloader)
- `bot/scanner/scanner.py` — Added `download_intraday_5m` import; added `_compute_tod_baselines` helper; added Step 5b TOD baseline computation in `run_daily_scan`
- `tests/signal/test_bar_aggregator.py` — Added `TestCumulativeSessionVolume` (4 tests: default=0, accumulation, per-code isolation, reset_session)
- `tests/scanner/test_fetcher.py` — Added `TestDownloadIntraday5m` (3 tests: kwargs, passthrough, event names)
- `tests/scanner/test_scanner.py` — Added `TestComputeTodBaselines` (3 unit tests) and `TestTodBaselineIntegration` (2 integration tests)

## Decisions Made

- **cum_volume field position**: Placed at END of BarEvent (after `lod`) not between `volume` and `hod` as PATTERNS.md suggested. Python dataclass rule requires fields with defaults to come after non-default fields. All BarEvent constructions use keyword args so positional order is not a concern.
- **bot.py wiring (Rule 2)**: Without wiring `cum_volume` from `bar_data` into BarEvent, the computed value in BarAggregator would never reach BarEvent.cum_volume in the signal pipeline. Added as required correctness fix.
- **cfg.rvol_tod_lookback_days**: PATTERNS.md call site had `cfg.rvol_lookback_days` (wrong — that is the daily RVOL lookback). Used `cfg.rvol_tod_lookback_days` (the Phase 7 field added in 07-01).
- **Direct data_5m.get(yf_sym) lookup**: Used instead of `get_ticker_frame()` to preserve the capital-V "Volume" column that `_compute_tod_baselines` expects (yfinance convention). `get_ticker_frame` lowercases all columns.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical wiring] bot/service/bot.py cum_volume wiring**
- **Found during:** Task 1 GREEN
- **Issue:** BarAggregator correctly computes and emits `cum_volume` in bar_data, but `bot/service/bot.py::_on_bar_closed` constructs BarEvent without the `cum_volume` kwarg — so `BarEvent.cum_volume` would always be 0 even with the accumulator correctly producing values.
- **Fix:** Added `cum_volume=bar_data.get("cum_volume", 0)` to the BarEvent construction in bot.py.
- **Files modified:** `bot/service/bot.py`
- **Commit:** `fe638c7`

**2. [Rule 1 - Bug] PATTERNS.md call site typo: cfg.rvol_lookback_days vs cfg.rvol_tod_lookback_days**
- **Found during:** Task 3 GREEN
- **Issue:** PATTERNS.md scanner call site specified `cfg.rvol_lookback_days` (the daily RVOL baseline lookback), but the correct Phase 7 field is `cfg.rvol_tod_lookback_days` (added in 07-01 with default 14).
- **Fix:** Used `cfg.rvol_tod_lookback_days` at the call site.
- **Files modified:** `bot/scanner/scanner.py`
- **Commit:** `aa8f519`

## Known Stubs

None — all new code is fully functional. No placeholder values, hardcoded empties, or TODO markers introduced.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: network | bot/scanner/scanner.py | download_intraday_5m called once at premarket scan — same yfinance trust boundary as existing daily+1m downloads; existing degradation gate (T-07-05) and per-candidate try/except applied |

All STRIDE threats from the plan threat_model are mitigated:
- T-07-05 (degraded yfinance 5m batch): download_intraday_5m reuses existing degradation gate + per-candidate try/except
- T-07-06 (UTC vs ET bucket mismatch): df.index.tz_convert(ET) before strftime("%H:%M") in _compute_tod_baselines; test asserts ET bucketing
- T-07-07 (cross-session volume carryover): reset_session() clears _session_volume; test proves reset behavior
- T-07-08 (rate limits): bounded concurrency (threads=5) reused; download runs once at premarket only
- T-07-SC (supply-chain): no new packages installed

## TDD Gate Compliance

All three tasks followed the RED then GREEN cycle:
1. RED commit verified to fail before implementation
2. GREEN commit verified to make all new tests pass
3. Full regression suite (547 tests) passes after all three GREEN commits

## Next Phase Readiness

- Plan 07-03 (broker-side stop order) can proceed independently — no dependency on 07-02
- Plan 07-04 (exit model) can proceed independently
- Plan 07-05 (RVOL-TOD gate in signal_engine) depends on 07-02: `BarEvent.cum_volume` and `store.get_tod_baseline(scan_date, code, time_bucket)` are both ready

---
*Phase: 07-strategy-optimization*
*Completed: 2026-07-03*

## Self-Check: PASSED
