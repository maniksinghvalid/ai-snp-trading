import sys; sys.argv=["x"]
import os
HERE = os.path.dirname(os.path.abspath(__file__))
import importlib.util, pandas as pd, numpy as np
spec = importlib.util.spec_from_file_location("ss", os.path.join(HERE, "strategy_search.py"))
src = open(os.path.join(HERE, "strategy_search.py")).read().split("# ------------------------------------------------------------------ data")[0]
ns = {}; exec(src, ns)
etf = ns["load"](ns["ETFS"]); C,H,L = etf["Close"],etf["High"],etf["Low"]
ibs = (C-L)/(H-L); ent=(ibs<0.2).fillna(False); ex=(ibs>0.8).fillna(False); rk=ibs.fillna(99)
spy = C["SPY"].dropna()
def run(cost, nxt, cols=None, px=etf):
    if cols is not None:
        px = {k:v[cols] for k,v in etf.items()}
        return ns["simulate"](px, ent[cols], ex[cols], rk[cols], cost, next_open=nxt)
    return ns["simulate"](px, ent, ex, rk, cost, next_open=nxt)
S = lambda e,t,lo="2019-01-01",hi="2100": ns["stats"](e,t,lo,hi)
print("SPY buy&hold OOS:", {k:round(v,2) if isinstance(v,float) else v for k,v in S(spy, pd.DataFrame(columns=['entry','exit','ret'])).items() if k in('CAGR%','Sharpe','maxDD%','pos_years')})
for cost in (0.0002, 0.0004, 0.0008):
    for nxt in (False, True):
        e,t = run(cost,nxt); s=S(e,t)
        print(f"cost/side {cost*100:.2f}% {'next_open' if nxt else 'close   '}: CAGR {s['CAGR%']:.1f}% Sharpe {s['Sharpe']:.2f} maxDD {s['maxDD%']:.1f}% PF {s['PF']:.2f}")
e,t = run(0.0002, True)
t["sym"]=None
print("\nOOS yearly (next_open):", (e["2019":]).resample("YE").last().pct_change().fillna(e["2019":].resample("YE").last().iloc[0]/e["2019":].iloc[0]-1).round(3).to_dict())
print("\nleave-one-out (next_open, OOS):")
for s_ in ns["ETFS"]:
    cols=[c for c in C.columns if c!=s_]; e2,t2=run(0.0002,True,cols); s=S(e2,t2)
    print(f"  without {s_:4s}: CAGR {s['CAGR%']:5.1f}% Sharpe {s['Sharpe']:.2f} PF {s['PF']:.2f}")
print("\nper-ETF standalone (single slot, next_open, OOS PF / trades):")
for s_ in ns["ETFS"]:
    px={k:v[[s_]] for k,v in etf.items()}
    e3,t3=ns["simulate"](px, ent[[s_]], ex[[s_]], rk[[s_]], 0.0002, slots=1, next_open=True); s=S(e3,t3)
    print(f"  {s_:4s} PF {s['PF']:.2f} trades {s['trades']} CAGR {s['CAGR%']:.1f}%")
