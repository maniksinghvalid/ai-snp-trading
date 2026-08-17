---
phase: 9
slug: options-backtester
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-17
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (project standard) |
| **Config file** | none dedicated — project-root conventions govern `tests/backtester/options/` |
| **Quick run command** | `pytest tests/backtester/options/ -x -q` |
| **Full suite command** | `pytest -q` (965 baseline as of b36af60) |
| **Estimated runtime** | ~5 s quick / ~90 s full |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/backtester/options/ -x -q`
- **After every plan wave:** Run `pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 09-xx | TBD | TBD | OBT-01 | — | N/A | unit | `pytest tests/backtester/options/test_engine.py::test_imports_not_copies -x` | ❌ W0 | ⬜ pending |
| 09-xx | TBD | TBD | OBT-02 | — | API key only via Bearer header, never logged/URL | unit | `pytest tests/backtester/options/test_data.py -x` | ❌ W0 | ⬜ pending |
| 09-xx | TBD | TBD | OBT-03 | — | N/A | unit | `pytest tests/backtester/options/test_greeks.py::test_iv_roundtrip tests/backtester/options/test_greeks.py::test_delta_monotonic -x` | ❌ W0 | ⬜ pending |
| 09-xx | TBD | TBD | OBT-04 | — | N/A | unit | `pytest tests/backtester/options/test_greeks.py::test_ivr_fixture -x` | ❌ W0 | ⬜ pending |
| 09-xx | TBD | TBD | OBT-05 | — | N/A | unit | `pytest tests/backtester/options/test_engine.py::test_fill_and_settlement -x` | ❌ W0 | ⬜ pending |
| 09-xx | TBD | TBD | OBT-06 | — | N/A | manual (git history) | `git log --diff-filter=A -- 'docs/research/*options-backtest-hypotheses.md'` predates first results commit | ❌ W0 (doc) | ⬜ pending |
| 09-xx | TBD | TBD | OBT-07 | — | N/A | unit | `pytest tests/backtester/options/test_engine.py::test_report_output_shape -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*The planner fills Task ID / Plan / Wave columns; execution updates Status.*

---

## Wave 0 Requirements

- [ ] `tests/backtester/options/__init__.py`, `conftest.py` — option-chain-grid fixture (adapt `tests/options/test_strategy.py::_grid` for row-shape parity), Massive `_get_json` monkeypatch fixture (copy `tests/backtester/test_massive.py` style)
- [ ] `tests/backtester/options/test_data.py` — stubs for OBT-02
- [ ] `tests/backtester/options/test_greeks.py` — stubs for OBT-03/04
- [ ] `tests/backtester/options/test_engine.py` — stubs for OBT-01/05/07
- [ ] `tests/backtester/options/test_options_run.py` — CLI smoke (no network)
- [ ] `backtester/options/__init__.py`, `data.py`, `greeks.py`, `engine.py`, `backtester/options_run.py` — modules under test (Wave 0/1 of the phase itself)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Hypotheses doc committed before first real-data run | OBT-06 | Git-history ordering, not a pytest assertion | `git log --diff-filter=A --format=%h%x20%ad -- 'docs/research/*options-backtest-hypotheses.md'` and compare against the first `backtester/results/options/` results-doc commit |
| Massive ~24-month aggregates cutoff still holds at Wave 0 | OBT-02 | Plan-tier dependent, live API | Re-run the research probe (one 2024-01 and one 2024-09 SPY contract) before designing IS/OOS windows |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
