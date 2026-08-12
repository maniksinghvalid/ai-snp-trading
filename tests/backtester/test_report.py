#!/usr/bin/env python3
"""
tests.backtester.test_report — BT-03: compute_metrics + write_report against known fixtures.

Wave 0 stub (plan 06-01): `pytest.importorskip` guards this whole module until
backtester/report.py exists (plan 06-04) — the suite SKIPs cleanly until then, then these
real assertions run RED -> GREEN.

Uses tests.backtester.fixtures.make_trade_log() (2 winners, 2 losers, hand-computed expected
win_rate/avg_r_multiple/profit_factor/max_drawdown_usd documented in the fixture's own
docstring) and asserts compute_metrics derives realized_pnl -- (exit_price - entry_price) *
quantity -- rather than reading a stored field (the `trades` table has no realized_pnl
column; bot/state/migrations.py lines 51-61).
"""
import csv
import json

import pytest

from tests.backtester.fixtures import make_trade_log, recent_session_days

mod = pytest.importorskip("backtester.report")

compute_metrics = mod.compute_metrics
write_report = mod.write_report
build_equity_curve = mod.build_equity_curve


def test_compute_metrics_matches_fixture_documented_expected_values():
    metrics = compute_metrics(make_trade_log())

    assert metrics["total_trades"] == 4
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["avg_r_multiple"] == pytest.approx(0.375)
    assert metrics["profit_factor"] == pytest.approx(1.2)
    assert metrics["max_drawdown_usd"] == pytest.approx(1000.0)


def test_compute_metrics_empty_trade_list_returns_zeroed_dict_without_raising():
    metrics = compute_metrics([])

    assert metrics["total_trades"] == 0
    assert metrics["win_rate"] == 0.0
    assert metrics["avg_r_multiple"] == 0.0
    assert metrics["max_drawdown_usd"] == 0.0


def test_write_report_writes_csv_with_contract_columns_and_summary_json(tmp_path):
    trades = make_trade_log()
    output_dir = tmp_path / "run-out"

    result = write_report(trades, str(output_dir))

    csv_path = output_dir / "trades.csv"
    summary_path = output_dir / "summary.json"
    assert csv_path.exists()
    assert summary_path.exists()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(trades)
    for expected_col in ("entry_price", "exit_price", "quantity", "exit_reason"):
        assert expected_col in rows[0], f"trades.csv must include {expected_col!r} column"

    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)
    assert summary["total_trades"] == result["total_trades"]
    assert summary["win_rate"] == pytest.approx(result["win_rate"])


# ============================================================
# Costs / capital / equity-curve expansion (plan 2026-08-12)
# ============================================================

def test_metrics_costs_capital_and_extended_keys():
    trades = make_trade_log()
    m = compute_metrics(trades, starting_capital=100_000.0, commission_per_share=0.005)
    # 4 trades, qty 100/100/200/200 -> 600 shares x 2 sides x $0.005 = $6.00
    assert m["total_commission_usd"] == pytest.approx(6.0)
    # gross pnl = 1000-500+800-1000 = 300 -> net 294; per-trade net: +999,-501,+798,-1002
    assert m["net_pnl_usd"] == pytest.approx(294.0)
    assert m["final_portfolio_value_usd"] == pytest.approx(100_294.0)
    assert m["total_return_pct"] == pytest.approx(0.294)
    assert m["avg_win_usd"] == pytest.approx((999 + 798) / 2)
    assert m["avg_loss_usd"] == pytest.approx((-501 - 1002) / 2)
    assert m["profit_factor"] == pytest.approx((999 + 798) / 1503)
    assert m["per_symbol"]["US.AAA"]["total_trades"] == 1
    assert m["per_symbol"]["US.AAA"]["net_pnl_usd"] == pytest.approx(999.0)


def test_default_args_keep_legacy_gross_values():
    m = compute_metrics(make_trade_log())
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(1.2)
    assert m["max_drawdown_usd"] == pytest.approx(1000.0)


def test_equity_curve_flat_day_carry_and_sharpe():
    day1, day2 = recent_session_days(2)
    trades = [
        {"code": "US.AAA", "entry_price": 100.0, "exit_price": 110.0, "quantity": 100,
         "exit_reason": "TRAIL", "r_multiple": 2.0, "closed_at": f"{day1} 10:00:00"},
    ]
    curve = build_equity_curve(trades, 100_000.0, start=day1, end=day2)
    assert curve == [(day1, 101_000.0), (day2, 101_000.0)]  # flat day carries forward
    m = compute_metrics(trades, starting_capital=100_000.0, start=day1, end=day2)
    assert m["num_trading_days"] == 2
    assert m["max_drawdown_pct"] == 0.0
    assert m["sharpe_ratio"] > 0.0


def test_exposure_merges_overlapping_intervals():
    day1 = recent_session_days(1)[0]
    trades = [
        {"code": "US.AAA", "entry_price": 1.0, "exit_price": 1.0, "quantity": 1,
         "exit_reason": "STOP", "r_multiple": 0.0,
         "opened_at": f"{day1} 10:00:00", "closed_at": f"{day1} 11:00:00"},
        {"code": "US.BBB", "entry_price": 1.0, "exit_price": 1.0, "quantity": 1,
         "exit_reason": "STOP", "r_multiple": 0.0,
         "opened_at": f"{day1} 10:30:00", "closed_at": f"{day1} 11:30:00"},
    ]
    m = compute_metrics(trades, start=day1, end=day1)
    # union = 10:00-11:30 = 1.5h of one 6.5h session
    assert m["exposure_pct"] == pytest.approx(100.0 * 1.5 / 6.5)


def test_empty_trades_extended_zeroes():
    m = compute_metrics([], starting_capital=50_000.0)
    assert m["final_portfolio_value_usd"] == 50_000.0
    assert m["per_symbol"] == {} and m["total_trades"] == 0


def test_write_report_emits_equity_curve_and_opened_at_column(tmp_path):
    write_report(make_trade_log(), str(tmp_path))
    header = (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "opened_at" in header.split(",")
    curve_lines = (tmp_path / "equity_curve.csv").read_text(encoding="utf-8").strip().splitlines()
    assert curve_lines[0] == "date,equity"
    assert len(curve_lines) >= 2
