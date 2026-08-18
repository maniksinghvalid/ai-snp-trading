#!/usr/bin/env python3
"""Tests for backtester.experimental.aggregate (plan 10-05, Task 1)."""
import csv as _csv

from backtester.experimental import aggregate as agg_mod
from tests.backtester import fixtures

_FIELDS = ["code", "opened_at", "entry_price", "exit_price", "quantity",
           "exit_reason", "r_multiple", "closed_at", "side"]


def _write_trades_csv(path, trades):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for t in trades:
            writer.writerow({k: t.get(k, "") for k in _FIELDS})


def _build_run_tree(tmp_path, window, cost, arm, trades):
    out_dir = tmp_path / "runs" / window / cost / arm
    out_dir.mkdir(parents=True)
    _write_trades_csv(out_dir / "trades.csv", trades)
    return tmp_path / "runs"


def test_aggregate_matches_hand_computed_metrics_on_make_trade_log(tmp_path, monkeypatch):
    """ALL-pooled PF == 1.2 (make_trade_log's documented overall PF); the day-1
    slice (trades AAA win + BBB loss) PF == 2.0; max_consecutive_losses == 1 (the
    win/loss sequence alternates). Uses monkeypatched SLICES since make_trade_log's
    dates are always 'recent' (fixtures.py convention), never inside the real
    2023-2026 SLICES ranges."""
    trades = fixtures.make_trade_log()
    day1 = trades[0]["closed_at"][:10]
    day2 = trades[2]["closed_at"][:10]
    monkeypatch.setattr(agg_mod, "SLICES", {"DAY1": (day1, day1), "DAY2": (day2, day2)})
    monkeypatch.setattr(agg_mod, "IS_SLICES", ["DAY1"])
    monkeypatch.setattr(agg_mod, "OOS_SLICES", ["DAY2"])

    # cost="zero" -> commission=0.0, matching make_trade_log's hand-computed (gross)
    # expected metrics exactly.
    root = _build_run_tree(tmp_path, "W", "zero", "test_arm", trades)
    rows = agg_mod.aggregate(str(root))

    all_row = next(r for r in rows if r["slice"] == "ALL")
    day1_row = next(r for r in rows if r["slice"] == "DAY1")

    assert round(all_row["profit_factor"], 4) == 1.2
    assert round(day1_row["profit_factor"], 4) == 2.0
    assert all_row["max_consecutive_losses"] == 1
    assert all_row["total_trades"] == 4


def test_bootstrap_ci_is_deterministic_and_not_nan():
    values = [0.5, -1.0, 2.0, -0.3, 1.1, 0.2]
    a = agg_mod.bootstrap_ci(values, n=1000, seed=0)
    b = agg_mod.bootstrap_ci(values, n=1000, seed=0)
    assert a == b
    assert all(v == v for v in a)  # not NaN


def test_bootstrap_ci_empty_input_never_raises():
    assert agg_mod.bootstrap_ci([], n=1000, seed=0) == (0.0, 0.0)


def test_infinite_pf_never_raises_and_md_prints_inf(tmp_path, monkeypatch):
    """Every 'loss' pnl is exactly 0 (zero-cost arm, breakeven trade) -> PF == inf,
    and the MD renderer must print the literal 'inf', never crash, never an empty
    cell."""
    day = "2023-07-05"
    monkeypatch.setattr(agg_mod, "SLICES", {"DAY": (day, day)})
    monkeypatch.setattr(agg_mod, "IS_SLICES", ["DAY"])
    monkeypatch.setattr(agg_mod, "OOS_SLICES", [])
    trades = [
        {"code": "US.AAA", "entry_price": 100.0, "exit_price": 110.0, "quantity": 100,
         "exit_reason": "TARGET", "r_multiple": 2.0, "closed_at": f"{day} 10:00:00"},
        {"code": "US.BBB", "entry_price": 100.0, "exit_price": 100.0, "quantity": 100,
         "exit_reason": "BE", "r_multiple": 0.0, "closed_at": f"{day} 11:00:00"},
    ]
    # cost="zero" -> commission=0.0, so the flat BBB trade's net pnl is exactly 0.
    root = _build_run_tree(tmp_path, "W", "zero", "arm_inf", trades)

    rows = agg_mod.aggregate(str(root))
    all_row = next(r for r in rows if r["slice"] == "ALL")
    assert all_row["profit_factor"] == float("inf")

    out = tmp_path / "out"
    rc = agg_mod.main(["--root", str(root), "--out", str(out)])
    assert rc == 0
    md_text = (out / "results.md").read_text()
    assert "inf" in md_text


def test_rows_missing_opened_at_fall_back_to_closed_at_for_slice_assignment(tmp_path, monkeypatch):
    """make_trade_log rows have NO opened_at -- slice bucketing must use closed_at."""
    trades = fixtures.make_trade_log()
    day1 = trades[0]["closed_at"][:10]
    assert all(not t.get("opened_at") for t in trades)
    monkeypatch.setattr(agg_mod, "SLICES", {"DAY1": (day1, day1)})
    monkeypatch.setattr(agg_mod, "IS_SLICES", [])
    monkeypatch.setattr(agg_mod, "OOS_SLICES", [])

    root = _build_run_tree(tmp_path, "W", "zero", "arm", trades[:2])  # both on day1
    rows = agg_mod.aggregate(str(root))
    day1_row = next(r for r in rows if r["slice"] == "DAY1")
    assert day1_row["total_trades"] == 2


def test_walk_forward_picks_best_by_pf_and_reports_next_slice_no_rerun():
    rows_by_arm_slice = {
        ("ext2_n3", "E1"): {"profit_factor": 1.5, "total_trades": 30},
        ("ext2_base", "E1"): {"profit_factor": 1.1, "total_trades": 30},
        ("ext2_n12", "E1"): {"profit_factor": 0.9, "total_trades": 30},
        ("ext2_n3", "E2"): {"profit_factor": 0.5, "total_trades": 25},
        ("ext2_base", "E2"): {"profit_factor": 1.3, "total_trades": 25},
        ("ext2_n12", "E2"): {"profit_factor": 1.0, "total_trades": 25},
    }
    families = [{"name": "ext2_confirm_bars", "members": ["ext2_n3", "ext2_base", "ext2_n12"]}]

    results = agg_mod.walk_forward(rows_by_arm_slice, families)
    step = next(r for r in results if r["from_slice"] == "E1" and r["to_slice"] == "E2")

    assert step["picked_arm"] == "ext2_n3"       # best PF (1.5) on E1
    assert step["next_slice_pf"] == 0.5          # ext2_n3's OWN E2 PF, not re-derived
    assert step["next_slice_trades"] == 25


def test_slices_module_constants():
    assert len(agg_mod.SLICES) == 9
    assert agg_mod.SLICES["C"] == ("2024-09-02", "2024-12-31")
    assert set(agg_mod.IS_SLICES) == {"E1", "E2", "E3", "C"}
    assert set(agg_mod.OOS_SLICES) == {"B", "A", "D1", "D2", "D3"}


def test_main_writes_results_csv_and_md_with_tjl_root(tmp_path):
    trades = [
        {"code": "US.AAA", "entry_price": 100.0, "exit_price": 110.0, "quantity": 100,
         "exit_reason": "TARGET", "r_multiple": 2.0, "closed_at": "2023-07-05 10:00:00",
         "opened_at": "2023-07-05 09:45:00"},
        {"code": "US.BBB", "entry_price": 100.0, "exit_price": 95.0, "quantity": 100,
         "exit_reason": "STOP", "r_multiple": -1.0, "closed_at": "2023-07-06 11:00:00",
         "opened_at": "2023-07-06 10:45:00"},
    ]
    root = _build_run_tree(tmp_path, "E", "base", "ext2_base", trades)

    tjl_root = tmp_path / "tjl"
    tjl_dir = tjl_root / "E1" / "tjl_base"
    tjl_dir.mkdir(parents=True)
    _write_trades_csv(tjl_dir / "trades.csv", trades)

    out = tmp_path / "out"
    rc = agg_mod.main(["--root", str(root), "--tjl-root", str(tjl_root), "--out", str(out)])

    assert rc == 0
    assert (out / "results.csv").exists()
    assert (out / "results.md").exists()
    csv_text = (out / "results.csv").read_text()
    assert "ext2_base" in csv_text
    assert "tjl_base" in csv_text
