import os
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
src=open(os.path.join(HERE, "strategy_search.py")).read().split("# ------------------------------------------------------------------ data")[0]
ns={}; exec(src,ns); load,simulate,stats=ns["load"],ns["simulate"],ns["stats"]
pairs={"SPY":"SSO","QQQ":"QLD","IWM":"UWM","DIA":"DDM"}; u=list(pairs); lv=[pairs[k] for k in u]
etf=load(u+lv); C,H,L=etf["Close"],etf["High"],etf["Low"]
ibs=(C[u]-L[u])/(H[u]-L[u])
F=lambda d: d.fillna(False).astype(bool)
def run(cols_sig, cols_trade, cost, nxt, slots):
    s=ibs[cols_sig].copy(); s.columns=cols_trade
    px={k:v[cols_trade] for k,v in etf.items()}
    return simulate(px, F((s<0.2)&C[cols_trade].notna().values), F(s>0.8), s.fillna(99), cost, slots=slots, next_open=nxt)
S=lambda e,t,lo="2019-01-01",hi="2100": stats(e,t,lo,hi)
def line(tag,e,t):
    s=S(e,t); print(f"{tag:46s} CAGR {s['CAGR%']:5.1f}%  Sharpe {s['Sharpe']:.2f}  maxDD {s['maxDD%']:6.1f}%  PF {s['PF']:.2f}  trades {s['trades']}")
print("--- OOS 2019-26 ---")
for cost in (0.0004,0.001,0.002):
    for nxt in (False,True):
        e,t=run(u,lv,cost,nxt,4); line(f"R5 IBS-2x cost {cost*100:.2f}%/side {'next_open' if nxt else 'close'}",e,t)
print()
for nxt in (False,True):
    e,t=run(u,u,0.0002,nxt,4); line(f"IBS-1x same 4 ETFs (unlevered) {'next_open' if nxt else 'close'}",e,t)
print("\n--- per instrument, 1 slot, close fill, OOS ---")
for a,b in pairs.items():
    e,t=run([a],[b],0.0004,False,1); line(f"  {b} (signal {a})",e,t)
e,t=run(u,lv,0.0004,False,4)
eq=e["2019":]; yr=eq.resample("YE").last(); yr=yr.pct_change(); yr.iloc[0]=eq.resample("YE").last().iloc[0]/eq.iloc[0]-1
print("\nR5 yearly OOS (close):", {k.year:round(100*v,1) for k,v in yr.items()})
e2,t2=run(u,lv,0.0004,True,4); eq2=e2["2019":]; yr2=eq2.resample("YE").last().pct_change(); yr2.iloc[0]=eq2.resample("YE").last().iloc[0]/eq2.iloc[0]-1
print("R5 yearly OOS (next_open):", {k.year:round(100*v,1) for k,v in yr2.items()})
# exposure: fraction of days with >=1 position and mean invested fraction, via trades
days=pd.Series(0.0,index=eq.index)
for _,r in t[(t.entry>="2019-01-01")].iterrows(): days[(days.index>r.entry)&(days.index<=r.exit)]+=0.25
print(f"R5 mean invested fraction OOS: {days.mean():.2f}; days with any position: {(days>0).mean():.2f}; mean hold {(t.exit-t.entry).dt.days.mean():.1f} cal days")
print("R5 worst 5 trades OOS:", sorted(t[t.entry>='2019-01-01'].ret.round(3))[:5])
print("R5 worst OOS drawdown window:", (lambda d: (d.idxmin().date(), (eq.loc[:d.idxmin()].idxmax()).date()))(eq/eq.cummax()-1))
