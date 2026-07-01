---
phase: 01
slug: foundation
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-23
---

# Phase 01 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Reconstructed retroactively from PLAN/SUMMARY/VERIFICATION artifacts (State B).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio |
| **Config file** | `tests/conftest.py` (shared fixtures; no pytest.ini/pyproject) |
| **Quick run command** | `python3 -m pytest tests/<subdir> -q` |
| **Full suite command** | `python3 -m pytest tests/ -q` |
| **Estimated runtime** | ~0.3 seconds (217 tests) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/<subdir> -q` for the touched subsystem
- **After every plan wave:** Run `python3 -m pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~1 second

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | (scaffolding) | — | Package tree + deps importable; no `skills/` import | infra | `python3 -m pytest tests/ -q` | ✅ | ✅ green |
| 01-01-02 | 01 | 1 | SAFE-01, SAFE-05 | T-01-02 | Triple fail-closed guard raises `PaperGuardError` (never sys.exit); append-only audit preserves prior entries | unit | `python3 -m pytest tests/safety/test_paper_guard.py tests/safety/test_audit_log.py -q` | ✅ | ✅ green |
| 01-01-03 | 01 | 1 | SAFE-02, SAFE-03 | T-01-05 | `connect()` invokes `assert_paper_account` as hard gate; reconcile skeletons are coroutines; loop interval in 60–90s | unit | `python3 -m pytest tests/gateway/test_gateway.py -q` | ✅ | ✅ green |
| 01-02-01 | 02 | 1 | STATE-01 | T-01-08 | PRAGMA user_version migration creates 5-table v1 schema; idempotent; UNIQUE constraints enforced | unit | `python3 -m pytest tests/state/test_migrations.py -q` | ✅ | ✅ green |
| 01-02-02 | 02 | 1 | STATE-01 | T-01-06, T-01-07 | Atomic temp-file + fsync + os.replace; parse-validate before swap; 0600 perms; crash-injection leaves original intact | unit | `python3 -m pytest tests/state/test_store.py tests/state/test_atomic_write.py -q` | ✅ | ✅ green |
| 01-03-01 | 03 | 1 | CFG-01 | T-01-09 | rules.json loads via jsonschema; malformed/missing config raises `ConfigError` (fail-fast) | unit | `python3 -m pytest tests/config/test_loader.py -q` | ✅ | ✅ green |
| 01-03-02 | 03 | 1 | CFG-01 | — | Pure SMA/RVOL/swing_low_2_2; RVOL strict `date < signal_date` no-look-ahead; zero network calls | unit | `python3 -m pytest tests/strategy/test_indicators.py -q` | ✅ | ✅ green |
| 01-03-03 | 03 | 1 | CFG-01 | — | StrategyCore ABC + TrendJoinLong read every threshold from config (D-12 behavioral swap proof; no bare literals) | unit | `python3 -m pytest tests/strategy/test_config_driven.py -q` | ✅ | ✅ green |
| 01-04-01 | 04 | 1 | SVC-03, SVC-04 | T-01-15 | ET = zoneinfo America/New_York DST-correct (-4h/-5h); rotating JSON logger; no credentials logged | unit | `python3 -m pytest tests/safety/test_et_helpers.py tests/safety/test_logger.py -q` | ✅ | ✅ green |
| 01-04-02 | 04 | 1 | SAFE-04, SAFE-05 | T-01-12, T-01-13 | KillSwitch sentinel + SIGINT → Event + flush-once + append-only `kill_switch` audit; idempotent | unit | `python3 -m pytest tests/safety/test_kill_switch.py -q` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. The test suite (217 tests across 12 files) and shared fixtures in `tests/conftest.py` (`tmp_state_db`, `minimal_rules`, `mock_trade_ctx`) were created inline during plan execution. No additional Wave 0 scaffolding required.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end paper guard against a live OpenD instance | SAFE-01 | The full broker flow (real OpenD → `get_acc_list()` → DataFrame with `trd_env` → guard evaluation) cannot run in CI without a live OpenD daemon. Unit tests use a mocked trade context. | With OpenD running and logged into a SIMULATE paper account, call `MoomooGateway.connect()` with `PAPER_TRADING=true`, `FUTU_TRD_ENV=SIMULATE`, and the correct `FUTU_ACC_ID` → expect success. Repeat with `PAPER_TRADING=false` (or a mismatched/REAL account) → expect `PaperGuardError` raised before any order API, plus a `paper_guard_refusal` entry in `~/.futu_trade_audit.jsonl`. |
| KillSwitch graceful shutdown under real OS signal delivery | SAFE-04 | WR-01 (SIGINT reentrancy on a locked path) is mitigated by `threading.RLock()` in code, but real signal delivery under asyncio's event-loop thread model cannot be fully exercised by calling `_handle_signal()` directly in a unit test. | Start an asyncio loop that calls `ks.install()`, registers a flush callback, and polls `ks.triggered`. Send SIGINT (Ctrl-C or `kill -INT <pid>`) while the loop is active → expect clean shutdown with no hang/deadlock, flush callback runs exactly once, and a `kill_switch` audit entry is written. |

---

## Validation Sign-Off

- [x] All tasks have automated verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (none — existing infra covers all)
- [x] No watch-mode flags
- [x] Feedback latency < 1s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-06-23

---

## Validation Audit 2026-06-23

| Metric | Count |
|--------|-------|
| Requirements audited | 9 (SAFE-01/02/03/04/05, STATE-01, CFG-01, SVC-03, SVC-04) |
| Tasks mapped | 10 |
| Automated (COVERED) | 10 |
| Gaps found (MISSING/PARTIAL) | 0 |
| Tests generated this audit | 0 (existing suite already complete) |
| Manual-only items | 2 (live-OpenD E2E; real-signal kill switch) |
| Full suite result | 217 passed in ~0.3s |

**Outcome:** Phase 01 is Nyquist-compliant. Every requirement and the pure strategy indicators have automated, green verification at task-level resolution. The two manual-only items require a live OpenD daemon and real OS signal delivery respectively — neither is automatable in CI and both are integration smoke tests, not coverage gaps.
