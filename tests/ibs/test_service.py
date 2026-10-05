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


def test_unmanaged_universe_holding_alerts_once_per_session(
        make_ibs_bot, ibs_store, ibs_gateway, make_positions_df, set_now):
    """WR-07: a universe code held at the broker with no active row (e.g. a BUY
    recorded as not placed that did reach OpenD) alerts once per code per session;
    it is never traded or adopted (D-12)."""
    ibs_gateway.get_positions.return_value = (
        0, make_positions_df({"US.XLU": 100, "US.DIVO": 630}))
    bot = make_ibs_bot()
    set_now(datetime(2026, 10, 5, 9, 31, tzinfo=ET))
    _run(bot.reconcile(startup=True))
    _run(bot.reconcile())
    alerts = _alerts(bot)
    assert len(alerts) == 1
    assert "US.XLU" in alerts[0] and "check moomoo" in alerts[0]
    assert "DIVO" not in alerts[0]
    set_now(datetime(2026, 10, 6, 15, 50, tzinfo=ET))
    _run(bot.reconcile())
    assert len(_alerts(bot)) == 2
    assert ibs_store.get_active_positions() == []
    for m in ("place_order", "cancel_order"):
        getattr(ibs_gateway, m).assert_not_awaited()


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


# ============================================================
# Task 2: decision job guards, one snapshot, exit batch
# ============================================================

MON_1550 = datetime(2026, 10, 5, 15, 50, tzinfo=ET)


@pytest.fixture
def decide_env(make_ibs_bot, ibs_cfg, ibs_store, ibs_gateway, make_positions_df,
               make_snapshot_row, set_now, audits):
    """Build a bot at Mon 2026-10-05 15:50 with an enabled gate and recording executor."""
    class Env:
        pass
    env = Env()
    env.store, env.gw, env.audits = ibs_store, ibs_gateway, audits
    import dataclasses
    # the real 10 s retry sleep is replaced by a tiny one (WR-01 tests)
    env.bot = make_ibs_bot(dataclasses.replace(ibs_cfg, decision_read_retry_s=0.01))
    env.bot._entries_enabled = True
    env.bot._executor.work = AsyncMock(return_value=None)
    env.set_now = set_now
    set_now(MON_1550)

    def setup(rows, snap):
        for r in rows:
            ibs_store.insert_position(r)
        held = {r["code"]: r["qty"] for r in rows if r["status"] == "OPEN"}
        ibs_gateway.get_positions.return_value = (0, make_positions_df(held))
        import pandas as pd
        ibs_gateway.get_market_snapshot.return_value = (0, pd.DataFrame([
            make_snapshot_row(c, 100.0 + 10.0 * v, 110.0, 100.0) for c, v in snap.items()
        ]))
    env.setup = setup
    env.status = lambda code: next(
        p for p in ibs_store.get_active_positions() + ibs_store.get_positions(("CLOSED",))
        if p["code"] == code)
    return env


def test_decide_noop_on_non_trading_day(decide_env):
    decide_env.set_now(datetime(2026, 10, 4, 15, 50, tzinfo=ET))
    _run(decide_env.bot._job_decide())
    decide_env.gw.get_positions.assert_not_awaited()
    decide_env.gw.get_market_snapshot.assert_not_awaited()
    assert decide_env.store.get_meta("ibs_decision_date") is None


@pytest.mark.parametrize("case", ["disabled", "kill", "decided", "past_deadline"])
def test_decide_skip_guards(decide_env, case, monkeypatch):
    bot = decide_env.bot
    if case == "disabled":
        bot._entries_enabled = False
        decide_env.gw.get_global_state.return_value = {"connected": False}
    elif case == "kill":
        bot._kill_switch.triggered = True
    elif case == "decided":
        decide_env.store.set_meta("ibs_decision_date", "2026-10-05")
    else:
        decide_env.set_now(datetime(2026, 10, 5, 15, 59, tzinfo=ET))
    logged = []
    monkeypatch.setattr("bot.ibs.service._logger.info",
                        lambda ev, **kw: logged.append((ev, kw)))
    _run(bot._job_decide())
    decide_env.gw.get_market_snapshot.assert_not_awaited()
    assert any(ev == "ibs_decision_skipped" and kw.get("reason") for ev, kw in logged)
    if case == "past_deadline":
        assert decide_env.store.get_meta("ibs_decision_date") is None
    # WR-02: a skipped decision is never silent (except the idempotent re-fire)
    alerts = _alerts(bot)
    if case == "decided":
        assert alerts == []
    else:
        assert len(alerts) == 1 and "<b>IBS decision skipped</b>" in alerts[0]


def test_entries_disabled_but_connected_still_runs_exits(decide_env):
    """WR-02: a watchdog lag (entries off, OpenD already back) must not drop exits."""
    decide_env.setup([_row()], {"US.SPY": 0.9, "US.XLU": 0.05})
    decide_env.bot._entries_enabled = False
    _run(decide_env.bot._job_decide())
    calls = decide_env.bot._executor.work.await_args_list
    assert [c.args[:2] for c in calls] == [("SELL", "US.SPY")]  # no BUY
    assert any("exits only" in a for a in _alerts(decide_env.bot))


def test_decide_writes_meta_first_and_is_idempotent(decide_env):
    seen = []
    orig = decide_env.gw.get_positions

    async def gp(*a, **k):
        seen.append(decide_env.store.get_meta("ibs_decision_date"))
        return await orig(*a, **k)

    decide_env.gw.get_positions = gp
    _run(decide_env.bot._job_decide())
    # reconcile read + the post-exit entry-guard read (Plan 08), both after the meta write
    assert seen == ["2026-10-05", "2026-10-05"]
    decide_env.gw.get_market_snapshot.assert_awaited_once_with(
        list(decide_env.bot._cfg.universe))
    _run(decide_env.bot._job_decide())
    assert decide_env.gw.get_market_snapshot.await_count == 1


def test_reconcile_precedes_snapshot(decide_env):
    order = []
    orig_gp, orig_snap = decide_env.gw.get_positions, decide_env.gw.get_market_snapshot

    async def gp(*a, **k):
        order.append("positions")
        return await orig_gp(*a, **k)

    async def snap(*a, **k):
        order.append("snapshot")
        return await orig_snap(*a, **k)

    decide_env.gw.get_positions, decide_env.gw.get_market_snapshot = gp, snap
    _run(decide_env.bot._job_decide())
    assert order[:2] == ["positions", "snapshot"]  # entry-guard read follows (Plan 08)


def test_snapshot_failure_alerts_without_exception_text(decide_env):
    decide_env.gw.get_market_snapshot.return_value = (-1, "secret broker text")
    _run(decide_env.bot._job_decide())
    decide_env.bot._executor.work.assert_not_awaited()
    alerts = _alerts(decide_env.bot)
    assert len(alerts) == 1 and "<b>IBS decision error</b>" in alerts[0]
    assert "snapshot failed" not in alerts[0] and "secret" not in alerts[0]
    assert [a["event"] for a in decide_env.audits] == ["ibs_decision_error"]
    assert decide_env.bot._decision_task is None


def test_transient_read_failure_is_retried(decide_env):
    """WR-01: one snapshot blip no longer consumes the day's exits."""
    decide_env.setup([_row()], {"US.SPY": 0.9})
    good = decide_env.gw.get_market_snapshot.return_value
    decide_env.gw.get_market_snapshot.side_effect = [(-1, "blip"), good]
    _run(decide_env.bot._job_decide())
    assert [c.args[:2] for c in decide_env.bot._executor.work.await_args_list] == [
        ("SELL", "US.SPY")]
    assert not any("decision error" in a for a in _alerts(decide_env.bot))
    assert decide_env.store.get_meta("ibs_decision_date") == "2026-10-05"


def test_persistent_read_failure_gives_up_and_frees_the_day(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.gw.get_positions.return_value = (-1, None)
    _run(decide_env.bot._job_decide())
    assert decide_env.gw.get_positions.await_count == 1 + decide_env.bot._cfg.decision_read_retries
    decide_env.bot._executor.work.assert_not_awaited()
    assert [a for a in _alerts(decide_env.bot) if "decision error" in a]
    # no order was attempted, so the day is not consumed
    assert decide_env.store.get_meta("ibs_decision_date") != "2026-10-05"


def test_read_retry_is_bounded_by_the_deadline(decide_env):
    from datetime import timedelta
    decide_env.setup([_row()], {"US.SPY": 0.9})
    deadline = decide_env.bot._deadline(MON_1550.date())
    decide_env.set_now(deadline - timedelta(milliseconds=5))
    decide_env.gw.get_positions.return_value = (-1, None)
    _run(decide_env.bot._job_decide())
    assert decide_env.gw.get_positions.await_count == 1


def test_exit_decisions(decide_env):
    decide_env.setup(
        [_row("US.SPY", "P1", entry_date="2026-10-01"),
         _row("US.QQQ", "P2", entry_date="2026-09-21"),
         _row("US.IWM", "P3", entry_date="2026-10-02"),
         _row("US.DIA", "P4", status="NEEDS_ATTENTION")],
        {"US.SPY": 0.9, "US.QQQ": 0.5, "US.IWM": 0.5, "US.DIA": 0.95})
    _run(decide_env.bot._job_decide())
    calls = decide_env.bot._executor.work.await_args_list
    assert [(c.args[0], c.args[1]) for c in calls] == [("SELL", "US.SPY"), ("SELL", "US.QQQ")]
    assert decide_env.store.get_trades_on("2026-10-05") == []  # None result -> no fill
    reasons = {p["code"]: p["exit_reason"] for p in decide_env.store.get_active_positions()}
    assert reasons["US.SPY"] == "ibs" and reasons["US.QQQ"] == "time"
    assert reasons["US.IWM"] is None and reasons["US.DIA"] is None


def test_exit_full_fill(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor.work.return_value = ("O1", 780.10, 12)
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_positions(("CLOSED",))[0]
    assert pos["code"] == "US.SPY" and pos["qty"] == 0
    trades = decide_env.store.get_trades_on("2026-10-05")
    assert len(trades) == 1 and trades[0]["pnl_usd"] == pytest.approx(120.0)
    alert = _alerts(decide_env.bot)[-1]
    assert "IBS exit" in alert and "US.SPY" in alert


@pytest.mark.parametrize("avg", [0.0, float("nan")])
def test_exit_fill_without_avg_price_uses_limit(decide_env, avg):
    """WR-04: a fill reported with no average price never books a 100% loss."""
    decide_env.setup([_row()], {"US.SPY": 0.9})  # last 109.0
    decide_env.bot._executor.work.return_value = ("O1", avg, 12)
    _run(decide_env.bot._job_decide())
    trade = decide_env.store.get_trades_on("2026-10-05")[0]
    assert trade["exit_price"] == pytest.approx(108.95)  # last - exit_limit_buffer_usd
    assert trade["pnl_usd"] == pytest.approx((108.95 - 770.10) * 12)


def test_exit_partial_fill(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor.work.return_value = ("O2", 780.10, 5)
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert (pos["status"], pos["qty"], pos["exit_pending"]) == ("OPEN", 7, 1)
    assert "partial" in _alerts(decide_env.bot)[-1]


def test_exit_unfilled(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert (pos["status"], pos["exit_pending"]) == ("OPEN", 1)
    assert "unfilled" in _alerts(decide_env.bot)[-1]


def test_exit_exception_after_order_placed_needs_attention(decide_env):
    """CR-01: only an exception AFTER an order reached the broker is "state unknown"."""
    decide_env.setup([_row()], {"US.SPY": 0.9})

    async def placed_then_boom(side, code, qty, last, deadline, on_placed=None):
        await on_placed("O9")
        raise RuntimeError("boom broker text")

    decide_env.bot._executor.work.side_effect = placed_then_boom
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert pos["status"] == "NEEDS_ATTENTION"
    alert = _alerts(decide_env.bot)[-1]
    assert "IBS NEEDS ATTENTION" in alert and "boom" not in alert
    assert "ibs_exit_unknown" in [a["event"] for a in decide_env.audits]


def test_exit_place_failure_stays_open_and_retries(decide_env):
    """CR-01: place_order raising (rate limit, buying power) exposes nothing ->
    back to OPEN with exit_pending kept, one alert, retried next session."""
    from bot.ibs.execution import IbsExecutor
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor = IbsExecutor(decide_env.gw, decide_env.bot._cfg)
    decide_env.gw.place_order.side_effect = RuntimeError("rate limited secret")
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert (pos["status"], pos["exit_pending"]) == ("OPEN", 1)
    assert decide_env.store.get_orders(("WORKING", "DONE", "CANCELLED")) == []
    alerts = _alerts(decide_env.bot)
    assert len(alerts) == 1 and "retried next session" in alerts[0]
    assert "NEEDS ATTENTION" not in alerts[0] and "secret" not in alerts[0]
    assert "ibs_exit_not_placed" in [a["event"] for a in decide_env.audits]
    # retried next session: the OPEN row is selected and sold again
    decide_env.gw.place_order.side_effect = None
    decide_env.store.set_meta("ibs_decision_date", "")
    decide_env.bot._executor.work = AsyncMock(return_value=None)
    _run(decide_env.bot._job_decide())
    assert [c.args[:2] for c in decide_env.bot._executor.work.await_args_list] == [
        ("SELL", "US.SPY")]


def test_executor_deadline_is_before_the_hard_cancel_sweep(decide_env):
    """CR-02: the executor gives up executor_margin_s before the sweep fires."""
    bot = decide_env.bot
    assert bot._deadline(MON_1550.date()) == datetime(2026, 10, 5, 15, 58, 30, tzinfo=ET)


def _placed_then(exc, oid="O9"):
    async def work(side, code, qty, last, deadline, on_placed=None):
        await on_placed(oid)
        raise exc
    return work


def _broker_order(status, dealt=0, avg=0.0, oid="O9"):
    return AsyncMock(return_value=[{"order_id": oid, "order_status": status,
                                    "dealt_qty": dealt, "dealt_avg_price": avg}])


def test_exit_timeout_cleanly_cancelled_is_retried(decide_env):
    """CR-02: deadline hit, fill_leg cancelled the order, broker confirms 0 filled ->
    OPEN + exit_pending (retried next session), order row CANCELLED, no NEEDS_ATTENTION."""
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor.work.side_effect = _placed_then(asyncio.TimeoutError())
    decide_env.gw.get_order_status = _broker_order("CANCELLED_ALL")
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert (pos["status"], pos["exit_pending"], pos["qty"]) == ("OPEN", 1, 12)
    assert decide_env.store.get_orders(("CANCELLED",))[0]["order_id"] == "O9"
    assert decide_env.store.get_orders(("WORKING",)) == []
    alerts = _alerts(decide_env.bot)
    assert "unfilled" in alerts[-1] and not any("NEEDS ATTENTION" in a for a in alerts)


def test_exit_timeout_partial_fill_is_recorded(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor.work.side_effect = _placed_then(asyncio.TimeoutError())
    decide_env.gw.get_order_status = _broker_order("CANCELLED_PART", 5, 780.10)
    _run(decide_env.bot._job_decide())
    pos = decide_env.store.get_active_positions()[0]
    assert (pos["status"], pos["qty"], pos["exit_pending"]) == ("OPEN", 7, 1)
    assert len(decide_env.store.get_trades_on("2026-10-05")) == 1
    assert "partial" in _alerts(decide_env.bot)[-1]


def test_exit_timeout_order_still_live_needs_attention(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.bot._executor.work.side_effect = _placed_then(asyncio.TimeoutError())
    decide_env.gw.get_order_status = _broker_order("SUBMITTED")
    _run(decide_env.bot._job_decide())
    assert decide_env.store.get_active_positions()[0]["status"] == "NEEDS_ATTENTION"
    assert decide_env.store.get_orders(("WORKING",))[0]["order_id"] == "O9"


def test_hard_cancel_mid_exit_flags_row_and_alerts(decide_env):
    """CR-02: a decision cancelled mid-order never leaves the row CLOSING."""
    decide_env.setup([_row()], {"US.SPY": 0.9})

    async def hang(side, code, qty, last, deadline, on_placed=None):
        await on_placed("O9")
        await asyncio.sleep(3600)

    decide_env.bot._executor.work.side_effect = hang

    async def scenario():
        job = asyncio.create_task(decide_env.bot._job_decide())
        for _ in range(20):
            await asyncio.sleep(0)
        await decide_env.bot._job_hard_cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass

    _run(scenario())
    assert decide_env.store.get_active_positions()[0]["status"] == "NEEDS_ATTENTION"
    assert any("IBS NEEDS ATTENTION" in a for a in _alerts(decide_env.bot))


@pytest.mark.parametrize("side", ["SELL", "BUY"])
def test_non_cancellation_base_exception_is_not_handled(decide_env, side):
    """IN-07: only Exception / CancelledError are handled mid-order. SystemExit and
    friends (GeneratorExit, KeyboardInterrupt) propagate with no await in a handler;
    startup reconcile flags the row left OPENING/CLOSING."""
    bot = decide_env.bot
    day = MON_1550.date()
    quotes = {"US.SPY": {"ibs": 0.9, "last": 109.0}, "US.XLU": {"ibs": 0.05, "last": 100.5}}
    bot._executor.work.side_effect = _placed_then(SystemExit(3))
    if side == "SELL":
        decide_env.setup([_row()], {})
        coro = bot._run_exits(day, bot._deadline(day), quotes)
    else:
        decide_env.setup([], {})
        coro = bot._run_entries(day, bot._deadline(day), quotes, set())
    with pytest.raises(SystemExit):
        _run(coro)
    bot._alerter.send.assert_not_awaited()
    statuses = {p["status"] for p in decide_env.store.get_active_positions()}
    assert statuses == {"CLOSING" if side == "SELL" else "OPENING"}


def test_exit_deferred_without_quote(decide_env):
    decide_env.setup([_row("US.QQQ", "P2", entry_date="2026-09-21")], {})
    _run(decide_env.bot._job_decide())
    decide_env.bot._executor.work.assert_not_awaited()
    pos = decide_env.store.get_active_positions()[0]
    assert pos["exit_pending"] == 1 and pos["status"] == "OPEN"
    assert "deferred" in _alerts(decide_env.bot)[-1]


def test_exit_retry_of_pending(decide_env):
    decide_env.setup([_row(entry_date="2026-10-02")], {"US.SPY": 0.5})
    decide_env.store.mark_exit_pending("P1", "ibs", "2026-10-02")
    _run(decide_env.bot._job_decide())
    calls = decide_env.bot._executor.work.await_args_list
    assert [(c.args[0], c.args[1]) for c in calls] == [("SELL", "US.SPY")]


def test_exit_deferred_when_not_enough_time(decide_env):
    decide_env.setup([_row()], {"US.SPY": 0.9})
    decide_env.set_now(datetime(2026, 10, 5, 15, 58, tzinfo=ET))
    _run(decide_env.bot._job_decide())
    decide_env.bot._executor.work.assert_not_awaited()
    assert decide_env.store.get_active_positions()[0]["exit_pending"] == 1
    assert "deferred" in _alerts(decide_env.bot)[-1]


def test_exit_loop_stops_on_kill_switch(decide_env):
    decide_env.setup(
        [_row("US.SPY", "P1"), _row("US.QQQ", "P2")], {"US.SPY": 0.9, "US.QQQ": 0.9})

    async def work(*a, **k):
        decide_env.bot._kill_switch.triggered = True

    decide_env.bot._executor.work.side_effect = work
    _run(decide_env.bot._job_decide())
    assert decide_env.bot._executor.work.await_count == 1


def test_work_records_orders(decide_env):
    decide_env.store.insert_position(_row())
    row = decide_env.store.get_active_positions()[0]

    async def fake(side, code, qty, last, deadline, on_placed=None):
        await on_placed("O9")
        return ("O9", 10.0, 3)

    decide_env.bot._executor.work = fake
    result = _run(decide_env.bot._work("SELL", row, 12, 100.0, MON_1550))
    assert result == ("O9", 10.0, 3)
    assert decide_env.store.get_orders(("DONE",))[0]["order_id"] == "O9"
    done = [a for a in decide_env.audits if a["event"] == "ibs_order_done"][0]
    assert done["order_ids"] == ["O9"]


def test_work_exception_keeps_order_working(decide_env):
    decide_env.store.insert_position(_row())
    row = decide_env.store.get_active_positions()[0]

    async def fake(side, code, qty, last, deadline, on_placed=None):
        await on_placed("O9")
        raise RuntimeError("cancel unconfirmed")

    decide_env.bot._executor.work = fake
    with pytest.raises(RuntimeError):
        _run(decide_env.bot._work("SELL", row, 12, 100.0, MON_1550))
    assert decide_env.store.get_orders(("WORKING",))[0]["order_id"] == "O9"
