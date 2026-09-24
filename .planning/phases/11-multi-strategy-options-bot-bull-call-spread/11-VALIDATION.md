---
phase: 11
slug: multi-strategy-options-bot-bull-call-spread
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-24
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (installed; project standard) |
| **Config file** | none — tests discovered via the `tests/` package layout |
| **Quick run command** | `python3 -m pytest -q tests/options tests/backtester/options` |
| **Full suite command** | `python3 -m pytest -q` |
| **Estimated runtime** | ~4 s quick (261 tests) / ~65 s full (baseline 1134 passed, 1 skipped — 2026-09-24) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest -q tests/options tests/backtester/options`
- **After every plan wave:** Run `python3 -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green — ≥1134 passed, 0 new failures, no new skips beyond the 1 pre-existing
- **Max feedback latency:** 70 seconds

---

## Per-Task Verification Map

Task IDs are assigned by the planner; each plan task that implements a requirement below
MUST carry the listed automated command (or a narrower `-k` selection of it) in its
`<verify>` block. Rows are keyed by requirement until plans exist.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| per plan | per plan | per plan | MSO-01 | T-11-02 | `strategies` shape loads; `load_options_book` returns every strategy flattened; legacy flat shape still loads | unit | `python3 -m pytest -q tests/options/test_config.py -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-02 | T-11-02 | `load_options_config` flat contract unchanged; `options_run --strategy` + `legacy_view` before `--set`; debit structure rejected | unit | `python3 -m pytest -q tests/backtester/options/test_options_run.py -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-03 | T-11-02 / T-11-05 | `ConfigError` on dup names, both/neither universe keys, unknown `universe_source`, unimplemented structure, missing credit IV/manage keys | unit | `python3 -m pytest -q tests/options/test_config.py -k "fail" -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-04 | — | bull-call strikes, 1/4-rule gate, `debit × 100` sizing; credit-path results unchanged | unit | `python3 -m pytest -q tests/options/test_strategy.py -k "bull_call or size" -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-05 | — | `manage_decision_debit` order + sign math (NVDA 225/235 @ 3.58/1.62 literal case) | unit | `python3 -m pytest -q tests/options/test_strategy.py -k manage_decision_debit -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-06 | T-11-01 | reader is `mode=ro` only; cap 20 by rank; missing/locked/empty → `[]`; write attempt raises | unit | `python3 -m pytest -q tests/options/test_universe.py -x` | ❌ W0 | ⬜ pending |
| per plan | per plan | per plan | MSO-07 | T-11-04 | `strategy_name` migration idempotent; legacy rows default `tasty_credit_spreads`; negative `credit_per_spread` round-trips; close math correct for debit | unit | `python3 -m pytest -q tests/options/test_store.py -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-08 | T-11-04 | per-strategy job ids; manage dispatch by `strategy_name`; per-strategy caps vs global breaker/BP/one-per-underlying; unknown-strategy rows → NEEDS_ATTENTION | unit + integration | `python3 -m pytest -q tests/options/test_service.py -x` | ✅ extend | ⬜ pending |
| per plan | per plan | per plan | MSO-09 | T-11-03 | shipped file converted; D-26 field-for-field equivalence; `bot/main.py` routes the `strategies` shape to the options bot | unit + regression | `python3 -m pytest -q tests/options/test_config.py tests/options/test_dispatch.py -x` | ✅ extend | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

Threat refs (from 11-RESEARCH.md § Security Domain): T-11-01 options bot writes to the
equity DB; T-11-02 malformed `strategies` config loads with wrong risk values;
T-11-03 `bot/main.py` misroutes the options config; T-11-04 orphaned/mis-attributed
positions managed with the wrong strategy's parameters; T-11-05 unimplemented structure
passes the schema.

---

## Wave 0 Requirements

- [ ] `tests/options/test_universe.py` — new file for MSO-06 (real WAL-mode fixture DB; missing file; held write lock; empty day; 23 rows → 20; write attempt on the reader's connection raises `sqlite3.OperationalError`)
- [ ] `bot/options/universe.py` (or planner-chosen module) — the reader itself
- [ ] migration test for the `strategy_name` column alongside the existing options-migration tests

*Framework install: none — pytest, jsonschema, APScheduler already present.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live paper run with both books | MSO-08 | Needs OpenD logged in + RTH + a populated equity watchlist | During RTH after the equity premarket scan: `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`; confirm log `options_jobs_registered` lists `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`; confirm the 10:05 scan reads the watchlist (log line with code count) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 70s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
