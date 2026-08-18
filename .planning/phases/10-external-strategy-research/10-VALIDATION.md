---
phase: 10
slug: external-strategy-research
status: verified
nyquist_compliant: true
wave_0_complete: true  # tests/backtester/experimental/__init__.py scaffold landed in 10-01 Task 1, confirmed
created: 2026-08-18
audited: 2026-08-18
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

*Filled in by the planner 2026-08-18. Requirements in scope: XSR-01..XSR-06 (all six mapped). Threat refs point at the per-plan `<threat_model>` registers: the real boundaries in this phase are production-tree scope (T-10-02), the shared Massive quota / Phase 9 warm-cache-pool (T-10-03), cache-key spoofing (T-10-08), evidence integrity (T-10-01/T-10-12), and the research-to-production gate (T-10-15).*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 10-01-T1 | 10-01 | 1 | XSR-03 | T-10-01 | Frozen 15-arm list is machine-readable and immutable after commit | unit (assertion script) | `python3 -c "import json;d=json.load(open('backtester/experimental/arms.json'));assert len(d['arms'])==15"` | ✅ | ✅ green |
| 10-01-T2 | 10-01 | 1 | XSR-01, XSR-03 | T-10-01 | Every hypothesis + protocol section exists before any run | scripted grep | `for h in H1 H2 H3 H4 H5 H6 H7 H8; do grep -q "^### $h" docs/research/2026-08-18-external-strategies-hypotheses.md; done` | ✅ | ✅ green |
| 10-01-T3 | 10-01 | 1 | XSR-03 | T-10-01, T-10-03 | Pre-registration commit precedes any run artifact; SPY series provenance recorded | manual (git log check) | `git log --diff-filter=A --format=%H -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` | ✅ | ✅ green — commit `f8439aa` |
| 10-02-T1 | 10-02 | 2 | XSR-02, XSR-04 | T-10-05, T-10-07 | Side-aware P&L; `extra_fields` never mutates `_CSV_FIELDS`; no look-ahead in indicators | unit | `python3 -m pytest -q tests/backtester/test_report.py tests/backtester/experimental/test_indicators.py -x` | ✅ | ✅ green — 32 passed |
| 10-02-T2 | 10-02 | 2 | XSR-02 | T-10-06 | Prefix-invariance (no look-ahead) in all three signal generators; short exit math | unit | `python3 -m pytest -q tests/backtester/experimental/test_strategies.py tests/backtester/experimental/test_exits.py -x` | ✅ | ✅ green — 18 passed |
| 10-02-T3 | 10-02 | 2 | XSR-02 | T-10-06, T-10-07 | Fills at N+1 open; caps/breaker/force-close/half-day; short row sign | unit | `python3 -m pytest -q tests/backtester/experimental/test_engine.py -x` | ✅ | ✅ green — 15 passed |
| 10-03-T1 | 10-03 | 2 | XSR-04 | T-10-03, T-10-08 | `run.py` refuses to run on a missing cache file and constructs no data source | unit | `python3 -m pytest -q tests/backtester/experimental/test_run.py -x` | ✅ | ✅ green — 27 passed |
| 10-03-T2 | 10-03 | 2 | XSR-02, XSR-04 | T-10-03 | One feed + one day-grouping per window; four artifacts per arm; 13-column CSV | unit | `python3 -m pytest -q tests/backtester/experimental/test_run.py -x` | ✅ | ✅ green — same suite as 10-03-T1 |
| 10-03-T3 | 10-03 | 2 | XSR-04 | T-10-09 | TJL regime gate is a day-filter; `BacktestHarness` never invoked | unit | `python3 -m pytest -q tests/backtester/experimental/test_run.py -x` | ✅ | ✅ green — same suite as 10-03-T1 |
| 10-04-T1 | 10-04 | 3 | XSR-04 | T-10-03, T-10-02 | 240/240 cache paths present; warm-cache-pool alive; pre-registration precedes runs; tree scope clean | scripted pre-flight | `python3 -c "from backtester.experimental.run import WINDOWS, MEGA24, _required_cache_paths; ..."` (see plan) | ✅ | ✅ green — 240/240 paths, scope clean, pid 48166 alive |
| 10-04-T2 | 10-04 | 3 | XSR-04 | T-10-03, T-10-10 | Every arm × window has `summary.json`; no `fetch` token in any run log | scripted | `python3 -c "... assert not missing ... assert not hits"` (see plan Task 2 verify) | ✅ | ✅ green* — 75 arm-windows complete; *the literal `fetch` grep matches only `postflight.log`'s own PASS line ("no fetch token in any log"), not a real fetch — see audit note below |
| 10-04-T3 | 10-04 | 3 | XSR-04 | T-10-09, T-10-03 | TJL re-report parity with evidence of record; zero new cache files; warm-pool unchanged | scripted post-flight | `find backtester/cache/massive -newer backtester/results/experimental/logs/preflight.log -name '*.csv'` returns 0 | ✅ | ✅ green* — 0 non-`O_*` (equity) files new; all 670 new-mtime hits are `O_*` option-contract files from Phase 9's still-running `warm-cache-pool` — see audit note below |
| 10-05-T1 | 10-05 | 4 | XSR-05 | T-10-14 | Deterministic bootstrap CI; `inf` PF renders safely; slice/pool math reuses `report.compute_metrics` | unit | `python3 -m pytest -q tests/backtester/experimental/test_aggregate.py -x` | ✅ | ✅ green — 8 passed |
| 10-05-T2 | 10-05 | 4 | XSR-05 | T-10-13, T-10-12 | Evidence committed under `docs/research/assets/`; `combo` appended only by the pre-registered rule | unit + manual | `python3 -m pytest -q tests/backtester/experimental/test_charts.py -x` | ✅ | ✅ green — 5 passed |
| 10-05-T3 | 10-05 | 4 | XSR-01, XSR-05 | T-10-12, T-10-02 | Every H1–H8 verdict is numerically justified; `rules.json` unchanged | manual (scripted section/verdict check) | `python3 -c "... assert 'Verdict table' in t ..."` (see plan Task 3 verify) | ✅ | ✅ green — 14 numbered sections, scope clean |
| 10-06-T1 | 10-06 | 5 | XSR-06 | T-10-15 | Gate decision is mechanical over the pre-registered verdict table | scripted | `python3 -c "... print('GATE:', 'TRIGGERED' if supported else 'NOT-TRIGGERED')"` | ✅ | ✅ green — `GATE: TRIGGERED` (via H2) |
| 10-06-T2 | 10-06 | 5 | XSR-06 | T-10-15 | Operator confirms scope before any production edit (skipped when gate closed) | manual (checkpoint) | `grep -qE 'full-sketch\|seam-only\|defer\|NOT-TRIGGERED' .planning/phases/10-external-strategy-research/10-06-SUMMARY.md` | ✅ | ✅ green — operator selected `defer` |
| 10-06-T3 | 10-06 | 5 | XSR-06 | T-10-16, T-10-17, T-10-18 | Integration lives on an unmerged feature branch; default config still builds today's pipeline | unit + repo-state | `python3 -m pytest -q && git rev-parse --abbrev-ref HEAD | grep -q '^feature/phase10-'` | ✅ | ✅ N/A (deferred)* — full suite green (1127 passed, 1 skipped); the branch sub-check's premise (a `feature/phase10-*` branch exists) presumes the TRIGGERED+non-defer path, which did not occur — see audit note below |

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

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planner-signed 2026-08-18 — every task carries an `<automated>` verify (10-04's are scripted assertions over run artifacts; 10-06-T2 is the single operator checkpoint, gated on a mechanical verdict read). No 3 consecutive tasks without automated feedback. No watch-mode flags. Feedback latency < 60s for all pytest commands; 10-04's run matrix is the one long-running task and is logged per invocation.

---

## Validation Audit 2026-08-18

Post-execution audit: every task's Automated Command re-run live against the built codebase (not re-derived from SUMMARY.md claims).

| Metric | Count |
|--------|-------|
| Tasks checked | 18/18 |
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

**Caveats investigated and closed (not gaps):**

1. **10-04-T2** — the literal `fetch`-token grep over `backtester/results/experimental/logs/*.log` matches `postflight.log`, but the matching line is the check's own PASS message: `"no fetch token in any log"`. The substring "fetch" appears inside the sentence describing the absence of a fetch, not inside a real HTTP/network trace line. Confirmed via `grep -i fetch` on the file directly — one line, and it's the assertion's own success text.

2. **10-04-T3** — `find backtester/cache/massive -newer preflight.log -name '*.csv'` returns 670 files (grows over time as this check is re-run, since `preflight.log`'s mtime advances each re-run). All 670 are `O_*`-prefixed option-contract cache files; `grep -vc '/O_'` over the same file list returns **0** — meaning zero non-option (i.e. zero equity/MEGA24) files are new. These are 100% attributable to Phase 9's `warm-cache-pool` background process (pid 48166, running continuously since 2026-08-17, confirmed alive via `ps -p`), which fetches new options data by design and is explicitly out of scope for this phase's cache-only guarantee. Phase 10's own cache-only discipline (zero new *equity* fetches) holds with zero exceptions.

3. **10-06-T3** — the branch sub-check (`git rev-parse --abbrev-ref HEAD | grep -q '^feature/phase10-'`) was written assuming the TRIGGERED+non-defer path. The actual outcome was TRIGGERED (via H2) *then deferred* at the Task 2 operator checkpoint — Task 3 (the branch-creating task) correctly never ran, so no `feature/phase10-*` branch exists and `HEAD` is `develop`. This is the correct, expected state for a deferred trigger, not a check failure; `10-06-SUMMARY.md` and `10-SECURITY.md` (T-10-16/T-10-17) both independently confirm this same "precondition never occurred" reading. The full-suite half of this task's check (`python3 -m pytest -q`) passed live: 1127 passed, 1 skipped.

**Conclusion:** All 18 tasks verified against live code/repo state. Zero genuine gaps. `nyquist_compliant: true` confirmed post-execution, not just at plan time.
