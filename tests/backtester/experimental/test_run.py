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
