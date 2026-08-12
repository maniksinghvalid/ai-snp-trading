#!/usr/bin/env python3
"""
tests.backtester.test_run — backtester.run CLI: arg validation, config-error exit,
live-DB collision guard, and end-to-end feed -> harness -> report wiring (BT-01/03/04).

Reuses the exact real-construction pattern tests/backtester/test_harness.py established
(drive the REAL main() with only the yfinance seam patched) -- the full end-to-end test
below reuses that module's own dedicated signal/fill/stop-out dataset (not the shared
ahead-only fixture) because a real SignalEngine gate pass (I1/I2/I3/entry-window) is
required to produce an actual closed trade; the ahead-only fixture's breakout bar closes
below its own high and never clears the real I2 gate (documented in 06-05-SUMMARY.md
deviation 5) -- reusing test_harness.py's proven dataset avoids re-deriving that fixture.
"""
import backtester.run as run_mod
from tests.backtester.fixtures import recent_session_days
from tests.backtester.test_harness import _DAY as _E2E_DAY
from tests.backtester.test_harness import _mock_yf_download as _e2e_mock_yf_download

# Runtime-derived anchor (never a hardcoded literal) so the rolling ~60-calendar-day
# window guard can never turn this suite red on a future calendar date (06-12
# gap-closure, WR-06 time bomb).
_DAY = recent_session_days(1)[0]


def _base_args(output_dir, **overrides):
    args = {
        "--symbols": "US.AAPL",
        "--start": _DAY,
        "--end": _DAY,
        "--output-dir": str(output_dir),
    }
    args.update(overrides)
    argv = []
    for k, v in args.items():
        argv.extend([k, v])
    return argv


# ============================================================
# Task 1 -- argparse + config load + input validation + DB collision guard
# ============================================================

def test_bad_start_date_exits_nonzero_before_fetch(tmp_path, capsys):
    exit_code = run_mod.main([
        "--symbols", "US.AAPL",
        "--start", "not-a-date",
        "--end", _DAY,
        "--output-dir", str(tmp_path),
    ])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "[ERROR]" in captured.err


def test_empty_symbols_exits_nonzero(tmp_path, capsys):
    exit_code = run_mod.main([
        "--symbols", "   ",
        "--start", _DAY,
        "--end", _DAY,
        "--output-dir", str(tmp_path),
    ])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "[ERROR]" in captured.err


def test_config_error_exits_1_with_stderr_message(monkeypatch, tmp_path, capsys):
    from bot.config.loader import ConfigError

    def fake_load(_path):
        raise ConfigError("malformed rules.json")

    monkeypatch.setattr(run_mod, "load_strategy_config", fake_load)

    exit_code = run_mod.main(_base_args(tmp_path))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[ERROR]" in captured.err


def test_db_collision_guard_refuses_live_db_path(monkeypatch, tmp_path, capsys):
    from bot.state.store import DEFAULT_DB_PATH

    monkeypatch.setattr(run_mod, "_scratch_db_path", lambda: DEFAULT_DB_PATH)

    exit_code = run_mod.main(_base_args(tmp_path))

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "[ERROR]" in captured.err
    assert DEFAULT_DB_PATH in captured.err


def test_run_py_never_constructs_a_broker_gateway():
    import inspect

    source = inspect.getsource(run_mod)
    assert "MoomooGateway" not in source


# ============================================================
# Task 2 -- SimulatedBarFeed -> BacktestHarness -> write_report end-to-end
# ============================================================

def test_out_of_window_start_exits_nonzero_with_error(tmp_path, capsys):
    exit_code = run_mod.main([
        "--symbols", "US.AAPL",
        "--start", "2000-01-01",
        "--end", "2000-01-02",
        "--output-dir", str(tmp_path),
    ])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "[ERROR]" in captured.err


def test_end_to_end_run_writes_summary_and_nonempty_trades_csv(monkeypatch, tmp_path):
    monkeypatch.setattr("yfinance.download", _e2e_mock_yf_download)
    output_dir = tmp_path / "out"

    exit_code = run_mod.main([
        "--symbols", "US.TEST",
        "--start", _E2E_DAY,
        "--end", _E2E_DAY,
        "--output-dir", str(output_dir),
    ])

    assert exit_code == 0
    assert (output_dir / "summary.json").exists()

    trades_csv = output_dir / "trades.csv"
    assert trades_csv.exists()
    rows = trades_csv.read_text().splitlines()
    assert len(rows) >= 2, "expected a header row plus at least one trade row"


# ============================================================
# Costs / capital / source knobs (plan 2026-08-12)
# ============================================================

import os as _os

RULES_JSON_PATH = _os.path.join(_os.path.dirname(__file__), "..", "..", "rules.json")


def test_interval_other_than_5m_rejected(tmp_path, capsys):
    rc = run_mod.main(_base_args(tmp_path) + ["--interval", "1m"])
    assert rc == 1
    assert "5m" in capsys.readouterr().err


def test_negative_costs_rejected(tmp_path, capsys):
    rc = run_mod.main(_base_args(tmp_path) + ["--commission-per-share", "-0.01"])
    assert rc == 1


def test_massive_source_without_api_key_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env fallback in the tmp cwd
    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        (tmp_path / "rules.json").write_text(f.read())
    rc = run_mod.main(_base_args(tmp_path / "out") + ["--source", "massive"])
    assert rc == 1
    assert "MASSIVE_API_KEY" in capsys.readouterr().err


def test_flags_plumb_capital_slippage_and_report_kwargs(monkeypatch, tmp_path):
    captured = {}

    class FakeFeed:
        def __init__(self, *a, **k):
            pass

    class FakeHarness:
        def __init__(self, cfg, feed, store, slippage_usd=0.0):
            captured["cfg"] = cfg
            captured["slippage_usd"] = slippage_usd
            self.trade_log = []

        def setup_day(self, day, symbols):
            pass

        async def run(self):
            pass

    def fake_write_report(trades, output_dir, **kwargs):
        captured["report_kwargs"] = kwargs
        return {}

    monkeypatch.chdir(tmp_path)
    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        (tmp_path / "rules.json").write_text(f.read())
    monkeypatch.setattr(run_mod, "SimulatedBarFeed", FakeFeed)
    monkeypatch.setattr(run_mod, "BacktestHarness", FakeHarness)
    monkeypatch.setattr(run_mod, "write_report", fake_write_report)

    rc = run_mod.main(_base_args(tmp_path / "out") + [
        "--starting-capital", "55000",
        "--commission-per-share", "0.005",
        "--slippage-usd", "0.02",
    ])
    assert rc == 0
    assert captured["cfg"].sizing_equity_usd == 55_000.0
    assert captured["slippage_usd"] == 0.02
    assert captured["report_kwargs"] == {
        "starting_capital": 55_000.0,
        "commission_per_share": 0.005,
        "start": _DAY,
        "end": _DAY,
    }
