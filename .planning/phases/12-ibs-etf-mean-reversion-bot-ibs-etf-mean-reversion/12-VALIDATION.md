---
phase: 12
slug: ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
status: approved
nyquist_compliant: true
wave_0_complete: true
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
| **Estimated runtime** | ~60 seconds (full suite), <15 seconds (tests/ibs) |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ibs -x -q` (plus the task's own file command below)
- **After every plan wave:** Run `python3 -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green; operator RTH probe + `--live-1lot` recorded in a UAT note
- **Max feedback latency:** 60 seconds
- **Parallel waves share one working tree** (`use_worktrees=false`): a failure confined to a sibling plan's mid-RED test file is not the current plan's failure; the wave-end full suite is authoritative.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 12-01-01 | 01 | 1 | IBS-10 | T-12-17 | six research scripts compile, only path constants changed, no `/Users/` or `.scratch` paths, `simulate` signature intact | static | `python3 -m pytest tests/ibs/test_research_provenance.py -x -q` | ✅ | ✅ green |
| 12-01-02 | 01 | 1 | IBS-10 | T-12-16 | results doc cites every CONTEXT reference number + limitations; captured evidence files present | static | `python3 -m pytest tests/ibs/test_research_provenance.py -x -q` | ✅ | ✅ green |
| 12-02-01 | 02 | 1 | IBS-01 | T-12-01, T-12-04a, T-12-03a | every missing/invalid key, inverted thresholds, leverage (OD-1), bad HH:MM, window too short, duplicate/ill-formed codes, paths colliding with equity/options DB/kill/report/log → `ConfigError` (fail closed) | unit | `python3 -m pytest tests/ibs/test_schema_config.py -x -q` | ✅ | ✅ green |
| 12-02-02 | 02 | 1 | IBS-01 | T-12-01 | shipped `rules_ibs.json` equals the fixture literal and loads; D-01 universe exact | unit | `python3 -m pytest tests/ibs/test_schema_config.py -x -q` | ✅ | ✅ green |
| 12-03-01 | 03 | 1 | IBS-06, IBS-08 | T-12-06a | `configure_logging(log_name=, force=)` kw-only (conftest default-dir patch intact); `trading_days_between` holiday/half-day correct | unit | `python3 -m pytest tests/safety/test_logger.py tests/scanner/test_calendar.py -x -q` | ✅ (extended) | ✅ green |
| 12-03-02 | 03 | 1 | IBS-06 | T-12-04b, T-12-04c, T-12-05a, T-12-09 | migration 0008 idempotent, one active row per code, 0001-0007 untouched; `AUDIT_LOG_PATH` isolated for the whole session | unit | `python3 -m pytest tests/state/test_migrations.py tests/safety/test_audit_log.py -x -q && python3 -m pytest -q` | ✅ (extended) | ✅ green |
| 12-04-01 | 04 | 2 | IBS-03 | T-12-02, T-12-02b | `compute_ibs`/`parse_snapshot` fail closed (NaN/≤0/high==low/outside range/suspended/stale date/stale age); `decide_exits`/`decide_entries`/`size_position`/`trading_days_held` | unit | `python3 -m pytest tests/ibs/test_strategy.py -x -q` | ✅ | ✅ green |
| 12-04-02 | 04 | 2 | IBS-03 | T-12-13 | PARITY vs AST-extracted research `simulate`; same-day re-entry divergence (ruling 3) pinned | parity | `python3 -m pytest tests/ibs/test_parity.py -x -q` | ✅ | ✅ green |
| 12-05-01 | 05 | 2 | IBS-06 | T-12-04, T-12-04d, T-12-14 | store CRUD on tmp DB; one active row per code; partial exit keeps remaining qty; realized P&L; `exit_pending` keeps first reason/date; `?` binding only | unit | `python3 -m pytest tests/ibs/test_store.py -x -q` | ✅ | ✅ green |
| 12-05-02 | 05 | 2 | IBS-05 | T-12-03, T-12-03b | LIMIT only via `LegExecutor`; BUY `last+buf`, SELL `last−buf`; escalation; partial; CR-04 unconfirmed cancel raises; deadline → cancel + TimeoutError; bot/options unchanged | unit | `python3 -m pytest tests/ibs/test_execution.py -x -q && python3 -m pytest tests/options -q` | ✅ | ✅ green |
| 12-06-01 | 06 | 3 | IBS-07 | T-12-05, T-12-08, T-12-06 | readiness gate order (connect → reconcile → install → enable); mismatch/missing → NEEDS_ATTENTION + one alert; external holdings never written or traded; alerts escaped | unit | `python3 -m pytest tests/ibs/test_service.py -x -q` | ✅ | ✅ green |
| 12-06-02 | 06 | 3 | IBS-04, IBS-05 | T-12-02c, T-12-03c, T-12-10, T-12-03d | decide no-ops (non-trading day, disabled, kill switch, already decided, past deadline); meta written first; ONE snapshot; exits sequential with `exit_pending` retry, partial/unfilled/unknown handling | unit | `python3 -m pytest tests/ibs/test_service.py -x -q` | ✅ | ✅ green |
| 12-07-01 | 07 | 3 | IBS-09 | T-12-07, T-12-07e | probe read-only never calls `place_order`/`cancel_order`; `--live-1lot` refused without `--confirm` (exit 2) before any gateway; no DB created | unit | `python3 -m pytest tests/ibs/test_operator_tooling.py -x -q` | ✅ | ✅ green |
| 12-07-02 | 07 | 3 | IBS-09 | T-12-07b, T-12-07c, T-12-07d | plist parses (`plutil -lint`), PAPER_TRADING/SIMULATE/FUTU_ACC_ID/`rules_ibs.json`/KeepAlive dict, no secrets; runbook covers cutover + first-week checks; CLAUDE.md section | static | `plutil -lint deploy/com.bot.ibs.plist && python3 -m pytest tests/ibs/test_operator_tooling.py -x -q` | ✅ | ✅ green |
| 12-08-01 | 08 | 4 | IBS-04, IBS-07 | T-12-05b, T-12-10b, T-12-03e | entries after exits; external holdings + broker-read failure block entries; same-session re-entry excluded; slots/sizing; OPENING row before BUY; outcomes | unit | `python3 -m pytest tests/ibs/test_service_entries.py -x -q` | ✅ | ✅ green |
| 12-08-02 | 08 | 4 | IBS-05 | T-12-03f, T-12-03g | hard-cancel at close − 1 min cancels in-flight decision then every WORKING order; CANCEL_FAILED alerts; cancels audited | unit | `python3 -m pytest tests/ibs/test_service_entries.py -x -q && python3 -m pytest -q` | ✅ | ✅ green |
| 12-09-01 | 09 | 5 | IBS-04, IBS-08 | T-12-03h, T-12-06c | arm 15:50/15:59/16:05 normal, 12:50/12:59/13:05 half-day, none on non-trading day, passed slots skipped, no force-close job (D-06); EOD escaped, report written | unit | `python3 -m pytest tests/ibs/test_lifecycle.py -x -q` | ✅ | ✅ green |
| 12-09-02 | 09 | 5 | IBS-02, IBS-08 | T-12-07f, T-12-08b, T-12-01b, T-12-06d, T-12-15 | shutdown order (cancel decision → sweep → close → alert → scheduler); main wiring (`ibs.log`, own DB/kill file, watchdog); dispatch routes IBS, equity/options unchanged | unit | `python3 -m pytest tests/ibs/test_lifecycle.py tests/ibs/test_dispatch.py tests/options/test_dispatch.py -x -q` | ✅ | ✅ green |
| 12-09-03 | 09 | 5 | IBS-01, IBS-05 | T-12-15, T-12-18 | static: no `OrderType.MARKET`/`force_close`/`unlock_trade`/`manage_exit`/`yfinance`/`.subscribe(` and no strategy literal in `bot/ibs/`; full suite + options suite green | static + full | `python3 -m pytest tests/ibs/test_hygiene.py -x -q && python3 -m pytest -q` | ✅ | ✅ green |

*Status: pending (open box) · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/ibs/__init__.py`, `tests/ibs/conftest.py` — `ibs_rules` LITERAL dict + `ibs_cfg` (Plan 02); service fixtures with lazy `IbsBot` import (Plan 06)
- [x] `tests/ibs/test_{research_provenance,schema_config,strategy,parity,store,execution,service,operator_tooling,service_entries,lifecycle,dispatch,hygiene}.py` — created by the plans in the map above (each task writes its failing test first, D-15)
- [x] `tests/conftest.py` — session-autouse `_isolate_audit_log` (Plan 03; patches `bot.safety.audit_log.AUDIT_LOG_PATH`)
- [x] `backtester/experimental/ibs_search/strategy_search.py` lands (Plan 01) before `test_parity.py` (Plan 04, Wave 2)
- [x] Framework install: none

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Snapshot freshness/`update_time` during RTH; `max_snapshot_age_s` and limit-buffer calibration | IBS-03/04 | needs live OpenD during a session | run `python3 scripts/uat_ibs_probe.py` between 15:30–15:55 ET; confirm all 17 rows fresh, IBS plausible vs a chart, `ask − bid` within the 0.05 buffers |
| ETF LIMIT fill behaviour on SIMULATE (TTL, re-price, cancel) | IBS-05 | no ETF round trip observed yet on the paper account | operator runs `python3 scripts/uat_ibs_probe.py --live-1lot --confirm` during RTH; verify audit log + Telegram + scratch `ibs_orders` rows |
| launchd supervision + kill-file stop without restart | IBS-09 | host process management | `launchctl load` the IBS plist, `touch .bot_kill_ibs`, confirm exit and no auto-restart |
| Cutover: Trend Join Long stopped, IBS running, options bot unaffected | IBS-09 | operator-run process control | follow `deploy/IBS-RUNBOOK.md`; confirm via `pgrep -fl "python3 -m bot"` that only `--rules rules_options.json` and `--rules rules_ibs.json` remain |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [x] nyquist_compliant flag set in frontmatter

**Approval:** automated gate green 2026-10-04 (1665 passed, 1 skipped); manual-only items (RTH probe, --live-1lot, launchd kill-file stop, cutover) pending operator
