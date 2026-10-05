#!/usr/bin/env python3
"""tests/ibs/test_execution.py — IbsExecutor adapter over LegExecutor (IBS-05, D-08, D-09).

asyncio.sleep is NOT patched: LegExecutor measures its TTL with loop.time(), so the
tiny cfg TTL/poll values keep this file fast.
"""
import asyncio
import dataclasses
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from bot.execution.engine import _get_trd_side_buy, _get_trd_side_sell
from bot.ibs.execution import IbsExecutor, _leg_cfg

NOW = datetime(2026, 10, 5, 15, 50, tzinfo=ZoneInfo("America/New_York"))
CODE = "US.XLU"


class FakeGateway:
    """Records placements/cancels; `dealt` maps order_id -> dealt qty (default 0)."""

    def __init__(self, dealt=None, cancel_raises=False):
        self.placed = []
        self.cancelled = []
        self.dealt = dealt or {}
        self.cancel_raises = cancel_raises
        self._n = 0

    async def place_order(self, code, qty, price, trd_side):
        self._n += 1
        self.placed.append((code, qty, price, trd_side))
        return f"O{self._n}"

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if self.cancel_raises:
            raise RuntimeError("cancel failed")

    async def get_order_status(self, order_id):
        return [{
            "order_id": order_id,
            "dealt_qty": self.dealt.get(order_id, 0),
            "dealt_avg_price": 100.0,
        }]


@pytest.fixture
def cfg(ibs_cfg):
    return dataclasses.replace(ibs_cfg, poll_interval_seconds=0.01, order_ttl_seconds=0.05)


def run(gw, cfg, side="BUY", qty=5, deadline=None, on_placed=None, now=NOW):
    deadline = deadline or now + timedelta(seconds=60)
    ex = IbsExecutor(gw, cfg)
    with patch("bot.ibs.execution.now_et", return_value=now):
        return asyncio.run(ex.work(side, CODE, qty, 100.00, deadline, on_placed=on_placed))


def test_buy_fills_first_poll(cfg):
    gw = FakeGateway(dealt={"O1": 5})
    assert run(gw, cfg) == ("O1", 100.0, 5)
    assert gw.placed == [(CODE, 5, 100.05, _get_trd_side_buy())]


def test_sell_price_below_last(cfg):
    gw = FakeGateway(dealt={"O1": 5})
    assert run(gw, cfg, side="SELL")[2] == 5
    assert gw.placed == [(CODE, 5, 99.95, _get_trd_side_sell())]


def test_buy_escalates_up_and_gives_up(cfg):
    gw = FakeGateway()
    assert run(gw, cfg) is None
    assert [p[2] for p in gw.placed] == [100.05, 100.10, 100.15, 100.20]
    assert gw.cancelled == ["O1", "O2", "O3", "O4"]


def test_sell_escalates_down(cfg):
    gw = FakeGateway()
    assert run(gw, cfg, side="SELL") is None
    assert [p[2] for p in gw.placed] == [99.95, 99.90, 99.85, 99.80]


def test_ttl_partial_returned(cfg):
    gw = FakeGateway(dealt={"O1": 2})
    assert run(gw, cfg) == ("O1", 100.0, 2)
    assert gw.cancelled == ["O1"]


def test_unconfirmed_cancel_raises(cfg):
    gw = FakeGateway(dealt={"O1": 2}, cancel_raises=True)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        run(gw, cfg)


def test_deadline_times_out_and_cancels(cfg):
    gw = FakeGateway()
    with pytest.raises(asyncio.TimeoutError):
        run(gw, cfg, deadline=NOW + timedelta(seconds=0.02))
    assert gw.placed and gw.cancelled[-1] == f"O{len(gw.placed)}"


def test_deadline_passed_places_nothing(cfg):
    gw = FakeGateway()
    assert run(gw, cfg, deadline=NOW - timedelta(seconds=1)) is None
    assert gw.placed == []


def test_bad_side_rejected(cfg):
    gw = FakeGateway()
    with pytest.raises(ValueError):
        run(gw, cfg, side="SHORT")
    assert gw.placed == []


def test_on_placed_awaited_per_order(cfg):
    seen = []

    async def cb(order_id):
        seen.append(order_id)

    run(FakeGateway(), cfg, on_placed=cb)
    assert seen == ["O1", "O2", "O3", "O4"]


def test_leg_cfg_has_exactly_five_attributes(cfg):
    ns = _leg_cfg(cfg, 0.07)
    assert vars(ns) == {
        "limit_buffer_usd": 0.07,
        "poll_interval_s": cfg.poll_interval_seconds,
        "ttl_s": cfg.order_ttl_seconds,
        "escalation_step_usd": cfg.escalation_step_usd,
        "max_retries": cfg.max_reprices,
    }
