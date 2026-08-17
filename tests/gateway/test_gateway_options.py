#!/usr/bin/env python3
"""
tests/gateway/test_gateway_options.py — Phase 8 option reads on MoomooGateway.

Verifies (no network — the SDK quote/trade contexts are MagicMocks):
  - get_stock_ids: ETF call first, one STOCK call for the remainder only
  - screen_options: pages until last_page, flattens `underlying`, normalises keys
  - get_option_positions: option-code regex whitelist + short-sign convention
"""
import asyncio

import pandas as pd
import pytest
from unittest.mock import MagicMock

from bot.gateway.gateway import (
    MoomooGateway,
    GatewayConfig,
    GatewayError,
    _OPTION_CODE_RE,
    _OPTION_SCREEN_MAX_PAGES,
)


# ============================================================
# Helpers
# ============================================================

def _run(coro):
    return asyncio.run(coro)


def _gw() -> MoomooGateway:
    gw = MoomooGateway(GatewayConfig(acc_id=1727266, paper_trading=True, trd_env="SIMULATE"))
    gw._quote_ctx = MagicMock()
    gw._trade_ctx = MagicMock()
    return gw


def _screen_row(**over) -> dict:
    """One option_screen row with the SDK's raw column names."""
    row = {
        "code": "US.SPY260320P600000",
        "option_name": "SPY 260320 600.00 P",
        "option_type": 2,
        "strike_price": 600.0,
        "strike_date": "2026-03-20",
        "left_day": 45,
        "price": 5.0,
        "mid_price": 5.05,
        "bid_price": 5.0,
        "ask_price": 5.1,
        "bid_ask_spread": 0.1,
        "open_interest": 1234,
        "implied_volatility": 0.21,
        "delta": -0.16,
        "theta": -0.05,
        "otm_probability": 0.84,
        "underlying": {
            "stock_id": 202805,
            "iv": 0.2,
            "hv": 0.18,
            "iv_rank": 0.066,
            "iv_percentile": 0.12,
            "market_cap": 1.0,
            "price": 640.0,
            "change_ratio": -0.021,
        },
    }
    row.update(over)
    return row


def _page(rows, last_page: bool):
    """SDK get_option_screen success payload: (ret, (last_page, all_count, df))."""
    return (0, (last_page, len(rows), pd.DataFrame(rows)))


# ============================================================
# get_stock_ids
# ============================================================

class TestGetStockIds:
    def test_etf_call_resolves_all(self):
        gw = _gw()
        gw._quote_ctx.get_stock_basicinfo.return_value = (
            0, pd.DataFrame([
                {"code": "US.SPY", "stock_id": 202805},
                {"code": "US.QQQ", "stock_id": 202806},
            ]),
        )
        out = _run(gw.get_stock_ids(["US.SPY", "US.QQQ"]))
        assert out == {"US.SPY": 202805, "US.QQQ": 202806}
        assert gw._quote_ctx.get_stock_basicinfo.call_count == 1

    def test_missing_codes_trigger_second_stock_call_with_remainder_only(self):
        gw = _gw()
        calls = []

        def _basicinfo(market, stock_type, code_list=None):
            calls.append(list(code_list))
            if len(calls) == 1:
                return (0, pd.DataFrame([{"code": "US.SPY", "stock_id": 202805}]))
            return (0, pd.DataFrame([{"code": "US.AAPL", "stock_id": 111}]))

        gw._quote_ctx.get_stock_basicinfo.side_effect = _basicinfo
        out = _run(gw.get_stock_ids(["US.SPY", "US.AAPL"]))

        assert out == {"US.SPY": 202805, "US.AAPL": 111}
        assert calls == [["US.SPY", "US.AAPL"], ["US.AAPL"]]   # remainder only

    def test_unresolved_code_is_omitted(self):
        gw = _gw()
        gw._quote_ctx.get_stock_basicinfo.return_value = (
            0, pd.DataFrame([{"code": "US.SPY", "stock_id": 202805}]),
        )
        out = _run(gw.get_stock_ids(["US.SPY", "US.NOPE"]))
        assert out == {"US.SPY": 202805}

    def test_falsy_stock_id_row_is_skipped(self):
        gw = _gw()
        gw._quote_ctx.get_stock_basicinfo.return_value = (
            0, pd.DataFrame([
                {"code": "US.SPY", "stock_id": 202805},
                {"code": "US.QQQ", "stock_id": 0},
            ]),
        )
        out = _run(gw.get_stock_ids(["US.SPY", "US.QQQ"]))
        assert out == {"US.SPY": 202805}

    def test_sdk_failure_raises_gateway_error(self):
        gw = _gw()
        gw._quote_ctx.get_stock_basicinfo.return_value = (-1, "boom")
        with pytest.raises(GatewayError):
            _run(gw.get_stock_ids(["US.SPY"]))


# ============================================================
# screen_options
# ============================================================

class TestScreenOptions:
    def test_single_page_calls_screen_once(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page([_screen_row()], True)
        rows = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))
        assert len(rows) == 1
        assert gw._quote_ctx.get_option_screen.call_count == 1

    def test_pages_until_last_page(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.side_effect = [
            _page([_screen_row(code="US.SPY260320P600000")], False),
            _page([_screen_row(code="US.SPY260320P590000", strike_price=590.0)], True),
        ]
        rows = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))
        assert gw._quote_ctx.get_option_screen.call_count == 2
        assert [r["code"] for r in rows] == [
            "US.SPY260320P600000", "US.SPY260320P590000",
        ]

    def test_empty_page_breaks_even_when_last_page_never_set(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.side_effect = [
            _page([_screen_row()], False),
            _page([], False),
        ]
        rows = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))
        assert len(rows) == 1
        assert gw._quote_ctx.get_option_screen.call_count == 2

    def test_paging_is_capped(self):
        """A server that never sets last_page cannot hang the executor thread."""
        gw = _gw()
        gw._quote_ctx.get_option_screen.side_effect = (
            lambda req: _page([_screen_row()], False)
        )
        rows = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))
        assert gw._quote_ctx.get_option_screen.call_count == _OPTION_SCREEN_MAX_PAGES
        assert len(rows) == _OPTION_SCREEN_MAX_PAGES

    def test_page_from_advances_by_rows_returned(self):
        gw = _gw()
        seen = []

        def _screen(req):
            seen.append(req.page_from)
            if len(seen) == 1:
                return _page([_screen_row(), _screen_row()], False)
            return _page([_screen_row()], True)

        gw._quote_ctx.get_option_screen.side_effect = _screen
        _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))
        assert seen == [0, 2]

    def test_underlying_dict_is_flattened(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page([_screen_row()], True)
        row = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))[0]
        assert row["u_stock_id"] == 202805
        assert row["u_price"] == 640.0
        assert row["u_iv"] == 0.2
        assert row["u_iv_rank"] == 0.066        # NOT scaled here — service does the x100
        assert row["u_iv_percentile"] == 0.12
        assert row["u_change_ratio"] == -0.021
        assert "underlying" not in row

    def test_non_dict_underlying_yields_none_u_keys(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page(
            [_screen_row(underlying="N/A")], True,
        )
        row = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))[0]
        for key in ("u_stock_id", "u_price", "u_iv", "u_iv_rank",
                    "u_iv_percentile", "u_change_ratio"):
            assert row[key] is None
        assert "underlying" not in row

    def test_keys_normalised_for_pick_strikes(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page([_screen_row()], True)
        row = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))[0]
        assert row["strike"] == 600.0
        assert row["expiry"] == "2026-03-20"
        assert row["dte"] == 45
        assert row["bid"] == 5.0
        assert row["ask"] == 5.1
        assert row["mid"] == 5.05
        assert row["iv"] == 0.21
        # untouched raw columns pick_strikes/leg_is_liquid consume directly
        assert row["code"] == "US.SPY260320P600000"
        assert row["open_interest"] == 1234
        assert row["delta"] == -0.16

    def test_expiry_truncated_to_date(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page(
            [_screen_row(strike_date="2026-03-20 00:00:00")], True,
        )
        row = _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))[0]
        assert row["expiry"] == "2026-03-20"

    @pytest.mark.parametrize("option_type,expected", [
        (1, "C"), ("1", "C"), ("CALL", "C"), ("C", "C"),
        (2, "P"), ("2", "P"), ("PUT", "P"), ("P", "P"),
    ])
    def test_right_mapping(self, option_type, expected):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page(
            [_screen_row(option_type=option_type)], True,
        )
        row = _run(gw.screen_options([202805], "C", 30, 60, 0.10, 0.25))[0]
        assert row["right"] == expected

    def test_unknown_option_type_falls_back_to_right_argument(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = _page(
            [_screen_row(option_type="N/A")], True,
        )
        row = _run(gw.screen_options([202805], "C", 30, 60, 0.10, 0.25))[0]
        assert row["right"] == "C"

    def test_sdk_failure_raises_gateway_error(self):
        gw = _gw()
        gw._quote_ctx.get_option_screen.return_value = (-1, "screen failed")
        with pytest.raises(GatewayError):
            _run(gw.screen_options([202805], "P", 30, 60, 0.10, 0.25))


# ============================================================
# get_option_positions
# ============================================================

class TestGetOptionPositions:
    def test_regex_keeps_option_codes_and_drops_equity(self):
        assert _OPTION_CODE_RE.match("US.SPY260320P600000")
        assert _OPTION_CODE_RE.match("US.QQQ260117C500000")
        assert not _OPTION_CODE_RE.match("US.SPY")
        assert not _OPTION_CODE_RE.match("HK.00700")

    def test_equity_positions_are_invisible(self):
        gw = _gw()
        gw._trade_ctx.position_list_query.return_value = (0, pd.DataFrame([
            {"code": "US.SPY", "qty": 500, "position_side": "LONG"},
            {"code": "US.AAPL", "qty": 100, "position_side": "LONG"},
            {"code": "US.SPY260320P600000", "qty": 2, "position_side": "LONG"},
        ]))
        out = _run(gw.get_option_positions())
        assert out == {"US.SPY260320P600000": 2}

    def test_short_side_is_negative(self):
        gw = _gw()
        gw._trade_ctx.position_list_query.return_value = (0, pd.DataFrame([
            {"code": "US.SPY260320P600000", "qty": 2, "position_side": "SHORT"},
            {"code": "US.SPY260320P590000", "qty": 2, "position_side": "LONG"},
        ]))
        out = _run(gw.get_option_positions())
        assert out == {"US.SPY260320P600000": -2, "US.SPY260320P590000": 2}

    def test_already_negative_qty_stays_negative(self):
        gw = _gw()
        gw._trade_ctx.position_list_query.return_value = (0, pd.DataFrame([
            {"code": "US.SPY260320P600000", "qty": -3, "position_side": "N/A"},
        ]))
        out = _run(gw.get_option_positions())
        assert out == {"US.SPY260320P600000": -3}

    def test_refresh_cache_is_true(self):
        gw = _gw()
        gw._trade_ctx.position_list_query.return_value = (0, pd.DataFrame())
        assert _run(gw.get_option_positions()) == {}
        assert gw._trade_ctx.position_list_query.call_args.kwargs["refresh_cache"] is True

    def test_sdk_failure_raises_gateway_error(self):
        gw = _gw()
        gw._trade_ctx.position_list_query.return_value = (-1, "denied")
        with pytest.raises(GatewayError):
            _run(gw.get_option_positions())
