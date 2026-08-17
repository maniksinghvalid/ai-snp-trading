#!/usr/bin/env python3
"""
tests/service/test_bot.py — RED unit stubs for TradingBot (SVC-01, SCAN-07, R-04-01).

These tests target bot.service.bot.TradingBot which is implemented in plan 05-01.
They are xfail until that plan lands, so the suite collects cleanly and the
later wave has concrete verification targets to turn green.

Requirements covered:
  SVC-01  — APScheduler-driven main loop with correct ET-time jobs
  SCAN-07 — Intraday rescan job fires within the 09:55–12:55 window (~7× at 30min)
  R-04-01 — KillSwitch.register_flush wired before run() starts
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio


# ============================================================
# Helpers
# ============================================================

def _make_bot_with_mocks():
    """Return a TradingBot with all dependencies mocked (no scheduler started).

    Raises ImportError (caught by xfail) until bot.service.bot is implemented.
    """
    from bot.service.bot import TradingBot  # noqa: F401 — import as proof it exists

    mock_cfg = MagicMock()
    mock_cfg.premarket_scan_et = "08:30"
    mock_cfg.market_open_et = "09:30"
    mock_cfg.intraday_rescan_interval_min = 30
    mock_cfg.intraday_rescan_start_et = "09:55"
    mock_cfg.intraday_rescan_end_et = "12:55"
    mock_cfg.eod_report_et = "15:55"
    mock_cfg.force_close_et = "15:51"
    mock_cfg.misfire_grace_scan_s = 3600
    mock_cfg.misfire_grace_rescan_s = 600
    mock_cfg.force_close_misfire_grace_s = 300

    mock_gateway = MagicMock()
    mock_gateway.get_global_state = AsyncMock(
        return_value={"connected": True, "qot_logined": True, "trd_logined": True}
    )
    mock_store = MagicMock()
    mock_scanner = MagicMock()
    mock_position_manager = MagicMock()
    mock_engine = MagicMock()
    mock_watchdog = MagicMock()
    mock_alerter = MagicMock()
    mock_kill_switch = MagicMock()

    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=mock_store,
        scanner=mock_scanner,
        position_manager=mock_position_manager,
        execution_engine=mock_engine,
        watchdog=mock_watchdog,
        alerter=mock_alerter,
        kill_switch=mock_kill_switch,
    )
    return bot, mock_kill_switch, mock_position_manager


# ============================================================
# SVC-01: Scheduler jobs (TradingBot)
# ============================================================

def test_premarket_scan_job_called():
    """TradingBot must register a premarket scan job at cfg.premarket_scan_et (SVC-01)."""
    bot, _, _ = _make_bot_with_mocks()
    # _register_jobs must add a job whose id contains 'premarket' or 'scan'
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("premarket" in jid or "scan" in jid for jid in job_ids), (
        f"No premarket scan job registered; jobs: {job_ids}"
    )


def test_intraday_rescan_job_called():
    """Intraday rescan job must fire ~7× in the 09:55–12:55 window at 30min intervals (SCAN-07)."""
    bot, _, _ = _make_bot_with_mocks()
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("rescan" in jid or "intraday" in jid for jid in job_ids), (
        f"No intraday rescan job registered; jobs: {job_ids}"
    )


def test_force_close_job_called():
    """TradingBot must register a force-close job at cfg.force_close_et (SVC-01, D-08)."""
    bot, _, _ = _make_bot_with_mocks()
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("force_close" in jid or "force-close" in jid for jid in job_ids), (
        f"No force-close job registered; jobs: {job_ids}"
    )


@pytest.mark.asyncio
async def test_readiness_gate_blocks_entries():
    """_readiness_gate must set entries_enabled=False until reconcile completes (D-08, SVC-01)."""
    bot, _, pm = _make_bot_with_mocks()
    # Before readiness gate runs, entries should be disabled
    assert bot._entries_enabled is False, "entries_enabled must start False"
    # _readiness_gate enables entries after connect+reconcile
    await bot._readiness_gate()
    assert bot._entries_enabled is True, (
        "_entries_enabled must be True after _readiness_gate completes"
    )


def test_kill_switch_flush_registered():
    """KillSwitch.register_flush must be wired with position_manager.flush_all before run() (R-04-01)."""
    bot, ks_mock, pm_mock = _make_bot_with_mocks()
    # _register_jobs (called inside run startup) should wire register_flush
    bot._register_jobs()
    ks_mock.register_flush.assert_called_once_with(pm_mock.flush_all)


@pytest.mark.asyncio
async def test_intraday_rescan_passes_open_position_codes():
    """Finding 2.2 regression: _job_intraday_rescan must pass real active_codes
    (open positions + in-flight intents), never an empty set, so the scanner
    never evicts/unsubscribes a symbol with an open position or pending intent.
    """
    from datetime import date, time as dtime

    bot, _, _ = _make_bot_with_mocks()

    bot._store.get_open_positions.return_value = [
        {"code": "US.AAPL", "remaining_quantity": 100, "phase": "ACTIVE"},
    ]
    bot._store.get_pending_intent_codes.return_value = [
        {"intent_id": "intent-1", "code": "US.MSFT"},
    ]
    bot._scanner.run_intraday_rescan.return_value = []

    with patch("bot.service.bot.is_trading_day", return_value=True), \
         patch("bot.service.bot.now_et") as mock_now:
        mock_now.return_value.date.return_value = date(2026, 6, 24)
        mock_now.return_value.time.return_value = dtime(10, 30)
        await bot._job_intraday_rescan()

    assert bot._scanner.run_intraday_rescan.call_count == 1
    _, kwargs = bot._scanner.run_intraday_rescan.call_args
    active_codes = kwargs.get("active_codes")
    assert active_codes == {"US.AAPL", "US.MSFT"}, (
        f"Expected active_codes={{'US.AAPL', 'US.MSFT'}} from open positions + "
        f"pending intents, got {active_codes!r}"
    )


def test_scheduler_has_required_jobs():
    """TradingBot._register_jobs must add premarket, market_open, rescan, force_close, eod_report jobs."""
    bot, _, _ = _make_bot_with_mocks()
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    REQUIRED_PATTERNS = ["premarket", "market_open", "rescan", "force_close", "eod"]
    for pattern in REQUIRED_PATTERNS:
        assert any(pattern in jid for jid in job_ids), (
            f"No job with '{pattern}' in job id; jobs: {job_ids}"
        )


# ============================================================
# 05-04: EOD report job (DASH-01, ALERT-03)
# ============================================================

@pytest.mark.asyncio
async def test_eod_report_job_writes_reports_and_dispatches_summary(tmp_path):
    """_job_eod_report must call write_reports and dispatch the daily summary (DASH-01, ALERT-03)."""
    bot, _, _ = _make_bot_with_mocks()

    # Configure mock_store to return known data
    mock_trades = [
        {"code": "US.AAPL", "r_multiple": 1.5, "entry_price": 190.0,
         "exit_price": 195.0, "quantity": 10, "exit_reason": "trail",
         "closed_at": "2026-06-24T15:00:00"},
    ]
    mock_positions = []
    bot._store.get_closed_trades.return_value = mock_trades
    # get_daily_trade_stats provides uncapped aggregates for the summary (DAILY-CAP-01)
    bot._store.get_daily_trade_stats.return_value = {
        "trade_count": 1, "wins": 1, "losses": 0, "realized_pnl": 50.0
    }
    bot._store.get_open_positions.return_value = mock_positions

    # Patch write_reports and is_trading_day
    with patch("bot.service.bot._write_reports") as mock_write, \
         patch("bot.service.bot.is_trading_day", return_value=True), \
         patch("bot.service.bot.now_et") as mock_now:
        from datetime import date
        mock_now.return_value.date.return_value = date(2026, 6, 24)
        mock_write.return_value = None
        await bot._job_eod_report()

    # write_reports must have been called once
    assert mock_write.called, "_write_reports must be called by _job_eod_report"

    # alerter.send must have been dispatched (create_task is fire-and-forget,
    # so check format_daily_summary was called or send was called)
    assert bot._alerter.send.called or bot._alerter.format_daily_summary.called, (
        "Daily summary must be dispatched via alerter"
    )


# ============================================================
# Regression: premarket-high-not-seeded (D-01/D-03 seam lock)
#
# _job_market_open_subscribe must call signal_engine.fetch_premarket_highs(codes)
# BEFORE gateway.subscribe(codes) so Gate 1 of on_bar is satisfied before any bar
# can flow. subscribe() arms the push handler; if it ran first, bars arriving during
# the snapshot RTT (~100-500ms) would hit Gate 1 with an empty _premarket_highs dict
# and be dropped (race). Seeding first eliminates the window.
# Without the seed call at all, _premarket_highs stays empty and no entry can fire.
# ============================================================

@pytest.mark.asyncio
async def test_market_open_subscribe_seeds_premarket_highs():
    """_job_market_open_subscribe must call fetch_premarket_highs(codes) BEFORE subscribe(codes).

    Regression for premarket-high-not-seeded (2026-06-25): the market-open job
    subscribed feeds but never called fetch_premarket_highs(), leaving Gate 1 of
    on_bar permanently blocking every bar with signal_skipped_no_premarket_high.

    Race follow-up (ordering lock): even with the seed call present, running it
    AFTER subscribe leaves a ~100-500ms window (the snapshot RTT) during which
    subscribe()'s armed push handler can deliver bars that hit an empty
    _premarket_highs dict and are dropped. The fix moves fetch_premarket_highs
    to run BEFORE gateway.subscribe so _premarket_highs is frozen before the
    push handler can deliver any bar. This test locks that ordering via a shared
    call-recorder parent mock so the sequence cannot silently regress.
    """
    from bot.service.bot import TradingBot
    from datetime import date

    mock_cfg = MagicMock()
    mock_cfg.premarket_scan_et = "08:30"
    mock_cfg.market_open_et = "09:30"
    mock_cfg.intraday_rescan_interval_min = 30
    mock_cfg.intraday_rescan_start_et = "09:55"
    mock_cfg.intraday_rescan_end_et = "12:55"
    mock_cfg.eod_report_et = "15:55"
    mock_cfg.force_close_et = "15:51"
    mock_cfg.misfire_grace_scan_s = 3600
    mock_cfg.misfire_grace_rescan_s = 600
    mock_cfg.force_close_misfire_grace_s = 300

    # Shared parent mock as a call recorder: attaching both async children to it
    # records their invocations on a single ordered `parent.mock_calls` list, so
    # we can assert fetch_premarket_highs happens strictly BEFORE subscribe.
    call_recorder = MagicMock()
    call_recorder.fetch_premarket_highs = AsyncMock(
        return_value={"US.AAPL": 150.0, "US.MSFT": 300.0}
    )
    call_recorder.subscribe = AsyncMock()

    mock_gateway = MagicMock()
    mock_gateway.subscribe = call_recorder.subscribe
    mock_store = MagicMock()
    mock_store.get_watchlist_codes.return_value = ["US.AAPL", "US.MSFT"]

    # The critical mock: fetch_premarket_highs must be called by the job
    mock_signal_engine = MagicMock()
    mock_signal_engine.fetch_premarket_highs = call_recorder.fetch_premarket_highs

    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=mock_store,
        scanner=MagicMock(),
        position_manager=MagicMock(),
        execution_engine=MagicMock(),
        kill_switch=MagicMock(),
        alerter=MagicMock(),
        watchdog=None,
        signal_engine=mock_signal_engine,
        risk_engine=MagicMock(),
    )

    with patch("bot.service.bot.is_trading_day", return_value=True), \
         patch("bot.service.bot.now_et") as mock_now:
        mock_now.return_value.date.return_value = date(2026, 6, 24)
        await bot._job_market_open_subscribe()

    # Assert gateway.subscribe was called with the watchlist
    mock_gateway.subscribe.assert_called_once_with(["US.AAPL", "US.MSFT"])

    # Assert fetch_premarket_highs was called with the same codes (the regression seam)
    mock_signal_engine.fetch_premarket_highs.assert_called_once_with(["US.AAPL", "US.MSFT"])

    # ---- Ordering lock (race fix): fetch_premarket_highs MUST precede subscribe ----
    # Build the ordered sequence of method names recorded on the shared parent.
    recorded_order = [c[0] for c in call_recorder.mock_calls if c[0]]
    assert "fetch_premarket_highs" in recorded_order, "seed call must occur"
    assert "subscribe" in recorded_order, "subscribe call must occur"
    assert recorded_order.index("fetch_premarket_highs") < recorded_order.index("subscribe"), (
        "_job_market_open_subscribe must call signal_engine.fetch_premarket_highs(codes) "
        "BEFORE gateway.subscribe(codes) so _premarket_highs is frozen before the push "
        "handler is armed — otherwise bars arriving during the snapshot RTT hit an empty "
        "Gate 1 and are dropped (premarket-high-not-seeded race follow-up, D-01/D-03). "
        f"Recorded order was: {recorded_order}"
    )


@pytest.mark.asyncio
async def test_market_open_subscribe_skips_seed_when_signal_engine_none():
    """_job_market_open_subscribe must not crash when signal_engine is None.

    Defensive guard: if signal_engine is not injected (e.g. during early startup or
    in test rigs that don't need it), the seeding step is silently skipped rather
    than raising AttributeError. The subscribe still completes normally.
    """
    from bot.service.bot import TradingBot
    from datetime import date

    mock_cfg = MagicMock()
    mock_cfg.premarket_scan_et = "08:30"
    mock_cfg.market_open_et = "09:30"
    mock_cfg.intraday_rescan_interval_min = 30
    mock_cfg.intraday_rescan_start_et = "09:55"
    mock_cfg.intraday_rescan_end_et = "12:55"
    mock_cfg.eod_report_et = "15:55"
    mock_cfg.force_close_et = "15:51"
    mock_cfg.misfire_grace_scan_s = 3600
    mock_cfg.misfire_grace_rescan_s = 600
    mock_cfg.force_close_misfire_grace_s = 300

    mock_gateway = MagicMock()
    mock_gateway.subscribe = AsyncMock()
    mock_store = MagicMock()
    mock_store.get_watchlist_codes.return_value = ["US.AAPL"]

    # signal_engine=None — the guard must prevent AttributeError
    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=mock_store,
        scanner=MagicMock(),
        position_manager=MagicMock(),
        execution_engine=MagicMock(),
        kill_switch=MagicMock(),
        alerter=MagicMock(),
        watchdog=None,
        signal_engine=None,
        risk_engine=None,
    )

    with patch("bot.service.bot.is_trading_day", return_value=True), \
         patch("bot.service.bot.now_et") as mock_now:
        mock_now.return_value.date.return_value = date(2026, 6, 24)
        # Must not raise even with signal_engine=None
        await bot._job_market_open_subscribe()

    # Subscribe still called
    mock_gateway.subscribe.assert_called_once_with(["US.AAPL"])


# ============================================================
# Finding 1.1 + #5: wire FillEvent into PositionManager; resolve intent on both paths
# ============================================================

def _make_bot_with_full_pipeline():
    """Return a TradingBot with full signal/risk/exec pipeline mocked for _process_bar tests.

    Sets up AsyncMock for all async methods called inside _process_bar so the pipeline
    can be driven end-to-end without a running event loop at construction time.
    """
    from bot.service.bot import TradingBot

    mock_cfg = MagicMock()
    mock_cfg.premarket_scan_et = "08:30"
    mock_cfg.market_open_et = "09:30"
    mock_cfg.intraday_rescan_interval_min = 30
    mock_cfg.intraday_rescan_start_et = "09:55"
    mock_cfg.intraday_rescan_end_et = "12:55"
    mock_cfg.eod_report_et = "15:55"
    mock_cfg.force_close_et = "15:51"
    mock_cfg.misfire_grace_scan_s = 3600
    mock_cfg.misfire_grace_rescan_s = 600
    mock_cfg.force_close_misfire_grace_s = 300

    mock_gateway = MagicMock()
    mock_gateway.get_global_state = AsyncMock(
        return_value={"connected": True, "qot_logined": True, "trd_logined": True}
    )

    mock_signal_engine = MagicMock()
    mock_signal_engine.on_bar = AsyncMock(return_value=MagicMock())
    mock_signal_engine.note_intent_resolved = MagicMock()

    mock_intent = MagicMock()
    mock_intent.code = "US.AAPL"
    mock_intent.entry_price = 182.55
    mock_intent.stop_price = 180.18
    mock_intent.quantity = 100
    mock_intent.intent_id = "intent-test-001"

    mock_risk_engine = MagicMock()
    mock_risk_engine.on_signal = AsyncMock(return_value=mock_intent)

    mock_exec_engine = MagicMock()
    # Default: consume_intent returns None (abandon path); override per test.
    mock_exec_engine.consume_intent = AsyncMock(return_value=None)

    mock_position_manager = MagicMock()
    mock_position_manager.on_bar = AsyncMock()
    mock_position_manager.arm_stop_protection = AsyncMock()  # D-01 post-fill hook

    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=MagicMock(),
        scanner=MagicMock(),
        position_manager=mock_position_manager,
        execution_engine=mock_exec_engine,
        kill_switch=MagicMock(),
        alerter=MagicMock(),
        watchdog=None,
        signal_engine=mock_signal_engine,
        risk_engine=mock_risk_engine,
    )
    # Enable entries so the signal/risk/exec pipeline branch runs.
    bot._entries_enabled = True
    return bot, mock_signal_engine, mock_position_manager, mock_exec_engine, mock_intent


_PROCESS_BAR_DATA = {
    "code": "US.AAPL",
    "time_key": "2026-06-24 10:05:00",
    "open": 182.0,
    "high": 183.0,
    "low": 180.0,
    "close": 182.5,
    "volume": 100000,
    "hod": 183.0,
    "lod": 180.0,
}


@pytest.mark.asyncio
async def test_premarket_scan_reschedules_force_close_for_half_day():
    """Finding 1.5: _job_premarket_scan must reschedule the force_close job via DateTrigger.

    Verifies that after _job_premarket_scan completes:
    1. Exactly one job with id='force_close' exists (reschedule_job, NOT a second add_job).
    2. The trigger is a DateTrigger (not CronTrigger) so it fires once at the
       calendar-aware close minus 9 minutes.
    3. When get_force_close_time_et returns 12:51 (half-day), the job's next
       run time is at 12:51 ET today (not the static 15:51 from rules.json).

    Previously: force_close was registered as a static CronTrigger('15:51') that
    fires every day at 15:51 ET regardless of half-days. On early-close days the
    bot holds positions for ~3 hours past market close.
    """
    from bot.service.bot import TradingBot
    from apscheduler.triggers.date import DateTrigger
    from datetime import date as _date, time as _time
    import datetime as _datetime
    from unittest.mock import patch

    bot, _, _ = _make_bot_with_mocks()

    # Patch get_force_close_time_et to simulate a half-day (12:51 ET close)
    mock_close_time = _time(12, 51)

    from zoneinfo import ZoneInfo as _ZI
    today = _date(2026, 7, 3)  # Example half-day (July 3)
    # 13:00 ET is past the half-day close, so _register_jobs' startup arm is a
    # no-op here and only _job_premarket_scan can produce the 12:51 reschedule.
    with patch("bot.service.bot.get_force_close_time_et", return_value=mock_close_time), \
         patch("bot.service.bot.is_trading_day", return_value=True), \
         patch("bot.service.bot.now_et",
               return_value=_datetime.datetime(2026, 7, 3, 13, 0, tzinfo=_ZI("America/New_York"))):
        # Also register_jobs before running premarket scan so force_close job exists
        bot._register_jobs()

        await bot._job_premarket_scan()

    # Verify exactly one 'force_close' job exists (no duplicate)
    force_close_jobs = [j for j in bot._scheduler.get_jobs() if j.id == "force_close"]
    assert len(force_close_jobs) == 1, (
        f"Exactly one force_close job must exist after _job_premarket_scan; "
        f"got {len(force_close_jobs)}: {[j.id for j in force_close_jobs]}"
    )

    # Verify the trigger is a DateTrigger (not CronTrigger)
    from apscheduler.triggers.cron import CronTrigger
    job = force_close_jobs[0]
    assert isinstance(job.trigger, DateTrigger), (
        f"force_close job must use DateTrigger after reschedule; "
        f"got {type(job.trigger).__name__}"
    )

    # Verify the run_date is at 12:51 ET (half-day close - 9min)
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    expected_run_at = _datetime.datetime.combine(today, mock_close_time, tzinfo=ET)
    # job.next_run_time is timezone-aware; compare in ET
    next_run = job.next_run_time
    if next_run is not None and next_run.tzinfo is not None:
        next_run_et = next_run.astimezone(ET)
        assert next_run_et.hour == 12 and next_run_et.minute == 51, (
            f"force_close job must be scheduled at 12:51 ET on half-day; "
            f"got {next_run_et.strftime('%H:%M')} ET"
        )


def test_register_jobs_arms_force_close_when_started_after_premarket_scan():
    """Regression: a bot (re)started AFTER premarket_scan_et on a trading day must still
    force-close today.

    Observed 2026-08-17: process killed 14:10Z, restarted 14:17Z (10:17 ET). Only
    _job_premarket_scan (08:30 ET) rescheduled the force_close DateTrigger off its
    2099-01-01 placeholder, so the restarted bot would never have force-closed at 15:51.
    _register_jobs (called by run() before scheduler.start()) must arm today's
    calendar-aware close itself. Uses the real calendar: 2026-08-17 is a normal
    Monday session → 15:51 ET.
    """
    import datetime as _datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    bot, _, _ = _make_bot_with_mocks()
    started_at = _datetime.datetime(2026, 8, 17, 10, 17, tzinfo=ET)  # after 08:30, before 15:51

    with patch("bot.service.bot.now_et", return_value=started_at):
        bot._register_jobs()

    job = bot._scheduler.get_job("force_close")
    next_run = getattr(job, "next_run_time", None)  # pending jobs have no attr until (re)scheduled
    assert next_run is not None, "force_close job must be armed at startup"
    assert next_run.astimezone(ET) == _datetime.datetime(2026, 8, 17, 15, 51, tzinfo=ET), (
        f"force_close must be armed for today's 15:51 ET; got {next_run}"
    )


# ============================================================
# Finding 1.1 + #5: wire FillEvent into PositionManager; resolve intent on both paths
# ============================================================

@pytest.mark.asyncio
async def test_process_bar_registers_position_on_fill():
    """Regression 1.1: _process_bar must call register_position + on_fill when consume_intent fills.

    Previously, bot.py:276 discarded the FillEvent return value from consume_intent,
    so PositionManager never received the fill and the position was unmanaged.
    """
    from bot.execution.events import FillEvent
    from datetime import datetime as _dt

    bot, mock_se, mock_pm, mock_ee, mock_intent = _make_bot_with_full_pipeline()

    fill = FillEvent(
        order_id="ord-001",
        intent_id=mock_intent.intent_id,
        code="US.AAPL",
        filled_qty=100,
        avg_fill_price=182.60,
        is_entry=True,
        fill_time=_dt(2026, 6, 24, 10, 5, 0),
    )
    mock_ee.consume_intent = AsyncMock(return_value=fill)

    await bot._process_bar(_PROCESS_BAR_DATA)

    mock_pm.register_position.assert_called_once()
    mock_pm.on_fill.assert_called_once_with(fill)


@pytest.mark.asyncio
async def test_process_bar_resolves_intent_on_fill():
    """Regression 1.1/#5: signal_engine.note_intent_resolved called once on fill path."""
    from bot.execution.events import FillEvent
    from datetime import datetime as _dt

    bot, mock_se, mock_pm, mock_ee, mock_intent = _make_bot_with_full_pipeline()

    fill = FillEvent(
        order_id="ord-002",
        intent_id=mock_intent.intent_id,
        code="US.AAPL",
        filled_qty=100,
        avg_fill_price=182.60,
        is_entry=True,
        fill_time=_dt(2026, 6, 24, 10, 5, 0),
    )
    mock_ee.consume_intent = AsyncMock(return_value=fill)

    await bot._process_bar(_PROCESS_BAR_DATA)

    mock_se.note_intent_resolved.assert_called_once()


@pytest.mark.asyncio
async def test_process_bar_resolves_intent_on_abandon():
    """Regression 1.1/#5: on abandon path (consume_intent=None), both resolve_pending_intent
    and note_intent_resolved must be called (both-paths invariant).
    """
    bot, mock_se, mock_pm, mock_ee, mock_intent = _make_bot_with_full_pipeline()
    # consume_intent returns None → abandon path
    mock_ee.consume_intent = AsyncMock(return_value=None)

    await bot._process_bar(_PROCESS_BAR_DATA)

    # resolve_pending_intent called with the intent_id and "ABANDONED"
    bot._store.resolve_pending_intent.assert_called_once_with(
        mock_intent.intent_id, "ABANDONED"
    )
    # note_intent_resolved called on BOTH paths
    mock_se.note_intent_resolved.assert_called_once()


# ============================================================
# 07-03: arm_stop_protection post-fill hook (D-01/D-03)
# ============================================================

@pytest.mark.asyncio
async def test_process_bar_arm_stop_protection_called_after_on_fill():
    """_process_bar must call position_manager.arm_stop_protection(pos) after on_fill on fill path.

    Broker stop placement (D-01) must be armed immediately after the entry fill is
    confirmed. The test asserts that arm_stop_protection is called exactly once on
    the fill path and not at all on the abandon path.
    """
    from bot.execution.events import FillEvent
    from datetime import datetime as _dt

    bot, mock_se, mock_pm, mock_ee, mock_intent = _make_bot_with_full_pipeline()

    # arm_stop_protection is a new async method on the position_manager mock
    mock_pm.arm_stop_protection = AsyncMock()

    fill = FillEvent(
        order_id="ord-stop-test-001",
        intent_id=mock_intent.intent_id,
        code="US.AAPL",
        filled_qty=100,
        avg_fill_price=182.60,
        is_entry=True,
        fill_time=_dt(2026, 6, 24, 10, 5, 0),
    )
    mock_ee.consume_intent = AsyncMock(return_value=fill)

    await bot._process_bar(_PROCESS_BAR_DATA)

    mock_pm.arm_stop_protection.assert_called_once(), (
        "_process_bar must call arm_stop_protection exactly once after on_fill"
    )


@pytest.mark.asyncio
async def test_process_bar_arm_stop_protection_not_called_on_abandon():
    """_process_bar must NOT call arm_stop_protection on the abandon path (consume_intent=None)."""
    bot, mock_se, mock_pm, mock_ee, mock_intent = _make_bot_with_full_pipeline()

    mock_pm.arm_stop_protection = AsyncMock()
    mock_ee.consume_intent = AsyncMock(return_value=None)

    await bot._process_bar(_PROCESS_BAR_DATA)

    mock_pm.arm_stop_protection.assert_not_called(), (
        "_process_bar must NOT call arm_stop_protection when consume_intent returns None"
    )


# ============================================================
# Task 3 (07-05): Bot orchestrator circuit-breaker side-effects (D-08)
# ============================================================

def _make_breaker_bot(today_str: str = "2026-07-03", breaker_date: str = None):
    """Return a TradingBot with mocks configured for circuit-breaker side-effect tests.

    Args:
        today_str: The ET date string that now_et().date().isoformat() returns.
        breaker_date: What store.get_circuit_breaker_date() returns (None = no trip).
    """
    from bot.service.bot import TradingBot

    mock_cfg = MagicMock()
    mock_cfg.premarket_scan_et = "08:30"
    mock_cfg.market_open_et = "09:30"
    mock_cfg.intraday_rescan_interval_min = 30
    mock_cfg.intraday_rescan_start_et = "09:55"
    mock_cfg.intraday_rescan_end_et = "12:55"
    mock_cfg.eod_report_et = "15:55"
    mock_cfg.force_close_et = "15:51"
    mock_cfg.misfire_grace_scan_s = 3600
    mock_cfg.misfire_grace_rescan_s = 600
    mock_cfg.force_close_misfire_grace_s = 300

    mock_store = MagicMock()
    mock_store.get_circuit_breaker_date.return_value = breaker_date
    # get_pending_intent_codes returns [(intent_id, code)] pairs
    mock_store.get_pending_intent_codes.return_value = [
        ("intent-001", "US.AAPL"),
        ("intent-002", "US.MSFT"),
    ]

    mock_alerter = MagicMock()
    mock_alerter.send = AsyncMock()

    mock_gateway = MagicMock()
    mock_gateway.get_global_state = AsyncMock(
        return_value={"connected": True, "qot_logined": True, "trd_logined": True}
    )

    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=mock_store,
        scanner=MagicMock(),
        position_manager=MagicMock(),
        execution_engine=MagicMock(),
        kill_switch=MagicMock(),
        alerter=mock_alerter,
        watchdog=None,
    )
    return bot, mock_store, mock_alerter


@pytest.mark.asyncio
async def test_breaker_side_effects_on_fresh_trip_abandons_intents_and_alerts():
    """On first trip this session, PENDING intents are marked ABANDONED and one alert fires.

    Verifies (D-08):
    - resolve_pending_intent called for each PENDING intent with status "ABANDONED"
    - alerter.send called exactly once with a circuit-breaker message
    - _breaker_handled becomes True after the first side-effect call
    """
    session_date = "2026-07-03"
    # Breaker is tripped today (store returns today's date)
    bot, mock_store, mock_alerter = _make_breaker_bot(
        today_str=session_date,
        breaker_date=session_date,
    )
    bot._breaker_handled = False  # not yet handled this session

    await bot._handle_circuit_breaker_side_effects(session_date)

    # Both PENDING intents must be abandoned
    mock_store.resolve_pending_intent.assert_any_call("intent-001", "ABANDONED")
    mock_store.resolve_pending_intent.assert_any_call("intent-002", "ABANDONED")
    assert mock_store.resolve_pending_intent.call_count == 2, (
        "resolve_pending_intent must be called for each PENDING intent (D-08)."
    )

    # Exactly one alert must be sent
    mock_alerter.send.assert_called_once(), (
        "alerter.send must be called exactly once on the first trip (D-08)."
    )

    # Flag must be set so subsequent bars don't repeat the side-effects
    assert bot._breaker_handled is True, (
        "_breaker_handled must be True after first side-effects run."
    )


@pytest.mark.asyncio
async def test_breaker_side_effects_idempotent_on_subsequent_bars():
    """On a subsequent bar (breaker still tripped, _breaker_handled=True), no further action.

    Verifies the one-shot invariant: side-effects run only ONCE per session (D-08).
    """
    session_date = "2026-07-03"
    bot, mock_store, mock_alerter = _make_breaker_bot(
        today_str=session_date,
        breaker_date=session_date,
    )
    bot._breaker_handled = True  # already handled on a prior bar

    await bot._handle_circuit_breaker_side_effects(session_date)

    # No additional abandonment or alert
    mock_store.resolve_pending_intent.assert_not_called(), (
        "No PENDING intents must be abandoned on repeat calls (_breaker_handled=True)."
    )
    mock_alerter.send.assert_not_called(), (
        "alerter.send must NOT be called again when _breaker_handled is True."
    )


@pytest.mark.asyncio
async def test_breaker_handled_initialized_from_store_on_startup():
    """At startup (_readiness_gate), _breaker_handled is initialized from stored circuit breaker date.

    - If store.get_circuit_breaker_date() == today's ET date → _breaker_handled = True
      (restart does not re-alert or re-cancel — Pitfall 6).
    - If stored date is None or a prior date → _breaker_handled = False.

    This test drives _readiness_gate directly to simulate the startup sequence.
    """
    from bot.service.bot import TradingBot
    from datetime import date as _date

    today_str = "2026-07-03"

    # Case 1: breaker was tripped today → _breaker_handled must start True after startup
    bot_tripped, mock_store_tripped, _ = _make_breaker_bot(
        today_str=today_str,
        breaker_date=today_str,
    )
    with patch("bot.service.bot.now_et") as mock_now:
        mock_now.return_value.date.return_value = _date(2026, 7, 3)
        mock_now.return_value.time.return_value.replace.return_value = __import__("datetime").time(8, 0)
        await bot_tripped._readiness_gate()

    assert bot_tripped._breaker_handled is True, (
        "After startup with today's breaker date, _breaker_handled must be True "
        "so a mid-session restart does not re-alert or re-cancel (Pitfall 6)."
    )

    # Case 2: no breaker date → _breaker_handled must start False after startup
    bot_clear, mock_store_clear, _ = _make_breaker_bot(
        today_str=today_str,
        breaker_date=None,  # no prior trip
    )
    with patch("bot.service.bot.now_et") as mock_now:
        mock_now.return_value.date.return_value = _date(2026, 7, 3)
        mock_now.return_value.time.return_value.replace.return_value = __import__("datetime").time(8, 0)
        await bot_clear._readiness_gate()

    assert bot_clear._breaker_handled is False, (
        "After startup with no breaker date, _breaker_handled must be False."
    )


@pytest.mark.asyncio
async def test_breaker_alert_failure_swallowed_alert04():
    """alerter.send failures in _handle_circuit_breaker_side_effects must be swallowed (ALERT-04).

    A failing alert must not propagate into the bar loop or prevent _breaker_handled
    from being set True. The bot continues running normally after a send failure.
    """
    session_date = "2026-07-03"
    bot, mock_store, mock_alerter = _make_breaker_bot(
        today_str=session_date,
        breaker_date=session_date,
    )
    bot._breaker_handled = False

    # Alerter raises on send — must be swallowed (ALERT-04)
    mock_alerter.send = AsyncMock(side_effect=Exception("Telegram timeout"))

    # Must not raise
    await bot._handle_circuit_breaker_side_effects(session_date)

    # _breaker_handled must still be set True (side-effects ran, alert failed silently)
    assert bot._breaker_handled is True, (
        "_breaker_handled must be True even when alerter.send raises (ALERT-04: "
        "alert failures must not interrupt the bar loop)."
    )
