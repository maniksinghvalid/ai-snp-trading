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

from tests.backtester.fixtures import make_trade_log

mod = pytest.importorskip("backtester.report")

compute_metrics = mod.compute_metrics
write_report = mod.write_report


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
