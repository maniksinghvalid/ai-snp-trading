# External strategy backtest hypotheses (Phase 10, pre-registration)

**Pre-registration statement**

Dated 2026-08-18, before any real-data backtest run of `backtester.experimental.run` has
occurred. Commit discipline proves it: this file and `backtester/experimental/arms.json` are
committed together in ONE commit, before `backtester/results/experimental/` exists. Proof
command:

```
git log --diff-filter=A --format='%h %ad %s' --date=short -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json
```

Confirm the returned commit precedes any commit touching `backtester/results/experimental/`
(that directory is gitignored, so the correct check is that it does not exist on disk at
commit time — see `## Regime series provenance` and the plan's Task 3 verification). The
hypotheses, arms, windows, evidence floor, metric of record and verdict rules below are
frozen before a single real-data number exists to bias them, mirroring the Phase 9 discipline
(`docs/research/2026-08-17-options-backtest-hypotheses.md`) that this project applies to
every backtest-driven decision.

## Sources and credibility

Two Reddit day-trading threads were critically extracted via Apify Reddit scrapers (OP text +
~350 comments with scores each) — reddit.com blocks direct fetch from this environment, so no
live re-fetch is possible or attempted here; the extraction is reproduced from
`~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md` Appendix A, captured
2026-08-18.

**Thread 1** — u/El1teM1ndset, "day trading strategies that actually work (unlike the crap
fake gurus sell you)", r/Daytrading, 2025-02-27, score 1233 (97% upvoted), 173 comments.

- OP disclaimer (explicit, in-post): "EDIT: I do not 'know' all of these work. this is more
  or less an easily accessible list… some I've used myself and some come from books (Market
  Wizards, Al Brooks, Raschke, Hougaard)." OP later refused to say which ones they personally
  trade.
- Zero sizing, stop, target, R:R, win-rate, or sample-size data anywhere in the post.
- Contains unsourced folk statistics: "80% chance it fades" (earnings reversal), "9:45 AM
  reversal" (first 15 minutes = retail noise) — neither has a cited source or backtest.
- Top pushback comment scores: "If you've never used them, how do you know they work?" (36);
  "posting something you haven't got a clue works" (30); title-vs-edit contradiction (8, 2,
  1); "reads like ChatGPT" (3, 3, 2, 2); "if any strategy were effective it could be
  programmed into a bot" (1); "no one talks about sizing/risk because yoloing gets internet
  points" (8).
- Supportive comment-thread rules with scores: EMA 20/50/200 multi-timeframe "trade HTF
  setup, trail while LTF EMAs respected" (20, self-reported 4 months paper-trading);
  premarket-range breakout in first 30 min (8); ORB on NQ — 5m opening high/low, 1m retest
  entry, min 2R (13); "VWAP + 200 EMA, catch the 9:45-10:30 pump" (2).

**Thread 2** — u/Logical_Argument_216, "Consistent trading strategy that has worked for me
and netted $300K+ last year", r/Daytrading, 2025-01-26, score 4413 (97% upvoted), 650
comments.

- 10-day-vs-10-period SMA confusion: OP wrote "10-day SMA" but multiple top comments
  establish community consensus that OP means a **10-period SMA on the 5-minute chart** — OP
  never corrected the ambiguity.
- MACD settings never given by OP; one replier states 12/26/9, another recommends 8/21/5.
- Admitted discretion: "false signals happen all the time… I just close my position"; does
  not take every signal (top comment score 331 asks "which crosses do you take?"; comment
  score 183 posts a chart of failed signals); a weekly P&L target causes the OP to step away,
  which contradicts a take-every-setup expectancy claim (comment score 21).
- No backtest anywhere: "I haven't calculated [win rate/R:R] KPIs."
- Claimed performance: $321,480 Jul 14-Dec 31 2024 **including swing trades** (not pure day
  trades); commenters derive an underlying account of roughly $2.9-3M, implying ~10-11% over
  5.5 months versus SPY's own ~+10% over the same window (2024 full-year SPY +25%). A
  Dec-2025 edit states "gave a lot back in November but overall a great year" with
  screenshots only, no verifiable statement.
- Top pushback comment scores: 10-day-vs-10-period confusion undermining the "15 years'
  experience" claim (181, 42, 33, 18, 12); "$300k = 11%, underperformed buy-and-hold" (178,
  95, 40, 31); "dollars not percent = red flag" (22, 40); "all crossover strategies fail in
  chop; could be coded and would be a losing bot without the discretionary sauce" (33); "I
  backtested it on ES over the last month — remarkable amount of false signals" (14); "no
  losing weeks shown / unverified screenshots" (4, 1); "everyone is a genius in a bull
  market" (3, 1).

**Credibility assessment separating STATED RULES from ASSUMPTIONS/INTERPRETATION:** every
numeric parameter this phase implements that the OP did not explicitly state (MACD settings,
exact SMA period-vs-day reading, notional-sizing percentage, discretionary entry/exit
filtering) is an assumption, not a stated rule — see `## Strategy definitions (as
implemented)` DEVIATIONS-FROM-OP-WORDING subsection below for the explicit list. Neither
thread contains a backtest, a documented win rate, or a documented sample size; both are
treated in this research as unverified anecdote requiring independent backtest evidence
before any weight is given to their claims.

## Universe

Frozen 24-name MEGA24 list, verbatim (backtest/validation-only, never referenced by `bot/`,
frozen 2026-08-12 by the prior TJL audit):

```
US.AAPL,MSFT,NVDA,AMD,AVGO,META,GOOGL,AMZN,NFLX,TSLA,HD,COST,WMT,JPM,GS,BAC,XOM,CVX,CAT,DE,BA,UNH,LLY,JNJ
```

Declared biases: survivorship (today's mega-caps, not historically-representative
constituents); tech tilt (10 of 24 names); no ETFs (SPY/QQQ excluded from the trading
universe — SPY is used only as the read-only regime input, see below).

## Windows

- **A** 2025-05-01 -> 2025-07-31
- **B** 2025-04-01 -> 2025-04-30
- **C** 2024-09-02 -> 2024-12-31 (never 2024-09-03 — the cache key is start minus 30 calendar
  days = 2024-08-03)
- **D** 2025-08-01 -> 2026-07-31
- **E** 2023-07-03 -> 2024-08-30

**IS = E union C. OOS = B union A union D.** Jan-Mar 2025 is explicitly uncovered by either
IS or OOS — stated up front, not discovered mid-analysis.

Nine regime slices for tables/charts only (date-sliced from already-generated trade logs, no
re-runs):

- E1 2023-07-03 -> 2023-11-30
- E2 2023-12-01 -> 2024-04-30
- E3 2024-05-01 -> 2024-08-30
- C (as above)
- B (as above)
- A (as above)
- D1 2025-08-01 -> 2025-11-28
- D2 2025-12-01 -> 2026-03-31
- D3 2026-04-01 -> 2026-07-31

TJL comparator for H7 uses the full-universe (not MEGA24) evidence: E1-E3 as IS, D1-D3 as
OOS, matching the existing `backtester/results/strategy-audit-validation/fulluniverse/`
baseline.

## Costs

- **Base** (metric of record): $0.005/share commission on both entry and exit + $0.03/share
  adverse slippage.
- **Stress** (sensitivity only): $0.05/share adverse slippage.
- **Zero-cost**: upper-bound diagnostic only, never used for a verdict.

## Metric of record

**Profit factor (PF), net, at base cost, is primary.** Sortino ratio is secondary and only
overrides the PF ranking when the two arms' PFs are within 5% of each other. A bootstrap 95%
confidence interval on mean R is the final tie-break when PF and Sortino both remain
ambiguous. The simplest passing configuration wins ties.

Every arm x slice/window reports: total return, CAGR, max drawdown, Sharpe, Sortino, win
rate, profit factor, number of trades, average trade $, average win/loss, expectancy in R,
exposure, final equity, max consecutive losses, average hold time in minutes, and a
per-side (long/short) split.

## Evidence floor

**>=25 closed trades per arm, in IS and in OOS separately.** An arm/hypothesis with fewer
than 25 closed trades in either window is reported INSUFFICIENT-EVIDENCE for that window,
regardless of what the raw PF number happens to say. This floor is never loosened and is
never a reason to narrow the universe — an underpowered result is reported honestly as
underpowered, not worked around.

## Verdict rules

- **SUPPORTED** — the hypothesis's a-priori prediction holds in BOTH the IS window and the
  OOS window, with the evidence floor met in both.
- **REJECTED** — the evidence floor is met in both windows, but the prediction fails in
  either IS or OOS.
- **INSUFFICIENT-EVIDENCE** — the evidence floor is not met in one or both windows; no
  verdict is drawn from the raw numbers even if they are shown for transparency.

**Production-candidate bar (H8 only):** PF >= 1.3 in both IS and OOS, Sortino > 1, max
drawdown <= 10%, IS/OOS PF within +/-40% of each other, PF >= 1.15 at stress cost, no sign
flip in PF when any single sensitivity knob moves one step, and >= 2 entries per month.

## Hypotheses

Reproduced verbatim from the approved design
(`~/.claude/plans/analyze-and-improve-autotrader-cosmic-clover.md` "Pre-registered protocol"
section) including each parenthesised a-priori prediction. Not renumbered, not softened.

### H1

Ext#2 as-is has edge (PF>1 both; predicted REJECTED).

### H2

AutoTrader `partial_be_trail` beats Ext#2 %-ladder on the same entries (predicted: R-based
>= ladder).

### H3

Weekly SPY regime gate improves Ext#2 (predicted: improves, still <1.3).

### H4

ORB-30+HTF+VWAP has edge (predicted: INSUFFICIENT/REJECTED, maybe positive in trending
slices).

### H5

Fixed-2R vs partial_be_trail for ORB.

### H6

VWAP pullback has edge (predicted REJECTED).

### H7

Regime gate improves TJL (predicted INSUFFICIENT — trade count).

### H8

Combined arm clears the production bar (predicted: no -> recommendation "no production
change; keep TJL parked; continue options path" unless data says otherwise).

**Not hypotheses:** the sensitivity/robustness diagnostics — confirm-bars N, 09:45 entry
start, stop basis, sizing (risk1pct vs notional10), opening-range minutes, intrabar vs
close-only stop fills, cost profile (base/stress/zero), and the uncapped diagnostic arm — are
robustness checks reported alongside the hypotheses, not separate falsifiable predictions.

## Arms

Reproduced verbatim from `backtester/experimental/arms.json` (the frozen, machine-readable
source of truth `run.py` consumes):

| Name | Strategy | Overrides (vs `defaults.<strategy>`) | Purpose |
|---|---|---|---|
| `ext2_base` | ext2 | none | Ext#2 (SMA10+MACD) as-implemented — H1 baseline |
| `ext2_n3` | ext2 | `confirm_bars=3` | Tighter cross-confirmation window sensitivity |
| `ext2_n12` | ext2 | `confirm_bars=12` | Looser cross-confirmation window sensitivity |
| `ext2_w0945` | ext2 | `entry_start=09:45` | OP's "9:45 rule" entry-window-start sensitivity |
| `ext2_stop_pd` | ext2 | `stop_basis=prev_day` | Stop-basis sensitivity: prior-day low/high |
| `ext2_stop_atr2` | ext2 | `stop_basis=atr2` | Stop-basis sensitivity: close +/- 2*ATR14 |
| `ext2_notional10` | ext2 | `sizing=notional10` | OP's stated notional sizing style vs risk1pct |
| `ext2_at_exit` | ext2 | `exit_model=partial_be_trail` | H2 — AutoTrader exit vs Ext#2 pct_ladder |
| `ext2_regime` | ext2 | `regime_gate=weekly_spy` | H3 — weekly SPY regime gate on Ext#2 entries |
| `ext2_uncapped` | ext2 | `max_concurrent=null, max_entries_per_day=null` (diagnostic) | Quantifies the 5/day, 5-concurrent cap priority artefact — not a hypothesis |
| `orb30_base` | orb | none | H4 — ORB-30 + HTF-EMA100 + VWAP trend core |
| `orb30_fixed2r` | orb | `exit_model=fixed_2r` | H5 — fixed-2R exit vs partial_be_trail |
| `orb5` | orb | `or_bars=1` | Opening-range-length sensitivity: 5-minute OR |
| `orb15` | orb | `or_bars=3` | Opening-range-length sensitivity: 15-minute OR |
| `vwap_pb_base` | vwap_pb | none | H6 — VWAP pullback-bounce entry |

**Pre-registered `combo` rule:** a 16th arm named `combo` may be appended to `arms.json` in
Wave 4, in its own separate commit, if and only if an entry family (ext2, orb, or vwap_pb)
reaches PF > 1 in IS with >= 25 trades. It is not included in this pre-registration because
whether it exists at all is itself data-dependent — deciding it before any run would violate
the falsifiability discipline this document exists to enforce.

## Strategy definitions (as implemented)

Bars are labelled by their START time; a signal is decided on bar CLOSE at time t; the fill
happens at bar t+1's OPEN price plus/minus adverse slippage.

**ext2** (SMA10 + MACD, "Ext#2"): SMA(10) and MACD(12,26,9) computed on the continuous padded
5-minute close series (warm-up approximately 21 sessions). Long entry at bar t when
`close > sma AND close[t-1] <= sma[t-1] AND macd > signal AND bars_since(macd_cross_up) <= N`
OR `macd crosses up at t AND bars_since(sma_cross_up) <= N` (N = `confirm_bars`, base 6).
Short is the exact mirror. Entry window 09:35-15:30 ET (sensitivity: 09:45 start). Initial
stop = session low-of-day (longs) / high-of-day (shorts) including the signal bar
(sensitivity: prior-day low/high; sensitivity: close minus/plus 2*ATR14). Exit model
`pct_ladder` by default: scale out 25% at each of +1%/+2%/+3%, move to breakeven after the
first take, runner exits at stop or force-close (sensitivity `ext2_at_exit`: AutoTrader's
`partial_be_trail`). Sizing `risk1pct` by default (sensitivity `notional10`: OP's stated
notional style). Unlimited entries per symbol per day; `ext2_uncapped` additionally lifts the
5-per-day and 5-concurrent portfolio caps as a diagnostic-only arm.

**orb** (Ext#1 trend core, "ORB-30"): opening range = the first 6 bars of the session
(09:30-09:55 ET; sensitivity: 1 or 3 bars = 5- or 15-minute OR). Entries allowed
10:00 <= entry time < 12:00 ET. Long entry requires close beyond the opening-range high AND
close above session VWAP AND above the higher-timeframe bias (last completed 1-hour close
above its 100-period EMA on hourly closes). Short is the mirror. Entries are skipped when the
opening-range height exceeds 3% of close (`max_or_height_pct`). Initial stop = opening-range
low/high. One entry per symbol per day; `trend_lock` true (the day's first entry side locks
that symbol for the rest of the day). Exit model `partial_be_trail` by default (sensitivity
`orb30_fixed2r`: breakeven at 1R, full exit at 2R, no trailing).

**vwap_pb** (Ext#1 VWAP pullback, "VWAP bounce"): requires the higher-timeframe bull bias
(as in `orb`) AND a confirmed first-hour uptrend (session high through 09:59 exceeds the
opening-range high). Entry window 10:00-15:00 ET. Long entry on a bar where
`low <= vwap <= close AND close > open AND volume >= 1.2x the trailing 20-bar average volume`
(a green, VWAP-reclaiming bar on above-average volume). Short is the mirror. Initial stop =
`min(low, vwap) * 0.999` for longs, `max(high, vwap) * 1.001` for shorts. Two entries per
symbol per day. Exit model `partial_be_trail`.

**DEVIATIONS-FROM-OP-WORDING** (explicit, so a skeptical reader can see exactly where
interpretation replaced a stated rule):

- **MACD settings assumed 12/26/9** — the OP in Thread 2 never stated MACD parameters; one
  commenter suggested 12/26/9 (the industry-standard default), another suggested 8/21/5. This
  research implements the industry-standard 12/26/9 and treats the alternative as untested.
- **"10-day SMA" implemented as a 10-period SMA on the 5-minute chart**, per the community
  consensus in Thread 2's top comments — the OP never corrected this reading despite direct
  challenges.
- **Discretionary filters are not automatable and are omitted**: "which crosses to take"
  (OP admits not taking every signal), closing a position early "on feel," and the weekly
  P&L stop that causes the OP to step away from trading — none of these have a stateable
  mechanical rule and are not implemented anywhere in `ext2`/`orb`/`vwap_pb`.
- **`notional10` sizing implemented as `qty = floor(10000 / close)`** with no risk-based cap
  — this scales the OP's stated 1-2%-of-notional sizing style to this project's $100,000
  paper-equity basis ($10,000 = 10% of $100k notional per position, matching the existing
  `bot/risk/risk_engine.py` notional cap constant already used elsewhere in this project).
- **`regime_gate=weekly_spy` implemented as**: a bear week blocks new LONG entries, a bull
  week blocks new SHORT entries, a neutral week halves the computed quantity (floor, skipped
  entirely if the halved quantity rounds to < 1).
- **`trend_lock` implemented as**: the first entry's side locks that symbol's side for the
  remainder of the trading day (no flip-flopping long/short on the same symbol within a
  session).

## Known limitations

- Survivorship bias in the frozen 24-name MEGA24 universe (today's mega-caps, not
  point-in-time constituents).
- The 5-trades-per-day and 5-concurrent-position caps are applied in strict time-then-
  alphabetical priority order, which is an artefact of implementation, not a strategy
  property; `ext2_uncapped` exists specifically to quantify how much edge (or lack thereof)
  is hidden or exaggerated by this priority ordering.
- No premarket data in the experimental engine — unlike the production TJL scanner, none of
  `ext2`/`orb`/`vwap_pb` consider premarket price action.
- No borrow/locate cost modeled on short positions.
- Non-compounding $100,000 sizing basis for every trade, matching the production risk engine
  convention but not a compounding equity curve.
- Commissions are applied at the report layer only (post-hoc, on the trade log), not as a
  live per-fill deduction — matches the existing backtester convention project-wide.
- Force-close-bar convention: a position still open at the force-close time is filled at that
  bar's close price (15:50 bar on a normal day, 12:50 bar on a half day), not at the exact
  force-close timestamp.
- Stops are evaluated on bar CLOSE by default (production parity), meaning a single bar can
  close beyond the stop level and the realized loss on that trade can exceed 1R;
  `--stop-fill intrabar` is available as a sensitivity mode only, not the metric of record.
- Windows leave 2025-01-01 through 2025-03-31 uncovered by either IS or OOS — stated
  explicitly, not silently absorbed into either bucket.

## What would change rules.json

**Zero changes to `rules.json` or `rules_options.json` occur during this phase, regardless
of any hypothesis's verdict.** If a hypothesis is SUPPORTED in both IS and OOS, the specific
change it would justify is:

- **H2 SUPPORTED** would justify replacing `exit.model` semantics or adding an alternative
  exit model to `_IMPLEMENTED_EXIT_MODELS` (currently only the production `partial_be_trail`-
  style FSM exists; Ext#2's `pct_ladder` would need to be added and schema-validated first).
- **H3 or H7 SUPPORTED** would justify adding a new top-level `regime_gate` key to
  `rules.json` (analogous to how `regime_gate` already exists as an experimental-arm
  parameter here), consumed by `run_daily_scan` as an additional day-level gate.
- **H4/H5/H6 SUPPORTED** would justify a new `strategy_name` value (e.g. `"orb30"` or
  `"vwap_pb"`) dispatched in `bot/main.py`, alongside a new `bot/strategy/<name>.py`
  `StrategyCore` subclass — a materially larger change than a config-value edit, requiring
  its own plan and its own pre-registration-style validation before touching `develop`.
- **H8 SUPPORTED** (the production-candidate bar) would justify the Wave-5 conditional
  productionization sketch: a default-off, schema-valid feature branch off `develop`
  (`feature/phase10-<arm>`), never auto-merged, described in the results doc regardless of
  outcome so the operator can see exactly what a future integration would touch.

Any such change is a separate, operator-approved follow-up action outside this phase's
deliverable.

## Regime series provenance

The SPY weekly-regime input series is the phase's only network fetch. Fetched via
`bot.scanner.fetcher._download_batch` (the project's shared yfinance batch kernel, WR-02 —
never a raw `yf.download` call) followed by `get_ticker_frame(data, "SPY")` to normalize
columns to lowercase `open/high/low/close/volume`, then written to CSV with
`frame.to_csv(...)`, matching `backtester/feed.py::_load_5m`'s cache-write convention. Zero
Massive API requests were made — this fetch does not touch or contend with the Phase 9
`warm-cache-pool` process's 5 req/min budget.

- **Source:** yfinance, via `bot.scanner.fetcher._download_batch`
- **Requested range:** 2022-01-01 -> 2026-08-18, interval `1d`, `prepost=False`
- **Actual first index date:** 2022-01-03
- **Actual last index date:** 2026-08-17
- **Row count:** 1,159
- **File:** `backtester/cache/SPY_1d_regime.csv` (gitignored — `backtester/cache/` is a local
  data artifact per `.gitignore`; this section is the only durable, committed record making
  the regime input reproducible and auditable)
- **sha256:** `45873a7227101ed15224ae20c89a2fa90bac873df6f73d9256b7d911c06fc38e`
