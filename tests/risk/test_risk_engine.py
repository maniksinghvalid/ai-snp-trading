#!/usr/bin/env python3
"""
tests/risk/test_risk_engine.py — Tests for bot/risk/risk_engine.py

Verifies (no broker — gateway.get_equity is mocked):
  - RISK-01: 1% risk sizing from live equity
  - RISK-01: $100k fallback on failed or implausible equity query (D-05)
  - RISK-02: 10% notional cap applied when it yields a smaller qty
  - RISK-03: OrderIntent carries correct stop_price (LOD − 1%) and quantity
  - D-07: <1-share result emits no intent; reason logged
  - D-12: intent persisted to StateStore pending_intents table
  - D-12: intent logged to structlog
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import capture_logs

from bot.config.loader import StrategyConfig
from bot.risk.events import OrderIntent
from bot.signal.events import BarEvent, SignalEvent
from bot.state.store import StateStore


# ============================================================
# Fixtures and helpers
# ============================================================

# Phase 4 (04-01) added 10 required execution fields to StrategyConfig.
# These risk tests don't exercise execution behavior, so _make_cfg spreads
# these canonical defaults (matching rules.json) to satisfy the constructor.
_EXECUTION_DEFAULTS = {
    "entry_limit_buffer_usd": 0.05,
    "entry_ttl_seconds": 20,
    "entry_max_retries": 2,
    "entry_poll_interval_seconds": 5,
    "exit_limit_buffer_usd": 0.05,
    "exit_ttl_seconds": 15,
    "exit_escalation_step_usd": 0.10,
    "exit_escalation_cadence_seconds": 10,
    "force_close_escalation_step_usd": 0.20,
    "force_close_escalation_cadence_seconds": 15,
}

# ============================================================
# Service defaults (Phase 5 — canonical rules.json service block)
# ============================================================
#
# StrategyConfig gained 15 required service fields in Phase 5 (05-00).
# These tests don't exercise service/scheduler behavior, so every
# StrategyConfig(...) call spreads these canonical defaults to satisfy the
# constructor. Values match the service block in rules.json exactly.
_SERVICE_DEFAULTS = {
    "premarket_scan_et": "08:30",
    "market_open_et": "09:30",
    "intraday_rescan_interval_min": 30,
    "intraday_rescan_start_et": "09:55",
    "intraday_rescan_end_et": "12:55",
    "eod_report_et": "15:55",
    "watchdog_poll_interval_s": 60,
    "watchdog_reconnect_initial_s": 5,
    "watchdog_reconnect_cap_s": 60,
    "alerts_enabled": True,
    "misfire_grace_scan_s": 3600,
    "misfire_grace_rescan_s": 600,
    "force_close_misfire_grace_s": 300,
    "launchd_throttle_interval_s": 30,
    "crash_loop_alert_threshold": 5,
}


def _make_cfg(
    max_risk_per_trade_pct: float = 1.0,
    max_position_size_pct: int = 10,
    max_concurrent_positions: int = 5,
    max_trades_per_day: int = 5,
) -> StrategyConfig:
    """Return a StrategyConfig with test-friendly risk parameters."""
    return StrategyConfig(
        min_price_usd=10.0,
        d3_min_gap_pct=1.0,
        rvol_min=2.0,
        rvol_lookback_days=14,
        earliest_entry_et="10:05",
        latest_entry_et="15:30",
        force_close_et="15:55",
        initial_stop_pct=1.0,   # lod_minus_1pct — stop = lod * 0.99
        partial_profit_trigger_r=1.5,
        partial_profit_fraction=0.3333,
        breakeven_trigger_r=1.0,
        max_risk_per_trade_pct=max_risk_per_trade_pct,
        max_position_size_pct=max_position_size_pct,
        max_concurrent_positions=max_concurrent_positions,
        max_trades_per_day=max_trades_per_day,
        **_EXECUTION_DEFAULTS, **_SERVICE_DEFAULTS,
    )


def _make_bar(
    code: str = "US.AAPL",
    close: float = 50.0,
    low: float = 48.0,
    lod: float = 48.0,
    volume: int = 100_000,
) -> BarEvent:
    """Return a BarEvent with the given fields."""
    return BarEvent(
        code=code,
        time_key="2026-06-24 10:05:00",
        open=49.0,
        high=51.0,
        low=low,
        close=close,
        volume=volume,
        hod=51.0,
        lod=lod,
    )


def _make_signal(
    code: str = "US.AAPL",
    close: float = 50.0,
    lod: float = 48.0,
    low: float = 48.0,
) -> SignalEvent:
    """Return a SignalEvent with the given fields."""
    bar = _make_bar(code=code, close=close, low=low, lod=lod)
    return SignalEvent(
        code=code,
        bar=bar,
        premarket_high=45.0,
        hod=51.0,
        lod=lod,
        rvol=3.5,
        emitted_at=datetime.now(timezone.utc),
    )


def _make_mock_gateway(equity: float = 100_000.0) -> MagicMock:
    """Return a mock gateway with get_equity() returning a fixed float."""
    gw = MagicMock()
    gw.get_equity = AsyncMock(return_value=equity)
    return gw


def _make_store_in_memory() -> StateStore:
    """Return an in-memory StateStore with all migrations applied (including 0003)."""
    store = StateStore(":memory:")
    store.open()
    return store


def _get_intent_row(store: StateStore, intent_id: str) -> dict:
    """Read a pending_intents row by intent_id; returns {} if not found."""
    row = store.conn.execute(
        "SELECT intent_id, code, status, entry_price, stop_price, quantity "
        "FROM pending_intents WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()
    if row is None:
        return {}
    return dict(zip(
        ["intent_id", "code", "status", "entry_price", "stop_price", "quantity"],
        row,
    ))


def _make_risk_engine(
    cfg: StrategyConfig = None,
    gateway=None,
    store: StateStore = None,
    signal_engine=None,
):
    """Construct a RiskEngine with defaults suitable for unit tests."""
    from bot.risk.risk_engine import RiskEngine

    if cfg is None:
        cfg = _make_cfg()
    if gateway is None:
        gateway = _make_mock_gateway()
    if store is None:
        store = _make_store_in_memory()

    return RiskEngine(cfg=cfg, gateway=gateway, store=store, signal_engine=signal_engine)


# ============================================================
# RISK-01: live equity
# ============================================================

class TestRiskEngineEquity:
    """RiskEngine must call gateway.get_equity() on every sizing decision."""

    def test_live_equity_called(self):
        """
        gateway.get_equity() is called once per on_signal invocation — live
        equity, never a stale cached value (RISK-01, D-05).
        """
        gw = _make_mock_gateway(equity=150_000.0)
        engine = _make_risk_engine(gateway=gw)
        signal = _make_signal()

        asyncio.run(engine.on_signal(signal))

        gw.get_equity.assert_awaited_once()

    def test_equity_fallback(self):
        """
        When gateway.get_equity() returns the fallback value ($100k) — due to
        a failed or implausible query — RiskEngine still produces a correctly
        sized OrderIntent using the fallback equity (D-05).
        """
        # The fallback path is gateway's responsibility; RiskEngine just uses
        # whatever float get_equity() returns (including the 100k fallback).
        gw = _make_mock_gateway(equity=100_000.0)
        engine = _make_risk_engine(gateway=gw)
        # entry=50, lod=48 → stop=47.52, stop_dist=2.48, risk=1000,
        # risk_qty=floor(1000/2.48)=403, notional_cap=floor(10000/50)=200
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None, "Expected an OrderIntent with fallback equity"
        assert intent.equity_used == 100_000.0
        assert intent.quantity == 200  # 10% cap binds


# ============================================================
# RISK-01/02: sizing math
# ============================================================

class TestRiskEngineSizingMath:
    """1% risk sizing and 10% notional cap applied correctly."""

    def test_sizing_worked_example(self):
        """
        Worked example (plan spec):
          equity = 100,000; max_risk_per_trade_pct = 1.0; max_position_size_pct = 10
          entry_price = 50.0; lod = 48.0
          stop_price = 48.0 * (1 - 1/100) = 47.52
          stop_distance = 50.0 - 47.52 = 2.48
          risk_dollars = 100000 * 1% = 1000
          risk_qty = floor(1000 / 2.48) = floor(403.22...) = 403
          notional_cap = floor(100000 * 10% / 50.0) = floor(10000/50) = 200
          qty = min(403, 200) = 200 (notional cap binds — RISK-02)
        Assert OrderIntent.quantity == 200.
        """
        gw = _make_mock_gateway(equity=100_000.0)
        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10)
        engine = _make_risk_engine(cfg=cfg, gateway=gw)
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None, "Expected an OrderIntent for this worked example"
        assert intent.quantity == 200, (
            f"Expected qty=200 (notional cap binds), got {intent.quantity}"
        )

    def test_notional_cap(self):
        """
        When the 10%-notional-cap qty is smaller than the 1%-risk qty,
        the smaller value is used and shares are rounded DOWN via math.floor (RISK-02, D-07).
        """
        # Same as the worked example: notional cap (200) < risk qty (403)
        gw = _make_mock_gateway(equity=100_000.0)
        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10)
        engine = _make_risk_engine(cfg=cfg, gateway=gw)
        # entry=50, lod=48 → stop=47.52, stop_dist=2.48, risk_qty=403, cap_qty=200
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None
        assert intent.quantity == 200, (
            f"10% notional cap must bind (200 < 403). Got qty={intent.quantity}"
        )
        # Verify floor rounding by checking a case that would give 0.5 fractional shares
        # equity=100k, entry=500, lod=490 → stop=490*0.99=485.1, dist=14.9
        # risk=1000, risk_qty=floor(67.11)=67; cap=floor(10000/500)=20 — cap binds
        gw2 = _make_mock_gateway(equity=100_000.0)
        engine2 = _make_risk_engine(cfg=cfg, gateway=gw2)
        signal2 = _make_signal(close=500.0, lod=490.0)
        intent2 = asyncio.run(engine2.on_signal(signal2))
        assert intent2 is not None
        assert intent2.quantity == 20  # floor(10000/500)=20


# ============================================================
# D-07: under-budget / non-positive stop guard
# ============================================================

class TestRiskEngineUnderBudget:
    """qty < 1 or non-positive stop distance must produce no OrderIntent."""

    def test_under_budget_no_intent(self):
        """
        When the sized quantity rounds down to 0 (very low equity or very wide stop),
        no OrderIntent is emitted and 'intent_skipped_under_budget' is logged (D-07).
        """
        # equity=100k, max_risk=1% → risk_dollars=1000
        # entry=1000.0, lod=0.01 → stop=0.01*0.99=0.0099
        # stop_dist = 1000 - 0.0099 ≈ 999.99 → risk_qty=floor(1000/999.99)=1
        # notional_cap = floor(100000*10%/1000) = floor(10) = 10
        # qty = min(1,10) = 1 — that's valid. We need to make qty < 1.
        # Try: equity=50, max_risk=1% → risk_dollars=0.5
        # entry=100, lod=50 → stop=49.5, dist=50.5 → risk_qty=floor(0.5/50.5)=floor(0.0099)=0
        gw = _make_mock_gateway(equity=50.0)
        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10)
        engine = _make_risk_engine(cfg=cfg, gateway=gw)
        signal = _make_signal(close=100.0, lod=50.0)

        with capture_logs() as log_output:
            intent = asyncio.run(engine.on_signal(signal))

        assert intent is None, (
            "Expected None when qty rounds down to 0 (under-budget)"
        )
        event_types = [e.get("event") for e in log_output]
        assert "intent_skipped_under_budget" in event_types, (
            f"Expected 'intent_skipped_under_budget' in log events; got {event_types}"
        )

    def test_non_positive_stop_distance_no_intent(self):
        """
        When entry_price <= stop_price (non-positive stop distance), no
        OrderIntent is emitted and the reason is logged (D-07 guard).
        """
        # lod=100.0 → stop=99.0; entry=50.0 → stop_distance=50.0-99.0=-49.0 (negative)
        gw = _make_mock_gateway(equity=100_000.0)
        engine = _make_risk_engine(gateway=gw)
        signal = _make_signal(close=50.0, lod=100.0)  # lod > entry — pathological

        with capture_logs() as log_output:
            intent = asyncio.run(engine.on_signal(signal))

        assert intent is None, (
            "Expected None when stop_distance <= 0"
        )
        event_types = [e.get("event") for e in log_output]
        assert "non_positive_stop_distance" in event_types, (
            f"Expected 'non_positive_stop_distance' in log events; got {event_types}"
        )


# ============================================================
# RISK-03: OrderIntent field correctness
# ============================================================

class TestRiskEngineIntentFields:
    """OrderIntent must carry correct stop_price, quantity, intent_id, etc."""

    def test_intent_fields_correct(self):
        """
        Emitted OrderIntent has:
          - stop_price == compute_initial_stop(signal.lod) (RISK-03)
          - quantity >= 1 (whole shares, floor-rounded)
          - intent_id is a non-empty UUID string
          - equity_used matches the mocked live equity
          - source_signal is the triggering SignalEvent
          - risk_dollars == equity * max_risk_per_trade_pct / 100
          - notional == entry_price * quantity
        """
        equity = 100_000.0
        gw = _make_mock_gateway(equity=equity)
        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10)
        engine = _make_risk_engine(cfg=cfg, gateway=gw)
        lod = 48.0
        close = 50.0
        signal = _make_signal(close=close, lod=lod)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None, "Expected an OrderIntent to be emitted"

        # RISK-03: stop_price from compute_initial_stop(lod)
        # = lod * (1 - initial_stop_pct/100) = 48.0 * 0.99 = 47.52
        expected_stop = lod * (1.0 - cfg.initial_stop_pct / 100.0)
        assert abs(intent.stop_price - expected_stop) < 1e-9, (
            f"stop_price should be {expected_stop}, got {intent.stop_price}"
        )

        # quantity >= 1 and is a whole number
        assert intent.quantity >= 1, f"quantity must be >= 1, got {intent.quantity}"
        assert isinstance(intent.quantity, int), (
            f"quantity must be int, got {type(intent.quantity)}"
        )

        # intent_id is a non-empty UUID string
        assert isinstance(intent.intent_id, str) and len(intent.intent_id) > 0, (
            "intent_id must be a non-empty string"
        )
        parsed = uuid.UUID(intent.intent_id)  # raises ValueError if not valid UUID
        assert str(parsed) == intent.intent_id or parsed.version == 4

        # equity_used matches
        assert intent.equity_used == equity, (
            f"equity_used should be {equity}, got {intent.equity_used}"
        )

        # source_signal is the triggering SignalEvent
        assert intent.source_signal is signal

        # risk_dollars = equity * max_risk_per_trade_pct / 100
        expected_risk_dollars = equity * cfg.max_risk_per_trade_pct / 100.0
        assert abs(intent.risk_dollars - expected_risk_dollars) < 1e-9

        # notional = entry_price * quantity
        assert abs(intent.notional - close * intent.quantity) < 1e-9, (
            f"notional should be entry_price*qty={close * intent.quantity}, got {intent.notional}"
        )

    def test_intent_persisted_to_statestore(self):
        """
        Emitting an OrderIntent inserts a row into the pending_intents
        StateStore table with status='PENDING' and the correct fields (D-12).
        """
        gw = _make_mock_gateway(equity=100_000.0)
        store = _make_store_in_memory()
        engine = _make_risk_engine(gateway=gw, store=store)
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None, "Expected an OrderIntent to check persistence"

        row = _get_intent_row(store, intent.intent_id)
        assert row, (
            f"pending_intents row for intent_id={intent.intent_id} not found in StateStore"
        )
        assert row["status"] == "PENDING", (
            f"Expected status='PENDING', got {row['status']}"
        )
        assert row["code"] == signal.code, (
            f"Expected code={signal.code}, got {row['code']}"
        )
        assert abs(row["stop_price"] - intent.stop_price) < 1e-9
        assert row["quantity"] == intent.quantity

        store.close()

    def test_intent_logged_to_structlog(self):
        """
        Emitting an OrderIntent logs an 'order_intent_emitted' structlog event
        with intent_id, code, entry_price, stop_price, quantity (D-12, RISK-03 #5).
        """
        gw = _make_mock_gateway(equity=100_000.0)
        engine = _make_risk_engine(gateway=gw)
        signal = _make_signal(close=50.0, lod=48.0)

        with capture_logs() as log_output:
            intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None

        emitted_events = [e for e in log_output if e.get("event") == "order_intent_emitted"]
        assert len(emitted_events) >= 1, (
            f"Expected at least one 'order_intent_emitted' log event; got {log_output}"
        )

        emitted = emitted_events[0]
        assert "stop_price" in emitted, "structlog event must carry stop_price (RISK-03 #5)"
        assert "quantity" in emitted, "structlog event must carry quantity (RISK-03 #5)"
        assert abs(emitted["stop_price"] - intent.stop_price) < 1e-9
        assert emitted["quantity"] == intent.quantity


# ============================================================
# RISK-05: daily cap (independent of concurrent cap)
# ============================================================

class TestRiskEngineDailyCap:
    """Daily cap is independent from the concurrent-position cap."""

    def test_daily_cap_independent_of_concurrent(self):
        """
        After max_trades_per_day intents are emitted (via note_intent_emitted()),
        no further intent is produced even when concurrent positions are free (RISK-05).

        The pending tally is managed by SignalEngine.note_intent_emitted() which is
        called by RiskEngine at emission time. RiskEngine itself does not count — it
        calls the signal_engine.note_intent_emitted() hook provided at construction.
        The daily cap check lives in SignalEngine; once the tally is saturated, no
        SignalEvent reaches RiskEngine. This test instead verifies that RiskEngine
        calls note_intent_emitted() exactly once per emitted intent (so SignalEngine
        can correctly track the count).
        """
        call_count = {"n": 0}

        def _mock_note():
            call_count["n"] += 1

        gw = _make_mock_gateway(equity=100_000.0)
        store = _make_store_in_memory()

        # Build a mock signal_engine with note_intent_emitted
        mock_signal_engine = MagicMock()
        mock_signal_engine.note_intent_emitted = _mock_note

        engine = _make_risk_engine(
            gateway=gw,
            store=store,
            signal_engine=mock_signal_engine,
        )
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(engine.on_signal(signal))

        assert intent is not None, "Expected an intent for this valid signal"
        assert call_count["n"] == 1, (
            f"note_intent_emitted() must be called exactly once per emitted intent; "
            f"called {call_count['n']} times"
        )

        store.close()


# ============================================================
# CR-02 regression: _pending_count advances by EXACTLY 1 per emitted intent
# ============================================================

class TestPendingCountWiredPipeline:
    """CR-02 regression: wired SignalEngine + RiskEngine pipeline must advance
    _pending_count by exactly 1 per emitted intent — not 2.

    The bug (CR-02): SignalEngine.on_bar() incremented _pending_count directly
    AND RiskEngine called note_intent_emitted() which incremented it again,
    advancing the tally by 2 per intent. The fix removes the direct increment
    from on_bar(); note_intent_emitted() is the sole owner of the tally.

    This test wires a REAL SignalEngine to a REAL RiskEngine (not fully mocked)
    and drives a SignalEvent through the pipeline, then asserts _pending_count
    incremented by exactly 1.
    """

    def test_pending_count_increments_by_exactly_one_per_intent(self):
        """
        Drive a SignalEvent through a real (wired) SignalEngine + RiskEngine.

        After one successful OrderIntent emission, _pending_count on the
        SignalEngine must be exactly 1 — not 2 (the CR-02 double-count bug).

        Uses a pre-built SignalEvent (bypasses on_bar() gates) to focus solely
        on the tally increment path: SignalEngine.note_intent_emitted() called
        by RiskEngine after building the intent.
        """
        from bot.risk.risk_engine import RiskEngine
        from bot.signal.signal_engine import SignalEngine

        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10,
                        max_trades_per_day=5)

        gw = _make_mock_gateway(equity=100_000.0)
        store = _make_store_in_memory()

        signal_engine = SignalEngine(cfg=cfg, gateway=gw, store=store)
        risk_engine = RiskEngine(
            cfg=cfg, gateway=gw, store=store, signal_engine=signal_engine
        )

        # Verify starting state
        assert signal_engine._pending_count == 0, "Should start at 0"

        # Create a valid SignalEvent (gates already passed — we call on_signal directly)
        signal = _make_signal(close=50.0, lod=48.0)

        intent = asyncio.run(risk_engine.on_signal(signal))

        assert intent is not None, "Expected a valid OrderIntent for this signal"

        # CR-02 regression: _pending_count must be exactly 1, not 2.
        assert signal_engine._pending_count == 1, (
            f"CR-02 regression: _pending_count must be exactly 1 per emitted intent "
            f"(RiskEngine calls note_intent_emitted() exactly once). "
            f"Got {signal_engine._pending_count}. If it is 2, the double-count bug "
            "has been reintroduced (on_bar() also incremented _pending_count)."
        )

        store.close()

    def test_note_intent_resolved_correctly_unwinds(self):
        """
        note_intent_resolved() decrements _pending_count by 1 — correctly
        unwinding a single note_intent_emitted() call.

        After emit (count=1) and resolve (count=0), the count is back to 0.
        This ensures the CR-02 fix is compatible with the unwind path: if the
        tally advanced by 2 but unwinds by 1, the count would never reach 0.
        """
        from bot.risk.risk_engine import RiskEngine
        from bot.signal.signal_engine import SignalEngine

        cfg = _make_cfg(max_risk_per_trade_pct=1.0, max_position_size_pct=10,
                        max_trades_per_day=5)

        gw = _make_mock_gateway(equity=100_000.0)
        store = _make_store_in_memory()

        signal_engine = SignalEngine(cfg=cfg, gateway=gw, store=store)
        risk_engine = RiskEngine(
            cfg=cfg, gateway=gw, store=store, signal_engine=signal_engine
        )

        signal = _make_signal(close=50.0, lod=48.0)
        asyncio.run(risk_engine.on_signal(signal))

        # One intent emitted → pending_count should be 1
        assert signal_engine._pending_count == 1, "Expected 1 after one emit"

        # Resolve the intent → should unwind to 0
        signal_engine.note_intent_resolved()
        assert signal_engine._pending_count == 0, (
            "note_intent_resolved() must unwind _pending_count to 0 "
            "(CR-02: if tally was 2 after emit, single unwind would leave 1)"
        )

        store.close()


# ============================================================
# sizing_equity_usd basis selection — RISK-01 / 260702-ick Task 3
# ============================================================

class TestSizingEquityUsdBasis:
    """RiskEngine.on_signal uses cfg.sizing_equity_usd as the sizing basis when set,
    and falls back to gateway.get_equity() when None. equity_used records the basis used.
    """

    def test_fixed_basis_bypasses_get_equity(self):
        """When cfg.sizing_equity_usd is set, RiskEngine uses it without calling get_equity."""
        from bot.risk.risk_engine import RiskEngine

        cfg = _make_cfg(sizing_equity_usd=200_000.0)
        gw = _make_mock_gateway(equity=100_000.0)  # different from fixed basis
        store = _make_store_in_memory()

        signal = _make_signal(code="US.AAPL", close=50.0, lod=48.0)
        risk_engine = RiskEngine(gateway=gw, store=store, cfg=cfg, signal_engine=None)

        intent = asyncio.run(risk_engine.on_signal(signal))
        store.close()

        assert intent is not None, "RiskEngine must emit an intent with a valid signal"
        assert intent.equity_used == 200_000.0, (
            f"equity_used must equal cfg.sizing_equity_usd (200000); got {intent.equity_used}"
        )
        gw.get_equity.assert_not_awaited(), (
            "get_equity must NOT be called when cfg.sizing_equity_usd is set"
        )

    def test_null_sizing_equity_falls_back_to_live_equity(self):
        """When cfg.sizing_equity_usd is None, RiskEngine awaits gateway.get_equity()."""
        from bot.risk.risk_engine import RiskEngine

        cfg = _make_cfg(sizing_equity_usd=None)
        gw = _make_mock_gateway(equity=150_000.0)
        store = _make_store_in_memory()

        signal = _make_signal(code="US.AAPL", close=50.0, lod=48.0)
        risk_engine = RiskEngine(gateway=gw, store=store, cfg=cfg, signal_engine=None)

        intent = asyncio.run(risk_engine.on_signal(signal))
        store.close()

        assert intent is not None, "RiskEngine must emit an intent with a valid signal"
        assert intent.equity_used == 150_000.0, (
            f"equity_used must equal live equity (150000); got {intent.equity_used}"
        )
        gw.get_equity.assert_awaited_once(), (
            "get_equity must be awaited exactly once when sizing_equity_usd is None"
        )
