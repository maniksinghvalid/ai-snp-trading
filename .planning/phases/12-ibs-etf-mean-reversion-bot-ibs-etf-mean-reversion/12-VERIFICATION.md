---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
verified: 2026-10-04T23:00:00Z
status: human_needed
score: 10/10 requirements verified (automated); 4 operator-run live items pending
overrides_applied: 0
re_verification: false
human_verification:
  - test: "RTH read-only probe: PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/uat_ibs_probe.py, run between 15:30 and 15:55 ET on a trading day"
    expected: "All 17 rows fresh (age_s well under signal.max_snapshot_age_s = 900), IBS values plausible against a chart, ask-bid within the 0.05 limit buffers; the printed would-do list matches expectations; nothing is placed"
    why_human: "Needs live OpenD during a regular session; snapshot freshness/update_time semantics and buffer calibration cannot be checked offline (12-VALIDATION.md manual item 1)"
  - test: "python3 scripts/uat_ibs_probe.py --live-1lot --confirm --symbol US.XLU (same env vars), during RTH, on a flat universe ETF"
    expected: "BUY 1 share fills, SELL 1 share fills through the full TTL/re-price/cancel path; audit log + scratch ibs_orders rows consistent; no '1 share still held' message"
    why_human: "Places real (paper) orders; no ETF round trip has been observed on the SIMULATE account yet; operator-run by design (manual item 2). Also confirms a SIMULATE rejection surfaces as expected (RESEARCH Pitfall 9)"
  - test: "launchd supervision: copy deploy/com.bot.ibs.plist to ~/Library/LaunchAgents, fill OPERATOR values, launchctl load, then touch .bot_kill_ibs"
    expected: "Process exits on the kill file and is NOT restarted (KeepAlive SuccessfulExit=false); positions stay held; working orders cancelled; 'IBS bot stopped' Telegram alert"
    why_human: "Host process management; cannot be exercised without starting the bot (manual item 3)"
  - test: "Cutover per deploy/IBS-RUNBOOK.md: stop Trend Join Long (touch .bot_kill / Ctrl-C), leave options bot, start IBS"
    expected: "pgrep -fl 'python3 -m bot' shows only --rules rules_options.json and --rules rules_ibs.json; ibs.log shows ibs_readiness_gate_passed and jobs armed 15:50/15:59/16:05 ET (12:50/12:59/13:05 half-day); first-week checks in runbook section 6 pass"
    why_human: "Operator-run process control on the live host; OD-4 stop of Trend Join Long is an operator action by design (manual item 4)"
---

# Phase 12: IBS ETF mean-reversion bot Verification Report

**Phase Goal:** A new, separate paper-trading bot process (`python3 -m bot --rules rules_ibs.json`, self-contained `bot/ibs/` mirroring `bot/options/`) trading the IBS mean-reversion rule on 17 liquid US ETFs (buy IBS < 0.20 at close-10min, sell IBS > 0.80 or after 10 trading days, 10 slots of 10% of $100k, LIMIT only, overnight holds by design) with the full safety stack and operator cutover tooling.
**Verified:** 2026-10-04
**Status:** human_needed (every automated check passes; only the four operator-run live items remain)
**Re-verification:** No, initial verification (no prior VERIFICATION.md)

## Automated Gate (run by the verifier, not taken from SUMMARY)

| Check | Command | Result |
|-------|---------|--------|
| Full suite | `python3 -m pytest -q` | 1715 passed, 1 skipped (matches expectation); `tests/ibs` alone: 297 passed |
| Forbidden tokens | `grep -rnE "OrderType\.MARKET\|force_close\|unlock_trade\|<script" bot/ibs` | empty (rc=1) |
| Also absent | `grep -rn "subscribe\|yfinance" bot/ibs` | empty |
| Shared-code regression | `git diff --stat 5b8b4d2 -- bot/options bot/service bot/signal bot/position bot/execution bot/scanner/scanner.py` | empty |
| Plist | `plutil -lint deploy/com.bot.ibs.plist` | OK |
| Live-1lot guard | `python3 scripts/uat_ibs_probe.py --live-1lot` | prints "add --confirm to proceed", exit 2, before any gateway |
| Research scripts | `py_compile` on all six; `grep "/Users/\|.scratch"` in `backtester/experimental/ibs_search` | all compile; no absolute/scratch paths; `def simulate(px, entry, exit_, rank, cost, slots=10, max_hold=10, next_open=False)` intact |
| Behavioural smoke (direct import) | `load_ibs_config('rules_ibs.json')` + pure functions | 17 codes; size 99 sh at 100.05; entry ranks lowest IBS first and caps at free slots; exits emit `ibs` and `time` (time fires with IBS unknown); half-day close 13:00 (2026-11-27), trading_days_held skips the Thanksgiving holiday; `parse_snapshot` accepts fractional-second `update_time` |

## Observable Truths (ROADMAP goal decomposed + PLAN must_haves)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Separate process dispatched by `--rules rules_ibs.json`; equity/options paths unchanged | VERIFIED | `bot/main.py:88-91` routes `strategy_name == "ibs_etf_mean_reversion"` to `bot.ibs.service.main` via deferred import; tests/ibs/test_dispatch.py (IBS routes, shipped file routes, options route unchanged, equity route never imports IBS) pass; protected-path diff empty |
| 2 | Self-contained `bot/ibs/` (schema, config, strategy, store, execution, service) with strategy numbers only in `rules_ibs.json` | VERIFIED | 6 modules, 1733 lines, all substantive; hygiene test `test_no_strategy_literals_in_ibs_package` plus my own grep find no 0.2/0.8/10000/100000/"US.*" literals outside schema/config; `rules_ibs.json` holds every knob |
| 3 | 17-ETF universe exact (D-01) | VERIFIED | `rules_ibs.json` universe equals the D-01 list in order; drift guard in test_schema_config |
| 4 | IBS = (last-low)/(high-low) fail-closed per code (D-02) | VERIFIED | `strategy.py:compute_ibs/parse_snapshot` reject NaN/<=0/high<=low/last outside range/suspended/stale date/stale age; snapshot field names (`last_price`, `high_price`, `low_price`, `update_time`, `suspension`) confirmed in the installed moomoo SDK `quote_query.py` |
| 5 | Entry IBS < 0.20, not held, free slot, ranked lowest IBS first (D-03); exit IBS > 0.80 or >= 10 trading days, exits before entries, freed slots reusable same day (D-04) | VERIFIED | `decide_entries/decide_exits`; `_decide` runs `_run_exits` then `_run_entries` with `free = max_concurrent - len(active after exits)`; `trading_days_held` over `trading_days_between` (NYSE calendar, verified manually across a holiday); AST-extracted parity test vs the real research `simulate` passes |
| 5a | Documented deviation: no same-session re-buy of a code just exited (orchestrator ruling 3) | VERIFIED (deliberate divergence) | `excluded = active | exited | external | working` in `_run_entries`; pinned by a dedicated parity-divergence test and disclosed in the research doc limitations. See Notes |
| 6 | Sizing: 10 slots x 10% x $100k, whole shares, qty < 1 skipped (D-05); one active row per code | VERIFIED | `size_position` = floor(sizing x pct / 100 / limit); `_run_entries` skips `qty_lt_1`; migration 0008 partial unique index `ux_ibs_positions_active_code`; IntegrityError path skips the code; OPENING row inserted before BUY |
| 7 | LIMIT orders only; TTL then bounded re-price; hard cancel at close-1min; no overnight open orders (D-08/D-09, IBS-05) | VERIFIED | `IbsExecutor` delegates only to `LegExecutor.fill_leg` (marketable limit, bid=ask=last, entry/exit buffers via two adapters), bounded by `asyncio.wait_for` to hard-cancel minus `executor_margin_s`; no `OrderType.MARKET` in package; `_job_hard_cancel` cancels in-flight decision, flags OPENING/CLOSING rows, `_sweep_orders` cancels every WORKING row (status read first, CANCEL_FAILED alerts); unfilled entry -> ABORTED, unfilled exit -> stays OPEN with `exit_pending` and retried every session |
| 8 | Overnight holds by design; no force-close job or call (D-06) | VERIFIED | Jobs registered are only `ibs_arm`, `ibs_decide`, `ibs_hard_cancel`, `ibs_eod`; shutdown leaves positions held; hygiene + lifecycle tests assert it |
| 9 | Timing calendar-aware: decide close-10, cancel close-1, EOD close+5; half-day aware; non-trading-day no-op; mid-day restart after decision time makes no decision (D-07) | VERIFIED | `arm_today` builds DateTriggers from `get_market_close_et` (15:50/15:59/16:05 normal; 12:50/12:59/13:05 half-day), skips passed slots, no-op when `is_trading_day` false; `_job_decide` guards (non-trading day, kill switch, `ibs_decision_date` meta, past deadline, entries disabled) and writes the meta key before any broker call; one batched `get_market_snapshot` |
| 10 | Safety stack: paper guard via `gateway.connect()`, readiness gate order, own-DB-only reconcile (SAFE-OG-01 analog), external-holding entry guard, no auto-fix (D-10..D-12) | VERIFIED | `_readiness_gate`: connect -> `reconcile(startup=True)` -> kill switch install -> entries enabled; mismatch/missing/OPENING/CLOSING -> NEEDS_ATTENTION + one alert; external universe holdings counted/logged/alerted once per session, never adopted or traded; `_run_entries` re-reads the broker after exits and fails closed (no entries) when unreadable; `unlock_trade` absent |
| 11 | Ops: kill switch, watchdog, Telegram, EOD HTML report, own DB/kill/log/report paths (D-13/D-14) | VERIFIED | `main()`: `configure_logging(log_name=cfg.log_file, force=True)`, `IbsStore(cfg.state_db)`, `KillSwitch(sentinel_path=cfg.kill_file)`, `OpenDWatchdog` injected (reads flat `watchdog_*` cfg attrs, present on IbsConfig); config `_check` rejects any path colliding with the equity/options bots (`bot_state.db`, `options_state.db`, `.bot_kill`, `.bot_kill_options`, `reports`, `reports/options`, `bot.log`); `_job_eod` writes Telegram summary naming the strategy + HTML (all cells `html.escape`d, no script) to `reports/ibs/`; `_shutdown` order cancel decision -> sweep -> gateway.close -> alert -> scheduler |
| 12 | Operator tooling: read-only probe, `--live-1lot --confirm`, launchd plist, runbook (D-17, IBS-09) | VERIFIED (artifacts) | `scripts/uat_ibs_probe.py` imports the production pure functions, `--live-1lot` exits 2 without `--confirm`, refuses non-universe/held/in-window symbols (exit 3); plist parses, PAPER_TRADING/SIMULATE/FUTU_ACC_ID, `KeepAlive {SuccessfulExit: false}`, placeholder-only secrets; `deploy/IBS-RUNBOOK.md` covers cutover, start/stop via `.bot_kill_ibs`, first-week checks, rollback. Live execution is a human item |
| 13 | Provenance: six research scripts + results doc (IBS-10) | VERIFIED | Scripts present and compile; `docs/research/2026-10-04-ibs-etf-strategy-search.md` (413 lines) contains every CONTEXT reference number (16.4/1.30/-11.2/1.63/3164, 12.7/1.07/-21.7, IS 9.1/0.91, SPY 17.3/0.93/-33.7), OD-1..OD-4 and a Limitations section; captured evidence under `docs/research/assets/2026-10-04-ibs-etf-strategy-search/` |

**Score:** 13/13 truths verified.

## Operator Decisions

| ID | Honored? | Evidence |
|----|----------|----------|
| OD-1 unlevered IBS-17 | Yes | `position_pct_of_equity x max_concurrent_positions > 100` raises ConfigError; shipped 10 x 10 = 100 |
| OD-2 separate process mirroring `bot/options/` | Yes | own process/DB/kill/log/report; `TradingBot` not subclassed; protected-path diff empty |
| OD-3 near-close fill, decide at close-10min | Yes | `decision_before_close_min` 10 via calendar-aware DateTrigger |
| OD-4 Trend Join Long stopped at cutover | Yes (documented, operator-run) | Runbook section 1; actual stop is human item 4 |

## Decisions D-01..D-19

D-01..D-06 verified above (truths 3-8). D-07 truth 9. D-08/D-09 truth 7. D-10..D-12 truth 10. D-13/D-14 truth 11. D-15 (TDD, isolation): session-autouse `_isolate_audit_log` fixture in `tests/conftest.py`; logger default-dir patch intact; suite 1715 passed. D-16 pure core + AST-extracted parity: verified. D-17 probe: verified (artifact). D-19 ponytail: no registry/yfinance/backtester rewrite; execution reuses `LegExecutor` by import with zero edits to `bot/options`. D-18: see Notes (threat IDs T-12-xx are tracked in the plans and the validation map; the `/gsd-secure-phase` audit artifact has not been produced yet).

## Requirements Coverage (source: 12-CONTEXT.md `<requirements>`; absent from .planning/REQUIREMENTS.md by design)

| Req | Declaring plans | Status | Evidence |
|-----|-----------------|--------|----------|
| IBS-01 | 02 | SATISFIED | schema + frozen IbsConfig, ConfigError fail-closed, no `sys.exit` in loader, no literals |
| IBS-02 | 09 | SATISFIED | dispatch branch + 4 dispatch tests |
| IBS-03 | 04 | SATISFIED | pure strategy + parity test vs research `simulate` |
| IBS-04 | 06, 08, 09 | SATISFIED | arm/decide jobs, exits-before-entries, one snapshot |
| IBS-05 | 05, 06, 08, 09 | SATISFIED | LIMIT-only executor, TTL/escalation, hard-cancel sweep, audit log |
| IBS-06 | 03, 05 | SATISFIED | migration 0008, IbsStore, `trading_days_between` |
| IBS-07 | 06, 08 | SATISFIED | readiness gate, own-DB reconcile, external-holding guard |
| IBS-08 | 03, 09 | SATISFIED | kill switch, watchdog, alerts, EOD, own log via `configure_logging(log_name, force)` |
| IBS-09 | 07 | SATISFIED (artifacts); live run pending | probe, plist, runbook, CLAUDE.md section |
| IBS-10 | 01 | SATISFIED | scripts + results doc |

No orphaned requirements: every IBS-01..IBS-10 ID appears in at least one PLAN `requirements` field.

## Anti-Patterns

No TBD/FIXME/XXX debt markers or stub patterns found in `bot/ibs`; grep for forbidden tokens empty. No blockers.

## Code-Review History

Three review iterations: iteration 1 found 2 critical / 6 warning / 5 info, iteration 2 found 0 / 1 / 6, iteration 3 (`12-REVIEW.md`) is clean (0 / 0 / 3 info; IN-13 applied in b1de276). Fixes recorded in `12-REVIEW-FIX.md` (iteration 2) and `12-REVIEW-FIX.iter2.md` (iteration 1): 17 findings fixed test-first. Deferred as non-blocking infos from iteration 1: IN-01, IN-03, IN-04 (not re-raised by the reviewer).

## Notes (non-blocking)

1. **Ruling 3 divergence from research.** RESEARCH Pitfall 4 recommended research parity (same-session re-buy after a time exit) and flagged it as Open Question 5 needing an explicit operator decision. The orchestrator ruled to forbid it. It is disclosed in the results doc and pinned by a test, so it is honest, but the 16.4% CAGR reference includes those wash trades. The operator may want to acknowledge this at cutover.
2. **D-18 secure-phase audit.** Per-plan threat models exist (T-12-xx), but no `12-SECURITY.md` exists yet (Phase 11 has one). Run `/gsd-secure-phase 12` as the follow-up GSD step; not a goal gap.
3. **Info:** `parse_snapshot` does not require `volume > 0` (RESEARCH Pitfall 3 suggested it); the other fail-closed checks plus the staleness bound cover the stated D-02 requirement.
4. **Working-tree hygiene for the orchestrator:** `12-REVIEW.md` is modified and `12-REVIEW.iter3.md` and `.scratch/` are untracked; `.scratch/` should not be committed.

## Gaps Summary

None. All truths, artifacts, key links, requirements, operator decisions and automated hygiene checks pass. The status is `human_needed` solely because the four live items in 12-VALIDATION.md "Manual-Only Verifications" (RTH probe, `--live-1lot --confirm`, launchd load + kill-file stop, cutover) are operator-run by design and require a live OpenD session and host process control.

---

_Verified: 2026-10-04_
_Verifier: Claude (gsd-verifier)_
