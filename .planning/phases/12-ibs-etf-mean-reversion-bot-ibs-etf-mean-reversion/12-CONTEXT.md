# Phase 12: IBS ETF mean-reversion bot (ibs_etf_mean_reversion) - Context

**Gathered:** 2026-10-04
**Status:** Ready for planning
**Source:** Operator decisions 2026-10-04 (chat, four explicit choices) after a two-round pre-registered strategy search run in this session (worktree `intelligent-knuth-f27d36`, scripts in `.scratch/`: `strategy_search.py`, `strategy_search_r2.py`, `ibs_robust.py`, `r5_robust.py`, `ibs_sizing.py`, `screen_study.py`). Trend Join Long was validated no-edge on 2026-08-13 (full-universe backtest, 226 trades, net −$2,546) and its premarket screen was shown to carry no intraday excess return on 2026-10-03.

<domain>
## Phase Boundary

A NEW, SEPARATE paper-trading bot process that trades one daily-bar mean-reversion
strategy on 17 liquid US ETFs, holding positions overnight for up to 10 trading days:

- `python3 -m bot --rules rules_ibs.json` (with `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266`) dispatched from `bot/main.py` by `strategy_name == "ibs_etf_mean_reversion"`.
- Self-contained `bot/ibs/` package mirroring `bot/options/` (Phase 8 D4/D6): own SQLite DB, own kill file, own report dir, own Telegram naming. `TradingBot` is a TEMPLATE, not a base class — its 5m BarAggregator / scanner / PositionManager wiring has no use here.
- Once per trading day, 10 minutes before the calendar-aware close (15:50 ET normal days, 12:50 ET half-days), it reads ONE batched moomoo snapshot of the universe, computes IBS per ETF, places exit orders then entry orders (LIMIT only), and cancels anything still unfilled 1 minute before the close.
- Reuses the shared infrastructure as-is: `bot/gateway/gateway.py`, `bot/safety/*` (paper guard, kill switch, audit log, ET helpers, logger), `bot/scanner/calendar.py` (trading days, close time), `bot/service/alerter.py` + `bot/service/report.py` patterns, `bot/service/watchdog.py`.

NOT in this phase: any change to the equity bot's live path (`bot/service/bot.py`, scanner, signal, position, execution), any change to the options bot, a backtester harness arm (the research scripts ARE the reference implementation), leveraged-ETF variants, margin, shorting, real-money anything.

Cutover (operator-run, documented in this phase, never executed by the agent): stop the Trend Join Long launchd job, start the IBS bot. The options bot keeps running alongside.
</domain>

<decisions>
## Operator decisions (FINAL — do not re-litigate)

- **OD-1 Sizing: unlevered IBS-17.** NOT the 2x variants (those doubled return and 2–2.5× the drawdown with a lower Sharpe; rejected by the operator).
- **OD-2 Deployment: new separate bot process** mirroring `bot/options/`; the equity bot code is untouched.
- **OD-3 Fill timing: near the close**, decisions from the live snapshot at close − 10 min; orders before the bell. (Backtest "close fill" column is the target; "next-open" column is the conservative floor.)
- **OD-4 Trend Join Long is STOPPED at cutover.** It showed no edge in every test. Its unmerged scanner fixes on this branch (commits d2720f1, 568e310, 6cda8cc, 9bf3222) ride along to develop but become moot.

## Strategy (fixed by the research; every number lives in `rules_ibs.json`, schema in `bot/ibs/schema.py`, NO hardcoded strategy literals in Python)

- **D-01 Universe** (17 codes, Moomoo format): `US.SPY US.QQQ US.IWM US.DIA US.XLK US.XLF US.XLE US.XLV US.XLI US.XLY US.XLP US.XLU US.XLB US.TLT US.GLD US.EFA US.EEM`.
- **D-02 Signal**: `IBS = (last − low) / (high − low)` from TODAY's session snapshot (day high, day low, last price). Fail closed per code: skip when any field is missing/NaN/≤ 0, when `high == low`, or when the snapshot is not from today's session.
- **D-03 Entry**: `IBS < signal.ibs_entry_max` (0.20), code not already held (and no working entry order), a free slot available. Candidates ranked ascending by IBS (lowest first). Research rule reference: `strategy_search.py::simulate` entry branch.
- **D-04 Exit**: `IBS > signal.ibs_exit_min` (0.80) at decision time, OR `trading_days_held >= signal.max_hold_trading_days` (10), where `trading_days_held` = number of NYSE trading days strictly after the entry date up to and including today (matches `t − d0 >= max_hold` in the research sim). Exits are decided and placed BEFORE entries; slots freed by today's exits are usable by today's entries (research: `free = slots − (len(pos) − len(exits))`).
- **D-05 Sizing**: `risk.max_concurrent_positions` = 10 slots; each position targets `risk.position_pct_of_equity` (10%) × `risk.sizing_equity_usd` (100000) → `qty = floor(10000 / limit_price)`; skip if qty < 1. Equal weight, no pyramiding, one position per code.
- **D-06 Overnight holds are the design.** This bot has NO 15:51 force-close, must never call the equity bot's `force_close_all`, and must never be affected by it (separate process + separate DB already guarantee this; a test must assert the IBS service registers no force-close job).
- **D-07 Timing** (calendar-aware via `bot/scanner/calendar.py`): decision job at `get_market_close_et(today) − service.decision_before_close_min` (10); hard-cancel sweep at `close − service.hard_cancel_before_close_min` (1); post-close reconcile + EOD report at `close + service.eod_report_after_close_min` (5). Non-trading days: every job no-ops. Mid-day restart after the decision time: no decision today (jobs are time-triggered with the same coalesce/misfire-grace pattern as the equity bot).

## Execution invariants (inherit from the codebase)

- **D-08 LIMIT orders only** (`OrderType.NORMAL`; MARKET is never submitted — EXEC-02). Marketable limit: BUY at `last + execution.entry_limit_buffer_usd`, SELL at `last − execution.exit_limit_buffer_usd`. TTL (`execution.order_ttl_seconds`) then bounded re-price toward the market by `execution.escalation_step_usd` up to `execution.max_reprices` times — the same shape as `bot/execution/engine.py`'s TTL/escalation (reuse its helpers where they are pure; do not import the equity engine's position-manager coupling).
- **D-09 No overnight open orders.** Whatever is still working at `close − 1 min` is cancelled. An unfilled entry is simply skipped that day (no carry-over intent). An unfilled/partial exit leaves the position open; it is re-evaluated next session (an exit is retried every session until flat) and raises a Telegram warning.
- **D-10 Paper only.** `bot/safety/paper_guard.py` triple-check on connect (SIMULATE env + account env check + broker-reported env). Every placed/modified/cancelled order is appended to the audit log (SAFE-05). `unlock_trade` is never called.
- **D-11 Startup readiness gate** (mirrors Phase 8 D8): `gateway.connect()` → reconcile THIS bot's open positions (own DB) against broker positions → enable trading. Qty mismatch or a DB position the broker no longer holds → mark `NEEDS_ATTENTION` + alert; never auto-trade to "fix" it.
- **D-12 SAFE-OG-01 analog**: the bot only ever manages codes that appear on a position row in ITS OWN database. A broker holding in a universe ETF that this bot did not buy (equity bot leftovers, manual holdings, options assignments) is counted, logged, and never closed, adopted, or sized against. Entry guard: do not enter a code the broker already holds externally (same rule the options bot got in 260926-kvt).
- **D-13 Shared-account coexistence**: no K_5M subscriptions needed (snapshot is subscription-free); if any subscription is used, `is_all_conn=False` semantics apply. Own DB (`data/ibs_state.db`), own kill file, own report dir (`reports/ibs/`), own log file (`logs/ibs.log`) — follow the exact D6 naming pattern used by `bot/options/service.py`.
- **D-14 Operations**: kill-switch graceful shutdown (cancel working orders, flush DB, leave positions — they are meant to be held), OpenD watchdog with reconnect, structured JSON logs, Telegram alerts (entry filled, exit filled, exit retry/partial, reconcile mismatch, errors, daily summary naming the strategy), EOD HTML report (positions held, trades closed, realized P&L, mark-to-market), launchd plist under `deploy/` + runbook.

## Quality bar

- **D-15 TDD**: failing test first for every behaviour; full suite green (baseline 1401 passed / 1 skipped on `claude/awesome-gould-a0cef9`). Tests never write to `logs/bot.log`, any production DB, or the real audit log (existing conftest isolation; extend it for the new log/DB paths).
- **D-16 Pure strategy core**: `bot/ibs/strategy.py` holds pure functions (`compute_ibs`, `decide_exits`, `decide_entries`, `size_position`, `trading_days_held`) with no I/O, plus a PARITY test that replays a small synthetic multi-day scenario through both the research sim's rules (ported verbatim from `strategy_search.py::simulate`'s decision logic) and the production functions and asserts identical entry/exit decisions.
- **D-17 Operator UAT path**: `scripts/uat_ibs_probe.py` — read-only during RTH: snapshot → IBS table (code, high, low, last, IBS) → what the bot WOULD do (exits/entries/qty), places nothing; plus `--live-1lot --confirm` operator-run smoke mode placing ONE 1-share paper order with the full TTL/cancel path, like `scripts/uat_options_probe.py`.
- **D-18 Security**: per-phase threat model + `/gsd-secure-phase` audit as usual (new attack surfaces: rules file parsing, snapshot data trust, DB, Telegram).
- **D-19 Ponytail**: minimal build. No new abstractions beyond what `bot/options/` already proved necessary; no plugin/strategy registry; no backtester harness rewrite; no yfinance in the live path. Research scripts move to `backtester/experimental/ibs_search/` unchanged except for path constants.

## First deliverable (Plan 1): research provenance

Commit `backtester/experimental/ibs_search/{strategy_search.py, strategy_search_r2.py, ibs_robust.py, r5_robust.py, ibs_sizing.py, screen_study.py}` and write `docs/research/2026-10-04-ibs-etf-strategy-search.md` in the style of `docs/research/2026-08-18-external-strategies-results.md`: pre-registered hypotheses and pass criteria for both rounds, the verdict tables, robustness (cost ×1/×2/×4, leave-one-out, per-ETF standalone, yearly OOS), the sizing table, the 2026-10-03 screen-study result, and the explicit limitations (survivorship for stock universes, backtest ≠ live, "close fill" assumes a 15:50 decision).

Reference numbers (OOS 2019-01-01..2026-10-02, net of 0.02%/side, 17 ETFs, 10 slots): close fill CAGR 16.4%, Sharpe 1.30, maxDD −11.2%, PF 1.63, 3164 trades, positive 7/8 years; next-open fill CAGR 12.7%, Sharpe 1.07, maxDD −21.7%. IS 2010-2018: CAGR 9.1%, Sharpe 0.91. SPY buy & hold OOS: 17.3% / 0.93 / −33.7%. Flat years: 2022 +4%, 2023 +2%, 2024 −2%.
</decisions>

<requirements>
## Requirements (Phase 12)

- **IBS-01** Config: `rules_ibs.json` validated by `bot/ibs/schema.py`; `ConfigError` fail-closed on any missing/invalid field; no strategy literal in Python.
- **IBS-02** Dispatch: `python3 -m bot --rules rules_ibs.json` routes to `bot.ibs.service.main`; the equity and options dispatch paths are unchanged (regression tests).
- **IBS-03** Signal/decision purity: D-02..D-05 as pure functions with the research-parity test (D-16).
- **IBS-04** Daily decision job at close − 10 min (calendar-aware, half-day aware, non-trading-day no-op), exits before entries, one batched snapshot.
- **IBS-05** Execution: LIMIT-only marketable orders with TTL/escalation, hard cancel at close − 1 min, no overnight open orders, audit-logged.
- **IBS-06** State: own SQLite DB with positions (entry date, qty, avg fill, status), trades (realized P&L), orders/intents; idempotent per-session upserts; `trading_days_held` derived from the NYSE calendar.
- **IBS-07** Readiness gate + reconcile + SAFE-OG-01 analog (D-11, D-12) + external-holding entry guard.
- **IBS-08** Operations: kill switch, watchdog, Telegram alerts, EOD HTML report, structured logs, own log/DB/kill/report paths (D-13, D-14).
- **IBS-09** Operator tooling: `scripts/uat_ibs_probe.py` read-only + `--live-1lot --confirm`; `deploy/` launchd plist; runbook covering cutover (stop Trend Join Long, start IBS) and the Monday-morning checks.
- **IBS-10** Provenance: research scripts committed under `backtester/experimental/ibs_search/` + results doc (Plan 1).
</requirements>

<deferred>
## Deferred / explicitly out of scope

- 2x / mixed-leverage variants (operator rejected).
- A dedicated IBS backtester arm in `backtester/` (research scripts are the reference).
- Regime filters, volatility scaling, additional ETFs, parameter sweeps (no re-tuning — the edge is the published rule).
- Equity-bot retirement code changes (operator stops the process; code stays).
- Options bot changes.
</deferred>

<open_questions>
## For the researcher/planner to resolve from the SDK (not from guesswork)

1. Exact `get_market_snapshot` column names for day high / day low / last price (`high_price`, `low_price`, `last_price` expected; the gateway today only parses `pre_high_price`) and the field that proves the snapshot is from today's session (`update_time`?). Verify against the installed `moomoo` SDK docstrings.
2. Whether the trade context's position query returns the ETF holdings with a usable `qty`/`cost_price` for reconcile (it does for the equity bot — confirm the same call works for ETFs; it should).
3. The options bot's exact D6 file/dir naming so the IBS bot mirrors it one-for-one.
</open_questions>
