#!/usr/bin/env python3
"""
backtester.experimental.aggregate — Wave-4 evidence layer (XSR-05): slices the Wave-3
run-tree trades.csv files into the 9 pre-registered regime windows and the IS/OOS pools,
recomputes metrics per slice/pool via `backtester.report.compute_metrics` (never
reimplemented here), adds the extended metrics report.py does not provide (avg trade $,
expectancy R, max consecutive losses, avg hold minutes, per-side split, bootstrap CI on
mean R, IS/OOS PF ratio), and writes a committed results.csv + results.md. Also runs a
re-run-free walk-forward over the pre-declared sensitivity families, and — when a
`--tjl-root` is given — folds the TJL comparator (`tjl_base`/`tjl_regime`) into the same
table so H7 is answerable from one place.

Pure aggregation: never constructs a `SimulatedBarFeed`, `MassiveDataSource`, or an
`Engine` — every number here is derived from already-written trades.csv rows on disk.
Day extraction is always `[:10]`/`[:19]` string slicing (10-RESEARCH Pitfall 5) — never
an ISO-datetime full-string parse; `backtester.report._to_dt` (strptime-based) is reused
instead.

Exports: SLICES, IS_SLICES, OOS_SLICES, aggregate, bootstrap_ci, walk_forward, main
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

import numpy as np

from backtester.experimental.run import COST_PROFILES
from backtester.report import _net_pnl, _to_dt, compute_metrics

# ============================================================
# Locked single-source-of-truth tables (mirrors the hypotheses doc verbatim)
# ============================================================

SLICES = {
    "E1": ("2023-07-03", "2023-11-30"),
    "E2": ("2023-12-01", "2024-04-30"),
    "E3": ("2024-05-01", "2024-08-30"),
    "C": ("2024-09-02", "2024-12-31"),
    "B": ("2025-04-01", "2025-04-30"),
    "A": ("2025-05-01", "2025-07-31"),
    "D1": ("2025-08-01", "2025-11-28"),
    "D2": ("2025-12-01", "2026-03-31"),
    "D3": ("2026-04-01", "2026-07-31"),
}

IS_SLICES = ["E1", "E2", "E3", "C"]
OOS_SLICES = ["B", "A", "D1", "D2", "D3"]

# H7's fulluniverse TJL comparator uses its OWN, narrower IS/OOS split (E1-E3 / D1-D3
# only) per the hypotheses doc — distinct from the MEGA24 arms' IS_SLICES/OOS_SLICES
# above, which also include C and B/A.
_TJL_IS_SLICES = ["E1", "E2", "E3"]
_TJL_OOS_SLICES = ["D1", "D2", "D3"]

# Pre-declared sensitivity families for the walk-forward (never a hypothesis on their
# own — robustness diagnostics only, per the hypotheses doc's "Not hypotheses" note).
FAMILIES = [
    {"name": "ext2_confirm_bars", "members": ["ext2_n3", "ext2_base", "ext2_n12"]},
    {"name": "orb_window", "members": ["orb5", "orb15", "orb30_base"]},
    {"name": "ext2_stop_basis", "members": ["ext2_base", "ext2_stop_pd", "ext2_stop_atr2"]},
]

_CSV_COLUMNS = [
    "arm", "cost", "slice", "total_trades", "win_rate", "profit_factor", "avg_r_multiple",
    "expectancy_r", "avg_trade_usd", "avg_win_usd", "avg_loss_usd", "max_drawdown_usd",
    "max_drawdown_pct", "total_return_pct", "cagr_pct", "sharpe_ratio", "sortino_ratio",
    "calmar_ratio", "exposure_pct", "net_pnl_usd", "final_portfolio_value_usd",
    "max_consecutive_losses", "avg_hold_minutes", "long_trades", "short_trades",
    "long_pf", "short_pf", "r_mean_ci_low", "r_mean_ci_high", "is_oos_pf_ratio",
]


# ============================================================
# trades.csv I/O
# ============================================================

def _read_trades(csv_path: str) -> list:
    """Read one arm's trades.csv, coercing the numeric columns the CSV round-trip
    loses (mirrors run.py's own `_load_baseline_trades` convention)."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["entry_price"] = float(row["entry_price"])
        row["exit_price"] = float(row["exit_price"])
        row["r_multiple"] = float(row["r_multiple"])
        row["quantity"] = int(row["quantity"])
    return rows


def _slice_for_date(date_str: str):
    """The SLICES key whose [start, end] contains date_str, or None. Plain string
    comparison — every date here is already 'YYYY-MM-DD', lexicographic order matches
    chronological order."""
    for name, (start, end) in SLICES.items():
        if start <= date_str <= end:
            return name
    return None


def _bounds_for_slices(slice_names) -> tuple:
    """(min start, max end) across the named SLICES entries — the 'slice range' a
    pooled (IS/OOS/ALL) row's compute_metrics start/end is drawn from. (None, None)
    when none of the names resolve (compute_metrics then falls back to the traded
    span)."""
    starts = [SLICES[s][0] for s in slice_names if s in SLICES]
    ends = [SLICES[s][1] for s in slice_names if s in SLICES]
    if not starts:
        return None, None
    return min(starts), max(ends)


# ============================================================
# Extended metrics (NOT in report.py — belong here per plan 10-05's own scope)
# ============================================================

def _profit_factor(pnls: list) -> float:
    """Same semantics as report._win_loss_stats' profit_factor, standalone so a
    per-side split can be computed without re-deriving win/loss dicts."""
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p <= 0))
    if losses > 0:
        return wins / losses
    return float("inf") if pnls else 0.0


def _safe_ratio(is_pf: float, oos_pf: float) -> float:
    """IS/OOS profit-factor ratio, safe against 0 and inf on either side."""
    if oos_pf == float("inf"):
        return 1.0 if is_pf == float("inf") else 0.0
    if oos_pf == 0:
        return float("inf") if is_pf > 0 else 0.0
    return is_pf / oos_pf


def bootstrap_ci(values, n: int = 10000, seed: int = 0) -> tuple:
    """95% bootstrap CI (2.5th/97.5th percentile of resampled means) on `values`
    (typically r_multiples). `numpy.random.default_rng(seed).choice` resampling —
    deterministic: the same input + seed always returns the identical bounds, which
    is what makes the interval citable in the results doc. (0.0, 0.0) on an empty
    input (never raises, never NaN)."""
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    resampled = rng.choice(arr, size=(n, arr.size), replace=True)
    low, high = np.percentile(resampled.mean(axis=1), [2.5, 97.5])
    return float(low), float(high)


def _extras(trades: list, commission: float) -> dict:
    """avg_trade_usd, expectancy_r, max_consecutive_losses, avg_hold_minutes,
    per-side split, bootstrap CI on mean R — the extras report.py does not provide."""
    if not trades:
        return {
            "avg_trade_usd": 0.0, "expectancy_r": 0.0, "max_consecutive_losses": 0,
            "avg_hold_minutes": 0.0, "long_trades": 0, "short_trades": 0,
            "long_pf": 0.0, "short_pf": 0.0, "r_mean_ci_low": 0.0, "r_mean_ci_high": 0.0,
        }

    ordered = sorted(trades, key=lambda t: _to_dt(t["closed_at"]))
    pnls = [_net_pnl(t, commission) for t in ordered]
    net_pnl = sum(pnls)
    expectancy_r = sum(t["r_multiple"] for t in trades) / len(trades)

    max_losses = run = 0
    for pnl in pnls:
        if pnl <= 0:
            run += 1
            max_losses = max(max_losses, run)
        else:
            run = 0

    holds = [
        (_to_dt(t["closed_at"]) - _to_dt(t["opened_at"])).total_seconds() / 60.0
        for t in trades if t.get("opened_at")
    ]
    avg_hold = sum(holds) / len(holds) if holds else 0.0

    longs = [t for t in trades if t.get("side") != "short"]
    shorts = [t for t in trades if t.get("side") == "short"]
    long_pf = _profit_factor([_net_pnl(t, commission) for t in longs])
    short_pf = _profit_factor([_net_pnl(t, commission) for t in shorts])

    r_low, r_high = bootstrap_ci([t["r_multiple"] for t in trades])

    return {
        "avg_trade_usd": net_pnl / len(trades),
        "expectancy_r": expectancy_r,
        "max_consecutive_losses": max_losses,
        "avg_hold_minutes": avg_hold,
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_pf": long_pf,
        "short_pf": short_pf,
        "r_mean_ci_low": r_low,
        "r_mean_ci_high": r_high,
    }


def _row(arm: str, cost: str, slice_name: str, trades: list, commission: float,
         bounds: tuple = None) -> dict:
    """One results-table row: report.compute_metrics (reused, never reimplemented)
    plus this module's own extras."""
    if bounds is None:
        bounds = SLICES.get(slice_name, (None, None))
    start, end = bounds
    metrics = compute_metrics(trades, 100_000.0, commission, start, end)
    metrics.pop("per_symbol", None)
    metrics.pop("assumptions", None)
    row = {"arm": arm, "cost": cost, "slice": slice_name}
    row.update(metrics)
    row.update(_extras(trades, commission))
    return row


# ============================================================
# Walk-forward (pure aggregation over already-computed rows — never re-runs the engine)
# ============================================================

def walk_forward(rows_by_arm_slice: dict, families: list) -> list:
    """For each family, walk the 9 SLICES in chronological order, pick the
    best-by-PF member on slice k (skipping members with 0 trades or no row), and
    record that SAME member's slice k+1 PF/trade-count -- no engine re-run, pure
    lookup into `rows_by_arm_slice` ({(arm, slice): row})."""
    order = sorted(SLICES, key=lambda s: SLICES[s][0])
    results = []
    for family in families:
        for i in range(len(order) - 1):
            slice_k, slice_k1 = order[i], order[i + 1]
            candidates = [
                (member, rows_by_arm_slice[(member, slice_k)]["profit_factor"])
                for member in family["members"]
                if (member, slice_k) in rows_by_arm_slice
                and rows_by_arm_slice[(member, slice_k)]["total_trades"] > 0
            ]
            if not candidates:
                continue
            picked_arm = max(candidates, key=lambda c: c[1])[0]
            next_row = rows_by_arm_slice.get((picked_arm, slice_k1))
            results.append({
                "family": family["name"],
                "from_slice": slice_k,
                "to_slice": slice_k1,
                "picked_arm": picked_arm,
                "next_slice_pf": next_row["profit_factor"] if next_row else None,
                "next_slice_trades": next_row["total_trades"] if next_row else 0,
            })
    return results


# ============================================================
# TJL comparator (H7) — reads existing --tjl-regime output read-only
# ============================================================

def _tjl_rows(tjl_root: str) -> list:
    """Rows for tjl_base/tjl_regime from <tjl_root>/<label>/{tjl_base,tjl_regime}/
    trades.csv, plus the fulluniverse H7 IS (E1-E3) / OOS (D1-D3) pools."""
    rows = []
    is_trades = defaultdict(list)
    oos_trades = defaultdict(list)

    if not os.path.isdir(tjl_root):
        return rows

    for label in sorted(os.listdir(tjl_root)):
        label_path = os.path.join(tjl_root, label)
        if not os.path.isdir(label_path):
            continue
        for arm in ("tjl_base", "tjl_regime"):
            trades_csv = os.path.join(label_path, arm, "trades.csv")
            if not os.path.isfile(trades_csv):
                continue
            trades = _read_trades(trades_csv)
            rows.append(_row(arm, "base", label, trades, 0.005))
            if label in _TJL_IS_SLICES:
                is_trades[arm].extend(trades)
            if label in _TJL_OOS_SLICES:
                oos_trades[arm].extend(trades)

    for arm in ("tjl_base", "tjl_regime"):
        if is_trades[arm]:
            rows.append(_row(arm, "base", "IS", is_trades[arm], 0.005,
                             bounds=_bounds_for_slices(_TJL_IS_SLICES)))
        if oos_trades[arm]:
            rows.append(_row(arm, "base", "OOS", oos_trades[arm], 0.005,
                             bounds=_bounds_for_slices(_TJL_OOS_SLICES)))
    return rows


# ============================================================
# Main aggregation entry point
# ============================================================

def aggregate(root: str, tjl_root: str = None) -> list:
    """Walk <root>/<window>/<cost>/<arm>/trades.csv, bucket every row into its
    SLICES entry (opened_at falling back to closed_at, [:10] string slicing —
    Pitfall 5), and emit one row per (arm, cost, slice) plus the pooled
    (arm, cost, IS/OOS/ALL) rows. Appends the TJL comparator rows when tjl_root
    is given."""
    trades_by_key = defaultdict(list)
    arm_costs = set()

    if os.path.isdir(root):
        for window_dir in sorted(os.listdir(root)):
            window_path = os.path.join(root, window_dir)
            if not os.path.isdir(window_path):
                continue
            for cost in sorted(os.listdir(window_path)):
                cost_path = os.path.join(window_path, cost)
                if not os.path.isdir(cost_path):
                    continue
                for arm in sorted(os.listdir(cost_path)):
                    trades_csv = os.path.join(cost_path, arm, "trades.csv")
                    if not os.path.isfile(trades_csv):
                        continue
                    arm_costs.add((arm, cost))
                    for row in _read_trades(trades_csv):
                        day = (row.get("opened_at") or row["closed_at"])[:10]
                        slice_name = _slice_for_date(day)
                        if slice_name is not None:
                            trades_by_key[(arm, cost, slice_name)].append(row)

    rows = []
    for arm, cost in sorted(arm_costs):
        commission = COST_PROFILES[cost][0]
        all_trades = []
        for slice_name in SLICES:
            slice_trades = trades_by_key.get((arm, cost, slice_name), [])
            if slice_trades:
                rows.append(_row(arm, cost, slice_name, slice_trades, commission))
            all_trades.extend(slice_trades)

        is_trades = [t for s in IS_SLICES for t in trades_by_key.get((arm, cost, s), [])]
        oos_trades = [t for s in OOS_SLICES for t in trades_by_key.get((arm, cost, s), [])]

        is_row = _row(arm, cost, "IS", is_trades, commission, bounds=_bounds_for_slices(IS_SLICES))
        oos_row = _row(arm, cost, "OOS", oos_trades, commission, bounds=_bounds_for_slices(OOS_SLICES))
        ratio = _safe_ratio(is_row["profit_factor"], oos_row["profit_factor"])
        is_row["is_oos_pf_ratio"] = ratio
        oos_row["is_oos_pf_ratio"] = ratio
        rows.append(is_row)
        rows.append(oos_row)
        rows.append(_row(arm, cost, "ALL", all_trades, commission,
                         bounds=_bounds_for_slices(list(SLICES))))

    if tjl_root:
        rows.extend(_tjl_rows(tjl_root))

    return rows


# ============================================================
# Writers
# ============================================================

def _fmt(value):
    """CSV-cell formatting: inf/-inf as the literal string (never an empty cell,
    never a crash — T-10-14)."""
    if value is None:
        return ""
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        if value == float("-inf"):
            return "-inf"
    return value


def _fmt_md(value):
    """Markdown-cell formatting: same inf-safety as _fmt, human-rounded floats."""
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        if value == float("-inf"):
            return "-inf"
        return f"{value:.4f}"
    return str(value)


def _write_csv(rows: list, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _fmt(row.get(col)) for col in _CSV_COLUMNS})


def _write_md(rows: list, path: str) -> None:
    lines = ["# Experimental aggregate results", ""]

    lines.append("## Per-arm IS/OOS summary")
    lines.append("")
    lines.append("| Arm | Cost | Slice | Trades | PF | Sortino | Avg R | IS/OOS PF ratio |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in rows:
        if row["slice"] in ("IS", "OOS"):
            lines.append(
                f"| {row['arm']} | {row['cost']} | {row['slice']} | {row['total_trades']} "
                f"| {_fmt_md(row['profit_factor'])} | {_fmt_md(row['sortino_ratio'])} "
                f"| {_fmt_md(row['avg_r_multiple'])} | {_fmt_md(row.get('is_oos_pf_ratio'))} |"
            )
    lines.append("")

    lines.append("## Per-slice PF matrix")
    lines.append("")
    slice_order = sorted(SLICES, key=lambda s: SLICES[s][0])
    lines.append("| Arm | Cost | " + " | ".join(slice_order) + " |")
    lines.append("|---|---|" + "---|" * len(slice_order))
    by_arm_cost = defaultdict(dict)
    for row in rows:
        if row["slice"] in SLICES and not row["arm"].startswith("tjl_"):
            by_arm_cost[(row["arm"], row["cost"])][row["slice"]] = row["profit_factor"]
    for (arm, cost), pf_by_slice in sorted(by_arm_cost.items()):
        cells = [_fmt_md(pf_by_slice.get(s)) for s in slice_order]
        lines.append(f"| {arm} | {cost} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Walk-forward")
    lines.append("")
    rows_by_arm_slice = {(r["arm"], r["slice"]): r for r in rows if r["cost"] == "base"}
    wf = walk_forward(rows_by_arm_slice, FAMILIES)
    lines.append("| Family | From | To | Picked arm | Next-slice PF | Next-slice trades |")
    lines.append("|---|---|---|---|---|---|")
    for w in wf:
        lines.append(
            f"| {w['family']} | {w['from_slice']} | {w['to_slice']} | {w['picked_arm']} "
            f"| {_fmt_md(w['next_slice_pf'])} | {w['next_slice_trades']} |"
        )
    lines.append("")

    lines.append("## TJL comparator")
    lines.append("")
    lines.append("| Arm | Slice | Trades | PF | Avg R |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        if row["arm"].startswith("tjl_"):
            lines.append(
                f"| {row['arm']} | {row['slice']} | {row['total_trades']} "
                f"| {_fmt_md(row['profit_factor'])} | {_fmt_md(row['avg_r_multiple'])} |"
            )
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ============================================================
# CLI
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtester.experimental.aggregate",
        description="Slice a Wave-3 run tree into the pre-registered evidence tables.",
    )
    parser.add_argument("--root", required=True, help="Run-tree root, e.g. .../runs")
    parser.add_argument("--tjl-root", default=None, help="TJL --tjl-regime output root")
    parser.add_argument("--out", required=True, help="Output directory for results.csv/md")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    rows = aggregate(args.root, args.tjl_root)
    os.makedirs(args.out, exist_ok=True)
    _write_csv(rows, os.path.join(args.out, "results.csv"))
    _write_md(rows, os.path.join(args.out, "results.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
