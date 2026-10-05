# Phase 12: IBS ETF mean-reversion bot (ibs_etf_mean_reversion) - Research

**Researched:** 2026-10-04
**Domain:** Separate daily-bar mean-reversion paper-trading bot process on moomoo/OpenD (Python asyncio, APScheduler, SQLite), mirroring `bot/options/`
**Confidence:** HIGH for SDK/codebase facts (read from installed SDK source, repo source, and a live read-only OpenD probe run during this research); MEDIUM for live-fill behaviour of ETF LIMIT orders on SIMULATE (not yet exercised; that is what `--live-1lot` is for)

<user_constraints>
## User Constraints (from 12-CONTEXT.md)

### Locked Decisions

**Operator decisions (FINAL — do not re-litigate)**

- **OD-1 Sizing: unlevered IBS-17.** NOT the 2x variants (those doubled return and 2–2.5x the drawdown with a lower Sharpe; rejected by the operator).
- **OD-2 Deployment: new separate bot process** mirroring `bot/options/`; the equity bot code is untouched.
- **OD-3 Fill timing: near the close**, decisions from the live snapshot at close − 10 min; orders before the bell. (Backtest "close fill" column is the target; "next-open" column is the conservative floor.)
- **OD-4 Trend Join Long is STOPPED at cutover.** It showed no edge in every test. Its unmerged scanner fixes on this branch (commits d2720f1, 568e310, 6cda8cc, 9bf3222) ride along to develop but become moot.

**Strategy (fixed by the research; every number lives in `rules_ibs.json`, schema in `bot/ibs/schema.py`, NO hardcoded strategy literals in Python)**

- **D-01 Universe** (17 codes, Moomoo format): `US.SPY US.QQQ US.IWM US.DIA US.XLK US.XLF US.XLE US.XLV US.XLI US.XLY US.XLP US.XLU US.XLB US.TLT US.GLD US.EFA US.EEM`.
- **D-02 Signal**: `IBS = (last − low) / (high − low)` from TODAY's session snapshot (day high, day low, last price). Fail closed per code: skip when any field is missing/NaN/≤ 0, when `high == low`, or when the snapshot is not from today's session.
- **D-03 Entry**: `IBS < signal.ibs_entry_max` (0.20), code not already held (and no working entry order), a free slot available. Candidates ranked ascending by IBS (lowest first). Research rule reference: `strategy_search.py::simulate` entry branch.
- **D-04 Exit**: `IBS > signal.ibs_exit_min` (0.80) at decision time, OR `trading_days_held >= signal.max_hold_trading_days` (10), where `trading_days_held` = number of NYSE trading days strictly after the entry date up to and including today (matches `t − d0 >= max_hold` in the research sim). Exits are decided and placed BEFORE entries; slots freed by today's exits are usable by today's entries (research: `free = slots − (len(pos) − len(exits))`).
- **D-05 Sizing**: `risk.max_concurrent_positions` = 10 slots; each position targets `risk.position_pct_of_equity` (10%) × `risk.sizing_equity_usd` (100000) → `qty = floor(10000 / limit_price)`; skip if qty < 1. Equal weight, no pyramiding, one position per code.
- **D-06 Overnight holds are the design.** This bot has NO 15:51 force-close, must never call the equity bot's `force_close_all`, and must never be affected by it (separate process + separate DB already guarantee this; a test must assert the IBS service registers no force-close job).
- **D-07 Timing** (calendar-aware via `bot/scanner/calendar.py`): decision job at `get_market_close_et(today) − service.decision_before_close_min` (10); hard-cancel sweep at `close − service.hard_cancel_before_close_min` (1); post-close reconcile + EOD report at `close + service.eod_report_after_close_min` (5). Non-trading days: every job no-ops. Mid-day restart after the decision time: no decision today (jobs are time-triggered with the same coalesce/misfire-grace pattern as the equity bot).

**Execution invariants (inherit from the codebase)**

- **D-08 LIMIT orders only** (`OrderType.NORMAL`; MARKET is never submitted — EXEC-02). Marketable limit: BUY at `last + execution.entry_limit_buffer_usd`, SELL at `last − execution.exit_limit_buffer_usd`. TTL (`execution.order_ttl_seconds`) then bounded re-price toward the market by `execution.escalation_step_usd` up to `execution.max_reprices` times — the same shape as `bot/execution/engine.py`'s TTL/escalation (reuse its helpers where they are pure; do not import the equity engine's position-manager coupling).
- **D-09 No overnight open orders.** Whatever is still working at `close − 1 min` is cancelled. An unfilled entry is simply skipped that day (no carry-over intent). An unfilled/partial exit leaves the position open; it is re-evaluated next session (an exit is retried every session until flat) and raises a Telegram warning.
- **D-10 Paper only.** `bot/safety/paper_guard.py` triple-check on connect (SIMULATE env + account env check + broker-reported env). Every placed/modified/cancelled order is appended to the audit log (SAFE-05). `unlock_trade` is never called.
- **D-11 Startup readiness gate** (mirrors Phase 8 D8): `gateway.connect()` → reconcile THIS bot's open positions (own DB) against broker positions → enable trading. Qty mismatch or a DB position the broker no longer holds → mark `NEEDS_ATTENTION` + alert; never auto-trade to "fix" it.
- **D-12 SAFE-OG-01 analog**: the bot only ever manages codes that appear on a position row in ITS OWN database. A broker holding in a universe ETF that this bot did not buy (equity bot leftovers, manual holdings, options assignments) is counted, logged, and never closed, adopted, or sized against. Entry guard: do not enter a code the broker already holds externally (same rule the options bot got in 260926-kvt).
- **D-13 Shared-account coexistence**: no K_5M subscriptions needed (snapshot is subscription-free); if any subscription is used, `is_all_conn=False` semantics apply. Own DB (`data/ibs_state.db`), own kill file, own report dir (`reports/ibs/`), own log file (`logs/ibs.log`) — follow the exact D6 naming pattern used by `bot/options/service.py`.
- **D-14 Operations**: kill-switch graceful shutdown (cancel working orders, flush DB, leave positions — they are meant to be held), OpenD watchdog with reconnect, structured JSON logs, Telegram alerts (entry filled, exit filled, exit retry/partial, reconcile mismatch, errors, daily summary naming the strategy), EOD HTML report (positions held, trades closed, realized P&L, mark-to-market), launchd plist under `deploy/` + runbook.

**Quality bar**

- **D-15 TDD**: failing test first for every behaviour; full suite green (baseline 1401 passed / 1 skipped on `claude/awesome-gould-a0cef9`). Tests never write to `logs/bot.log`, any production DB, or the real audit log (existing conftest isolation; extend it for the new log/DB paths).
- **D-16 Pure strategy core**: `bot/ibs/strategy.py` holds pure functions (`compute_ibs`, `decide_exits`, `decide_entries`, `size_position`, `trading_days_held`) with no I/O, plus a PARITY test that replays a small synthetic multi-day scenario through both the research sim's rules (ported verbatim from `strategy_search.py::simulate`'s decision logic) and the production functions and asserts identical entry/exit decisions.
- **D-17 Operator UAT path**: `scripts/uat_ibs_probe.py` — read-only during RTH: snapshot → IBS table (code, high, low, last, IBS) → what the bot WOULD do (exits/entries/qty), places nothing; plus `--live-1lot --confirm` operator-run smoke mode placing ONE 1-share paper order with the full TTL/cancel path, like `scripts/uat_options_probe.py`.
- **D-18 Security**: per-phase threat model + `/gsd-secure-phase` audit as usual (new attack surfaces: rules file parsing, snapshot data trust, DB, Telegram).
- **D-19 Ponytail**: minimal build. No new abstractions beyond what `bot/options/` already proved necessary; no plugin/strategy registry; no backtester harness rewrite; no yfinance in the live path. Research scripts move to `backtester/experimental/ibs_search/` unchanged except for path constants.

**First deliverable (Plan 1): research provenance** — Commit `backtester/experimental/ibs_search/{strategy_search.py, strategy_search_r2.py, ibs_robust.py, r5_robust.py, ibs_sizing.py, screen_study.py}` and write `docs/research/2026-10-04-ibs-etf-strategy-search.md` in the style of `docs/research/2026-08-18-external-strategies-results.md`: pre-registered hypotheses and pass criteria for both rounds, the verdict tables, robustness (cost x1/x2/x4, leave-one-out, per-ETF standalone, yearly OOS), the sizing table, the 2026-10-03 screen-study result, and the explicit limitations (survivorship for stock universes, backtest ≠ live, "close fill" assumes a 15:50 decision). Reference numbers (OOS 2019-01-01..2026-10-02, net of 0.02%/side, 17 ETFs, 10 slots): close fill CAGR 16.4%, Sharpe 1.30, maxDD −11.2%, PF 1.63, 3164 trades, positive 7/8 years; next-open fill CAGR 12.7%, Sharpe 1.07, maxDD −21.7%. IS 2010-2018: CAGR 9.1%, Sharpe 0.91. SPY buy & hold OOS: 17.3% / 0.93 / −33.7%. Flat years: 2022 +4%, 2023 +2%, 2024 −2%.

### Claude's Discretion

CONTEXT.md has no explicit "Claude's Discretion" section. The `<open_questions>` block (SDK snapshot columns, position-query suitability, D6 naming) is resolved below. By implication the following are discretionary and are recommended here: the execution-helper reuse strategy, DB table design, scheduler arming pattern, config key shapes beyond those named, the default execution buffer values, and the test layout.

### Deferred Ideas (OUT OF SCOPE)

- 2x / mixed-leverage variants (operator rejected).
- A dedicated IBS backtester arm in `backtester/` (research scripts are the reference).
- Regime filters, volatility scaling, additional ETFs, parameter sweeps (no re-tuning — the edge is the published rule).
- Equity-bot retirement code changes (operator stops the process; code stays).
- Options bot changes.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IBS-01 | `rules_ibs.json` validated by `bot/ibs/schema.py`; `ConfigError` fail-closed; no strategy literal in Python | Mirror `bot/options/{schema,config}.py` (jsonschema + `ConfigError` re-used from `bot.config.loader`); proposed key shape in "Config shape"; cross-field checks listed; drift-guard test vs shipped file |
| IBS-02 | `python3 -m bot --rules rules_ibs.json` routes to `bot.ibs.service.main`; equity + options dispatch unchanged | One new `elif` in `bot/main.py` next to the options branch (`bot/main.py:80`); dispatch test pattern from `tests/options/test_dispatch.py`; regression tests keep options/equity routes |
| IBS-03 | Pure D-02..D-05 functions + research-parity test | Exact research logic extracted (`strategy_search.py:51-94`); parity design that runs the REAL research `simulate` (AST-extracted, no yfinance import) against production functions; 3 research behaviours that the CONTEXT text does not spell out are documented in Pitfalls 4-6 |
| IBS-04 | Daily decision job at close − 10 min, half-day aware, non-trading-day no-op, exits before entries, one batched snapshot | Verified live: one `get_market_snapshot` call for all 17 codes, no subscription; `get_market_close_et` verified for 2026-11-27 / 12-24 (13:00); arming pattern (daily cron arms one-shot DateTrigger jobs with `replace_existing=True`) from `bot/service/bot.py:630-665` lessons |
| IBS-05 | LIMIT-only marketable orders with TTL/escalation, hard cancel at close − 1 min, no overnight open orders, audit-logged | Reuse `bot.options.execution.LegExecutor.fill_leg` through two config adapters; `asyncio.wait_for` deadline; sweep job; DAY time-in-force is the broker-side backstop; rate limits (place 15/30s, order_list 10/30s, positions 10/30s per acc_id) |
| IBS-06 | Own SQLite DB: positions (entry date, qty, avg fill, status), trades (realized P&L), orders; idempotent per-session; `trading_days_held` from NYSE calendar | `IbsStore(StateStore)` with distinct `ibs_*` tables; partial unique index for one-active-row-per-code; `meta` key for per-session idempotency; one additive calendar helper |
| IBS-07 | Readiness gate + reconcile + SAFE-OG-01 analog + external-holding entry guard | `OptionsBot.reconcile` structure (`bot/options/service.py:427-582`) ported to share positions; verified live that `position_list_query` returns share rows with float `qty`, `cost_price`, `average_cost`, `position_side`; account holds unrelated human ETF/stock shares so the guard is exercised for real |
| IBS-08 | Kill switch, watchdog, Telegram, EOD report, structured logs, own log/DB/kill/report paths | Exact option-bot wiring documented; `OpenDWatchdog` duck-typing requirements (`_entries_enabled`, `_store`, `_position_manager`, `_bar_agg`, cfg `watchdog_*`); own-log-file gap (Pitfall 1) and fix |
| IBS-09 | `scripts/uat_ibs_probe.py` (read-only + `--live-1lot --confirm`); `deploy/` launchd plist; runbook for cutover + Monday checks | Probe mirrors `scripts/uat_options_probe.py`; plist template gaps found (no `FUTU_ACC_ID`, `KeepAlive=true` defeats the kill file); both live bots currently run from a terminal, not launchd |
| IBS-10 | Research scripts committed under `backtester/experimental/ibs_search/` + results doc | Hard-coded `ROOT` / `.scratch` paths enumerated; `strategy_search.py` executes a yfinance download at import time (cannot be imported by tests) |
</phase_requirements>

## Summary

Phase 12 is a re-skin of the Phase 8 options-bot architecture around a much simpler strategy. Every piece of infrastructure already exists and was exercised live: `MoomooGateway` (connect + triple paper guard, snapshot, positions, place/cancel/status), `KillSwitch`, `append_audit`, `OpenDWatchdog`, `TelegramAlerter`, `write_reports`, `StateStore` subclassing, `LegExecutor` (TTL / cancel-replace escalation / cancel-on-exception), the `calendar` helpers, and the `bot.main` dispatch peek. The new code is: a pure strategy module, a schema/config pair, a small store, a thin execution adapter, a scheduler/service class, one `bot/main.py` branch, the probe script, the plist and runbook, and the research provenance move. No new third-party packages are needed (Package Legitimacy Audit: none).

The SDK questions in CONTEXT's `<open_questions>` are fully resolved, and two of the answers contradict the documentation or CONTEXT's assumptions, so they change the design. (1) The snapshot columns are `update_time`, `last_price`, `open_price`, `high_price`, `low_price`, `prev_close_price`, `volume`, `suspension`, `sec_status` (SDK source `open_quote_context.py:412-545`), returned for ETFs with no subscription. A live probe run during this research (Sunday 21:19 ET) showed `high_price`/`low_price` are REGULAR-SESSION ONLY (several ETFs have `pre_low_price < low_price` / `pre_high_price > high_price`), and that `update_time` carries fractional seconds (`"2026-10-04 21:19:04.863"`) and is the time of the last quote update, not the session date. Outside RTH it reflects the overnight (24h) session, so the date-equals-today test alone does not prove the data is from today's regular session; a `low <= last <= high` consistency check and an age bound are needed. (2) The codebase has NO precedent for a per-bot log file: the equity and options bots both write `logs/bot.log`, and `configure_logging` hard-codes that name and is guarded against being called twice. `logs/ibs.log` (D-13) therefore needs a small additive change to `bot/safety/logger.py`.

**Primary recommendation:** Build `bot/ibs/` as a one-for-one clone of the `bot/options/` file layout (`schema, config, strategy, store, execution, service`), reuse `LegExecutor` through two `SimpleNamespace` config adapters (BUY/SELL buffers differ) with `bid = ask = last`, work orders sequentially under an `asyncio.wait_for` deadline, arm the three daily jobs as one-shot `DateTrigger`s from a daily cron job with `replace_existing=True`, and prove strategy equivalence by running the REAL research `simulate` function (AST-extracted) against the production pure functions on a synthetic multi-day scenario.

## Architectural Responsibility Map

Not a web application; tiers are mapped to this system's actual components.

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| IBS computation, exit/entry decisions, sizing, held-days count | Pure strategy core (`bot/ibs/strategy.py`) | — | No I/O; unit + parity testable; shared verbatim by service and probe so the probe cannot drift |
| Snapshot read, positions, order place/cancel/status | Broker gateway (`MoomooGateway` → OpenD) | — | Existing paper-guarded access layer; IBS adds no new SDK call |
| Scheduling (decision / hard-cancel / EOD), calendar awareness | Service (`IbsBot` + AsyncIOScheduler) | `bot/scanner/calendar.py` | Close time varies on half-days; scheduler arms DateTrigger jobs each morning |
| Order working (TTL, re-price, cancel-on-error) | Execution adapter (`bot/ibs/execution.py` over `LegExecutor`) | Gateway | Proven CR-03/CR-04 semantics reused; adapter only maps config names |
| Position/trade/order persistence, per-session idempotency | SQLite (`IbsStore(StateStore)`, `data/ibs_state.db`) | — | Own DB per D6; DB-level one-active-row-per-code invariant |
| Reconcile + SAFE-OG-01 + external-holding guard | Service (`IbsBot.reconcile`) | Gateway positions read | Broker is truth; only codes on IBS rows are ever acted on |
| Alerts, EOD HTML | `TelegramAlerter` + `write_reports` | Service formatters | Reused; all interpolated values `html.escape`d |
| Operator UAT / cutover | `scripts/uat_ibs_probe.py`, `deploy/`, runbook | — | Operator-run only; the agent never executes live steps |

## Standard Stack

### Core

No new dependencies. Everything below is already installed and pinned in `requirements.txt`.

| Library | Version (installed) | Purpose | Why Standard |
|---------|---------------------|---------|--------------|
| moomoo-api | 10.07.6708 [VERIFIED: `python3 -c "import moomoo"`] | Snapshot, positions, orders | Project-mandated broker SDK (CLAUDE.md) |
| apscheduler | 3.11.2 [VERIFIED: import] | `AsyncIOScheduler`, `CronTrigger`, `DateTrigger` | Already the scheduler for equity + options bots |
| pandas-market-calendars | 5.4.0 [VERIFIED: import] | NYSE sessions, holidays, early closes | Backs `bot/scanner/calendar.py` |
| jsonschema | 4.26.0 [VERIFIED: import] | `rules_ibs.json` validation | Same as `bot/options/schema.py` |
| structlog | 26.1.0 [VERIFIED: import] | JSON logs | `bot/safety/logger.py` |
| pytest / pytest-asyncio | 9.0.3 / per `requirements-dev.txt` [VERIFIED: `pytest --version`] | Tests | Existing framework; options tests use plain `asyncio.run(...)` wrappers |
| Python | 3.14.5 runtime [VERIFIED] | — | CLAUDE.md says 3.6+, but `zoneinfo` (3.9+) is already required; match repo style, add nothing newer than the repo already uses |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| yfinance | 1.4.1 [VERIFIED: import] | Research scripts only | `backtester/experimental/ibs_search/*` and (optionally) one probe cross-check. NEVER in the live path (D-19) |
| pandas / numpy | 3.0.3 / per requirements | Research scripts, parity test frames | Parity test builds small OHLC frames for the real research `simulate` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Reusing `bot.options.execution.LegExecutor` | New `bot/ibs/execution.py` copying `fill_leg` | ~120 more lines to write and test; only worth it if the operator wants zero import coupling between the two bots (D4 says self-contained package). Recommendation: reuse; the coupling is one import of a frozen, well-tested class (see Open Question 2) |
| Cancel + replace re-price (via `fill_leg`) | `modify_order(ModifyOrderOp.NORMAL, ...)` | `modify_order` NORMAL exists in the SDK (`open_trade_context.py:636-674`) and avoids the cancel/fill race, but nothing in this repo has ever used it and SIMULATE behaviour is unverified. Cancel + replace is proven live; use it. Add a `modify_order` experiment to `--live-1lot` only if the operator wants it |
| Daily cron arming one-shot DateTriggers | One static `CronTrigger` per job | Static cron cannot follow half-day closes (12:50 vs 15:50) without hard-coding both times |
| New migration 0008 for `ibs_*` tables | `IbsStore.open()` runs its own `CREATE TABLE IF NOT EXISTS` | See Open Question 1; both work. Migration is the established pattern (0006/0007) but edits a shared file and the `CURRENT_VERSION`-sensitive tests |

**Installation:** none. `pip install` is not part of this phase.

**Version verification:** versions above were read from the live interpreter in this worktree on 2026-10-04, not from training data.

## Package Legitimacy Audit

No external packages are installed or added by this phase. `requirements.txt` is unchanged. `slopcheck` / registry checks are therefore not applicable.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| (none) | — | — | — | — | n/a | n/a |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
                         rules_ibs.json
                               |
        python3 -m bot --rules rules_ibs.json
                               |
                      bot/main.py peek (strategy_name)
                               |  == "ibs_etf_mean_reversion"
                               v
                      bot/ibs/service.main()
   load_ibs_config (jsonschema + cross-field checks, ConfigError -> exit 1)
   configure_logging(log_name="ibs.log")   MoomooGateway   IbsStore(data/ibs_state.db)
   TelegramAlerter   KillSwitch(.bot_kill_ibs)   IbsBot   OpenDWatchdog
                               |
                         asyncio.run(IbsBot.run())
                               |
        readiness gate: gateway.connect() [paper guard x3]
              -> reconcile(startup) [own rows vs broker positions]
              -> kill_switch.install() -> entries_enabled = True
                               |
        scheduler.start(); arm_today(); watchdog task; kill-file poll loop
                               |
   daily cron (arm job, ~09:00 ET)  --- is_trading_day? --no--> no-op
                               | yes: close = get_market_close_et(today)
        +----------------------+------------------------+
        v                      v                        v
  DateTrigger              DateTrigger             DateTrigger
  close-10 min             close-1 min             close+5 min
  ibs_decide               ibs_hard_cancel         ibs_eod
        |                      |                        |
        |                      |                        |
        v                      v                        v
  1 guards: trading day, entries_enabled, kill switch,
    not already decided today (meta key), now <= deadline
  2 reconcile (fresh get_positions)  -> NEEDS_ATTENTION codes frozen
  3 ONE get_market_snapshot(17 codes) -> parse_snapshot (fail closed per code)
  4 decide_exits (held rows only) -> place SELL LIMIT sequentially
        LegExecutor.fill_leg(bid=ask=last)  under wait_for(deadline)
        -> fills booked to ibs_trades / ibs_positions; alerts
  5 fresh get_positions -> external-holding + working-order guard
  6 decide_entries (IBS<0.20, not held, free slots, ascending IBS)
        size_position -> row OPENING -> BUY LIMIT sequentially -> OPEN/ABORTED
                               |
                         hard-cancel sweep: cancel every non-terminal ibs_orders row
                               |
                         EOD: reconcile + snapshot mark-to-market -> Telegram + reports/ibs/*.html

   External:  OpenD 127.0.0.1:11111  <->  moomoo paper acc 1727266 (shared with options bot + human)
   Kill: touch .bot_kill_ibs -> loop sees file -> cancel in-flight decision, sweep orders, close gateway
```

### Recommended Project Structure

```
bot/ibs/
├── __init__.py      # docstring only, no re-exports (same reason as bot/options/__init__.py)
├── schema.py        # IBS_SCHEMA (jsonschema)
├── config.py        # IbsConfig (flat dataclass) + load_ibs_config(path) + cross-field checks
├── strategy.py      # PURE: parse_snapshot, compute_ibs, decide_exits, decide_entries, size_position, trading_days_held
├── store.py         # IbsStore(StateStore): ibs_positions / ibs_trades / ibs_orders + get/set_meta
├── execution.py     # IbsExecutor: two LegExecutor instances (entry / exit cfg adapters) + deadline wrapper
└── service.py       # IbsBot + main()
rules_ibs.json                       # repo root, single source of truth
scripts/uat_ibs_probe.py             # read-only probe + --live-1lot --confirm
deploy/com.bot.ibs.plist             # template; + deploy/README.md section / runbook
docs/research/2026-10-04-ibs-etf-strategy-search.md
backtester/experimental/ibs_search/  # the six research scripts (path constants fixed)
tests/ibs/{__init__,conftest,test_schema_config,test_strategy,test_parity,test_store,test_execution,test_service,test_dispatch,test_hygiene}.py
```

Shared files touched (all additive): `bot/main.py` (one `elif`), `bot/__main__.py` (docstring), `bot/safety/logger.py` (kw-only `log_name`, `force`), `bot/scanner/calendar.py` (one helper), `tests/conftest.py` (audit-log isolation), optionally `bot/state/migrations.py` (Open Question 1).

### Pattern 1: Dispatch (IBS-02)

**What:** peek the rules file's top-level `strategy_name` and hand off before any equity component is constructed.
**Where:** `bot/main.py:80` is the existing options branch. Add a sibling branch; unknown names still fall through to the equity loader (`ConfigError` exit 1), unchanged.
```python
# Source: bot/main.py:80 (existing options branch) — add directly below it
if data.get("strategy_name", "") == "ibs_etf_mean_reversion":
    from bot.ibs.service import main as _ibs_main   # deferred: equity path never imports bot.ibs
    _ibs_main(rules_path)
    return
```
Test it exactly like `tests/options/test_dispatch.py:44-56`: write a rules file, `monkeypatch.setattr(bot.ibs.service, "main", ...)`, assert the equity `MoomooGateway` constructor was never called. Keep the existing options/equity dispatch tests untouched as the regression set.

### Pattern 2: D6 naming, one-for-one (open question 3, resolved)

| Item | Options bot (source) | IBS (mirror) |
|------|----------------------|--------------|
| DB path | `service.state_db` = `data/options_state.db` (`rules_options.json`) | `data/ibs_state.db` |
| Kill file | `service.kill_file` = `.bot_kill_options` (cwd-relative) | `.bot_kill_ibs` |
| Report dir | `service.report_dir` = `reports/options`; service does `os.makedirs(report_dir, exist_ok=True)` before `write_reports` because `write_reports` uses a NON-recursive `mkdir` (`bot/service/report.py:321`) | `reports/ibs` (same makedirs) |
| Log file | NONE. Options writes the shared `logs/bot.log` (`configure_logging` hard-codes it, `bot/safety/logger.py:87`) | `logs/ibs.log` per D-13 — requires the logger change in Pitfall 1 |
| Telegram prefix | `<b>Options …</b>` string prefix in each message body (no prefix mechanism in `TelegramAlerter`) | `<b>IBS …</b>`; every interpolated value through `html.escape` (`_esc`) |
| Audit events | `options_*` event names via `append_audit` | `ibs_*` event names |
| Job ids | `options_entry_scan_<name>`, `options_manage`, `options_eod` | `ibs_decide`, `ibs_hard_cancel`, `ibs_eod`, `ibs_arm` |
| Equity-DB guard | `ConfigError` if `service.equity_state_db` resolves to the options DB (`bot/options/config.py` T-11-01) | `ConfigError` if `service.state_db` resolves (abspath) to `data/bot_state.db` or `data/options_state.db` |

### Pattern 3: Arming close-relative jobs (D-07)

**What:** a daily `CronTrigger` job (`ibs_arm`, e.g. 09:00 ET, time from `rules_ibs.json`) computes today's close from `get_market_close_et(today)` and adds three one-shot `DateTrigger` jobs with `replace_existing=True`; `run()` also calls the same `arm_today()` once right after `scheduler.start()` for a restart before the decision time.
**Why not a static cron:** half-days close at 13:00 (verified: `get_market_close_et(date(2026,11,27)) == "13:00"`, `date(2026,12,24) == "13:00"`), so 15:50 would be wrong.
**Lessons already paid for in this repo** (`bot/service/bot.py:630-665`): a `DateTrigger` job is removed after it fires, so the next day's `reschedule_job` raised `JobLookupError` and force_close ran unarmed on 2026-08-26/27. `add_job(..., id=..., replace_existing=True)` after `scheduler.start()` avoids that class of bug entirely.
```python
# Source: pattern from bot/service/bot.py:630-665 (equity) + bot/options/service.py:600-645 (options job kwargs)
def arm_today(self) -> None:
    today = now_et().date()
    if not is_trading_day(today):
        return
    close = _close_dt(today)                       # datetime.combine(today, HH:MM, tzinfo=ET)
    common = dict(coalesce=True, max_instances=1, misfire_grace_time=cfg.misfire_grace_s, replace_existing=True)
    for job_id, fn, offset in (
        ("ibs_decide",      self._job_decide,      -timedelta(minutes=cfg.decision_before_close_min)),
        ("ibs_hard_cancel", self._job_hard_cancel, -timedelta(minutes=cfg.hard_cancel_before_close_min)),
        ("ibs_eod",         self._job_eod,         +timedelta(minutes=cfg.eod_report_after_close_min)),
    ):
        run_at = close + offset
        if run_at <= now_et():                     # mid-day restart: this slot already passed -> no job (D-07)
            continue
        self._scheduler.add_job(fn, DateTrigger(run_date=run_at, timezone=_ET), id=job_id, **common)
```
Defence in depth: `_job_decide` itself refuses to act when `now_et() > hard_cancel_time − margin` (a laptop that slept through 15:50 and wakes at 15:58 must not place orders even if APScheduler fires the misfired job inside its grace window).

### Pattern 4: Order working via `LegExecutor` (IBS-05) — what is reusable in `bot/execution/engine.py`

`ExecutionEngine.consume_intent` / `_manage_entry_order` / `manage_exit` are NOT reusable: they need `OrderIntent`, `StateStore.pending_intents`, `StrategyConfig` field names, and `manage_exit` is an unbounded retry-until-flat loop (`engine.py:701-800`), which D-09 forbids. Pure helpers that ARE reusable (and already imported by `bot/options/execution.py:30`): `_get_trd_side_buy/_get_trd_side_sell`, `_TERMINAL_ORDER_STATUSES`, `_cancel_confirmed`.

`bot.options.execution.LegExecutor.fill_leg(code, side, qty, bid, ask, aggressive=False, on_placed=None)` already implements exactly D-08/D-09: price = `mid ± limit_buffer_usd`, poll `order_list_query` to `ttl_s`, cancel then re-read, escalate by `escalation_step_usd` for `max_retries` attempts, cancel-on-any-exception (shielded) and the CR-04 "cancel unconfirmed" `RuntimeError`. It reads only five attributes from its cfg: `limit_buffer_usd, poll_interval_s, ttl_s, escalation_step_usd, max_retries`. Passing `bid = ask = last` makes `mid == last`, so BUY = `last + buffer`, SELL = `last − buffer`, which is D-08 exactly. Because entry and exit buffers differ, build two executors:
```python
# Source: bot/options/execution.py:60-132 (LegExecutor/fill_leg); adapter is the new part
from types import SimpleNamespace
from bot.options.execution import LegExecutor

def _exec_cfg(cfg, buffer_usd):
    return SimpleNamespace(
        limit_buffer_usd=buffer_usd, poll_interval_s=cfg.poll_interval_s, ttl_s=cfg.order_ttl_s,
        escalation_step_usd=cfg.escalation_step_usd, max_retries=cfg.max_reprices,
    )

class IbsExecutor:
    def __init__(self, gateway, cfg):
        self._entry = LegExecutor(gateway, _exec_cfg(cfg, cfg.entry_limit_buffer_usd))
        self._exit = LegExecutor(gateway, _exec_cfg(cfg, cfg.exit_limit_buffer_usd))

    async def work(self, side, code, qty, last, deadline, on_placed):
        ex = self._entry if side == "BUY" else self._exit
        remaining = (deadline - now_et()).total_seconds()
        if remaining <= 0:
            return None
        # Cancelling fill_leg (timeout) triggers its shielded cancel_order, then re-raises (CR-03).
        return await asyncio.wait_for(ex.fill_leg(code, side, qty, last, last, on_placed=on_placed), remaining)
```
Return contract (inherited): `(order_id, avg_price, filled_qty)` on a full or TTL-partial fill, `None` if nothing filled; it raises on an unconfirmed cancel or when the deadline cancels it. Callers must treat every exception as "exposure unknown" (flag the row `NEEDS_ATTENTION`, alert) — never as "nothing filled".

Work orders SEQUENTIALLY (exits first, then entries by ascending IBS). Reason: shared rate limits (below). A marketable ETF limit normally fills on the first poll (`poll_interval_s` after placement), so ~20 orders take roughly 2 minutes; the deadline caps the pathological case and an unplaced entry is simply skipped (D-09).

### Pattern 5: Reconcile / readiness / shutdown (IBS-07, IBS-08)

Port `OptionsBot._readiness_gate` (`bot/options/service.py:427-445`) and `reconcile` (`451-582`) with one simplification: one row = one code, `expected = db qty`, `actual = broker qty` (floats from `position_list_query`, cast `int`). Statuses: startup flags `OPENING`/`CLOSING` rows `NEEDS_ATTENTION` (process died mid-order; never auto-resume); steady state inspects `OPEN` rows only so the status flip is the alert-once guard. A broker quantity that differs from the row (including `broker > db`, the case where an options assignment or a human adds shares of the same ETF) flips to `NEEDS_ATTENTION`; the bot then neither exits nor sizes against that code, but the row keeps its slot. Broker holdings on universe codes with no active IBS row are `external`: logged (`ibs_reconcile_external_ignored`), never touched, and excluded from entry candidates.

Shutdown ordering (an improvement the options code names as its own "upgrade path", `bot/options/execution.py:197-203`): (1) cancel and await the in-flight decision task so `fill_leg`'s shielded cancel runs while the gateway is still open, (2) sweep remaining non-terminal `ibs_orders`, (3) `gateway.close()`, (4) alert, (5) `scheduler.shutdown(wait=False)`. Options does 3 → 4 → 5 and so its cancel fails at shutdown.

`OpenDWatchdog` (`bot/service/watchdog.py:52-83`) duck-types the bot. `IbsBot` must expose `_entries_enabled`, `_store`, `_position_manager = None`, `_bar_agg = None`, and `IbsConfig` must expose `watchdog_poll_interval_s`, `watchdog_reconnect_initial_s`, `watchdog_reconnect_cap_s`. On reconnect the watchdog calls `gateway.startup_reconcile(bot._store, None)` (`watchdog.py:~178`), which is the EQUITY reconcile run against the IBS store. It is inert only because the IBS DB's equity `positions` table is empty and orphan adoption needs a DB row or pending intent (`gateway.py:1568-1586`). Therefore: `IbsStore` MUST subclass `StateStore` (the watchdog needs `get_open_positions`/`has_pending_intent`), and IBS tables MUST NOT be named `positions`, `pending_intents`, or `trades`.

### Anti-Patterns to Avoid

- **Making `TradingBot`/`PositionManager`/`BarAggregator` a base class** (D4): TradingBot is a template only.
- **Any `force_close`/`manage_exit`/`MARKET`/`unlock_trade` reference in `bot/ibs/`**: add a static hygiene test that greps the package source for these strings (D-06, EXEC-02, D-10).
- **Concurrent per-order polling**: ten workers each polling `order_list_query` every 4 s blows the 10 requests / 30 s budget that the options bot also draws on.
- **Re-using `OptionsStore` or the `option_*` tables**: separate store, separate table names.
- **Computing the decision from `ask_price`/`bid_price`**: outside RTH those are stale/wide (live probe: EFA ask 105.50 / bid 102.01, XLB 49.81 / 48.82). D-02/D-08 specify `last_price`; keep it.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Working a LIMIT order with TTL, escalation, cancel-on-error, CR-04 unconfirmed-cancel handling | A new fill loop | `bot.options.execution.LegExecutor.fill_leg` | Months of fixes (CR-03, CR-04, 260925-inw) already encoded and tested |
| Paper-only enforcement | A new guard | `MoomooGateway.connect()` → `assert_paper_account` | Triple guard, audited refusals |
| Audit trail | A new log | `bot.safety.audit_log.append_audit` (+ gateway `place_order` already audits every order) | Append-only, shared format |
| Kill switch | Signal/file handling | `KillSwitch(sentinel_path=cfg.kill_file)` + the options run loop | Idempotent, tested |
| OpenD health + reconnect | A reconnect loop | `OpenDWatchdog` (duck-typed bot) | Exists; just satisfy its attribute contract |
| Order-status rate-limit retry | Sleep loops | `gateway.get_order_status` (retries the documented "high frequency" error, `gateway.py:1269-1334`) | Already handled |
| NYSE holidays / early closes | A date table | `is_trading_day`, `get_market_close_et`, `pandas_market_calendars.valid_days` | Verified for 2026 early closes |
| HTML report writer | Own file writer | `bot.service.report.write_reports` (+ `os.makedirs`) | Writes dated + `latest.html` |
| Config validation | Hand checks | `jsonschema` + `ConfigError` (re-used from `bot.config.loader`) | One exception type for the whole bot |
| Telegram | `requests` | `TelegramAlerter.send` (stdlib urllib, never raises) | No new dependency |
| Trading-day counting | `timedelta` / weekday math | A calendar-backed count (one additive helper, below) | Holidays break weekday math |

**Key insight:** the only genuinely new logic is ~100 lines of pure strategy functions and the scheduling/arming; everything that can go wrong at 15:50 with real orders has already been debugged in the options executor and equity engine. Resist re-implementing any of it.

## Runtime State Inventory

Not a rename/refactor/migration phase (greenfield bot). One cutover-related inventory is still useful for the runbook:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Equity bot DB `data/bot_state.db`; options DB `data/options_state.db`. IBS adds `data/ibs_state.db` (new, empty). Broker account holds NO shares of the 17 universe ETFs today (verified live 2026-10-04: share holdings are DIVO, SCHF, IAU, CLOV, IBIT, MARA(short), O) but holds many option legs on SPY/QQQ/TLT/GLD/XLE/SCHF | None for data; probe prints holdings so the operator confirms before cutover |
| Live service config | Equity bot and options bot are running as plain terminal processes (`pgrep` shows `-m bot` and `-m bot --rules rules_options.json`); NO `com.bot.*` LaunchAgent is installed (`~/Library/LaunchAgents` listing) | Runbook must stop Trend Join Long by `touch .bot_kill` in the repo working directory it was launched from (default sentinel `./.bot_kill`, `bot/safety/kill_switch.py:~38`), or SIGINT; CONTEXT's "stop the launchd job" does not match reality |
| OS-registered state | None for IBS yet. New LaunchAgent only if the operator installs `deploy/com.bot.ibs.plist` | Operator step in runbook |
| Secrets/env vars | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (read by `main()`), `PAPER_TRADING`, `FUTU_TRD_ENV`, `FUTU_ACC_ID` | None new; plist template must add `FUTU_ACC_ID` (missing today) |
| Build artifacts | None | None |

## Common Pitfalls

### Pitfall 1: `logs/ibs.log` has no precedent and `configure_logging` is called too early
**What goes wrong:** D-13 says "follow the exact D6 pattern", but the options bot has no own log file. `configure_logging(log_dir, level)` hard-codes `bot.log` (`bot/safety/logger.py:87`) and `bot.main.main` calls it at line 67 BEFORE the dispatch peek, setting `_configured = True` (`logger.py:61,80`); a second call returns immediately.
**How to avoid:** add keyword-only `log_name: str = "bot.log"` and `force: bool = False` to `configure_logging`; `bot.ibs.service.main` calls `configure_logging(log_name="ibs.log", force=True)` (the function already clears root handlers on each call). Keyword-only parameters live in `__kwdefaults__`, so the existing session fixture in `tests/conftest.py:44-62`, which overwrites the positional `__defaults__` tuple `(log_dir, level)`, keeps working (verified in a REPL: `f.__defaults__ = ("A","B")` leaves a kw-only default intact). Fallback with zero shared-code change: send launchd stderr to `logs/ibs.stderr.log` (the stderr handler emits every event as ConsoleRenderer text) and accept the shared `bot.log`.
**Warning signs:** `logs/ibs.log` empty while `logs/bot.log` shows `ibs_*` events.

### Pitfall 2: Tests write to the REAL audit log today
**What goes wrong:** there is no global audit isolation in `tests/conftest.py`. `~/.futu_trade_audit.jsonl` is 26 MB and contains 3,245 `kill_switch` entries whose `sentinel_path` is a pytest tmp dir and 2,087 `opend_reconnect` entries [VERIFIED: grep]. Options tests avoid it only by per-module `monkeypatch.setattr(service, "append_audit", ...)`, which does nothing for `KillSwitch._trigger`, `paper_guard._fail`, `OpenDWatchdog`, or the gateway's lazily-imported `append_audit` (`gateway.py` `place_order`). D-15's "existing conftest isolation" for the audit log does not exist.
**How to avoid:** add a session-scoped autouse fixture in `tests/conftest.py` next to `_isolate_bot_log` that `monkeypatch`es `bot.safety.audit_log.AUDIT_LOG_PATH` to a tmp file (`append_audit` reads the module global at call time, `audit_log.py:47`). The existing safety tests that set the same attribute themselves and restore it keep working.
**Warning signs:** new `ibs_*` events with tmp paths in the real audit file after a test run.

### Pitfall 3: Snapshot `update_time` and `high/low` semantics differ from the docs
**What goes wrong (live-verified 2026-10-04 ~21:19 ET, OpenD 10.07.6708):**
- Docstring says `update_time` is `yyyy-MM-dd HH:mm:ss`; the live value is `"2026-10-04 21:19:04.863"` (fractional seconds, string). A strict `strptime("%Y-%m-%d %H:%M:%S")` fails closed for every code.
- It is the last quote-update time in ET, and for illiquid names it lags: XLB/XLY/EFA showed `19:54:59` while SPY showed `21:19:04`. On a Sunday evening it is the OVERNIGHT (24h) session clock, so `date == today` does not prove the numbers are from today's regular session.
- `high_price`/`low_price` are regular-session only: SPY `pre_low_price 766.64 < low_price 767.145`, DIA `pre_high_price 514.2015 > high_price 513.0601`, QQQ/IWM/XLK also show `pre_low < low_price`. (If premarket were included, `low_price` would be ≤ `pre_low_price`.) So the research definition (daily OHLC) matches the snapshot fields.
- `last_price` outside RTH can be an overnight/after-hours print while high/low stay regular-session; it can sit outside `[low, high]`.
**How to avoid:** parse with `update_time[:19]` (or `datetime.fromisoformat`); fail closed per code unless ALL hold: all of `last_price/high_price/low_price` finite and `> 0`; `high > low`; `low <= last <= high`; `suspension is False`; parsed `update_time` date (ET) `== today` and age `<= signal.max_snapshot_age_s`; `volume > 0`. Make `max_snapshot_age_s` a config key and calibrate it from the RTH probe (see Open Question 3); log (do not trade) every skipped code with its reason.
**Warning signs:** probe shows IBS outside `[0, 1]` or ages of hours during RTH.

### Pitfall 4: Same-day re-entry after a time exit (research behaviour the CONTEXT text does not state)
**What goes wrong:** in the research close-fill branch, exits are popped from `pos` BEFORE candidates are built (`strategy_search.py:73-81`), so a position that time-exits (held ≥ 10 days) while today's IBS is still < 0.20 is sold at the close and immediately re-bought in the same session (two crossings). A production implementation that "never re-enters a code sold today" would silently diverge from the research.
**How to avoid:** replicate the research (candidates computed after exits), sequence BUYs strictly after the exit batch completes, and re-read broker positions between the two batches so the entry guard sees the post-exit state. The parity scenario must include this case. If the operator would rather avoid the wash round trip, that is a rule change and needs an explicit decision (Open Question 5), not a silent difference.

### Pitfall 5: `free` slots under partial/failed exits
**What goes wrong:** research assumes every decided exit fills. Production exits can partially fill or fail (D-09), and a failed exit still occupies its slot.
**How to avoid:** `decide_exits` returns the decision list (parity with research); `decide_entries(candidates, held_codes_after_exits, slots)` is called by the service with the POST-exit held set, so `free = slots − len(held_after_exits)`. When all exits fill this equals the research's `slots − (len(pos) − len(exits))`. `NEEDS_ATTENTION` rows count as held.

### Pitfall 6: Research sizing compounds, production does not
**What goes wrong:** the research allocates `min(mtm/slots, cash)` (10% of current marked equity, fractional shares). D-05 uses a fixed `sizing_equity_usd × position_pct` and whole shares. The paper account actually holds ~$1.00M (`total_assets 1,004,989.96`, `cash 872,898`, `power 1,602,449` — verified live), so buying power is irrelevant and `risk.sizing_equity_usd = 100000` is the deliberate notional anchor (same device as the shared-account fix recorded in project memory).
**How to avoid:** the parity test compares DECISIONS (which codes, which days), not quantities. Whole-share flooring leaves slack (SPY ~$770 → 12 shares = $9,240; QQQ ~$750 → 13 = $9,750): accept and log the realized notional.

### Pitfall 7: Shared per-account rate limits
**What goes wrong:** moomoo caps (per single `acc_id`): `place_order` 15 requests / 30 s with ≥ 0.02 s spacing [CITED: openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html]; `order_list_query` and `position_list_query` 10 requests / 30 s when `refresh_cache=True` [CITED: …/trade/get-order-list.html, …/trade/get-position-list.html]; `get_market_snapshot` 60 / 30 s, 400 codes per call [CITED: …/quote/get-market-snapshot.html, search result for the 60/30s figure]. The options bot's manage job (every 5 min until 15:55) and `fill_leg` polling draw on the same budgets at 15:50.
**How to avoid:** one snapshot, two position reads (start, after exits), sequential orders, `poll_interval_s >= 4` (≤ 7.5 status reads / 30 s alone), rely on the gateway's built-in rate-limit retry, and never poll in parallel. Cancel + replace costs 2 place/modify calls per re-price: 10 orders × up to 3 re-prices stays under 15 / 30 s only because sequencing spreads them; keep `max_reprices` small.

### Pitfall 8: Kill-file semantics under launchd
**What goes wrong:** `deploy/com.bot.trading.plist` uses `KeepAlive = true`. A kill-file shutdown exits with status 0 and launchd restarts the process immediately; the sentinel file still exists, so it re-triggers every `ThrottleInterval`. The template also omits `FUTU_ACC_ID`, so `assert_paper_account` Guard 3 fails (`acc_id` defaults to 0). It also hard-codes the equity `rules.json` command.
**How to avoid:** the IBS plist uses `KeepAlive` as a dict `{SuccessfulExit: false}` (restart only after a crash), sets `FUTU_ACC_ID`, `PYTHONUNBUFFERED=1`, `WorkingDirectory` = the main repo checkout on `develop` (project memory: worktree fixes are inert until merged), and `ProgramArguments` `-m bot --rules rules_ibs.json`. Runbook: stop = `touch .bot_kill_ibs`, start = remove the file then `launchctl load`.

### Pitfall 9: A SIMULATE rejection looks like "no fill"
**What goes wrong:** if `place_order` succeeds but the broker later marks the order `FAILED`/`SUBMIT_FAILED`, `fill_leg` polls to the TTL with `dealt_qty = 0`, then its cancel raises (order not cancellable) → `RuntimeError("cancel … unconfirmed")`. Safe, but noisy and slow.
**How to avoid:** catch the exception per code, mark the row `NEEDS_ATTENTION`/`ABORTED` appropriately, alert once, continue to the next code. Verify the happy path and one rejection path in `--live-1lot`. Whole-share qty, `fill_outside_rth=False`, default `TimeInForce.DAY` (`open_trade_context.py:544-548`) means anything left working at the close expires broker-side too, a backstop for D-09 even on a hard kill (launchd stops with SIGTERM; only SIGINT is handled by `KillSwitch`).

### Pitfall 10: Marketable-limit buffer scale
**What goes wrong:** a flat `$0.05` buffer is 0.6 bp on SPY but ~12 bp on XLU (~$40), versus the research's 2 bp/side cost assumption.
**How to avoid:** default `entry_limit_buffer_usd` and `exit_limit_buffer_usd` to `0.02`, `escalation_step_usd` `0.02`, `max_reprices` `2` [ASSUMED — liquid-ETF spreads are ~$0.01; the RTH probe should print `(ask − bid)` per code so the operator confirms]. All are rules-file values, not code.

### Pitfall 11: Research scripts cannot be imported
**What goes wrong:** `strategy_search.py` runs `yf.download` and a full simulation at module level below the `# ---… data` marker; `ibs_robust.py`, `ibs_sizing.py`, `r5_robust.py`, `strategy_search_r2.py` `exec` its prelude via hard-coded absolute `ROOT` and `.scratch/…` paths (`ROOT = "/Users/acdc/…"`; `strategy_search.py:28,191,200`; `ibs_sizing.py:2-3`; `r5_robust.py:2-3`; `strategy_search_r2.py:32-33,147`; `screen_study.py:14,46,123`).
**How to avoid:** Plan 1 changes only the path constants (resolve `ROOT` from `__file__`, point the prelude reads at the same directory, write CSV outputs under `backtester/results/`, which is already gitignored). The parity test must NOT import the module; it extracts `def simulate` with `ast` (see Code Examples).

## Code Examples

### Pure strategy core (IBS-03) — shape only

```python
# Source: decision logic from .scratch/strategy_search.py:51-94 (research simulate, close-fill branch)
import math

def compute_ibs(last, high, low):
    """IBS or None (fail closed). Mirrors (C-L)/(H-L) with NaN -> no signal."""
    vals = (last, high, low)
    if any(v is None or not math.isfinite(v) or v <= 0 for v in vals) or high <= low or not (low <= last <= high):
        return None
    return (last - low) / (high - low)

def decide_exits(held, ibs_by_code, held_days, cfg, pending=frozenset()):
    """held: iterable of codes this bot owns. Time exit applies even when IBS is unknown (research: X NaN -> False,
    but `t - d0 >= max_hold` still fires). pending = codes whose earlier exit did not complete (retry unconditionally, D-09)."""
    out = []
    for code in held:
        ibs = ibs_by_code.get(code)
        if (code in pending
                or held_days[code] >= cfg.max_hold_trading_days
                or (ibs is not None and ibs > cfg.ibs_exit_min)):
            out.append(code)
    return out

def decide_entries(ibs_by_code, held_after_exits, free, cfg, order):
    """order: universe list -> stable tie-break identical to the research (column order)."""
    cand = [c for c, v in ibs_by_code.items()
            if v is not None and v < cfg.ibs_entry_max and c not in held_after_exits]
    cand.sort(key=lambda c: (ibs_by_code[c], order.index(c)))
    return cand[:max(free, 0)]

def size_position(limit_price, cfg):
    return int((cfg.sizing_equity_usd * cfg.position_pct_of_equity / 100.0) // limit_price) if limit_price > 0 else 0

def trading_days_held(entry_date, today, session_dates):
    """Count of sessions strictly after entry_date up to and including today. session_dates: sorted iterable of dates."""
    return sum(1 for d in session_dates if entry_date < d <= today)
```
Pure means no calendar import inside `strategy.py`; the service injects `session_dates` from the calendar helper. Note the research sort is by IBS then column order (stable `list.sort` over `np.where` order), hence the `(ibs, universe index)` key.

### Additive calendar helper (one function; no helper exists today)

`get_prior_n_trading_days` returns days BEFORE a reference date and `_nyse` is private, so add one public function to `bot/scanner/calendar.py`:
```python
# Source: bot/scanner/calendar.py:32-43 (is_trading_day uses the same valid_days call)
def trading_days_between(start_exclusive: date, end_inclusive: date) -> list:
    """NYSE sessions d with start_exclusive < d <= end_inclusive, ascending, as date objects."""
    valid = _nyse.valid_days(start_date=start_exclusive + timedelta(days=1), end_date=end_inclusive)
    return [d.date() for d in valid]
```
(`valid_days` over a 16-day window returned 11 sessions in ~0.1 ms, verified.) The service calls it once per decision with the oldest open entry date.

### Parity test that runs the REAL research function

```python
# Source: ast-extraction avoids importing strategy_search.py (which downloads data at import time)
import ast, pathlib, numpy as np, pandas as pd

def _load_research_simulate():
    path = pathlib.Path("backtester/experimental/ibs_search/strategy_search.py")
    tree = ast.parse(path.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "simulate")
    ns = {"np": np, "pd": pd}
    exec(compile(ast.Module([fn], []), str(path), "exec"), ns)
    return ns["simulate"]
```
Scenario design: build ~25 consecutive NYSE sessions (`trading_days_between`) × ~5 synthetic symbols with hand-chosen Close/High/Low so that, in order: (a) several simultaneous IBS < 0.20 candidates exceed free slots (rank by ascending IBS), (b) an exit by IBS > 0.80 frees a slot used the same day, (c) a time exit with IBS still < 0.20 triggers same-day re-entry (Pitfall 4), (d) two candidates tie on IBS (column-order tie break), (e) a NaN / `high == low` bar. Run `simulate(px, ent, ex, rank, cost=0.0, slots=3, max_hold=3)` (it reads only `px["Close"]` and `px["Open"]`) and a production replay loop (`compute_ibs` → `decide_exits` → `decide_entries`, fills at close, `trading_days_held` over the same sessions). Compare the multiset of `(entry_date, exit_date)` and the `ret` column (make every trade's return unique by construction so the symbol is identifiable): the research returns `trades` with `entry, exit, ret` only. Keep the scenario free of cash exhaustion (research `alloc = min(mtm/slots, cash)`; production never skips for cash).

### Config shape (names fixed by CONTEXT; the rest are recommendations)

```json
{
  "strategy_name": "ibs_etf_mean_reversion",
  "universe": ["US.SPY","US.QQQ","US.IWM","US.DIA","US.XLK","US.XLF","US.XLE","US.XLV","US.XLI","US.XLY","US.XLP","US.XLU","US.XLB","US.TLT","US.GLD","US.EFA","US.EEM"],
  "signal": {"ibs_entry_max": 0.20, "ibs_exit_min": 0.80, "max_hold_trading_days": 10, "max_snapshot_age_s": 900},
  "risk": {"sizing_equity_usd": 100000, "position_pct_of_equity": 10, "max_concurrent_positions": 10},
  "execution": {"entry_limit_buffer_usd": 0.02, "exit_limit_buffer_usd": 0.02, "order_ttl_seconds": 30,
                "poll_interval_seconds": 5, "escalation_step_usd": 0.02, "max_reprices": 2},
  "service": {"arm_time_et": "09:00", "decision_before_close_min": 10, "hard_cancel_before_close_min": 1,
              "eod_report_after_close_min": 5, "misfire_grace_s": 120,
              "watchdog_poll_interval_s": 60, "watchdog_reconnect_initial_s": 5, "watchdog_reconnect_cap_s": 300,
              "state_db": "data/ibs_state.db", "kill_file": ".bot_kill_ibs", "report_dir": "reports/ibs", "log_file": "ibs.log"}
}
```
Cross-field `ConfigError` checks (plain Python, like options' `_check_strategy`): `0 < ibs_entry_max < ibs_exit_min < 1`; universe non-empty, unique, each `^US\.[A-Z]+$`; `max_concurrent_positions >= 1`; `decision_before_close_min > hard_cancel_before_close_min >= 1`; `poll_interval_seconds >= 3.1` (keeps one status read under the 10/30 s cap); worst-case single-order time `order_ttl_seconds * (max_reprices + 1)` must be strictly less than `(decision_before_close_min − hard_cancel_before_close_min) * 60`; `state_db` must not resolve (abspath) to `data/bot_state.db` or `data/options_state.db`. All JSON numbers are required keys (no Python defaults), so `rules_ibs.json` stays the single source of truth (CFG-01).

### Store design

```sql
CREATE TABLE IF NOT EXISTS ibs_positions (
  position_id TEXT PRIMARY KEY, code TEXT NOT NULL, qty INTEGER NOT NULL,
  entry_date TEXT NOT NULL, entry_price REAL, entry_order_id TEXT,
  status TEXT NOT NULL,            -- OPENING | OPEN | CLOSING | CLOSED | NEEDS_ATTENTION | ABORTED
  exit_pending_reason TEXT,        -- set when a decided exit did not complete; retried unconditionally next session (D-09)
  opened_at TEXT, closed_at TEXT, close_reason TEXT, realized_pnl_usd REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ibs_positions_active_code
  ON ibs_positions(code) WHERE status IN ('OPENING','OPEN','CLOSING','NEEDS_ATTENTION');
CREATE TABLE IF NOT EXISTS ibs_trades (        -- one row per EXIT FILL (handles partial exits)
  trade_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, code TEXT NOT NULL, qty INTEGER NOT NULL,
  entry_price REAL, exit_price REAL, exit_date TEXT NOT NULL, reason TEXT, pnl_usd REAL
);
CREATE TABLE IF NOT EXISTS ibs_orders (        -- every order id, for the hard-cancel sweep and the audit trail
  order_id TEXT PRIMARY KEY, position_id TEXT, code TEXT NOT NULL, side TEXT NOT NULL,
  qty INTEGER, price REAL, status TEXT NOT NULL, session_date TEXT NOT NULL, created_at TEXT
);
```
The partial unique index makes "one position per code" (D-05) a database invariant. `meta` (already inherited) holds `ibs_decision_date` for per-session idempotency: the decision job writes it when its guards pass, so a duplicate fire or a mid-decision restart cannot decide twice (a restart before the deadline therefore means "no decision today", which is the D-07 policy). Whether the tables come from migration 0008 or from an `IbsStore.open()` override: Open Question 1. Reuse `OptionsStore`'s `get_meta`/`set_meta` pattern; either copy the 12 lines or hoist nothing (do not edit `OptionsStore`).

### Live facts captured for the tests (use as fixture shapes)

Snapshot row (live, 2026-10-04 21:19 ET): `{"code": "US.SPY", "update_time": "2026-10-04 21:19:04.863", "last_price": 769.64, "open_price": 770.58, "high_price": 772.65, "low_price": 767.145, "prev_close_price": 763.99, "volume": 46335295.0, "suspension": False, "sec_status": "NORMAL", "pre_high_price": 772.0, "pre_low_price": 766.64, …}`. Position row: `qty` float (e.g. `630.0`), `cost_price == average_cost == diluted_cost` for shares, `position_side` `"LONG"`/`"SHORT"`, shorts carry NEGATIVE `qty`. `get_order_status("")` returns `[]` when no orders exist (and an empty DataFrame has no columns: guard `data is None or len(data) == 0`, as the gateway already does).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Equity bot's 15:51 force-close, intraday-only | Overnight holds up to 10 trading days, no force-close job | This phase (D-06) | Registered-jobs test must assert no force-close; shutdown must NOT flatten positions |
| Static `CronTrigger` per timed job (options) | Daily cron arms calendar-aware one-shot `DateTrigger`s (equity force-close lesson) | Equity fix 2026-08 | Half-day correctness |
| Options executor sequences multi-leg spreads | Same class used for single-leg ETF orders | This phase | Config adapter, not a rewrite |

**Deprecated/outdated:**
- `deal_list_query` / `gateway.get_order_fills`: "Paper trading does not support deal data" on SIMULATE (`engine.py:471-472`); never use it. Fills come from `order_list_query` `dealt_qty` / `dealt_avg_price` only.
- `place_order(fill_outside_rth=...)` is deprecated in favour of `session` [CITED: place-order doc]; IBS needs neither (RTH only).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Default buffers `0.02 / 0.02`, escalation step `0.02`, `max_reprices 2`, `order_ttl_seconds 30`, `poll_interval_seconds 5` fill liquid ETFs at 15:50 | Pitfall 10, Config shape | Orders cancelled unfilled → skipped entries / retried exits. Mitigation: RTH probe prints spreads; `--live-1lot` exercises the path; all values are JSON-tunable |
| A2 | `max_snapshot_age_s = 900` is a sensible freshness bound at 15:50 RTH (quiet ETFs such as XLB/XLY/EFA showed `update_time` lagging by ~1.4 h overnight) | Pitfall 3, Config shape | Too tight → healthy codes skipped (fail-safe); too loose → stale data traded. Calibrate in the first RTH probe |
| A3 | `modify_order(ModifyOrderOp.NORMAL)` price changes work on SIMULATE | Alternatives | Only matters if someone chooses it over cancel + replace; the recommended path does not depend on it |
| A4 | A rejected order (`FAILED`/`SUBMIT_FAILED`) surfaces through `fill_leg` as a `RuntimeError` on the cancel step | Pitfall 9 | Handled by the generic per-code `except`; confirm in `--live-1lot` |
| A5 | `DAY` time-in-force orders left working at the close are expired by SIMULATE | Pitfall 9 | Only a backstop; the hard-cancel sweep is the primary mechanism |
| A6 | Importing `bot.options.execution.LegExecutor` from `bot/ibs/` is acceptable under "self-contained package" (D4) | Pattern 4 | If the operator wants zero coupling, add ~120 lines (Open Question 2) |
| A7 | Same-day sell-then-rebuy of the same ETF after a time exit is acceptable on the paper account (the research does it) | Pitfall 4 | Extra round-trip cost; operator may prefer a rule change |
| A8 | `trading_days_held` counted from the NYSE calendar equals the research's row-index difference (`t − d0`), i.e. the yfinance index has no missing sessions | Pattern/Code | A missing yfinance row would shift research exit dates by a day; production follows the real calendar |

## Open Questions

1. **Where do the `ibs_*` tables live?**
   - What we know: options added them in shared migration 0006/0007 (`bot/state/migrations.py:308-401`), which also creates them in the equity DB and requires editing `tests/state/test_migrations.py` (it imports `CURRENT_VERSION`, `MIGRATIONS`, `_migration_0007`). `IbsStore.open()` could instead run `CREATE TABLE IF NOT EXISTS` after `super().open()`.
   - What's unclear: operator preference between "established pattern" and "touch no shared file".
   - Recommendation: migration `_migration_0008` (callable, idempotent, `CURRENT_VERSION = 8`), matching the project's D-08 rule "new objects always in a new migration", and update the migration tests. Pick the `open()` override only if the planner wants zero shared-file edits.
2. **Reuse `LegExecutor` across bots or copy it?**
   - Recommendation: reuse via the adapter (Pattern 4). Add one test that fails if `LegExecutor`'s cfg attribute names change.
3. **What `max_snapshot_age_s` is right at 15:50?**
   - What we know: overnight, liquid ETFs update within seconds and quiet ones lag by hours.
   - What's unclear: RTH behaviour per ETF (cannot be probed on a Sunday).
   - Recommendation: the first RTH probe prints age per code; set the value from that, keep the fail-closed default.
4. **Does the SIMULATE paper engine fill marketable ETF LIMITs at all times and honour partial fills/cancels as expected?**
   - Recommendation: gate cutover on a green `--live-1lot --confirm` run in RTH (BUY 1 share of a cheap liquid ETF such as US.XLU, observe fill, SELL it).
5. **Same-day re-entry after a time exit** (Pitfall 4): keep research parity (recommended) or forbid it?
6. **`exit_pending_reason` persistence:** D-09 says an exit is "retried every session until flat". A pure rule re-evaluation would not re-exit a position whose IBS fell back below 0.80 before day 10. Recommendation: persist the flag (column above) so an incomplete exit is retried unconditionally; if the operator wants strict rule re-evaluation, drop the column and `pending` argument. Parity is unaffected when exits fill.
7. **Stopping Trend Join Long:** CONTEXT assumes a launchd job; none is installed. The runbook should give both paths (kill file / SIGINT in the terminal it runs in; `launchctl unload` only if the operator has since installed one).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| OpenD (127.0.0.1:11111) | Every broker call, probe | ✓ (port open; live snapshot/positions/accinfo succeeded) | server accepted SDK 10.07.6708 | none (bot is non-functional without it, per CLAUDE.md) |
| moomoo-api SDK | Gateway | ✓ | 10.07.6708 | — |
| Python | Runtime | ✓ | 3.14.5 | — |
| pytest | Tests | ✓ | 9.0.3 | — |
| pandas / pandas-market-calendars / apscheduler / structlog / jsonschema | Service, tests | ✓ | 3.0.3 / 5.4.0 / 3.11.2 / 26.1.0 / 4.26.0 | — |
| yfinance | Research scripts only | ✓ | 1.4.1 | n/a for the live path |
| Telegram credentials | Alerts | unknown (`.env` exists, deliberately not read) | — | `TelegramAlerter` no-ops when unset; `run()` logs `alerter_disabled_at_startup` |
| Paper account | Orders | ✓ acc 1727266, `total_assets` 1,004,989.96, `cash` 872,898.03, `power` 1,602,449.09 | — | — |
| RTH (09:30–16:00 ET) | Probe calibration, `--live-1lot` | ✗ now (Sunday evening) | — | Operator runs both during RTH on a trading day; the agent never places orders |

**Missing dependencies with no fallback:** none for building and unit-testing. Live UAT (age calibration, fills) requires an RTH session.
**Missing dependencies with fallback:** Telegram secrets (alerts degrade to log only).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 (+ pytest-asyncio available; options tests drive coroutines with a local `_run = asyncio.run` helper — follow that) |
| Config file | none (no `pytest.ini`/`pyproject.toml`); shared fixtures in `tests/conftest.py` |
| Quick run command | `python3 -m pytest tests/ibs -x -q` |
| Full suite command | `python3 -m pytest -q` (baseline per CONTEXT: 1401 passed / 1 skipped) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| IBS-01 | Valid file loads to `IbsConfig`; every missing/invalid key, bad HH:MM, inverted thresholds, duplicate/ill-formed codes, window-too-short, state_db == equity/options DB → `ConfigError`; shipped `rules_ibs.json` equals the fixture literal | unit | `python3 -m pytest tests/ibs/test_schema_config.py -x` | ❌ Wave 0 |
| IBS-02 | `bot.main.main(rules_path=…)` with `strategy_name == "ibs_etf_mean_reversion"` calls `bot.ibs.service.main(path)` and never builds the equity gateway; options (`tasty_credit_spreads`, `strategies`) and equity routes unchanged | unit | `python3 -m pytest tests/ibs/test_dispatch.py tests/options/test_dispatch.py -x` | ❌ Wave 0 (options file ✅) |
| IBS-03 | `compute_ibs` fail-closed cases; `decide_exits`/`decide_entries`/`size_position`/`trading_days_held` incl. ties, half-day-free calendar counts, holiday spans; PARITY: real research `simulate` (AST-extracted) vs production on the synthetic scenario (entry/exit date multiset + per-trade ret) | unit + parity | `python3 -m pytest tests/ibs/test_strategy.py tests/ibs/test_parity.py -x` | ❌ Wave 0 (parity needs Plan 1's research files committed first) |
| IBS-04 | `arm_today`: normal day → 3 jobs at 15:50 / 15:59 / 16:05 ET; half-day (patch/use 2026-11-27) → 12:50 / 12:59 / 13:05; non-trading day → none; restart after decision time → only still-future jobs; `_job_decide` refuses after the deadline and on a second call same date; exits placed before entries (call-order assertion); gateway `get_market_snapshot` awaited exactly once | unit (mock gateway) | `python3 -m pytest tests/ibs/test_service.py -k "arm or decide" -x` | ❌ Wave 0 |
| IBS-05 | Executor adapter: BUY price `last + entry_buf`, SELL `last − exit_buf`; escalation direction; deadline cancel (fake gateway never fills → `cancel_order` awaited, `TimeoutError`); partial fill; unconfirmed cancel → row `NEEDS_ATTENTION`; hard-cancel sweep cancels every non-terminal `ibs_orders`; static hygiene test: no `OrderType.MARKET`, `force_close`, `unlock_trade`, `manage_exit` in `bot/ibs/*.py`; registered jobs contain no force-close | unit + static | `python3 -m pytest tests/ibs/test_execution.py tests/ibs/test_hygiene.py -x` | ❌ Wave 0 |
| IBS-06 | Store CRUD on a tmp-path DB; partial unique index rejects a 2nd active row for a code but allows a new row after `CLOSED`/`ABORTED`; partial exit keeps remaining qty; `ibs_trades` realized P&L; meta idempotency key; migration (if chosen) is idempotent | unit | `python3 -m pytest tests/ibs/test_store.py -x` | ❌ Wave 0 |
| IBS-07 | Reconcile: matching qty OK; qty mismatch / broker-higher / missing → `NEEDS_ATTENTION` + one alert; startup `OPENING`/`CLOSING` → `NEEDS_ATTENTION`; broker holdings with no IBS row are never traded and block entry; entry guard re-reads positions; `NEEDS_ATTENTION` rows hold a slot but are never exited | unit | `python3 -m pytest tests/ibs/test_service.py -k "reconcile or external or guard" -x` | ❌ Wave 0 |
| IBS-08 | `run()` lifecycle (readiness gate order, kill-file → shutdown order: cancel decision task → sweep → gateway.close → alert), watchdog attributes present (`_entries_enabled`, `_store`, `_position_manager is None`, `_bar_agg is None`, cfg `watchdog_*`), alerts HTML-escaped, EOD HTML has no `<script>` and escapes cells, report dir created, `configure_logging(log_name="ibs.log", force=True)` writes under the (patched) log dir only, `AUDIT_LOG_PATH` isolated | unit | `python3 -m pytest tests/ibs/test_service.py tests/safety/test_logger.py -x` | ❌ Wave 0 (logger test file ✅) |
| IBS-09 | Probe: read-only mode never calls `place_order` (mock gateway), prints IBS table via the SAME `parse_snapshot`/`decide_*`/`size_position` functions; argparse refuses `--live-1lot` without `--confirm` (exit 2); plist template parses with `plistlib` and contains `PAPER_TRADING`, `FUTU_TRD_ENV=SIMULATE`, `FUTU_ACC_ID`, `rules_ibs.json`, `KeepAlive` dict | unit + static; live steps are manual | `python3 -m pytest tests/ibs/test_hygiene.py -x`; manual: `python3 scripts/uat_ibs_probe.py` in RTH, then `--live-1lot --confirm` | ❌ Wave 0; live items manual-only (need RTH + OpenD + operator) |
| IBS-10 | Six scripts exist under `backtester/experimental/ibs_search/` and compile (`py_compile`), no absolute `/Users/acdc` path remains; results doc exists and cites the reference numbers | static | `python3 -m pytest tests/ibs/test_hygiene.py -k research -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/ibs -x -q`
- **Per wave merge:** `python3 -m pytest -q` (full suite, must stay at baseline + new tests)
- **Phase gate:** full suite green; operator RTH probe + `--live-1lot` recorded in a UAT note before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/ibs/__init__.py`, `tests/ibs/conftest.py` — `ibs_rules` LITERAL dict (not derived from loader code, same drift-guard reasoning as `tests/options/conftest.py`), `ibs_cfg`, tmp `IbsStore`, mock gateway/alerter/kill-switch fixtures
- [ ] All `tests/ibs/test_*.py` files in the map above
- [ ] `tests/conftest.py` — session-autouse audit-log isolation fixture (Pitfall 2); extend `_isolate_bot_log` only if the logger signature change needs it (it should not)
- [ ] Plan 1 must land `backtester/experimental/ibs_search/strategy_search.py` BEFORE the parity test can run
- [ ] Framework install: none

## Security Domain

`security_enforcement` is enabled in `.planning/config.json` (ASVS level 1, block on high).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Broker auth is OpenD login; paper account needs no unlock; `unlock_trade` never called (static test) |
| V3 Session Management | no | — |
| V4 Access Control | yes (paper-only boundary, own-rows-only) | Triple paper guard in `gateway.connect()`; SAFE-OG-01 analog: act only on codes with an IBS row; `FUTU_ACC_ID` explicit |
| V5 Input Validation | yes | `jsonschema` + cross-field checks for `rules_ibs.json`; per-code fail-closed validation of every snapshot field (finite, > 0, `low <= last <= high`, today, fresh, not suspended); parameterised SQL only |
| V6 Cryptography | no | None in scope; no secrets stored by the bot |
| V7 Error handling / logging | yes | Never log the Telegram token; alert text uses fixed templates + `html.escape` for interpolated values; broker error strings excluded from alerts (engine precedent) |
| V8 Data protection | yes | Filled-in plist contains Telegram secrets: `chmod 600`, never committed (template placeholders only) |
| V14 Configuration | yes | Single rules file, fail-closed; DB path guard prevents opening the equity/options DB |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Real-money order path reached | Elevation | `assert_paper_account` (3 guards), `PAPER_TRADING`/`FUTU_TRD_ENV` hard-coded in plist, LIMIT-only, no `unlock_trade` |
| Corrupt/garbage snapshot drives a wrong trade | Tampering | Fail-closed `parse_snapshot` (Pitfall 3); sanity bound on qty (`size_position` with `limit_price > 0`); never act on ask/bid |
| Mis-edited `rules_ibs.json` (e.g. thresholds inverted, tiny window) | Tampering | Schema + cross-field `ConfigError`, process exits 1 before connecting |
| Bot closes a human's or the options bot's holding | Tampering / Repudiation | Only IBS rows are ever exited; external codes logged and blocked from entry; `NEEDS_ATTENTION` never auto-traded |
| Duplicate decision / double order after restart or double fire | Tampering | `meta` per-date key; partial unique index; entry guard re-reads broker positions and open orders |
| Telegram HTML/alert injection from broker strings | Tampering | `html.escape` on every interpolated value; `parse_mode HTML` only with escaped content |
| Orders left working overnight | Denial/Tampering | Hard-cancel sweep + DAY TIF + deadline-bounded decision + shutdown order |
| Secrets committed | Information disclosure | Plist template ships placeholders; runbook says `chmod 600`; `.env` is gitignored |
| SQL injection | Tampering | `?` placeholders only (store pattern), no string interpolation of values |

## Sources

### Primary (HIGH confidence)
- moomoo SDK source (installed `moomoo` 10.07.6708 at `/opt/homebrew/lib/python3.14/site-packages/moomoo`): `quote/open_quote_context.py:412-545` (snapshot column docstring), `:546-720` (the returned `col_list`, incl. `pre_*`/`after_*`), `quote/quote_query.py:422-570` (snapshot unpack: `update_time = record.basic.updateTime`, `volume`, `suspension`, `pre_/after_` maps at `:97-130`), `trade/open_trade_context.py:412-460` (`position_list_query` columns), `:462-487` (`order_list_query` columns incl. `dealt_qty`, `dealt_avg_price`, `order_status`), `:544-548` (`place_order` signature, `time_in_force=TimeInForce.DAY`), `:636-674` (`modify_order`), `common/constant.py:1418-1458` (`OrderStatus` values), `:1506-1528` (`ModifyOrderOp`), `:3006-3018` (`TimeInForce`)
- Live read-only OpenD probe, 2026-10-04 ~21:19 ET: 17-ETF `get_market_snapshot` (no subscription), `position_list_query(refresh_cache=True)`, `accinfo_query`, `order_list_query` through `MoomooGateway`; no order was placed
- Repo source read in full or in the cited ranges: `bot/main.py`, `bot/__main__.py`, `bot/options/{service,config,schema,store,execution,__init__}.py`, `bot/gateway/gateway.py`, `bot/execution/engine.py`, `bot/safety/{paper_guard,kill_switch,audit_log,et_helpers,logger}.py`, `bot/service/{alerter,report,watchdog}.py`, `bot/service/bot.py:440-690`, `bot/state/{store,migrations}.py`, `bot/scanner/calendar.py`, `tests/conftest.py`, `tests/options/{conftest,test_dispatch,test_service}.py`, `scripts/uat_options_probe.py`, `deploy/*`, `.scratch/strategy_search.py:1-202` and the other five research scripts, `.planning/config.json`, `08-SUMMARY.md`
- moomoo OpenAPI docs: https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html (15 req/30 s per acc_id, 0.02 s spacing, DAY default, price precision), https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-list.html and https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html (10 req/30 s per acc_id with `refresh_cache=True`; `cost_price` is diluted cost), https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html (400 codes/request; no subscription requirement stated; `update_time` is US Eastern for US stocks)

### Secondary (MEDIUM confidence)
- https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html via search: snapshot "60 requests every 30 seconds"
- Calendar behaviour verified by running `get_market_close_et` / `is_trading_day` for 2026-10-05, 2026-11-27, 2026-12-24, 2026-12-25 and 2027-07-02/05 in this worktree

### Tertiary (LOW confidence)
- None relied on. Items depending on unobserved RTH/SIMULATE fill behaviour are listed in the Assumptions Log instead.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all versions read from the live interpreter
- Architecture: HIGH — a direct mirror of shipped, live-exercised code with the divergences enumerated
- SDK/field semantics: HIGH for names/types/RTH-only high-low (live-verified); MEDIUM for `update_time` freshness during RTH (needs the RTH probe)
- Execution behaviour on SIMULATE for ETF LIMITs: MEDIUM — mechanism proven for option legs and equities, ETF round trip not yet observed
- Pitfalls: HIGH — each is tied to a file/line or a live observation

**Research date:** 2026-10-04
**Valid until:** 2026-11-03 (stable codebase; re-check only if `bot/options/execution.py`, `bot/safety/logger.py`, or the moomoo SDK/OpenD version changes)
