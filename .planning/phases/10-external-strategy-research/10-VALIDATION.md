---
phase: 10
slug: external-strategy-research
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-18
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.24 (`requirements-dev.txt`) |
| **Config file** | none — no `pytest.ini`/`pyproject.toml`; convention-based (`tests/`) |
| **Quick run command** | `python3 -m pytest tests/backtester/experimental/ -q` |
| **Full suite command** | `python3 -m pytest -q` |
| **Estimated runtime** | ~1040 existing tests green today; the new `tests/backtester/experimental/` suite (~40 tests, per the design doc) adds a few seconds — full suite stays well under a minute |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/backtester/experimental/ -q`
- **After every plan wave:** Run `python3 -m pytest -q` (full suite — must stay green; `bot/` and existing `backtester/` tests must show zero regressions)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

*Filled in by the planner once task IDs exist (Wave 1–5 tasks per `10-01..10-06-PLAN.md`). Requirements in scope: XSR-01..XSR-06. No threat-model refs expected beyond the standard security gate (this phase is offline, read-only against cached market data — no new network/auth/input-trust boundary).*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| *(planner fills)* | 10-01 | 1 | XSR-03 | — | Pre-registration doc + arms.json committed before any run artifact exists | manual (git log check) | `git log --diff-filter=A --format=%H -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` | ✅ | ⬜ pending |
| *(planner fills)* | 10-02 | 2 | XSR-02 | — | No look-ahead in signal generation (prefix-invariance) | unit | `pytest tests/backtester/experimental/test_strategies.py -q` | ❌ W1 | ⬜ pending |
| *(planner fills)* | 10-02 | 2 | XSR-02 | — | Fills happen at N+1 bar open, never the signal bar's close | unit | `pytest tests/backtester/experimental/test_engine.py -q` | ❌ W1 | ⬜ pending |
| *(planner fills)* | 10-03 | 2 | XSR-04 | — | `run.py` refuses to fetch when cache is missing (cache-only guard) | unit | `pytest tests/backtester/experimental/test_run.py -q` (or equivalent CLI test) | ❌ W2 | ⬜ pending |
| *(planner fills)* | 10-04 | 3 | XSR-04 | — | Every arm × window produces `summary.json` with the evidence-floor trade count recorded | manual | inspect `backtester/results/experimental/runs/*/*/*/summary.json` | ✅ | ⬜ pending |
| *(planner fills)* | 10-05 | 4 | XSR-05 | — | Results doc reports SUPPORTED/REJECTED/INSUFFICIENT-EVIDENCE per H1–H8 with numbers | manual | review `docs/research/2026-08-18-external-strategies-results.md` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/backtester/experimental/__init__.py` + package scaffold — stubs for XSR-02
- [ ] `tests/backtester/experimental/conftest.py` (if needed) — reuse `tests/backtester/fixtures.py`'s `recent_session_days`/`make_ahead_only_5m_dataset`/`make_trade_log` rather than re-deriving fixtures
- [ ] No new framework install required — pytest/pytest-asyncio already present

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|--------------------|
| Cache-only discipline held across all Wave 3 runs | XSR-04 | Requires inspecting run logs / network absence, not a unit-testable property | Grep all `backtester/experimental/run.py` invocation logs for `[fetch]`/HTTP lines (expect none); confirm `backtester/results/options/warm-cache-pool.pid` process is still alive after the run batch |
| Pre-registration commit precedes any result artifact | XSR-03 | Git-history ordering, not code behavior | `git log --diff-filter=A --format="%H %ad" -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` must show a commit timestamp before the first commit touching `backtester/results/experimental/` (if that dir is ever committed) or before the first local run timestamp in `params.json` |
| Every hypothesis verdict is numerically justified | XSR-05 | Judgment call on evidence floor / metric thresholds, not purely mechanical | Cross-check each H1–H8 verdict in the results doc against the raw `aggregate.py` output (`results.csv`) for that arm/window |
| Wave 5 (conditional) production-integration branch, if created | XSR-06 | Branch existence/scope is a repo-state check, not a unit test | `git log --oneline feature/phase10-<arm> ^develop` shows the integration commits; confirm `develop` itself has zero new commits from this phase |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
