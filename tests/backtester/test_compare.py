#!/usr/bin/env python3
"""
tests.backtester.test_compare — backtester.compare: one-fetch, N-config variant runner
(strategy-audit plan, P0-A).

Reuses test_harness.py's proven signal/fill/stop-out dataset (a real SignalEngine
gate pass is required to produce an actual closed trade) so the end-to-end test
below exercises a real trade, not a synthetic metrics dict.
"""
import json
import os

import backtester.compare as compare_mod
from tests.backtester.test_harness import _DAY as _E2E_DAY
from tests.backtester.test_harness import _mock_yf_download as _e2e_mock_yf_download

RULES_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "rules.json")


def _base_args(output_dir, rules_json, **overrides):
    args = {
        "--symbols": "US.TEST",
        "--start": _E2E_DAY,
        "--end": _E2E_DAY,
        "--output-dir": str(output_dir),
        "--rules-json": rules_json,
    }
    args.update(overrides)
    argv = []
    for k, v in args.items():
        argv.extend([k, v])
    return argv


def _write_variant_rules(tmp_path, name, **json_overrides):
    """Write a rules.json variant to tmp_path/name.json; overrides shallow-merge
    into the top-level dict (sufficient for the label-driven knobs this test flips)."""
    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        rules = json.load(f)
    rules.update(json_overrides)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(rules))
    return str(path)


def test_parse_rules_json_variants_label_equals_path():
    variants = compare_mod._parse_variants("base=rules.json,alt=other.json")
    assert variants == [("base", "rules.json"), ("alt", "other.json")]


def test_parse_rules_json_variants_bare_path_labels_by_basename():
    variants = compare_mod._parse_variants("rules.json,configs/alt_rules.json")
    assert variants == [("rules", "rules.json"), ("alt_rules", "configs/alt_rules.json")]


def test_bad_rules_json_list_exits_nonzero(tmp_path, capsys):
    rc = compare_mod.main(_base_args(tmp_path, ""))
    assert rc == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_end_to_end_two_variants_write_separate_reports_and_print_table(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr("yfinance.download", _e2e_mock_yf_download)

    base_path = _write_variant_rules(tmp_path, "base")
    # A variant with a materially different risk knob (still a VALID StrategyConfig)
    # so the two variants are provably independent configs, not just relabeled copies.
    alt_path = _write_variant_rules(tmp_path, "alt", risk={
        "max_risk_per_trade_pct": 1.0, "max_position_size_pct_of_portfolio": 10,
        "max_concurrent_positions": 5, "max_trades_per_day": 5,
        "sizing_equity_usd": 50000, "daily_circuit_breaker_r": 2.0,
    })

    output_dir = tmp_path / "compare-out"
    rc = compare_mod.main(_base_args(
        output_dir, f"base={base_path},alt={alt_path}",
    ))

    assert rc == 0
    assert (output_dir / "base" / "summary.json").exists()
    assert (output_dir / "alt" / "summary.json").exists()

    with open(output_dir / "base" / "summary.json", encoding="utf-8") as f:
        base_summary = json.load(f)
    with open(output_dir / "alt" / "summary.json", encoding="utf-8") as f:
        alt_summary = json.load(f)
    assert base_summary["assumptions"]["starting_capital_usd"] == 100_000.0
    assert alt_summary["assumptions"]["starting_capital_usd"] == 50_000.0

    out = capsys.readouterr().out
    assert "base" in out and "alt" in out
    assert "sharpe" in out.lower() or "sortino" in out.lower()


def test_compare_never_constructs_a_broker_gateway():
    import inspect

    source = inspect.getsource(compare_mod)
    assert "MoomooGateway" not in source
