#!/usr/bin/env python3
"""
tests/ibs/test_service.py — IbsBot gate, reconcile, decision-job guards and exits.

Real IbsStore on a tmp DB; gateway / alerter / kill switch are mocks. The clock is
always injected (set_now) — never the wall clock.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def audits(monkeypatch):
    rec = []
    monkeypatch.setattr("bot.ibs.service.append_audit", lambda e: rec.append(e))
    return rec


def _row(code="US.SPY", pid="P1", qty=12, status="OPEN", entry_date="2026-10-01",
         entry_price=770.10, **over):
    row = {"position_id": pid, "code": code, "qty": qty, "entry_date": entry_date,
           "entry_price": entry_price, "entry_order_id": "E1", "status": status,
           "opened_at": "2026-10-01T15:55:00-04:00"}
    row.update(over)
    return row


def _alerts(bot):
    return [c.args[0] for c in bot._alerter.send.await_args_list]


# ============================================================
# Task 1: construction, gate, reconcile
# ============================================================

def test_construction_contract(make_ibs_bot, ibs_store):
    bot = make_ibs_bot()
    assert bot._entries_enabled is False
    assert bot._position_manager is None
    assert bot._bar_agg is None
    assert bot._decision_task is None
    assert bot._store is ibs_store
    assert str(bot._scheduler.timezone) == "America/New_York"
    assert bot._cfg.watchdog_poll_interval_s == 60
    assert bot._cfg.watchdog_reconnect_initial_s == 5
    assert bot._cfg.watchdog_reconnect_cap_s == 300


def test_readiness_gate_order(make_ibs_bot, ibs_gateway, ibs_kill_switch):
    bot = make_ibs_bot()
    calls = []
    ibs_gateway.connect.side_effect = lambda: calls.append("connect")
    orig = ibs_gateway.get_positions

    async def gp(*a, **k):
        calls.append("get_positions")
        return await orig(*a, **k)

    ibs_gateway.get_positions = gp
    ibs_kill_switch.install.side_effect = lambda: calls.append("install")
    _run(bot._readiness_gate())
    assert calls == ["connect", "get_positions", "install"]
    assert bot._entries_enabled is True


def test_readiness_gate_connect_failure(make_ibs_bot, ibs_gateway, ibs_kill_switch):
    bot = make_ibs_bot()
    ibs_gateway.connect.side_effect = RuntimeError("paper guard")
    with pytest.raises(RuntimeError, match="paper guard"):
        _run(bot._readiness_gate())
    ibs_kill_switch.install.assert_not_called()
    assert bot._entries_enabled is False


def test_startup_reconcile_flags_opening_and_closing(make_ibs_bot, ibs_store, audits):
    ibs_store.insert_position(_row("US.SPY", "P1", status="OPENING"))
    ibs_store.insert_position(_row("US.QQQ", "P2", status="CLOSING"))
    bot = make_ibs_bot()
    _run(bot.reconcile(startup=True))
    assert {p["status"] for p in ibs_store.get_active_positions()} == {"NEEDS_ATTENTION"}
    alerts = _alerts(bot)
    assert len(alerts) == 2 and all("IBS NEEDS ATTENTION" in a for a in alerts)
    assert [a["event"] for a in audits] == ["ibs_reconcile_incomplete"] * 2


def test_steady_state_does_not_flag_opening(make_ibs_bot, ibs_store):
    ibs_store.insert_position(_row(status="OPENING"))
    bot = make_ibs_bot()
    _run(bot.reconcile())
    assert ibs_store.get_active_positions()[0]["status"] == "OPENING"
    bot._alerter.send.assert_not_awaited()


def test_reconcile_match_is_silent(make_ibs_bot, ibs_store, ibs_gateway, make_positions_df):
    ibs_store.insert_position(_row())
    ibs_gateway.get_positions.return_value = (0, make_positions_df({"US.SPY": 12}))
    bot = make_ibs_bot()
    _run(bot.reconcile())
    assert ibs_store.get_active_positions()[0]["status"] == "OPEN"
    bot._alerter.send.assert_not_awaited()


@pytest.mark.parametrize("holdings", [{}, {"US.SPY": 0}, {"US.SPY": 15}])
def test_reconcile_mismatch(make_ibs_bot, ibs_store, ibs_gateway, make_positions_df,
                            audits, holdings):
    ibs_store.insert_position(_row())
    ibs_gateway.get_positions.return_value = (0, make_positions_df(holdings))
    bot = make_ibs_bot()
    _run(bot.reconcile())
    assert ibs_store.get_active_positions()[0]["status"] == "NEEDS_ATTENTION"
    assert len(_alerts(bot)) == 1 and "IBS NEEDS ATTENTION" in _alerts(bot)[0]
    assert audits[0]["event"] == "ibs_reconcile_mismatch"
    assert audits[0]["expected_qty"] == 12


def test_reconcile_mismatch_alerts_once(make_ibs_bot, ibs_store):
    ibs_store.insert_position(_row())
    bot = make_ibs_bot()
    _run(bot.reconcile())
    _run(bot.reconcile())
    assert len(_alerts(bot)) == 1


def test_external_holdings_ignored(make_ibs_bot, ibs_store, ibs_gateway,
                                   make_positions_df, monkeypatch):
    ibs_gateway.get_positions.return_value = (
        0, make_positions_df({"US.XLU": 100, "US.DIVO": 630}))
    logged = []
    monkeypatch.setattr("bot.ibs.service._logger.info",
                        lambda ev, **kw: logged.append((ev, kw)))
    bot = make_ibs_bot()
    broker = _run(bot.reconcile())
    assert broker == {"US.XLU": 100, "US.DIVO": 630}
    assert ibs_store.get_active_positions() == []
    for m in ("place_order", "cancel_order"):
        getattr(ibs_gateway, m).assert_not_awaited()
    ext = [kw for ev, kw in logged if ev == "ibs_reconcile_external_ignored"]
    assert ext and ext[0]["count"] == 1 and ext[0]["codes"] == ["US.XLU"]


def test_reconcile_query_failure(make_ibs_bot, ibs_store, ibs_gateway):
    ibs_store.insert_position(_row())
    ibs_gateway.get_positions.return_value = (-1, None)
    bot = make_ibs_bot()
    with pytest.raises(RuntimeError):
        _run(bot.reconcile())
    assert ibs_store.get_active_positions()[0]["status"] == "OPEN"
    bot._alerter.send.assert_not_awaited()


def test_alert_values_escaped(make_ibs_bot, ibs_store):
    ibs_store.insert_position(_row(code="US.<X>"))
    bot = make_ibs_bot()
    _run(bot.reconcile())
    assert "US.&lt;X&gt;" in _alerts(bot)[0]
