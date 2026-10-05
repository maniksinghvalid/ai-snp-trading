#!/usr/bin/env python3
"""
tests/ibs/test_service_entries.py — IbsBot entry batch (after exits) and hard-cancel sweep.

Real IbsStore on a tmp DB; gateway / alerter / kill switch are mocks, executor.work is
replaced by a recording fake. The clock is always injected (set_now).
"""
import asyncio
import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ET = ZoneInfo("America/New_York")
MON_1550 = datetime(2026, 10, 5, 15, 50, tzinfo=ET)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def audits(monkeypatch):
    rec = []
    monkeypatch.setattr("bot.ibs.service.append_audit", lambda e: rec.append(e))
    return rec


def _alerts(bot):
    return [c.args[0] for c in bot._alerter.send.await_args_list]


def _row(code="US.SPY", pid="P1", qty=12, status="OPEN", entry_date="2026-10-01",
         entry_price=100.0, **over):
    row = {"position_id": pid, "code": code, "qty": qty, "entry_date": entry_date,
           "entry_price": entry_price, "entry_order_id": "E1", "status": status,
           "opened_at": "2026-10-01T15:55:00-04:00"}
    row.update(over)
    return row


class _AfterPlace:
    """executor result: record an order via on_placed, THEN raise `exc` (state unknown)."""

    def __init__(self, exc):
        self.exc = exc


@pytest.fixture
def env(make_ibs_bot, ibs_cfg, ibs_store, ibs_gateway, ibs_kill_switch,
        make_positions_df, make_snapshot_row, set_now, audits):
    """Bot at Mon 2026-10-05 15:50, 3 slots, executor.work recording every call."""
    class Env:
        pass
    e = Env()
    e.store, e.gw, e.audits, e.ks = ibs_store, ibs_gateway, audits, ibs_kill_switch
    e.bot = make_ibs_bot(dataclasses.replace(ibs_cfg, max_concurrent_positions=3))
    e.bot._entries_enabled = True
    e.calls, e.results, e.hooks = [], {}, {}
    e.at_call = {}
    set_now(MON_1550)

    async def fake(side, code, qty, last, deadline, on_placed=None):
        e.calls.append((side, code, qty, last))
        e.at_call[(side, code)] = [r["code"] for r in ibs_store.get_positions(("OPENING",))]
        hook = e.hooks.get((side, code))
        if hook:
            hook()
        res = e.results.get((side, code), (f"{side[0]}-{code}", last, qty))
        if isinstance(res, _AfterPlace):
            await on_placed(f"{side[0]}-{code}")
            raise res.exc
        if isinstance(res, Exception):
            raise res
        return res

    e.bot._executor.work = fake

    def setup(rows=(), snap=None, held=None, reads=None):
        """snap: {code: ibs_value | (last, high, low)}; held: broker {code: qty}."""
        for r in rows:
            ibs_store.insert_position(r)
        if held is None:
            held = {r["code"]: r["qty"] for r in rows if r["status"] == "OPEN"}
        reads = reads or [held]
        ibs_gateway.get_positions.side_effect = None
        ibs_gateway.get_positions.return_value = (0, make_positions_df(reads[-1]))
        if len(reads) > 1:
            ibs_gateway.get_positions.side_effect = [
                (0, make_positions_df(r)) for r in reads]
        recs = []
        for c, v in (snap or {}).items():
            last, high, low = v if isinstance(v, tuple) else (100.0 + 10.0 * v, 110.0, 100.0)
            recs.append(make_snapshot_row(c, last, high, low))
        ibs_gateway.get_market_snapshot.return_value = (0, pd.DataFrame(recs))
    e.setup = setup
    e.buys = lambda: [c for c in e.calls if c[0] == "BUY"]
    e.decide = lambda: _run(e.bot._job_decide())
    return e


THREE_LOW = {"US.XLU": 0.05, "US.XLP": 0.10, "US.XLB": 0.15, "US.XLE": 0.15, "US.SPY": 0.50}


# ============================================================
# Task 1: entry batch
# ============================================================

def test_three_slots_lowest_ibs_tie_by_universe_index(env):
    env.setup(snap=THREE_LOW)
    env.decide()
    assert [c[1] for c in env.buys()] == ["US.XLU", "US.XLP", "US.XLE"]
    assert len(env.store.get_positions(("OPEN",))) == 3


def test_sells_precede_buys(env):
    env.setup(rows=[_row("US.XLK", "K1", 5)], snap={**THREE_LOW, "US.XLK": 0.9},
              reads=[{"US.XLK": 5}, {}])
    env.decide()
    sides = [c[0] for c in env.calls]
    assert sides == ["SELL", "BUY", "BUY", "BUY"]


def test_exit_frees_exactly_one_slot(env):
    rows = [_row("US.XLK", "K1", 5), _row("US.XLF", "F1", 5), _row("US.DIA", "D1", 5)]
    env.setup(rows=rows, snap={"US.XLK": 0.9, "US.XLF": 0.5, "US.DIA": 0.5,
                               "US.XLU": 0.05, "US.XLP": 0.10},
              reads=[{"US.XLK": 5, "US.XLF": 5, "US.DIA": 5}, {"US.XLF": 5, "US.DIA": 5}])
    env.decide()
    assert [c[1] for c in env.buys()] == ["US.XLU"]


def test_partial_exit_and_needs_attention_hold_slots(env):
    rows = [_row("US.XLK", "K1", 5), _row("US.XLF", "F1", 5, status="NEEDS_ATTENTION")]
    env.setup(rows=rows, snap={"US.XLK": 0.9, "US.XLU": 0.05, "US.XLP": 0.10, "US.XLB": 0.15},
              held={"US.XLK": 5, "US.XLF": 5})
    env.results[("SELL", "US.XLK")] = ("S1", 109.0, 2)  # partial: 3 shares remain OPEN
    env.decide()
    # XLK (partial) + XLF (NEEDS_ATTENTION) occupy 2 of 3 slots -> one BUY
    assert [c[1] for c in env.buys()] == ["US.XLU"]


def test_external_holding_never_entered(env, monkeypatch):
    logged = []
    monkeypatch.setattr("bot.ibs.service._logger.info",
                        lambda ev, **kw: logged.append((ev, kw)))
    env.setup(snap={"US.XLU": 0.0, "US.XLP": 0.10}, held={"US.XLU": 100})
    env.decide()
    assert [c[1] for c in env.buys()] == ["US.XLP"]
    assert any(ev == "ibs_external_holdings" and kw["codes"] == ["US.XLU"]
               for ev, kw in logged)


def test_post_exit_read_failure_skips_all_entries(env):
    env.setup(snap=THREE_LOW)
    env.gw.get_positions.side_effect = [(0, env.gw.get_positions.return_value[1]), (-1, None)]
    env.decide()
    assert env.calls == []
    assert any("entries skipped" in a for a in _alerts(env.bot))


def test_working_order_excludes_code(env):
    env.store.insert_order({"order_id": "OLD", "position_id": "Z", "code": "US.XLU",
                            "side": "BUY", "qty": 5, "status": "WORKING",
                            "session_date": "2026-10-02", "created_at": "x"})
    env.setup(snap={"US.XLU": 0.0, "US.XLP": 0.10})
    env.decide()
    assert [c[1] for c in env.buys()] == ["US.XLP"]


def test_same_session_exit_not_reentered(env):
    env.setup(rows=[_row("US.QQQ", "Q1", 5, entry_date="2026-09-18")],
              snap={"US.QQQ": 0.05}, reads=[{"US.QQQ": 5}, {}])
    env.decide()
    assert [c[0] for c in env.calls] == ["SELL"]


def test_sizing_and_qty_lt_1(env):
    env.setup(snap={"US.XLU": (50.0, 60.0, 50.0)})
    env.decide()
    assert env.buys() == [("BUY", "US.XLU", 199, 50.0)]
    env.store.set_meta("ibs_decision_date", "")
    env.calls.clear()
    for r in env.store.get_active_positions():
        env.store.set_position_status(r["position_id"], "CLOSED")
    env.setup(snap={"US.XLU": (12000.0, 12100.0, 12000.0)})
    env.decide()
    assert env.buys() == []
    assert len(env.store.get_active_positions()) == 0


def test_opening_row_precedes_buy_and_fill_recorded(env):
    env.setup(snap={"US.XLU": 0.05})
    env.results[("BUY", "US.XLU")] = ("B1", 50.04, 199)
    env.decide()
    assert env.at_call[("BUY", "US.XLU")] == ["US.XLU"]
    row = env.store.get_active_positions()[0]
    assert (row["status"], row["qty"], row["entry_price"], row["entry_order_id"],
            row["entry_date"]) == ("OPEN", 199, 50.04, "B1", "2026-10-05")
    assert any("IBS entry" in a for a in _alerts(env.bot))


def test_partial_entry_opens_filled_qty(env):
    env.setup(snap={"US.XLU": 0.05})
    env.results[("BUY", "US.XLU")] = ("B2", 50.04, 5)
    env.decide()
    assert env.store.get_active_positions()[0]["qty"] == 5
    assert any("partial" in a for a in _alerts(env.bot))


def test_unfilled_entry_aborted_silently(env):
    env.setup(snap={"US.XLU": 0.05})
    env.results[("BUY", "US.XLU")] = None
    env.decide()
    assert env.store.get_active_positions() == []
    ab = env.store.get_positions(("ABORTED",))
    assert len(ab) == 1 and ab[0]["close_reason"] == "entry_unfilled"
    assert _alerts(env.bot) == []


def test_entry_place_failure_aborts_and_continues(env):
    """CR-01: a BUY that never reached the broker is ABORTED, not NEEDS_ATTENTION."""
    env.setup(snap={"US.XLU": 0.05, "US.XLP": 0.10})
    env.results[("BUY", "US.XLU")] = RuntimeError("buying power secret")
    env.decide()
    rows = {r["code"]: r["status"] for r in env.store.get_active_positions()}
    assert rows == {"US.XLP": "OPEN"}
    ab = env.store.get_positions(("ABORTED",))
    assert [(r["code"], r["close_reason"]) for r in ab] == [("US.XLU", "entry_place_failed")]
    assert not any("NEEDS ATTENTION" in a or "secret" in a for a in _alerts(env.bot))
    assert any(a["event"] == "ibs_entry_not_placed" for a in env.audits)


def test_entry_exception_needs_attention_and_continues(env):
    env.setup(snap={"US.XLU": 0.05, "US.XLP": 0.10})
    env.results[("BUY", "US.XLU")] = _AfterPlace(RuntimeError("boom broker text"))
    env.decide()
    rows = {r["code"]: r["status"] for r in env.store.get_active_positions()}
    assert rows == {"US.XLU": "NEEDS_ATTENTION", "US.XLP": "OPEN"}
    assert any("IBS NEEDS ATTENTION" in a and "boom" not in a for a in _alerts(env.bot))
    assert any(a["event"] == "ibs_entry_unknown" for a in env.audits)


def test_duplicate_active_row_integrity_error_skips(env):
    env.setup(snap={"US.XLU": 0.05, "US.XLP": 0.10, "US.XLE": 0.15})
    env.hooks[("BUY", "US.XLU")] = lambda: env.store.insert_position(
        _row("US.XLP", "RACE", 3, entry_date="2026-10-05"))
    env.decide()
    assert [c[1] for c in env.calls] == ["US.XLU", "US.XLE"]


@pytest.mark.parametrize("how", ["kill", "disabled"])
def test_kill_switch_or_disabled_after_exits_blocks_entries(env, how):
    env.setup(rows=[_row("US.XLK", "K1", 5)], snap={**THREE_LOW, "US.XLK": 0.9},
              reads=[{"US.XLK": 5}, {}])

    def trip():
        if how == "kill":
            env.ks.triggered = True
        else:
            env.bot._entries_enabled = False
    env.hooks[("SELL", "US.XLK")] = trip
    env.decide()
    assert env.buys() == []


def test_no_time_skips_entries(env, set_now, monkeypatch):
    env.setup(snap={"US.XLU": 0.05})
    set_now(datetime(2026, 10, 5, 15, 58, tzinfo=ET))
    logged = []
    monkeypatch.setattr("bot.ibs.service._logger.info",
                        lambda ev, **kw: logged.append((ev, kw)))
    env.decide()
    assert env.buys() == []
    assert any(ev == "ibs_entry_skipped" and kw.get("reason") == "no_time"
               for ev, kw in logged)


# ============================================================
# Task 2: hard-cancel sweep
# ============================================================

def _order(store, oid, code="US.XLU", status="WORKING", day="2026-10-05"):
    store.insert_order({"order_id": oid, "position_id": "P-" + oid, "code": code,
                        "side": "BUY", "qty": 5, "status": status,
                        "session_date": day, "created_at": "x"})


def _status(store, oid):
    return next(o["status"] for o in store.get_orders(
        ("WORKING", "CANCELLED", "CANCEL_FAILED", "DONE")) if o["order_id"] == oid)


def test_sweep_cancels_every_working_order(env):
    _order(env.store, "O1")
    _order(env.store, "O0", day="2026-10-02")
    _order(env.store, "O2", status="DONE")
    n = _run(env.bot._sweep_orders("hard_cancel"))
    assert n == 2
    assert [c.args[0] for c in env.gw.cancel_order.await_args_list] == ["O1", "O0"]
    assert _status(env.store, "O1") == _status(env.store, "O0") == "CANCELLED"
    assert _status(env.store, "O2") == "DONE"
    ev = [a for a in env.audits if a["event"] == "ibs_order_cancel"]
    assert {a["order_id"] for a in ev} == {"O1", "O0"} and all(
        a["reason"] == "hard_cancel" for a in ev)


def test_sweep_cancel_failure_marks_and_alerts(env):
    _order(env.store, "O1")
    _order(env.store, "O0")

    async def cancel(oid):
        if oid == "O1":
            raise RuntimeError("secret broker text")
    env.gw.cancel_order.side_effect = cancel
    _run(env.bot._sweep_orders("hard_cancel"))
    assert _status(env.store, "O1") == "CANCEL_FAILED"
    assert _status(env.store, "O0") == "CANCELLED"
    al = _alerts(env.bot)
    assert len(al) == 1 and "IBS cancel FAILED" in al[0] and "O1" in al[0]
    assert "secret" not in al[0]
    assert any(a["event"] == "ibs_order_cancel_failed" for a in env.audits)


def test_hard_cancel_non_trading_day(env, set_now):
    _order(env.store, "O1")
    set_now(datetime(2026, 10, 4, 15, 59, tzinfo=ET))
    _run(env.bot._job_hard_cancel())
    env.gw.cancel_order.assert_not_awaited()


def test_hard_cancel_cancels_running_decision_first(env, set_now):
    _order(env.store, "O1")
    set_now(datetime(2026, 10, 5, 15, 59, tzinfo=ET))

    async def scenario():
        async def never():
            await asyncio.sleep(3600)
        env.bot._decision_task = asyncio.create_task(never())
        await asyncio.sleep(0)
        task = env.bot._decision_task
        await env.bot._job_hard_cancel()
        return task
    task = _run(scenario())
    assert task.cancelled()
    assert any(a["event"] == "ibs_decision_cancelled" for a in env.audits)
    env.gw.cancel_order.assert_awaited_once_with("O1")


@pytest.mark.parametrize("status", ["OPENING", "CLOSING"])
def test_hard_cancel_flags_rows_left_mid_order(env, set_now, status):
    """CR-02: after cancelling the decision, any OPENING/CLOSING row is flagged + alerted."""
    env.store.insert_position(_row("US.XLU", "X1", 5, status=status))
    set_now(datetime(2026, 10, 5, 15, 59, tzinfo=ET))
    _run(env.bot._job_hard_cancel())
    assert env.store.get_active_positions()[0]["status"] == "NEEDS_ATTENTION"
    al = _alerts(env.bot)
    assert len(al) == 1 and "IBS NEEDS ATTENTION" in al[0] and "US.XLU" in al[0]


def test_entry_timeout_unfilled_is_aborted(env):
    """CR-02: entry deadline hit with the order cleanly cancelled and 0 filled -> ABORTED."""
    from unittest.mock import AsyncMock
    env.setup(snap={"US.XLU": 0.05})
    env.results[("BUY", "US.XLU")] = _AfterPlace(asyncio.TimeoutError())
    env.gw.get_order_status = AsyncMock(return_value=[{
        "order_id": "B-US.XLU", "order_status": "CANCELLED_ALL",
        "dealt_qty": 0, "dealt_avg_price": 0.0}])
    env.decide()
    ab = env.store.get_positions(("ABORTED",))
    assert [(r["code"], r["close_reason"]) for r in ab] == [("US.XLU", "entry_unfilled")]
    assert env.store.get_orders(("CANCELLED",))[0]["order_id"] == "B-US.XLU"


def test_hard_cancel_nothing_to_do(env, set_now):
    set_now(datetime(2026, 10, 5, 15, 59, tzinfo=ET))
    _run(env.bot._job_hard_cancel())
    env.gw.cancel_order.assert_not_awaited()
    assert _alerts(env.bot) == []


def test_hard_cancel_swallows_error_but_not_cancellation(env, set_now, monkeypatch):
    set_now(datetime(2026, 10, 5, 15, 59, tzinfo=ET))

    async def boom(reason):
        raise RuntimeError("x")
    monkeypatch.setattr(env.bot, "_sweep_orders", boom)
    _run(env.bot._job_hard_cancel())  # swallowed

    async def cancelled(reason):
        raise asyncio.CancelledError()
    monkeypatch.setattr(env.bot, "_sweep_orders", cancelled)
    with pytest.raises(asyncio.CancelledError):
        _run(env.bot._job_hard_cancel())
