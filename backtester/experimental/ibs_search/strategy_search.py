#!/usr/bin/env python3
"""Pre-registered strategy search (daily bars, long-only, net of costs).

Prior work REJECTED every intraday family tried (TJL, ORB, VWAP pullback, Ext#2) and
concluded any revival needs a structurally different horizon (overnight / pullback).
So every candidate here is a multi-day or overnight hold, all from published,
well-known anomalies -- no parameter tuning (rules fixed before the first run).

Hypotheses (rules fixed up front):
  S1 RSI2-stocks : S&P 500 stock, close>SMA200 & RSI(2)<10 -> buy; exit close>SMA5 or 10d.
                   max 10 positions x 10% equity, lowest RSI2 first.
  S2 RSI2-ETF    : same rules on a liquid ETF universe (no survivorship bias).
  S3 IBS-ETF     : IBS=(C-L)/(H-L) < 0.2 -> buy; exit IBS > 0.8 or 10d. ETF universe.
  S4 Overnight   : SPY / QQQ, buy every close, sell next open.
  S5 TOM-SPY     : turn of month -- buy close of the 4th-last trading day, sell close
                   of the 3rd trading day of the next month.

Pass criteria (ALL required, judged on OOS 2019-01-01..latest, net of costs):
  - profit factor >= 1.2 and >= 100 trades (S4/S5: >= 50 trades)
  - positive in >= 5 of the 8 OOS calendar years
  - Sharpe >= 0.5 and max drawdown shallower than SPY buy&hold over the same window
  - IS (2010-2018) also net positive (no sign flip)
  - next-open execution variant also net positive (robust to fill timing)
"""
import glob
import os
import numpy as np, pandas as pd, yfinance as yf

START, IS_END, OOS_START = "2008-01-01", "2018-12-31", "2019-01-01"
COST_STOCK, COST_ETF = 0.0006, 0.0002          # per side (slippage + commission)
ETFS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP",
        "XLU", "XLB", "TLT", "GLD", "EFA", "EEM"]


def load(syms):
    raw = yf.download(syms, start=START, interval="1d", group_by="ticker",
                      auto_adjust=True, threads=8, progress=False)
    out = {}
    for f in ("Open", "High", "Low", "Close"):
        out[f] = pd.DataFrame({s: raw[s][f] for s in syms if s in raw.columns.get_level_values(0)}).ffill()
    return out


def rsi(c, n=2):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def simulate(px, entry, exit_, rank, cost, slots=10, max_hold=10, next_open=False):
    """Daily portfolio sim. Signals at close t; fill at close t (or open t+1)."""
    C, O = px["Close"], px["Open"]
    dates, syms = C.index, C.columns
    Cv, Ov = C.to_numpy(), O.to_numpy()
    E, X, R = entry.to_numpy(), exit_.to_numpy(), rank.to_numpy()
    cash, pos, trades, eq = 1.0, {}, [], np.empty(len(dates))
    pend_x, pend_e = [], []
    for t in range(len(dates)):
        if next_open:                                   # execute yesterday's decisions
            for j in pend_x:
                sh, ep, d0 = pos.pop(j); p = Ov[t, j] * (1 - cost)
                cash += sh * p; trades.append((dates[d0], dates[t], p / ep - 1))
            for j, alloc in pend_e:
                p = Ov[t, j] * (1 + cost)
                if np.isfinite(p) and p > 0 and j not in pos:
                    sh = alloc / p; cash -= alloc; pos[j] = (sh, p, t)
            pend_x, pend_e = [], []
        mtm = cash + sum(sh * Cv[t, j] for j, (sh, _, _) in pos.items() if np.isfinite(Cv[t, j]))
        eq[t] = mtm
        exits = [j for j, (_, _, d0) in pos.items() if X[t, j] or t - d0 >= max_hold]
        if next_open:
            pend_x = exits
        else:
            for j in exits:
                sh, ep, d0 = pos.pop(j); p = Cv[t, j] * (1 - cost)
                cash += sh * p; trades.append((dates[d0], dates[t], p / ep - 1))
        free = slots - (len(pos) - (len(exits) if next_open else 0))
        if free <= 0:
            continue
        cand = [j for j in np.where(E[t])[0] if j not in pos and np.isfinite(Cv[t, j])]
        cand.sort(key=lambda j: R[t, j])
        avail = cash
        for j in cand[:free]:
            alloc = min(mtm / slots, avail)
            if alloc <= 0:
                break
            avail -= alloc
            if next_open:
                pend_e.append((j, alloc))  # cash debited at fill
            else:
                p = Cv[t, j] * (1 + cost); sh = alloc / p
                cash -= alloc; pos[j] = (sh, p, t)
    return pd.Series(eq, index=dates), pd.DataFrame(trades, columns=["entry", "exit", "ret"])


def stats(eq, tr, lo, hi):
    e = eq[lo:hi]; e = e / e.iloc[0]
    r = e.pct_change().dropna()
    t = tr[(tr["entry"] >= lo) & (tr["entry"] <= hi)]
    g, l = t.loc[t.ret > 0, "ret"].sum(), -t.loc[t.ret < 0, "ret"].sum()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    yearly = e.resample("YE").last().pct_change()
    yearly.iloc[0] = e.resample("YE").last().iloc[0] - 1
    return {
        "trades": len(t), "win%": 100 * (t.ret > 0).mean() if len(t) else np.nan,
        "avg_trade%": 100 * t.ret.mean() if len(t) else np.nan,
        "PF": g / l if l > 0 else np.inf,
        "CAGR%": 100 * (e.iloc[-1] ** (1 / yrs) - 1),
        "Sharpe": np.sqrt(252) * r.mean() / r.std() if r.std() > 0 else np.nan,
        "maxDD%": 100 * (e / e.cummax() - 1).min(),
        "pos_years": f"{(yearly > 0).sum()}/{len(yearly)}",
    }


def report(name, eq_c, tr_c, eq_o, tr_o, spy_eq):
    rows = []
    for label, lo, hi in (("IS 2010-18", "2010-01-01", IS_END), ("OOS 2019-26", OOS_START, "2100")):
        s = stats(eq_c, tr_c, lo, hi); s.update(window=label, fill="close")
        so = stats(eq_o, tr_o, lo, hi); so.update(window=label, fill="next_open")
        b = stats(spy_eq, tr_c.iloc[:0], lo, hi)
        s["SPY_maxDD%"] = so["SPY_maxDD%"] = b["maxDD%"]
        s["SPY_Sharpe"] = so["SPY_Sharpe"] = b["Sharpe"]
        rows += [s, so]
    df = pd.DataFrame(rows).set_index(["window", "fill"])
    print(f"\n### {name}\n{df.round(2).to_string()}")
    return df


def verdict(name, df, min_trades=100):
    o, oo, i = df.loc[("OOS 2019-26", "close")], df.loc[("OOS 2019-26", "next_open")], df.loc[("IS 2010-18", "close")]
    py, ny = map(int, o["pos_years"].split("/"))
    checks = {
        "PF>=1.2": o["PF"] >= 1.2, f"trades>={min_trades}": o["trades"] >= min_trades,
        "pos_years>=5/8": py >= 5, "Sharpe>=0.5": o["Sharpe"] >= 0.5,
        "DD<SPY": o["maxDD%"] > o["SPY_maxDD%"], "IS_positive": i["CAGR%"] > 0,
        "next_open_positive": oo["CAGR%"] > 0,
    }
    ok = all(checks.values())
    print(f"VERDICT {name}: {'PASS' if ok else 'FAIL'}  " +
          ", ".join(f"{k}={'y' if v else 'N'}" for k, v in checks.items()))
    return ok


# ------------------------------------------------------------------ data
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
etf = load(ETFS)
spy_eq = etf["Close"]["SPY"].dropna()
results = {}

# S2 / S3 on ETFs
C, H, L = etf["Close"], etf["High"], etf["Low"]
r2 = rsi(C)
for name, ent, ex, rank in (
    ("S2 RSI2-ETF", (C > C.rolling(200).mean()) & (r2 < 10), C > C.rolling(5).mean(), r2),
    ("S3 IBS-ETF", ((C - L) / (H - L)) < 0.2, ((C - L) / (H - L)) > 0.8, (C - L) / (H - L)),
):
    ec, tc = simulate(etf, ent.fillna(False), ex.fillna(False), rank.fillna(99), COST_ETF)
    eo, to = simulate(etf, ent.fillna(False), ex.fillna(False), rank.fillna(99), COST_ETF, next_open=True)
    results[name] = verdict(name, report(name, ec, tc, eo, to, spy_eq))

# S4 overnight, S5 turn-of-month (single-asset, full allocation)
for sym in ("SPY", "QQQ"):
    c, o = etf["Close"][sym].dropna(), etf["Open"][sym].dropna()
    on = (o.shift(-1) * (1 - COST_ETF)) / (c * (1 + COST_ETF)) - 1
    eq = (1 + on.fillna(0)).cumprod()
    tr = pd.DataFrame({"entry": on.index, "exit": on.index, "ret": on.values}).dropna()
    nm = f"S4 Overnight-{sym}"
    results[nm] = verdict(nm, report(nm, eq, tr, eq, tr, spy_eq), min_trades=50)

c = etf["Close"]["SPY"].dropna()
ym = c.index.to_period("M")
pos_in_m = c.groupby(ym).cumcount()
n_in_m = c.groupby(ym).transform("size")
from_end = n_in_m - pos_in_m                       # 1 == last trading day
entry_d = c.index[from_end == 4]
exit_d = c.index[pos_in_m == 2]
tr = []
for d in entry_d:
    x = exit_d[exit_d > d]
    if len(x):
        tr.append((d, x[0], c[x[0]] * (1 - COST_ETF) / (c[d] * (1 + COST_ETF)) - 1))
tom = pd.DataFrame(tr, columns=["entry", "exit", "ret"])
held = pd.Series(0.0, index=c.index)
for d0, d1, _ in tr:
    held[(c.index > d0) & (c.index <= d1)] = 1
daily = c.pct_change().fillna(0) * held
eq = (1 + daily).cumprod()
results["S5 TOM-SPY"] = verdict("S5 TOM-SPY", report("S5 TOM-SPY", eq, tom, eq, tom, spy_eq), min_trades=50)

# S1 on S&P 500 stocks (survivorship-biased: current constituents)
syms = pd.read_csv(sorted(glob.glob(f"{ROOT}/data/sp500_*.csv"))[-1])["symbol"].tolist()
stk = load(syms)
C = stk["Close"]
r2 = rsi(C)
ent = ((C > C.rolling(200).mean()) & (r2 < 10)).fillna(False)
ex = (C > C.rolling(5).mean()).fillna(False)
ec, tc = simulate(stk, ent, ex, r2.fillna(99), COST_STOCK)
eo, to = simulate(stk, ent, ex, r2.fillna(99), COST_STOCK, next_open=True)
results["S1 RSI2-stocks"] = verdict("S1 RSI2-stocks", report("S1 RSI2-stocks (SURVIVORSHIP-BIASED)", ec, tc, eo, to, spy_eq))
os.makedirs(os.path.join(ROOT, "backtester", "results", "ibs_search"), exist_ok=True)
tc.to_csv(os.path.join(ROOT, "backtester", "results", "ibs_search", "s1_trades_close.csv"), index=False)

print("\nSUMMARY:", results)
