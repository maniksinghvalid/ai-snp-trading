---
phase: 06
slug: backtester
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-06
---

# Phase 06 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing — 587+ tests green) |
| **Config file** | existing pytest configuration at repo root |
| **Quick run command** | `python3 -m pytest tests/backtester/ -q` |
| **Full suite command** | `python3 -m pytest -q` |
| **Estimated runtime** | ~60 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/backtester/ -q`
- **After every plan wave:** Run `python3 -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| (filled by planner) | | | BT-01..BT-04 | | | unit/integration | `python3 -m pytest tests/backtester/ -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/backtester/` — new test package for feed, execution, harness, report
- [ ] Synthetic 5m dataset fixture with a known ahead-only signal (look-ahead proof, success criterion 2)

*Existing pytest infrastructure covers framework needs; only new test files are required.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live yfinance 5m fetch within rolling ~60-day window | BT-04 | Network-dependent; window is relative to "now" | Run `backtester/run.py` with a recent date range and confirm bars load and report is written |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
