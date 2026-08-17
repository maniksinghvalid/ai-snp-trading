# Options backtest hypotheses (Phase 9, pre-registration)

**Pre-registration statement**

Dated 2026-08-17, before any real-data backtest run of `backtester/options_run.py` has
occurred. Commit discipline proves it: this file is committed in its own commit, ahead of
Plan 09-03 (engine) and Plan 09-04 (first real run) — check `git log --diff-filter=A --format='%h %ad %s' --date=short -- 'docs/research/2026-08-17-options-backtest-hypotheses.md'`
and confirm no commit under `backtester/results/options/` predates it. As of this commit,
`backtester/results/options/` does not exist (and `backtester/results/`/`backtester/validation_run/`
in the working tree, if present, are pre-existing equity-backtester scratch output, not
options results). The hypotheses, arms, windows, evidence floor, metric of record and
verdict rules below are frozen before a single number exists to bias them — the Phase 6
lesson (IS PF=inf, OOS PF=0.18, an overfit backtester with no pre-registration) is exactly
what this file exists to prevent for the options bot.

## Data window

Plan 09-01's live probe (`.planning/phases/09-options-backtester/09-01-SUMMARY.md`) pinned
the Massive option-daily-aggregates entitlement boundary: May 2024 and July 2024 both 403
(`NOT_AUTHORIZED: "Your plan doesn't include this data timeframe"`), August 2024 returned
200. This is a **rolling ~24-month window from "today"**, not a fixed calendar date — as
"today" advances, the ceiling advances with it. Pinned relative to this pre-registration's
date (2026-08-17): usable range is approximately **2024-08-15 → 2026-08-17**.

The ceiling is ~24 months and is **NOT the lever for the evidence floor**. A 45-DTE
iron-condor strategy on one underlying completes roughly 8-9 full entry-to-exit cycles per
year even before the IVR>=30 entry gate cuts most days out — one more year of history would
not multiply the trade count enough to matter. **Pooling across the D-04 universe
(SPY first, then QQQ/IWM/TLT/GLD/XLE) is the lever**, not a longer time span (RESEARCH.md
Open Question 2).

## IS / OOS windows

All dates below fall inside the entitled `[2024-08-15, 2026-08-17]` range.

- **IVR warm-up** (no entries evaluated; only used to build each underlying's daily
  ATM-IV series so `iv_rank` clears `IV_RANK_MIN_OBS=60` before the IS start):
  **2024-08-15 → 2024-11-15** (67 NYSE trading days, `backtester/options/greeks.py`'s own
  `IV_RANK_MIN_OBS=60` constant — verified via `pandas_market_calendars` NYSE calendar).
  The window GROWS toward `IV_RANK_WINDOW=252` as more days accumulate; it never resets
  or requires a full 252-day fill before the first `iv_rank` value is available.
- **IS window (in-sample):** **2024-11-18 → 2025-07-31** (~8.5 months). First entry no
  earlier than 2024-11-18 so `iv_rank` has already cleared its warm-up.
- **OOS window (out-of-sample):** **2025-08-01 → 2026-06-15** (~10.5 months). Last entry
  date capped at 2026-06-15 (not 2026-08-17) so that a worst-case position opened at
  `max_dte=60` and held to expiry with no early exit (`2026-06-15 + 60d = 2026-08-14`)
  still fully settles inside the entitled data range and before this pre-registration's
  "today" — no OOS trade can be left artificially open/unsettled by a lack of future data.

Effective config for both windows is `rules_options.json` unchanged plus the single `--set`
override under test per hypothesis (D-15) — never an edited `rules_options.json`.

## Hypotheses

**H1 — IVR entry threshold: `entry.ivr_min` 20 vs 30**
- Arm A (baseline): `rules_options.json` as-is (`entry.ivr_min=30`).
- Arm B: `--set entry.ivr_min=20`.
- Prediction: lowering the gate admits more entries at lower IV-regime conviction; PF/Sortino
  should be flat-to-worse per trade but trade count rises. H1 is SUPPORTED only if Arm B's
  metric of record does NOT degrade relative to Arm A in both IS and OOS AND both arms clear
  the evidence floor — i.e. the looser gate would win under D-14/D-17's rule, not merely
  "produce more trades."
- Decision rule: compare Arm A vs Arm B PF+Sortino (metric of record below) in IS, then in
  OOS, independently.

**H2 — Short-strike delta: `structure.short_delta` 0.16 vs 0.20**
- Arm A (baseline): `rules_options.json` as-is (`structure.short_delta=0.20`).
- Arm B: `--set structure.short_delta=0.16`.
- Prediction: a farther-OTM (lower-delta) short strike lowers win rate per trade (thinner
  credit, closer to a coin-flip on assignment) but should raise average credit-to-max-loss
  ratio; the metric of record decides whether that tradeoff nets positive.
- Decision rule: same IS-then-OOS comparison as H1, same two arms via `--set`.

**H3 — Structure: `structure.type` iron_condor vs put_credit_spread**
- Arm A (baseline): `rules_options.json` as-is (`structure.type=iron_condor`).
- Arm B: `--set structure.type=put_credit_spread`.
- Prediction: a one-sided PCS collects less total credit per trade (no call side) but has
  half the legs (lower assignment-guard/commission drag) and no call-side pin risk.
- Decision rule: same IS-then-OOS comparison as H1/H2, same two arms via `--set`.

## Metric of record

Per D-16, `backtester/options/engine.py`'s report glue imports
`backtester.report._sortino_ratio` and `backtester.report._win_loss_stats` directly (never
`compute_metrics`/`write_report`/`_net_pnl`, which assume per-share stock P&L — RESEARCH.md
Pitfall 2) and computes its own options-shaped `[(date, equity)]` curve and per-trade dollar
P&L list to feed them.

- **Profit factor (PF):** gross profit / gross loss across closed trades in the window —
  from `_win_loss_stats`'s returned dict.
- **Sortino ratio:** downside-deviation-adjusted return over the equity curve —
  `backtester.report._sortino_ratio(curve, starting_capital)`.
- **Tie-break:** average credit captured % — `(credit - net_exit) / credit` averaged across
  closed, non-expired-worthless trades in the window.

A hypothesis wins an arm-vs-arm comparison when it has the higher PF AND the higher Sortino
in that window; if PF and Sortino disagree on direction, PF is primary and Sortino is the
tie-break override only when PF is within 5% between arms (both must be economically
distinguishable, not a rounding artifact of a small trade count).

## Evidence floor

**>=25 closed trades per arm, in BOTH the IS window and the OOS window, independently.**

Rough arithmetic estimate (SPY-only, first real run per D-04): a 45-DTE iron condor closes
in roughly 21-60 calendar days once opened (assignment-guard/dte-exit/profit-target cap the
hold), so one underlying supports at most ~2 concurrent-then-closed cycles per quarter absent
the IVR gate. With `entry.ivr_min=30` historically passing SPY on a minority of trading days
(elevated-IV regimes are the exception, not the rule), SPY alone in an 8.5-month IS window is
very unlikely to clear 25 trades for a single hypothesis arm, let alone three hypotheses each
needing their own 25/arm in both windows. **Widening the universe past SPY-only (D-04's
QQQ/IWM/TLT/GLD/XLE) is expected to be necessary before H1-H3 can be evaluated at all** —
this is stated up front, not discovered mid-run.

**INSUFFICIENT-EVIDENCE is an expected, valid, publishable outcome for one, two, or all three
hypotheses if the floor is not cleared — it is NOT a reason to loosen the floor, extend the
OOS window into untested territory, or narrow the universe further to make a target easier to
hit.** A hypothesis that cannot be evaluated says something true about the strategy's trade
frequency; reporting INSUFFICIENT-EVIDENCE honestly is the correct outcome in that case.

## Verdict rules

Per underlying decision, per hypothesis (D-17 vocabulary):

- **SUPPORTED** — Arm B wins the metric-of-record comparison (see above) in BOTH the IS
  window AND the OOS window, AND both arms clear the >=25-trade evidence floor in BOTH
  windows.
- **REJECTED** — both arms clear the evidence floor in both windows, but Arm B does not win
  in both IS and OOS (e.g. wins IS but loses OOS — the Phase 6 overfitting signature).
- **INSUFFICIENT-EVIDENCE** — either arm fails to clear >=25 trades in either window. No PF/
  Sortino comparison is reported as a verdict in this case (the numbers may still be shown
  for transparency, but are not treated as evidence for or against the hypothesis).

## Known limitations, declared before results

- **No bid/ask in Massive daily aggregates (D-11):** the fill model is entirely
  close-derived — `synthesize_bid_ask` fabricates a bid/ask from `close ± spread_pct/2` so
  `leg_is_liquid` can run unmodified. This is a real, stated approximation, not a hidden one.
- **Open-interest gate is not evaluable offline:** Massive's `O:` daily aggregates have no
  `open_interest` field at all (RESEARCH.md Massive API item 2); `min_open_interest` is
  therefore approximated from `v` (volume) when present, documented as a limitation, never
  silently upgraded to "OI gate enforced" language in the results doc (RESEARCH.md
  Anti-Patterns).
- **Deterministic `sorted(symbols)` underlying order** replaces live's broker `stock_id`-sort
  iteration order (RESEARCH.md Pitfall 3) — affects which underlyings get capacity on a day
  the concurrent-position cap binds; a documented, intentional divergence from live, not a
  cap/threshold change.
- **IVR is min-max normalized** (`(iv_today - min) / (max - min) * 100` over the trailing
  window), matching the tastytrade/industry "IV Rank" convention — NOT a percentile rank
  (IVP). This is RESEARCH.md's A1/A4 assumption: A1 (min-max is the intended convention) is
  now a locked implementation choice (D-10 as amended, `backtester/options/greeks.py`); A4
  (moomoo's live `IV_RANK` uses the same convention) remains an unverified assumption — if
  live moomoo turns out to compute IVP instead, H1's threshold comparison would be answering
  a related-but-different question than the live bot's own gate, a limitation to flag
  verbatim in the results doc, not silently reconciled.

## What would change rules_options.json

`rules_options.json` changes AFTER this backtest's results exist, and only for a hypothesis
whose verdict is SUPPORTED (never REJECTED or INSUFFICIENT-EVIDENCE) — the single sentence
gating Plan 09-04's final decision checkpoint.
