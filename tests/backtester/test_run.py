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
