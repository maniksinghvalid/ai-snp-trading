---
phase: 9
slug: options-backtester
status: planned
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-17
updated: 2026-08-17
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
| T-09-01 | 09-01 | 1 | OBT-01, OBT-02 | T-09-E1 | data.py imports no broker/gateway symbol | unit | `pytest tests/backtester/options/test_data.py -x -q` | ✅ backtester/options/data.py | ✅ green |
| T-09-02 | 09-01 | 1 | OBT-02 | T-09-I1, T-09-T1, T-09-D1 | API key Bearer-header only, never in URL; cache filenames regex-guarded against traversal; 429 backoff + read-through cache | unit | `pytest tests/backtester/options/test_data.py tests/backtester/test_massive.py -x -q` | ✅ backtester/massive.py | ✅ green |
| T-09-03 | 09-01 | 1 | OBT-02 | — | N/A (no-look-ahead correctness, not security) | unit | `pytest tests/backtester/options/test_data.py::test_no_lookahead_contracts_for_day -x -q` | ✅ backtester/options/data.py | ✅ green |
| T-09-04 | 09-02 | 2 | OBT-03 | T-09-D1 | bounded bisection (`max_iter=100`) cannot spin | unit | `pytest tests/backtester/options/test_greeks.py::test_iv_roundtrip tests/backtester/options/test_greeks.py::test_delta_monotonic -x -q` | ✅ backtester/options/greeks.py | ✅ green |
| T-09-05 | 09-02 | 2 | OBT-04 | — | N/A | unit | `pytest tests/backtester/options/test_greeks.py::test_ivr_fixture -x -q` | ✅ backtester/options/greeks.py | ✅ green |
| T-09-06 | 09-02 | 2 | OBT-06 | T-09-R1 | pre-registration provable from git history, not back-datable | manual (git history) | `git log --diff-filter=A --format=%h -- 'docs/research/*options-backtest-hypotheses.md' \| grep -q .` | ✅ docs/research/2026-08-17-options-backtest-hypotheses.md | ✅ green |
| T-09-07 | 09-03 | 3 | OBT-01 | T-09-E1 | engine imports no `bot.gateway`/`moomoo`/`place_order` | unit | `pytest tests/backtester/options/test_engine.py::test_imports_not_copies tests/backtester/options/test_engine.py::test_no_broker_imports -x -q` | ✅ backtester/options/engine.py | ✅ green |
| T-09-08 | 09-03 | 3 | OBT-05 | — | N/A | unit | `pytest tests/backtester/options/test_engine.py::test_fill_and_settlement -x -q` | ✅ backtester/options/engine.py | ✅ green |
| T-09-09 | 09-03 | 3 | OBT-07 | T-09-T1 | stock-shaped P&L pipeline provably not in the call path | unit | `pytest tests/backtester/options/test_report.py -x -q` | ✅ backtester/options/report.py | ✅ green |
| T-09-10 | 09-04 | 4 | OBT-01, OBT-07 | T-09-T2, T-09-E1 | `--set` never rewrites `rules_options.json`; CLI constructs no gateway/StateStore | unit | `pytest tests/backtester/options/test_options_run.py -x -q` | ✅ backtester/options_run.py | ✅ green (8 passed) |
| T-09-11 | 09-04 | 4 | OBT-06 | T-09-R1, T-09-D1 | hypotheses commit precedes every result artifact; backfill has a wall-clock stop budget | manual (git order) + artifact check | ordering gate PASS (`a62c0af` precedes all artifacts); wall-clock stop budget INVOKED — see below | N/A (no arm runs produced; infeasibility documented) | ⚠️ stopped-per-budget (see note) |
| T-09-12 | 09-04 | 4 | OBT-07 | T-09-T2 | `rules_options.json` changes only for a SUPPORTED verdict, one key max | integration + doc | `grep -Ec 'H[123].*(SUPPORTED\|REJECTED\|INSUFFICIENT-EVIDENCE)' docs/research/*-options-backtest-results.md && pytest -q` | ✅ docs/research/2026-08-17-options-backtest-results.md | ✅ green (3 verdicts, 1027 passed 1 skipped, rules_options.json unchanged) |

**T-09-11 note (stopped-per-budget, not a red status):** the plan's own `<action>` text
pre-authorizes stopping and reporting the observed rate rather than grinding past ~2 hours of
wall clock. SPY's IS-window strike-band-narrowed candidate count (58,366-79,750 depending on
band width, measured from the live-fetched contracts-reference cache) combined with the
measured live fetch rate (5.3-5.8 req/min, two independent measurements) projects to ~183
hours for one arm's one window — ~90x the budget. The RESEARCH.md Open Question #3 escape
hatch (grouped-daily options endpoint) was probed once, live: HTTP 400 (not available). One
real end-to-end smoke run (`backtester/results/options/smoke-real-data`) completed
successfully against live Massive data (330 real contract fetches, exit 0), satisfying
success_criteria #1 (pipeline correctness) without attempting the infeasible full backfill.
Full detail: `docs/research/2026-08-17-options-backtest-results.md`.

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*The planner filled Task ID / Plan / Wave; execution updates Status.*

---

## Wave 0 Requirements

All Wave 0 scaffolding is owned by **T-09-01** (Plan 09-01, Wave 1) — it creates the tests package,
shared fixtures and the first test module before any other Phase 9 test lands. Remaining test modules
are created by the task whose behaviour they cover (each task's `<verify><automated>` names the file
it creates), so no task ships without a runnable check.

- [x] `tests/backtester/options/__init__.py`, `conftest.py` (chain-grid fixture adapted from `tests/options/test_strategy.py::_grid`; `fake_massive` `_get_json` monkeypatch fixture from `tests/backtester/test_massive.py`) — **T-09-01**
- [x] `tests/backtester/options/test_data.py` — **T-09-01** (extended by T-09-02, T-09-03)
- [x] `tests/backtester/options/test_greeks.py` — **T-09-04** (extended by T-09-05)
- [x] `tests/backtester/options/test_engine.py` — **T-09-07** (extended by T-09-08)
- [x] `tests/backtester/options/test_report.py` — **T-09-09**
- [x] `tests/backtester/options/test_options_run.py` — **T-09-10**
- [x] `backtester/options/{__init__,data,greeks,engine,report}.py` — [x] `backtester/options_run.py` — modules under test (Plans 09-01 → 09-04)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Hypotheses doc committed before first real-data run | OBT-06 | Git-history ordering, not a pytest assertion | `git log --diff-filter=A --format=%h%x20%ad -- 'docs/research/*options-backtest-hypotheses.md'` must return a commit dated before every `backtester/results/options/*/summary.json` mtime — checked at the top of T-09-11 |
| Massive ~24-month aggregates cutoff still holds | OBT-02 | Plan-tier dependent, live API | T-09-01's re-probe: binary-search one SPY contract between 2024-01 (known 403) and 2024-09 (known 200); record the boundary in `backtester/options/data.py`'s docstring |
| Missing `O:` aggregate bars mean "no trade" (assumption A2) | OBT-02 | Requires a live illiquid-contract pull | T-09-01's second probe: one far-OTM GDX/USO contract over a 1-month entitled window; record whether non-trading days are absent rows or `v: 0` rows |
| Results doc verdicts follow the pre-registered rules | OBT-06, OBT-07 | Judgement call about honesty of interpretation | T-09-12 `<human-check>` at end-of-phase verification (`workflow.human_verify_mode=end-of-phase`) |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (owned by T-09-01)
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** executed 2026-08-17 (09-04). T-09-10/T-09-12 green; T-09-11 stopped
per its own pre-authorized wall-clock budget (see note above) — all three hypotheses
report INSUFFICIENT-EVIDENCE, `rules_options.json` unchanged, full suite green.
