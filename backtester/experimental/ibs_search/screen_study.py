#!/usr/bin/env python3
"""Screen-only validation of the Trend Join Long premarket screen (D1/D2/D3).

Part 1: yfinance daily bars, current S&P 500, ~4y. Does passing the screen predict
        intraday (open->close) behaviour vs. baselines? Market-demeaned, CIs by
        day-clustered bootstrap.
Part 2: cached Massive 5m bars (with premarket). Replicates the live 08:30 ET
        screen exactly and measures the strategy's actual holding window
        (10:05 -> 15:50 ET).
"""
import glob, os, re
import numpy as np, pandas as pd, yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(ROOT, "backtester", "results", "ibs_search")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(0)


def boot_ci(df, col, n=2000):
    """95% CI of the mean of `col`, resampling whole trading days (clusters)."""
    g = df.groupby("date")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    if len(s) < 5:
        return (np.nan, np.nan)
    idx = rng.integers(0, len(s), size=(n, len(s)))
    means = s[idx].sum(1) / c[idx].sum(1)
    return tuple(np.percentile(means, [2.5, 97.5]))


def summarize(df, groups, ret="oc", ex="ex"):
    rows = []
    for name, mask in groups.items():
        d = df[mask]
        lo, hi = boot_ci(d, ex)
        rows.append({
            "group": name, "n": len(d), "days": d["date"].nunique(),
            "mean_ret_%": 100 * d[ret].mean(), "median_ret_%": 100 * d[ret].median(),
            "win_%": 100 * (d[ret] > 0).mean(),
            "mean_excess_%": 100 * d[ex].mean(),
            "excess_CI95_%": f"[{100*lo:+.2f}, {100*hi:+.2f}]",
        })
    return pd.DataFrame(rows)


# ============================================================ Part 1
syms = pd.read_csv(sorted(glob.glob(f"{ROOT}/data/sp500_*.csv"))[-1])["symbol"].tolist()
raw = yf.download(syms, period="4y", interval="1d", group_by="ticker",
                  auto_adjust=True, threads=8, progress=False)
frames = []
for s in syms:
    try:
        f = raw[s].dropna(how="all").copy()
    except KeyError:
        continue
    if len(f) < 260:
        continue
    f.columns = f.columns.str.lower()
    f["pc"] = f["close"].shift(1)
    f["ph"] = f["high"].shift(1)
    f["sma200"] = f["close"].rolling(200).mean().shift(1)   # prior closes only
    f["sym"] = s
    frames.append(f)
d = pd.concat(frames).dropna(subset=["pc", "sma200", "open", "close"])
d["date"] = d.index.date
d["gap"] = d["open"] / d["pc"] - 1
d["oc"] = d["close"] / d["open"] - 1
d["mfe"] = d["high"] / d["open"] - 1
d["mae"] = d["low"] / d["open"] - 1
d["ex"] = d["oc"] - d.groupby("date")["oc"].transform("mean")
d["D1"] = d["open"] > d["ph"]          # proxy: live uses 08:30 premarket price
d["D2"] = d["pc"] > d["sma200"]
d["D3"] = d["gap"] >= 0.03
d["screen"] = d["D1"] & d["D2"] & d["D3"]

print(f"\n=== PART 1: daily, {d['sym'].nunique()} symbols, {d['date'].nunique()} sessions "
      f"{d['date'].min()} .. {d['date'].max()} ===")
p1 = summarize(d, {
    "all stock-days": d["oc"].notna(),
    "gap>=3% (D3 only)": d["D3"],
    "D3 & D1": d["D3"] & d["D1"],
    "SCREEN D1&D2&D3": d["screen"],
    "D3 & D1 & NOT D2": d["D3"] & d["D1"] & ~d["D2"],
    "gap<=-3% (contrast)": d["gap"] <= -0.03,
})
print(p1.round(3).to_string(index=False))

print("\n--- gap-threshold sensitivity (D1 & D2 held) ---")
b = d[d["D1"] & d["D2"]].copy()
b["bucket"] = pd.cut(b["gap"], [0.01, 0.02, 0.03, 0.05, 0.08, 10], right=False,
                     labels=["1-2%", "2-3%", "3-5%", "5-8%", "8%+"])
b = b.dropna(subset=["bucket"])
p1b = summarize(b, {str(k): b["bucket"] == k for k in b["bucket"].cat.categories})
print(p1b.round(3).to_string(index=False))

sc = d[d["screen"]]
per_day = sc.groupby("date").size().reindex(sorted(d["date"].unique()), fill_value=0)
print("\n--- funnel: screen candidates per session ---")
print(f"mean {per_day.mean():.2f}, median {per_day.median():.0f}, "
      f"zero-candidate days {100*(per_day==0).mean():.1f}%, "
      f">20 (cap binds) {100*(per_day>20).mean():.1f}%, max {per_day.max()}")

# Does gap RANK (the watchlist ordering) carry information within a day?
multi = sc[sc.groupby("date")["sym"].transform("size") >= 2].copy()
multi["rank"] = multi.groupby("date")["gap"].rank(ascending=False)
top, rest = multi[multi["rank"] == 1], multi[multi["rank"] > 1]
print(f"\n--- rank: days with >=2 candidates ({multi['date'].nunique()}) ---")
print(f"rank-1 mean oc {100*top['oc'].mean():+.3f}% (n={len(top)}) vs "
      f"rank>1 {100*rest['oc'].mean():+.3f}% (n={len(rest)}); "
      f"spearman(gap, oc) = {multi[['gap','oc']].corr('spearman').iloc[0,1]:+.3f}")

print("\n--- intraday path for screen names (open-relative) ---")
rngpos = (sc["close"] - sc["low"]) / (sc["high"] - sc["low"])
print(f"median MFE {100*sc['mfe'].median():+.2f}%, median MAE {100*sc['mae'].median():+.2f}%, "
      f"close in top quartile of day range {100*(rngpos>=0.75).mean():.1f}%, "
      f"bottom quartile {100*(rngpos<=0.25).mean():.1f}%")

# ============================================================ Part 2
print("\n=== PART 2: cached Massive 5m (incl. premarket), live-exact 08:30 screen, "
      "10:05->15:50 hold ===")
d2_lookup = d.set_index(["sym", "date"])["D2"]
d2_lookup = d2_lookup[~d2_lookup.index.duplicated()]
rows = []
for path in glob.glob(f"{ROOT}/backtester/cache/massive/*_5m_*.csv"):
    m = re.match(r"([A-Z.\-]+)_5m_(\d{4}-\d\d-\d\d)_(\d{4}-\d\d-\d\d)\.csv$", os.path.basename(path))
    if not m:
        continue
    sym = m.group(1)
    f = pd.read_csv(path, index_col=0)
    if f.empty:
        continue
    f.index = pd.to_datetime(f.index, utc=True).tz_convert("America/New_York")
    f["date"] = f.index.date
    f["t"] = f.index.strftime("%H:%M")
    prev = None
    for day, g in f.groupby("date"):
        rth = g[(g["t"] >= "09:30") & (g["t"] < "16:00")]
        if prev is not None and len(rth) >= 70:
            pm = g[g["t"] < "08:30"]
            e = rth[rth["t"] == "10:00"]   # bar 10:00-10:05 -> close ~= 10:05 price
            x = rth[rth["t"] == "15:45"]   # bar 15:45-15:50 -> close ~= 15:50 price
            if len(pm) and len(e) and len(x):
                pmp = pm["Close"].iloc[-1]
                rows.append({
                    "sym": sym, "date": day, "gap": pmp / prev["close"] - 1,
                    "D1": pmp > prev["high"], "D2": d2_lookup.get((sym, day), np.nan),
                    "has_pm": True,
                    "hold": x["Close"].iloc[0] / e["Close"].iloc[0] - 1,
                })
        if len(rth) >= 70:
            prev = {"close": rth["Close"].iloc[-1], "high": rth["High"].max()}
q = pd.DataFrame(rows).drop_duplicates(["sym", "date"]).dropna(subset=["D2"])
q["D2"] = q["D2"].astype(bool)
q["D3"] = q["gap"] >= 0.03
q["screen"] = q["D1"] & q["D2"] & q["D3"]
q["ex"] = q["hold"] - q.groupby("date")["hold"].transform("mean")
print(f"{q['sym'].nunique()} symbols, {q['date'].nunique()} sessions, {len(q)} symbol-days "
      "(cache universe = historically frequent gappers: selection-biased)")
p2 = summarize(q, {
    "all cached stock-days": q["hold"].notna(),
    "|gap|<1% (quiet)": q["gap"].abs() < 0.01,
    "gap>=3% (D3 only)": q["D3"],
    "SCREEN D1&D2&D3 @08:30": q["screen"],
    "D3 & D1 & NOT D2": q["D3"] & q["D1"] & ~q["D2"],
}, ret="hold")
print(p2.round(3).to_string(index=False))

p1.to_csv(f"{OUT}/part1_daily.csv", index=False)
p1b.to_csv(f"{OUT}/part1_gap_buckets.csv", index=False)
p2.to_csv(f"{OUT}/part2_5m_hold.csv", index=False)
