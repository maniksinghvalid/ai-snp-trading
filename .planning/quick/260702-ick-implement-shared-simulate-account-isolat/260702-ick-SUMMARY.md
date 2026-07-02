---
phase: quick-260702-ick
plan: "01"
subsystem: gateway, scanner, risk, config
tags: [safety, isolation, risk-sizing, TDD]
dependency_graph:
  requires: [SAFE-OG-01, SCAN-08, RISK-01]
  provides: [get_external_codes, scan_exclusion, sizing_equity_usd]
  affects: [bot/gateway/gateway.py, bot/scanner/scanner.py, bot/risk/risk_engine.py, bot/config/loader.py, bot/config/schema.py, rules.json]
tech_stack:
  added: []
  patterns: [TDD-RED-GREEN, fail-open-on-GatewayError, trailing-dataclass-default]
key_files:
  created: []
  modified:
    - bot/gateway/gateway.py
    - bot/scanner/scanner.py
    - bot/risk/risk_engine.py
    - bot/config/schema.py
    - bot/config/loader.py
    - rules.json
    - tests/gateway/test_gateway.py
    - tests/execution/test_engine.py
    - tests/scanner/test_scanner.py
    - tests/config/test_loader.py
    - tests/risk/test_risk_engine.py
decisions:
  - "get_external_codes raises GatewayError on failed broker query; caller owns fail-open logic"
  - "Scanner fails open on GatewayError (EXEC-04 is the entry backstop)"
  - "sizing_equity_usd=None in _make_cfg for risk tests: existing tests continue on live-equity path"
  - "_make_mock_gateway in scanner tests gets get_external_codes=AsyncMock(return_value=set()) to keep pre-existing tests from breaking"
metrics:
  duration: "~35 minutes"
  completed: "2026-07-02T20:34:02Z"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 11
---

# Phase quick-260702-ick Plan 01: Shared SIMULATE Account Isolation Summary

Implement the approved shared-SIMULATE account isolation design so the bot never sizes from, adopts, or competes with the operator's manual holdings on shared SIMULATE account 1727266. Three pieces: gateway ownership predicate, scanner scan-time exclusion, and fixed sizing basis.

## Tasks Completed

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 RED | TestGetExternalCodes + EXEC-04 regression | 04d8497 | done |
| 1 GREEN | get_external_codes(store) on MoomooGateway | 02b2c4c | done |
| 2 RED | Scanner external code exclusion tests | 4493def | done |
| 2 GREEN | _exclude_external_codes + wired into both scan paths | 8f1cfba | done |
| 3 RED | sizing_equity_usd config + risk engine tests | 5759653 | done |
| 3 GREEN | rules.json / schema / loader / RiskEngine implementation | 62a2fd7 | done |

## What Was Built

**Task 1 — Gateway `get_external_codes(store)`**

New async method on `MoomooGateway` implementing the SAFE-OG-01 ownership predicate:
- Calls `get_positions(refresh_cache=True)` (Pitfall B mandatory for SIMULATE)
- Builds `open_pos_codes` from `store.get_open_positions()` and checks `store.has_pending_intent()`
- Returns a `set` of codes held at the broker but NOT bot-owned (manual holdings, any short positions)
- Raises `GatewayError` on failed broker query — caller decides fail-open/fail-closed policy
- Located near `get_positions` at ~line 356 in `gateway.py`

Also added `test_exec04_blocks_entry_for_manual_holding` as a EXEC-04 regression test documenting the entry-time backstop — NO change to `bot/execution/engine.py`.

**Task 2 — Scanner scan-time exclusion (`_exclude_external_codes`)**

New module-level helper in `scanner.py`:
- `gateway=None` → no-op (preserves all existing unit tests without a broker)
- Calls `_run_coro(gateway.get_external_codes(store))` using the existing async bridge
- On success: filters out codes in the external set, logs `symbol_excluded_manual_holding` per dropped code
- On `GatewayError`: logs `external_exclusion_skipped_query_failed`, returns full list (fail-open — EXEC-04 backstop)
- Also imports `GatewayError` at top of `scanner.py`

Wired into both scan entrypoints immediately after `_compute_candidates` (Step 3b):
- `run_daily_scan`: before `passing.sort(...)` at Step 4
- `run_intraday_rescan`: before `passing.sort(...)` at Step 4

Fixed `_make_mock_gateway()` in `test_scanner.py` to add `get_external_codes = AsyncMock(return_value=set())` to prevent 4 pre-existing subscribe tests from breaking.

**Task 3 — Fixed sizing basis (`risk.sizing_equity_usd`)**

- `rules.json`: added `"sizing_equity_usd": 100000` to risk block (source-of-truth default)
- `bot/config/schema.py`: added `"sizing_equity_usd": {"type": ["number", "null"]}` to risk properties (NOT in `required` — absent key falls back to loader default)
- `bot/config/loader.py`: added `from typing import Optional`; added trailing field `sizing_equity_usd: Optional[float] = 100_000.0` to `StrategyConfig`; mapping: `sizing_equity_usd=rk.get("sizing_equity_usd", 100_000)` (absent → 100000, explicit null → None)
- `bot/risk/risk_engine.py`: replaced unconditional `equity = await self._gateway.get_equity()` with basis selection — `if self._cfg.sizing_equity_usd is not None: equity = float(self._cfg.sizing_equity_usd)` else awaits `get_equity()`. `equity_used` unchanged.
- `tests/risk/test_risk_engine.py`: updated `_make_cfg()` with `sizing_equity_usd=None` param (default None → existing tests keep using live-equity path unchanged)

## Verification

```
python3 -m pytest -q
# 514 passed, 1 skipped (baseline: 496 passed + 18 new tests)

python3 -m pytest tests/gateway/test_gateway.py tests/execution/test_engine.py tests/scanner/test_scanner.py tests/config/test_loader.py tests/risk/test_risk_engine.py -q
# 156 passed, 1 skipped

python3 -c "from bot.config.loader import load_strategy_config; c=load_strategy_config('rules.json'); print('sizing_equity_usd=', c.sizing_equity_usd)"
# sizing_equity_usd= 100000
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _make_mock_gateway() to set get_external_codes as AsyncMock**
- **Found during:** Task 2 GREEN — 4 pre-existing scanner subscribe tests broke when `_exclude_external_codes` was wired in
- **Issue:** `_make_mock_gateway()` in `test_scanner.py` only set `subscribe = AsyncMock()`. When the new `_exclude_external_codes` called `_run_coro(gateway.get_external_codes(store))`, it received a plain `MagicMock` return value (not a coroutine), causing `asyncio.run()` to raise `TypeError: An asyncio.Future, a coroutine or an awaitable is required`
- **Fix:** Added `gw.get_external_codes = AsyncMock(return_value=set())` to `_make_mock_gateway()`. Returns empty set → no exclusions in non-exclusion tests (correct behavior preserved)
- **Files modified:** `tests/scanner/test_scanner.py`
- **Commit:** 8f1cfba (included with GREEN implementation)

## Known Stubs

None. All data flows are wired. `get_external_codes` returns a real broker-queried set; `_exclude_external_codes` uses it to filter the candidate list; `sizing_equity_usd` is wired from `rules.json` through `schema.py` → `loader.py` → `RiskEngine`.

## Threat Flags

None. No new network endpoints, auth paths, or schema changes at trust boundaries beyond what was planned.

## TDD Gate Compliance

All three tasks followed RED → GREEN discipline:
- Task 1: `04d8497` (test/RED) → `02b2c4c` (feat/GREEN)
- Task 2: `4493def` (test/RED) → `8f1cfba` (feat/GREEN)
- Task 3: `5759653` (test/RED) → `62a2fd7` (feat/GREEN)

## Self-Check: PASSED

All key files found on disk. All 6 task commits verified in git log. Full suite: 514 passed, 1 skipped (0 failed).
