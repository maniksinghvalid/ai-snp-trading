#!/usr/bin/env python3
"""
tests.backtester.experimental.test_run — CLI-level tests for
backtester.experimental.run (Phase 10, plan 10-03).

Fake-engine seam: patches backtester.experimental.run._build_engine and
._load_regime_labels directly (this module's own seam names), and patches
backtester.experimental.engine.group_by_day / .build_frame /
backtester.experimental.strategies.{ext2,orb,vwap_pb}_signals at their SOURCE
module via monkeypatch.setattr("dotted.path", fake) -- run.py's own
function-local imports pick up the patched callable at call time. No static
top-level import of backtester.experimental.{engine,strategies,indicators}
in this file (parallelism contract, 10-03-PLAN.md).

Plain def test_* functions, no pytest markers (project convention).
"""
import json

import pandas as pd

import backtester.experimental.run as run_mod

_ARMS_PATH = "backtester/experimental/arms.json"


# ============================================================
# Task 1: WINDOWS/MEGA24/COST_PROFILES, arg parser, cache guard
# ============================================================

def test_windows_table_locked():
    assert run_mod.WINDOWS["C"] == ("2024-09-02", "2024-12-31")
    assert len(run_mod.WINDOWS) == 5


def test_mega24_and_cost_profiles_tables():
    assert len(run_mod.MEGA24) == 24
    assert run_mod.MEGA24[0] == "US.AAPL"
    assert run_mod.COST_PROFILES["base"] == (0.005, 0.03)
    assert run_mod.COST_PROFILES["stress"] == (0.005, 0.05)
    assert run_mod.COST_PROFILES["zero"] == (0.0, 0.0)


def test_resolve_window_from_letter():
    args = run_mod.build_arg_parser().parse_args(["--window", "C", "--out", "/tmp/x"])
    assert run_mod._resolve_window(args) == ("2024-09-02", "2024-12-31")


def test_resolve_window_from_explicit_start_end():
    args = run_mod.build_arg_parser().parse_args(
        ["--start", "2025-01-02", "--end", "2025-01-31", "--out", "/tmp/x"]
    )
    assert run_mod._resolve_window(args) == ("2025-01-02", "2025-01-31")


def test_resolve_window_neither_is_an_error():
    args = run_mod.build_arg_parser().parse_args(["--out", "/tmp/x"])
    try:
        run_mod._resolve_window(args)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_resolve_window_both_is_an_error():
    args = run_mod.build_arg_parser().parse_args(
        ["--window", "C", "--start", "2025-01-02", "--end", "2025-01-31", "--out", "/tmp/x"]
    )
    try:
        run_mod._resolve_window(args)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_required_cache_paths_derives_padded_key_not_hand_typed():
    paths = run_mod._required_cache_paths(
        ["US.AAPL"], "2024-09-02", "2024-12-31", "backtester/cache/massive"
    )
    assert any(p.endswith("AAPL_5m_2024-08-03_2024-12-31.csv") for p in paths), paths
    assert any(p.endswith("AAPL_1d_2023-07-30_2024-12-31.csv") for p in paths), paths


def test_missing_cache_exits_1_prints_paths_and_constructs_no_massive_source(
    monkeypatch, tmp_path, capsys
):
    def _raise(*a, **kw):
        raise AssertionError("MassiveDataSource must not be constructed on a cache miss")

    monkeypatch.setattr(run_mod, "MassiveDataSource", _raise)
    rc = run_mod.main([
        "--window", "C", "--symbols", "US.NOSUCHSYM",
        "--arms", _ARMS_PATH, "--out", str(tmp_path / "out"),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "NOSUCHSYM_5m_2024-08-03_2024-12-31.csv" in err


def test_allow_fetch_bypasses_the_guard():
    assert run_mod._assert_cache(
        ["US.NOSUCHSYM"], "2024-09-02", "2024-12-31", "backtester/cache/massive",
        allow_fetch=True,
    ) is True


def test_assert_cache_false_when_any_required_file_missing():
    assert run_mod._assert_cache(
        ["US.NOSUCHSYM"], "2024-09-02", "2024-12-31", "backtester/cache/massive",
        allow_fetch=False,
    ) is False


def test_only_filters_preserving_arms_json_order():
    defaults, arms = run_mod._load_arms(_ARMS_PATH, "orb30_base,ext2_base")
    assert [a["name"] for a in arms] == ["ext2_base", "orb30_base"]


def test_only_unknown_arm_raises_naming_it():
    try:
        run_mod._load_arms(_ARMS_PATH, "ext2_base,not_a_real_arm")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "not_a_real_arm" in str(exc)


def test_only_unknown_arm_cli_exits_1(tmp_path, capsys):
    rc = run_mod.main([
        "--window", "C", "--only", "not_a_real_arm",
        "--arms", _ARMS_PATH, "--out", str(tmp_path / "out"),
    ])
    assert rc == 1
    assert "not_a_real_arm" in capsys.readouterr().err


def test_run_py_has_no_module_level_experimental_imports():
    with open("backtester/experimental/run.py", encoding="utf-8") as f:
        for line in f:
            assert not line.startswith("from backtester.experimental")
            assert not line.startswith("import backtester.experimental")


def test_run_py_never_references_broker_gateway_or_state_store():
    with open("backtester/experimental/run.py", encoding="utf-8") as f:
        text = f.read()
    for token in ("MoomooGateway", "StateStore", "place_order"):
        assert token not in text


def test_parse_set_overrides_json_and_string_fallback():
    overrides = run_mod._parse_set_overrides(["confirm_bars=3", "stop_basis=prev_day"])
    assert overrides == {"confirm_bars": 3, "stop_basis": "prev_day"}


def test_resolve_params_overlays_defaults_arm_and_set_overrides():
    defaults, arms = run_mod._load_arms(_ARMS_PATH, "ext2_n3")
    params = run_mod._resolve_params(defaults, arms[0], {"confirm_bars": 99})
    assert params["confirm_bars"] == 99  # --set wins over the arm's own delta
    assert params["strategy"] == "ext2"
    assert params["arm"] == "ext2_n3"
    assert params["sma_period"] == 10  # untouched default passes through


def test_git_sha_never_raises(monkeypatch):
    import subprocess

    def _boom(*a, **kw):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert run_mod._git_sha() == "unknown"


# ============================================================
# Task 2: per-window arm loop
# ============================================================

_CANNED_TRADES = [
    {
        "code": "US.AAPL", "opened_at": "2024-09-03 10:00:00", "entry_price": 100.0,
        "exit_price": 101.0, "quantity": 10, "exit_reason": "force_close",
        "r_multiple": 0.5, "closed_at": "2024-09-03 15:51:00", "side": "long",
        "n_legs": 1,
    },
    {
        "code": "US.MSFT", "opened_at": "2024-09-03 10:05:00", "entry_price": 50.0,
        "exit_price": 49.0, "quantity": 20, "exit_reason": "stop",
        "r_multiple": -1.0, "closed_at": "2024-09-03 11:00:00", "side": "short",
        "n_legs": 1,
    },
]


class _CannedEngine:
    gap_through_entries = 3

    def __init__(self, trades):
        self._trades = trades

    def run(self, days):
        return self._trades


def _fake_build_engine_factory(trades):
    def _fake(frames, signals, params, feed, slippage, stop_fill, regime_fn=None):
        rows = []
        for t in trades:
            row = dict(t)
            row["strategy"] = params.get("strategy")
            row["arm"] = params.get("arm")
            row["regime"] = "none"
            rows.append(row)
        return _CannedEngine(rows)
    return _fake


def _patch_arm_loop_seams(monkeypatch, group_calls=None, feed_calls=None, trades=None):
    """Bypasses the cache guard, patches the feed constructor + group_by_day +
    build_frame + every strategy signal fn + _build_engine so the arm loop
    runs with zero network access and zero dependency on 10-02's real
    frame/signal shapes."""
    monkeypatch.setattr(run_mod, "_assert_cache", lambda *a, **kw: True)
    monkeypatch.setattr(run_mod, "MassiveDataSource", lambda *a, **kw: object())

    class _FakeFeed:
        def __init__(self, *a, **kw):
            if feed_calls is not None:
                feed_calls["n"] += 1

        def next_bar(self, code, after):
            return None

    monkeypatch.setattr(run_mod, "SimulatedBarFeed", _FakeFeed)

    def _fake_group_by_day(feed, days):
        if group_calls is not None:
            group_calls["n"] += 1
        return {d: [] for d in days}

    monkeypatch.setattr("backtester.experimental.engine.group_by_day", _fake_group_by_day)
    monkeypatch.setattr(
        "backtester.experimental.engine.build_frame",
        lambda feed, code, params: pd.DataFrame({"close": [1.0, 2.0]}),
    )
    fake_signals = lambda frame, p: frame
    monkeypatch.setattr("backtester.experimental.strategies.ext2_signals", fake_signals)
    monkeypatch.setattr("backtester.experimental.strategies.orb_signals", fake_signals)
    monkeypatch.setattr("backtester.experimental.strategies.vwap_pb_signals", fake_signals)

    monkeypatch.setattr(run_mod, "_build_engine", _fake_build_engine_factory(trades if trades is not None else _CANNED_TRADES))


def test_multi_arm_run_one_feed_one_grouping_four_files_per_arm(monkeypatch, tmp_path):
    group_calls, feed_calls = {"n": 0}, {"n": 0}
    _patch_arm_loop_seams(monkeypatch, group_calls=group_calls, feed_calls=feed_calls)

    out = tmp_path / "out"
    rc = run_mod.main([
        "--window", "C", "--only", "ext2_base,ext2_n3,ext2_n12",
        "--arms", _ARMS_PATH, "--out", str(out),
    ])
    assert rc == 0
    assert feed_calls["n"] == 1
    assert group_calls["n"] == 1

    for arm_name in ("ext2_base", "ext2_n3", "ext2_n12"):
        arm_dir = out / "C" / "base" / arm_name
        for fname in ("trades.csv", "equity_curve.csv", "summary.json", "params.json"):
            assert (arm_dir / fname).exists(), f"{arm_dir / fname} missing"


def test_trades_csv_header_end_to_end(monkeypatch, tmp_path):
    _patch_arm_loop_seams(monkeypatch)
    out = tmp_path / "out"
    rc = run_mod.main([
        "--window", "C", "--only", "ext2_base", "--arms", _ARMS_PATH, "--out", str(out),
    ])
    assert rc == 0
    with open(out / "C" / "base" / "ext2_base" / "trades.csv", newline="", encoding="utf-8") as f:
        header = f.readline().strip()
    assert header == (
        "code,opened_at,entry_price,exit_price,quantity,exit_reason,r_multiple,"
        "closed_at,side,strategy,arm,n_legs,regime"
    )


def test_params_json_has_all_required_keys(monkeypatch, tmp_path):
    _patch_arm_loop_seams(monkeypatch)
    out = tmp_path / "out"
    rc = run_mod.main([
        "--window", "C", "--only", "ext2_base", "--cost", "stress", "--stop-fill", "intrabar",
        "--arms", _ARMS_PATH, "--out", str(out),
    ])
    assert rc == 0
    with open(out / "C" / "stress" / "ext2_base" / "params.json", encoding="utf-8") as f:
        params_doc = json.load(f)
    for key in (
        "arm", "strategy", "params", "window", "start", "end", "cost_profile",
        "commission_per_share_usd", "slippage_per_share_usd", "stop_fill",
        "symbols", "git_sha", "gap_through_entries",
    ):
        assert key in params_doc, key
    assert params_doc["arm"] == "ext2_base"
    assert params_doc["strategy"] == "ext2"
    assert params_doc["window"] == "C"
    assert params_doc["start"] == "2024-09-02"
    assert params_doc["end"] == "2024-12-31"
    assert params_doc["cost_profile"] == "stress"
    assert params_doc["commission_per_share_usd"] == 0.005
    assert params_doc["slippage_per_share_usd"] == 0.05
    assert params_doc["stop_fill"] == "intrabar"
    assert params_doc["gap_through_entries"] == 3


def test_zero_trade_arm_still_writes_all_four_files_and_main_returns_0(monkeypatch, tmp_path):
    _patch_arm_loop_seams(monkeypatch, trades=[])
    out = tmp_path / "out"
    rc = run_mod.main([
        "--window", "C", "--only", "ext2_base", "--arms", _ARMS_PATH, "--out", str(out),
    ])
    assert rc == 0
    arm_dir = out / "C" / "base" / "ext2_base"
    for fname in ("trades.csv", "equity_curve.csv", "summary.json", "params.json"):
        assert (arm_dir / fname).exists()
    with open(arm_dir / "summary.json", encoding="utf-8") as f:
        summary = json.load(f)
    assert summary["total_trades"] == 0
