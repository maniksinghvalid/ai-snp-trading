---
phase: 01-foundation
verified: 2026-06-23T18:43:00Z
status: human_needed
score: 6/6 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Connect MoomooGateway to a running OpenD instance (or a mock that serves SIMULATE accounts) and confirm that PaperGuardError fires when PAPER_TRADING=false or when the account has trd_env=REAL"
    expected: "The process reaches the guard, raises PaperGuardError before any order API is callable, and writes a paper_guard_refusal entry to ~/.futu_trade_audit.jsonl"
    why_human: "Live OpenD is not running in CI; the test suite mocks the broker. End-to-end wiring of the real SDK through the guard cannot be verified without an OpenD daemon."
  - test: "Trigger the kill switch via the sentinel file during an active asyncio event loop (with registered flush callbacks) and observe graceful shutdown"
    expected: "Event loop shuts down cleanly; all registered flush callbacks run exactly once; kill_switch audit entry is present in the JSONL log; no deadlock or hang occurs"
    why_human: "WR-01 deadlock risk on SIGINT-vs-lock-holder path requires observing real signal delivery. RLock fix is verified in code but signal reentrancy under load cannot be exercised by unit tests alone."
---

# Phase 01: Foundation Verification Report

**Phase Goal:** The broker access layer, durable state store, and pure strategy logic are in place — independently tested — and the environment safety gate is enforced at startup
**Verified:** 2026-06-23T18:43:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth | Status | Evidence |
| --- | ----- | ------ | -------- |
| 1 | MoomooGateway connects to OpenD on 127.0.0.1:11111 and the hard paper guard (SAFE-01) passes only when PAPER_TRADING=true AND the selected account asserts SIMULATE; REAL account or mismatch hard-exits before any order path | ✓ VERIFIED | `bot/gateway/gateway.py:265-269` — `connect()` calls `_check_opend_alive()` then `assert_paper_account()` as the hard gate. `bot/safety/paper_guard.py` implements all three independent guards (flag + env + broker trd_env via RET_OK). 29 gateway tests pass. |
| 2 | StateStore creates and migrates the SQLite schema; atomic writes (temp-file + os.replace) pass a crash-injection test without corruption | ✓ VERIFIED | `bot/state/migrations.py` creates all 5 v1 tables via PRAGMA user_version; `bot/state/store.py:85` — `os.fsync(f.fileno())` present before `os.replace`; spot-check confirms tables and user_version=1. 51 state tests pass including crash-injection. |
| 3 | TrendJoinLong indicator functions (SMA200, RVOL, swing_low_2_2, D1/D2/D3 filters) pass unit tests against synthetic DataFrames with no network calls | ✓ VERIFIED | `bot/strategy/indicators.py` — `sma`, `rvol` (strict `date < signal_date` cutoff verified), `swing_low_2_2`. `bot/strategy/trend_join_long.py` — D1/D2/D3 + I1/I2/I3. Behavioral spot-check confirmed: 5.0 RVOL with no look-ahead bias. 44 strategy tests pass. |
| 4 | rules.json (CFG-01) loads and validates at startup; every strategy constant is read from it — unit test confirms no strategy literal is hardcoded in StrategyCore, and a malformed/missing config fails fast | ✓ VERIFIED | `rules.json` at repo root contains all 6 parameter groups. `bot/config/loader.py:163` — `parse_initial_stop_rule(str(ex["initial_stop_rule"]))` maps the stop rule field; `StrategyConfig.initial_stop_pct` is a dedicated stop field decoupled from `max_risk_per_trade_pct` (CR-01 fix confirmed). D-12 behavioral swap test passes: same bar yields different `passes_daily_filters` result under baseline vs modified config. 23 config tests pass. |
| 5 | Startup reconciliation skeleton (SAFE-02) and the 60–90s reconciliation loop (SAFE-03) are wired into the gateway; broker truth overrides in-memory state | ✓ VERIFIED | `bot/gateway/gateway.py:305-357` — `reconcile_once()` (async, returns dict, calls `get_positions()` + `get_acc_list()` via executor) and `reconciliation_loop(interval_s=75.0)` (within 60-90s, with exception handling per WR-03 fix). Phase 4 marker comments present. Both are coroutines (verified programmatically). |
| 6 | Kill switch (file-touch and SIGINT) triggers clean shutdown with a state flush; append-only JSONL audit log records every event without overwriting prior entries | ✓ VERIFIED | `bot/safety/kill_switch.py` — RLock (WR-01 fix), idempotent via `_triggered_once`, `register_flush`, `install()` wires `signal.SIGINT`, `append_audit` reused from audit_log. Spot-check confirmed: trigger fires callbacks once, writes 1 kill_switch entry; second trigger is no-op. 70 safety tests pass. |

**Score:** 6/6 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `bot/gateway/gateway.py` | MoomooGateway + GatewayConfig + GatewayError + reconcile skeletons | ✓ VERIFIED | 358 lines, all exports present, no skills imports, no sys.exit |
| `bot/safety/paper_guard.py` | assert_paper_account triple fail-closed + PaperGuardError | ✓ VERIFIED | All 3 guards implemented, uses RET_OK (not bare 0), raises not exits |
| `bot/safety/audit_log.py` | append-only JSONL audit writer | ✓ VERIFIED | `"a"` mode, UTC timestamp, ensure_ascii=False, silent-on-failure |
| `bot/_utils.py` | null-safe accessors (safe_get, safe_float, safe_int, format_enum, safe_close) | ✓ VERIFIED | All 5 helpers present, no skills/ import |
| `bot/state/migrations.py` | PRAGMA user_version runner + full v1 schema | ✓ VERIFIED | 5 tables (positions, trades, daily_scan, bar_cache, meta), CURRENT_VERSION=1, idempotent |
| `bot/state/store.py` | StateStore + atomic_write_json + resolve_db_path | ✓ VERIFIED | tempfile.mkstemp + fsync + os.replace + fsync dir + json.load validate before swap |
| `bot/config/loader.py` | load_strategy_config + StrategyConfig + ConfigError | ✓ VERIFIED | jsonschema.validate wired; ConfigError on missing/malformed/schema-invalid; initial_stop_pct field present |
| `bot/config/schema.py` | SCHEMA (jsonschema dict for rules.json) | ✓ VERIFIED | All 6 top-level groups required; typed fields |
| `bot/strategy/indicators.py` | sma, rvol (no look-ahead), swing_low_2_2 | ✓ VERIFIED | RVOL strict `date < signal_date` cutoff; no I/O imports |
| `bot/strategy/core.py` | StrategyCore ABC | ✓ VERIFIED | abc.ABC with 4 @abstractmethod hooks |
| `bot/strategy/trend_join_long.py` | TrendJoinLong — all params from StrategyConfig | ✓ VERIFIED | 7 self._cfg accesses; `initial_stop_pct` used (not max_risk_per_trade_pct); no bare numeric literals as thresholds |
| `bot/safety/et_helpers.py` | DST-correct ET helpers via zoneinfo | ✓ VERIFIED | ZoneInfo("America/New_York"), no pytz, no fixed offsets; DST spot-checked |
| `bot/safety/logger.py` | structlog rotating-file + stderr config | ✓ VERIFIED | RotatingFileHandler, ConsoleRenderer, contextvars, no credentials logged |
| `bot/safety/kill_switch.py` | KillSwitch sentinel + SIGINT → Event + flush + audit | ✓ VERIFIED | RLock (WR-01 fix), idempotent, imports append_audit (not redefines) |
| `rules.json` | CFG-01 canonical strategy config | ✓ VERIFIED | All 6 groups, strategy_name: "Trend Join Long", initial_stop_rule: "lod_minus_1pct" |
| `requirements.txt` | Runtime deps pinned | ✓ VERIFIED | moomoo-api, pandas, numpy, jsonschema, structlog; no APScheduler/telegram |
| `requirements-dev.txt` | Dev deps with -r requirements.txt | ✓ VERIFIED | pytest, pytest-asyncio, -r requirements.txt |
| `.gitignore` | data/, __pycache__ | ✓ VERIFIED | Present |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `bot/safety/paper_guard.py` | `trade_ctx.get_acc_list()` | broker account-type read for configured acc_id | ✓ WIRED | Line 91: `ret, data = trade_ctx.get_acc_list()` |
| `bot/safety/paper_guard.py` | `bot/safety/audit_log.py` | append_audit on guard refusal | ✓ WIRED | Line 52-56: `_fail()` calls `append_audit({"event": "paper_guard_refusal", ...})` |
| `bot/gateway/gateway.py` | `bot/safety/paper_guard.py` | assert_paper_account invoked at connect() | ✓ WIRED | Line 269: `assert_paper_account(self.cfg, self._trade_ctx)` |
| `bot/state/store.py` | `bot/state/migrations.py` | run_migrations(conn) called on open() | ✓ WIRED | Line 198: `run_migrations(self._conn)` |
| `bot/state/store.py` | `os.replace` | atomic temp-file swap in atomic_write_json | ✓ WIRED | Line 93: `os.replace(tmp, path)` |
| `bot/config/loader.py` | `jsonschema.validate` | validate rules.json against SCHEMA at load | ✓ WIRED | Line 133: `jsonschema.validate(data, SCHEMA)` |
| `bot/strategy/trend_join_long.py` | `StrategyConfig` | all D1/D2/D3 + intraday + exit params read from cfg | ✓ WIRED | 7 `self._cfg.` accesses; stop uses `initial_stop_pct` not `max_risk_per_trade_pct` |
| `bot/safety/kill_switch.py` | `signal.SIGINT` | signal.signal(SIGINT, handler) | ✓ WIRED | Line 116: `signal.signal(signal.SIGINT, self._handle_signal)` |
| `bot/safety/kill_switch.py` | `bot/safety/audit_log.py` | append_audit shutdown event on trigger | ✓ WIRED | Line 183-187: `append_audit({"event": "kill_switch", ...})` |
| `bot/safety/logger.py` | `structlog` | configure_logging wires rotating file + renderer | ✓ WIRED | RotatingFileHandler + ConsoleRenderer + ProcessorFormatter chain |

### Data-Flow Trace (Level 4)

Data-flow tracing is not applicable to this phase. All artifacts are either:
- Pure function libraries (indicators, strategy) with no external data source
- Safety/infrastructure modules that operate on in-process data (audit log writes, config loader reads local file)
- Skeleton stubs with explicitly documented Phase 4 payloads (reconcile_once drift field)

No component in Phase 1 renders dynamic external data to a user-visible output; data flows are library primitives, not rendering pipelines.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| All 217 tests pass | `python3 -m pytest tests/ -q` | 217 passed in 0.31s | ✓ PASS |
| rules.json loads with correct values | `load_strategy_config('rules.json')` | min_price_usd=3.0, d3_min_gap_pct=3.0, rvol_min=2.0, initial_stop_pct=1.0, force_close_et=15:51 | ✓ PASS |
| compute_initial_stop uses config (CR-01 fix) | `TrendJoinLong(cfg).compute_initial_stop(100.0)` | 99.0 (lod*0.99 from initial_stop_pct=1.0) | ✓ PASS |
| RVOL excludes current-day volume | `rvol(frame, signal_date, 14, 5_000_000)` | 5.0 (denominator uses only 14 prior days at 1M each) | ✓ PASS |
| StateStore creates v1 schema | `StateStore(tmp).open().conn` | 5 tables present, user_version=1 | ✓ PASS |
| Audit log is append-only | Two `append_audit()` calls | 2 lines, first line unchanged | ✓ PASS |
| KillSwitch idempotent + audit | `trigger()` twice | 1 flush callback invocation, 1 audit entry | ✓ PASS |
| ET helpers are DST-correct | `to_et(july_utc)` and `to_et(jan_utc)` | -4h (EDT) and -5h (EST) | ✓ PASS |
| D-12 config-drivenness behavioral proof | Baseline vs modified config, same input bar | Baseline passes, modified (gap 6.0%) fails — output differs | ✓ PASS |
| No skills/ imports in bot/ | `grep -rn "from skills\|import skills" bot/` | No matches (docstring reference only — not an import) | ✓ PASS |
| No sys.exit in library code | `grep -rn "sys.exit" bot/gateway/... bot/safety/paper_guard.py ...` | No matches | ✓ PASS |

### Probe Execution

Not applicable — no probe-*.sh scripts declared for this phase.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| CFG-01 | 01-03 | All strategy parameters externalized to rules.json; no strategy constants hardcoded | ✓ SATISFIED | rules.json loads via jsonschema validation; D-12 behavioral test passes; `parse_initial_stop_rule` maps stop rule field to `initial_stop_pct` (CR-01 fix) |
| STATE-01 | 01-02 | Durable SQLite state with atomic writes for positions, stops, scans, trades | ✓ SATISFIED | 5-table schema created via PRAGMA user_version; atomic_write_json uses fsync + os.replace + parse-validate; crash-injection tests pass |
| SAFE-01 | 01-01 | Hard paper-trading guard at startup — triple fail-closed | ✓ SATISFIED | assert_paper_account checks flag + FUTU_TRD_ENV + broker trd_env; uses RET_OK (WR-04 fix); raises PaperGuardError, never sys.exit |
| SAFE-02 | 01-01 | Startup reconciliation skeleton | ✓ SATISFIED (skeleton) | `reconcile_once()` is an async coroutine that reads broker positions and accounts; Phase 4 full logic marker present. REQUIREMENTS.md explicitly documents "Skeleton (01-01); full logic Phase 4" |
| SAFE-03 | 01-01 | 60–90s reconciliation loop skeleton | ✓ SATISFIED (skeleton) | `reconciliation_loop(interval_s=75.0)` loops with exception handling (WR-03 fix); CancelledError re-raised. REQUIREMENTS.md: "Skeleton (01-01); full logic Phase 4" |
| SAFE-04 | 01-04 | Kill switch triggers clean shutdown with state flush | ✓ SATISFIED | KillSwitch — sentinel file + SIGINT → Event + flush callbacks + audit; RLock (WR-01 fix); idempotent |
| SAFE-05 | 01-01, 01-04 | Append-only trade/order audit log (JSONL) | ✓ SATISFIED | audit_log.py uses "a" mode; paper_guard_refusal and kill_switch events both append; prior entries preserved |
| SVC-03 | 01-04 | Structured, rotating application logging | ✓ SATISFIED | structlog 26.1.0 with RotatingFileHandler (10MB×5), ConsoleRenderer to stderr, contextvars chain |
| SVC-04 | 01-04 | All timing uses US Eastern (zoneinfo), DST-correct | ✓ SATISFIED | ET = ZoneInfo("America/New_York"); DST spot-checked (-4h summer, -5h winter); no pytz or fixed offsets |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `bot/strategy/trend_join_long.py` | 70 | `if prior_close == 0.0:` | ℹ️ Info | Division guard for gap_pct calculation; not a stub — correctly returns False for zero-price. |
| `bot/gateway/gateway.py` | 323,330 | `# Phase 4: full reconciliation logic` and `"drift": {}` | ℹ️ Info | Intentional skeleton stub for reconcile_once() drift payload; explicitly scoped to Phase 4 per REQUIREMENTS.md and plan spec. Not a BLOCKER — the wiring (async, broker calls, returns dict) is real. |
| `bot/safety/et_helpers.py` | 42 | `to_et()` treats naive datetimes as UTC | ℹ️ Info | WR-05: documented design decision ("Naive datetime passed to to_et() treated as UTC — explicit, consistent with PITFALLS #6"). Deferred robustness item from 01-REVIEW.md. Not a blocker — the convention is explicit and documented. |
| `bot/strategy/indicators.py` | 40-43 | `sma` returns NaN for insufficient data | ℹ️ Info | IN-02: NaN from sma() is indistinguishable from failed filter. Deferred robustness item from 01-REVIEW.md. Does not block goal — no test exercises insufficient-history path in a way that breaks strategy behavior. |

No TBD/FIXME/XXX markers found in any bot/ source file.

All INFO-level items are documented in 01-REVIEW.md as deferred robustness items (WR-02/WR-05/WR-06/IN-01 through IN-05). The two BLOCKER items from the code review (CR-01: wrong config field for stop; CR-02: missing fsync) have been verified as FIXED:
- CR-01 fixed: `compute_initial_stop` uses `self._cfg.initial_stop_pct` (not `max_risk_per_trade_pct`); `parse_initial_stop_rule()` maps the dedicated stop field
- CR-02 fixed: `atomic_write_json` calls `f.flush()` + `os.fsync(f.fileno())` before `os.replace`, then `os.fsync(dir_fd)` after

WR-01 fixed: `threading.RLock()` used in `KillSwitch` (not plain `Lock`)
WR-03 fixed: `reconciliation_loop` wraps `reconcile_once()` in try/except, re-raises `CancelledError`
WR-04 fixed: `paper_guard.py` imports and uses `RET_OK` from moomoo SDK (not bare `0`)

WR-02/WR-05/WR-06 and INFO items remain as documented deferred robustness items, per the verification instruction.

### Human Verification Required

#### 1. End-to-end paper guard with real OpenD

**Test:** With OpenD running and logged in to a SIMULATE paper account, run a small script that calls `MoomooGateway.connect()` with `PAPER_TRADING=true` and `FUTU_TRD_ENV=SIMULATE` and the correct `FUTU_ACC_ID`. Then repeat with `PAPER_TRADING=false` (or a mismatched account).

**Expected:** Pass case — `connect()` returns without raising; the gateway is usable. Fail case — `PaperGuardError` is raised before `connect()` returns; a `paper_guard_refusal` entry appears in `~/.futu_trade_audit.jsonl`; no order API was called.

**Why human:** The full broker flow (real OpenD → `get_acc_list()` → DataFrame with `trd_env` field → guard evaluation) cannot be exercised without a live OpenD daemon. Unit tests use mocked trade contexts. The guard logic is proven correct in isolation but the integration path needs one end-to-end smoke test.

#### 2. KillSwitch graceful shutdown under real signal delivery

**Test:** Start a small asyncio event loop that has `ks.install()` called, registers a flush callback, and polls `ks.triggered`. Send SIGINT (Ctrl-C or `kill -INT <pid>`) from a terminal while the loop is running (not idle).

**Expected:** Loop shuts down cleanly without hanging; the flush callback runs exactly once; a `kill_switch` audit entry is written; no deadlock or infinite wait occurs.

**Why human:** WR-01 (SIGINT reentrancy on locked path) is mitigated by `threading.RLock()` in code, but real signal delivery under asyncio's event-loop thread model cannot be fully simulated by calling `_handle_signal(signal.SIGINT, None)` directly in a test. The real test is sending the OS signal to a running process.

---

## Gaps Summary

No gaps. All 6 success criteria are verified in the codebase. The two code-review BLOCKERs (CR-01, CR-02) and two of the three code-review WARNINGs (WR-01, WR-03, WR-04) have been fixed and confirmed in the source. The two human verification items above are integration-level smoke tests that require a running OpenD instance — they do not block the phase goal, which is met in full by the test-proven code.

---

_Verified: 2026-06-23T18:43:00Z_
_Verifier: Claude (gsd-verifier)_
