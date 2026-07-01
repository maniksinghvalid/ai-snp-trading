---
phase: 01-foundation
plan: "01"
subsystem: broker-access-layer
tags: [gateway, paper-guard, audit-log, scaffolding, safety, moomoo, asyncio]
dependency_graph:
  requires: []
  provides:
    - bot.gateway.MoomooGateway
    - bot.gateway.GatewayConfig
    - bot.gateway.GatewayError
    - bot.gateway.get_gateway_config
    - bot.safety.assert_paper_account
    - bot.safety.PaperGuardError
    - bot.safety.audit_log.append_audit
    - bot._utils (safe_get, safe_float, safe_int, format_enum, safe_close)
  affects:
    - All later plans (01-02, 01-03, 01-04) build on bot/ package tree
    - Phase 4 order placement must re-invoke assert_paper_account per D-05
tech_stack:
  added:
    - moomoo-api>=10.4.6408,<11.0 (broker SDK, direct import per D-02)
    - pandas (SDK DataFrame handling)
    - numpy==2.5.0 (indicator math, pinned)
    - jsonschema (strategy config validation, later phases)
    - structlog==26.1.0 (structured logging, later phases)
    - pytest>=8.0 + pytest-asyncio>=0.24 (test tooling)
  patterns:
    - GatewayConfig dataclass (mirrors FutuConfig from common.py, per D-02)
    - Triple fail-closed paper guard (flag + env + broker truth per D-04)
    - append-only JSONL audit log (extends ~/.futu_trade_audit.jsonl per SAFE-05)
    - asyncio.run_in_executor wrappers for blocking SDK calls (Anti-Pattern 5)
    - Reconciliation skeleton (reconcile_once + reconciliation_loop) per SAFE-02/03
key_files:
  created:
    - bot/__init__.py
    - bot/_utils.py
    - bot/gateway/__init__.py
    - bot/gateway/gateway.py
    - bot/safety/__init__.py
    - bot/safety/audit_log.py
    - bot/safety/paper_guard.py
    - tests/__init__.py
    - tests/conftest.py
    - tests/gateway/__init__.py
    - tests/gateway/test_gateway.py
    - tests/safety/__init__.py
    - tests/safety/test_audit_log.py
    - tests/safety/test_paper_guard.py
    - requirements.txt
    - requirements-dev.txt
    - .gitignore
  modified: []
decisions:
  - "D-02 upheld: gateway imports moomoo SDK directly, never from skills/ directory"
  - "Guard raises PaperGuardError (never sys.exit); only bot/main.py exits — PATTERNS.md rule"
  - "UTC timestamps in audit log via datetime.now(timezone.utc) instead of utcnow() (deprecated in Python 3.12+)"
  - "bot/gateway/__init__.py omits re-exports initially (avoids circular import when gateway.py not yet created) then re-exports after Task 3 completes"
  - "asyncio.iscoroutinefunction replaced with inspect.iscoroutinefunction in tests (deprecated in Python 3.16)"
metrics:
  duration: "8 minutes"
  completed_date: "2026-06-23"
  tasks_completed: 3
  tasks_total: 3
  files_created: 17
  files_modified: 0
  tests_added: 54
---

# Phase 01 Plan 01: Foundation Scaffolding + Safety-Critical Broker Layer Summary

**One-liner:** Triple fail-closed paper guard (flag + env + broker trd_env), append-only JSONL audit log, and MoomooGateway with persistent contexts and 60-90s reconciliation skeletons — wired together and proven by 54 automated tests.

## What Was Built

This plan establishes the full foundation that every later phase depends on:

1. **Package scaffolding** (`bot/`, `bot/gateway/`, `bot/safety/`, `tests/` mirror tree) with shared null-safe helpers in `bot/_utils.py` copied verbatim from `skills/moomooapi/scripts/common.py` per D-02.

2. **Audit log** (`bot/safety/audit_log.py`): `append_audit()` extends the existing `~/.futu_trade_audit.jsonl` pattern — append-only `"a"` mode, UTC timestamps, `ensure_ascii=False`, silent-on-failure (SAFE-05).

3. **Paper guard** (`bot/safety/paper_guard.py`): `assert_paper_account()` enforces three independent fail-closed assertions (D-04):
   - Guard 1: `cfg.paper_trading == True` (PAPER_TRADING env flag)
   - Guard 2: `cfg.trd_env.upper() == "SIMULATE"` (FUTU_TRD_ENV env var)
   - Guard 3: The exact `cfg.acc_id` row in `get_acc_list()` reports `trd_env == SIMULATE` (no auto-selection — D-03)
   - Any failure calls `_fail()`: writes `paper_guard_refusal` audit entry then raises `PaperGuardError` (never terminates process)

4. **MoomooGateway** (`bot/gateway/gateway.py`): `GatewayConfig` dataclass (all FUTU_* + PAPER_TRADING env vars; trd_env defaults to SIMULATE), `get_gateway_config()`, `_check_opend_alive()` (raises ConnectionError not SystemExit), `_parse_trd_env`/`_parse_market`/`_check_ret`, and `MoomooGateway` class with:
   - Persistent `OpenQuoteContext` + `OpenSecTradeContext` created at `connect()`
   - `assert_paper_account()` invoked as hard gate before the gateway is usable
   - Async wrappers `get_acc_list()` / `get_positions()` via `run_in_executor`
   - Reconciliation skeletons: `reconcile_once()` (returns dict with positions/accounts/drift) and `reconciliation_loop(interval_s=75.0)` — within the 60-90s SAFE-03 range

5. **Test suite** (54 tests across `tests/safety/` and `tests/gateway/`): verifies all four guard failure cases, append-only semantics, UTC timestamps, D-02 compliance (no skills imports), ConnectionError propagation, connect() paper-guard invocation, async skeleton types and return shapes, and reconciliation loop interval bounds.

6. **Dependency files**: `requirements.txt` (runtime: moomoo-api, pandas, numpy, jsonschema, structlog), `requirements-dev.txt` (pytest, pytest-asyncio), `.gitignore` (data/, __pycache__, .env, build artifacts).

## Task Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | f7ff738 | Package scaffolding, shared utils, dependency files, conftest |
| 2 | fb6fb8c | Audit log writer + triple fail-closed paper guard |
| 3 | 5724a8c | MoomooGateway persistent contexts, run_in_executor, reconciliation skeletons |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] "sys.exit" literal in docstrings caused test assertion failures**
- **Found during:** Task 2 (first test run) and Task 3
- **Issue:** Tests checking `"sys.exit" not in source` failed because docstrings mentioned "sys.exit" conceptually (e.g. "never sys.exit"). The plan requires the source check to return 0 matches.
- **Fix:** Rephrased all docstrings to say "never terminates the process" or "raises PaperGuardError" rather than "never sys.exit". Zero actual sys.exit() calls exist — purely a comment wording issue.
- **Files modified:** `bot/safety/paper_guard.py`, `bot/gateway/gateway.py`
- **Commit:** Inline with the task commits above

**2. [Rule 1 - Bug] "from skills" literal in docstrings caused D-02 compliance test failures**
- **Found during:** Task 3 (test_gateway_does_not_import_skills)
- **Issue:** Module docstrings mentioned "from skills/" to explain what NOT to do, which triggered the grep-based import check.
- **Fix:** Rephrased docstrings to avoid the literal "from skills" while preserving the D-02 explanation.
- **Files modified:** `bot/_utils.py`, `bot/gateway/gateway.py`
- **Commit:** Inline with task commits

**3. [Rule 2 - Deprecation] utcnow() deprecated in Python 3.12+**
- **Found during:** Task 2 test run (DeprecationWarning in output)
- **Issue:** `datetime.datetime.utcnow()` is deprecated and scheduled for removal.
- **Fix:** Replaced with `datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")` which is the forward-compatible pattern.
- **Files modified:** `bot/safety/audit_log.py`
- **Commit:** fb6fb8c

**4. [Rule 2 - Deprecation] asyncio.iscoroutinefunction deprecated in Python 3.16**
- **Found during:** Task 3 test run (DeprecationWarning)
- **Issue:** `asyncio.iscoroutinefunction` is deprecated; `inspect.iscoroutinefunction` is the correct future-proof API.
- **Fix:** Updated test assertions to use `inspect.iscoroutinefunction`.
- **Files modified:** `tests/gateway/test_gateway.py`
- **Commit:** 5724a8c

**5. [Rule 3 - Blocking] bot/gateway/__init__.py circular import at Task 1**
- **Found during:** Task 1 verification — `import bot.gateway` failed because `__init__.py` imported from `gateway.py` which didn't exist yet.
- **Fix:** `bot/gateway/__init__.py` started as a docstring-only file for Task 1; re-exports were added in Task 3 after `gateway.py` was created.
- **Files modified:** `bot/gateway/__init__.py` (updated twice)
- **Commit:** Task 1 commit (chore) and Task 3 commit (feat)

## Known Stubs

The following are intentional skeleton stubs documented for Phase 4:

| Stub | File | Line | Reason |
|------|------|------|--------|
| `"drift": {}` | `bot/gateway/gateway.py` | reconcile_once() | Phase 4 populates with actual drift analysis (SAFE-02 skeleton) |
| `# Phase 4: full reconciliation logic` | `bot/gateway/gateway.py` | reconcile_once() + reconciliation_loop() | Loop is wired; payload grows in Phase 4 |

These stubs do not prevent this plan's goal (broker access + paper safety) from being achieved. They are explicitly scoped to Phase 4 per the plan's SAFE-02/SAFE-03 specification ("skeleton/wiring only this phase, full logic Phase 4").

## Threat Flags

No new threat surface beyond the threat model. All network/file access is covered:
- `bot/gateway/gateway.py` socket TCP check → T-01-05 (accepted)
- `bot/safety/audit_log.py` file write to `~/.futu_trade_audit.jsonl` → T-01-02 (mitigated by append-only)

## Self-Check: PASSED

- [x] `bot/gateway/gateway.py` exists (337 lines, > 120 min)
- [x] `bot/safety/paper_guard.py` exists and contains `def assert_paper_account(` and `class PaperGuardError`
- [x] `bot/safety/audit_log.py` exists and contains `def append_audit(`
- [x] `bot/_utils.py` exists with all 5 helpers
- [x] `requirements.txt` contains `moomoo-api`
- [x] 54 tests pass: `python3 -m pytest tests/safety tests/gateway -q` exits 0
- [x] Commits f7ff738, fb6fb8c, 5724a8c exist in git log
