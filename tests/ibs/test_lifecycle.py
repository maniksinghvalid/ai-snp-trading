#!/usr/bin/env python3
"""
tests/ibs/test_lifecycle.py — IbsBot job arming, EOD report, shutdown order, run loop, main().

Real IbsStore on a tmp DB; gateway / alerter / kill switch are mocks. The clock is
always injected (set_now) — never the wall clock. Scheduler tests start APScheduler
paused inside asyncio.run so DateTrigger jobs are registered but never fire.
"""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from apscheduler.triggers.cron import CronTrigger

ET = ZoneInfo("America/New_York")
JOB_IDS = {"ibs_arm", "ibs_decide", "ibs_hard_cancel", "ibs_eod"}


def _run(coro):
    return asyncio.run(coro)


def _at(y, mo, d, h=9, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def _with_started_scheduler(bot, fn):
    """Run fn() with the scheduler started paused; return (result, {job_id: job}).

    Jobs are captured before shutdown (shutdown clears the job store).
    """
    async def go():
        bot._scheduler.start(paused=True)
        try:
            result = fn()
            return result, {j.id: j for j in bot._scheduler.get_jobs()}
        finally:
            bot._scheduler.shutdown(wait=False)
    return _run(go())


def _times(jobs):
    return {i: j.trigger.run_date.strftime("%Y-%m-%d %H:%M") for i, j in jobs.items()}


@pytest.fixture
def audits(monkeypatch):
    rec = []
    monkeypatch.setattr("bot.ibs.service.append_audit", lambda e: rec.append(e))
    return rec


# ============================================================
# Task 1: arming
# ============================================================

def test_arm_normal_day(make_ibs_bot, set_now):
    bot = make_ibs_bot()
    set_now(_at(2026, 10, 5))
    _, jobs = _with_started_scheduler(bot, bot.arm_today)
    assert _times(jobs) == {
        "ibs_decide": "2026-10-05 15:50",
        "ibs_hard_cancel": "2026-10-05 15:59",
        "ibs_eod": "2026-10-05 16:05",
    }


def test_arm_half_day(make_ibs_bot, set_now):
    bot = make_ibs_bot()
    set_now(_at(2026, 11, 27))
    _, jobs = _with_started_scheduler(bot, bot.arm_today)
    assert _times(jobs) == {
        "ibs_decide": "2026-11-27 12:50",
        "ibs_hard_cancel": "2026-11-27 12:59",
        "ibs_eod": "2026-11-27 13:05",
    }


@pytest.mark.parametrize("day", [(2026, 10, 4), (2026, 12, 25)])
def test_arm_non_trading_day(make_ibs_bot, set_now, day):
    bot = make_ibs_bot()
    set_now(_at(*day))
    armed, jobs = _with_started_scheduler(bot, bot.arm_today)
    assert armed == []
    assert jobs == {}


def test_arm_skips_passed_slot(make_ibs_bot, set_now):
    bot = make_ibs_bot()
    set_now(_at(2026, 10, 5, 15, 55))
    _, jobs = _with_started_scheduler(bot, bot.arm_today)
    assert set(jobs) == {"ibs_hard_cancel", "ibs_eod"}


def test_arm_twice_is_idempotent(make_ibs_bot, set_now):
    bot = make_ibs_bot()
    set_now(_at(2026, 10, 5))

    def twice():
        bot.arm_today()
        bot.arm_today()

    _, jobs = _with_started_scheduler(bot, twice)
    assert sorted(jobs) == ["ibs_decide", "ibs_eod", "ibs_hard_cancel"]


def test_armed_job_options(make_ibs_bot, set_now, ibs_cfg):
    bot = make_ibs_bot()
    set_now(_at(2026, 10, 5))
    _, jobs = _with_started_scheduler(bot, bot.arm_today)
    assert len(jobs) == 3
    for job in jobs.values():
        assert job.coalesce is True
        assert job.max_instances == 1
        assert job.misfire_grace_time == ibs_cfg.misfire_grace_s


def test_register_jobs_no_force_close(make_ibs_bot, set_now):
    bot = make_ibs_bot()
    set_now(_at(2026, 10, 5))

    def both():
        bot._register_jobs()
        bot.arm_today()

    _, jobs = _with_started_scheduler(bot, both)
    assert set(jobs) <= JOB_IDS
    assert not any("force" in i for i in jobs)
    trig = jobs["ibs_arm"].trigger
    assert isinstance(trig, CronTrigger)
    fields = {f.name: str(f) for f in trig.fields}
    assert fields["hour"] == "9" and fields["minute"] == "0"
    assert str(trig.timezone) == "America/New_York"


def test_job_arm_swallows_exception(make_ibs_bot, monkeypatch):
    bot = make_ibs_bot()
    monkeypatch.setattr(bot, "arm_today", MagicMock(side_effect=RuntimeError("boom")))
    _run(bot._job_arm())  # must not raise


def test_job_arm_reraises_cancelled(make_ibs_bot, monkeypatch):
    bot = make_ibs_bot()
    monkeypatch.setattr(bot, "arm_today", MagicMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        _run(bot._job_arm())


# ============================================================
# Task 1: EOD report
# ============================================================

def _pos(code="US.SPY", pid="P1", qty=10, entry_price=100.0, status="OPEN", **over):
    row = {"position_id": pid, "code": code, "qty": qty, "entry_date": "2026-10-01",
           "entry_price": entry_price, "entry_order_id": "E1", "status": status,
           "opened_at": "2026-10-01T15:55:00-04:00"}
    row.update(over)
    return row


def _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now,
             now=None):
    import dataclasses
    cfg = dataclasses.replace(ibs_cfg, report_dir=str(tmp_path / "reports" / "ibs"))
    bot = make_ibs_bot(cfg)
    set_now(now or _at(2026, 10, 5, 16, 5))
    bot.reconcile = AsyncMock(return_value={})
    return bot


def _snap(rows):
    return (0, pd.DataFrame(rows))


def test_eod_summary_and_report(make_ibs_bot, ibs_cfg, ibs_gateway, ibs_store,
                                ibs_alerter, tmp_path, monkeypatch, set_now):
    bot = _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now)
    ibs_store.insert_position(_pos("US.SPY", "P1", 10, 100.0))
    ibs_store.insert_position(_pos("US.QQQ", "P2", 5, 200.0))
    ibs_store.record_exit_fill("P2", "T1", 5, 210.0, "2026-10-05", "2026-10-05T15:55:00-04:00")
    ibs_gateway.get_market_snapshot = AsyncMock(
        return_value=_snap([{"code": "US.SPY", "last_price": 103.0}]))

    _run(bot._job_eod())

    bot.reconcile.assert_awaited_once()
    ibs_gateway.get_market_snapshot.assert_awaited_once()
    ibs_alerter.send.assert_awaited_once()
    text = ibs_alerter.send.await_args.args[0]
    assert "<b>IBS EOD</b>" in text and "ibs_etf_mean_reversion" in text
    assert "closed today 1" in text
    assert "+50.00" in text  # realized: (210-200)*5
    assert "+30.00" in text  # unrealized: (103-100)*10
    rd = tmp_path / "reports" / "ibs"
    assert (rd / "2026-10-05.html").exists() and (rd / "latest.html").exists()


def test_eod_escapes_values(make_ibs_bot, ibs_cfg, ibs_gateway, ibs_store,
                            ibs_alerter, tmp_path, monkeypatch, set_now):
    bot = _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now)
    ibs_store.insert_position(_pos("US.<X>", "P1", 1, 10.0))
    ibs_gateway.get_market_snapshot = AsyncMock(return_value=_snap([]))
    _run(bot._job_eod())
    text = ibs_alerter.send.await_args.args[0]
    html_doc = (tmp_path / "reports" / "ibs" / "latest.html").read_text(encoding="utf-8")
    assert "US.&lt;X&gt;" in text and "US.<X>" not in text
    assert "US.&lt;X&gt;" in html_doc and "US.<X>" not in html_doc
    assert "<script" not in html_doc


def test_eod_non_trading_day(make_ibs_bot, ibs_cfg, ibs_gateway, ibs_alerter,
                             tmp_path, monkeypatch, set_now):
    bot = _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now,
                   now=_at(2026, 10, 4, 16, 5))
    _run(bot._job_eod())
    ibs_alerter.send.assert_not_awaited()
    assert not (tmp_path / "reports").exists()


def test_eod_snapshot_failure_still_sends(make_ibs_bot, ibs_cfg, ibs_gateway, ibs_store,
                                          ibs_alerter, tmp_path, monkeypatch, set_now):
    bot = _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now)
    ibs_store.insert_position(_pos("US.SPY", "P1", 10, 100.0))
    ibs_gateway.get_market_snapshot = AsyncMock(return_value=(1, "err"))
    _run(bot._job_eod())
    text = ibs_alerter.send.await_args.args[0]
    assert "n/a" in text
    assert (tmp_path / "reports" / "ibs" / "latest.html").exists()


def test_eod_reconcile_error_does_not_block(make_ibs_bot, ibs_cfg, ibs_gateway,
                                            ibs_alerter, tmp_path, monkeypatch, set_now):
    bot = _eod_bot(make_ibs_bot, ibs_cfg, ibs_gateway, tmp_path, monkeypatch, set_now)
    bot.reconcile = AsyncMock(side_effect=RuntimeError("broker down"))
    ibs_gateway.get_market_snapshot = AsyncMock(return_value=_snap([]))
    _run(bot._job_eod())
    ibs_alerter.send.assert_awaited_once()


# ============================================================
# Task 2: shutdown order
# ============================================================

def _wire_recorder(bot, ibs_gateway, ibs_alerter, calls):
    ibs_gateway.cancel_order = AsyncMock(side_effect=lambda oid: calls.append(f"cancel:{oid}"))
    ibs_gateway.close = MagicMock(side_effect=lambda: calls.append("close"))
    ibs_alerter.send = AsyncMock(side_effect=lambda text: calls.append(f"alert:{text}"))
    bot._scheduler = MagicMock(shutdown=MagicMock(
        side_effect=lambda **k: calls.append("scheduler_shutdown")))


def _working_order(store, oid="O1"):
    store.insert_order({"order_id": oid, "position_id": "P1", "code": "US.SPY",
                        "side": "BUY", "qty": 1, "status": "WORKING",
                        "session_date": "2026-10-05", "created_at": "2026-10-05T15:51:00-04:00"})


def test_shutdown_order(make_ibs_bot, ibs_gateway, ibs_alerter, ibs_store, audits):
    bot = make_ibs_bot()
    calls = []
    _wire_recorder(bot, ibs_gateway, ibs_alerter, calls)
    _working_order(ibs_store)

    async def go():
        async def decision():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                calls.append("decision_cancelled")
                raise
        bot._decision_task = asyncio.create_task(decision())
        await asyncio.sleep(0)  # let it start
        await bot._shutdown()

    _run(go())
    assert calls == ["decision_cancelled", "cancel:O1", "close",
                     "alert:<b>IBS bot stopped</b>", "scheduler_shutdown"]
    assert {"event": "ibs_bot_shutdown", "reason": "kill_switch"} in audits


def test_shutdown_survives_step_failures(make_ibs_bot, ibs_gateway, ibs_alerter,
                                         ibs_store, audits):
    bot = make_ibs_bot()
    calls = []
    _wire_recorder(bot, ibs_gateway, ibs_alerter, calls)
    ibs_gateway.close = MagicMock(side_effect=RuntimeError("close boom"))
    ibs_gateway.cancel_order = AsyncMock(side_effect=RuntimeError("cancel boom"))
    _working_order(ibs_store)
    _run(bot._shutdown())
    assert calls == ["alert:<b>IBS bot stopped</b>", "scheduler_shutdown"]


def test_shutdown_never_started_scheduler(make_ibs_bot, audits):
    bot = make_ibs_bot()
    _run(bot._shutdown())  # real, never-started scheduler: must not raise


# ============================================================
# Task 2: run loop
# ============================================================

class _FakeKill:
    def __init__(self, calls):
        self.triggered = False
        self._calls = calls
        self._polls = 0

    def install(self):
        self._calls.append("install")

    def check_file(self):
        self._polls += 1
        return True

    def trigger(self, reason):
        self._calls.append(f"trigger:{reason}")
        self.triggered = True


def test_run_order_and_clean_stop(make_ibs_bot, ibs_gateway, ibs_alerter, ibs_store,
                                  set_now, audits):
    bot = make_ibs_bot()
    calls = []
    bot._kill_switch = _FakeKill(calls)
    set_now(_at(2026, 10, 4))  # Sunday: arm_today arms nothing
    ibs_gateway.connect = MagicMock(side_effect=lambda: calls.append("connect"))
    ibs_gateway.close = MagicMock(side_effect=lambda: calls.append("close"))
    real_reconcile = bot.reconcile

    async def rec(startup=False):
        calls.append("reconcile")
        return await real_reconcile(startup=startup)

    bot.reconcile = rec
    real_start = bot._scheduler.start
    bot._scheduler.start = lambda *a, **k: (calls.append("scheduler_start"),
                                            real_start(*a, **k))[1]
    real_arm = bot.arm_today
    bot.arm_today = lambda: (calls.append("arm_today"), real_arm())[1]

    state = {}

    class _Dog:
        async def run(self):
            state["started"] = True
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

    bot._watchdog = _Dog()
    _run(bot.run())

    assert calls[:6] == ["connect", "reconcile", "install", "scheduler_start",
                         "arm_today", "trigger:sentinel_file"]
    assert calls[-1] == "close"
    assert state == {"started": True, "cancelled": True}
    assert ibs_gateway.place_order.await_count == 0  # positions left held, nothing sold


def test_run_connect_failure_never_starts_scheduler(make_ibs_bot, ibs_gateway, audits):
    bot = make_ibs_bot()
    ibs_gateway.connect = MagicMock(side_effect=RuntimeError("paper guard"))
    started = []
    bot._scheduler.start = lambda *a, **k: started.append(1)
    shutdown_called = []

    async def fake_shutdown():
        shutdown_called.append(1)

    bot._shutdown = fake_shutdown
    with pytest.raises(RuntimeError, match="paper guard"):
        _run(bot.run())
    assert started == []
    assert bot._entries_enabled is False
    assert shutdown_called == [1]


# ============================================================
# Task 2: main()
# ============================================================

def _write_ibs_rules(tmp_path, payload):
    p = tmp_path / "rules_ibs.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_main_composes_and_runs(ibs_rules, tmp_path, monkeypatch):
    import bot.ibs.service as svc
    rules = _write_ibs_rules(tmp_path, ibs_rules)
    monkeypatch.chdir(tmp_path)
    cl = MagicMock()
    store_cls = MagicMock()
    kill_cls = MagicMock()
    dog_cls = MagicMock()
    ran = []
    for name, obj in (("configure_logging", cl), ("IbsStore", store_cls),
                      ("KillSwitch", kill_cls), ("OpenDWatchdog", dog_cls),
                      ("MoomooGateway", MagicMock()), ("TelegramAlerter", MagicMock()),
                      ("get_gateway_config", MagicMock())):
        monkeypatch.setattr(svc, name, obj)
    monkeypatch.setattr(svc.asyncio, "run", lambda coro: (ran.append(1), coro.close()))

    svc.main(rules)

    cl.assert_called_once_with(log_name="ibs.log", force=True)
    store_cls.assert_called_once_with("data/ibs_state.db")
    kill_cls.assert_called_once_with(sentinel_path=".bot_kill_ibs")
    assert ran == [1]
    assert dog_cls.call_count == 1
    assert (tmp_path / "data").is_dir()


def test_main_invalid_rules_exits_before_gateway(ibs_rules, tmp_path, monkeypatch, capsys):
    import bot.ibs.service as svc
    ibs_rules["signal"]["ibs_entry_max"] = 5.0  # violates the schema
    rules = _write_ibs_rules(tmp_path, ibs_rules)
    gw = MagicMock()
    monkeypatch.setattr(svc, "MoomooGateway", gw)
    monkeypatch.setattr(svc, "configure_logging", MagicMock())
    with pytest.raises(SystemExit) as exc:
        svc.main(rules)
    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err
    gw.assert_not_called()
