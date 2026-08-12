#!/usr/bin/env python3
"""
backtester.report — performance metrics + trade CSV / equity-curve / summary.json writer.

Operates purely on the harness-supplied trade-log list (dicts with keys: code,
entry_price, exit_price, quantity, exit_reason, r_multiple, closed_at, and —
since the costs/capital expansion — opened_at). Never reads StateStore (nothing
in bot/ writes the trades table for a backtest; the harness list is the truth).

Cost model (report-layer only — documented assumption): commissions do NOT feed
back into position sizing or the Gate 7 circuit breaker during the replay;
slippage is applied at fill time by SimulatedExecution, commissions here.
Per-trade net pnl:
    (exit_price - entry_price) * quantity - commission_per_share * quantity * 2
(entry shares + total exit shares across partial legs = quantity each way).

avg_r_multiple stays GROSS (it is the strategy's R math, mirrored from the
replay's own Gate 7 accounting); every dollar metric (win rate, profit factor,
drawdowns, equity curve, Sharpe, CAGR) is NET of commissions.

The equity curve is REALIZED-ONLY, marked daily: the strategy force-closes all
positions intraday (time_filter.force_close_et), so end-of-day equity has no
open-position mark-to-market component by construction.

Exports: compute_metrics, build_equity_curve, write_report
"""
import csv
import json
import os
from collections import defaultdict
from datetime import datetime

import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")

_RTH_SECONDS_PER_DAY = 6.5 * 3600.0  # 09:30-16:00 ET regular session
_TRADING_DAYS_PER_YEAR = 252


def _net_pnl(trade: dict, commission_per_share: float) -> float:
    """Realized pnl net of commissions; derived, never read from a stored field."""
    gross = (trade["exit_price"] - trade["entry_price"]) * trade["quantity"]
    return gross - commission_per_share * trade["quantity"] * 2


def _to_dt(value) -> datetime:
    """Accept a datetime or a 'YYYY-MM-DD HH:MM:SS[...]' string (trade-log convention)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")


def _day_str(value) -> str:
    return str(value)[:10]


def build_equity_curve(trades: list, starting_capital: float,
                       commission_per_share: float = 0.0,
                       start=None, end=None) -> list:
    """[("YYYY-MM-DD", end-of-day equity), ...] over every NYSE day in [start, end].

    Days with no closed trades carry the prior equity forward (bot is in cash
    overnight by construction). start/end default to the first/last trade's
    close date when not supplied. Returns [] with no trades and no range.
    """
    pnl_by_day = defaultdict(float)
    for t in trades:
        pnl_by_day[_day_str(t["closed_at"])] += _net_pnl(t, commission_per_share)

    if start is None or end is None:
        traded_days = sorted(pnl_by_day)
        if not traded_days:
            return []
        start = start or traded_days[0]
        end = end or traded_days[-1]

    days = [d.strftime("%Y-%m-%d") for d in _NYSE.valid_days(start_date=start, end_date=end)]
    curve = []
    equity = float(starting_capital)
    for day in days:
        equity += pnl_by_day.get(day, 0.0)
        curve.append((day, equity))
    return curve


def _sharpe_ratio(curve: list, starting_capital: float) -> float:
    """Annualised Sharpe (rf=0) from daily equity returns (flat days = 0 return)."""
    equities = [starting_capital] + [e for _, e in curve]
    returns = [
        equities[i] / equities[i - 1] - 1.0
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = variance ** 0.5
    if std == 0:
        return 0.0
    return (mean / std) * (_TRADING_DAYS_PER_YEAR ** 0.5)


def _exposure_pct(trades: list, n_trading_days: int) -> float:
    """% of total RTH time with >=1 open position (union of [opened_at, closed_at]).

    Trades lacking opened_at (pre-expansion logs) are skipped — exposure is then
    a lower bound, never a crash.
    """
    intervals = []
    for t in trades:
        if not t.get("opened_at") or not t.get("closed_at"):
            continue
        opened, closed = _to_dt(t["opened_at"]), _to_dt(t["closed_at"])
        if closed > opened:
            intervals.append([opened, closed])
    if not intervals or n_trading_days <= 0:
        return 0.0
    intervals.sort()
    merged = [intervals[0]]
    for current in intervals[1:]:
        if current[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], current[1])
        else:
            merged.append(current)
    in_market = sum((e - s).total_seconds() for s, e in merged)
    return 100.0 * in_market / (n_trading_days * _RTH_SECONDS_PER_DAY)


def _win_loss_stats(pnls: list) -> dict:
    """win_rate / avg_win / avg_loss / profit_factor over a net-pnl list."""
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = sum(wins) / gross_loss
    else:
        # No losing dollars: inf for any non-empty trade set (legacy semantics).
        profit_factor = float("inf") if pnls else 0.0
    return {
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "avg_win_usd": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_usd": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": profit_factor,
    }


def compute_metrics(trades: list, starting_capital: float = 100_000.0,
                    commission_per_share: float = 0.0,
                    start=None, end=None) -> dict:
    """Full performance metrics for the run + per-symbol breakdown.

    trades: closed-trade dicts in chronological (exit-time) order — required
        for the trade-sequence max_drawdown_usd calculation.
    starting_capital / commission_per_share / start / end: reporting
        assumptions ("YYYY-MM-DD" window; defaults to the traded span).

    Backward-compatible keys (unchanged values at the default arguments):
    win_rate, avg_r_multiple, profit_factor, max_drawdown_usd, total_trades.
    Empty trade list returns a zeroed dict without raising.
    """
    curve = build_equity_curve(trades, starting_capital, commission_per_share, start, end)

    if not trades:
        return {
            "win_rate": 0.0, "avg_r_multiple": 0.0, "profit_factor": 0.0,
            "max_drawdown_usd": 0.0, "total_trades": 0,
            "starting_capital_usd": float(starting_capital),
            "final_portfolio_value_usd": float(starting_capital),
            "net_pnl_usd": 0.0, "total_commission_usd": 0.0,
            "total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0, "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "exposure_pct": 0.0, "num_trading_days": len(curve),
            "per_symbol": {},
        }

    pnls = [_net_pnl(t, commission_per_share) for t in trades]
    wl = _win_loss_stats(pnls)

    # Trade-sequence drawdown in dollars (pre-existing semantics, kept verbatim;
    # trades must be in chronological exit order).
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
    total_return_pct = 100.0 * net_pnl / starting_capital if starting_capital > 0 else 0.0

    cagr_pct = 0.0
    if curve and final_value > 0 and starting_capital > 0:
        span_days = (
            datetime.strptime(curve[-1][0], "%Y-%m-%d")
            - datetime.strptime(curve[0][0], "%Y-%m-%d")
        ).days + 1
        if span_days >= 1:
            cagr_pct = 100.0 * ((final_value / starting_capital) ** (365.25 / span_days) - 1.0)

    per_symbol = {}
    for code in sorted({t["code"] for t in trades}):
        sym_pnls = [_net_pnl(t, commission_per_share) for t in trades if t["code"] == code]
        stats = _win_loss_stats(sym_pnls)
        stats["total_trades"] = len(sym_pnls)
        stats["net_pnl_usd"] = sum(sym_pnls)
        per_symbol[code] = stats

    return {
        "win_rate": wl["win_rate"],
        "avg_r_multiple": sum(t["r_multiple"] for t in trades) / len(trades),
        "profit_factor": wl["profit_factor"],
        "max_drawdown_usd": max_dd_usd,
        "total_trades": len(trades),
        "starting_capital_usd": float(starting_capital),
        "final_portfolio_value_usd": final_value,
        "net_pnl_usd": net_pnl,
        "total_commission_usd": commission_per_share * sum(t["quantity"] for t in trades) * 2,
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "sharpe_ratio": _sharpe_ratio(curve, float(starting_capital)),
        "max_drawdown_pct": max_dd_pct,
        "avg_win_usd": wl["avg_win_usd"],
        "avg_loss_usd": wl["avg_loss_usd"],
        "exposure_pct": _exposure_pct(trades, len(curve)),
        "num_trading_days": len(curve),
        "per_symbol": per_symbol,
    }


_CSV_FIELDS = ["code", "opened_at", "entry_price", "exit_price", "quantity",
               "exit_reason", "r_multiple", "closed_at"]


def write_report(trades: list, output_dir: str, starting_capital: float = 100_000.0,
                 commission_per_share: float = 0.0, start=None, end=None) -> dict:
    """Write trades.csv + equity_curve.csv + summary.json to output_dir.

    Creates output_dir if absent (operator-chosen local path, T-06-08).
    trades.csv: one row per trade (stdlib csv.DictWriter); rows missing
        opened_at (older logs) get an empty cell, never a crash.
    equity_curve.csv: (date, equity) per NYSE day — independently inspectable.
    summary.json: compute_metrics dict. json.dump defaults to allow_nan=True,
        so profit_factor == inf round-trips as the JSON token `Infinity`.

    Returns the metrics dict.
    """
    os.makedirs(output_dir, exist_ok=True)

    metrics = compute_metrics(trades, starting_capital, commission_per_share, start, end)
    curve = build_equity_curve(trades, starting_capital, commission_per_share, start, end)

    with open(os.path.join(output_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for trade in trades:
            writer.writerow({k: trade.get(k) for k in _CSV_FIELDS})

    with open(os.path.join(output_dir, "equity_curve.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "equity"])
        writer.writerows(curve)

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return metrics
