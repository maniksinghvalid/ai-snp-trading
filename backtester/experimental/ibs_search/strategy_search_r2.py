#!/usr/bin/env python3
"""Round 2 pre-registered search: must BEAT IBS-ETF (round 1 winner), not just pass.

Round 1 (strategy_search.py) tested 6 hypotheses; IBS-ETF won (OOS CAGR 16.4% close
fill / 12.7% next-open, Sharpe 1.30 / 1.07, maxDD -11% / -22%). Round 2 adds 6 more
structurally different families (trend, rotation, leverage, ensemble). ~16 tests in
total now, so the bar is RAISED. Rules fixed before the first run; no tuning after.

Hypotheses:
  R1 Lev-trend  : hold the 2x/3x ETF while the underlying closes > SMA200, else cash.
                  (SPY->SSO, QQQ->QLD, SPY->UPRO, QQQ->TQQQ; Gayed 2016 "Leverage for
                  the Long Run".) Daily check at the close.
  R2 GEM        : Antonacci dual momentum. Monthly: SPY vs EFA by 12-mo return; hold the
                  winner if its 12-mo return beats BIL's, else AGG.
  R3 Sector-mom : Monthly: top-3 of the 9 sector SPDRs by 6-mo return, only those with a
                  positive 6-mo return; equal weight; rest cash. (Faber-style.)
  R4 MR-ensemble: union of the two round-1 mean-reversion signals on the 17-ETF universe:
                  enter if IBS<0.2 OR (RSI2<10 & C>SMA200); exit if IBS>0.8 OR C>SMA5 OR 10d.
  R5 IBS-2x     : IBS rules from round 1 computed on SPY/QQQ/IWM/DIA, but the 2x ETF
                  (SSO/QLD/UWM/DDM) is what is bought. 4 slots x 25%.
  R6 XS-momentum: S&P 500 stocks, 12-1 month momentum, top-10 equal weight, monthly,
                  all cash when SPY <= SMA200. SURVIVORSHIP-BIASED (current constituents).

"Better than IBS" criteria (ALL required, OOS 2019-01-01..latest, close fill, net):
  CAGR > 16.4%  AND  Sharpe >= 1.0  AND  maxDD shallower than SPY  AND  positive in
  >= 5 of 8 OOS years  AND  IS 2010-18 net positive  AND  next-open fill net positive
  AND trades >= 100 (daily strategies) / >= 30 (monthly strategies).
"""
import glob
import os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
prelude = open(os.path.join(HERE, "strategy_search.py")).read()
ns = {}
exec(prelude.split("# ------------------------------------------------------------------ data")[0], ns)
load, simulate, stats, report, rsi = ns["load"], ns["simulate"], ns["stats"], ns["report"], ns["rsi"]
IS_END, OOS_START = ns["IS_END"], ns["OOS_START"]
COST_ETF, COST_LEV, COST_STOCK = 0.0002, 0.0004, 0.0006
BIG = 10 ** 6
IBS_CAGR = 16.4


def verdict(name, df, min_trades=100):
    o = df.loc[("OOS 2019-26", "close")]
    oo = df.loc[("OOS 2019-26", "next_open")]
    i = df.loc[("IS 2010-18", "close")]
    py = int(o["pos_years"].split("/")[0])
    checks = {
        f"CAGR>{IBS_CAGR}": o["CAGR%"] > IBS_CAGR, "Sharpe>=1.0": o["Sharpe"] >= 1.0,
        "DD<SPY": o["maxDD%"] > o["SPY_maxDD%"], "pos_years>=5/8": py >= 5,
        f"trades>={min_trades}": o["trades"] >= min_trades,
        "IS_positive": i["CAGR%"] > 0, "next_open_positive": oo["CAGR%"] > 0,
    }
    ok = all(checks.values())
    print(f"VERDICT {name}: {'BETTER' if ok else 'NOT BETTER'}  " +
          ", ".join(f"{k}={'y' if v else 'N'}" for k, v in checks.items()))
    return ok, o


def both_fills(px, ent, ex, rank, cost, **kw):
    ec, tc = simulate(px, ent, ex, rank, cost, **kw)
    eo, to = simulate(px, ent, ex, rank, cost, next_open=True, **kw)
    return ec, tc, eo, to


def month_end_mask(idx):
    m = idx.to_period("M")
    return np.r_[m[1:] != m[:-1], True]


# ------------------------------------------------------------------ data
ETFS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP",
        "XLU", "XLB", "TLT", "GLD", "EFA", "EEM",
        "SSO", "QLD", "UWM", "DDM", "UPRO", "TQQQ", "AGG", "BIL"]
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB"]
MR17 = ETFS[:17]
etf = load(ETFS)
C, H, L = etf["Close"], etf["High"], etf["Low"]
spy_eq = C["SPY"].dropna()
F = lambda df: df.fillna(False).astype(bool)
sub = lambda cols: {k: v[cols] for k, v in etf.items()}
results = {}

# R1 leveraged trend
for under, lev in (("SPY", "SSO"), ("QQQ", "QLD"), ("SPY", "UPRO"), ("QQQ", "TQQQ")):
    state = (C[under] > C[under].rolling(200).mean()) & C[lev].notna()
    ent = F(pd.DataFrame({lev: state}))
    ex = F(pd.DataFrame({lev: ~state & C[lev].notna()}))
    rk = pd.DataFrame({lev: 0.0}, index=C.index)
    name = f"R1 Lev-trend {lev} ({under}>SMA200)"
    results[name] = verdict(name, report(name, *both_fills(sub([lev]), ent, ex, rk, COST_LEV, slots=1, max_hold=BIG), spy_eq), 30)

# R2 GEM
me = month_end_mask(C.index)
r12 = C / C.shift(252) - 1
ent = pd.DataFrame(False, index=C.index, columns=["SPY", "EFA", "AGG"])
ex = ent.copy()
for t in np.where(me)[0]:
    s, e, b = r12["SPY"].iloc[t], r12["EFA"].iloc[t], r12["BIL"].iloc[t]
    if np.isnan(s) or np.isnan(e) or np.isnan(b):
        continue
    risk = "SPY" if s >= e else "EFA"
    pick = risk if max(s, e) > b else "AGG"
    ent.iloc[t, ent.columns.get_loc(pick)] = True
    for other in ent.columns:
        if other != pick:
            ex.iloc[t, ex.columns.get_loc(other)] = True
rk = pd.DataFrame(0.0, index=C.index, columns=ent.columns)
name = "R2 GEM dual momentum (SPY/EFA/AGG)"
results[name] = verdict(name, report(name, *both_fills(sub(list(ent.columns)), ent, ex, rk, COST_ETF, slots=1, max_hold=BIG), spy_eq), 30)

# R3 sector momentum top-3
r6 = C[SECTORS] / C[SECTORS].shift(126) - 1
ent = pd.DataFrame(False, index=C.index, columns=SECTORS)
ex = ent.copy()
for t in np.where(me)[0]:
    row = r6.iloc[t].dropna()
    top = list(row[row > 0].sort_values(ascending=False).index[:3])
    for s in SECTORS:
        if s in top:
            ent.iloc[t, ent.columns.get_loc(s)] = True
        else:
            ex.iloc[t, ex.columns.get_loc(s)] = True
rk = -r6.fillna(-99)
name = "R3 Sector momentum top-3 (6-mo, monthly)"
results[name] = verdict(name, report(name, *both_fills(sub(SECTORS), ent, ex, rk, COST_ETF, slots=3, max_hold=BIG), spy_eq), 30)

# R4 mean-reversion ensemble on the 17 ETFs
c17, h17, l17 = C[MR17], H[MR17], L[MR17]
ibs = (c17 - l17) / (h17 - l17)
r2 = rsi(c17)
ent = F((ibs < 0.2) | ((r2 < 10) & (c17 > c17.rolling(200).mean())))
ex = F((ibs > 0.8) | (c17 > c17.rolling(5).mean()))
name = "R4 MR-ensemble IBS|RSI2 (17 ETFs)"
results[name] = verdict(name, report(name, *both_fills(sub(MR17), ent, ex, ibs.fillna(99), COST_ETF), spy_eq))

# R5 IBS signals on the index ETF, 2x ETF traded
pairs = {"SPY": "SSO", "QQQ": "QLD", "IWM": "UWM", "DIA": "DDM"}
u = list(pairs); lv = [pairs[k] for k in u]
ibs_u = ((C[u] - L[u]) / (H[u] - L[u])); ibs_u.columns = lv
ent = F((ibs_u < 0.2) & C[lv].notna().values)
ex = F(ibs_u > 0.8)
name = "R5 IBS-2x (signals SPY/QQQ/IWM/DIA -> SSO/QLD/UWM/DDM)"
results[name] = verdict(name, report(name, *both_fills(sub(lv), ent, ex, ibs_u.fillna(99), COST_LEV, slots=4), spy_eq))

# R6 cross-sectional 12-1 momentum, S&P 500 (survivorship-biased), SPY>SMA200 gate
syms = pd.read_csv(sorted(glob.glob(f"{ROOT}/data/sp500_*.csv"))[-1])["symbol"].tolist()
stk = load(syms)
CS = stk["Close"]
mom = CS.shift(21) / CS.shift(252) - 1
gate = (C["SPY"] > C["SPY"].rolling(200).mean()).reindex(CS.index).fillna(False)
me_s = month_end_mask(CS.index)
ent = pd.DataFrame(False, index=CS.index, columns=CS.columns)
ex = ent.copy()
for t in np.where(me_s)[0]:
    if not gate.iloc[t]:
        ex.iloc[t, :] = True
        continue
    row = mom.iloc[t].dropna()
    top = set(row.sort_values(ascending=False).index[:10])
    ent.iloc[t, [ent.columns.get_loc(s) for s in top]] = True
    ex.iloc[t, [ex.columns.get_loc(s) for s in CS.columns if s not in top]] = True
name = "R6 XS-momentum S&P500 top-10 (SURVIVORSHIP-BIASED)"
results[name] = verdict(name, report(name, *both_fills(stk, ent, ex, -mom.fillna(-99), COST_STOCK, slots=10, max_hold=BIG), spy_eq), 30)

print("\n================ ROUND 2 SUMMARY (OOS 2019-26, close fill) ================")
print(f"{'strategy':58s} {'CAGR%':>6s} {'Sharpe':>6s} {'maxDD%':>7s} {'trades':>6s}  verdict")
print(f"{'(round-1 winner) IBS-ETF 17 ETFs':58s} {'16.4':>6s} {'1.30':>6s} {'-11.2':>7s} {'3164':>6s}  baseline")
for name, (ok, o) in results.items():
    print(f"{name[:58]:58s} {o['CAGR%']:6.1f} {o['Sharpe']:6.2f} {o['maxDD%']:7.1f} {int(o['trades']):6d}  {'BETTER' if ok else 'not better'}")
