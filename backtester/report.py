#!/usr/bin/env python3
"""
backtester.report — BT-03 performance metrics + per-trade CSV/summary.json writer.

CRITICAL (verified against source, overrides 06-RESEARCH's "reuse the closed-trades
reader" note): nothing in `bot/` writes the `trades` table (no `INSERT INTO trades`
exists anywhere in `bot/`), so StateStore's closed-trade query returns an EMPTY list on
a backtest run. This module therefore NEVER reads StateStore — it operates purely on
the harness-supplied trade-log list (a list of dicts with keys: code, entry_price,
exit_price, quantity, exit_reason, r_multiple, closed_at).

realized_pnl is DERIVED per trade as (exit_price - entry_price) * quantity, mirroring
bot.state.store.StateStore.get_daily_trade_stats — the `trades` table schema itself has
no realized_pnl column (bot/state/migrations.py), so there is no stored field to read.

Exports: compute_metrics
"""


def compute_metrics(trades: list) -> dict:
    """Compute win rate, avg R-multiple, profit factor, max drawdown, total trades.

    trades: list of closed-trade dicts (code, entry_price, exit_price, quantity,
        exit_reason, r_multiple, closed_at), in chronological (exit-time) order --
        required for max_drawdown_usd's running peak-to-trough calculation.

    realized_pnl is derived per trade, never read from a stored field:
        (exit_price - entry_price) * quantity

    Returns dict with keys: win_rate, avg_r_multiple, profit_factor, max_drawdown_usd,
    total_trades. Empty trade list returns a zeroed dict without raising.
    """
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "win_rate": 0.0,
            "avg_r_multiple": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usd": 0.0,
            "total_trades": 0,
        }

    pnls = [(t["exit_price"] - t["entry_price"]) * t["quantity"] for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / total_trades
    avg_r_multiple = sum(t["r_multiple"] for t in trades) / total_trades

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    cum_pnl = 0.0
    peak = 0.0
    max_drawdown_usd = 0.0
    for pnl in pnls:  # trades must be in chronological (exit-time) order
        cum_pnl += pnl
        peak = max(peak, cum_pnl)
        max_drawdown_usd = max(max_drawdown_usd, peak - cum_pnl)

    return {
        "win_rate": win_rate,
        "avg_r_multiple": avg_r_multiple,
        "profit_factor": profit_factor,
        "max_drawdown_usd": max_drawdown_usd,
        "total_trades": total_trades,
    }
