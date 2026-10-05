#!/usr/bin/env python3
"""tests/ibs/test_operator_tooling.py — probe / plist / runbook / CLAUDE.md checks (12-07)."""
import asyncio
import dataclasses
import importlib.util
import os
import plistlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.safety.et_helpers import ET

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "uat_ibs_probe", os.path.join(ROOT, "scripts", "uat_ibs_probe.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Probe
# ============================================================

def test_live_1lot_without_confirm_exits_2_before_gateway(monkeypatch):
    mod = _load_probe()
    gw_cls = MagicMock()
    monkeypatch.setattr(mod, "MoomooGateway", gw_cls)
    with pytest.raises(SystemExit) as e:
        mod.main(["--live-1lot"])
    assert e.value.code == 2
    gw_cls.assert_not_called()


def test_bad_rules_exits_1_before_gateway(monkeypatch, tmp_path, capsys):
    mod = _load_probe()
    gw_cls = MagicMock()
    monkeypatch.setattr(mod, "MoomooGateway", gw_cls)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mod.main(["--rules", str(bad)])
    assert e.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err
    gw_cls.assert_not_called()


def test_probe_is_read_only_and_prints_plan(ibs_cfg, tmp_path, capsys,
                                                  make_snapshot_row, make_positions_df):
    mod = _load_probe()
    cfg = dataclasses.replace(ibs_cfg, state_db=str(tmp_path / "absent.db"))
    ts = "2026-10-05 15:44:58.000"
    rows = [
        make_snapshot_row("US.SPY", 101.0, 110.0, 100.0, ts),             # IBS 0.10
        make_snapshot_row("US.QQQ", 105.0, 110.0, 100.0, ts),             # 0.50
        make_snapshot_row("US.IWM", 109.0, 110.0, 100.0, ts),             # 0.90
        make_snapshot_row("US.DIA", 101.0, 110.0, 100.0, "2026-10-02 15:59:00.000"),  # stale
        make_snapshot_row("US.XLU", 100.5, 110.0, 100.0, ts),             # 0.05 but held externally
    ]
    gw = MagicMock()
    gw.get_market_snapshot = AsyncMock(return_value=(0, pd.DataFrame(rows)))
    gw.get_positions = AsyncMock(return_value=(0, make_positions_df({"US.XLU": 5})))
    gw.place_order = AsyncMock()
    gw.cancel_order = AsyncMock()

    asyncio.run(mod.probe(cfg, gw, datetime(2026, 10, 5, 15, 45, tzinfo=ET)))

    out = capsys.readouterr().out
    gw.place_order.assert_not_awaited()
    gw.cancel_order.assert_not_awaited()
    for code in ("US.SPY", "US.QQQ", "US.IWM", "US.DIA"):
        assert code in out
    assert "0.10" in out
    assert "stale_date" in out
    assert "external" in out
    assert "WOULD ENTER" in out and "US.SPY" in out and "qty 98" in out
    assert "WOULD ENTER US.XLU" not in out
    assert not os.path.exists(cfg.state_db)


def test_probe_reads_db_read_only_without_migrations(ibs_cfg, tmp_path, capsys, monkeypatch,
                                                     make_snapshot_row, make_positions_df):
    """WR-06: the read-only probe never opens the live bot's DB through IbsStore.open()."""
    from bot.ibs.store import IbsStore
    db = tmp_path / "ibs_state.db"
    st = IbsStore(str(db)).open()
    st.insert_position({"position_id": "P1", "code": "US.XLU", "qty": 5,
                        "entry_date": "2026-10-01", "status": "OPEN", "opened_at": "x"})
    st.close()
    before = db.read_bytes()
    mod = _load_probe()
    monkeypatch.setattr(mod, "IbsStore", MagicMock(side_effect=AssertionError("IbsStore used")))
    cfg = dataclasses.replace(ibs_cfg, state_db=str(db))
    gw = MagicMock()
    gw.get_market_snapshot = AsyncMock(return_value=(0, pd.DataFrame([])))
    gw.get_positions = AsyncMock(return_value=(0, make_positions_df({"US.XLU": 5})))
    asyncio.run(mod.probe(cfg, gw, datetime(2026, 10, 5, 15, 45, tzinfo=ET)))
    out = capsys.readouterr().out
    assert "US.XLU qty=5 OPEN" in out and "IBS row" in out
    assert db.read_bytes() == before


MON_10 = datetime(2026, 10, 5, 10, 0, tzinfo=ET)


def _live_env(ibs_cfg, tmp_path, monkeypatch, make_snapshot_row, make_positions_df,
              held=None, now=MON_10):
    """Probe module + mocks for live_1lot; never the wall clock, no leaked temp dirs."""
    mod = _load_probe()
    row = make_snapshot_row("US.XLU", 100.5, 110.0, 100.0, now.strftime("%Y-%m-%d %H:%M:%S"))
    gw = MagicMock()
    gw.get_market_snapshot = AsyncMock(return_value=(0, pd.DataFrame([row])))
    gw.get_positions = AsyncMock(return_value=(0, make_positions_df(held or {})))
    calls = []

    class FakeExecutor:
        def __init__(self, gateway, cfg):
            pass

        async def work(self, side, code, qty, last, deadline, on_placed=None):
            calls.append((side, code, qty))
            await on_placed("O-" + side)
            return ("O-" + side, last + (0.05 if side == "BUY" else -0.05), qty)

    monkeypatch.setattr(mod, "IbsExecutor", FakeExecutor)
    monkeypatch.setattr(mod, "now_et", lambda: now)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **k: str(scratch))
    cfg = dataclasses.replace(ibs_cfg, state_db=str(tmp_path / "ibs_state.db"))
    return mod, cfg, gw, calls


def test_live_1lot_buys_then_sells_one_share(ibs_cfg, tmp_path, monkeypatch, capsys,
                                             make_snapshot_row, make_positions_df):
    mod, cfg, gw, calls = _live_env(ibs_cfg, tmp_path, monkeypatch, make_snapshot_row,
                                    make_positions_df)
    asyncio.run(mod.live_1lot(cfg, gw, "US.XLU", MON_10))
    assert calls == [("BUY", "US.XLU", 1), ("SELL", "US.XLU", 1)]
    assert "round-trip friction" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["not_universe", "held", "active_row", "unreadable", "window"])
def test_live_1lot_refuses_unsafe_symbol(ibs_cfg, tmp_path, monkeypatch, capsys,
                                         make_snapshot_row, make_positions_df, case):
    """WR-06: never shift the share count of a symbol the live bot trades or could trade."""
    now = datetime(2026, 10, 5, 15, 48, tzinfo=ET) if case == "window" else MON_10
    mod, cfg, gw, calls = _live_env(
        ibs_cfg, tmp_path, monkeypatch, make_snapshot_row, make_positions_df,
        held={"US.XLU": 7} if case == "held" else None, now=now)
    symbol = "US.AAPL" if case == "not_universe" else "US.XLU"
    if case == "unreadable":
        gw.get_positions = AsyncMock(return_value=(-1, None))
    if case == "active_row":
        from bot.ibs.store import IbsStore
        st = IbsStore(cfg.state_db).open()
        st.insert_position({"position_id": "P1", "code": "US.XLU", "qty": 5,
                            "entry_date": "2026-10-01", "status": "NEEDS_ATTENTION",
                            "opened_at": "x"})
        st.close()
    assert asyncio.run(mod.live_1lot(cfg, gw, symbol, now)) is False
    assert calls == []
    assert "refusing" in capsys.readouterr().out


def test_live_1lot_refusal_exits_nonzero(monkeypatch, tmp_path):
    mod = _load_probe()
    gw = MagicMock()
    monkeypatch.setattr(mod, "MoomooGateway", MagicMock(return_value=gw))
    monkeypatch.setattr(mod, "get_gateway_config", MagicMock())
    monkeypatch.setattr(mod, "probe", AsyncMock())
    with pytest.raises(SystemExit) as e:
        mod.main(["--rules", os.path.join(ROOT, "rules_ibs.json"), "--live-1lot",
                  "--confirm", "--symbol", "US.AAPL"])
    assert e.value.code not in (0, None)
    gw.place_order.assert_not_called()
    gw.close.assert_called_once()


# ============================================================
# Deploy artefacts
# ============================================================

def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_ibs_plist_template():
    path = os.path.join(ROOT, "deploy", "com.bot.ibs.plist")
    with open(path, "rb") as f:
        pl = plistlib.load(f)
    assert pl["Label"] == "com.bot.ibs"
    assert pl["ProgramArguments"][1:] == ["-m", "bot", "--rules", "rules_ibs.json"]
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert pl["RunAtLoad"] is True
    assert pl["ThrottleInterval"] == 30
    assert pl["ExitTimeOut"] == 60  # IN-08: room for the SIGTERM sweep before SIGKILL
    env = pl["EnvironmentVariables"]
    assert env["PAPER_TRADING"] == "true"
    assert env["FUTU_TRD_ENV"] == "SIMULATE"
    assert env["FUTU_ACC_ID"] == "1727266"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["TELEGRAM_BOT_TOKEN"] == "YOUR_TOKEN_HERE"
    assert pl["StandardOutPath"].endswith("logs/ibs.stdout.log")
    assert pl["StandardErrorPath"].endswith("logs/ibs.stderr.log")
    assert "<string>REAL</string>" not in _read("deploy", "com.bot.ibs.plist")


def test_ibs_runbook_covers_cutover():
    text = _read("deploy", "IBS-RUNBOOK.md")
    for needle in (".bot_kill_ibs", "touch .bot_kill", ".bot_kill_options", "rules_ibs.json",
                   "launchctl", "pgrep", "--live-1lot --confirm", "NEEDS_ATTENTION",
                   "reports/ibs/latest.html", "logs/ibs.log", "chmod 600"):
        assert needle in text, needle


def test_claude_md_has_phase12_section():
    text = _read("CLAUDE.md")
    head = "## Phase 12 — IBS bot (ibs_etf_mean_reversion)"
    assert text.count(head) == 1
    assert text.index("## Phase 8 — Options bot") < text.index(head) < text.index("<!-- GSD:stack-start")
