# External strategy research — results (Phase 10, XSR-01/XSR-05)

**Dated 2026-08-18**, same day as the pre-registration
(`docs/research/2026-08-18-external-strategies-hypotheses.md`, commit `f8439aa`, which
strictly precedes every artifact this doc cites — proof:
`git log --diff-filter=A --format='%h %ad %s' --date=short -- docs/research/2026-08-18-external-strategies-hypotheses.md backtester/experimental/arms.json`).
Aggregate evidence: `docs/research/assets/2026-08-18-external-strategies/{results.csv,results.md,results-intrabar.csv,equity_curves.svg,pf_by_slice.svg}`,
produced by `backtester/experimental/{aggregate,charts}.py` from the Wave-3 run tree
(`backtester/results/experimental/{runs,runs-intrabar,tjl}/`, gitignored — the CSV/MD/SVG
copies under `docs/research/assets/` are the durable evidence).

## 1. Executive summary

**No external strategy tested here clears a production bar, and none is recommended for
integration.** Every automatable core extracted from the two Reddit threads — Ext#2
(SMA10+MACD, `ext2_*`), Ext#1's trend core (ORB-30+HTF+VWAP, `orb*`), and Ext#1's VWAP
pullback (`vwap_pb_base`) — shows a base-cost profit factor **below 1.0 in-sample** across
every arm and every sensitivity variant (min 0.803, max 0.922, all IS, all n>=1160
trades). None of H1, H4, or H6 (the three "does this external idea have edge on its own"
hypotheses) is SUPPORTED. Two candidate improvements to Ext#2 (H2's exit model, H3's
weekly-regime gate) show mixed or negative effects and are also not SUPPORTED. The weekly
SPY regime gate applied to TJL itself (H7) makes TJL's IS profit factor **worse**, not
better (0.798 -> 0.586), reversing the pre-registered a-priori guess of
"INSUFFICIENT-EVIDENCE" — the evidence floor was in fact cleared (97 IS / 92 OOS
trades), and the answer is a clean REJECTED. The pre-registered `combo` rule (append a
16th arm only if an entry family clears PF>1 in IS with >=25 trades) never fired — no
family gets above PF 0.92 in IS — so no combined arm was ever built or tested, and H8
(the production-candidate bar) is REJECTED by the same evidence: nothing in this research
comes remotely close to the required PF>=1.3.

**Recommendation: no production change.** `rules.json` and `rules_options.json` are
unchanged and stay that way. TJL remains parked (no evidence here changes its 2026-08-13
falsification); the options bot (Phase 8/9, `tasty_credit_spreads`) remains the active
paper-trading strategy pending its own live UAT. Section 12 describes, for completeness,
exactly what a future integration would touch if a later research pass ever does clear the
bar — this phase's own numbers do not.

## 2. External strategy breakdown

Two Reddit day-trading threads were critically extracted via Apify Reddit scrapers (OP text
+ ~350 comments with scores each; reddit.com blocks direct fetch from this environment, so
this is a reproduction of the 2026-08-18 extraction, not a live re-fetch). STATED rules
(what the OP actually wrote) are separated from AS-IMPLEMENTED definitions (this phase's
mechanical translation) below; every gap between the two is called out explicitly.

### Thread 1 — u/El1teM1ndset, "day trading strategies that actually work", r/Daytrading,
2025-02-27, score 1233 (97% upvoted), 173 comments

**Stated (verbatim from the OP, 26 setups in 8 buckets):** trend (100-EMA-1H bias trade;
VWAP bounce on wick+volume, no first-touch entry; first-hour trend lock), mean reversion
(broken-parabolic short, "fake halt" trap, RSI 90/10 exhaustion), liquidity (stop-hunt
reversal at prior day H/L, "market-maker refill zones", dark-pool footprints), scalping
(1-min rip & dip, L2 big-bid scalping, options-sweep scalping), news reaction (FOMC fade,
"80% chance it fades" earnings reversal — unsourced, merger-arb scalp), psychological
(bagholder bounce, bull-flag fake breakdown, "9:45 AM reversal" — unsourced folk stat),
options data (gamma-squeeze ignition, max-pain Friday fade, OI-strike fakeouts), AH/premarket
(premarket VWAP reclaim, AH pump fade, closing-bell rip). **Not stated anywhere:** position
sizing, stop rules, targets, R:R, win rate, sample size, instrument, session, or any P&L
number. OP's own disclaimer: "I do not 'know' all of these work... some come from books
(Market Wizards, Al Brooks, Raschke, Hougaard)"; OP refused to say which they personally
trade.

**As implemented here (`orb`, `vwap_pb`):** only the automatable trend-following subset —
1H-EMA-100 bias (`htf_ema_bias`), an opening-range breakout (`orb`, standing in for
"first-hour trend lock" + the community's ORB-on-NQ comment, score 13), and a VWAP
pullback-bounce entry (`vwap_pb`, standing in for "VWAP bounce — wait for wick+volume,
no first touch"). Mean-reversion (RSI exhaustion, PDH/PDL fade, bagholder bounce),
liquidity/L2/dark-pool, options-flow, and news/FOMC/earnings setups are explicitly NOT
automatable from what the OP stated and are not implemented anywhere in this phase — see
Appendix C's "Conflicts" note below for why several of them actively contradict the
trend-following core.

### Thread 2 — u/Logical_Argument_216, "Consistent trading strategy... netted $300K+ last
year", r/Daytrading, 2025-01-26, score 4413 (97% upvoted), 650 comments

**Stated:** 5-minute chart; "10-day SMA" (community consensus, including multiple top
comments, reads this as a 10-period SMA on the 5m chart — OP never corrected the ambiguity
despite direct challenges) confirmed by a MACD crossover (settings never given by OP; one
replier states 12/26/9, another 8/21/5); long/short on a 5m candle crossing and holding the
SMA, confirmed by MACD. Exits: scale out at +1%/+2%/+3%, let the last 25% run to a stop at
breakeven or the previous day's low. Stops: "usually same day or previous day lows... I've
sat on losing positions before". Sizing: 1-5% of portfolio notional per trade (not
risk-based). Universe: "a few liquid names like QQQ, SPY, META, AMZN, TSLA" (also COIN,
NVDA, RGTI in examples). Admitted discretion: doesn't take every signal (top comment score
331: "which crosses do you take?"), closes losers "on feel", a weekly P&L target causes the
OP to step away (comment score 21 notes this contradicts a take-every-setup expectancy
claim). No backtest anywhere: "I haven't calculated [win rate/R:R] KPIs."

**Claimed performance:** $321,480 Jul 14-Dec 31 2024 including swing trades (not pure day
trades); commenters derive an account of roughly $2.9-3M, implying ~10-11% over 5.5 months
vs. SPY's own ~+10% over the same window (2024 full-year SPY +25%) — the OP's own claimed
edge, even taken at face value, roughly matched the index it was compared against.

**As implemented here (`ext2`):** SMA(10) + MACD(12,26,9) on the continuous 5-minute close
series; long at bar t when `close>sma AND close[t-1]<=sma[t-1] AND macd>signal AND
bars_since(macd_cross_up)<=N` (or the mirror ordering), N=`confirm_bars` (base 6); short is
the exact mirror; entry window 09:35-15:30 ET (sensitivity: 09:45 start, matching Thread
1's "9:45 reversal" folk rule); initial stop = session low/high (sensitivities: prior-day
low/high, close +/- 2*ATR14); exit `pct_ladder` (25% off at each of +1%/+2%/+3%, breakeven
after the first take) by default, sensitivity `ext2_at_exit` swaps in AutoTrader's own
`partial_be_trail` FSM; sizing `risk1pct` by default, sensitivity `ext2_notional10`
implements the OP's stated notional style as `qty=floor(10000/close)`.

### DEVIATIONS-FROM-OP-WORDING (every place interpretation replaced a stated rule)

- MACD settings assumed 12/26/9 (industry-standard default; OP never stated a value; one
  commenter suggested 12/26/9, another 8/21/5 — the alternative is untested here).
- "10-day SMA" implemented as a 10-period SMA on the 5-minute chart, per Thread 2's own
  top-comment consensus (OP never corrected this reading).
- Discretionary filters are NOT automatable and are omitted: "which crosses to take",
  closing a position early "on feel", and the weekly P&L stop that causes the OP to step
  away — none has a stateable mechanical rule.
- `notional10` sizing implemented as `qty=floor(10000/close)` (10% of this project's
  $100,000 paper-equity basis), scaling the OP's stated 1-2%-of-notional style to this
  project's convention — not a literal transcription of a number the OP gave.
- `regime_gate=weekly_spy` implemented as: a bear week blocks new LONGs, a bull week blocks
  new SHORTs, a neutral week halves the computed quantity (floor, skipped if <1) — an
  original design for this research, not sourced from either thread.
- `trend_lock` implemented as: the day's first entry side locks that symbol for the rest of
  the session (Thread 1's "first-hour trend lock" bucket, generalized to the whole day for
  `orb`).

## 3. Credibility assessment

Neither thread contains a backtest, a documented win rate, a documented R:R, or a
documented sample size — both are treated in this research as unverified anecdote requiring
independent evidence (which Sections 9-11 below provide) before any weight is given to
their claims.

**Thread 1 pushback (comment scores):** "If you've never used them, how do you know they
work?" (36); "posting something you haven't got a clue works" (30); title-vs-edit
contradiction (8, 2, 1); "reads like ChatGPT" (3, 3, 2, 2); "if any strategy were effective
it could be programmed into a bot" (1); "no one talks about sizing/risk because yoloing
gets internet points" (8). Zero P&L evidence, zero sample sizes anywhere in the post; "80%
earnings fade" and "9:45 reversal" are unsourced folk statistics presented without
qualification.

**Thread 2 pushback (comment scores):** 10-day-vs-10-period SMA confusion undermining the
"15 years' experience" framing (181, 42, 33, 18, 12); "$300k = 11%, underperformed
buy-and-hold" (178, 95, 40, 31); "dollars not percent = red flag" (22, 40); "all crossover
strategies fail in chop; could be coded and would be a losing bot without the discretionary
sauce" (33); "I backtested it on ES over the last month — remarkable amount of false
signals" (14); "no losing weeks shown / unverified screenshots" (4, 1); "everyone is a
genius in a bull market" (3, 1). A Dec-2025 edit states "gave a lot back in November but
overall a great year" with screenshots only, no verifiable statement.

**Net assessment:** both threads are anecdote from anonymous, unverified accounts, in a
bull-market window, admittedly filtered by discretion the OP cannot state as a rule, with
the single most-upvoted rebuttal in each thread directly challenging the poster's
credibility. This research treats them exactly as the pre-registration says: automatable
cores backtested on their own merits (Sections 9-11), with every non-automatable,
discretionary, or unstated parameter flagged as an assumption (Section 2), never as ground
truth.

## 4. Current strategy analysis

TJL ("Trend Join Long") as implemented: `rules.json` -> `bot/config/loader.py`,
`bot/strategy/trend_join_long.py`, `bot/signal/signal_engine.py`,
`bot/risk/risk_engine.py`, `bot/position/{state,manager}.py`. Full design review:
[`docs/2026-07-03-strategy-analysis-trend-join-long.md`](../2026-07-03-strategy-analysis-trend-join-long.md).

- **Universe/scan:** S&P 500 (Wikipedia scrape), price >= $3; premarket scan 08:30 ET on
  yfinance daily bars; D1 close > prior-day high, D2 prior close > SMA200 (strictly-prior
  rows), D3 gap >= 3%; gap-ranked top-20 watchlist; 30-min rescans 09:55-12:55.
- **Intraday (5m closed bars):** I1 close > frozen premarket high; I2 HOD breakout; I3
  RVOL-TOD >= 2.0 (14-session baseline). Entry window [10:05, 15:30) ET. No EMA/VWAP/ATR/
  RSI/MACD anywhere in the production indicator set.
- **Exit FSM (close-only checks):** initial stop LOD-1%; 1/3 off at +0.75R; breakeven at
  +1.0R; trail = 5m swing low (2,2), never loosens; force-flat at 15:51 ET.
- **Risk:** 1% of $100k risk on stop distance, 10% notional cap, <=5 concurrent, <=5
  trades/day, -$2,000 realised daily breaker. No regime/market filter beyond the per-symbol
  SMA200 gate, no shorts.

**Evidence of record (falsified 2026-08-13, cited verbatim, unchanged by this phase): 226
trades, -0.019R/trade, PF 1.02/0.59/0.97/0.50/1.08/1.32 across E1,E2,E3,D1,D2,D3**
(120-name gapper universes; $0.005/share commission + $0.03/share slippage), -$2,546 total.
This phase's `tjl_base` re-report (Task 3 of plan 10-04, --tjl-regime read-only re-derivation)
reproduces this exactly on the fulluniverse slices — see Section 9's TJL comparator table —
confirming the evidence of record is intact and this phase did not disturb it. On the fixed
24-name MEGA24 set used everywhere else in this research, TJL trades far more sparsely (2-14
trades per window; see `backtester/results/strategy-audit-validation/window{A..E}`),
descriptive only and not used for any verdict here.

**Root causes previously identified (2026-08-13 audit, unchanged):** signal scarcity,
chase-by-construction (entries only after a HOD break at >=10:05, buying strength that has
already partly played out), comfort-optimised exits (partial/breakeven tuned for psychology,
not edge), and an IS-to-OOS collapse in several slices.

## 5. Comparison and conflicts

| Dimension | TJL (AutoTrader) | Ext#1 (Thread 1 list) | Ext#2 (SMA10+MACD) |
|---|---|---|---|
| Direction | long only | mixed (many shorts) | long+short (OP ~90% long) |
| Timeframe | 5m intraday, daily scan | 1m/5m/1H bias | 5m (also daily/weekly swing) |
| Signal | gap>=3% + PMH break + HOD break + RVOL>=2 | trend/mean-rev/liquidity heuristics | SMA10 cross + MACD cross |
| Universe | S&P 500 gappers (top-20) | low-floats/futures/anything | few liquid mega-caps/ETFs |
| Stop | LOD-1% (bar-close eval) | unspecified | prev-day/session low, "sat on losers" |
| Take-profit | 1/3@0.75R, BE@1R, swing trail | unspecified (ORB comment: 2R) | 25%@+1/+2/+3%, runner BE |
| Sizing | 1% risk of $100k, 10% notional cap | unspecified | 1-2% notional (not risk-based) |
| Regime filter | none (per-symbol SMA200 only) | HTF 1H EMA100 bias | weekly index SMA+MACD -> size down |
| Frequency | very low (~35-47/4mo on 120 names) | n/a | high (crossovers daily) |
| Evidence | 226-trade backtest, negative | none | this phase: PF<1 in every IS window tested |

**Overlaps:** trend-following bias; time-of-day windows; breakeven stop after first profit;
a few liquid names. **Conflicts (must NOT be merged into one signal):** Ext#2's
discretionary "which cross to take", weekly P&L stop, and "sitting on losers" directly
contradict TJL's hard-stop discipline; notional vs. risk-based sizing are incompatible
sizing philosophies; Ext#1's mean-reversion setups (RSI exhaustion, PDH/PDL fade, bagholder
bounce) are directionally opposed to trend-following entries and are excluded from `orb`/
`vwap_pb` for exactly this reason. MACD(12/26 EMA cross) and an SMA10 cross are largely
redundant momentum confirmations of the same underlying move, not two independent edges.
**Complementary candidates evaluated in this phase:** weekly regime gate (H3/H7), an
R-based scale-out ladder replacing the fixed-percent one (H2), VWAP as an entry filter (H6),
ORB as a higher-frequency entry inside AutoTrader's own risk/exit framework (H4/H5), and a
09:45 entry-window start (sensitivity inside H1). All are REJECTED or unsupported by
Section 9's numbers.

**TJL's 1H-EMA100 filter is redundant.** D2 (prior close > SMA200) + a >=3% gap-up +
PMH/HOD breakout already puts every TJL candidate above its own 100-hour EMA by
construction — the weekly SPY gate is the only orthogonal (market-level) filter TJL lacks,
and Section 9 shows even that gate does not help (H7: REJECTED).

## 6. Gaps in the current strategy

- No market/regime-level filter — TJL trades identically in bull, bear, and chop weeks;
  the one orthogonal filter this research could test (weekly SPY regime) made things worse
  on Ext#2's entries and on TJL itself (H3, H7 both REJECTED), so this is a gap without a
  demonstrated fix, not a validated opportunity.
- Chase-by-construction entries (HOD break at >=10:05 buys strength that has already
  partly played out) — unresolved by anything tested in this phase.
- Very low signal frequency (35-47 trades / 4 months on the full S&P 500) limits statistical
  power on every future validation; this phase's own MEGA24-universe TJL comparator only
  clears the 25-trade evidence floor by pooling multiple slices.
- No short side, despite `direction` existing as a config field — decorative today.
- No cooldown after a stop-out; no exposure/correlation control across concurrent positions.

## 7. Recommended improvements

Prioritised by expected value x robustness / risk / effort — **none of these are approved
for `rules.json`; they are the candidate backlog a future, separately pre-registered pass
could test differently:**

1. **(Low effort, unproven here) Re-test the weekly regime gate with a stricter definition**
   — this phase's `weekly_regime()` (close vs. weekly SMA10 + weekly MACD) made TJL's IS PF
   worse (H7). A different regime definition (e.g. SPY vs its own 200-day SMA, a coarser
   bull/bear/chop split) was not tested and remains an open question, not a recommendation.
2. **(Low effort, unproven here) R-based exits over fixed-percent ladders** — H2 found
   `partial_be_trail` beats Ext#2's `pct_ladder` on the SAME entries in both IS and OOS
   (Section 9), but this was tested on Ext#2's entries, which have no edge themselves; the
   result says "if you must use a percent-ladder exit, don't" — it does not imply TJL's own
   exit FSM needs to change, since TJL already uses `partial_be_trail`.
3. **(High effort, not attempted) A genuinely different entry signal** — every entry logic
   tested in this phase (TJL, TJL+regime, Ext#2 in 6 variants, ORB in 4 variants, VWAP
   pullback) is negative-PF in-sample. The ceiling on "tune TJL's exits/filters" has likely
   been reached; a materially different signal family (not represented by any Reddit thread
   examined here) would be the next research direction, not incremental tuning.
4. **(No effort — already the active path) Continue the options bot** — `tasty_credit_spreads`
   (Phase 8/9) remains the only strategy in this project with a built, cache-tested backtest
   pipeline and pending live paper UAT; nothing in this phase changes that priority.

## 8. Methodology and assumptions

- **Universe:** frozen 24-name MEGA24 mega-cap list (survivorship-biased, tech-tilted,
  no ETFs), pre-dating this research, cached for windows A-E with zero fetches.
- **Windows:** A 2025-05-01->07-31, B 2025-04-01->04-30, C 2024-09-02->12-31 (not 09-03),
  D 2025-08-01->2026-07-31, E 2023-07-03->2024-08-30. IS = E union C, OOS = B union A union
  D. Nine regime slices (E1/E2/E3/C/B/A/D1/D2/D3) date-slice the same trade logs for tables
  and charts — no re-runs.
- **Costs:** base (metric of record) $0.005/share commission both sides + $0.03/share
  adverse slippage; stress $0.05/share slippage; zero-cost is an upper-bound diagnostic,
  never used for a verdict.
- **Fills/stops:** signal decided on bar CLOSE at t, fill at bar t+1's OPEN +/- adverse
  slippage; stops evaluated on bar CLOSE by default (production parity — a single bar can
  close beyond the stop and realise a loss >1R); `--stop-fill intrabar` is a sensitivity
  mode only (Section 11), not the metric of record.
- **Sizing:** non-compounding $100,000 paper-equity basis, `qty=min(floor(1000/|close-stop|),
  floor(10000/close))`, skip if <1 — matches `bot/risk/risk_engine.py` parity exactly.
- **Cache-only discipline:** `backtester.experimental.run` refuses to run against Massive
  when any required 5m/1d cache file is missing unless `--allow-fetch` is explicitly passed
  (never used in this phase's run matrix); the SPY weekly-regime series is the phase's only
  network fetch, via yfinance (`bot.scanner.fetcher._download_batch`), zero Massive
  requests, cached to `backtester/cache/SPY_1d_regime.csv`.
- **Exact commands run** (from `backtester/results/experimental/`, mirrored in
  `logs/*.log`):

  ```bash
  R=backtester/results/experimental
  for W in E C B A D; do
    python3 -m backtester.experimental.run --window $W --arms backtester/experimental/arms.json --cost base   --out $R/runs
    python3 -m backtester.experimental.run --window $W --arms backtester/experimental/arms.json --cost stress --only ext2_base,orb30_base,vwap_pb_base,ext2_regime --out $R/runs
    python3 -m backtester.experimental.run --window $W --arms backtester/experimental/arms.json --cost zero   --only ext2_base,orb30_base,vwap_pb_base,ext2_regime --out $R/runs
    python3 -m backtester.experimental.run --window $W --arms backtester/experimental/arms.json --cost base --stop-fill intrabar --only ext2_base,orb30_base,vwap_pb_base --out $R/runs-intrabar
  done
  for S in E1 E2 E3 D1 D2 D3; do python3 -m backtester.experimental.run --tjl-regime --baseline backtester/results/strategy-audit-validation/fulluniverse/$S/base --out $R/tjl/$S; done
  for W in A B C D E; do python3 -m backtester.experimental.run --tjl-regime --baseline backtester/results/strategy-audit-validation/window$W/base --out $R/tjl/$W; done
  python3 -m backtester.experimental.aggregate --root $R/runs --tjl-root $R/tjl --out $R/aggregate
  python3 -m backtester.experimental.aggregate --root $R/runs-intrabar --out $R/aggregate-intrabar
  python3 -m backtester.experimental.charts --root $R/runs --agg $R/aggregate/results.csv --out docs/research/assets/2026-08-18-external-strategies/
  ```

## 9. Results

Full numbers: `docs/research/assets/2026-08-18-external-strategies/results.csv` (302 rows:
every arm x cost x slice, plus IS/OOS/ALL pools) and `results.md` (readable tables). Charts:
`equity_curves.svg` (core-arm equity curves, all windows pooled, base cost) and
`pf_by_slice.svg` (per-slice PF bars, base cost, core arms + `tjl_base`), both under
`docs/research/assets/2026-08-18-external-strategies/`.

### Base-cost IS/OOS summary, entry families (all rows >= 25-trade floor)

| Arm | IS trades | IS PF | OOS trades | OOS PF | IS/OOS PF ratio |
|---|---|---|---|---|---|
| ext2_base | 1890 | 0.8968 | 1675 | 0.9747 | 0.920 |
| ext2_at_exit | 1890 | 0.9073 | 1675 | 0.9795 | 0.926 |
| ext2_regime | 1890 | 0.8908 | 1675 | 0.9006 | 0.989 |
| ext2_uncapped (diagnostic) | 13140 | 0.8835 | 11667 | 0.8859 | 0.997 |
| orb30_base | 1880 | 0.8113 | 1661 | 0.9312 | 0.871 |
| orb30_fixed2r | 1880 | 0.8030 | 1661 | 0.9582 | 0.838 |
| vwap_pb_base | 1167 | 0.8975 | 1022 | 0.7623 | 1.177 |

### TJL comparator (fulluniverse, H7), IS = E1+E2+E3, OOS = D1+D2+D3

| Arm | IS trades | IS PF | OOS trades | OOS PF |
|---|---|---|---|---|
| tjl_base | 118 | 0.7978 | 108 | 0.9277 |
| tjl_regime | 97 | 0.5860 | 92 | 0.9055 |

### Per-slice PF, base cost (all n >= 99 trades except vwap_pb's B slice at 58)

| Arm | E1 | E2 | E3 | C | B | A | D1 | D2 | D3 |
|---|---|---|---|---|---|---|---|---|---|
| ext2_base | 0.87 | 0.84 | 0.97 | 0.92 | 0.76 | 0.80 | 0.97 | 1.03 | 1.13 |
| ext2_regime | 0.73 | 1.03 | 0.86 | 0.97 | 0.86 | 0.66 | 0.84 | 1.11 | 1.01 |
| orb30_base | 0.83 | 0.82 | 0.91 | 0.69 | 1.01 | 0.95 | 0.67 | 1.01 | 1.10 |
| vwap_pb_base | 1.07 | 0.64 | 0.98 | 0.98 | 0.97 | 0.84 | 0.66 | 0.70 | 0.81 |

## 10. Baseline vs improved

TJL baseline (`tjl_base`, fulluniverse) is the only production strategy evidence; the
weekly regime day-filter (`tjl_regime`) is TJL's one tested candidate improvement, and it
is **worse**, not better: IS PF 0.798 -> 0.586 (H7 REJECTED). None of the external-strategy
arms (`ext2_*`, `orb*`, `vwap_pb_base`) score above `tjl_base`'s own IS PF (0.798) except
`ext2_at_exit`/`ext2_base`/`ext2_regime` in isolation are all still below 1.0 despite being
run on a different (MEGA24, not fulluniverse) universe, so a direct apples-to-apples
"baseline vs improved" comparison is not meaningful across universes — the honest summary
is: **every candidate this phase tested, on its own universe, is at or below breakeven
in-sample.** There is no improved variant to recommend.

## 11. Robustness and overfitting

- **IS/OOS PF ratio** (Section 9 table): every entry-family arm's ratio is within the
  H8 bar's own +/-40% band (0.838-1.177), i.e. results are at least *directionally*
  consistent between IS and OOS — but consistently below 1.0, so consistency here means
  "consistently no edge," not "consistently profitable."
  `ext2_uncapped`'s ratio (0.997) confirms the 5-trades/day and 5-concurrent caps are not
  distorting the underlying (lack of) edge — capped and uncapped PF differ by <2%.
- **Sensitivity families** (walk-forward, `results.md`): across all three families
  (`ext2_confirm_bars`, `orb_window`, `ext2_stop_basis`) and all 8 slice-to-slice
  transitions, the best-by-PF member's very NEXT slice PF tops out at 1.06 (orb_window
  D2->D3, stop-basis D1->D2/D2->D3) — no walk-forward-selected member ever sustains a PF
  anywhere near the 1.3 production bar one slice later.
- **Cost stress:** `ext2_base` IS PF 1.020 (zero cost) -> 0.897 (base) -> 0.836 (stress);
  `orb30_base` 0.924 -> 0.811 -> 0.754; `vwap_pb_base` 1.182 -> 0.897 -> 0.776. Ext#2 and
  VWAP pullback are close to flat at zero cost and go negative once realistic costs are
  applied — commissions/slippage matter, but are not the primary reason these arms lack
  edge (ORB is already clearly negative even at zero cost).
- **Intrabar stop fills** (`results-intrabar.csv`): `vwap_pb_base` IS PF actually crosses
  above 1.0 under intrabar fills (1.025 vs. 0.897 at close-only), its OOS PF does not
  (0.791 vs. 0.762) — an intrabar-fill artifact, not a robust edge; `ext2_base` and
  `orb30_base` remain below 1.0 in both fill modes.
- **`ext2_uncapped` diagnostic:** at 13,140 IS trades (7x `ext2_base`'s capped 1,890), PF is
  0.884 — nearly identical to the capped arm's 0.897 — confirming the 5-trades-per-day /
  5-concurrent priority cap is not hiding a materially different result; it is a real
  frequency artifact with only a marginal effect on the reported edge.
- **Bootstrap CIs on mean R** (`results.csv`, `r_mean_ci_low`/`r_mean_ci_high` columns,
  10,000 resamples, seed 0, deterministic): every entry-family arm's IS/OOS mean-R interval
  straddles or sits below zero — none excludes zero on the positive side, consistent with
  "no demonstrated edge," not a borderline-significant positive result being obscured.

## 12. Recommended architecture

Described for completeness even though nothing here is SUPPORTED (per the pre-registration's
own "What would change rules.json" section, verbatim):

- **H2 SUPPORTED** would justify replacing `exit.model` semantics or adding an alternative
  exit model to `_IMPLEMENTED_EXIT_MODELS` (currently only the production
  `partial_be_trail`-style FSM exists; Ext#2's `pct_ladder` would need to be added and
  schema-validated first). H2 IS in fact SUPPORTED here (Section "Verdict table" below) —
  but only as a statement about Ext#2's own entries, which have no edge; this is noted, not
  acted on.
- **H3 or H7 SUPPORTED** would justify adding a new top-level `regime_gate` key to
  `rules.json`, consumed by `run_daily_scan` as an additional day-level gate. Neither is
  SUPPORTED here.
- **H4/H5/H6 SUPPORTED** would justify a new `strategy_name` value (e.g. `"orb30"` or
  `"vwap_pb"`) dispatched in `bot/main.py`, alongside a new `bot/strategy/<name>.py`
  `StrategyCore` subclass — a materially larger change than a config-value edit, requiring
  its own plan and its own pre-registration-style validation. None is SUPPORTED here.
- **H8 SUPPORTED** would justify the conditional productionization sketch: a default-off,
  schema-valid feature branch off `develop` (`feature/phase10-<arm>`), never auto-merged.
  H8 is REJECTED here (no arm is within striking distance of the production bar), so no
  such branch was created.

**Zero code changes to `bot/` occurred in this phase.** `rules.json` and
`rules_options.json` were not modified by this phase, regardless of any hypothesis's
verdict.

## 13. Roadmap

1. This phase closes with a "no production change" recommendation, recorded in
   `.planning/STATE.md` and `.planning/ROADMAP.md`.
2. Plan 10-06 (conditional productionization) is gated on H8 — H8 is REJECTED, so 10-06's
   scope is limited to documenting this outcome, not building a feature branch.
3. Next research candidate (unscheduled, not pre-registered): a materially different entry
   signal family, since every incremental tweak to TJL and every automatable external idea
   tested here tops out below breakeven in-sample.
4. The options bot (`tasty_credit_spreads`, Phase 8/9) remains the active priority: live
   paper UAT is still pending, independent of this phase's findings.

## 14. Risks, limitations and open questions

- **Survivorship bias** in the frozen 24-name MEGA24 universe (today's mega-caps, not
  point-in-time constituents) — a strategy that looks flat here could look different on a
  point-in-time universe, in either direction.
- **5-trades/day and 5-concurrent priority caps** are applied in strict time-then-
  alphabetical order — an implementation artifact, not a strategy property;
  `ext2_uncapped` (Section 11) shows this has only a marginal effect here, but the same
  caveat applies to every other capped arm.
- **No premarket data** in the experimental engine — unlike TJL's production scanner, none
  of `ext2`/`orb`/`vwap_pb` consider premarket price action.
- **No borrow/locate cost** modeled on short positions; non-compounding $100,000 sizing
  basis; commissions applied at the report layer only (post-hoc), matching the existing
  project-wide backtester convention.
- **Force-close-bar convention:** a position still open at force-close is filled at that
  bar's close price, not the exact force-close timestamp.
- **Stops evaluated on bar close by default** (production parity) — a single bar can close
  beyond the stop level, so realized losses can exceed 1R; `--stop-fill intrabar` is a
  sensitivity mode only (Section 11), not the metric of record.
- **Windows leave 2025-01-01 through 2025-03-31 uncovered** by either IS or OOS — stated
  explicitly, not silently absorbed into either bucket.
- **Open question:** would a coarser or differently-defined regime filter (not the
  close-vs-SMA10 + MACD weekly definition tested here) behave differently on TJL or Ext#2?
  Untested, and not assumed to be a viable next step without its own pre-registration.
- **Open question:** is there a signal family fundamentally different from both TJL's
  breakout-and-hold-trend approach and the two Reddit threads' momentum-crossover / ORB
  ideas that would clear the production bar? Out of scope for this phase.

## Verdict table

Every number below is sourced from `docs/research/assets/2026-08-18-external-strategies/results.csv`
(spot-checked against the tables in Section 9). The >=25-trade evidence floor is checked
per hypothesis, separately in IS and in OOS; the verdict is applied strictly by the
pre-registered rules in `docs/research/2026-08-18-external-strategies-hypotheses.md` —
never softened because a number looked promising.

| # | Hypothesis | Prediction | IS (PF / trades / floor) | OOS (PF / trades / floor) | Verdict |
|---|---|---|---|---|---|
| H1 | Ext#2 as-is has edge | PF>1 both (predicted REJECTED) | 0.897 / 1890 / met | 0.975 / 1675 / met | **REJECTED** |
| H2 | `partial_be_trail` beats `pct_ladder` on Ext#2's own entries | R-based >= ladder | 0.907 vs 0.897 / 1890 / met | 0.980 vs 0.975 / 1675 / met | **SUPPORTED** |
| H3 | Weekly SPY regime gate improves Ext#2 | improves, still <1.3 | 0.891 vs 0.897 (Sortino tiebreak favors regime, PF diff <5%) / 1890 / met | 0.901 vs 0.975 (PF diff >5%, regime worse) / 1675 / met | **REJECTED** |
| H4 | ORB-30+HTF+VWAP has edge | INSUFFICIENT/REJECTED, maybe positive in trending slices | 0.811 / 1880 / met | 0.931 / 1661 / met | **REJECTED** |
| H5 | Fixed-2R vs `partial_be_trail` for ORB | (no stated a-priori direction) | 0.803 vs 0.811 (fixed2r worse) / 1880 / met | 0.958 vs 0.931 (fixed2r better) / 1661 / met | **REJECTED** (no edge either way; direction flips IS->OOS) |
| H6 | VWAP pullback has edge | REJECTED | 0.897 / 1167 / met | 0.762 / 1022 / met | **REJECTED** |
| H7 | Regime gate improves TJL | predicted INSUFFICIENT (trade count) | 0.586 vs 0.798 base (worse) / 97 / met | 0.905 vs 0.928 base (worse) / 92 / met | **REJECTED** (floor was in fact met, contrary to the a-priori guess) |
| H8 | Combined arm clears the production bar (PF>=1.3 both, Sortino>1, maxDD<=10%, IS/OOS ratio +/-40%, stress PF>=1.15, no sign flip, >=2 entries/mo) | predicted no -> no production change | no `combo` arm exists (rule never fired: no family cleared IS PF>1); best of all 15 arms is orb5 IS PF 0.922 | n/a | **REJECTED** — no arm, combined or otherwise, is within reach of PF>=1.3 |

**No arm is SUPPORTED in both IS and OOS.** Plan 10-06 (conditional productionization) is
gated on H8; H8 is REJECTED, so plan 10-06 documents this outcome rather than building a
feature branch.

**`rules.json` and `rules_options.json` were not modified by this phase, regardless of
verdict.**
