---
phase: 12
slug: ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-10-04
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `12-RESEARCH.md` § Validation Architecture. The planner fills the per-task rows; the executor flips Status.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.3 (+ pytest-asyncio available; options tests drive coroutines with a local `_run = asyncio.run` helper — follow that) |
| **Config file** | none (no `pytest.ini`/`pyproject.toml`); shared fixtures in `tests/conftest.py` |
| **Quick run command** | `python3 -m pytest tests/ibs -x -q` |
| **Full suite command** | `python3 -m pytest -q` (baseline 1401 passed / 1 skipped) |
| **Estimated runtime** | ~60 seconds (full suite), <10 seconds (tests/ibs) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ibs -x -q`
- **After every plan wave:** Run `python3 -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green; operator RTH probe + `--live-1lot` recorded in a UAT note
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 12-??-?? | ?? | ? | IBS-10 | — | research scripts compile, no absolute `/Users/acdc` paths; results doc cites reference numbers | static | `python3 -m pytest tests/ibs/test_hygiene.py -k research -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-01 | T-12-01 | every missing/invalid key, inverted thresholds, bad HH:MM, duplicate codes, state_db colliding with equity/options DB → `ConfigError` (fail closed) | unit | `python3 -m pytest tests/ibs/test_schema_config.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-02 | — | `strategy_name == "ibs_etf_mean_reversion"` routes to `bot.ibs.service.main`; equity gateway never constructed; options/equity routes unchanged | unit | `python3 -m pytest tests/ibs/test_dispatch.py tests/options/test_dispatch.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-03 | T-12-02 | `compute_ibs` fail-closed (NaN/≤0/high==low/stale/suspended); decide_exits/decide_entries/size_position/trading_days_held; PARITY vs AST-extracted research `simulate` | unit + parity | `python3 -m pytest tests/ibs/test_strategy.py tests/ibs/test_parity.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-04 | — | arm_today: 15:50/15:59/16:05 normal, 12:50/12:59/13:05 half-day, none on non-trading day; decide refuses past deadline and on second call; exits before entries; one snapshot call | unit (mock gateway) | `python3 -m pytest tests/ibs/test_service.py -k "arm or decide" -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-05 | T-12-03 | LIMIT only (static: no `OrderType.MARKET`/`force_close`/`unlock_trade`/`manage_exit` in `bot/ibs/`); BUY `last+buf`, SELL `last−buf`; deadline cancel; partial fill; unconfirmed cancel → NEEDS_ATTENTION; hard-cancel sweep | unit + static | `python3 -m pytest tests/ibs/test_execution.py tests/ibs/test_hygiene.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-06 | T-12-04 | store CRUD on tmp DB; one active row per code; partial exit keeps remaining qty; realized P&L; `exit_pending` persisted; idempotent schema/migration | unit | `python3 -m pytest tests/ibs/test_store.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-07 | T-12-05 | reconcile: qty mismatch/missing → NEEDS_ATTENTION + one alert; external holdings never traded and block entry; NEEDS_ATTENTION rows hold a slot, never auto-exited | unit | `python3 -m pytest tests/ibs/test_service.py -k "reconcile or external or guard" -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-08 | T-12-06 | readiness-gate order; kill-file shutdown order (cancel decision → sweep → close → alert); watchdog duck-type attrs; alerts HTML-escaped; EOD HTML escapes cells; `configure_logging(log_name="ibs.log", force=True)` stays in patched log dir; `AUDIT_LOG_PATH` isolated | unit | `python3 -m pytest tests/ibs/test_service.py tests/safety/test_logger.py -x` | ❌ W0 | ⬜ pending |
| 12-??-?? | ?? | ? | IBS-09 | T-12-07 | probe read-only never calls `place_order`; `--live-1lot` refused without `--confirm` (exit 2); plist parses, has PAPER_TRADING/SIMULATE/FUTU_ACC_ID/rules_ibs.json/KeepAlive dict | unit + static | `python3 -m pytest tests/ibs/test_hygiene.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/ibs/__init__.py`, `tests/ibs/conftest.py` — `ibs_rules` LITERAL dict (drift guard, as `tests/options/conftest.py`), `ibs_cfg`, tmp `IbsStore`, mock gateway/alerter/kill-switch fixtures
- [ ] `tests/ibs/test_{schema_config,dispatch,strategy,parity,service,execution,store,hygiene}.py` — stubs per the map above
- [ ] `tests/conftest.py` — session-autouse audit-log isolation fixture (patches `bot.safety.audit_log.AUDIT_LOG_PATH`; none exists today — the real `~/.futu_trade_audit.jsonl` already holds thousands of test entries)
- [ ] `backtester/experimental/ibs_search/strategy_search.py` must land (Plan 1) before `test_parity.py` can run
- [ ] Framework install: none

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Snapshot freshness/`update_time` during RTH; `max_snapshot_age_s` calibration | IBS-03/04 | needs live OpenD during a session | run `python3 scripts/uat_ibs_probe.py` between 15:30–15:55 ET; confirm all 17 rows fresh, IBS values plausible vs a chart |
| ETF LIMIT fill behaviour on SIMULATE (TTL, re-price, cancel) | IBS-05 | no ETF round trip observed yet on the paper account | operator runs `python3 scripts/uat_ibs_probe.py --live-1lot --confirm` during RTH; verify audit log + Telegram + `ibs_orders` row |
| launchd supervision + kill-file stop without restart | IBS-09 | host process management | `launchctl load` the IBS plist, `touch .bot_kill_ibs`, confirm exit and no auto-restart |
| Cutover: Trend Join Long stopped, IBS running, options bot unaffected | IBS-09 | operator-run process control | follow `deploy/IBS-RUNBOOK.md`; confirm `pgrep -f rules.json` empty, `pgrep -f rules_ibs.json` + options alive |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
