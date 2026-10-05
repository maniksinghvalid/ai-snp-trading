import os
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
src=open(os.path.join(HERE, "strategy_search.py")).read().split("# ------------------------------------------------------------------ data")[0]
ns={}; exec(src,ns); load,simulate,stats,ETFS=ns["load"],ns["simulate"],ns["stats"],ns["ETFS"]
pairs={"SPY":"SSO","QQQ":"QLD","IWM":"UWM","DIA":"DDM"}
etf=load(ETFS+list(pairs.values())); C,H,L=etf["Close"],etf["High"],etf["Low"]
ibs=(C-L)/(H-L); F=lambda d: d.fillna(False).astype(bool)
def run(sig_cols, trade_cols, cost, nxt, slots):
    s=ibs[sig_cols].copy(); s.columns=trade_cols
    px={k:v[trade_cols] for k,v in etf.items()}
    return simulate(px, F((s<0.2)&C[trade_cols].notna().values), F(s>0.8), s.fillna(99), cost, slots=slots, next_open=nxt)
def line(tag,e,t):
    s=stats(e,t,"2019-01-01","2100"); i=stats(e,t,"2010-01-01","2018-12-31")
    print(f"{tag:44s} OOS CAGR {s['CAGR%']:5.1f}% Sharpe {s['Sharpe']:.2f} maxDD {s['maxDD%']:6.1f}% | IS CAGR {i['CAGR%']:5.1f}% Sharpe {i['Sharpe']:.2f} maxDD {i['maxDD%']:6.1f}%")
mixed=[pairs.get(c,c) for c in ETFS]
for nxt in (False,True):
    tag="next_open" if nxt else "close    "
    for slots in (10,7,5):
        e,t=run(ETFS,ETFS,0.0002,nxt,slots); line(f"IBS-17 1x  {slots} slots ({100//slots}%)  {tag}",e,t)
    e,t=run(ETFS,mixed,0.0003,nxt,10); line(f"IBS-17 mixed (4 index ETFs as 2x) 10 slots {tag}",e,t)
    e,t=run(ETFS,mixed,0.0003,nxt,7); line(f"IBS-17 mixed (4 index ETFs as 2x) 7 slots  {tag}",e,t)
    print()
