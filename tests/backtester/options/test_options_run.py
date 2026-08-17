#!/usr/bin/env python3
"""
tests.backtester.options.test_options_run — backtester.options_run CLI: --set overrides,
validate-before-fetch ordering, and an offline smoke run (T-09-10).

All tests are offline: `load_massive_api_key`, `MassiveDataSource` and `OptionChainSource`
are monkeypatched on the `backtester.options_run` module object, and `OptionsBacktestEngine`
is replaced with a fake carrying a pre-built D-16-shaped trade log for the smoke test (the
engine's own replay logic is already covered by tests/backtester/options/test_engine.py --
this module tests CLI wiring only).
"""
import json
import os

import pytest

import backtester.options_run as run_mod

RULES_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "rules_options.json")


def _base_args(out_dir, **overrides):
    args = {
        "--rules": RULES_JSON_PATH,
        "--symbols": "US.SPY",
        "--start": "2024-11-18",
        "--end": "2024-11-19",
        "--out": str(out_dir),
    }
    args.update(overrides)
    argv = []
    for k, v in args.items():
        argv.extend([k, v])
    return argv


class _FakeChain:
    """Minimal OptionChainSource stand-in: no-op for every method the CLI/engine
    warm-up priming path calls."""

    def __init__(self, source, underlying, start, end):
        pass

    def load(self, *a, **k):
        return self

    def underlying_close(self, day):
        return None

    def contracts_for_day(self, *a, **k):
        return []


_FAKE_TRADE = {
    "symbol": "US.SPY", "structure": "iron_condor",
    "opened_date": "2024-11-18", "closed_date": "2024-11-19",
    "expiry": "2025-01-03",
    "short_put_strike": 400.0, "long_put_strike": 395.0,
    "short_call_strike": 450.0, "long_call_strike": 455.0,
    "credit_per_spread": 1.20, "qty": 1, "width": 5.0,
    "exit_reason": "profit_target", "pnl_usd": 60.0, "max_loss_usd": 380.0,
    "dte_at_open": 46, "dte_at_close": 45, "ivr_at_entry": 35.0,
    "credit_captured_pct": 50.0, "commission_usd": 2.6,
    "carried_mark": False, "min_leg_volume": 120,
}


class _FakeEngine:
    def __init__(self, cfg, chains, **kwargs):
        self.cfg = cfg
        self.chains = chains
        self.trade_log = [dict(_FAKE_TRADE)]

    def run(self, days):
        pass


def _patch_offline(monkeypatch):
    monkeypatch.setattr(run_mod, "load_massive_api_key", lambda: "fake-key")
    monkeypatch.setattr(run_mod, "MassiveDataSource", lambda api_key: object())
    monkeypatch.setattr(run_mod, "OptionChainSource", _FakeChain)


# ============================================================
# apply_overrides / --set
# ============================================================

def test_set_override_writes_effective_config(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "load_massive_api_key", lambda: (_ for _ in ()).throw(
        run_mod.MassiveApiError("no key in offline test")
    ))

    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        before = f.read()

    exit_code = run_mod.main(_base_args(tmp_path) + ["--set", "entry.ivr_min=20"])

    assert exit_code == 1  # fails later at the (monkeypatched) API-key step
    config_path = tmp_path / "config.json"
    assert config_path.exists()
    effective = json.loads(config_path.read_text())
    assert effective["entry"]["ivr_min"] == 20

    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        after = f.read()
    assert before == after  # rules_options.json on disk never rewritten (D-15)


def test_set_unknown_path_exits_1(tmp_path, capsys):
    exit_code = run_mod.main(_base_args(tmp_path) + ["--set", "entry.not_a_real_key=1"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[ERROR]" in captured.err
    assert not (tmp_path / "config.json").exists()


def test_apply_overrides_unknown_top_level_path_raises():
    with pytest.raises(ValueError, match="does not exist"):
        run_mod.apply_overrides({"entry": {"ivr_min": 30}}, ["structure.short_delta=0.1"])


def test_apply_overrides_string_fallback_when_not_json():
    result = run_mod.apply_overrides({"service": {"kill_file": "x"}}, ["service.kill_file=y.txt"])
    assert result["service"]["kill_file"] == "y.txt"


# ============================================================
# Validate-before-fetch ordering
# ============================================================

def test_bad_date_exits_before_config_load(monkeypatch, tmp_path, capsys):
    def _boom(_path):
        raise AssertionError("load_options_config must not run after a bad date")

    monkeypatch.setattr(run_mod, "load_options_config", _boom)

    exit_code = run_mod.main([
        "--rules", RULES_JSON_PATH,
        "--symbols", "US.SPY",
        "--start", "20xx",
        "--end", "2024-11-19",
        "--out", str(tmp_path),
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[ERROR]" in captured.err
    assert not (tmp_path / "config.json").exists()


def test_missing_api_key_exits_1(monkeypatch, tmp_path, capsys):
    def _raise():
        raise run_mod.MassiveApiError("MASSIVE_API_KEY is not set")

    monkeypatch.setattr(run_mod, "load_massive_api_key", _raise)

    exit_code = run_mod.main(_base_args(tmp_path))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[ERROR]" in captured.err
    assert "MASSIVE_API_KEY" in captured.err
    # config.json IS written before the API-key step (D-15/D-16 ordering).
    assert (tmp_path / "config.json").exists()


# ============================================================
# Offline smoke run
# ============================================================

def test_cli_smoke_offline(monkeypatch, tmp_path):
    _patch_offline(monkeypatch)
    monkeypatch.setattr(run_mod, "OptionsBacktestEngine", _FakeEngine)

    exit_code = run_mod.main(_base_args(tmp_path))

    assert exit_code == 0
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "trades.csv").exists()
    assert (tmp_path / "summary.json").exists()

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["total_trades"] == 1

    trades_csv = (tmp_path / "trades.csv").read_text().splitlines()
    assert len(trades_csv) >= 2  # header + one trade row


def test_help_exits_0(capsys):
    with pytest.raises(SystemExit) as exc_info:
        run_mod.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--rules", "--symbols", "--start", "--end", "--set", "--out"):
        assert flag in out
