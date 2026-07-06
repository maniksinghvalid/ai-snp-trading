---
phase: "05-service-orchestration-and-reliability"
plan: "00"
subsystem: "service-foundation"
tags: [apscheduler, config, gateway, position-manager, test-stubs, tdd]
dependency_graph:
  requires: []
  provides:
    - apscheduler==3.11.2 pinned + importable
    - bot.service package (bot/service/__init__.py)
    - tests/service package + conftest with telegram_env fixture
    - rules.json service block (15 tunables, CFG-01)
    - StrategyConfig.service.* fields (15 flat fields)
    - MoomooGateway.get_global_state() dict-returning async method
    - PositionManager.on_entry_alert + on_exit_alert optional callbacks
    - 14 RED test stubs for TradingBot/OpenDWatchdog/TelegramAlerter/ReportBuilder
    - .env.example with TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID documented
    - reports/ and logs/ gitignored
  affects:
    - bot/gateway/gateway.py (new method)
    - bot/position/manager.py (new params)
    - bot/config/schema.py (service block + required)
    - bot/config/loader.py (StrategyConfig + load_strategy_config)
    - rules.json (service block)
tech_stack:
  added:
    - apscheduler==3.11.2 (MIT, 14yr history, github.com/agronholm/apscheduler)
    - pytest-asyncio==1.4.0 (async test support for service layer tests)
  patterns:
    - run_in_executor wrapping (get_global_state follows subscribe/get_equity pattern)
    - Callback injection with None default (on_entry_alert/on_exit_alert backward-compatible)
    - xfail stub pattern (14 tests collect + xfail until waves 1-4 implement them)
    - ALERT-04 isolation (callback exceptions logged + swallowed, never propagate into fill processing)
key_files:
  created:
    - bot/service/__init__.py
    - tests/service/__init__.py
    - tests/service/conftest.py
    - tests/service/test_bot.py
    - tests/service/test_watchdog.py
    - tests/service/test_alerter.py
    - tests/service/test_report.py
    - .env.example
  modified:
    - requirements.txt (apscheduler==3.11.2, pytest-asyncio==1.4.0)
    - .gitignore (reports/, logs/)
    - rules.json (service block)
    - bot/config/schema.py (service block in properties + required)
    - bot/config/loader.py (StrategyConfig 15 new fields + svc_cfg mapping)
    - bot/gateway/gateway.py (get_global_state async method)
    - bot/position/manager.py (on_entry_alert/on_exit_alert params + invocations)
    - .planning/phases/05-service-orchestration-and-reliability/05-VALIDATION.md
decisions:
  - "Used @pytest.mark.xfail(strict=False) for RED stubs — allows suite to collect cleanly and tests to be xfail until respective plans implement the modules"
  - "Installed apscheduler + pytest-asyncio with --break-system-packages (macOS externally-managed Python; no venv found in project)"
  - "Added pytest-asyncio==1.4.0 to requirements.txt (Wave 0 adds it per 05-VALIDATION.md Wave 0 Requirements)"
  - "on_exit_alert fires only when remaining_quantity==0 (full close only, not partial exit fills)"
  - "R-multiple in exit alert computed inline in _on_exit_fill as (exit_price-entry)/risk; on_exit_alert receives code/reason/r_multiple"
metrics:
  duration_minutes: 8
  completed_date: "2026-06-25"
  tasks_completed: 4
  tasks_total: 4
  files_changed: 17
  lines_added: 817
---

# Phase 05 Plan 00: Wave-0 Foundation Summary

**One-liner:** apscheduler==3.11.2 pinned and importable; service config block (15 tunables) validated end-to-end through StrategyConfig; MoomooGateway.get_global_state() never-raising dict method; PositionManager backward-compatible alert callbacks; 14 RED test stubs for wave 1-4 feature slices.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 0 | Package-legitimacy checkpoint (pre-approved) | (pre-approved) | — |
| 1 | Pin apscheduler, scaffold bot/service + tests/service | c622e76 | requirements.txt, bot/service/__init__.py, tests/service/*, .env.example, .gitignore |
| 2 | Service config block in rules.json + schema.py + loader | 5f6b153 | rules.json, bot/config/schema.py, bot/config/loader.py |
| 3 (RED) | Add RED gateway tests for get_global_state | bb89c11 | tests/gateway/test_gateway.py |
| 3 (GREEN) | MoomooGateway.get_global_state + PositionManager callbacks | 7b114bd | bot/gateway/gateway.py, bot/position/manager.py |
| 4 | RED test stubs for bot/watchdog/alerter/report | 108c423 | tests/service/test_*.py, 05-VALIDATION.md |

## Decisions Made

### 1. xfail over pytest.importorskip for RED stubs

The stubs use `@pytest.mark.xfail(reason="...", strict=False)` rather than `pytest.importorskip("bot.service.bot")`. Both are valid per plan, but xfail provides cleaner output: 14 xfailed (expected) vs 14 skipped. The xfail approach also makes each stub's target more explicit and runs the full test body to catch import issues early.

### 2. pytest-asyncio installed + pinned

pytest-asyncio 1.4.0 was not installed at the start of plan execution. Installed via `--break-system-packages` (externally-managed Python on macOS; no project venv found). Added to requirements.txt as required by 05-VALIDATION.md Wave 0 Requirements.

### 3. Alert callback fires only on full position close

`on_exit_alert` is invoked only when `remaining_quantity == 0` (full close). Partial fills leave the position open and do not trigger the exit alert — consistent with the plan's description that the callback receives `exit_reason + r_multiple` which only makes sense on a complete exit.

### 4. R-multiple computed inline in _on_exit_fill

The plan specified `on_exit_alert(code, exit_reason, r_multiple)`. R-multiple is computed inline as `(exit_price - entry) / (entry - initial_stop)` with a zero-division guard. The "exit_reason" is the string `"exit_fill"` at this hook point; FSM-triggered exits (stop_out, partial, etc.) will set their own exit_reason when the Phase 5 alerter fan-out is wired in 05-03.

## Deviations from Plan

### Auto-added: pytest-asyncio pinned to requirements.txt

- **Found during:** Task 4
- **Issue:** pytest-asyncio was not installed; 05-VALIDATION.md Wave 0 Requirements explicitly calls for it to be added if missing
- **Fix:** Installed pytest-asyncio==1.4.0 and added it to requirements.txt under the Phase 5 comment block
- **Rule:** Rule 2 (auto-add missing critical functionality — test infrastructure required for Wave 0 Nyquist compliance)
- **Commit:** 108c423

None of the plan's specified tasks required deviation from the implementation spec.

## Verification Results

All plan success criteria met:

- apscheduler==3.11.2 pinned in requirements.txt and importable (`python3 -c "import apscheduler"` returns 3.11.2)
- `load_strategy_config('rules.json')` returns all 15 service.* fields with correct values
- `MoomooGateway.get_global_state()` exists, returns dict with connected/qot_logined/trd_logined, never raises on RET_OK/non-RET_OK/exception paths (5 gateway tests green)
- `PositionManager.__init__` accepts on_entry_alert=None + on_exit_alert=None (backward compatible)
- `pytest tests/gateway/test_gateway.py -x -q` → 49 passed
- `pytest tests/service/ -q` → 14 xfailed (all 14 named stubs collected cleanly)
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID documented in .env.example (blank)
- reports/ and logs/ gitignored
- No secrets in rules.json (service block holds non-secret toggles only — D-13)

## Known Stubs

None. All files created in this plan are foundation/scaffold files. The test stubs are intentionally xfail (RED) as they are Wave 0 Nyquist scaffolding targeting classes that will be implemented in plans 05-01 through 05-04.

## Threat Flags

None. All threat mitigations from the plan's threat register were applied:

- T-05-SC: Package legitimacy verified by operator before pin/install; pinned exact 3.11.2
- T-05-00-01: Telegram secrets NOT in rules.json; documented as blank in .env.example only (D-13)
- T-05-00-02: force_close_misfire_grace_s defaults to 300 (not None) — Pitfall 7 guard
- T-05-00-03: alert callbacks only receive position fields at this layer; no network sink yet (accepted)

## Self-Check: PASSED

Files exist:
- bot/service/__init__.py: FOUND
- tests/service/__init__.py: FOUND
- tests/service/conftest.py: FOUND
- tests/service/test_bot.py: FOUND
- tests/service/test_watchdog.py: FOUND
- tests/service/test_alerter.py: FOUND
- tests/service/test_report.py: FOUND
- .env.example: FOUND

Commits:
- c622e76 (Task 1): FOUND
- 5f6b153 (Task 2): FOUND
- bb89c11 (Task 3 RED): FOUND
- 7b114bd (Task 3 GREEN): FOUND
- 108c423 (Task 4): FOUND
