#!/usr/bin/env python3
"""
tests.execution.test_engine — Unit tests for ExecutionEngine (Phase 4, EXEC-01..05).

Implements the 04-03 test targets:
  - test_entry_placed_simulate_unit: EXEC-01 mock-gateway sampling path
  - test_no_market_orders: EXEC-02 no market order in code or behavior
  - test_ttl_cancel_replace: EXEC-03 cancel-replace on TTL; abandon after max_retries
  - test_fill_by_order_id: EXEC-05 fill matched by order_id; partial exit != stop-out

Still-stub (implemented in 04-04):
  - test_entry_placed_simulate: live SIMULATE verification (manual; 04-VALIDATION)
  - test_duplicate_guard: EXEC-04 broker duplicate-entry guard
"""
import asyncio
import pathlib
import pytest
from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call


# ============================================================
# Helpers — lightweight StrategyConfig-like objects for tests
# ============================================================

@dataclass
class _MockCfg:
    """Minimal config object matching StrategyConfig execution fields (CFG-01)."""
    trd_env: str = "SIMULATE"
    entry_limit_buffer_usd: float = 0.05
    entry_ttl_seconds: float = 0.05       # very short for fast tests
    entry_max_retries: int = 2
    entry_poll_interval_seconds: float = 0.01
    exit_limit_buffer_usd: float = 0.05
    exit_ttl_seconds: float = 0.05
    exit_escalation_step_usd: float = 0.10
    exit_escalation_cadence_seconds: float = 0.01
    force_close_escalation_step_usd: float = 0.20
    force_close_escalation_cadence_seconds: float = 0.01


@dataclass
class _MockIntent:
    """Minimal OrderIntent-like object for tests."""
    code: str = "US.AAPL"
    entry_price: float = 182.00
    stop_price: float = 180.18
    quantity: int = 100
    equity_used: float = 100_000.0
    risk_dollars: float = 182.0
    notional: float = 18_200.0
    emitted_at: datetime = field(default_factory=datetime.now)
    intent_id: str = "test-intent-001"


def _make_mock_store():
    """Build a MagicMock StateStore with pending_intents resolve support."""
    store = MagicMock()
    store.conn = MagicMock()
    store.conn.execute = MagicMock()
    store.conn.commit = MagicMock()
    return store


def _run(coro):
    """Run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


# ============================================================
# test_entry_placed_simulate — manual-only live test (pytest.skip)
# ============================================================

@pytest.mark.skip(
    reason="Manual-only live SIMULATE verification — requires running OpenD + "
    "logged-in paper account. Automated coverage: test_entry_placed_simulate_unit. "
    "See 04-VALIDATION.md Manual-Only Verifications."
)
def test_entry_placed_simulate():
    """EXEC-01 (live SIMULATE): Entry order placed in SIMULATE env; FillEvent emitted.

    Manual-only — see 04-VALIDATION Manual-Only Verifications. The automated
    sampling path is test_entry_placed_simulate_unit below. This live integration
    test requires a running OpenD GUI + a logged-in paper (SIMULATE) account; it is
    skipped in the automated suite and run manually per 04-VALIDATION.md.
    """
    ...  # manual procedure documented in 04-VALIDATION.md Manual-Only Verifications


# ============================================================
# test_entry_placed_simulate_unit — EXEC-01 automated mock-gateway path
# ============================================================

def test_entry_placed_simulate_unit():
    """EXEC-01 (automated mock-gateway): consume_intent places entry under SIMULATE.

    Drives _manage_entry_order with a mock gateway whose place_order returns an
    order_id and whose get_order_fills returns one matching fill. Asserts:
      (i) gateway.place_order called once with code="US.AAPL"
          and that the engine never submits a MARKET order type
      (ii) cfg.trd_env == "SIMULATE" (no live broker)
      (iii) FillEvent(is_entry=True) emitted with the matched order_id
    """
    from bot.execution.engine import ExecutionEngine
    from bot.execution.events import FillEvent

    cfg = _MockCfg()
    assert cfg.trd_env == "SIMULATE"   # (ii) SIMULATE env

    gw = MagicMock()
    gw.place_order = AsyncMock(return_value="ORDER-101")
    gw.get_ask_price = AsyncMock(return_value=182.55)
    gw.get_order_fills = AsyncMock(return_value=[
        {"order_id": "ORDER-101", "code": "US.AAPL", "qty": 100, "price": 182.60},
    ])
    gw.cancel_order = AsyncMock()

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    intent = _MockIntent()
    fill_event = _run(engine._manage_entry_order(intent))

    # (iii) FillEvent(is_entry=True) with correct order_id
    assert fill_event is not None
    assert isinstance(fill_event, FillEvent)
    assert fill_event.is_entry is True
    assert fill_event.order_id == "ORDER-101"
    assert fill_event.code == "US.AAPL"
    assert fill_event.filled_qty == 100

    # (i) place_order called once; engine passes code correctly
    gw.place_order.assert_awaited_once()
    call_args = gw.place_order.call_args
    placed_code = call_args.args[0] if call_args.args else call_args.kwargs.get("code")
    assert placed_code == "US.AAPL"


# ============================================================
# test_no_market_orders — EXEC-02
# ============================================================

def test_no_market_orders():
    """EXEC-02: No market order ever placed; engine.py must never contain OrderType.MARKET.

    Two-part test:
      (a) grep gate: 'OrderType.MARKET' not in engine.py source
      (b) behavioral: engine calls gateway.place_order; verify
          the engine itself never passes an order_type argument that could be MARKET
    """
    # (a) grep gate — EXEC-02 source-level check
    engine_path = pathlib.Path(__file__).parents[2] / "bot" / "execution" / "engine.py"
    engine_src = engine_path.read_text()
    assert "OrderType.MARKET" not in engine_src, (
        "EXEC-02 violation: 'OrderType.MARKET' found in bot/execution/engine.py"
    )

    # (b) behavioral: engine delegates order type to gateway.place_order,
    #     which is the single point that enforces NORMAL; engine never passes an
    #     explicit order_type parameter itself
    from bot.execution.engine import ExecutionEngine

    cfg = _MockCfg()
    gw = MagicMock()
    gw.place_order = AsyncMock(return_value="ORDER-NOMARKET")
    gw.get_ask_price = AsyncMock(return_value=150.00)
    gw.get_order_fills = AsyncMock(return_value=[
        {"order_id": "ORDER-NOMARKET", "code": "US.TSLA", "qty": 50, "price": 150.05},
    ])
    gw.cancel_order = AsyncMock()

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    intent = _MockIntent(code="US.TSLA", quantity=50)
    _run(engine._manage_entry_order(intent))

    # Verify gateway.place_order was called and NOT with any "MARKET" string in args
    gw.place_order.assert_awaited()
    call_args = gw.place_order.call_args
    all_args = list(call_args.args) + list(call_args.kwargs.values())
    for arg in all_args:
        assert "MARKET" not in str(arg), (
            f"EXEC-02 violation: 'MARKET' found in place_order argument: {arg}"
        )


# ============================================================
# test_ttl_cancel_replace — EXEC-03 / D-05
# ============================================================

def test_ttl_cancel_replace():
    """EXEC-03: TTL cancel-replace fires on no-fill; abandon after max_retries (D-05).

    Three scenarios:
      1. First TTL returns no fill, re-place fires, second attempt fills.
      2. All TTLs expire with no fill: abandon after entry_max_retries, return None,
         pending_intents row updated to EXPIRED (D-05).
      3. Config-swap: entry_max_retries=1 places fewer orders than entry_max_retries=2
         (CFG-01 behavioral proof).
    """
    from bot.execution.engine import ExecutionEngine
    from bot.execution.events import FillEvent

    # ----------------------------------------------------------------
    # Scenario 1: one TTL miss then a fill on the re-placed order
    # ----------------------------------------------------------------
    # Use a stateful list to track placed order_ids; fill only after second place
    cfg = _MockCfg(entry_max_retries=2)

    placed_orders_s1 = []

    async def mock_place_s1(code, qty, price, side):
        oid = f"S1-ORDER-{len(placed_orders_s1) + 1}"
        placed_orders_s1.append(oid)
        return oid

    async def mock_fills_s1():
        # No fill while only the first order has been placed;
        # return fill once the second order is placed (i.e., after first TTL + re-place)
        if len(placed_orders_s1) >= 2:
            return [
                {"order_id": placed_orders_s1[1], "code": "US.AAPL",
                 "qty": 100, "price": 182.60}
            ]
        return []

    gw1 = MagicMock()
    gw1.get_ask_price = AsyncMock(return_value=182.55)
    gw1.get_order_fills = mock_fills_s1
    gw1.place_order = mock_place_s1
    gw1.cancel_order = AsyncMock()

    store1 = _make_mock_store()
    engine1 = ExecutionEngine(gateway=gw1, store=store1, cfg=cfg)

    fill_event = _run(engine1._manage_entry_order(_MockIntent()))

    assert fill_event is not None, "Scenario 1: expected FillEvent after re-place"
    assert isinstance(fill_event, FillEvent)
    assert fill_event.order_id == placed_orders_s1[1], (
        "Scenario 1: fill must be for the re-placed order (second place)"
    )
    gw1.cancel_order.assert_awaited()            # TTL-expired first order was cancelled
    assert len(placed_orders_s1) == 2            # exactly one TTL miss then re-place

    # ----------------------------------------------------------------
    # Scenario 2: all TTLs expire, abandon after entry_max_retries (D-05)
    # ----------------------------------------------------------------
    gw2 = MagicMock()
    gw2.get_ask_price = AsyncMock(return_value=182.55)
    gw2.get_order_fills = AsyncMock(return_value=[])   # never fills
    # Provide enough order_id values for all retry iterations
    gw2.place_order = AsyncMock(side_effect=[
        "S2-O-1", "S2-O-2", "S2-O-3", "S2-O-4", "S2-O-5",
    ])
    gw2.cancel_order = AsyncMock()

    store2 = _make_mock_store()
    engine2 = ExecutionEngine(gateway=gw2, store=store2, cfg=cfg)

    result = _run(engine2._manage_entry_order(_MockIntent(intent_id="test-intent-002")))

    assert result is None, "Scenario 2: expected None (abandoned) after max_retries"
    assert gw2.cancel_order.await_count > 0, "All attempts must be cancelled on abandon"

    # D-05: store must have been told about the EXPIRED status via guarded method (CR-01 / 06.1-09)
    store2.expire_pending_intent.assert_called()

    # ----------------------------------------------------------------
    # Scenario 3: config-swap proves entry_max_retries controls retry count (CFG-01)
    # ----------------------------------------------------------------
    cfg3 = _MockCfg(entry_max_retries=1)   # fewer retries than cfg (max_retries=2)
    placed_orders_s3 = []

    async def mock_place_s3(code, qty, price, side):
        oid = f"S3-ORDER-{len(placed_orders_s3) + 1}"
        placed_orders_s3.append(oid)
        return oid

    gw3 = MagicMock()
    gw3.get_ask_price = AsyncMock(return_value=182.55)
    gw3.get_order_fills = AsyncMock(return_value=[])
    gw3.place_order = mock_place_s3
    gw3.cancel_order = AsyncMock()

    store3 = _make_mock_store()
    engine3 = ExecutionEngine(gateway=gw3, store=store3, cfg=cfg3)

    _run(engine3._manage_entry_order(_MockIntent(intent_id="test-intent-003")))

    # max_retries=1 → initial + 1 retry = 2 total places
    assert len(placed_orders_s3) <= 2, (
        f"CFG-01 violation: entry_max_retries=1 should result in ≤2 place_order calls, "
        f"got {len(placed_orders_s3)}"
    )

    # Scenario 2 (max_retries=2) must result in more place calls than scenario 3 (max_retries=1)
    assert gw2.place_order.await_count > len(placed_orders_s3), (
        "CFG-01 config-swap: entry_max_retries=2 must produce more place calls than max_retries=1"
    )


# ============================================================
# test_duplicate_guard — EXEC-04 (implemented in 04-04)
# ============================================================

def test_duplicate_guard():
    """EXEC-04: Broker-verified duplicate guard blocks second entry for same code.

    Two paths tested:
      (a) get_positions() returns an open position for the code → consume_intent
          blocks before place_order and returns None (EXEC-04 / D-09).
      (b) get_positions() returns empty but get_order_status() has an open BUY
          order for the code → consume_intent blocks (Pitfall F).

    The guard MUST run before place_order — verified by asserting place_order is
    never called when the guard fires. The check is broker-verified (refresh_cache)
    not in-memory only.
    """
    from bot.execution.engine import ExecutionEngine
    import pandas as pd

    cfg = _MockCfg()
    intent = _MockIntent(code="US.AAPL")

    # ---- Path (a): open position at broker → block ----
    # Build a minimal broker positions DataFrame for the code
    positions_df = pd.DataFrame([{
        "code": "US.AAPL",
        "qty": 100,
        "average_cost": 182.0,
        "position_side": "LONG",
    }])

    gw_a = MagicMock()
    gw_a.get_positions = AsyncMock(return_value=(0, positions_df))  # RET_OK=0
    gw_a.get_order_status = AsyncMock(return_value=[])
    gw_a.place_order = AsyncMock(return_value="ORDER-SHOULD-NOT-PLACE")
    gw_a.get_ask_price = AsyncMock(return_value=182.55)
    gw_a.get_order_fills = AsyncMock(return_value=[])
    gw_a.cancel_order = AsyncMock()

    store_a = _make_mock_store()
    engine_a = ExecutionEngine(gateway=gw_a, store=store_a, cfg=cfg)

    result_a = _run(engine_a.consume_intent(intent))

    # consume_intent must return None (blocked) and must NOT call place_order
    assert result_a is None, (
        "EXEC-04: consume_intent must return None when open broker position exists"
    )
    gw_a.place_order.assert_not_awaited(), (
        "EXEC-04: place_order must NOT be called when duplicate guard fires"
    )
    # get_positions must have been called (broker-verified check)
    gw_a.get_positions.assert_awaited()

    # ---- Path (b): open BUY order at broker (Pitfall F) → block ----
    # Empty positions but open BUY order for the code
    empty_positions_df = pd.DataFrame([])  # no open positions

    open_buy_orders = [{
        "order_id": "ORDER-EXISTING-BUY",
        "code": "US.AAPL",
        "order_status": "WAITING_SUBMIT",  # not a terminal status
        "qty": 100,
        "dealt_qty": 0,
        "dealt_avg_price": 0.0,
        "trd_side": "BUY",
    }]

    gw_b = MagicMock()
    gw_b.get_positions = AsyncMock(return_value=(0, empty_positions_df))
    gw_b.get_order_status = AsyncMock(return_value=open_buy_orders)
    gw_b.place_order = AsyncMock(return_value="ORDER-SHOULD-NOT-PLACE-B")
    gw_b.get_ask_price = AsyncMock(return_value=182.55)
    gw_b.get_order_fills = AsyncMock(return_value=[])
    gw_b.cancel_order = AsyncMock()

    store_b = _make_mock_store()
    engine_b = ExecutionEngine(gateway=gw_b, store=store_b, cfg=cfg)

    result_b = _run(engine_b.consume_intent(intent))

    # consume_intent must return None (blocked by open BUY order)
    assert result_b is None, (
        "Pitfall F: consume_intent must return None when an open BUY order exists"
    )
    gw_b.place_order.assert_not_awaited(), (
        "Pitfall F: place_order must NOT be called when an open BUY order exists"
    )
    # Both get_positions and get_order_status must have been called
    gw_b.get_positions.assert_awaited()
    gw_b.get_order_status.assert_awaited()

    # ---- Path (c): no duplicate → entry proceeds normally ----
    # Empty positions AND no open orders → guard passes, entry proceeds
    gw_c = MagicMock()
    gw_c.get_positions = AsyncMock(return_value=(0, empty_positions_df))
    gw_c.get_order_status = AsyncMock(return_value=[])
    gw_c.get_ask_price = AsyncMock(return_value=182.55)
    gw_c.get_order_fills = AsyncMock(return_value=[
        {"order_id": "ORDER-NEW", "code": "US.AAPL", "qty": 100, "price": 182.60},
    ])
    gw_c.place_order = AsyncMock(return_value="ORDER-NEW")
    gw_c.cancel_order = AsyncMock()

    store_c = _make_mock_store()
    engine_c = ExecutionEngine(gateway=gw_c, store=store_c, cfg=cfg)

    from bot.execution.events import FillEvent
    result_c = _run(engine_c.consume_intent(intent))

    # No duplicate → entry proceeds → FillEvent returned
    assert result_c is not None, (
        "EXEC-04: when no duplicate exists, consume_intent must proceed with entry"
    )
    assert isinstance(result_c, FillEvent)
    gw_c.place_order.assert_awaited()


# ============================================================
# test_fill_by_order_id — EXEC-05
# ============================================================

def test_fill_by_order_id():
    """EXEC-05: Fill matched by order_id only; partial exit != stop-out.

    Synthetic deal rows: one row matches entry order_id, one shares the code
    but has a different order_id (an unrelated exit fill). Asserts:
      - Only the matching order_id row is counted for the entry fill
      - A 100-share fill against a 300-share entry is not treated as full fill
        (partial fill is accepted as the position — D-06)
      - Fill reconciliation by order_id (never by code or quantity)
    """
    from bot.execution.engine import ExecutionEngine
    from bot.execution.events import FillEvent

    cfg = _MockCfg()
    gw = MagicMock()
    gw.get_ask_price = AsyncMock(return_value=182.55)

    ENTRY_ORDER_ID = "ENTRY-ORDER-999"
    OTHER_ORDER_ID = "EXIT-ORDER-111"   # same code, different order_id

    # Synthetic fill table: matching row + unrelated row for same code
    gw.get_order_fills = AsyncMock(return_value=[
        # This is the entry fill (matching order_id)
        {"order_id": ENTRY_ORDER_ID, "code": "US.AAPL", "qty": 100, "price": 182.60},
        # This is an unrelated exit fill (different order_id) — must NOT be counted
        {"order_id": OTHER_ORDER_ID, "code": "US.AAPL", "qty": 200, "price": 183.00},
    ])
    gw.place_order = AsyncMock(return_value=ENTRY_ORDER_ID)
    gw.cancel_order = AsyncMock()

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    # Entry for 300 shares; only 100 actually fill (partial fill, D-06)
    intent = _MockIntent(quantity=300)
    fill_event = _run(engine._manage_entry_order(intent))

    # FillEvent must only account for the matched order_id row (100 shares)
    assert fill_event is not None
    assert isinstance(fill_event, FillEvent)
    assert fill_event.order_id == ENTRY_ORDER_ID, (
        "EXEC-05: fill must be keyed by order_id, not code"
    )
    assert fill_event.filled_qty == 100, (
        "EXEC-05: only matching order_id fill row counted (100 shares, not 300)"
    )
    # D-06: partial fill (100/300) is accepted as the position, not rejected
    assert fill_event.is_entry is True

    # Verify the OTHER_ORDER_ID fill (200 shares) was NOT included
    assert fill_event.filled_qty != 300, (
        "EXEC-05: unrelated order_id fill must not be conflated with entry fill"
    )
