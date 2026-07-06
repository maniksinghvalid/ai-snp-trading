---
phase: 5
slug: service-orchestration-and-reliability
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-24
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) + pytest-asyncio (verify installed; Wave 0 adds if missing) |
| **Config file** | existing `pyproject.toml` / `pytest.ini` (check before adding) |
| **Quick run command** | `pytest tests/service/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/service/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01 | 01 | — | SVC-01 | — | Scheduler fires premarket scan job | unit | `pytest tests/service/test_bot.py::test_premarket_scan_job_called -x` | ❌ W0 | ⬜ pending |
| 05-01 | 01 | — | SCAN-07 | — | Intraday rescan job fires ~7× in window | unit | `pytest tests/service/test_bot.py::test_intraday_rescan_job_called -x` | ❌ W0 | ⬜ pending |
| 05-01 | 01 | — | SVC-01 | — | Force-close job registered/fires | unit | `pytest tests/service/test_bot.py::test_force_close_job_called -x` | ❌ W0 | ⬜ pending |
| 05-01 | 01 | — | SVC-01 | — | D-08 readiness gate blocks entries before reconcile | unit | `pytest tests/service/test_bot.py::test_readiness_gate_blocks_entries -x` | ❌ W0 | ⬜ pending |
| 05-01 | 01 | — | R-04-01 | — | KillSwitch.register_flush wired before loop | unit | `pytest tests/service/test_bot.py::test_kill_switch_flush_registered -x` | ❌ W0 | ⬜ pending |
| 05-02 | 02 | — | SVC-02 | — | Watchdog detects disconnect → entries disabled | unit | `pytest tests/service/test_watchdog.py::test_disconnect_disables_entries -x` | ❌ W0 | ⬜ pending |
| 05-02 | 02 | — | SVC-02 | — | Reconnect → startup_reconcile → entries re-enabled | unit | `pytest tests/service/test_watchdog.py::test_reconnect_reenables_entries -x` | ❌ W0 | ⬜ pending |
| 05-03 | 03 | — | ALERT-01 | — | Entry alert content (ticker/size/entry/stop) | unit | `pytest tests/service/test_alerter.py::test_entry_alert_content -x` | ❌ W0 | ⬜ pending |
| 05-03 | 03 | — | ALERT-02 | — | Exit alert for each exit_reason type | unit | `pytest tests/service/test_alerter.py::test_exit_alert_for_all_reasons -x` | ❌ W0 | ⬜ pending |
| 05-03 | 03 | — | ALERT-03 | — | Daily summary includes trades/wins/PnL/open-risk | unit | `pytest tests/service/test_alerter.py::test_daily_summary_content -x` | ❌ W0 | ⬜ pending |
| 05-03 | 03 | — | ALERT-04 | — | urllib exception → send() does not raise/propagate | unit | `pytest tests/service/test_alerter.py::test_send_failure_does_not_raise -x` | ❌ W0 | ⬜ pending |
| 05-04 | 04 | — | DASH-01 | — | HTML report renders all required sections | unit | `pytest tests/service/test_report.py::test_html_report_sections -x` | ❌ W0 | ⬜ pending |
| 05-04 | 04 | — | DASH-01 | — | SVG R-multiple histogram correct bucket counts | unit | `pytest tests/service/test_report.py::test_r_histogram_buckets -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/service/__init__.py` — new test package
- [ ] `tests/service/test_bot.py` — TradingBot/scheduler unit stubs (SVC-01, SCAN-07, R-04-01)
- [ ] `tests/service/test_watchdog.py` — OpenD watchdog unit stubs (SVC-02)
- [ ] `tests/service/test_alerter.py` — TelegramAlerter unit stubs (ALERT-01..04)
- [ ] `tests/service/test_report.py` — report/dashboard unit stubs (DASH-01)
- [ ] Verify `pytest-asyncio` installed; add to requirements if missing
- [ ] `tests/service/conftest.py` — shared fixtures (fake gateway, mock scanner, tmp StateStore)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| One full paper-trading session end-to-end | SVC-01 (crit. #1) | APScheduler real-time firing + live OpenD paper account; not deterministically time-controllable | Run the service against a running OpenD paper account; observe scheduler jobs fire at correct ET times across a session |
| OpenD-disconnect simulation | SVC-02 (crit. #2) | Requires killing a real OpenD process | `kill` OpenD; observe entries pause + Telegram alert (or log) within one ~60s poll cycle; restart OpenD, observe reconnect + reconcile + re-enable |
| Service restarts on crash under supervisor | SVC-01/SAFE-04 (crit. #5) | launchd KeepAlive behavior is OS-level | Install LaunchAgent; kill the process; confirm auto-restart + rotating JSON logs written |
| Dashboard renders from `file://` offline | DASH-01 (crit. #6) | Visual confirmation of offline no-JS render | Open `reports/latest.html` in a browser with no server/network; confirm histogram + tables render |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
