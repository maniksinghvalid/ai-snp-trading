---
phase: 01-foundation
plan: "04"
subsystem: safety-primitives
tags: [et-helpers, zoneinfo, structlog, logging, kill-switch, sigint, sentinel, audit-log, shutdown, safety]
dependency_graph:
  requires:
    - bot.safety.audit_log.append_audit (01-01)
  provides:
    - bot.safety.et_helpers.ET
    - bot.safety.et_helpers.now_et
    - bot.safety.et_helpers.to_et
    - bot.safety.logger.configure_logging
    - bot.safety.logger.get_logger
    - bot.safety.kill_switch.KillSwitch
  affects:
    - All later phases that need ET-correct timing logic (Phase 2, 3, 4, 5)
    - All later phases that emit structured logs (Phase 2-6)
    - Phase 5 service scheduler wires KillSwitch to the main event loop
tech_stack:
  added:
    - zoneinfo (Python 3.9+ stdlib) — DST-correct America/New_York aware datetimes
    - structlog==26.1.0 — configured this phase (declared in requirements.txt in 01-01)
    - logging.handlers.RotatingFileHandler — rotating JSON file output
  patterns:
    - ET = ZoneInfo("America/New_York") — never fixed offsets or pytz (SVC-04)
    - structlog configured with contextvars for asyncio propagation
    - dual-output structlog: JSON RotatingFileHandler + ConsoleRenderer to stderr
    - KillSwitch idempotent _trigger: set Event → log → flush callbacks → audit
    - append_audit reused from bot.safety.audit_log (01-01), never redefined
key_files:
  created:
    - bot/safety/et_helpers.py
    - bot/safety/logger.py
    - bot/safety/kill_switch.py
    - tests/safety/test_et_helpers.py
    - tests/safety/test_logger.py
    - tests/safety/test_kill_switch.py
  modified: []
decisions:
  - "Naive datetime passed to to_et() treated as UTC (not host-local) — explicit, consistent with PITFALLS #6"
  - "structlog configured with cache_logger_on_first_use=True for performance; reset_defaults() in tests for isolation"
  - "KillSwitch _trigger protected by threading.Lock for thread-safety; idempotency enforced via _triggered_once flag"
  - "AUDIT_LOG_PATH swapped via module attribute in tests (no monkeypatching of append_audit internals needed)"
metrics:
  duration: "3 minutes"
  completed_date: "2026-06-23"
  tasks_completed: 2
  tasks_total: 2
  files_created: 6
  files_modified: 0
  tests_added: 39
---

# Phase 01 Plan 04: ET Helpers + Structured Logger + KillSwitch Summary

**One-liner:** DST-correct zoneinfo ET helpers, structlog rotating-file + stderr logger, and idempotent KillSwitch (sentinel-file + SIGINT → shutdown Event + state-flush + append-only audit) — 39 tests, all passing.

## What Was Built

This plan delivers the remaining foundation safety primitives — timezone correctness, structured logging, and graceful shutdown — that every later phase depends on:

1. **ET helpers** (`bot/safety/et_helpers.py`):
   - `ET = ZoneInfo("America/New_York")` — canonical timezone object, no fixed offsets, no third-party libs.
   - `now_et() -> datetime` — returns `datetime.now(tz=ET)`, always aware (utcoffset() is not None).
   - `to_et(dt) -> datetime` — converts any aware datetime to ET; naive input is treated as UTC (explicit, per PITFALLS #6), never host-local. Raises `TypeError` for non-datetime inputs.
   - DST-correct: July datetimes return -4h (EDT), January datetimes return -5h (EST) — proven by test.

2. **Structured logger** (`bot/safety/logger.py`):
   - `configure_logging(log_dir="logs", level="INFO")` — one-call setup with two channels:
     - Rotating JSON file (`RotatingFileHandler`, 10 MB × 5 backups) under `log_dir/bot.log`.
     - Human-readable `ConsoleRenderer` to stderr with colour when TTY.
   - `contextvars` integration via `structlog.contextvars.merge_contextvars` — context flows across asyncio boundaries.
   - Processor chain: `TimeStamper(fmt="iso")`, `add_log_level`, `add_logger_name`, `PositionalArgumentsFormatter`, `StackInfoRenderer`.
   - Idempotent (guarded by `_configured` flag).
   - `get_logger(name=None)` — returns a bound structlog logger.
   - No credentials or FutuConfig fields are ever logged (T-01-15).

3. **KillSwitch** (`bot/safety/kill_switch.py`):
   - Constructor takes a configurable `sentinel_path` (explicit argument, then `BOT_KILL_FILE` env var, then `./.bot_kill`).
   - `install()` — registers `signal.signal(signal.SIGINT, self._handle_signal)`.
   - `check_file()` — returns True if the sentinel file exists (for orchestration loop polling).
   - `trigger(reason)` — explicit trigger (called when `check_file()` is True).
   - `_handle_signal(signum, frame)` — SIGINT handler; converges on `_trigger("SIGINT")`.
   - `_trigger(reason)` — idempotent core (threading.Lock + _triggered_once flag):
     - Sets `threading.Event`.
     - Logs structured `kill_switch_triggered` warning via `get_logger`.
     - Invokes all registered flush callbacks in order.
     - Appends `{"event": "kill_switch", "reason": reason}` via `append_audit` (01-01's audit log — reused, never redefined).
   - `register_flush(callback)` — registers a state-flush callable (StateStore.flush wired in Phase 5).
   - `triggered` property — True if event is set.

4. **Test suite** (39 tests total, 0 failures):
   - `test_et_helpers.py` (11 tests): ET is ZoneInfo, now_et() is aware, DST summer/winter offsets, moment preservation, naive=UTC, non-UTC aware, TypeError, fixed-offset detection.
   - `test_logger.py` (10 tests): log file creation, directory creation, idempotency, info/warning level in JSON, timestamp in JSON, event field in JSON, get_logger with and without name.
   - `test_kill_switch.py` (18 tests): check_file before/after touch, trigger sets event, callbacks run, audit written, prior entries preserved, SIGINT handler paths, install registers handler, idempotency (flush once / one audit entry), import check, env var config.

## Task Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 0c38a57 | ET helpers + structlog rotating logger + tests |
| 2 | 005d060 | KillSwitch sentinel + SIGINT + shutdown Event + audit + tests |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all files are fully implemented with no placeholder data or empty returns flowing to consumers.

## Threat Flags

No new threat surface beyond the threat model:
- `bot/safety/logger.py` file write to `logs/bot.log` → covered by T-01-15 (no credentials logged — implemented).
- `bot/safety/kill_switch.py` reads sentinel file path and writes audit entry → T-01-12 (graceful shutdown — implemented), T-01-13 (append-only audit — implemented).

## Self-Check: PASSED

- [x] `bot/safety/et_helpers.py` exists and contains `America/New_York` via ZoneInfo, defines `now_et` and `to_et`
- [x] `grep -n "pytz\|timedelta(hours=" bot/safety/et_helpers.py` returns nothing
- [x] `bot/safety/logger.py` defines `configure_logging` and `get_logger`, imports `structlog`, configures `RotatingFileHandler`
- [x] `bot/safety/kill_switch.py` defines `class KillSwitch`, references `signal.SIGINT`, imports `append_audit` from `bot.safety.audit_log`
- [x] `grep -n "SIGINT" bot/safety/kill_switch.py` matches
- [x] `grep -n "append_audit" bot/safety/kill_switch.py` matches
- [x] `python3 -m pytest tests/safety/test_et_helpers.py tests/safety/test_logger.py tests/safety/test_kill_switch.py -q` exits 0 (39 passed)
- [x] Commits 0c38a57 and 005d060 exist in git log
