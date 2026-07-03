---
phase: 07
slug: strategy-optimization
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-03
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing — 514 tests green on develop) |
| **Config file** | existing pytest configuration at repo root |
| **Quick run command** | `python3 -m pytest tests/ -x -q --timeout=60 -k "<task-scope>"` |
| **Full suite command** | `python3 -m pytest tests/ -q` |
| **Estimated runtime** | ~60 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run the quick run command scoped to the touched module
- **After every plan wave:** Run the full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| (filled by planner) | | | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Existing pytest infrastructure covers all phase requirements — no new framework install needed
- [ ] New test files per feature area (RVOL-TOD, tick-stop, circuit breaker) follow existing `tests/` conventions

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SIMULATE account honors `OrderType.STOP` | RISK-TICK-STOP | Broker-side behavior on paper account is empirically unverifiable in unit tests (research open question 1) | Place a stop order on SIMULATE via OpenD during a live session; observe trigger behavior; record result to select broker-side vs quote-monitor path |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
