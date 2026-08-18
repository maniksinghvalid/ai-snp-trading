#!/usr/bin/env python3
"""
backtester.experimental.charts — dependency-free SVG chart renderer for the Wave-4
evidence layer (XSR-05). No matplotlib, no new dependency: pure string-built SVG.

`equity_svg` plots one polyline per labelled equity curve (the [(date, equity), ...]
shape `backtester.report.build_equity_curve` returns) on a shared axis. `bars_svg`
plots grouped profit-factor bars (one group per slice, one bar per arm) with a
horizontal reference line at `hline` (1.0 = breakeven PF) — infinite PF values are
clipped to the chart top rather than raising or breaking the layout (T-10-14).

`main` reads a Wave-3 run-tree root (for the core arms' trades.csv, to build the
equity curves) and an aggregate `results.csv` (for the per-slice PF bars), and writes
both SVGs to --out.

Exports: equity_svg, bars_svg, main
"""
import argparse
import csv
import os
import sys

CORE_ARMS = ["ext2_base", "orb30_base", "vwap_pb_base"]

_PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


# ============================================================
# equity_svg
# ============================================================

def equity_svg(series_by_label: dict, title: str = "") -> str:
    """One <polyline> per (label -> [(date, equity), ...]) series on a shared axis,
    with a text legend. Handles an empty series (still one <polyline>, empty
    points) and a flat series (y_min == y_max) without dividing by zero."""
    width, height, margin = 900, 400, 50
    all_values = [v for series in series_by_label.values() for _, v in series]
    if not all_values:
        all_values = [0.0]
    y_min, y_max = min(all_values), max(all_values)
    if y_min == y_max:
        y_min, y_max = y_min - 1.0, y_max + 1.0

    def _x(i, n):
        return margin + (width - 2 * margin) * (i / max(n - 1, 1))

    def _y(value):
        return height - margin - (height - 2 * margin) * (value - y_min) / (y_max - y_min)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<text x="{width / 2}" y="20" text-anchor="middle" font-size="14">{title}</text>',
        f'<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" '
        f'stroke="#ccc" />',
    ]
    for i, (label, series) in enumerate(series_by_label.items()):
        color = _PALETTE[i % len(_PALETTE)]
        n = len(series)
        points = " ".join(f"{_x(j, n):.1f},{_y(v):.1f}" for j, (_, v) in enumerate(series))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5" />')
        parts.append(f'<text x="{margin + 10}" y="{35 + i * 15}" font-size="11" fill="{color}">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ============================================================
# bars_svg
# ============================================================

def bars_svg(values_by_group: dict, title: str = "", hline: float = 1.0) -> str:
    """Grouped PF bars: one group per key of `values_by_group` (a slice label), one
    bar per arm inside the group, plus a dashed horizontal reference line at
    `hline`. Infinite/very-large PF values are clipped to the chart's y_max rather
    than raising or drawing off-canvas (T-10-14)."""
    width, height, margin = 900, 400, 60
    groups = list(values_by_group.keys())
    arms = sorted({arm for g in values_by_group.values() for arm in g})
    finite_values = [
        v for g in values_by_group.values() for v in g.values()
        if v not in (float("inf"), float("-inf"))
    ]
    y_max = max(finite_values + [hline], default=hline) * 1.1 or 1.0
    y_min = min(finite_values + [0.0, hline], default=0.0)
    span = (y_max - y_min) or 1.0

    def _y(value):
        clipped = min(value, y_max)
        return height - margin - (height - 2 * margin) * (clipped - y_min) / span

    plot_width = width - 2 * margin
    group_width = plot_width / max(len(groups), 1)
    bar_width = group_width / max(len(arms), 1) * 0.8

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<text x="{width / 2}" y="20" text-anchor="middle" font-size="14">{title}</text>',
    ]

    hline_y = _y(hline)
    parts.append(
        f'<line class="hline" x1="{margin}" y1="{hline_y:.1f}" x2="{width - margin}" '
        f'y2="{hline_y:.1f}" stroke="#888" stroke-dasharray="4,2" />'
    )
    parts.append(f'<text x="{width - margin + 4}" y="{hline_y + 4:.1f}" font-size="10">{hline}</text>')

    for gi, group in enumerate(groups):
        gx = margin + gi * group_width
        parts.append(
            f'<text x="{gx + group_width / 2:.1f}" y="{height - margin + 15}" font-size="10" '
            f'text-anchor="middle">{group}</text>'
        )
        for ai, arm in enumerate(arms):
            value = values_by_group[group].get(arm)
            if value is None:
                continue
            color = _PALETTE[ai % len(_PALETTE)]
            x = gx + ai * bar_width
            y_top = _y(value)
            bar_height = max((height - margin) - y_top, 0.0)
            parts.append(
                f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bar_width * 0.9:.1f}" '
                f'height="{bar_height:.1f}" fill="{color}" />'
            )

    for ai, arm in enumerate(arms):
        color = _PALETTE[ai % len(_PALETTE)]
        parts.append(f'<text x="{margin + 10}" y="{35 + ai * 15}" font-size="11" fill="{color}">{arm}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


# ============================================================
# CLI
# ============================================================

def _collect_core_trades(root: str, arms=CORE_ARMS, cost: str = "base") -> dict:
    """{arm: trades} pooling every window dir's trades.csv under root/<window>/<cost>/
    for each core arm, sorted chronologically by closed_at."""
    from backtester.experimental.aggregate import _read_trades

    trades_by_arm = {arm: [] for arm in arms}
    if os.path.isdir(root):
        for window_dir in sorted(os.listdir(root)):
            cost_path = os.path.join(root, window_dir, cost)
            if not os.path.isdir(cost_path):
                continue
            for arm in arms:
                trades_csv = os.path.join(cost_path, arm, "trades.csv")
                if os.path.isfile(trades_csv):
                    trades_by_arm[arm].extend(_read_trades(trades_csv))
    for arm in trades_by_arm:
        trades_by_arm[arm].sort(key=lambda t: t["closed_at"])
    return trades_by_arm


def _read_results_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        value = row.get("profit_factor")
        if value == "inf":
            row["profit_factor"] = float("inf")
        elif value == "-inf":
            row["profit_factor"] = float("-inf")
        elif value not in (None, ""):
            row["profit_factor"] = float(value)
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtester.experimental.charts",
        description="Render the equity-curve and per-slice PF-bar SVGs from a run tree + results.csv.",
    )
    parser.add_argument("--root", required=True, help="Run-tree root, e.g. .../runs")
    parser.add_argument("--agg", required=True, help="aggregate.py's results.csv path")
    parser.add_argument("--out", required=True, help="Output directory for the SVG files")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    from backtester.experimental.aggregate import SLICES
    from backtester.report import build_equity_curve

    trades_by_arm = _collect_core_trades(args.root)
    series_by_label = {
        arm: build_equity_curve(trades, 100_000.0, 0.005)
        for arm, trades in trades_by_arm.items() if trades
    }
    with open(os.path.join(args.out, "equity_curves.svg"), "w", encoding="utf-8") as f:
        f.write(equity_svg(series_by_label, title="Core-arm equity curves (all windows, base cost)"))

    results_rows = _read_results_csv(args.agg)
    slice_order = sorted(SLICES, key=lambda s: SLICES[s][0])
    bar_arms = set(CORE_ARMS) | {"tjl_base"}
    values_by_group = {s: {} for s in slice_order}
    for row in results_rows:
        if row.get("cost") == "base" and row.get("slice") in values_by_group and row.get("arm") in bar_arms:
            values_by_group[row["slice"]][row["arm"]] = row["profit_factor"]
    with open(os.path.join(args.out, "pf_by_slice.svg"), "w", encoding="utf-8") as f:
        f.write(bars_svg(values_by_group, title="Profit factor per slice per arm (base cost)"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
