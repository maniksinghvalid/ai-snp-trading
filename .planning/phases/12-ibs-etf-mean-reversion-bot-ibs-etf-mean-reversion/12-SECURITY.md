---
phase: 12
slug: ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
status: verified
threats_open: 0
threats_total: 46
asvs_level: 1
block_on: high
created: 2026-10-04
register_authored_at_plan_time: true
---

# Phase 12 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Register authored at plan time (9/9 PLAN.md files carry a `<threat_model>` block); verified against the implementation by `gsd-security-auditor` on 2026-10-04 (verify-mitigations mode — no new-threat scan). Post-plan code-review fixes (`12-REVIEW-FIX.md`) strengthened several controls; the stronger shipped control is what was verified.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| yfinance (internet) → research scripts | Third-party market data feeds the offline research only, never the live bot (D-19) | Daily OHLC bars (read-only) |
| repo → git history | Absolute home paths and gitignored data must not be committed | Source, doc and asset files |
| `rules_ibs.json` (operator-editable) → bot process | Untrusted JSON parsed at startup; a mis-edit must stop the bot before it connects | Strategy, risk, execution, service parameters, file paths |
| test process → real audit log / production logs | Tests must never write `~/.futu_trade_audit.jsonl` or `logs/bot.log` | Audit events, structured log lines |
| shared migration runner → equity / options / IBS DBs | One runner migrates every StateStore DB | Schema DDL (migration 0008) |
| broker snapshot (OpenD) → strategy decisions | Quote fields directly size and trigger orders | last/high/low, `update_time`, suspension flag |
| bot → broker order API (OpenD, shared paper account) | Every order is a real paper order on an account shared with the options bot and a human | LIMIT orders, cancels, order-status and position reads |
| bot → SQLite file | Persistent state drives later exits | Positions, trades, orders, meta idempotency keys |
| broker positions / snapshot → decision | External state decides which rows exist and what gets sold | Broker holdings, share counts, quotes |
| bot → shared paper account (coexistence) | The options bot and a human hold positions on the same account | External universe-ETF holdings (counted, never traded) |
| bot → Telegram | Alert text leaves the machine (HTML parse mode) | Fixed templates with escaped codes, quantities, prices, P&L |
| operator CLI → paper orders | `--live-1lot` places real paper orders on the shared account | One-share BUY and SELL LIMIT orders |
| repo → filled plist with secrets | The Telegram token lives only in the operator's local copy | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (placeholders in the template) |
| launchd → bot process | Supervisor semantics decide whether a stopped bot restarts | Exit codes, SIGTERM/SIGKILL timing |
| market close | Orders left working past the bell become overnight exposure | Working-order state at close − 1 min |
| operator kill file / SIGINT / SIGTERM → process | Stop must leave no working orders and no flattened positions | Sentinel `.bot_kill_ibs`, signals |
| process start → broker | Paper guard and readiness gate before any job is scheduled | SIMULATE flags, broker-reported account env |
| bot → Telegram / `reports/ibs` HTML | Rendered output contains DB and broker values | Escaped EOD summary and `latest.html` |

---

## Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation (verified evidence) | Status |
|-----------|----------|-----------|----------|-------------|--------------------------------|--------|
| T-12-16 | Repudiation | research provenance | low | mitigate | Scripts byte-identical except path constants; doc §11 reproduction commands; 8 captured assets; `test_research_provenance.py` (5) | closed |
| T-12-17 | Info disclosure | absolute paths / data CSVs | low | mitigate | No `/Users/` in scripts, bot/ibs, doc, assets, runbook; `HERE`/`ROOT` from `__file__`; `data/`, `backtester/results/` gitignored | closed |
| T-12-18 | Tampering | yfinance in live path | medium | mitigate | No yfinance import in bot/ibs or probe; `test_hygiene.py::test_no_forbidden_tokens_in_ibs_package` | closed |
| T-12-SC | Tampering | package installs | n/a | accept | No package added; `requirements*.txt` unchanged vs 5b8b4d2; only stdlib + pinned apscheduler/jsonschema — see Accepted Risks | closed |
| T-12-01 | Tampering | rules-file parsing (`load_ibs_config`) | high | mitigate | `schema.py:30-82` every key required/typed/bounded; `config.py` `_validate` + `_check` cross-field; `ConfigError` fail-closed; 35 bad-value cases + drift guard | closed |
| T-12-04a | Tampering / Info disclosure | state_db / kill_file / report_dir / log_file paths | high | mitigate | `config.py:22-25,134-144` abspath collision guard vs equity/options DB, kill files, report dirs, bot.log; `log_file` bare `*.log` | closed |
| T-12-03a | Elevation of privilege | sizing knobs (leverage) | medium | mitigate | `config.py:104-107` `pct × slots <= 100`; `test_leverage_rejected` | closed |
| T-12-07a | DoS | poll interval vs rate limit | low | mitigate | `schema.py:58` `exclusiveMinimum 3`; poll < TTL check | closed |
| T-12-09 | Repudiation | audit log polluted by tests | medium | mitigate | session-autouse `_isolate_audit_log`; `test_session_audit_log_isolated`; real audit log byte-identical across full-suite run | closed |
| T-12-04b | Tampering | migration 0008 vs live DBs | medium | mitigate | `CREATE … IF NOT EXISTS` only, additive diff; `TestMigration0008` idempotent + v7-upgrade tests | closed |
| T-12-04c | Tampering | duplicate active IBS position | high | mitigate | partial UNIQUE index `ux_ibs_positions_active_code`; 4 tests | closed |
| T-12-06a | Info disclosure / Repudiation | IBS events in bot.log | low | mitigate | `configure_logging(log_name=, force=)` kw-only; `service.py:968`; logger tests; no `logs/` created by tests | closed |
| T-12-05a | Tampering | watchdog equity reconcile vs IBS DB | high | mitigate | `ibs_*` table names only; equity `positions`/`pending_intents` stay empty; `_reconcile_core` SAFE-OG-01; `_position_manager = None` (code evidence; no dedicated test — see Observations) | closed |
| T-12-02 | Tampering | `parse_snapshot` / `compute_ibs` | high | mitigate | `strategy.py:37-86` 7 fail-closed skip reasons, bid/ask never read; `test_parse_snapshot_skips` (11) | closed |
| T-12-02b | Tampering | `size_position` on garbage price | high | mitigate | `strategy.py:116-122` 0 shares for non-finite/≤0; qty < 1 skipped | closed |
| T-12-13 | Repudiation | strategy drift from research | medium | mitigate | `test_parity.py` AST-extracted research `simulate`; divergence pinned | closed |
| T-12-03 | EoP / Tampering | order path (`IbsExecutor`) | high | mitigate | only `LegExecutor.fill_leg` → `gateway.place_order` (NORMAL, audited); bounded escalation; `wait_for` deadline + shielded cancel; hygiene greps | closed |
| T-12-03b | DoS | broker rate limits | medium | mitigate | sequential order loops; no `gather`; poll floor | closed |
| T-12-04 | Tampering | `IbsStore` SQL | high | mitigate | every value `?`-bound; fixed column names only; quote round-trip test incl. `'; DROP TABLE meta;--` | closed |
| T-12-04d | Tampering | duplicate active position | high | mitigate | `IntegrityError` caught at `service.py:633-636`; store + service tests | closed |
| T-12-14 | Repudiation | partial exit P&L lost | medium | mitigate | `record_exit_fill` one trade INSERT + UPDATE under one lock/commit | closed |
| T-12-08 | EoP | real-money order path | high | mitigate | `_readiness_gate` → `gateway.connect()` → `assert_paper_account` triple guard before reconcile | closed |
| T-12-05 | Tampering / Repudiation | reconcile + external holdings | high | mitigate | only own rows compared; mismatch → NEEDS_ATTENTION + one alert, never auto-fixed; externals logged (and alerted once/day, WR-07), never traded | closed |
| T-12-02c | Tampering | stale/invalid snapshot at decision | high | mitigate | `_read_front` fail-closed; decision error alert; per-code exclusion; exit deferred without quote | closed |
| T-12-03c | DoS / Tampering | orders near the close | high | mitigate | **stronger than planned:** deadline = close − hard_cancel_min − `executor_margin_s`; past-deadline guard; per-order worst-case check; config rejects a window that cannot fit | closed |
| T-12-10 | Tampering | duplicate decision after restart / double fire | high | mitigate | `ibs_decision_date` meta key before any broker call; cleared only on pre-order read failure | closed |
| T-12-06 | Tampering / Info disclosure | Telegram HTML | medium | mitigate | `_esc` on every interpolation (AST audit of all 20 `alerter.send` sites); no exception/broker text | closed |
| T-12-03d | Repudiation | order lifecycle audit | medium | mitigate | `on_placed` inserts every order id; `ibs_order_done` audit; gateway audits placements | closed |
| T-12-07 | EoP | probe `--live-1lot` | high | mitigate | exit 2 without `--confirm` before any gateway; paper guard; refusals (universe/held/active row/unreadable DB/decision window → exit 3); read-only mode never places/cancels | closed |
| T-12-07b | DoS / Tampering | plist KeepAlive + kill file | medium | mitigate | `KeepAlive {SuccessfulExit:false}`, SIMULATE/PAPER_TRADING/FUTU_ACC_ID, `ExitTimeOut 60`, SIGTERM handler | closed |
| T-12-07c | Info disclosure | Telegram secrets in plist | medium | mitigate | placeholders only; `chmod 600` guidance; token never logged; test pins placeholder | closed |
| T-12-07d | Tampering | cutover stops the wrong bot | medium | mitigate | runbook: separate sentinels, `pgrep -fl` confirmation, "leave the options bot running" | closed |
| T-12-07e | Tampering | probe creates DB side effect | low | mitigate | **stronger than planned:** sqlite `mode=ro` only if file exists; no `IbsStore.open()`; bytes-unchanged test | closed |
| T-12-05b | Tampering | external-holding entry guard | high | mitigate | fresh post-exit broker read; externals excluded; read failure skips ALL entries + alert | closed |
| T-12-10b | Tampering | duplicate BUY for a code | high | mitigate | OPENING row before BUY under unique index; working-order/active/same-session exclusion | closed |
| T-12-03e | Tampering | quantity from a bad price | high | mitigate | entries only from `parse_snapshot`-validated quotes; qty < 1 skipped | closed |
| T-12-03f | DoS | orders left working overnight | high | mitigate | `_job_hard_cancel`: cancel decision → flag mid-order rows → `_sweep_orders` (status-first, CANCEL_FAILED alert); DAY TIF backstop | closed |
| T-12-06b | Tampering / Info disclosure | Telegram HTML (entries/cancels) | medium | mitigate | same `_esc` discipline; cancel-failure test asserts no broker text | closed |
| T-12-03g | Repudiation | cancels not audited | medium | mitigate | `ibs_order_cancel` / `ibs_order_cancel_failed` / `ibs_order_already_closed` audit entries | closed |
| T-12-07f | DoS / Tampering | kill-switch shutdown | high | mitigate | ruling-8 order (cancel decision → sweep → close → alert → scheduler), each step isolated; positions left held; SIGTERM test | closed |
| T-12-08b | EoP | startup without paper guard | high | mitigate | `run()` starts with `_readiness_gate`; connect failure never starts the scheduler; hygiene forbids `unlock_trade` | closed |
| T-12-03h | Tampering | jobs at wrong time / late wake | high | mitigate | DateTriggers from `get_market_close_et` (half-day aware); passed slots skipped; coalesce/max_instances/misfire grace; past-deadline guard | closed |
| T-12-06c | Tampering / Info disclosure | EOD Telegram + HTML | medium | mitigate | `_esc` on every value/cell; static style; no `<script`; `test_eod_escapes_values` | closed |
| T-12-06d | Info disclosure / Repudiation | IBS logs in bot.log | low | mitigate | `configure_logging(log_name=cfg.log_file, force=True)`; logger tests | closed |
| T-12-01b | Tampering | invalid rules at launch | high | mitigate | `ConfigError` → stderr + exit 1 before any gateway; test | closed |
| T-12-15 | Tampering | regressions to equity/options bots | high | mitigate | empty diff vs 5b8b4d2 on bot/options, bot/service, bot/signal, bot/position, bot/execution, scanner, gateway, safety core; additive 7-line dispatch; 1715 passed | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-12-01 | T-12-SC | Supply-chain tampering via package installs: no package is installed or added in Phase 12; `bot/ibs` and the probe use only the stdlib plus already-pinned `apscheduler` and `jsonschema` (`requirements*.txt` unchanged vs 5b8b4d2; `12-RESEARCH.md` Package Legitimacy Audit: none). Re-audit on any future dependency change. | operator (plan-time disposition, 12-01-PLAN.md) | 2026-10-04 |

---

## Informational Observations (not registered threats; non-blocking)

1. T-12-05a is closed by code evidence only — there is no dedicated regression test of the watchdog's equity `startup_reconcile` against an `IbsStore`.
2. `risk.sizing_equity_usd` and `signal.max_snapshot_age_s` have no upper bound in the schema; the cross-field checks bound only the percentage and the order-window fit. A typo could 10× order size (the paper broker's buying power backstops it).
3. The T-12-04a path-collision guard uses `os.path.abspath`, not `realpath`; a symlink alias to the equity DB would evade it (as planned).
4. If the paper guard fails inside `connect()`, `run()`'s `finally` still runs `_shutdown`, whose sweep may cancel `ibs_orders` rows still WORKING in this bot's own DB — cancel-only, own ids only.
5. Code-review items IN-01 (bid/ask limit anchoring), IN-03 (shutdown cosmetics), IN-04 (research-script hygiene) remain deferred and weaken no registered mitigation.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-10-04 | 46 | 46 | 0 | gsd-security-auditor (sonnet, verify-mitigations mode; read-only checks: full suite 1715 passed / 1 skipped, hygiene greps, `plutil -lint`, diff vs 5b8b4d2, audit-log byte-identity) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-10-04
