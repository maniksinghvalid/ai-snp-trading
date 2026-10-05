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


def test_live_1lot_buys_then_sells_one_share(ibs_cfg, monkeypatch, capsys, make_snapshot_row):
    mod = _load_probe()
    now = datetime.now(ET)
    row = make_snapshot_row("US.XLU", 100.5, 110.0, 100.0, now.strftime("%Y-%m-%d %H:%M:%S"))
    gw = MagicMock()
    gw.get_market_snapshot = AsyncMock(return_value=(0, pd.DataFrame([row])))
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
    asyncio.run(mod.live_1lot(ibs_cfg, gw, "US.XLU", now))
    assert calls == [("BUY", "US.XLU", 1), ("SELL", "US.XLU", 1)]
    assert "round-trip friction" in capsys.readouterr().out
