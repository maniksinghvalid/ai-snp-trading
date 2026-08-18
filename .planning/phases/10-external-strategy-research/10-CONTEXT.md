# Phase 10: External Strategy Research - Context

**Gathered:** 2026-08-18
**Status:** Ready for planning
**Source:** PRD Express Path (~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md — brainstorming-approved design, operator-approved 2026-08-18)

<domain>
## Phase Boundary

This phase delivers a **backtest-only research package** (`backtester/experimental/`) that:
1. Extracts and critically assesses two Reddit day-trading strategies (already done — see plan Appendix A).
2. Implements their automatable cores plus TJL as pluggable, tested backtest modules.
3. Pre-registers hypotheses before running any real-data backtest.
4. Runs cache-only, cost-realistic backtests across the 5 already-cached windows (A–E) and 9 regime slices on the frozen 24-symbol universe.
5. Produces a 14-section report with a SUPPORTED/REJECTED/INSUFFICIENT-EVIDENCE verdict per hypothesis.
6. If (and only if) a hypothesis is SUPPORTED in both IS and OOS, sketches/implements the production integration on a **separate, unmerged feature branch** — `develop`, `rules.json`, and `rules_options.json` are NOT touched by this phase's own deliverable.

Out of scope for this phase: refactoring `SignalEngine`/FSM for pluggable strategies in production; a live short side; new data providers; touching the Phase 9 options warm-cache run; any merge to `develop`.

</domain>

<decisions>
## Implementation Decisions

### Universe & data (locked)
- Test universe: the frozen 24-symbol list `US.AAPL,MSFT,NVDA,AMD,AVGO,META,GOOGL,AMZN,NFLX,TSLA,HD,COST,WMT,JPM,GS,BAC,XOM,CVX,CAT,DE,BA,UNH,LLY,JNJ` (frozen 2026-08-12 by the prior TJL audit, validation-only — never referenced by `bot/`). Operator confirmed 2026-08-18 after asking whether these symbols would be hardcoded in production code; answer: no, they exist only as the `MEGA24` default of `backtester/experimental/run.py --symbols` and in the pre-registration doc.
- Windows (exact cache-keyed strings, do not deviate): A 2025-05-01→2025-07-31, B 2025-04-01→2025-04-30, C **2024-09-02**→2024-12-31 (not 09-03 — the cache key is start−30d = 2024-08-03; verify before any run), D 2025-08-01→2026-07-31, E 2023-07-03→2024-08-30.
- IS = E ∪ C; OOS = B ∪ A ∪ D. 9 regime slices for tables/charts only (date-sliced from trades, no re-runs): E1/E2/E3/C/B/A/D1/D2/D3 (exact sub-ranges in the plan file §Pre-registered protocol).
- Data policy (operator decision): SPY daily regime series via **yfinance** (zero Massive requests), cached to `backtester/cache/SPY_1d_regime.csv`. Zero new Massive requests anywhere in this phase — the Phase 9 `warm-cache-pool` background process (pid file `backtester/results/options/warm-cache-pool.pid`) must remain undisturbed.
- Costs: base $0.005/share commission (both sides) + $0.03/share adverse slippage; stress $0.05 slippage; zero-cost = upper-bound diagnostic only.

### Reuse (do not reinvent)
- `backtester/feed.py::SimulatedBarFeed` for all bar data (exact cache keys, `_massive_tod` padded 30-day warm-up frame for indicator seeding, `_bars_by_code` in-window bars with session hod/lod/cum_volume, `next_bar()` for N+1-open fills).
- `backtester/report.py::compute_metrics/build_equity_curve/write_report` for all metrics and CSV/JSON output (same 8-column `trades.csv` schema + extra columns `side,strategy,arm,n_legs,regime`).
- `bot/strategy/indicators.py::swing_low_2_2` (and its mirror `swing_high_2_2 = -swing_low_2_2(-highs)`) for the AutoTrader-parity exit model.
- `bot/position/manager.py::get_force_close_time_et` for half-day-aware force-close times (15:51 / 12:51).
- `backtester/run.py::_trading_days` for the NYSE calendar iteration.
- TJL baseline = existing results under `backtester/results/strategy-audit-validation/fulluniverse/{E1,E2,E3,D1,D2,D3}/base` (the 226-trade evidence of record) and `window{A..E}/base` (sparse, 24-name, descriptive only). Do NOT re-run TJL through the harness — "TJL + weekly regime gate" is computed as an exact day-filter of the existing `trades.csv` (TJL carries no cross-day state), zero harness changes required for the result; an optional 4-line `day_gate` seam in `BacktestHarness.setup_day` is allowed only as a parity check, not as the primary evidence path.

### Production code boundary (hard constraint)
- `bot/` package: **zero changes**.
- `rules.json` / `rules_options.json`: **zero changes** during this phase, regardless of verdict.
- `backtester/report.py`: exactly one small change allowed — `_net_pnl` gets a side-aware sign (`sign = -1 if trade.get("side") == "short" else 1`, backward compatible) and `write_report` gains an optional `extra_fields` parameter. This is the only edit to existing backtester code; everything else new lives under `backtester/experimental/`.
- `backtester/harness.py`: the optional `day_gate` seam (4 lines) is the only other allowed edit to existing code, and only if a plan chooses to add it as a parity check.

### Engine semantics (locked — see plan file §Wave 2 for full algorithm)
- Signal decided on bar CLOSE at time t; fill at bar t+1 OPEN ± adverse slippage (long: entry+slip/exit−slip; short: entry−slip/exit+slip) — same N+1 convention as production.
- Stops evaluated on bar CLOSE by default (production parity); `--stop-fill intrabar` is a sensitivity mode only (fills at the stop price when bar low/high crosses it, stop wins ties).
- Position sizing: `qty = min(floor(1000/|close−stop|), floor(10000/close))` from the **signal bar's close** (not the fill price) at $100k non-compounding basis; skip if qty < 1 (parity with `bot/risk/risk_engine.py`).
- Risk caps: ≤5 concurrent positions, ≤5 new entries/day, −$2,000 realised-gross daily breaker (blocks new entries only, does not force-close), one position per symbol, no re-entry on the bar a position just closed.
- Force-flat: at the bar containing 15:51 ET (12:51 on half-days via `get_force_close_time_et`), fill at that bar's close ∓ slip.
- Trade row convention (must match `backtester/harness.py:400-416` exactly): one row per position; `exit_price` = qty-weighted blended exit across all legs; `quantity` = full entry quantity; `r_multiple = sign · (exit_price − entry_price) / |entry_price − initial_stop|` where `sign = +1` long / `-1` short; extra columns `side, strategy, arm, n_legs, regime`.
- Long AND short both supported (a first for this backtester — production is long-only).

### Strategy definitions (locked — exact rules in the plan file; do not reinterpret)
- **ext2** (Ext#2 / SMA10+MACD): SMA(10) + MACD(12,26,9) on the continuous padded 5m close series; cross-confirmation within N bars (base N=6); window 09:35–15:30 (sens 09:45); stop = session LOD/HOD (sens prev-day low/high, 2×ATR14); exit `pct_ladder` (25% at +1/+2/+3%, BE after first take) by default, AutoTrader `partial_be_trail` as the `ext2_at_exit` arm; sizing risk1pct (sens notional10 = OP's stated style).
- **orb** (Ext#1 trend core / ORB30): opening range = first 6 bars (09:30–09:55, sens 1/3 bars); entries 10:00–12:00; requires close beyond the OR AND above/below session VWAP AND 1H-EMA100 HTF bias; stop = OR low/high; 1 entry/symbol/day; exit `partial_be_trail` (sens `fixed_2r`).
- **vwap_pb** (Ext#1 VWAP pullback): HTF bull + confirmed first-hour trend up, entry on a VWAP-touching green reclaim bar with volume ≥1.2× 20-bar average; stop = min(low, vwap)×0.999; 2 entries/symbol/day; exit `partial_be_trail`.
- Frozen arm list lives in `backtester/experimental/arms.json` (Wave 1 deliverable) — see plan file for the full 15-arm base list plus the conditional `combo` arm.

### Pre-registration discipline (hard constraint, mirrors Phase 9 OBT-06)
- `docs/research/2026-08-18-external-strategies-hypotheses.md` + `backtester/experimental/arms.json` must be **committed before any real-data run** exists under `backtester/results/experimental/`. Verification command: `git log --diff-filter=A --format=%H%x20%ad -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json` must show a commit that precedes any run artifact.
- Evidence floor: ≥25 closed trades per arm, per window, in IS and OOS **separately** — never loosened, never a reason to narrow the universe.
- Metric of record: PF (net, base cost) primary; Sortino secondary (only overrides when PFs are within 5% of each other); bootstrap 95% CI on mean R as the final tie-break.
- Verdict: SUPPORTED only if the prediction holds in **both** IS and OOS with the floor met in both.
- Hypotheses H1–H8 are fixed in the plan file's "Pre-registered protocol" section — the planner/executor must reproduce them verbatim into the hypotheses doc, not invent new ones.

### Claude's Discretion
- Exact internal module boundaries within `backtester/experimental/` (the plan file's file-by-file breakdown is a strong recommendation, not a contract) as long as the package stays self-contained and importable independent of `bot/`.
- Exact SVG chart styling (no-dep hand-rolled SVG is locked; visual polish is at the executor's discretion).
- Whether to implement the optional `day_gate` harness seam at all (the exact day-filter approach is sufficient and is the primary evidence path either way).
- Wave numbering/plan-file splitting as long as the dependency order (pre-registration → engine+tests → runs → report → conditional branch) is preserved.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Design & protocol
- `~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md` — the full approved design: pre-registered protocol, per-file module breakdown with LOC estimates, exact per-bar engine algorithm, run matrix/commands, test list, risks/pitfalls, and the two full Reddit-thread extractions (Appendix A) needed for the report's strategy-breakdown section
- `docs/research/2026-08-17-options-backtest-hypotheses.md` — the Phase 9 pre-registration template this phase's hypotheses doc must match in shape and rigor (evidence floor, verdict rules, "what would change rules.json" section)
- `docs/research/2026-08-17-options-backtest-results.md` — the results-doc shape/tone to match (INSUFFICIENT-EVIDENCE handling, root-cause transparency)

### Backtester code to reuse (read before writing new code)
- `backtester/feed.py` — `SimulatedBarFeed`, cache key construction, `_MASSIVE_TOD_PAD_DAYS`/`_MASSIVE_DAILY_PAD_DAYS` constants, `next_bar`
- `backtester/report.py` — `compute_metrics`, `build_equity_curve`, `write_report`, `_CSV_FIELDS`, `_net_pnl` (the function to patch)
- `backtester/harness.py:400-416` — the exact trade-row-on-close convention (blended exit_price, r_multiple formula) to reproduce in the new engine
- `backtester/execution.py` — `SimulatedExecution` for the N+1-fill/slippage-sign convention to mirror
- `backtester/massive.py` — `MassiveDataSource.cached_bars` cache-key format (must match exactly for cache-only runs to hit)
- `backtester/run.py` — `_trading_days`, CLI patterns to follow for `backtester/experimental/run.py`
- `bot/risk/risk_engine.py:105-158` — the sizing formula to mirror
- `bot/position/state.py` — `partial_be_trail` exit-model semantics (⅓ at 0.75R, BE at 1.0R, swing-low trail) to reproduce as one of the new exit models
- `bot/strategy/indicators.py` — `swing_low_2_2`
- `bot/position/manager.py` — `get_force_close_time_et`
- `tests/backtester/fixtures.py` — `make_ahead_only_5m_dataset`, `make_trade_log`, `recent_session_days` (reuse, don't reinvent, for the new test suite)
- `tests/backtester/test_execution.py:36` — `_FakeFeed` pattern to reuse for engine tests
- `backtester/results/strategy-audit-validation/fulluniverse/{E1,E2,E3,D1,D2,D3}/base/{trades.csv,summary.json}` — the TJL baseline evidence this phase compares against (read, never regenerate unless explicitly re-verifying)

### Prior evidence / falsification context
- `docs/2026-07-03-strategy-analysis-trend-join-long.md` — the original TJL design review (chase-by-construction, no kill switch — both later fixed) referenced in the report's "current strategy analysis" section
- `.planning/PROJECT.md:165`, `.planning/ROADMAP.md` Phase 8 block — the exact TJL no-edge verdict numbers (226 trades, −0.019R, PF 0.50–1.32 across windows) to cite verbatim in the report

</canonical_refs>

<specifics>
## Specific Ideas

- Reddit source threads (already fully extracted in the design doc, Appendix A — do not re-fetch, reddit.com blocks direct fetch from this environment; retrieval was done via Apify Reddit scrapers):
  - Thread 1: u/El1teM1ndset, "day trading strategies that actually work (unlike the crap fake gurus sell you)", r/Daytrading, 2025-02-27
  - Thread 2: u/Logical_Argument_216, "Consistent trading strategy that has worked for me and netted $300K+ last year", r/Daytrading, 2025-01-26
- The 1H-EMA100 HTF filter is explicitly noted as redundant for TJL (D2 SMA200 + ≥3% gap-up + PMH/HOD breakout already implies it) — the report must state this in one line rather than testing it as a TJL variant.
- Sensitivity/robustness diagnostics (not separate hypotheses): entry-window start (09:35 vs 09:45), stop basis (session LOD/HOD vs prior-day vs ATR), sizing (risk1pct vs notional10), OR window (5/15/30 min), stop-fill mode (close vs intrabar), cost profile (base/stress/zero), an "uncapped" diagnostic arm (`ext2_uncapped`) that lifts the 5/day and 5-concurrent caps to show the cap's priority artefact effect.
- Walk-forward is done by aggregation only (pick best-by-PF sensitivity variant on slice k, report its performance on slice k+1) — no additional backtest runs required for this.

</specifics>

<deferred>
## Deferred Ideas

- Broader universe (S&P 100+, or 24+SPY/QQQ) — deferred per operator decision (kept the frozen 24-name universe); would require new Massive fetches and a paused warm-cache job.
- Live short-side production capability — explicitly out of scope; this phase only proves/disproves backtest edge.
- Any merge of a Wave-5 integration branch to `develop` — that is a separate, future, operator-approved action, not part of this phase's deliverable.
- Paid Massive tier upgrade — noted as an option in the design doc but not pursued this phase (data policy = yfinance + existing cache only).

</deferred>

---

*Phase: 10-external-strategy-research*
*Context gathered: 2026-08-18 via PRD Express Path*
