#!/usr/bin/env python3
"""
tests/gateway/test_order_methods.py — Unit tests for MoomooGateway order methods.

Verifies (no network — all SDK contexts are mocked):
  - place_order returns order_id and uses OrderType.NORMAL (EXEC-02; never MARKET)
  - cancel_order calls modify_order with ModifyOrderOp.CANCEL, qty=0, price=0
  - get_order_fills passes refresh_cache=True (Pitfall B)
  - get_order_status passes refresh_cache=True (Pitfall B)
  - get_ask_price returns ask_price column; falls back to last_price when 0
  - get_bid_price returns bid_price column; falls back to last_price when 0
  - All methods import cleanly without moomoo-api (deferred imports)
"""
import asyncio
import inspect
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, AsyncMock

# ============================================================
# Imports under test — must succeed even without moomoo-api
# ============================================================
from bot.gateway.gateway import (
    MoomooGateway,
    GatewayConfig,
    GatewayError,
)


# ============================================================
# Helpers
# ============================================================

def _make_cfg() -> GatewayConfig:
    """Minimal GatewayConfig (SIMULATE, acc_id 0)."""
    return GatewayConfig(
        opend_host="127.0.0.1",
        opend_port=11111,
        trd_env="SIMULATE",
        default_market="US",
        acc_id=0,
        paper_trading=True,
    )


def _make_gateway(mock_trade_ctx=None, mock_quote_ctx=None) -> MoomooGateway:
    """Build a MoomooGateway with mocked contexts (no live OpenD)."""
    cfg = _make_cfg()
    gw = MoomooGateway(cfg)
    gw._trade_ctx = mock_trade_ctx or MagicMock()
    gw._quote_ctx = mock_quote_ctx or MagicMock()
    return gw


def _run(coro):
    """Run a coroutine in a new event loop."""
    return asyncio.run(coro)


# ============================================================
# Test: place_order — returns order_id, uses OrderType.NORMAL
# ============================================================

class TestPlaceOrder:
    """Asserts place_order behaviour: NORMAL order type, returns order_id."""

    def test_returns_order_id(self):
        """place_order returns the broker-assigned order_id string."""
        mock_trade_ctx = MagicMock()
        # DataFrame with the order_id the broker would return
        df = pd.DataFrame([{"order_id": "98765", "code": "US.AAPL"}])
        mock_trade_ctx.place_order.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)

        # Patch moomoo so the import inside place_order resolves
        import sys
        import types
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.OrderType = MagicMock()
        moomoo_mod.OrderType.NORMAL = "NORMAL_SENTINEL"
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.BUY = "BUY_SENTINEL"
        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            order_id = _run(gw.place_order(
                code="US.AAPL",
                qty=100,
                price=182.55,
                trd_side=moomoo_mod.TrdSide.BUY,
            ))

        assert order_id == "98765"

    def test_uses_order_type_normal_never_market(self):
        """place_order calls trade_ctx.place_order with OrderType.NORMAL — never MARKET."""
        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([{"order_id": "11111"}])
        mock_trade_ctx.place_order.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)

        import sys
        import types
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        moomoo_mod.OrderType = MagicMock()
        moomoo_mod.OrderType.NORMAL = "NORMAL_SENTINEL"
        moomoo_mod.TrdSide = MagicMock()
        moomoo_mod.TrdSide.BUY = "BUY_SENTINEL"
        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            _run(gw.place_order(
                code="US.AAPL",
                qty=50,
                price=182.00,
                trd_side=moomoo_mod.TrdSide.BUY,
            ))

        call_kwargs = mock_trade_ctx.place_order.call_args
        # order_type kwarg must be OrderType.NORMAL
        assert call_kwargs.kwargs.get("order_type") == "NORMAL_SENTINEL"
        # qty and price are coerced to correct types
        assert call_kwargs.kwargs.get("qty") == 50
        assert call_kwargs.kwargs.get("price") == 182.00

    def test_market_keyword_never_in_gateway_source(self):
        """EXEC-02 grep gate: 'OrderType.MARKET' must not appear in gateway.py source."""
        import pathlib
        src = pathlib.Path(__file__).parents[2] / "bot" / "gateway" / "gateway.py"
        text = src.read_text()
        assert "OrderType.MARKET" not in text, (
            "EXEC-02 violation: 'OrderType.MARKET' found in bot/gateway/gateway.py"
        )


# ============================================================
# Test: cancel_order — issues modify_order with CANCEL op, qty=0, price=0
# ============================================================

class TestCancelOrder:
    """Asserts cancel_order is implemented as modify_order with ModifyOrderOp.CANCEL."""

    def test_cancel_calls_modify_order_with_cancel_op(self):
        """cancel_order calls modify_order(modify_order_op=CANCEL, qty=0, price=0)."""
        mock_trade_ctx = MagicMock()
        mock_trade_ctx.modify_order.return_value = (0, "OK")

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)

        import sys
        import types
        moomoo_mod = sys.modules.get("moomoo") or types.ModuleType("moomoo")
        cancel_sentinel = object()
        moomoo_mod.ModifyOrderOp = MagicMock()
        moomoo_mod.ModifyOrderOp.CANCEL = cancel_sentinel
        with patch.dict("sys.modules", {"moomoo": moomoo_mod}):
            _run(gw.cancel_order(order_id="55555"))

        call_kwargs = mock_trade_ctx.modify_order.call_args
        assert call_kwargs.kwargs.get("modify_order_op") is cancel_sentinel
        assert call_kwargs.kwargs.get("order_id") == "55555"
        assert call_kwargs.kwargs.get("qty") == 0
        assert call_kwargs.kwargs.get("price") == 0


# ============================================================
# Test: get_order_fills — passes refresh_cache=True (Pitfall B)
# ============================================================

class TestGetOrderFills:
    """Asserts get_order_fills returns list of dicts with order_id and passes refresh_cache=True."""

    def test_passes_refresh_cache_true(self):
        """get_order_fills always calls deal_list_query with refresh_cache=True."""
        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([
            {
                "deal_id": "D1",
                "order_id": "O1",
                "code": "US.AAPL",
                "qty": 100,
                "price": 182.00,
                "trd_side": "BUY",
                "create_time": "2026-06-24 10:00:00",
            }
        ])
        mock_trade_ctx.deal_list_query.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)
        fills = _run(gw.get_order_fills())

        call_kwargs = mock_trade_ctx.deal_list_query.call_args
        assert call_kwargs.kwargs.get("refresh_cache") is True

    def test_returns_list_of_dicts_with_order_id(self):
        """get_order_fills returns list of dicts each containing 'order_id' key."""
        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([
            {
                "deal_id": "D2",
                "order_id": "O2",
                "code": "US.MSFT",
                "qty": 50,
                "price": 400.00,
                "trd_side": "BUY",
                "create_time": "2026-06-24 10:05:00",
            }
        ])
        mock_trade_ctx.deal_list_query.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)
        fills = _run(gw.get_order_fills())

        assert isinstance(fills, list)
        assert len(fills) == 1
        assert "order_id" in fills[0]
        assert fills[0]["order_id"] == "O2"

    def test_returns_empty_list_when_no_fills(self):
        """get_order_fills returns [] when deal_list_query returns empty DataFrame."""
        mock_trade_ctx = MagicMock()
        mock_trade_ctx.deal_list_query.return_value = (0, pd.DataFrame())

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)
        fills = _run(gw.get_order_fills())

        assert fills == []


# ============================================================
# Test: get_order_status — passes refresh_cache=True (Pitfall B)
# ============================================================

class TestGetOrderStatus:
    """Asserts get_order_status returns list of dicts and passes refresh_cache=True."""

    def test_passes_refresh_cache_true(self):
        """get_order_status always calls order_list_query with refresh_cache=True."""
        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([
            {
                "order_id": "O3",
                "code": "US.AAPL",
                "order_status": "FILLED",
                "qty": 100,
                "dealt_qty": 100,
                "dealt_avg_price": 182.50,
                "trd_side": "BUY",
            }
        ])
        mock_trade_ctx.order_list_query.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)
        _run(gw.get_order_status("O3"))

        call_kwargs = mock_trade_ctx.order_list_query.call_args
        assert call_kwargs.kwargs.get("refresh_cache") is True

    def test_returns_list_of_dicts_with_order_id(self):
        """get_order_status returns list of dicts each containing 'order_id' key."""
        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([
            {
                "order_id": "O4",
                "code": "US.TSLA",
                "order_status": "WAITING_SUBMIT",
                "qty": 20,
                "dealt_qty": 0,
                "dealt_avg_price": 0.0,
                "trd_side": "BUY",
            }
        ])
        mock_trade_ctx.order_list_query.return_value = (0, df)

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)
        rows = _run(gw.get_order_status())

        assert isinstance(rows, list)
        assert len(rows) == 1
        assert "order_id" in rows[0]
        assert rows[0]["order_id"] == "O4"


# ============================================================
# Test: get_ask_price / get_bid_price — column resolution
# ============================================================

class TestSnapshotPriceMethods:
    """Asserts get_ask_price and get_bid_price resolve columns correctly."""

    def _make_snapshot_df(self, ask=182.55, bid=182.50, last=182.52):
        return pd.DataFrame([
            {
                "code": "US.AAPL",
                "ask_price": ask,
                "bid_price": bid,
                "last_price": last,
            }
        ])

    def test_get_ask_price_returns_ask_column(self):
        """get_ask_price returns ask_price field from snapshot when non-zero."""
        mock_quote_ctx = MagicMock()
        df = self._make_snapshot_df(ask=182.55)
        mock_quote_ctx.get_market_snapshot.return_value = (0, df)

        gw = _make_gateway(mock_quote_ctx=mock_quote_ctx)
        ask = _run(gw.get_ask_price("US.AAPL"))

        assert ask == pytest.approx(182.55)

    def test_get_ask_price_falls_back_to_last_price_when_zero(self):
        """get_ask_price falls back to last_price when ask_price is 0 (illiquid)."""
        mock_quote_ctx = MagicMock()
        df = self._make_snapshot_df(ask=0.0, last=182.52)
        mock_quote_ctx.get_market_snapshot.return_value = (0, df)

        gw = _make_gateway(mock_quote_ctx=mock_quote_ctx)
        ask = _run(gw.get_ask_price("US.AAPL"))

        assert ask == pytest.approx(182.52)

    def test_get_bid_price_returns_bid_column(self):
        """get_bid_price returns bid_price field from snapshot when non-zero."""
        mock_quote_ctx = MagicMock()
        df = self._make_snapshot_df(bid=182.50)
        mock_quote_ctx.get_market_snapshot.return_value = (0, df)

        gw = _make_gateway(mock_quote_ctx=mock_quote_ctx)
        bid = _run(gw.get_bid_price("US.AAPL"))

        assert bid == pytest.approx(182.50)

    def test_get_bid_price_falls_back_to_last_price_when_zero(self):
        """get_bid_price falls back to last_price when bid_price is 0 (illiquid)."""
        mock_quote_ctx = MagicMock()
        df = self._make_snapshot_df(bid=0.0, last=182.52)
        mock_quote_ctx.get_market_snapshot.return_value = (0, df)

        gw = _make_gateway(mock_quote_ctx=mock_quote_ctx)
        bid = _run(gw.get_bid_price("US.AAPL"))

        assert bid == pytest.approx(182.52)


# ============================================================
# Test: get_order_status — rate-limit retry (RATE-01)
# ============================================================

class TestGetOrderStatusRateLimit:
    """Asserts get_order_status retries on rate-limit and raises on non-rate-limit errors."""

    def test_rate_limit_then_success_returns_rows(self):
        """get_order_status recovers from a rate-limit ret=-1 by retrying and returns rows.

        Mock order_list_query: first call returns (-1, high-frequency message);
        second call returns (RET_OK, df). Must FAIL before the fix because the
        current code calls _check_ret immediately and raises GatewayError.
        """
        from unittest.mock import patch as _patch

        mock_trade_ctx = MagicMock()
        df = pd.DataFrame([
            {
                "order_id": "O-RATELIMIT",
                "code": "US.CLOV",
                "order_status": "FILLED_ALL",
                "qty": 66,
                "dealt_qty": 66,
                "dealt_avg_price": 5.10,
                "trd_side": "SELL",
            }
        ])
        # First call → rate-limit error; second call → success
        mock_trade_ctx.order_list_query.side_effect = [
            (
                -1,
                "Get Order list request failed due to high frequency."
                " Maximum 10 times per 30 seconds.",
            ),
            (0, df),
        ]

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)

        # Patch backoff to 0 s so tests remain fast
        with _patch("bot.gateway.gateway._RATE_LIMIT_BACKOFF_SECONDS", 0.0):
            rows = _run(gw.get_order_status("O-RATELIMIT"))

        assert isinstance(rows, list), "rows must be a list after retry succeeds"
        assert len(rows) == 1, "one order row must be returned after successful retry"
        assert rows[0]["order_id"] == "O-RATELIMIT"
        assert rows[0]["dealt_qty"] == 66
        # Verify retry occurred: order_list_query called exactly twice
        assert mock_trade_ctx.order_list_query.call_count == 2, (
            "order_list_query must be called twice (rate-limit then success)"
        )

    def test_non_rate_limit_error_still_raises_gateway_error(self):
        """A non-rate-limit ret=-1 must still raise GatewayError immediately — no regression.

        This confirms only the rate-limit substring triggers retries; all other
        SDK errors are propagated unchanged.
        """
        mock_trade_ctx = MagicMock()
        mock_trade_ctx.order_list_query.return_value = (
            -1,
            "order_list_query failed: Access denied.",
        )

        gw = _make_gateway(mock_trade_ctx=mock_trade_ctx)

        with pytest.raises(GatewayError, match="order_list_query"):
            _run(gw.get_order_status())

        # Only one call — no retry on non-rate-limit errors
        assert mock_trade_ctx.order_list_query.call_count == 1, (
            "order_list_query must be called exactly once on a non-rate-limit error"
        )
