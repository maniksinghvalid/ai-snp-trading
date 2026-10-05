---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: verifying
stopped_at: Phase 11 complete — UAT 5/5 passed (2026-09-26); verified passed; branch not yet merged to develop
last_updated: "2026-10-05T02:50:06.779Z"
last_activity: 2026-10-05 -- Phase 12 execution started
progress:
  total_phases: 14
  completed_phases: 13
  total_plans: 72
  completed_plans: 73
  percent: 93
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-23)

**Core value:** The bot autonomously executes the Trend Join Long strategy end-to-end on a paper account — scan, enter, manage risk, exit, and report — correctly and unattended.
**Current focus:** Phase 12 — IBS ETF mean-reversion bot (ibs_etf_mean_reversion)

## Current Position

Phase: 12 (IBS ETF mean-reversion bot (ibs_etf_mean_reversion)) — EXECUTING
Plan: 9 of 9
Status: Phase complete — ready for verification
Last activity: 2026-10-05 -- Phase 12 execution started

Progress: [██████████] 9/9 phases (100%)

## Performance Metrics

**Velocity:**

- Total plans completed: 33
- Average duration: 6 minutes
- Total execution time: ~0.3 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 4 complete | 22 min | 5.5 min |
| 03 | 3 | - | - |
| 06 | 12 | - | - |
| 10 | 6 | - | - |
| 11 | 9 | - | - |

**Recent Trend:**

- Last 5 plans: 01-01 (8 min), 01-02 (4 min), 01-03 (7 min), 01-04 (3 min)
- Trend: consistent, improving

*Updated after each plan completion*
| Phase 02-premarket-scanner P01 | 8 minutes | 3 tasks | 6 files |
| Phase 02-premarket-scanner P02 | 12 minutes | 3 tasks | 4 files |
| Phase 02-premarket-scanner P03 | 18 | 3 tasks | 4 files |
| Phase 03-intraday-signal-and-risk-engine P01 | 11 minutes | 3 tasks | 14 files |
| Phase 03 P02 | 272 | 2 tasks | 2 files |
| Phase 03-intraday-signal-and-risk-engine P03 | 310 | 2 tasks | 4 files |
| Phase 07.1 P01 | 6 minutes | 2 tasks | 2 files |
| Phase 06-backtester PP01 | 12 minutes | 2 tasks tasks | 7 files files |
| Phase 06 P02 | 25 | 2 tasks | 2 files |
| Phase 06 P03 | 20 | 2 tasks | 2 files |
| Phase 06 P04 | 10 | 2 tasks | 1 files |
| Phase 06-backtester P05 | 45 | 2 tasks | 2 files |
| Phase 06-backtester P06 | 20 | 2 tasks | 2 files |
| Phase 06.2 P02 | 24 | 8 tasks | 13 files |
| Phase 06.2 P03 | 12min | 2 tasks | 5 files |
| Phase 09 P01 | 30min | 3 tasks | 6 files |
| Phase 09 P02 | ~35 minutes | 3 tasks | 3 files |
| Phase 09-options-backtester P03 | ~50 minutes | 3 tasks | 4 files |
| Phase 09 P04 | 90 minutes | 3 tasks | 4 files |
| Phase 09-options-backtester P05 | 55min | 3 tasks | 11 files |
| Phase 10 P01 | 10min | 3 tasks | 5 files |
| Phase 10-external-strategy-research P02 | 35min | 3 tasks | 10 files |
| Phase 10-external-strategy-research P03 | 25min | 3 tasks | 2 files |
| Phase 10-external-strategy-research P04 | 20min | 3 tasks | 0 files |
| Phase 10-external-strategy-research P05 | 2h | 3 tasks | 11 files |
| Phase 10 P06 | ~20 minutes | 3 tasks | 2 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P01 | 25min | 2 tasks | 4 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P02 | 25min | 3 tasks | 3 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P03 | 8min | 2 tasks | 6 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P04 | 8min | 2 tasks | 6 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P05 | 25min | 2 tasks | 2 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P06 | 14min | 2 tasks | 2 files |
| Phase 11 P07 | 20min | 3 tasks | 4 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P08 | 25min | 3 tasks | 5 files |
| Phase 11-multi-strategy-options-bot-bull-call-spread P09 | 15min | 3 tasks | 4 files |
| Phase 12 P01 | 25min | 2 tasks | 18 files |
| Phase 12 P02 | 15min | 2 tasks | 7 files |
| Phase 12 P03 | 12min | 2 tasks | 8 files |
| Phase 12 P04 | 15min | 2 tasks | 3 files |
| Phase 12 P06 | 20min | 2 tasks | 3 files |
| Phase 12 P07 | 15min | 2 tasks | 5 files |
| Phase 12 P08 | 25min | 2 tasks | 3 files |

## Accumulated Context

### Roadmap Evolution

- Phase 12 added (2026-10-04): IBS ETF mean-reversion bot (`ibs_etf_mean_reversion`) — successor to Trend Join Long (validated no-edge 2026-08-13; screen study 2026-10-03). Operator decisions: unlevered IBS-17, separate bot process, near-close fills, Trend Join Long stopped at cutover. Context in `.planning/phases/12-*/12-CONTEXT.md`.
- Phase 06.2 inserted after Phase 6 (2026-07-02): Code review remediation — fix 16 confirmed findings from 2026-07-02 review (3 tiers: blockers, correctness, hygiene) (URGENT)
- Phase 07.1 inserted after Phase 07 (2026-07-06): Close gap: RISK-TICK-STOP — wire gateway into PositionManager (found by /gsd-audit-milestone v1.0: bot/main.py never passes gateway= to PositionManager, so arm_stop_protection() no-ops in production) (URGENT)
- Phase 9 added (2026-08-17): Options backtester — replay bot/options/strategy.py pure functions over Massive option daily aggregates (contracts reference + O:… daily bars, Black-Scholes IV/delta, own ATM-IV series for IVR); pre-registered hypotheses (IVR 20 vs 30, 16Δ vs 20Δ, IC vs PCS) before any rules_options.json change
- Phase 10 added (2026-08-18): External strategy research — two Reddit day-trading strategies critically extracted (via Apify, reddit.com blocks direct fetch) and compared against TJL; new `backtester/experimental/` package backtests their automatable cores + candidate improvements (weekly SPY regime gate, exit model, entry-window/stop sensitivities) cache-only against the existing 24-symbol windows A–E, pre-registered hypotheses H1–H8, 14-section report; research-only, `rules.json`/`rules_options.json` unchanged, any SUPPORTED production integration goes on a separate unmerged branch. Design: `~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md`. Runs independently of the still-pending Phase 9 evidence run (warm-cache-pool, unaffected — Phase 10 uses yfinance for its one new data need, zero Massive requests)
- Phase 11 added (2026-09-24): Multi-strategy options bot — `strategies` array in `rules_options.json`, one options process runs `tasty_credit_spreads` (unchanged) + new `super_bull_call` bull call debit spread (Options With Ravish, youtube VZ1MbM3UQ5Q; transcript via Apify) on the equity premarket watchlist (read-only `data/bot_state.db`); per-strategy sizing, global breaker/BP cap; `load_options_config` keeps its flat contract so the Phase 9 backtester/probe are unaffected. Lifts the multi-strategy out-of-scope item for the options bot only. Design: `docs/superpowers/specs/2026-09-24-multi-strategy-options-design.md`

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: Bottom-up 6-phase build order derived from research SUMMARY.md dependency analysis
- Roadmap: Safety features (SIMULATE lock, state, reconciliation, kill switch) placed in Phase 1 — not deferred
- Roadmap: SVC-03 (structlog) and SVC-04 (zoneinfo ET) placed in Phase 1 as foundational infrastructure
- Roadmap: SIG-01 (watchlist-scoped subscriptions) placed in Phase 2 where subscriptions first occur
- Roadmap: Backtester is Phase 6 (last) to ensure bot/strategy/ and bot/position/ are stable before depending on them
- 01-01: D-02 upheld — gateway imports moomoo SDK directly, never from skills/; skills/ remains an untouched CLI reference
- 01-01: Paper guard raises PaperGuardError (never terminates process) — only bot/main.py handles process exit
- 01-01: UTC timestamps in audit log via datetime.now(timezone.utc) — utcnow() is deprecated in Python 3.12+
- Refine (2026-06-23, humbledtrader inputs): yfinance for scan + backtest data, Moomoo for execution + live 5m only
- Refine: `rules.json` externalized config (CFG-01) as single source of truth for live + backtest
- Refine: intraday re-scan every ~30 min (SCAN-07); top-20 gap-ranked watchlist cap (SCAN-08)
- Refine: daily new-entry cap `max_trades_per_day` (RISK-05); order_id-keyed fill matching (EXEC-05); strengthened paper guard (SAFE-01); optional static HTML dashboard (DASH-01)
- 01-02: bot/state/__init__.py started minimal and gained re-exports after store.py was created (avoids circular import during incremental task execution)
- 01-02: StateStore.open() enables WAL journal mode for better concurrency
- 01-02: atomic_write_json: temp in same dir as target guarantees same-filesystem atomic rename; chmod(0600) after os.replace
- 01-03: CFG-01: rules.json at repo root is single source of truth; StrategyConfig flattens nested JSON groups
- 01-03: RVOL denominator uses date < signal_date strict cutoff (no look-ahead, Pitfall #4)
- 01-03: D-12 behavioral proof via config-swap test (not AST scan); compute_initial_stop uses cfg.max_risk_per_trade_pct/100
- 01-04: Naive datetime passed to to_et() treated as UTC (not host-local) — explicit, consistent with PITFALLS #6
- 01-04: structlog configured with cache_logger_on_first_use=True; reset_defaults() in tests for isolation
- 01-04: KillSwitch _trigger protected by threading.Lock; idempotency via _triggered_once flag
- 01-04: AUDIT_LOG_PATH swapped via module attribute in tests (no monkeypatching of append_audit internals)
- [Phase ?]: 02-00: lxml added to requirements.txt explicitly — not pulled transitively by yfinance 1.4.1 on Python 3.14/Homebrew; pd.read_html requires HTML parser
- [Phase ?]: 02-00: T-02-SC package gate satisfied — yfinance -> github.com/ranaroussi/yfinance; pandas-market-calendars -> github.com/rsheftel/pandas_market_calendars; operator pre-approved before install
- 02-01: shared._ERRORS testing uses real yfinance.shared._ERRORS dict with side_effect mock to avoid patch-then-clear race (patch replaces object, code clears it, mock values lost)
- 02-01: wiki_to_yfinance uses str.replace('.', '-') — sufficient for all current S&P 500 tickers (BRK.B, BF.B); no regex needed
- 02-01: calendar.py reads market_close UTC from schedule() and tz_converts to ET; handles both half-day (13:00) and normal day (16:00) via same code path
- 03-01: SIG-02 — BarAggregator fires on_bar_closed exclusively on time_key advance; _seen_time_keys dedup survives SDK reconnects (Pitfall 1)
- 03-01: LOD = session running-min from first K_5M bar (not single bar low) — Pitfall 3 / Open-Q3 RESOLVED
- 03-01: get_market_snapshot is interpretation-free thin broker read; D-01/D-03 logic lives in 03-02 fetch_premarket_highs
- 03-01: Migration 0003 uses callable with executescript for idempotent CREATE TABLE IF NOT EXISTS (WR-03)
- 03-02: _in_entry_window() parses cfg.earliest_entry_et/latest_entry_et HH:MM strings — no hardcoded time literals (CFG-01)
- 03-02: D-03 conservative exclusion — pre_high_price <= 0/None/NaN excluded; non-RET_OK snapshot returns empty dict without raising
- 03-02: D-09 burst guard — _pending_count incremented immediately on SignalEvent emit; Phase 3 never writes daily_trade_count (Pitfall 5)
- 03-02: D-10 re-entry gate requires BOTH broker-flat (get_positions()) AND no pending_intents PENDING row for the code
- 03-02: asyncio.run() used in tests (Python 3.14 removed implicit default event loop in main thread)
- 03-03: get_equity() degrades gracefully — never raises, falls back to $100k on failure or implausible value (D-05)
- 03-03: math.floor used for both risk_qty and notional_cap_qty — explicit floor semantics (D-07, T-03-08)
- 03-03: RiskEngine optional signal_engine parameter wires note_intent_emitted() for D-09 burst guard (RISK-05)
- 03-03: Both _IMPLAUSIBLE_LOW ($1k) and _IMPLAUSIBLE_HIGH ($10M) as named constants — no inline literals (T-03-07)
- 07.1-01: RISK-TICK-STOP Gap 1 closed — bot/main.py now passes gateway=gateway into PositionManager(...); direct constructor-kwarg fix (not a bot/service/bot.py late-bind) since gateway is in scope at construction (main.py:75)
- 07.1-01: Wiring-regression pattern — drive real bot.main.main() with only I/O seams patched (MoomooGateway/StateStore/asyncio.run), capture the real PositionManager, assert _gateway identity + arm_stop_protection→subscribe_quote dispatch; hand-built PositionManager(gateway=mock) is what let this ship broken
- [Phase ?]: 06-01: fixture bars engineered so bar N close (105.00) and bar N+1 open (103.50) are trivially distinguishable — no look-ahead false negatives possible
- [Phase ?]: 06-01: test_execution.py uses a hand-rolled _FakeFeed (not backtester.feed.SimulatedBarFeed) to keep the stub dependent only on fixtures.py per the plan's key_links
- [Phase ?]: 06-02: next_bar(code, after) param named 'after' (matches PATTERNS.md SimulatedExecution call-site + test stub, not the plan prose's 'after_time_key')
- [Phase ?]: 06-02: premarket_highs() reuses bot.scanner.fetcher._download_batch directly (no public prepost=True 5m wrapper exists) rather than duplicating retry/degradation logic
- [Phase ?]: 06-02: daily_bars()/intraday_5m_for_tod() return the RAW yf.download dict (not routed through get_ticker_frame) since _evaluate_symbol/_compute_tod_baselines expect the raw per-ticker frame themselves
- [Phase ?]: 06-03: manage_exit's live signature carries no bar/time_key, so SimulatedExecution exposes on_bar(bar) recording the latest bar per code as the anchor for its own next_bar lookup (mirrors bar_buffer wiring, 06-RESEARCH Pitfall 6)
- [Phase ?]: 06-03: FillEvent.fill_time and exit_fills/fills rows store bar time_key strings (matches feed.py/fixtures.py convention), not datetime objects, despite the live dataclass's type annotation
- [Phase ?]: 06-04: win_rate/gross_profit/gross_loss use derived realized_pnl > 0 (agrees with r_multiple sign on the fixture; documented choice per plan behavior spec)
- [Phase ?]: 06-04: profit_factor==float('inf') round-trips through json.dump/json.load as the Infinity literal, no string sentinel needed
- [Phase ?]: 06-05: harness.setup_day restricts _compute_tod_baselines input to sessions strictly prior to `day` -- computing it from the same day being replayed is self-referential (rvol ratio always 1.0) and violates BT-02 no-look-ahead
- [Phase ?]: 06-05: harness.setup_day calls _compute_tod_baselines with cfg.rvol_tod_lookback_days (not rvol_lookback_days), matching scanner.py's run_daily_scan call site
- [Phase ?]: 06-05: harness drops the D-08 circuit-breaker side-effect gate (_entries_enabled) -- no kill-switch concept in an offline replay; SignalEngine's own Gate 7 circuit-breaker check still runs
- [Phase ?]: 06-05: harness sets PositionState.opened_at/updated_at from the fill's bar time at construction (not left None until on_fill) -- StateStore's positions table has both columns NOT NULL; harness also normalizes FillEvent.fill_time from SimulatedExecution's raw time_key string to a real ET datetime before on_fill() (both harness-side adapters, bot/ and backtester/execution.py unmodified)
- [Phase 06-06]: main(argv=None) returns an int exit code (not sys.exit) for testability; sys.exit(main()) only at __main__ guard
- [Phase 06-06]: DB-collision guard tested via monkeypatching _scratch_db_path itself, since the real uuid-based path can never naturally collide with data/bot_state.db
- [Phase 06-06]: end-to-end CLI test reuses test_harness.py's dedicated signal/fill/stop-out dataset (not the ahead-only fixture named in the plan) since the ahead-only fixture never clears the real SignalEngine I2 gate
- [Phase 06.2]: 06.2-02: reused pre-existing store.record_trade (backtester harness writer) for the live exit-fill trades-table write instead of adding a duplicate insert_trade(dict) method
- [Phase 06.2]: 06.2-02: gateway bid/ask price fetch retries then falls back to the last known good price within the same order loop (not bar-close data) on GatewayError; entry path now abandons gracefully instead of propagating an unhandled exception
- [Phase ?]: 06.2-03: _reconcile_core unifies reconcile_once (manager._positions-driven, alerts) and startup_reconcile (store.get_open_positions()-driven, alerter=None) via an is_startup=alerter-is-None branch, preserving both call sites pre-refactor behavior exactly (verified against test_restart_reconciliation cold-boot assertions with manager=None)
- [Phase ?]: 06.2-03: daily_bars_cache is a caller-owned dict threaded through run_intraday_rescan -> _compute_candidates and owned by TradingBot._daily_bar_cache, not a module-level singleton; run_daily_scan never passes it since premarket runs once per day
- [Phase 09]: 09-01: OptionChainSource.load() keeps all DTE/strike-band candidates (monthly+non-monthly); pick_expiry applies the monthly preference itself, since a 30..60 dte window is not always wide enough to contain a monthly expiry (Rule 1 bug found via test)
- [Phase 09]: 09-01: Massive option daily-aggregates history boundary pinned to August 2024 (rolling ~24mo window from today); missing bars are absent rows, never v=0 (A2 confirmed)
- [Phase ?]: IS window 2024-11-18..2025-07-31, OOS window 2025-08-01..2026-06-15, both inside the Aug-2024..today Massive entitlement; 67-trading-day IVR warm-up precedes IS start (09-02)
- [Phase ?]: implied_vol's fail-closed floor is discounted (present-value) intrinsic, not naive spot-strike -- deep-ITM European puts legitimately price below naive intrinsic (09-02)
- [Phase 09-03]: 09-03: credit_per_spread on the position dict is the fill-adjusted (post-slippage) credit, not pick_strikes' pre-fill sel[credit] -- more realistic downstream max_loss/manage-time math
- [Phase 09-03]: 09-03: expiry settlement checked before manage_decision in _manage_day -- an expiring position always settles at intrinsic, never races a manage_decision exit reason on its own expiry day
- [Phase ?]: 09-04: warm-up priming via a dedicated CLI-side loop (_prime_iv_series) feeding engine._iv_series directly, not engine.run(warmup_days) -- OptionsBacktestEngine.run_day has no public warm-up-only API and is frozen
- [Phase ?]: 09-04: all 3 hypotheses (H1/H2/H3) verdicted INSUFFICIENT-EVIDENCE -- SPY's option chain density x measured Massive fetch rate (5.3-5.8 req/min) projects ~183hr for one arm's one window, ~90x the plan's 2hr stop-and-report budget; grouped-daily escape hatch probed live, HTTP 400; rules_options.json unchanged
- [Phase 09-options-backtester]: 09-05: lazy per-decision-day fetch (rows_for) replaces eager contracts_for_day -- ~7.5k SPY contracts for the full 2024-08..2026-08 history at +/-10% band vs 58,366+ for one IS window; VERIFICATION gap 3 closed
- [Phase 09-options-backtester]: 09-05: CR-01 closed -- close_open_at_end() settles positions still open at --end with exit_reason=end_of_window; no longer silently dropped from reported metrics
- [Phase 09-options-backtester]: 09-05: root cause corrected -- Massive is a hard 5-req/min server-side tier cap (0.23s raw latency), not a gradual rate; free tier ~25h once for all of SPY, paid tier ~5min with --workers 8; all 3 hypotheses remain INSUFFICIENT-EVIDENCE (no live fetch run in this offline-only plan)
- [Phase 10]: 10-01: pre-registration doc + arms.json committed in ONE commit (f8439aa) before any experimental run artifact -- plan-mandated falsifiability discipline, not per-task commits — T-10-01 threat mitigation requires provable git ordering: doc+arms.json commit must precede backtester/results/experimental/
- [Phase 10]: 10-01: SPY regime series fetched via bot.scanner.fetcher._download_batch (yfinance), zero Massive requests, cached to gitignored backtester/cache/SPY_1d_regime.csv with sha256 provenance recorded in the committed doc
- [Phase 10-external-strategy-research]: 10-02: engine.py imports swing_low_2_2 directly and precomputes the swing pivot per bar, passing it into exits.py as new_swing_low/new_swing_high (mirrors bot.position.manager's compute-then-pass-in convention; satisfies the plan's key_link and the exactly-2-from-bot-imports grep) while exits.py keeps a bars-based fallback
- [Phase 10-external-strategy-research]: 10-02: strategies.py signal functions compute indicators internally per call (not pre-attached by build_frame) so they are independently testable/prefix-invariant before engine.py exists in the same plan
- [Phase ?]: 10-03: run.py frame cache in _run_arms keyed by code alone (not a params signature) -- build_frame(feed, code, params) verified to not consume params in 10-02's implementation, so code-only caching is maximally efficient and provably correct
- [Phase ?]: 10-03: run.py --tjl-regime is an early return in main() before window resolution/cache guard -- reads baseline trades.csv read-only, day-filters via regime_for_day(opened_at[:10]) string slicing only, never invokes the TJL replay harness
- [Phase ?]: 10-04: No per-task git commits — all run artifacts under backtester/results/experimental/ are gitignored by plan design; only the final metadata commit lands (mirrors 10-01 precedent)
- [Phase ?]: 10-04: cache-mtime post-run check excludes Phase 9 warm-cache-pool's own O_*-prefixed option-contract files (disjoint symbol space) to correctly isolate this plan's cache-only compliance — 0 new equity cache files confirmed
- [Phase ?]: 10-05: aggregate.py's TJL comparator uses a narrower E1-E3(IS)/D1-D3(OOS) pool than the MEGA24 arms' IS_SLICES/OOS_SLICES (which also include C/B/A) -- matches the hypotheses doc's explicit H7 comparator definition
- [Phase ?]: 10-05: combo arm never appended to arms.json -- pre-registered rule (PF>1 IS with >=25 trades) never fired; best of 15 arms is orb5 IS PF 0.922. H1-H8 verdicted: only H2 SUPPORTED (partial_be_trail beats pct_ladder on Ext#2's own entries); H1/H3/H4/H5/H6/H7/H8 REJECTED. Recommendation: no production change, rules.json/rules_options.json unmodified
- [Phase ?]: 10-06: Gate TRIGGERED via H2 (mechanical rule: SUPPORTED both IS/OOS, floor met) -- not H8-specifically; operator selected defer at Task 2 checkpoint (both exit variants PF<1.0, not a profitable arm); no feature branch built, file list recorded in results doc Section 12; XSR-06 stays open
- [Phase ?]: 11-01: New OptionsConfig fields carry no dataclass default (research A1) — both loader paths pass every field explicitly
- [Phase ?]: 11-01: _check_strategy covers both entry IV-gate keys and manage profit-target/stop-loss key pair (Pitfall 3), sharing one _RELOCATED table with legacy_view (Open Question 3 resolved)
- [Phase ?]: 11-02: size_position body extracted into shared _size_for_risk so size_debit_position reuses the exact floor+BP-cap logic; credit-path numeric results provably unchanged
- [Phase ?]: 11-02: _pick_bull_call reuses leg_is_liquid/_closest_delta/_pick_wing/_mid/_leg rather than duplicating credit-path helpers for the debit structure
- [Phase 11]: 11-03: insert_option_position's omit-None column build (not a strategy_name special case) — every nullable column still lands NULL when omitted, matching pre-existing behavior exactly
- [Phase 11]: 11-03: count_opened_on gained an optional strategy_name keyword (default None reproduces the exact pre-existing SQL) rather than a second method
- [Phase 11]: 11-03: bot/options/universe.py's no-bot.state-import isolation (D-17) is enforced by an ast-walk test, not just code review
- [Phase ?]: 11-04: options_run's debit-structure rejection reads the effective (post --set) config, not args.strategy, so --set structure.type=bull_call_spread is caught for any base strategy
- [Phase ?]: 11-04: bot/main.py dispatch widened to strategy_name==tasty_credit_spreads OR 'strategies' in data (research Pitfall 1/T-11-03) -- keeps the live launch command routing to the options bot after the rules_options.json conversion
- [Phase 11-05]: cfg threaded as an explicit parameter through _scan_and_open/_try_open instead of self._cfg, so one entry-scan body serves every strategy in the book
- [Phase 11-05]: Fixed a latent bug (Rule 1): options_position_opened audit read sel['credit'] unconditionally, which KeyErrors on a debit sel dict -- switched to pos['credit_per_spread'] (correct signed value for both structures)
- [Phase ?]: 11-06: manage_decision_debit's sign math and the existing net_exit/realized_per_spread close math needed zero changes for the debit case — verified against the NVDA worked example (5 spreads @ 8.00/1.20 -> realized 2420.00, 60.2% of max profit)
- [Phase ?]: 11-06: _manage_position keeps a defense-in-depth skip for a position whose strategy_name is not in self._strategies, behind the D-29 startup reconcile guard (belt-and-suspenders, not a substitute)
- [Phase 11-07]: WR-01 kind check compares structure KIND (bull_call_spread vs any credit structure), not exact structure_type string equality -- iron_condor and put_credit_spread share manage_decision and identical config fields
- [Phase 11-07]: manage_position's structure-kind check (WR-01) runs before the CR-01 quote gate so a mismatched row is never escalated using the wrong strategy's assignment_guard_dte
- [Phase 11-08]: CR-02: open_position returns None if unwound else False; _try_open returns pos (not a new dict) on the False branch so the SAME scan's BP/busy/concurrent-cap accounting counts the live exposure immediately
- [Phase 11-08]: WR-06: escalate at 3 consecutive counted bad-quote manage cycles inside the guard window, or on the expiry session's final manage cycle; a snapshot-outage chunk never counts
- [Phase 11-08]: WR-07: _quote_markable = ask-bid <= max(0.5*mid, $0.10); bid=0 accepted only when ask<=$0.10 (not a literal bid>0), so a legitimate far-OTM 0.00/0.05 wing is never stranded; gate = _quote_ok if in_guard else _quote_markable supersedes 11-07's hardcoded _quote_ok call site
- [Phase 11-09]: CR-03: fill_leg's per-attempt try wraps everything after place_order; except GeneratorExit re-raises untouched, except BaseException shields cancel_order(order_id) then re-raises the ORIGINAL exception (never a return value) -- a partial fill before the exception is unknown exposure the caller must escalate
- [Phase 11-09]: CR-03: no order ids added to the three hand-off alerts (not cheaply available; the unwind's close_legs runs with no callbacks) -- the leg_cancel_on_error_failed audit event carries the only order id that can still be working
- [Phase 11-09]: WR-10: one-time _warn_expiry_unmanaged on the FIRST expiry-day manage cycle that cannot manage a position (guarded by self._expiry_warned), not a widened final-cycle window -- holds even when APScheduler drops the final fire; no new NEEDS_ATTENTION path
- [Phase 11-09]: IN-08: self._quote_miss_streak now stores (ET session date, count) so a day-before-expiry streak can no longer make the first expiry-day miss escalate
- [Phase 11-09]: WR-11: self._snapshot_outage_cycles is a process-level counter (reuses _QUOTE_MISS_ESCALATE_CYCLES, no new knob) that alerts once per episode and re-arms on any clean cycle -- corrects 11-08's T-11-46 rationale (OpenDWatchdog only polls get_global_state, cannot see a quote-rights/whole-batch snapshot failure while connected)
- [Phase 11-09]: Phase 11 gap-closure loop CLOSED per operator scope (2026-09-25): CR-03 + WR-10 + WR-11 + IN-08 were the last four findings from 11-REVIEW.md @06b6787; residual T-11-56 (shutdown-order cancel failure) and T-11-57 (late-restart inside the last manage interval) accepted, not fixed
- [Phase 12]: Phase 12-01: HERE/ROOT defined below prelude data marker so exec()'d prelude works without __file__
- [Phase 12]: Phase 12-02: rules_ibs.json sole source of IBS strategy numbers; loader fails closed (unlevered, D-13 path collision, deadline window)
- [Phase 12-03]: ibs_* table names keep equity startup_reconcile blind to IBS DB; active-code partial unique index enforces D-05
- [Phase 12-04]: Parity scenarios use exact-binary prices/IBS so ties are exact; ruling 3 (no same-day re-entry) pinned by a dedicated test
- [Phase 12]: 12-06: reconcile returns broker map; _decide returns (quotes, exited) for Plan 08 reuse
- [Phase 12]: [12-07] Probe opens IBS DB only if it exists; plist KeepAlive SuccessfulExit=false so kill-file exit is not restarted
- [Phase 12-08]: IBS free slots from post-exit active rows; unfilled entry ABORTED (no carry-over); hard-cancel sweeps WORKING orders of any session

### Research Flags (must resolve before planning those phases)

- Phase 2: ~~snapshot batch quota~~ RESOLVED via yfinance (SCAN-06). Remaining (light): S&P 500 constituent source + yfinance batch reliability/rate behavior
- Phase 3: ~~Bar-close detection during subscription reconnect mid-bar~~ RESOLVED via _seen_time_keys dedup + is_first_push mid-bar guard. ~~HOD/premarket-high field~~ RESOLVED — pre_high_price confirmed; LOD = session running-min (Pitfall 3)
- Phase 4: Paper account order flow behavior (push reliability, fill model) — empirical SIMULATE validation needed
- Phase 6: ~~Moomoo historical 5m quota~~ RESOLVED via yfinance/flat-file (BT-04). Remaining (light): yfinance 5m history window (~60d) + whether a Parquet cache is needed

### Pending Todos

None yet.

### Blockers/Concerns

- ✓ [Phase 11] RESOLVED by 260926-kvt: options entry now refuses any underlying where the broker holds a foreign option (2026-09-25 worktree run sold TLT 11/20 75P against the main-repo DB's long 75P). Still run the options bot only from the main repo on develop with its real DB; the 2026-09-25 TLT NEEDS_ATTENTION row in the worktree DB needs manual cleanup.
- ⚠️ [Phase 11] Non-empty equity-watchlist read for `super_bull_call` still unexercised live (worktree run had no equity DB); re-check the 10:05 ET log line after merge + restart from the main repo.
- [Phase 11] Deferred review backlog: WR-02/03/04/08/09, IN-01..IN-13; WR-09 (no command to resolve NEEDS_ATTENTION rows) is the top follow-up.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260702-ick | Shared SIMULATE account isolation: get_external_codes() scan-time exclusion + risk.sizing_equity_usd fixed sizing basis (spec: docs/superpowers/specs/2026-07-02-shared-simulate-account-isolation-design.md) | 2026-07-02 | aed0430 | [260702-ick-implement-shared-simulate-account-isolat](./quick/260702-ick-implement-shared-simulate-account-isolat/) |
| 260817-0ph | Phase 8 options strategy (tasty_credit_spreads) Wave 1: migration 0006, bot/options pure core (schema/config/strategy), rules_options.json, tests (design: ~/.claude/plans/scrape-highly-rated-options-velvety-naur.md; research: docs/research/2026-08-17-tastylive-options-research.md) | 2026-08-17 | 47a47dd | [260817-0ph-phase-8-options-strategy-tasty-credit-sp](./quick/260817-0ph-phase-8-options-strategy-tasty-credit-sp/) |
| 260817-155 | Phase 8 options strategy Wave 2-3: gateway option methods (get_stock_ids/screen_options/get_option_positions), OptionsStore, LegExecutor + tests | 2026-08-17 | 7624ea9 | [260817-155-phase-8-options-strategy-wave-2-3-gatewa](./quick/260817-155-phase-8-options-strategy-wave-2-3-gatewa/) |
| 260817-1ie | Phase 8 options strategy Wave 4: OptionsBot service (entry scan/manage/eod jobs, reconcile, alerts, report), bot/main.py strategy_name dispatch + --rules, tests | 2026-08-17 | 35194ed | [260817-1ie-phase-8-options-strategy-wave-4-optionsb](./quick/260817-1ie-phase-8-options-strategy-wave-4-optionsb/) |
| 260817-ask | screen_options per-underlying requests (escapes moomoo 1000-row/request cap that starved SPY/QQQ/DIA/FXI) + 3.5s inter-call throttle (SDK 10/30s limit), 4 tests, live RTH probe re-verified | 2026-08-17 | f44837e | [260817-ask-fix-screen-options-1000-row-server-cap-s](./quick/260817-ask-fix-screen-options-1000-row-server-cap-s/) |
| 260817-asl | Fix force_close job never armed on mid-session (re)start: _register_jobs reschedules to today's calendar-aware close when started after 08:30 ET; _reschedule_force_close helper; regression test | 2026-08-17 | 8043243 | [260817-asl-fix-force-close-job-never-firing-when-bo](./quick/260817-asl-fix-force-close-job-never-firing-when-bo/) |
| 260819-bjv | Fix gateway _reconcile_core misclassifying options-bot legs as external: skip option codes (via _OPTION_CODE_RE) in orphan-adoption loop — OptionsService.reconcile() is their sole owner; dedupe reconcile_external_position_ignored to once/code/process (was 4 lines every ~75s); 3 regression tests | 2026-08-19 | 6ca0d36 | [260819-bjv-fix-gateway-py-reconcile-core-misclassif](./quick/260819-bjv-fix-gateway-py-reconcile-core-misclassif/) |
| 260824-avx | Fix intraday rescan crash: run_intraday_rescan unconditionally protects managed active codes (open positions/pending intents) from eviction/unsubscribe; gateway.unsubscribe treats "not been subscribed" as benign so cleanup never aborts the rescan or skips premarket-high seeding | 2026-08-24 | b1962ef | [260824-avx-fix-intraday-rescan-crash-protect-manage](./quick/260824-avx-fix-intraday-rescan-crash-protect-manage/) |
| 260827-j29 | Fix opened_at NOT NULL crash in register_position (filled position left unmanaged: no stop/trail/force-close, 2026-08-27 US.CRM) + re-create the one-shot force_close job after it fires (no force-close armed 2026-08-26/27) | 2026-08-27 | 247da3d | [260827-j29-fix-opened-at-not-null-crash-on-position](./quick/260827-j29-fix-opened-at-not-null-crash-on-position/) |
| 260925-goi | Fix CR-04 (phase 11): options fill_leg raises instead of escalating when a TTL cancel_order fails and the order is not fully filled (no second live order, no stale partial qty); _try_open routes any open_position exception to the CR-02 NEEDS_ATTENTION + working-orders alert (EX-03 routing) | 2026-09-25 | 7689f2b | [260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow](./quick/260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow/) |
| 260925-ho6 | Fix equity ExecutionEngine cancel-swallow (CR-04 parity with options 260925-goi): a failed cancel_order at any of 4 sites is re-read; if not filled/terminal the engine audits, retries once, alerts Telegram and raises CancelUnconfirmedError instead of placing the next BUY/SELL; bot.py books known entry shares and leaves no-fill intents PENDING; exits credit a lower bound and an in-memory exit hold blocks further SELLs and re-entries for the code until restart; 13 tests | 2026-09-25 | 3625cf1 | [260925-ho6-fix-equity-engine-cancel-swallow-unconfi](./quick/260925-ho6-fix-equity-engine-cancel-swallow-unconfi/) |
| 260925-inw | Fix equity ExecutionEngine TTL-cancel SUCCESS-path race (ho6 follow-up 1): sites 2 (entry TTL) and 4 (exit TTL) now re-read once after every TTL cancel, not only a failed one -- a partial fill landing between the last poll and a successful cancel (CANCELLED_PART) is returned as a FillEvent (no re-placed full BUY) / credited to total_filled before the next SELL is sized (no over-sell into a short); a failed or empty re-read escalates via _escalate_unconfirmed_cancel (audit + Telegram + exit hold on SELL); +1 order_list_query per TTL expiry; 4 tests | 2026-09-25 | 96a1497 | [260925-inw-fix-equity-engine-ttl-cancel-success-pat](./quick/260925-inw-fix-equity-engine-ttl-cancel-success-pat/) |
| 260926-kvt | Options bot entry guard (SAFE-OG-01, 2026-09-25 TLT net-zero incident): `_try_open` does one fresh `get_option_positions()` read per sized candidate before any DB row/order; skips (`options_entry_foreign_holding`, codes + overlapping leg_codes) if the broker holds ANY non-zero option qty on that underlying (own ACTIVE legs can't be there: `busy` already skipped the underlying); broker read failure fails closed (`options_entry_broker_read_failed`); 5 tests | 2026-09-26 | 6bf02b3 | [260926-kvt-options-bot-block-entry-when-broker-hold](./quick/260926-kvt-options-bot-block-entry-when-broker-hold/) |
| 260927-r53 | Test runs no longer write the production `logs/bot.log` (fake `leg_order_placed` O1/O2/O3 + `options_reconcile_mismatch` P1 seen there 2026-09-26 22:24Z): session autouse `_isolate_bot_log` in tests/conftest.py patches `configure_logging.__defaults__` to a session tmp dir (`bot.main.main()` tests reached the real default `log_dir="logs"`); guard test `test_bare_configure_logging_does_not_target_production_log` (mutation-checked); no bot/ change | 2026-09-28 | 34eb7b5 | [260927-r53-keep-test-runs-from-writing-to-productio](./quick/260927-r53-keep-test-runs-from-writing-to-productio/) |
| 260927-w4r | `TestP1BTradeRecording::test_on_quote_full_fill_closes_and_records_trade` failed when run 20:00-24:00 ET (`closed_at` ET-offset string -> SQLite `DATE()` normalises to next UTC date vs query by ET date); both `_on_quote` tests now freeze `bot.position.manager.now_et` at 2026-06-24 10:06 and query `"2026-06-24"` like siblings (partial-fill `== []` assert was vacuous after 20:00 ET). Red/green proven via scratchpad late-clock plugin; test-only, no bot/ change | 2026-09-28 | ed86bff | [260927-w4r-fix-after-20-00-et-wall-clock-flake-in-t](./quick/260927-w4r-fix-after-20-00-et-wall-clock-flake-in-t/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-10-05T02:50:06.772Z
Stopped at: Phase 11 complete — UAT 5/5 passed (2026-09-26); verified passed; branch not yet merged to develop
Resume file: None
