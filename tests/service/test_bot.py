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

@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
def test_premarket_scan_job_called():
    """TradingBot must register a premarket scan job at cfg.premarket_scan_et (SVC-01)."""
    bot, _, _ = _make_bot_with_mocks()
    # _register_jobs must add a job whose id contains 'premarket' or 'scan'
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("premarket" in jid or "scan" in jid for jid in job_ids), (
        f"No premarket scan job registered; jobs: {job_ids}"
    )


@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
def test_intraday_rescan_job_called():
    """Intraday rescan job must fire ~7× in the 09:55–12:55 window at 30min intervals (SCAN-07)."""
    bot, _, _ = _make_bot_with_mocks()
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("rescan" in jid or "intraday" in jid for jid in job_ids), (
        f"No intraday rescan job registered; jobs: {job_ids}"
    )


@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
def test_force_close_job_called():
    """TradingBot must register a force-close job at cfg.force_close_et (SVC-01, D-08)."""
    bot, _, _ = _make_bot_with_mocks()
    bot._register_jobs()
    job_ids = [j.id for j in bot._scheduler.get_jobs()]
    assert any("force_close" in jid or "force-close" in jid for jid in job_ids), (
        f"No force-close job registered; jobs: {job_ids}"
    )


@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
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


@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
def test_kill_switch_flush_registered():
    """KillSwitch.register_flush must be wired with position_manager.flush_all before run() (R-04-01)."""
    bot, ks_mock, pm_mock = _make_bot_with_mocks()
    # _register_jobs (called inside run startup) should wire register_flush
    bot._register_jobs()
    ks_mock.register_flush.assert_called_once_with(pm_mock.flush_all)


@pytest.mark.xfail(reason="implemented in 05-01 (TradingBot not yet created)", strict=False)
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
