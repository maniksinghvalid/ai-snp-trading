# IBS ETF strategy search — results (Phase 12, IBS-10)

**Dated 2026-10-04.** Research scripts: `backtester/experimental/ibs_search/` (copied from the
authoring session's untracked scratch directory with ONLY the path constants changed, so the
logic is byte-identical to what produced the numbers below). Evidence:
`docs/research/assets/2026-10-04-ibs-etf-strategy-search/` — `round1.txt`, `round2.txt`,
`ibs_robust.txt`, `ibs_sizing.txt`, `r5_robust.txt` (stdout of the scripts re-run on
2026-10-04 for this plan) and `part1_daily.csv`, `part1_gap_buckets.csv`,
`part2_5m_hold.csv` (outputs of `screen_study.py` from 2026-10-03, copied unchanged — the
script needs ~14k cached Massive 5m files and was not re-run). The authoring-session stdout
(2026-10-03/04) is the source of the reference numbers quoted in Section 9; the 2026-10-04
re-run reproduces them to within yfinance dividend-adjustment drift (Section 9).

## 1. Executive summary

**Verdict: S3 IBS-ETF passed every round-1 criterion and no round-2 family beat it on the
pre-registered, risk-aware bar; it is adopted — unlevered, on 17 ETFs, 10 slots.** Round 1
tested six pre-registered hypotheses (RSI2 on stocks and ETFs, IBS on ETFs, overnight SPY/QQQ,
turn-of-month): S2, S3 and S5 passed, S4 (both) and S1 failed. Round 2 raised the bar
("must BEAT IBS-ETF") and tested six structurally different families: five were not better.
R5 (IBS-2x) is flagged BETTER by the literal pre-registered bar (CAGR 31.7% vs 16.4%) but it
is the same IBS signal with leverage — same 935 trades as the unlevered rule on the same four
ETFs — at 2-2.5x the drawdown (-27.6% vs -11.2%) and a lower Sharpe (1.22 vs 1.30); the
operator rejected it.

Operator decisions (2026-10-04, final):

- **OD-1** Unlevered IBS-17 (17 ETFs, 10 slots x 10%). The 2x and mixed variants are rejected
  (Section 7).
- **OD-2** Deployment is a new, separate bot process (`bot/ibs/`, `python3 -m bot --rules
  rules_ibs.json`); the equity bot code is untouched.
- **OD-3** Fills near the close, decision at close - 10 minutes. The backtest "close fill" column
  is the target; the "next-open" column is the conservative floor.
- **OD-4** Trend Join Long is stopped at cutover. It showed no edge in every test (full-universe
  backtest 2026-08-13; premarket screen carries no intraday excess return, Section 8).

## 2. Round 1 — pre-registered hypotheses and pass criteria

Rules fixed before the first run; no parameter tuning. Prior work had rejected every intraday
family tried (TJL, ORB, VWAP pullback, Ext#2), so every candidate is a multi-day or overnight
hold from a published anomaly. Costs per side: stocks 0.06%, ETFs 0.02%. In-sample (IS)
2010-2018, out-of-sample (OOS) 2019-01-01..2026-10-02.

- **S1 RSI2-stocks**: S&P 500 stock, close > SMA200 and RSI(2) < 10 -> buy; exit close > SMA5 or 10 days; max 10 positions x 10% equity, lowest RSI2 first.
- **S2 RSI2-ETF**: same rules on a liquid ETF universe (no survivorship bias).
- **S3 IBS-ETF**: IBS = (C-L)/(H-L) < 0.2 -> buy; exit IBS > 0.8 or 10 days. ETF universe.
- **S4 Overnight**: SPY / QQQ, buy every close, sell next open.
- **S5 TOM-SPY**: turn of month — buy the close of the 4th-last trading day, sell the close of the 3rd trading day of the next month.

Pass criteria (ALL required, judged on OOS, net of costs):

1. profit factor >= 1.2 and >= 100 trades (S4/S5: >= 50 trades)
2. positive in >= 5 of the 8 OOS calendar years
3. Sharpe >= 0.5
4. max drawdown shallower than SPY buy & hold over the same window
5. IS (2010-2018) also net positive (no sign flip)
6. next-open execution variant also net positive (robust to fill timing)

(Criteria 3 and 4 are one bullet in the script docstring; seven checks are printed per verdict:
PF, trades, pos_years, Sharpe, DD<SPY, IS_positive, next_open_positive.)

## 3. Round 1 verdicts

SPY buy & hold OOS: CAGR 17.3%, Sharpe 0.93, maxDD -33.7%, 7/8 positive years
(IS: maxDD -19.35%, Sharpe 0.80). Source: `round1.txt`.

| hypothesis | window | fill | trades | win% | avg_trade% | PF | CAGR% | Sharpe | maxDD% | pos_years |
|---|---|---|---|---|---|---|---|---|---|---|
| S2 RSI2-ETF | IS 2010-18 | close | 1151 | 72.37 | 0.22 | 1.41 | 2.71 | 0.43 | -14.98 | 7/9 |
| S2 RSI2-ETF | IS 2010-18 | next_open | 1149 | 68.23 | 0.26 | 1.51 | 3.05 | 0.47 | -14.36 | 7/9 |
| S2 RSI2-ETF | OOS 2019-26 | close | 994 | 70.42 | 0.37 | 1.71 | 4.78 | 0.85 | -9.56 | 6/8 |
| S2 RSI2-ETF | OOS 2019-26 | next_open | 991 | 67.51 | 0.35 | 1.62 | 4.34 | 0.77 | -8.61 | 7/8 |
| S3 IBS-ETF | IS 2010-18 | close | 3464 | 65.62 | 0.24 | 1.42 | 9.13 | 0.91 | -15.35 | 8/9 |
| S3 IBS-ETF | IS 2010-18 | next_open | 3412 | 62.87 | 0.19 | 1.33 | 6.30 | 0.67 | -16.05 | 8/9 |
| S3 IBS-ETF | OOS 2019-26 | close | 3162 | 64.14 | 0.39 | 1.62 | 16.45 | 1.30 | -11.18 | 7/8 |
| S3 IBS-ETF | OOS 2019-26 | next_open | 3106 | 63.14 | 0.31 | 1.47 | 12.72 | 1.08 | -21.69 | 7/8 |
| S4 Overnight-SPY | IS 2010-18 | close | 2264 | 49.91 | -0.01 | 0.95 | -2.68 | -0.26 | -28.20 | 4/9 |
| S4 Overnight-SPY | IS 2010-18 | next_open | 2264 | 49.91 | -0.01 | 0.95 | -2.68 | -0.26 | -28.20 | 4/9 |
| S4 Overnight-SPY | OOS 2019-26 | close | 1948 | 53.44 | 0.00 | 1.01 | -0.12 | 0.05 | -30.14 | 5/8 |
| S4 Overnight-SPY | OOS 2019-26 | next_open | 1948 | 53.44 | 0.00 | 1.01 | -0.12 | 0.05 | -30.14 | 5/8 |
| S4 Overnight-QQQ | IS 2010-18 | close | 2264 | 52.92 | 0.01 | 1.04 | 1.70 | 0.22 | -18.62 | 6/9 |
| S4 Overnight-QQQ | IS 2010-18 | next_open | 2264 | 52.92 | 0.01 | 1.04 | 1.70 | 0.22 | -18.62 | 6/9 |
| S4 Overnight-QQQ | OOS 2019-26 | close | 1948 | 54.00 | 0.01 | 1.05 | 2.83 | 0.26 | -35.31 | 6/8 |
| S4 Overnight-QQQ | OOS 2019-26 | next_open | 1948 | 54.00 | 0.01 | 1.05 | 2.83 | 0.26 | -35.31 | 6/8 |
| S5 TOM-SPY | IS 2010-18 | close | 108 | 63.89 | 0.45 | 1.82 | 5.70 | 0.74 | -12.23 | 7/9 |
| S5 TOM-SPY | IS 2010-18 | next_open | 108 | 63.89 | 0.45 | 1.82 | 5.70 | 0.74 | -12.23 | 7/9 |
| S5 TOM-SPY | OOS 2019-26 | close | 92 | 60.87 | 0.50 | 1.75 | 6.41 | 0.69 | -15.26 | 6/8 |
| S5 TOM-SPY | OOS 2019-26 | next_open | 92 | 60.87 | 0.50 | 1.75 | 6.41 | 0.69 | -15.26 | 6/8 |
| S1 RSI2-stocks (SURVIVORSHIP-BIASED) | IS 2010-18 | close | 4940 | 65.26 | 0.22 | 1.24 | 11.75 | 0.81 | -20.68 | 8/9 |
| S1 RSI2-stocks (SURVIVORSHIP-BIASED) | IS 2010-18 | next_open | 4563 | 62.09 | 0.16 | 1.17 | 6.02 | 0.51 | -19.39 | 7/9 |
| S1 RSI2-stocks (SURVIVORSHIP-BIASED) | OOS 2019-26 | close | 4316 | 66.43 | 0.27 | 1.23 | 14.38 | 0.79 | -35.99 | 7/8 |
| S1 RSI2-stocks (SURVIVORSHIP-BIASED) | OOS 2019-26 | next_open | 3958 | 63.67 | 0.22 | 1.18 | 8.34 | 0.55 | -31.70 | 7/8 |

- VERDICT S2 RSI2-ETF: PASS  PF>=1.2=y, trades>=100=y, pos_years>=5/8=y, Sharpe>=0.5=y, DD<SPY=y, IS_positive=y, next_open_positive=y
- VERDICT S3 IBS-ETF: PASS  PF>=1.2=y, trades>=100=y, pos_years>=5/8=y, Sharpe>=0.5=y, DD<SPY=y, IS_positive=y, next_open_positive=y
- VERDICT S4 Overnight-SPY: FAIL  PF>=1.2=N, trades>=50=y, pos_years>=5/8=y, Sharpe>=0.5=N, DD<SPY=y, IS_positive=N, next_open_positive=N
- VERDICT S4 Overnight-QQQ: FAIL  PF>=1.2=N, trades>=50=y, pos_years>=5/8=y, Sharpe>=0.5=N, DD<SPY=N, IS_positive=y, next_open_positive=y
- VERDICT S5 TOM-SPY: PASS  PF>=1.2=y, trades>=50=y, pos_years>=5/8=y, Sharpe>=0.5=y, DD<SPY=y, IS_positive=y, next_open_positive=y
- VERDICT S1 RSI2-stocks: FAIL  PF>=1.2=y, trades>=100=y, pos_years>=5/8=y, Sharpe>=0.5=y, DD<SPY=N, IS_positive=y, next_open_positive=y

Summary: S2 PASS, S3 PASS, S5 PASS, S4-SPY FAIL, S4-QQQ FAIL, S1 FAIL (max drawdown worse than
SPY; survivorship-biased universe — current S&P 500 constituents).

## 4. Round 2 — "must beat IBS" hypotheses and criteria

About 16 tests in total by round 2, so the bar was raised. Rules fixed before the first run.

- **R1 Lev-trend**: hold the 2x/3x ETF while the underlying closes > SMA200, else cash (SPY->SSO, QQQ->QLD, SPY->UPRO, QQQ->TQQQ).
- **R2 GEM**: Antonacci dual momentum, monthly: SPY vs EFA by 12-month return; hold the winner if its 12-month return beats BIL's, else AGG.
- **R3 Sector-mom**: monthly, top-3 of the 9 sector SPDRs by 6-month return, only those with a positive 6-month return; equal weight; rest cash.
- **R4 MR-ensemble**: union of the two round-1 mean-reversion signals on the 17-ETF universe: enter if IBS < 0.2 OR (RSI2 < 10 and C > SMA200); exit if IBS > 0.8 OR C > SMA5 OR 10 days.
- **R5 IBS-2x**: IBS rules computed on SPY/QQQ/IWM/DIA, but the 2x ETF (SSO/QLD/UWM/DDM) is bought; 4 slots x 25%.
- **R6 XS-momentum**: S&P 500 stocks, 12-1 month momentum, top-10 equal weight, monthly, all cash when SPY <= SMA200. SURVIVORSHIP-BIASED (current constituents).

"Better than IBS" criteria (ALL required, OOS 2019-01-01..latest, close fill, net): CAGR > 16.4%
AND Sharpe >= 1.0 AND maxDD shallower than SPY AND positive in >= 5 of 8 OOS years AND IS
2010-18 net positive AND next-open fill net positive AND trades >= 100 (daily strategies) /
>= 30 (monthly strategies).

## 5. Round 2 verdicts

```
================ ROUND 2 SUMMARY (OOS 2019-26, close fill) ================
strategy                                                    CAGR% Sharpe  maxDD% trades  verdict
(round-1 winner) IBS-ETF 17 ETFs                             16.4   1.30   -11.2   3164  baseline
R1 Lev-trend SSO (SPY>SMA200)                                19.8   0.86   -37.5     20  not better
R1 Lev-trend QLD (QQQ>SMA200)                                33.6   1.01   -40.2     15  not better
R1 Lev-trend UPRO (SPY>SMA200)                               26.8   0.83   -51.3     20  not better
R1 Lev-trend TQQQ (QQQ>SMA200)                               46.2   1.00   -54.9     15  not better
R2 GEM dual momentum (SPY/EFA/AGG)                            9.9   0.63   -33.7     14  not better
R3 Sector momentum top-3 (6-mo, monthly)                      9.4   0.58   -31.5     79  not better
R4 MR-ensemble IBS|RSI2 (17 ETFs)                            14.2   1.15   -10.7   4138  not better
R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM)       31.7   1.22   -27.6    935  BETTER
R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED)           43.9   1.17   -39.4    243  not better
```

Per-hypothesis tables (source: `round2.txt`):

| hypothesis | window | fill | trades | win% | avg_trade% | PF | CAGR% | Sharpe | maxDD% | pos_years |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 Lev-trend SSO (SPY>SMA200) | IS 2010-18 | close | 24 | 20.83 | 6.98 | 3.67 | 12.09 | 0.61 | -37.75 | 5/9 |
| R1 Lev-trend SSO (SPY>SMA200) | IS 2010-18 | next_open | 24 | 25.00 | 6.52 | 3.13 | 10.02 | 0.53 | -43.22 | 5/9 |
| R1 Lev-trend SSO (SPY>SMA200) | OOS 2019-26 | close | 20 | 30.00 | 8.53 | 5.02 | 19.81 | 0.86 | -37.47 | 7/8 |
| R1 Lev-trend SSO (SPY>SMA200) | OOS 2019-26 | next_open | 20 | 40.00 | 7.80 | 4.65 | 18.56 | 0.81 | -36.14 | 7/8 |
| R1 Lev-trend QLD (QQQ>SMA200) | IS 2010-18 | close | 34 | 14.71 | 7.12 | 3.42 | 13.80 | 0.60 | -42.63 | 5/9 |
| R1 Lev-trend QLD (QQQ>SMA200) | IS 2010-18 | next_open | 34 | 17.65 | 6.08 | 2.96 | 11.23 | 0.51 | -46.90 | 4/9 |
| R1 Lev-trend QLD (QQQ>SMA200) | OOS 2019-26 | close | 15 | 33.33 | 22.14 | 8.33 | 33.64 | 1.01 | -40.24 | 7/8 |
| R1 Lev-trend QLD (QQQ>SMA200) | OOS 2019-26 | next_open | 15 | 26.67 | 20.95 | 6.50 | 31.70 | 0.96 | -42.45 | 7/8 |
| R1 Lev-trend UPRO (SPY>SMA200) | IS 2010-18 | close | 24 | 20.83 | 11.52 | 3.93 | 16.46 | 0.62 | -51.46 | 5/9 |
| R1 Lev-trend UPRO (SPY>SMA200) | IS 2010-18 | next_open | 24 | 25.00 | 10.80 | 3.34 | 12.89 | 0.53 | -58.07 | 5/9 |
| R1 Lev-trend UPRO (SPY>SMA200) | OOS 2019-26 | close | 20 | 25.00 | 13.44 | 5.14 | 26.81 | 0.83 | -51.26 | 7/8 |
| R1 Lev-trend UPRO (SPY>SMA200) | OOS 2019-26 | next_open | 20 | 40.00 | 12.04 | 4.68 | 24.85 | 0.79 | -49.91 | 7/8 |
| R1 Lev-trend TQQQ (QQQ>SMA200) | IS 2010-18 | close | 35 | 17.14 | 12.50 | 3.97 | 21.24 | 0.67 | -55.58 | 5/9 |
| R1 Lev-trend TQQQ (QQQ>SMA200) | IS 2010-18 | next_open | 35 | 17.14 | 10.70 | 3.30 | 17.52 | 0.59 | -59.63 | 5/9 |
| R1 Lev-trend TQQQ (QQQ>SMA200) | OOS 2019-26 | close | 15 | 26.67 | 37.07 | 9.13 | 46.15 | 1.00 | -54.86 | 7/8 |
| R1 Lev-trend TQQQ (QQQ>SMA200) | OOS 2019-26 | next_open | 15 | 26.67 | 34.54 | 7.00 | 42.59 | 0.95 | -57.71 | 7/8 |
| R2 GEM dual momentum (SPY/EFA/AGG) | IS 2010-18 | close | 16 | 75.00 | 4.20 | 3.79 | 6.43 | 0.49 | -19.97 | 7/9 |
| R2 GEM dual momentum (SPY/EFA/AGG) | IS 2010-18 | next_open | 13 | 53.85 | 5.42 | 4.20 | 6.77 | 0.55 | -19.35 | 7/9 |
| R2 GEM dual momentum (SPY/EFA/AGG) | OOS 2019-26 | close | 14 | 57.14 | 5.82 | 6.36 | 9.87 | 0.63 | -33.72 | 7/8 |
| R2 GEM dual momentum (SPY/EFA/AGG) | OOS 2019-26 | next_open | 13 | 38.46 | 4.41 | 4.09 | 6.44 | 0.46 | -33.72 | 6/8 |
| R3 Sector momentum top-3 (6-mo, monthly) | IS 2010-18 | close | 94 | 67.02 | 2.87 | 3.12 | 7.78 | 0.60 | -21.55 | 6/9 |
| R3 Sector momentum top-3 (6-mo, monthly) | IS 2010-18 | next_open | 90 | 60.00 | 2.42 | 2.47 | 3.73 | 0.37 | -21.58 | 5/9 |
| R3 Sector momentum top-3 (6-mo, monthly) | OOS 2019-26 | close | 79 | 53.16 | 2.59 | 2.13 | 9.35 | 0.58 | -31.48 | 7/8 |
| R3 Sector momentum top-3 (6-mo, monthly) | OOS 2019-26 | next_open | 74 | 59.46 | 2.62 | 2.06 | 8.41 | 0.59 | -30.64 | 6/8 |
| R4 MR-ensemble IBS|RSI2 (17 ETFs) | IS 2010-18 | close | 4508 | 67.17 | 0.17 | 1.39 | 8.54 | 0.87 | -15.28 | 8/9 |
| R4 MR-ensemble IBS|RSI2 (17 ETFs) | IS 2010-18 | next_open | 4326 | 62.64 | 0.13 | 1.29 | 5.67 | 0.63 | -12.73 | 8/9 |
| R4 MR-ensemble IBS|RSI2 (17 ETFs) | OOS 2019-26 | close | 4138 | 64.93 | 0.26 | 1.53 | 14.17 | 1.15 | -10.71 | 8/8 |
| R4 MR-ensemble IBS|RSI2 (17 ETFs) | OOS 2019-26 | next_open | 3928 | 61.71 | 0.21 | 1.40 | 9.82 | 0.88 | -21.81 | 7/8 |
| R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM) | IS 2010-18 | close | 981 | 67.69 | 0.49 | 1.46 | 12.68 | 0.69 | -31.84 | 7/9 |
| R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM) | IS 2010-18 | next_open | 970 | 63.92 | 0.42 | 1.38 | 10.61 | 0.60 | -30.73 | 8/9 |
| R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM) | OOS 2019-26 | close | 935 | 66.20 | 0.98 | 1.89 | 31.74 | 1.22 | -27.64 | 7/8 |
| R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM) | OOS 2019-26 | next_open | 918 | 63.83 | 0.73 | 1.60 | 21.73 | 0.92 | -36.85 | 7/8 |
| R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED) | IS 2010-18 | close | 314 | 59.87 | 9.09 | 3.53 | 28.18 | 1.15 | -35.04 | 9/9 |
| R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED) | IS 2010-18 | next_open | 236 | 56.78 | 10.17 | 3.25 | 20.76 | 0.99 | -33.58 | 9/9 |
| R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED) | OOS 2019-26 | close | 243 | 55.56 | 15.36 | 4.54 | 43.93 | 1.17 | -39.35 | 7/8 |
| R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED) | OOS 2019-26 | next_open | 196 | 51.53 | 17.17 | 4.72 | 40.04 | 1.15 | -37.83 | 6/8 |

- VERDICT R1 Lev-trend SSO (SPY>SMA200): NOT BETTER  CAGR>16.4=y, Sharpe>=1.0=N, DD<SPY=N, pos_years>=5/8=y, trades>=30=N, IS_positive=y, next_open_positive=y
- VERDICT R1 Lev-trend QLD (QQQ>SMA200): NOT BETTER  CAGR>16.4=y, Sharpe>=1.0=y, DD<SPY=N, pos_years>=5/8=y, trades>=30=N, IS_positive=y, next_open_positive=y
- VERDICT R1 Lev-trend UPRO (SPY>SMA200): NOT BETTER  CAGR>16.4=y, Sharpe>=1.0=N, DD<SPY=N, pos_years>=5/8=y, trades>=30=N, IS_positive=y, next_open_positive=y
- VERDICT R1 Lev-trend TQQQ (QQQ>SMA200): NOT BETTER  CAGR>16.4=y, Sharpe>=1.0=N, DD<SPY=N, pos_years>=5/8=y, trades>=30=N, IS_positive=y, next_open_positive=y
- VERDICT R2 GEM dual momentum (SPY/EFA/AGG): NOT BETTER  CAGR>16.4=N, Sharpe>=1.0=N, DD<SPY=N, pos_years>=5/8=y, trades>=30=N, IS_positive=y, next_open_positive=y
- VERDICT R3 Sector momentum top-3 (6-mo, monthly): NOT BETTER  CAGR>16.4=N, Sharpe>=1.0=N, DD<SPY=y, pos_years>=5/8=y, trades>=30=y, IS_positive=y, next_open_positive=y
- VERDICT R4 MR-ensemble IBS|RSI2 (17 ETFs): NOT BETTER  CAGR>16.4=N, Sharpe>=1.0=y, DD<SPY=y, pos_years>=5/8=y, trades>=100=y, IS_positive=y, next_open_positive=y
- VERDICT R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM): BETTER  CAGR>16.4=y, Sharpe>=1.0=y, DD<SPY=y, pos_years>=5/8=y, trades>=100=y, IS_positive=y, next_open_positive=y
- VERDICT R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED): NOT BETTER  CAGR>16.4=y, Sharpe>=1.0=y, DD<SPY=N, pos_years>=5/8=y, trades>=30=y, IS_positive=y, next_open_positive=y

Only R5 clears the literal bar. R1 (leverage-trend) fails on drawdown (-37% to -55%, worse than
SPY) and on trade count (15-20 trades); R2/R3 do not reach the CAGR bar; R4 is a strictly
weaker ensemble of the winner; R6 has a worse drawdown than SPY and is survivorship-biased.

## 6. Robustness of IBS-ETF

17 ETFs, 10 slots, OOS 2019-26. Source: `ibs_robust.txt` (2026-10-04 re-run).

Cost per side x1/x2/x4 (0.02% / 0.04% / 0.08%):

| cost/side | fill | CAGR | Sharpe | maxDD | PF |
|---|---|---|---|---|---|
| 0.02% (x1) | close | 16.5% | 1.30 | -11.2% | 1.63 |
| 0.02% (x1) | next_open | 12.6% | 1.07 | -21.7% | 1.47 |
| 0.04% (x2) | close | 14.6% | 1.17 | -12.0% | 1.55 |
| 0.04% (x2) | next_open | 11.0% | 0.95 | -21.9% | 1.41 |
| 0.08% (x4) | close | 10.9% | 0.90 | -13.9% | 1.40 |
| 0.08% (x4) | next_open | 7.7% | 0.69 | -22.2% | 1.28 |

Edge survives a 4x cost assumption on both fills. (Authoring-session run, same table:
0.02% close 16.4/1.30/-11.2/PF 1.62, next-open 12.7/1.07/-21.7/1.47; 0.04% close
14.6/1.17/-12.0/1.55, next-open 10.9/0.94/-21.9/1.40; 0.08% close 10.9/0.90/-13.9/1.40,
next-open 7.6/0.68/-22.2/1.27.)

Yearly OOS return (next-open fill, re-run): 2019 +17.1%, 2020 +38.3%, 2021 +22.8%,
**2022 +3.2%, 2023 +2.2%, 2024 -2.1%**, 2025 +17.2%, 2026 YTD +4.3%
(authoring session: 2019 +16.5%, 2022 +4.0%). The three flat years 2022-2024 are the strategy's
known weak stretch: IBS mean-reversion earned little in the 2022-24 regime.

Leave-one-out (next-open, OOS, re-run): CAGR 11.7%-12.9%, Sharpe 0.98-1.08, PF 1.46-1.52 for
every dropped ETF (worst: without GLD CAGR 11.7% / Sharpe 0.98; best: without XLP 12.9% /
1.08) — no single-ETF dependence.

Per-ETF standalone (1 slot, next-open, OOS), PF / trades / CAGR:

```
per-ETF standalone (single slot, next_open, OOS PF / trades):
  SPY  PF 1.61 trades 225 CAGR 8.8%
  QQQ  PF 1.86 trades 232 CAGR 16.0%
  IWM  PF 1.55 trades 233 CAGR 12.0%
  DIA  PF 1.81 trades 228 CAGR 11.6%
  XLK  PF 1.85 trades 247 CAGR 17.4%
  XLF  PF 1.83 trades 232 CAGR 15.4%
  XLE  PF 1.23 trades 220 CAGR 5.7%
  XLV  PF 1.52 trades 213 CAGR 7.6%
  XLI  PF 1.81 trades 236 CAGR 14.6%
  XLY  PF 1.74 trades 247 CAGR 15.7%
  XLP  PF 1.47 trades 219 CAGR 5.9%
  XLU  PF 1.17 trades 219 CAGR 2.8%
  XLB  PF 1.72 trades 230 CAGR 13.7%
  TLT  PF 1.15 trades 224 CAGR 1.8%
  GLD  PF 1.57 trades 192 CAGR 8.6%
  EFA  PF 1.28 trades 249 CAGR 4.7%
  EEM  PF 1.26 trades 239 CAGR 4.9%
```

All 17 ETFs have PF > 1 standalone; the weakest are TLT (1.15), XLU (1.17), XLE (1.23), EEM
(1.26), EFA (1.28).

## 7. Sizing

Source: `ibs_sizing.txt` (re-run). 1x = unlevered 17 ETFs; "mixed" = the four index ETFs
(SPY/QQQ/IWM/DIA) traded as their 2x counterparts (SSO/QLD/UWM/DDM).

```
IBS-17 1x  10 slots (10%)  close             OOS CAGR  16.5% Sharpe 1.30 maxDD  -11.2% | IS CAGR   9.1% Sharpe 0.90 maxDD  -15.3%
IBS-17 1x  7 slots (14%)  close              OOS CAGR  15.3% Sharpe 1.18 maxDD  -10.5% | IS CAGR   9.6% Sharpe 0.91 maxDD  -15.4%
IBS-17 1x  5 slots (20%)  close              OOS CAGR  15.8% Sharpe 1.14 maxDD  -11.8% | IS CAGR  10.3% Sharpe 0.94 maxDD  -16.9%
IBS-17 mixed (4 index ETFs as 2x) 10 slots close     OOS CAGR  20.0% Sharpe 1.22 maxDD  -17.3% | IS CAGR  10.0% Sharpe 0.80 maxDD  -20.4%
IBS-17 mixed (4 index ETFs as 2x) 7 slots  close     OOS CAGR  19.0% Sharpe 1.15 maxDD  -16.6% | IS CAGR  10.8% Sharpe 0.83 maxDD  -17.7%

IBS-17 1x  10 slots (10%)  next_open         OOS CAGR  12.8% Sharpe 1.09 maxDD  -21.7% | IS CAGR   6.3% Sharpe 0.67 maxDD  -16.1%
IBS-17 1x  7 slots (14%)  next_open          OOS CAGR  12.2% Sharpe 1.02 maxDD  -21.1% | IS CAGR   5.9% Sharpe 0.62 maxDD  -17.1%
IBS-17 1x  5 slots (20%)  next_open          OOS CAGR  13.7% Sharpe 1.08 maxDD  -21.3% | IS CAGR   6.4% Sharpe 0.65 maxDD  -16.1%
IBS-17 mixed (4 index ETFs as 2x) 10 slots next_open OOS CAGR  14.5% Sharpe 0.95 maxDD  -28.1% | IS CAGR   7.0% Sharpe 0.60 maxDD  -20.1%
IBS-17 mixed (4 index ETFs as 2x) 7 slots  next_open OOS CAGR  14.3% Sharpe 0.96 maxDD  -25.5% | IS CAGR   6.7% Sharpe 0.57 maxDD  -18.4%
```

R5 robustness (`r5_robust.txt`) — IBS signal on four index ETFs, 2x instruments:

```
--- OOS 2019-26 ---
R5 IBS-2x cost 0.04%/side close                CAGR  31.7%  Sharpe 1.22  maxDD  -27.6%  PF 1.89  trades 935
R5 IBS-2x cost 0.04%/side next_open            CAGR  21.7%  Sharpe 0.92  maxDD  -36.9%  PF 1.60  trades 918
R5 IBS-2x cost 0.10%/side close                CAGR  27.1%  Sharpe 1.08  maxDD  -29.3%  PF 1.75  trades 935
R5 IBS-2x cost 0.10%/side next_open            CAGR  17.5%  Sharpe 0.77  maxDD  -37.2%  PF 1.49  trades 918
R5 IBS-2x cost 0.20%/side close                CAGR  19.6%  Sharpe 0.84  maxDD  -35.2%  PF 1.54  trades 935
R5 IBS-2x cost 0.20%/side next_open            CAGR  10.7%  Sharpe 0.54  maxDD  -39.5%  PF 1.31  trades 918

IBS-1x same 4 ETFs (unlevered) close           CAGR  16.7%  Sharpe 1.29  maxDD  -13.6%  PF 1.99  trades 935
IBS-1x same 4 ETFs (unlevered) next_open       CAGR  12.3%  Sharpe 1.00  maxDD  -18.9%  PF 1.70  trades 918

--- per instrument, 1 slot, close fill, OOS ---
  SSO (signal SPY)                             CAGR  32.0%  Sharpe 1.20  maxDD  -24.5%  PF 2.12  trades 229
  QLD (signal QQQ)                             CAGR  48.9%  Sharpe 1.40  maxDD  -23.2%  PF 2.31  trades 236
  UWM (signal IWM)                             CAGR  20.6%  Sharpe 0.75  maxDD  -50.9%  PF 1.49  trades 237
  DDM (signal DIA)                             CAGR  23.3%  Sharpe 0.94  maxDD  -22.7%  PF 1.82  trades 233

R5 yearly OOS (close): {2019: 16.0, 2020: 162.6, 2021: 23.7, 2022: 45.8, 2023: 21.2, 2024: -7.9, 2025: 25.3, 2026: 10.2}
R5 yearly OOS (next_open): {2019: 21.6, 2020: 78.2, 2021: 37.9, 2022: 27.5, 2023: 8.8, 2024: -13.0, 2025: 25.9, 2026: 1.1}
R5 mean invested fraction OOS: 0.39; days with any position: 0.60; mean hold 4.6 cal days
R5 worst 5 trades OOS: [-0.176, -0.171, -0.154, -0.154, -0.151]
R5 worst OOS drawdown window: (datetime.date(2025, 4, 8), datetime.date(2024, 7, 16))
```

R5 is the IBS signal with leverage (same 935 trades as the unlevered rule on the same four
ETFs: 16.7% CAGR / Sharpe 1.29 / maxDD -13.6% unlevered vs 31.7% / 1.22 / -27.6% levered, close
fill). Leverage doubled the return, raised the drawdown 2-2.5x, lowered the
Sharpe, and carries decay and a -50.9% standalone UWM drawdown. **OD-1: unlevered IBS-17,
10 slots x 10%.** 7 and 5 slots give no better risk-adjusted result than 10.

## 8. Trend Join Long screen study (2026-10-03)

`screen_study.py` measured whether TJL's premarket screen (D1 & D2 & D3: gap >= 3% etc.) selects
stocks with intraday excess return. Outputs copied unchanged to the assets directory.

**Part 1 — daily bars (500 symbols, 804 sessions 2023-07-21..2026-10-02; open->close,
market-demeaned excess return, day-clustered bootstrap CI)** (`part1_daily.csv`):

| group | n | days | mean_ret_% | median_ret_% | win_% | mean_excess_% | excess_CI95_% |
|---|---|---|---|---|---|---|---|
| all stock-days | 399844 | 804 | 0.010 | 0.020 | 50.479 | -0.000 | [-0.00, +0.00] |
| gap>=3% (D3 only) | 5960 | 716 | -0.301 | -0.272 | 45.805 | -0.095 | [-0.28, +0.09] |
| D3 & D1 | 5184 | 696 | -0.214 | -0.238 | 46.277 | -0.097 | [-0.29, +0.08] |
| SCREEN D1&D2&D3 | 3111 | 619 | -0.012 | -0.064 | 48.505 | 0.046 | [-0.17, +0.25] |
| D3 & D1 & NOT D2 | 2073 | 496 | -0.518 | -0.468 | 42.933 | -0.310 | [-0.55, -0.07] |
| gap<=-3% (contrast) | 5283 | 674 | 0.029 | 0.068 | 50.767 | 0.155 | [-0.20, +0.50] |

Gap-bucket sensitivity (D1 & D2 held; `part1_gap_buckets.csv`):

| group | n | days | mean_ret_% | median_ret_% | win_% | mean_excess_% | excess_CI95_% |
|---|---|---|---|---|---|---|---|
| 1-2% | 9561 | 755 | -0.060 | -0.097 | 47.464 | -0.005 | [-0.07, +0.06] |
| 2-3% | 2864 | 612 | -0.128 | -0.148 | 47.486 | -0.053 | [-0.20, +0.09] |
| 3-5% | 1893 | 521 | -0.090 | -0.016 | 49.287 | -0.016 | [-0.22, +0.19] |
| 5-8% | 824 | 363 | 0.005 | -0.191 | 45.874 | 0.035 | [-0.34, +0.40] |
| 8%+ | 394 | 224 | 0.325 | 0.083 | 50.254 | 0.362 | [-0.23, +0.94] |

**Part 2 — cached Massive 5m bars incl. premarket, live-exact 08:30 screen, 10:05 -> 15:50
hold (301 symbols, 713 sessions, 60,902 symbol-days; selection-biased universe)**
(`part2_5m_hold.csv`):

| group | n | days | mean_ret_% | median_ret_% | win_% | mean_excess_% | excess_CI95_% |
|---|---|---|---|---|---|---|---|
| all cached stock-days | 60902 | 713 | -0.003 | 0.019 | 50.555 | 0.000 | [-0.00, +0.00] |
| |gap|<1% (quiet) | 43627 | 712 | -0.014 | 0.010 | 50.297 | -0.000 | [-0.02, +0.02] |
| gap>=3% (D3 only) | 2159 | 465 | -0.074 | 0.034 | 50.625 | -0.021 | [-0.19, +0.15] |
| SCREEN D1&D2&D3 @08:30 | 1341 | 369 | -0.097 | 0.036 | 50.708 | -0.047 | [-0.30, +0.18] |
| D3 & D1 & NOT D2 | 489 | 245 | -0.029 | -0.030 | 48.875 | 0.016 | [-0.24, +0.27] |

**Conclusion:** the D1&D2&D3 screen carries no intraday excess return. Daily: mean excess
+0.046% with a 95% CI of [-0.17, +0.25]; 5m live-exact hold: -0.047% with CI [-0.30, +0.18]
— both intervals span zero. Funnel notes from the session: mean 3.87 candidates per session,
23.0% of sessions have zero candidates, rank-1 candidates average +0.150% open->close vs
-0.097% for the rest. (These funnel/rank/path lines are from the session evidence, not from
the committed CSVs.) This is the OD-4 evidence.

## 9. Reference numbers adopted for Phase 12

OOS 2019-01-01..2026-10-02, net of 0.02%/side, 17 ETFs, 10 slots (authoring-session run):

| fill | CAGR | Sharpe | maxDD | PF | trades | positive years |
|---|---|---|---|---|---|---|
| close fill (target) | 16.4% | 1.30 | -11.2% | 1.63 | 3164 | 7/8 |
| next-open fill (conservative floor) | 12.7% | 1.07 | -21.7% | 1.47 | 3106 | 7/8 |

In-sample 2010-2018 (close fill): CAGR 9.1%, Sharpe 0.91 (next-open: 6.3% / 0.67). SPY buy &
hold OOS: 17.3% CAGR / 0.93 Sharpe / -33.7% maxDD. Flat years (next-open): 2022 +4%, 2023
+2%, 2024 -2%.

2026-10-04 re-run vs authoring session (same scripts, yfinance data pulled one day later;
adjusted prices drift with dividends, so a handful of marginal signals flip):

| metric | authoring session | 2026-10-04 re-run |
|---|---|---|
| S3 OOS close: trades / PF / CAGR / Sharpe / maxDD | 3164 / 1.63 / 16.48 / 1.30 / -11.18 | 3162 / 1.62 / 16.45 / 1.30 / -11.18 |
| S3 OOS next-open: trades / PF / CAGR / Sharpe / maxDD | 3106 / 1.47 / 12.66 / 1.07 / -21.69 | 3106 / 1.47 / 12.72 / 1.08 / -21.69 |
| S3 IS close: CAGR / Sharpe | 9.12 / 0.91 | 9.13 / 0.91 |
| SPY OOS | 17.32 / 0.93 / -33.72 | 17.32 / 0.93 / -33.72 |

The deltas are within +-0.1 percentage point of CAGR and two trades; no conclusion changes. The
round-2 summary table prints the round-1 baseline row with the authoring-session values (3164
trades) — it is a hardcoded reference row, not a re-computation.

## 10. Limitations

- **Survivorship bias** for the stock universes (S1, R6): the S&P 500 list is today's
  constituents, so those results overstate. The adopted ETF strategy (S3) is not affected.
- **Backtest != live.** Daily-bar research with a flat 0.02%/side cost; no partial fills, no
  queue position, no halts.
- **"Close fill" assumes a decision at the close.** Production decides from the 15:50 ET
  snapshot `last_price` (close - 10 minutes) and then sends marketable limit orders; the
  day's high/low/last at 15:50 differ from the final bar. Treat the next-open column as the
  conservative floor (12.7% CAGR, -21.7% maxDD) and the close-fill column as the target.
- **Cost assumption**: 0.02%/side (ETFs) vs production's flat-dollar marketable-limit buffers;
  Section 6 shows the edge persists to 4x cost but CAGR falls to ~10.9% (close fill).
- **Sizing**: the research sim compounds fractional shares from running equity; production uses
  a fixed `risk.sizing_equity_usd` x `position_pct_of_equity` with whole shares.
- **Same-day re-entry**: production forbids a same-day re-entry after a time exit
  (orchestrator ruling 3); the research sim may re-buy. Plan 04's parity test documents this
  divergence.
- **Flat stretch**: 2022 +4%, 2023 +2%, 2024 -2% — mean reversion on these ETFs was
  largely flat for three years; expect such regimes again.
- **Selection**: ~16 pre-registered tests were run across two rounds; the bar was raised in
  round 2 but multiple-comparison risk is not zero. IS (9.1% CAGR) is materially lower than OOS
  (16.4%).
- The Part 2 screen study uses a selection-biased cached-universe (301 symbols).

## 11. Reproduction

From the repository root (needs network for yfinance and `data/sp500_*.csv`; `screen_study.py`
also needs the Massive 5m cache under `backtester/cache/massive/`):

```
mkdir -p docs/research/assets/2026-10-04-ibs-etf-strategy-search backtester/results/ibs_search
python3 backtester/experimental/ibs_search/strategy_search.py    > docs/research/assets/2026-10-04-ibs-etf-strategy-search/round1.txt
python3 backtester/experimental/ibs_search/strategy_search_r2.py > docs/research/assets/2026-10-04-ibs-etf-strategy-search/round2.txt
python3 backtester/experimental/ibs_search/ibs_robust.py         > docs/research/assets/2026-10-04-ibs-etf-strategy-search/ibs_robust.txt
python3 backtester/experimental/ibs_search/ibs_sizing.py         > docs/research/assets/2026-10-04-ibs-etf-strategy-search/ibs_sizing.txt
python3 backtester/experimental/ibs_search/r5_robust.py         > docs/research/assets/2026-10-04-ibs-etf-strategy-search/r5_robust.txt
python3 backtester/experimental/ibs_search/screen_study.py       # writes backtester/results/ibs_search/part*.csv (~2 min)
```

Outputs written by the scripts at runtime go to `backtester/results/ibs_search/` (gitignored).
Results will drift slightly with the last trading day included and with yfinance's dividend
adjustments (Section 9).
