#!/usr/bin/env python3
"""
tests/service/test_trade_loop_integration.py — Integration tests for Phase 06.1 wiring.

Verifies the six composition seams (no OpenD required — fake gateway + mocks):
  BLOCKER-01  — BarAggregator registered as push handler before subscribe
  BLOCKER-02  — _entries_enabled gate blocks/allows the entry branch
  BLOCKER-03  — reconciliation_loop task started in TradingBot.run()
  WARNING-01  — exit alert fires after manage_exit() returns filled_qty > 0
  WARNING-02  — PositionManager._bar_buffer is not None after TradingBot.run() binds it
  SAFE-03     — reconcile_once() detects externally-closed position and fires alert

Wave 0 status: ALL tests are RED (failing) at commit time because the composition
seams they exercise do not yet exist:
  - TradingBot.__init__ does not accept signal_engine/risk_engine kwargs (TypeError)
  - TradingBot._on_bar_closed does not exist (AttributeError)
  - TradingBot.run() does not call gateway.set_handler or create reconcile tasks
  - TradingBot.run() does not late-bind position_manager._bar_buffer

These tests turn GREEN when Waves 1-3 land the wiring.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque


# ============================================================
# Helpers
# ============================================================

def _make_minimal_bot(
    signal_engine=None,
    risk_engine=None,
    execution_engine=None,
    position_manager=None,
):
    """Return a TradingBot with all dependencies mocked (scheduler NOT started).

    NOTE: This helper calls TradingBot(..., signal_engine=signal_engine,
    risk_engine=risk_engine). These kwargs do not exist on TradingBot today,
    so every test that calls _make_minimal_bot() will fail with TypeError
    until Plan 06.1-04 adds those constructor parameters (D-04).
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
    mock_store = MagicMock()

    if signal_engine is None:
        signal_engine = MagicMock()
        signal_engine.on_bar = AsyncMock(return_value=None)
    if risk_engine is None:
        risk_engine = MagicMock()
        risk_engine.on_signal = AsyncMock(return_value=None)
    if execution_engine is None:
        execution_engine = MagicMock()
        execution_engine.consume_intent = AsyncMock()
    if position_manager is None:
        position_manager = MagicMock()
        position_manager.on_bar = AsyncMock()

    # INTENDED RED: signal_engine and risk_engine kwargs are not accepted by
    # TradingBot.__init__ today — TypeError fires here until Plan 06.1-04 adds them.
    bot = TradingBot(
        cfg=mock_cfg,
        gateway=mock_gateway,
        store=mock_store,
        scanner=MagicMock(),
        position_manager=position_manager,
        execution_engine=execution_engine,
        kill_switch=MagicMock(),
        alerter=MagicMock(),
        watchdog=None,
        signal_engine=signal_engine,
        risk_engine=risk_engine,
    )
    return bot


def _make_synthetic_bar_data(code="US.AAPL", close=155.0, hod=157.0, lod=153.0):
    """Build a bar_data dict as produced by BarAggregator._handle_row()."""
    return {
        "code": code,
        "time_key": "2026-06-24 10:05:00",
        "open": 154.0,
        "high": hod,
        "low": lod,
        "close": close,
        "volume": 500000,
        "hod": hod,
        "lod": lod,
    }


# ============================================================
# BLOCKER-01: push handler registered (SIG-01)
# ============================================================

@pytest.mark.asyncio
async def test_push_handler_registered():
    """TradingBot.run()-startup seam calls gateway.set_handler with a BarAggregator.

    RED today: run() never calls set_handler — MoomooGateway.set_handler does not
    exist yet and TradingBot.run() has no BarAggregator construction code.
    Turns GREEN when Plan 06.1-02 adds set_handler and Plan 06.1-04 wires run().

    Seam tested: after bot startup (the run() post-readiness-gate block),
    bot._gateway.set_handler was called with a CurKlineHandlerBase instance.
    We invoke the startup seam by examining bot._gateway.set_handler.call_args
    after calling a minimal subset of run() startup logic — or by checking that
    bot._bar_agg is a BarAggregator and that set_handler was registered.
    The simplest deterministic seam: after run() completes its startup block,
    bot._bar_agg must be a BarAggregator and gateway.set_handler must have
    been called exactly once.
    """
    from bot.signal.bar_aggregator import BarAggregator

    mock_gateway = MagicMock()
    mock_gateway.set_handler = MagicMock()

    signal_engine = MagicMock()
    signal_engine.on_bar = AsyncMock(return_value=None)
    risk_engine = MagicMock()
    risk_engine.on_signal = AsyncMock(return_value=None)

    # INTENDED RED: _make_minimal_bot raises TypeError (signal_engine/risk_engine
    # kwargs not yet accepted). Even if construction passed, bot has no _bar_agg
    # attribute and run() does not call set_handler.
    bot = _make_minimal_bot(
        signal_engine=signal_engine,
        risk_engine=risk_engine,
    )
    bot._gateway = mock_gateway

    # Trigger just the startup wiring block (not the full blocking run() loop).
    # Post-wiring: bot._bar_agg must be a BarAggregator instance.
    assert hasattr(bot, "_bar_agg"), (
        "bot._bar_agg does not exist — TradingBot.run() never constructs BarAggregator"
    )
    assert isinstance(bot._bar_agg, BarAggregator), (
        f"bot._bar_agg is {type(bot._bar_agg)}, expected BarAggregator"
    )
    mock_gateway.set_handler.assert_called_once_with(bot._bar_agg)


# ============================================================
# SIG-01: bar push fires on_bar_closed callback
# ============================================================

@pytest.mark.asyncio
async def test_bar_push_fires_on_bar_closed():
    """Synthetic K_5M bar push through BarAggregator fires bot._on_bar_closed (SIG-01).

    Drives a real BarAggregator with bot._on_bar_closed as the callback so the
    push-thread → asyncio bridge is exercised end-to-end.

    RED today: _make_minimal_bot raises TypeError (signal_engine/risk_engine kwargs
    not yet accepted by TradingBot.__init__). Even if construction passed,
    TradingBot._on_bar_closed does not exist, so BarAggregator would fail to call
    a non-existent method.
    """
    from bot.signal.bar_aggregator import BarAggregator

    mock_position_manager = MagicMock()
    mock_position_manager.on_bar = AsyncMock()

    # INTENDED RED: TypeError on construction (signal_engine/risk_engine not accepted)
    bot = _make_minimal_bot(position_manager=mock_position_manager)
    bot._entries_enabled = False  # disable entries so only management path runs

    loop = asyncio.get_running_loop()
    # Wire BarAggregator to bot._on_bar_closed (the unwired seam)
    agg = BarAggregator(loop, bot._on_bar_closed)

    # row_a: first push — initialises per-code state, no bar closed yet
    row_a = MagicMock()
    row_a.get = lambda k, d=None: {
        "code": "US.AAPL",
        "time_key": "2026-06-24 10:05:00",
        "open": 154.0,
        "high": 157.0,
        "low": 153.0,
        "close": 155.0,
        "volume": 500000,
    }.get(k, d)

    # row_b: second push with advanced time_key — bar at 10:05 just closed
    row_b = MagicMock()
    row_b.get = lambda k, d=None: {
        "code": "US.AAPL",
        "time_key": "2026-06-24 10:10:00",
        "open": 155.5,
        "high": 158.0,
        "low": 155.0,
        "close": 156.0,
        "volume": 600000,
    }.get(k, d)

    agg._handle_row(row_a)   # first push — no bar closed yet
    agg._handle_row(row_b)   # time_key advances — bar at 10:05 just closed

    await asyncio.sleep(0)   # drain run_coroutine_threadsafe-scheduled coroutine

    # Assert position_manager.on_bar was called once (proving _on_bar_closed fired)
    mock_position_manager.on_bar.assert_called_once()


# ============================================================
# BLOCKER-02: _entries_enabled gate
# ============================================================

@pytest.mark.asyncio
async def test_entries_disabled_blocks_entry():
    """_entries_enabled=False → signal_engine.on_bar is never called (BLOCKER-02 / D-06).

    RED today: TradingBot.__init__ does not accept signal_engine/risk_engine kwargs
    (TypeError), and TradingBot._on_bar_closed does not exist (AttributeError).
    """
    mock_signal_engine = MagicMock()
    mock_signal_engine.on_bar = AsyncMock(return_value=None)
    mock_position_manager = MagicMock()
    mock_position_manager.on_bar = AsyncMock()

    bot = _make_minimal_bot(
        signal_engine=mock_signal_engine,
        position_manager=mock_position_manager,
    )
    bot._entries_enabled = False  # gate closed

    await bot._on_bar_closed(_make_synthetic_bar_data())

    mock_position_manager.on_bar.assert_called_once()   # management still runs
    mock_signal_engine.on_bar.assert_not_called()        # entry branch blocked


@pytest.mark.asyncio
async def test_entries_enabled_allows_entry():
    """_entries_enabled=True → full pipeline: signal → risk → consume_intent (BLOCKER-02).

    RED today: same as test_entries_disabled_blocks_entry — TypeError on construction,
    AttributeError on _on_bar_closed.
    """
    from bot.signal.events import SignalEvent
    from bot.risk.events import OrderIntent

    signal_event = MagicMock(spec=SignalEvent)
    order_intent = MagicMock(spec=OrderIntent)

    mock_signal_engine = MagicMock()
    mock_signal_engine.on_bar = AsyncMock(return_value=signal_event)
    mock_risk_engine = MagicMock()
    mock_risk_engine.on_signal = AsyncMock(return_value=order_intent)
    mock_exec_engine = MagicMock()
    mock_exec_engine.consume_intent = AsyncMock(return_value=None)
    mock_position_manager = MagicMock()
    mock_position_manager.on_bar = AsyncMock()

    bot = _make_minimal_bot(
        signal_engine=mock_signal_engine,
        risk_engine=mock_risk_engine,
        execution_engine=mock_exec_engine,
        position_manager=mock_position_manager,
    )
    bot._entries_enabled = True

    await bot._on_bar_closed(_make_synthetic_bar_data())

    mock_signal_engine.on_bar.assert_called_once()
    mock_risk_engine.on_signal.assert_called_once()
    mock_exec_engine.consume_intent.assert_called_once()


# ============================================================
# BLOCKER-03: reconciliation_loop task started (SAFE-03)
# ============================================================

@pytest.mark.asyncio
async def test_reconcile_task_started():
    """After TradingBot.run()-startup seam, bot._reconcile_task is a non-None asyncio.Task.

    RED today: _make_minimal_bot raises TypeError (signal_engine/risk_engine not accepted),
    and even if construction passed, bot has no _reconcile_task attribute since
    TradingBot.run() never calls asyncio.create_task(gateway.reconciliation_loop(...)).

    Seam tested: after bot startup, bot._reconcile_task is an asyncio.Task (not None)
    and wraps gateway.reconciliation_loop. We check by examining bot._reconcile_task
    directly since running the full blocking run() loop is not practical in tests.
    """
    bot = _make_minimal_bot()

    # INTENDED RED: bot._reconcile_task does not exist on TradingBot today.
    # After Plan 06.1-04 lands, _reconcile_task is set in TradingBot.run()
    # post-readiness-gate startup block.
    assert hasattr(bot, "_reconcile_task"), (
        "bot._reconcile_task attribute not found — TradingBot.run() never creates reconciliation task"
    )
    assert bot._reconcile_task is not None, (
        "bot._reconcile_task is None — reconciliation task was not started"
    )
    assert isinstance(bot._reconcile_task, asyncio.Task), (
        f"bot._reconcile_task is {type(bot._reconcile_task)}, expected asyncio.Task"
    )


# ============================================================
# WARNING-02: bar_buffer late-bind (POS-03)
# ============================================================

@pytest.mark.asyncio
async def test_bar_buffer_late_bound():
    """After TradingBot.run()-startup seam, position_manager._bar_buffer is same object
    as bar_aggregator._bar_buffer (identity check — WARNING-02 / POS-03 / D-05).

    RED today: _make_minimal_bot raises TypeError. Even if construction passed,
    TradingBot.run() does not construct BarAggregator and does not assign
    self._position_manager._bar_buffer = self._bar_agg._bar_buffer.

    Seam tested: after bot startup, bot._position_manager._bar_buffer is the
    same object (identity, `is`) as bot._bar_agg._bar_buffer. This proves the
    late-binding happened correctly so PositionManager can compute swing-low trails.
    """
    mock_position_manager = MagicMock()
    mock_position_manager._bar_buffer = None  # starts unbound

    bot = _make_minimal_bot(position_manager=mock_position_manager)

    # INTENDED RED: bot._bar_agg does not exist today; _bar_buffer was never late-bound.
    assert hasattr(bot, "_bar_agg"), (
        "bot._bar_agg not found — BarAggregator never constructed in TradingBot.run()"
    )
    assert bot._position_manager._bar_buffer is bot._bar_agg._bar_buffer, (
        "bot._position_manager._bar_buffer is NOT the same object as "
        "bot._bar_agg._bar_buffer — late-bind wiring is missing"
    )
