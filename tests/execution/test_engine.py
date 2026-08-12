#!/usr/bin/env python3
"""
tests.execution.test_engine — Unit tests for ExecutionEngine (Phase 4, EXEC-01..05).

Implements the 04-03 test targets:
  - test_entry_placed_simulate_unit: EXEC-01 mock-gateway sampling path
  - test_no_market_orders: EXEC-02 no market order in code or behavior
  - test_ttl_cancel_replace: EXEC-03 cancel-replace on TTL; abandon after max_retries
  - test_fill_by_order_id: EXEC-05 fill matched by order_id; partial exit != stop-out

Paper-fill regression tests (paper-deal-list-unsupported fix):
  - test_paper_fill_entry_no_deal_list_query: entry fill loop uses order_list_query,
    never deal_list_query — GatewayError from deal_list_query must not surface
  - test_paper_fill_exit_no_deal_list_query: manage_exit fill loop uses order_list_query —
    GatewayError from deal_list_query must not surface; correct total_filled / remaining
  - test_paper_fill_exit_partial_then_full: manage_exit partial-then-full fill across two
    outer iterations (different order_ids) — no double-count, EXEC-05 / CR-02 invariants

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
    order_id and whose get_order_status returns one matching order row with dealt_qty
    (order_list_query path — deal_list_query is unsupported on SIMULATE paper accounts).
    Asserts:
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
    # get_order_status returns cumulative dealt_qty per order (order_list_query path).
    # deal_list_query (get_order_fills) is unsupported on SIMULATE paper accounts.
    gw.get_order_status = AsyncMock(return_value=[
        {
            "order_id": "ORDER-101", "code": "US.AAPL",
            "order_status": "FILLED_ALL", "qty": 100,
            "dealt_qty": 100, "dealt_avg_price": 182.60, "trd_side": "BUY",
        },
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
    assert abs(fill_event.avg_fill_price - 182.60) < 0.001

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
    gw.get_order_status = AsyncMock(return_value=[
        {
            "order_id": "ORDER-NOMARKET", "code": "US.TSLA",
            "order_status": "FILLED_ALL", "qty": 50,
            "dealt_qty": 50, "dealt_avg_price": 150.05, "trd_side": "BUY",
        },
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

    async def mock_order_status_s1(order_id=""):
        # No fill while only the first order has been placed;
        # return dealt_qty once the second order is placed (after first TTL + re-place).
        # get_order_status returns cumulative dealt_qty per order (order_list_query path).
        if len(placed_orders_s1) >= 2 and order_id == placed_orders_s1[1]:
            return [
                {
                    "order_id": placed_orders_s1[1], "code": "US.AAPL",
                    "order_status": "FILLED_ALL", "qty": 100,
                    "dealt_qty": 100, "dealt_avg_price": 182.60, "trd_side": "BUY",
                }
            ]
        return []

    gw1 = MagicMock()
    gw1.get_ask_price = AsyncMock(return_value=182.55)
    gw1.get_order_status = mock_order_status_s1
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
    gw2.get_order_status = AsyncMock(return_value=[])   # never shows a fill (dealt_qty always 0)
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
    gw3.get_order_status = AsyncMock(return_value=[])   # never shows a fill
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
    # Empty positions AND no open orders → guard passes, entry proceeds.
    # get_order_status is called twice: once for the duplicate guard (returns [])
    # and once for fill polling (returns the filled order row).
    gw_c = MagicMock()
    gw_c.get_positions = AsyncMock(return_value=(0, empty_positions_df))
    gw_c.get_order_status = AsyncMock(side_effect=[
        [],   # first call: duplicate guard check → no open orders
        [     # second call: fill poll → order filled
            {
                "order_id": "ORDER-NEW", "code": "US.AAPL",
                "order_status": "FILLED_ALL", "qty": 100,
                "dealt_qty": 100, "dealt_avg_price": 182.60, "trd_side": "BUY",
            }
        ],
    ])
    gw_c.get_ask_price = AsyncMock(return_value=182.55)
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
# test_exec04_blocks_entry_for_manual_holding — EXEC-04 regression (260702-ick Task 1)
# ============================================================

def test_exec04_blocks_entry_for_manual_holding():
    """EXEC-04 regression: entry backstop blocks entry on manually held code.

    Documents the entry-time backstop that layers with the scan-time exclusion
    introduced in 260702-ick (spec Testing section). The EXEC-04 duplicate guard
    in consume_intent fires when get_positions reports the intent's code is already
    held at the broker — regardless of whether the bot owns it via DB record.

    This is a regression test: it documents existing behavior in ExecutionEngine
    with NO change to bot/execution/engine.py. The scan-time exclusion (Task 2)
    is the primary defence; EXEC-04 is the entry-time backstop.
    """
    from bot.execution.engine import ExecutionEngine
    import pandas as pd

    cfg = _MockCfg()
    intent = _MockIntent(code="US.AAPL")

    # Broker reports the code as held (manual holding — not bot-owned)
    positions_df = pd.DataFrame([{
        "code": "US.AAPL",
        "qty": 100,
        "average_cost": 182.0,
        "position_side": "LONG",
    }])

    gw = MagicMock()
    gw.get_positions = AsyncMock(return_value=(0, positions_df))
    gw.get_order_status = AsyncMock(return_value=[])
    gw.place_order = AsyncMock(return_value="ORDER-SHOULD-NOT-PLACE")
    gw.get_ask_price = AsyncMock(return_value=182.55)
    gw.cancel_order = AsyncMock()

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    result = _run(engine.consume_intent(intent))

    # consume_intent must return None — the EXEC-04 duplicate guard fired
    assert result is None, (
        "EXEC-04: consume_intent must return None when broker reports the code "
        "as already held (manual holding blocks entry)"
    )
    # place_order must never be called — the guard blocked before order placement
    gw.place_order.assert_not_awaited(), (
        "EXEC-04: place_order must NOT be called when the duplicate guard fires "
        "on a manually held position"
    )
    # get_positions must have been called (broker-verified check)
    gw.get_positions.assert_awaited()


# ============================================================
# test_fill_by_order_id — EXEC-05
# ============================================================

def test_fill_by_order_id():
    """EXEC-05: Fill matched by order_id only; partial exit != stop-out.

    Uses get_order_status(order_id) path (order_list_query — works on SIMULATE).
    The engine passes the specific order_id to get_order_status so the gateway
    filters at the query level. The test verifies:
      - Only the row whose order_id matches is counted (EXEC-05 order_id matching)
      - A 100-share dealt_qty against a 300-share entry is accepted as a partial fill
        (D-06) and the unfiltered response row for a different order_id is ignored
      - Fill reconciliation by order_id (never by code or quantity)
    """
    from bot.execution.engine import ExecutionEngine
    from bot.execution.events import FillEvent

    cfg = _MockCfg()
    gw = MagicMock()
    gw.get_ask_price = AsyncMock(return_value=182.55)

    ENTRY_ORDER_ID = "ENTRY-ORDER-999"
    OTHER_ORDER_ID = "EXIT-ORDER-111"   # same code, different order_id

    # get_order_status(order_id) returns ALL active orders (like order_list_query).
    # The engine filters by matching order_id — verify it ignores unrelated rows.
    gw.get_order_status = AsyncMock(return_value=[
        # This is the entry fill (matching order_id) — 100 shares dealt
        {
            "order_id": ENTRY_ORDER_ID, "code": "US.AAPL",
            "order_status": "FILLED_PART", "qty": 300,
            "dealt_qty": 100, "dealt_avg_price": 182.60, "trd_side": "BUY",
        },
        # This is an unrelated order (different order_id) — must NOT be counted
        {
            "order_id": OTHER_ORDER_ID, "code": "US.AAPL",
            "order_status": "FILLED_ALL", "qty": 200,
            "dealt_qty": 200, "dealt_avg_price": 183.00, "trd_side": "SELL",
        },
    ])
    gw.place_order = AsyncMock(return_value=ENTRY_ORDER_ID)
    gw.cancel_order = AsyncMock()

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    # Entry for 300 shares; only 100 actually deal (partial fill, D-06)
    intent = _MockIntent(quantity=300)
    fill_event = _run(engine._manage_entry_order(intent))

    # FillEvent must only account for the matched order_id row (100 dealt shares)
    assert fill_event is not None
    assert isinstance(fill_event, FillEvent)
    assert fill_event.order_id == ENTRY_ORDER_ID, (
        "EXEC-05: fill must be keyed by order_id, not code"
    )
    assert fill_event.filled_qty == 100, (
        "EXEC-05: only matching order_id dealt_qty counted (100 shares, not 300)"
    )
    # D-06: partial fill (100/300) is accepted as the position, not rejected
    assert fill_event.is_entry is True

    # Verify the OTHER_ORDER_ID row (200 dealt shares) was NOT included
    assert fill_event.filled_qty != 300, (
        "EXEC-05: unrelated order_id row must not be conflated with entry fill"
    )
    assert fill_event.filled_qty != 200, (
        "EXEC-05: OTHER_ORDER_ID dealt_qty must not be summed with entry fill"
    )


# ============================================================
# Paper-fill regression tests — paper-deal-list-unsupported fix
# ============================================================
# These tests lock the paper fill path: fill detection must use get_order_status
# (order_list_query, works on SIMULATE) not get_order_fills (deal_list_query,
# raises GatewayError on SIMULATE: "Paper trading does not support deal data.").


def test_paper_fill_entry_no_deal_list_query():
    """Paper fill regression: _manage_entry_order uses get_order_status, never deal_list_query.

    Gateway test-double where get_order_fills raises GatewayError (simulating
    SIMULATE paper account) and get_order_status returns progressive dealt_qty.
    Asserts:
      - No GatewayError raised (deal_list_query path NOT taken)
      - FillEvent emitted with correct filled_qty from dealt_qty
      - get_order_fills is never called (dead code path eliminated)
    """
    from bot.execution.engine import ExecutionEngine
    from bot.execution.events import FillEvent
    from bot.gateway.gateway import GatewayError as GwError

    cfg = _MockCfg()
    gw = MagicMock()
    gw.get_ask_price = AsyncMock(return_value=150.00)
    gw.place_order = AsyncMock(return_value="PAPER-ORDER-001")
    gw.cancel_order = AsyncMock()

    # deal_list_query path raises (as SIMULATE paper account does)
    async def _deal_list_raises():
        raise GwError("deal_list_query failed: ret=-1, data=Paper trading does not support deal data.")

    gw.get_order_fills = _deal_list_raises

    # order_list_query path returns dealt_qty (SIMULATE paper account supports this)
    gw.get_order_status = AsyncMock(return_value=[
        {
            "order_id": "PAPER-ORDER-001", "code": "US.NVDA",
            "order_status": "FILLED_ALL", "qty": 50,
            "dealt_qty": 50, "dealt_avg_price": 150.10, "trd_side": "BUY",
        },
    ])

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    intent = _MockIntent(code="US.NVDA", quantity=50)

    # Must NOT raise GatewayError — deal_list_query path must not be taken
    fill_event = _run(engine._manage_entry_order(intent))

    assert fill_event is not None, "Paper entry fill: FillEvent must be emitted"
    assert isinstance(fill_event, FillEvent)
    assert fill_event.filled_qty == 50, "Paper entry fill: dealt_qty from order_list_query"
    assert abs(fill_event.avg_fill_price - 150.10) < 0.001
    assert fill_event.is_entry is True

    # get_order_fills must never have been called (dead path eliminated)
    # If get_order_fills were awaited, _deal_list_raises would have propagated and
    # fill_event would be None. The fact fill_event is not None proves it was not called.
    # We additionally verify get_order_status was used.
    gw.get_order_status.assert_awaited()


def test_paper_fill_exit_no_deal_list_query():
    """Paper fill regression: manage_exit uses get_order_status, never deal_list_query.

    Drives a single manage_exit call (300 shares) against a gateway test-double where:
      - get_order_fills raises GatewayError (SIMULATE paper not supported)
      - get_order_status returns full fill in first poll round (dealt_qty == 300)

    Asserts:
      - No GatewayError (deal_list_query path NOT taken)
      - total_filled == 300 (all shares exited in one round)
      - remaining after the call == 0 (position fully flat)
      - get_order_status was called (correct path used)
    """
    from bot.execution.engine import ExecutionEngine
    from bot.gateway.gateway import GatewayError as GwError

    cfg = _MockCfg()
    gw = MagicMock()
    gw.get_bid_price = AsyncMock(return_value=149.90)
    gw.place_order = AsyncMock(return_value="EXIT-PAPER-001")
    gw.cancel_order = AsyncMock()

    async def _deal_list_raises(*args, **kwargs):
        raise GwError("deal_list_query failed: ret=-1, data=Paper trading does not support deal data.")

    gw.get_order_fills = _deal_list_raises

    gw.get_order_status = AsyncMock(return_value=[
        {
            "order_id": "EXIT-PAPER-001", "code": "US.NVDA",
            "order_status": "FILLED_ALL", "qty": 300,
            "dealt_qty": 300, "dealt_avg_price": 149.85, "trd_side": "SELL",
        },
    ])

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    try:
        from moomoo import TrdSide
        sell_side = TrdSide.SELL
    except ImportError:
        sell_side = "SELL"

    # Must NOT raise GatewayError
    total_filled, avg_price = _run(engine.manage_exit(
        code="US.NVDA",
        qty=300,
        side=sell_side,
        escalation_step=0.10,
        escalation_cadence=0.01,
        ttl=0.05,
    ))

    assert total_filled == 300, (
        f"Paper exit fill: expected total_filled=300, got {total_filled}"
    )
    assert avg_price == pytest.approx(149.85), (
        "P1-B: manage_exit must also return the fill's avg price"
    )
    # get_order_status must have been used (correct path)
    gw.get_order_status.assert_awaited()


def test_paper_fill_exit_partial_then_full():
    """Paper fill regression: manage_exit partial-then-full across two outer iterations.

    Simulates the 300-share partial-scale-out scenario (CR-02 quantity invariant):
      Round 1: place order for 300, get_order_status → dealt_qty=100 (partial fill)
               total_filled → 100, remaining → 200. Cancel remainder, re-place.
      Round 2: place order for 200, get_order_status → dealt_qty=200 (full fill)
               total_filled → 300, remaining → 0. Loop exits.

    Asserts:
      - No GatewayError at any point (deal_list_query never called)
      - total_filled == 300 (correct cumulation across two order_ids)
      - place_order called exactly twice (once per outer iteration)
      - No double-count: each order_id's dealt_qty counted exactly once
    """
    from bot.execution.engine import ExecutionEngine
    from bot.gateway.gateway import GatewayError as GwError

    cfg = _MockCfg()
    placed_orders = []

    async def mock_place(code, qty, price, side):
        oid = f"EXIT-PAPER-{len(placed_orders) + 1}"
        placed_orders.append(oid)
        return oid

    async def mock_order_status(order_id=""):
        # Round 1 (first order): partial fill — 100 of 300 dealt
        if len(placed_orders) >= 1 and order_id == placed_orders[0]:
            return [
                {
                    "order_id": placed_orders[0], "code": "US.NVDA",
                    "order_status": "FILLED_PART", "qty": 300,
                    "dealt_qty": 100, "dealt_avg_price": 149.85, "trd_side": "SELL",
                }
            ]
        # Round 2 (second order): full fill — 200 of 200 dealt
        if len(placed_orders) >= 2 and order_id == placed_orders[1]:
            return [
                {
                    "order_id": placed_orders[1], "code": "US.NVDA",
                    "order_status": "FILLED_ALL", "qty": 200,
                    "dealt_qty": 200, "dealt_avg_price": 149.80, "trd_side": "SELL",
                }
            ]
        return []

    gw = MagicMock()
    gw.get_bid_price = AsyncMock(return_value=149.90)
    gw.place_order = mock_place
    gw.cancel_order = AsyncMock()
    gw.get_order_fills = AsyncMock(side_effect=GwError(
        "deal_list_query failed: ret=-1, data=Paper trading does not support deal data."
    ))
    gw.get_order_status = mock_order_status

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    try:
        from moomoo import TrdSide
        sell_side = TrdSide.SELL
    except ImportError:
        sell_side = "SELL"

    # Must NOT raise GatewayError
    total_filled, avg_price = _run(engine.manage_exit(
        code="US.NVDA",
        qty=300,
        side=sell_side,
        escalation_step=0.10,
        escalation_cadence=0.01,
        ttl=0.05,
    ))

    assert total_filled == 300, (
        f"CR-02 quantity invariant: expected total_filled=300, got {total_filled}"
    )
    assert len(placed_orders) == 2, (
        f"Expected exactly 2 place_order calls (partial then remainder), got {len(placed_orders)}"
    )
    # No double-count: Round 1 dealt 100, Round 2 dealt 200 → total 300 (not 400 or 600)
    # If double-count occurred, total_filled would be 200 (100+100) or exceed 300.
    # P1-B: avg_price is qty-weighted across both legs: (100*149.85+200*149.80)/300
    assert avg_price == pytest.approx((100 * 149.85 + 200 * 149.80) / 300)


# ============================================================
# Finding 2.4 — bid price fallback on repeated GatewayError mid-loop
# ============================================================

def test_manage_exit_bid_price_falls_back_after_gateway_error():
    """manage_exit must never abort/price-at-zero when a mid-loop bid re-price
    exhausts its retries (GatewayError raised twice in a row) — it must fall
    back to the last known good bid_price and continue to a full fill.
    """
    from bot.execution.engine import ExecutionEngine
    from bot.gateway.gateway import GatewayError as GwError

    cfg = _MockCfg(exit_escalation_cadence_seconds=0.01)
    placed_orders = []

    async def mock_place(code, qty, price, side):
        oid = f"EXIT-FALLBACK-{len(placed_orders) + 1}"
        placed_orders.append(oid)
        return oid

    async def mock_order_status(order_id=""):
        if len(placed_orders) >= 1 and order_id == placed_orders[0]:
            return [{
                "order_id": placed_orders[0], "code": "US.NVDA",
                "order_status": "FILLED_PART", "qty": 200,
                "dealt_qty": 100, "dealt_avg_price": 149.85, "trd_side": "SELL",
            }]
        if len(placed_orders) >= 2 and order_id == placed_orders[1]:
            return [{
                "order_id": placed_orders[1], "code": "US.NVDA",
                "order_status": "FILLED_ALL", "qty": 100,
                "dealt_qty": 100, "dealt_avg_price": 149.80, "trd_side": "SELL",
            }]
        return []

    gw = MagicMock()
    # First call (initial pricing) succeeds; the mid-loop re-price (after the
    # partial fill) fails twice (exhausting _PRICE_FETCH_RETRIES) — manage_exit
    # must fall back to the last known bid_price (149.90) instead of raising.
    gw.get_bid_price = AsyncMock(
        side_effect=[149.90, GwError("snapshot failed"), GwError("snapshot failed")]
    )
    gw.place_order = mock_place
    gw.cancel_order = AsyncMock()
    gw.get_order_status = mock_order_status

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    try:
        from moomoo import TrdSide
        sell_side = TrdSide.SELL
    except ImportError:
        sell_side = "SELL"

    with patch("bot.execution.engine.asyncio.sleep", new=AsyncMock()):
        total_filled, avg_price = _run(engine.manage_exit(
            code="US.NVDA",
            qty=200,
            side=sell_side,
            escalation_step=0.10,
            escalation_cadence=0.01,
            ttl=0.05,
        ))

    assert total_filled == 200, (
        f"Expected full fill (100+100) after fallback, got {total_filled}"
    )
    assert len(placed_orders) == 2, (
        f"Expected 2 place_order calls (partial then remainder), got {len(placed_orders)}"
    )
    assert avg_price == pytest.approx((100 * 149.85 + 100 * 149.80) / 200)


# ============================================================
# Rate-limit resilience — RATE-01 manage_exit defense-in-depth
# ============================================================

def test_manage_exit_continues_on_transient_poll_failure():
    """manage_exit does not abort on a single transient get_order_status GatewayError.

    Simulates a rate-limit-driven GatewayError from the gateway on the first
    status poll, followed by a successful fill on the second poll. manage_exit
    must absorb the transient failure, keep the order live, and detect the fill
    on the next cadence tick rather than propagating the exception and orphaning
    the exit order.

    Must FAIL before the fix (GatewayError from get_order_status propagates out
    of the manage_exit poll loop and is caught by manager._place_exit_order which
    returns 0 — abandoning the exit).
    """
    from bot.execution.engine import ExecutionEngine
    from bot.gateway.gateway import GatewayError as GwError

    # Long TTL so two poll cadence ticks easily fit within deadline
    cfg = _MockCfg(exit_ttl_seconds=0.5, exit_escalation_cadence_seconds=0.01)

    gw = MagicMock()
    gw.get_bid_price = AsyncMock(return_value=149.90)
    gw.place_order = AsyncMock(return_value="EXIT-RL-001")
    gw.cancel_order = AsyncMock()

    call_count = {"n": 0}

    async def mock_status(order_id=""):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: rate-limit GatewayError (transient)
            raise GwError(
                "order_list_query failed: ret=-1, data=Get Order list request failed "
                "due to high frequency. Maximum 10 times per 30 seconds."
            )
        # Second call onward: return a fill
        return [
            {
                "order_id": "EXIT-RL-001",
                "code": "US.CLOV",
                "order_status": "FILLED_ALL",
                "qty": 66,
                "dealt_qty": 66,
                "dealt_avg_price": 149.85,
                "trd_side": "SELL",
            }
        ]

    gw.get_order_status = mock_status

    store = _make_mock_store()
    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    try:
        from moomoo import TrdSide
        sell_side = TrdSide.SELL
    except ImportError:
        sell_side = "SELL"

    # Must NOT raise GatewayError — transient poll failure must be absorbed
    total_filled, avg_price = _run(engine.manage_exit(
        code="US.CLOV",
        qty=66,
        side=sell_side,
        escalation_step=0.10,
        escalation_cadence=0.01,
        ttl=0.5,
    ))

    assert total_filled == 66, (
        f"manage_exit must detect fill after transient poll GatewayError; "
        f"got total_filled={total_filled}"
    )
    assert avg_price == pytest.approx(149.85)
    assert call_count["n"] >= 2, (
        "get_order_status must be called at least twice (first raises, second fills)"
    )


# ============================================================
# Finding 1.4: re-query dealt_qty after cancel before computing replacement qty
# ============================================================

def test_manage_exit_requeries_dealt_qty_after_cancel():
    """Regression 1.4: after cancel_order confirms, manage_exit must re-query dealt_qty.

    Scenario:
      - Total qty to exit = 100
      - Pre-cancel poll: dealt_qty = 80 (partial fill detected in poll loop)
      - cancel_order returns OK
      - Post-cancel re-query: dealt_qty = 90 (10 more shares filled during cancel)
      - Replacement order must be for 100 - 90 = 10, NOT 100 - 80 = 20
      - Without the fix, the engine uses the pre-cancel snapshot (80), over-orders
        by 10, potentially going net-short

    This test drives manage_exit with a carefully orchestrated sequence of
    get_order_status return values to verify that the post-cancel re-query
    is used for sizing the replacement order.
    """
    from bot.execution.engine import ExecutionEngine

    cfg = _MockCfg(
        exit_ttl_seconds=0.05,
        exit_escalation_cadence_seconds=0.01,
        exit_limit_buffer_usd=0.05,
        exit_escalation_step_usd=0.10,
    )
    store = _make_mock_store()

    # Track get_order_status call count to distinguish pre-cancel poll from post-cancel re-query.
    get_order_status_calls = {"n": 0}

    async def mock_get_order_status(order_id=None):
        get_order_status_calls["n"] += 1
        n = get_order_status_calls["n"]
        oid = str(order_id) if order_id else "ORDER-EXIT-001"

        if oid == "ORDER-EXIT-001":
            if n == 1:
                # First poll: pre-cancel snapshot (80 filled)
                return [{"order_id": "ORDER-EXIT-001", "dealt_qty": 80, "dealt_avg_price": 150.00}]
            else:
                # Post-cancel re-query: 90 filled (10 more during cancel)
                return [{"order_id": "ORDER-EXIT-001", "dealt_qty": 90, "dealt_avg_price": 150.00}]
        elif oid == "ORDER-EXIT-002":
            # Replacement order: fills the remaining 10 immediately
            return [{"order_id": "ORDER-EXIT-002", "dealt_qty": 10, "dealt_avg_price": 149.90}]
        return []

    order_seq = {"n": 0}

    async def mock_place_order(code, qty, price, side):
        order_seq["n"] += 1
        n = order_seq["n"]
        if n == 1:
            assert qty == 100, f"First order must be for full qty=100, got {qty}"
            return "ORDER-EXIT-001"
        elif n == 2:
            # This is the replacement order — assert correct qty after post-cancel re-query
            assert qty == 10, (
                f"Replacement order qty must be 100 - post_cancel_filled(90) = 10, got {qty}. "
                "Using pre-cancel snapshot (80) would give qty=20 — over-sell into short."
            )
            return "ORDER-EXIT-002"
        return f"ORDER-EXIT-{n:03d}"

    gw = MagicMock()
    gw.get_bid_price = AsyncMock(return_value=150.05)
    gw.place_order = mock_place_order
    gw.get_order_status = mock_get_order_status
    gw.cancel_order = AsyncMock()

    engine = ExecutionEngine(gateway=gw, store=store, cfg=cfg)

    from moomoo import TrdSide
    total_filled, avg_price = _run(engine.manage_exit(
        code="US.AAPL",
        qty=100,
        side=TrdSide.SELL,
        escalation_step=0.10,
        escalation_cadence=0.01,
        ttl=0.05,
    ))

    assert total_filled == 100, (
        f"manage_exit must exit all 100 shares; got total_filled={total_filled}"
    )
    # P1-B: avg_price weighted across the post-cancel re-queried leg (90@150.00)
    # and the replacement order's leg (10@149.90) -- NOT the stale pre-cancel
    # snapshot (80@150.00), matching the same re-query-after-cancel discipline
    # Finding 1.4 already established for quantity.
    assert avg_price == pytest.approx((90 * 150.00 + 10 * 149.90) / 100)
    assert order_seq["n"] == 2, (
        f"Must place exactly 2 orders (initial + replacement); got {order_seq['n']}"
    )
