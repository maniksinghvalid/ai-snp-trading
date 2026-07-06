# Phase 5: Service Orchestration and Reliability - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 5-service-orchestration-and-reliability
**Areas discussed:** Service lifecycle & scheduler, Process supervision & crash recovery, OpenD watchdog disconnect policy, Reporting: Telegram + HTML dashboard

(All four offered gray areas selected for discussion.)

---

## Service lifecycle & scheduler

### Process model
| Option | Description | Selected |
|--------|-------------|----------|
| Always-on 24/7 | One long-lived process; APScheduler owns daily timing; calendar no-op on weekends/holidays | ✓ |
| Daily start/stop via supervisor | External scheduler starts/stops the bot each day; relies on reconcile every boot | |

### Misfire policy
| Option | Description | Selected |
|--------|-------------|----------|
| Smart per-job | coalesce + per-job grace; valid-late jobs catch up, stale one-shots skip | ✓ |
| Run all missed jobs | Generous grace; every missed job fires on boot | |
| Skip all missed jobs | Tight grace; missed jobs never run | |

### Bar-loop coexistence
| Option | Description | Selected |
|--------|-------------|----------|
| Single AsyncIO event loop | Scheduler + bar/position tasks on one loop; no locking | ✓ |
| Scheduler thread + asyncio app | BackgroundScheduler on its own thread | |

### Daily reset
| Option | Description | Selected |
|--------|-------------|----------|
| Lazy session-date keying | No reset job; everything keyed by ET session_date (schema already does this) | ✓ |
| Scheduled premarket reset job | Explicit morning job clears counters | |

**User's choice:** All four recommended options.
**Notes:** Aligns with PROJECT.md "persistent process owns state + subscriptions." Misfire/coalesce grace values go to rules.json.

---

## Process supervision & crash recovery

### Supervisor mechanism
| Option | Description | Selected |
|--------|-------------|----------|
| launchd LaunchAgent | Native macOS; KeepAlive, RunAtLoad, ThrottleInterval, log paths | ✓ |
| Shell while-loop wrapper | Portable fallback; no boot-start | |
| systemd | Linux-native; wrong fit for the Mac (documented note only) | |

### Crash-loop guard
| Option | Description | Selected |
|--------|-------------|----------|
| Throttle + alert, keep trying | ThrottleInterval + Telegram alert on flap; keeps retrying unattended | ✓ |
| Throttle + give up after N | Stops after N rapid crashes; needs manual intervention | |

### Kill-switch flush wiring (R-04-01)
| Option | Description | Selected |
|--------|-------------|----------|
| Full graceful shutdown | flush_all + audit + unsubscribe/close gateway + final Telegram alert | ✓ |
| Minimal state flush only | Wire flush_all only; rely on next-boot reconciliation | |

### Startup readiness gate
| Option | Description | Selected |
|--------|-------------|----------|
| Hard gate: guard + OpenD + reconcile | Block entries until paper-guard + OpenD reachable + startup_reconcile done | ✓ |
| Best-effort start | Start scheduler immediately; jobs fail-safe individually | |

**User's choice:** All four recommended options.
**Notes:** Operator on macOS (darwin) — launchd chosen as native. Resolves the deferred R-04-01 / T-04-21 kill-switch flush wiring with a fuller graceful-shutdown handler.

---

## OpenD watchdog disconnect policy

### Pause scope
| Option | Description | Selected |
|--------|-------------|----------|
| Pause entries; keep exits queued | Suspend entries; bar-close stop checks keep running; exits fired on reconnect | ✓ |
| Pause all order placement | Halt entries and exits until reconnect | |

### Reconnect strategy
| Option | Description | Selected |
|--------|-------------|----------|
| Exponential backoff, capped | 5s→10s→30s→cap 60s; re-create contexts | ✓ |
| Fixed 60s retry | Poll/reconnect every 60s | |

### Recovery
| Option | Description | Selected |
|--------|-------------|----------|
| Re-run startup reconciliation | broker-truth reconcile + re-subscribe before re-enabling entries | ✓ |
| Resume immediately | Flush queued exits and resume on in-memory state | |

### Health alert (SVC-02 vs PROJECT.md tension)
| Option | Description | Selected |
|--------|-------------|----------|
| Alert on disconnect + reconnect | Narrow exception to deferred-health-alerts; satisfies SVC-02 criterion #2 | ✓ |
| Log-only | Honor PROJECT.md literally; no Telegram for health | |

**User's choice:** All four recommended options.
**Notes:** Resolved the explicit spec tension — SVC-02's stated success criterion wins; documented as a narrow, intentional exception to PROJECT.md's deferred system-health-alerts line. Backoff steps + poll interval go to rules.json.

---

## Reporting: Telegram + HTML dashboard

### Secrets location
| Option | Description | Selected |
|--------|-------------|----------|
| Env vars + .env.example | TELEGRAM_BOT_TOKEN/CHAT_ID via env (FUTU_* pattern); documented in .env.example | ✓ |
| Separate gitignored secrets file | secrets.json loaded at startup | |

### Transport
| Option | Description | Selected |
|--------|-------------|----------|
| Zero-dep: stdlib urllib in executor | urllib POST via run_in_executor + create_task; fire-and-forget; no new dep | ✓ |
| httpx async client | Native async HTTP; adds a dependency | |
| python-telegram-bot library | Full framework; overkill for one-way push | |

### Charts (no-JS dashboard)
| Option | Description | Selected |
|--------|-------------|----------|
| Inline SVG + CSS, zero-dep | Hand-generated SVG histogram + HTML/CSS tables; one self-contained file | ✓ |
| Embedded matplotlib PNG | base64 data-URI PNG; adds matplotlib | |
| ASCII/text histogram in <pre> | Monospace text bars; crude | |

### Output & retention
| Option | Description | Selected |
|--------|-------------|----------|
| Dated files in reports/ + latest.html | reports/YYYY-MM-DD.html kept + stable latest.html bookmark | ✓ |
| Single overwriting dashboard.html | One file overwritten daily; no history | |

**User's choice:** All four recommended options.
**Notes:** Lean-dependency ethos drove all choices — only one new runtime dep total (apscheduler); zero-dep Telegram and zero-dep dashboard. The bot does not auto-load .env (no python-dotenv) — secrets are sourced into the shell or supplied via the launchd plist EnvironmentVariables.

---

## Claude's Discretion

- Numeric tunables → rules.json: schedule ET times, re-scan cadence/window, watchdog poll interval, reconnect backoff steps/cap, launchd ThrottleInterval, misfire/coalesce grace, crash-loop alert threshold.
- Module/dataclass decomposition across the 4 plan slices; class names; main.py/__main__ location; alert event fan-out wiring.
- Alert message format/layout beyond the required fields.
- New `get_global_state()` gateway method (does not exist yet) — watchdog adds it.
- Adding `apscheduler` to requirements.txt (with package-legitimacy checkpoint).
- End-to-end full-session integration test + OpenD-disconnect simulation design.

## Deferred Ideas

- Broader system-health alerting (scan-didn't-run, job-failed, degraded data) — stays log-only in v1; only OpenD up/down promoted to alert.
- Interactive / two-way Telegram (status/pause/flatten commands) — one-way push only in v1.
- Server-backed / live web dashboard — out of scope; static no-JS file is the ceiling.
- Backtester — remains Phase 6.
