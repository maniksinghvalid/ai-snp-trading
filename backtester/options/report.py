#!/usr/bin/env python3
"""
backtester.options.report — options trade log -> trades.csv + summary.json (D-16, OBT-07).

Imports ONLY the module-private ratio helpers from `backtester.report`
(Sharpe/Sortino/Calmar/win-loss stats — functions of a plain equity curve or
a dollar-P&L list, with zero equity-specific assumption baked in). The public
per-trade pipeline in that module is deliberately NOT used here: its
per-trade dollar formula is `(exit_price - entry_price) * quantity -
commission * quantity * 2`, a per-SHARE stock formula — quantity there means
shares of one long stock position, and there is no x100 contract multiplier
and no concept of a multi-leg spread. Feeding a credit spread's numbers
through that formula runs without error but silently produces the wrong
dollar amount (RESEARCH.md Pitfall 2). This module computes its own
credit-spread P&L (already done in `backtester/options/engine.py`, matching
`bot/options/service.py`'s realized-P&L formula byte-for-byte) and its own
day-by-day equity curve, then hands only that plain curve/pnl-list to the
imported ratio helpers — never calling the stock-shaped compute_metrics,
write_report or build_equity_curve functions, and never re-deriving their
internal per-trade net-pnl formula.

Exports: build_options_equity_curve, compute_options_metrics,
         write_options_report, _OPTIONS_CSV_FIELDS
"""
import csv
import json
import os
from collections import defaultdict
from datetime import datetime

from backtester.options.data import trading_days
from backtester.report import _calmar_ratio, _sharpe_ratio, _sortino_ratio, _win_loss_stats

# D-16 column order, matching backtester/options/engine.py's trade-log keys exactly.
_OPTIONS_CSV_FIELDS = [
    "symbol", "structure", "opened_date", "closed_date", "expiry",
    "short_put_strike", "long_put_strike", "short_call_strike", "long_call_strike",
    "credit_per_spread", "qty", "width", "exit_reason", "pnl_usd", "max_loss_usd",
    "dte_at_open", "dte_at_close", "ivr_at_entry", "credit_captured_pct",
    "commission_usd", "carried_mark", "min_leg_volume",
]


def _assumptions(starting_capital: float, extra_assumptions: dict = None) -> dict:
    """The reporting-inputs block embedded in summary.json. This module only
    knows starting_capital; the CLI layer (Plan 09-04) merges in
    slippage_usd/commission_per_leg_usd/risk_free_rate/spread_pct/oi_source/
    rules_path and the IS/OOS label."""
    return {"starting_capital_usd": float(starting_capital), **(extra_assumptions or {})}


def build_options_equity_curve(trades: list, starting_capital: float, start: str, end: str) -> list:
    """[("YYYY-MM-DD", end-of-day equity), ...] over every NYSE day in [start, end].

    Sums each trade's `pnl_usd` by `closed_date`; days with no closed trade
    carry the prior equity forward. Reuses `backtester.options.data.trading_days`
    (same NYSE calendar object the engine's daily replay loop uses) rather
    than a second calendar call site.
    """
    pnl_by_day = defaultdict(float)
    for t in trades:
        pnl_by_day[t["closed_date"]] += t["pnl_usd"]

    curve = []
    equity = float(starting_capital)
    for day in trading_days(start, end):
        equity += pnl_by_day.get(day, 0.0)
        curve.append((day, equity))
    return curve


def compute_options_metrics(trades: list, starting_capital: float, start: str, end: str,
                            extra_assumptions: dict = None) -> dict:
    """Credit-spread-correct performance metrics + per-symbol breakdown.

    trades: D-16-shaped closed-trade dicts (backtester/options/engine.py's
        trade_log). Empty list returns a zeroed dict without raising (same
        contract as the equity backtester's report module).
    """
    assumptions = _assumptions(starting_capital, extra_assumptions)

    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "sortino": 0.0, "sharpe": 0.0, "calmar": 0.0,
            "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
            "avg_credit_captured_pct": 0.0, "total_pnl_usd": 0.0,
            "per_symbol": {}, "assumptions": assumptions,
        }

    ordered = sorted(trades, key=lambda t: t["closed_date"])
    pnls = [t["pnl_usd"] for t in ordered]
    wl = _win_loss_stats(pnls)
    curve = build_options_equity_curve(ordered, starting_capital, start, end)

    # Trade-sequence dollar drawdown (peak-relative over cumulative realized P&L,
    # trades in chronological exit order) -- report.py's version is not
    # importable (it lives inline in the stock-shaped metrics function), so
    # computed inline here instead.
    cum = peak = max_dd_usd = 0.0
    for pnl in pnls:
        cum += pnl
        peak = max(peak, cum)
        max_dd_usd = max(max_dd_usd, peak - cum)

    # Daily-equity drawdown in percent (peak-relative, starting capital included).
    eq_peak = float(starting_capital)
    max_dd_pct = 0.0
    for _, equity in curve:
        eq_peak = max(eq_peak, equity)
        if eq_peak > 0:
            max_dd_pct = max(max_dd_pct, 100.0 * (eq_peak - equity) / eq_peak)

    net_pnl = sum(pnls)
    final_value = float(starting_capital) + net_pnl
    cagr_pct = 0.0
    if curve and final_value > 0 and starting_capital > 0:
        span_days = (
            datetime.strptime(curve[-1][0], "%Y-%m-%d")
            - datetime.strptime(curve[0][0], "%Y-%m-%d")
        ).days + 1
        if span_days >= 1:
            cagr_pct = 100.0 * ((final_value / starting_capital) ** (365.25 / span_days) - 1.0)

    per_symbol = {}
    for sym in sorted({t["symbol"] for t in ordered}):
        sym_pnls = [t["pnl_usd"] for t in ordered if t["symbol"] == sym]
        stats = _win_loss_stats(sym_pnls)
        stats["total_trades"] = len(sym_pnls)
        stats["net_pnl_usd"] = sum(sym_pnls)
        per_symbol[sym] = stats

    captured = [t["credit_captured_pct"] for t in ordered if t.get("credit_captured_pct") is not None]

    return {
        "total_trades": len(ordered),
        "win_rate": wl["win_rate"],
        "profit_factor": wl["profit_factor"],
        "avg_win_usd": wl["avg_win_usd"],
        "avg_loss_usd": wl["avg_loss_usd"],
        "sortino": _sortino_ratio(curve, float(starting_capital)),
        "sharpe": _sharpe_ratio(curve, float(starting_capital)),
        "calmar": _calmar_ratio(cagr_pct, max_dd_pct),
        "max_drawdown_usd": max_dd_usd,
        "max_drawdown_pct": max_dd_pct,
        "avg_credit_captured_pct": sum(captured) / len(captured) if captured else 0.0,
        "total_pnl_usd": net_pnl,
        "per_symbol": per_symbol,
        "assumptions": assumptions,
    }


def write_options_report(trades: list, output_dir: str, starting_capital: float,
                         start: str, end: str, extra_assumptions: dict = None) -> dict:
    """Write trades.csv + summary.json to output_dir (creates it if absent).

    `config.json` (the effective, override-applied config) is written by the
    CLI (Plan 09-04), not here -- this module never sees it.
    """
    os.makedirs(output_dir, exist_ok=True)

    metrics = compute_options_metrics(trades, starting_capital, start, end, extra_assumptions)
    ordered = sorted(trades, key=lambda t: t["closed_date"])

    with open(os.path.join(output_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_OPTIONS_CSV_FIELDS)
        writer.writeheader()
        for trade in ordered:
            writer.writerow({k: trade.get(k) for k in _OPTIONS_CSV_FIELDS})

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    return metrics
