#!/usr/bin/env python3
"""
tests/backtester/options/test_report.py — Phase 9 Plan 03 (T-09-09).
"""
import json

import pytest

from backtester.options.report import (
    _OPTIONS_CSV_FIELDS,
    build_options_equity_curve,
    compute_options_metrics,
    write_options_report,
)

_START, _END = "2025-01-01", "2025-01-10"


def _trade(symbol, closed_date, pnl_usd, credit_captured_pct=50.0, **overrides):
    row = {
        "symbol": symbol, "structure": "put_credit_spread",
        "opened_date": "2024-12-01", "closed_date": closed_date, "expiry": "2025-02-15",
        "short_put_strike": 100.0, "long_put_strike": 95.0,
        "short_call_strike": None, "long_call_strike": None,
        "credit_per_spread": 1.00, "qty": 2, "width": 5.0,
        "exit_reason": "profit_target", "pnl_usd": pnl_usd, "max_loss_usd": 800.0,
        "dte_at_open": 45, "dte_at_close": 20, "ivr_at_entry": 55.0,
        "credit_captured_pct": credit_captured_pct, "commission_usd": 5.2,
        "carried_mark": False, "min_leg_volume": 1000,
    }
    row.update(overrides)
    return row


@pytest.fixture
def three_trades():
    return [
        _trade("US.AAA", "2025-01-03", 100.0, credit_captured_pct=60.0),
        _trade("US.AAA", "2025-01-06", -50.0, credit_captured_pct=-20.0),
        _trade("US.BBB", "2025-01-08", 75.0, credit_captured_pct=45.0),
    ]


def test_report_output_shape(tmp_path, three_trades):
    metrics = write_options_report(
        three_trades, str(tmp_path), starting_capital=100_000.0, start=_START, end=_END,
    )

    csv_path = tmp_path / "trades.csv"
    summary_path = tmp_path / "summary.json"
    assert csv_path.exists()
    assert summary_path.exists()

    header = csv_path.read_text().splitlines()[0].split(",")
    assert header == _OPTIONS_CSV_FIELDS

    on_disk = json.loads(summary_path.read_text())
    for key in ("total_trades", "win_rate", "profit_factor", "avg_win_usd", "avg_loss_usd",
               "sortino", "sharpe", "calmar", "max_drawdown_usd", "max_drawdown_pct",
               "avg_credit_captured_pct", "total_pnl_usd", "per_symbol", "assumptions"):
        assert key in on_disk, key
    assert on_disk == metrics


def test_metrics_match_hand_computed(three_trades):
    metrics = compute_options_metrics(
        three_trades, starting_capital=100_000.0, start=_START, end=_END,
    )

    assert metrics["total_trades"] == 3
    assert metrics["win_rate"] == pytest.approx(2 / 3)
    assert metrics["profit_factor"] == pytest.approx((100.0 + 75.0) / 50.0)
    assert metrics["total_pnl_usd"] == pytest.approx(125.0)
    assert metrics["avg_credit_captured_pct"] == pytest.approx((60.0 - 20.0 + 45.0) / 3)
    assert set(metrics["per_symbol"]) == {"US.AAA", "US.BBB"}
    assert metrics["per_symbol"]["US.AAA"]["total_trades"] == 2
    assert metrics["per_symbol"]["US.AAA"]["net_pnl_usd"] == pytest.approx(50.0)


def test_does_not_use_stock_pnl_helpers():
    import ast
    import inspect

    from backtester.options import report as report_mod

    src = inspect.getsource(report_mod)
    tree = ast.parse(src)
    docstring = ast.get_docstring(tree) or ""
    # The module docstring is ALLOWED to name these (it explains why they are
    # unused); the code AFTER the docstring must never call them.
    code_only = src.replace(docstring, "", 1)
    for forbidden in ("compute_metrics", "write_report(", "_net_pnl", "build_equity_curve("):
        assert forbidden not in code_only, forbidden

    import_line = next(
        line for line in src.splitlines() if line.startswith("from backtester.report import")
    )
    assert import_line.strip() == (
        "from backtester.report import _calmar_ratio, _sharpe_ratio, _sortino_ratio, _win_loss_stats"
    )


def test_empty_trades_returns_zeroed_metrics():
    metrics = compute_options_metrics([], starting_capital=100_000.0, start=_START, end=_END)

    assert metrics["total_trades"] == 0
    assert metrics["win_rate"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert metrics["max_drawdown_usd"] == 0.0
    assert metrics["per_symbol"] == {}
    assert metrics["assumptions"]["starting_capital_usd"] == 100_000.0


def test_equity_curve_carries_flat_days_forward(three_trades):
    curve = build_options_equity_curve(three_trades, 100_000.0, _START, _END)
    by_day = dict(curve)
    assert by_day["2025-01-02"] == pytest.approx(100_000.0)  # no trade closed yet
    assert by_day["2025-01-03"] == pytest.approx(100_100.0)  # +100 trade
    assert by_day["2025-01-10"] == pytest.approx(100_125.0)  # cumulative through last trade
