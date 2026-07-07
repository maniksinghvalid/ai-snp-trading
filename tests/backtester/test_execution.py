#!/usr/bin/env python3
"""
tests.backtester.test_execution — BT-02: SimulatedExecution N+1-open fill (look-ahead proof).

Wave 0 stub (plan 06-01): `pytest.importorskip` guards this whole module until
backtester/execution.py exists (plan 06-03) — the suite SKIPs cleanly until then, then
these real assertions run RED -> GREEN.

Uses tests.backtester.fixtures.make_ahead_only_5m_dataset(): bar index 3 ("bar N") is the
only bar whose close clears entry conditions (105.00); bar index 4 ("bar N+1") opens at
103.50 -- materially different from bar N's close. A fill correctly using bar N+1's open
(103.50) is trivially distinguishable from a look-ahead bug filling at intent.entry_price
(105.00, bar N's close). Bar index 5 is the LAST bar -- a signal sourced from it has no
next bar, so consume_intent must return None (D-05 abandon), never raise.

Async convention: asyncio.run(coro) inside a plain def test_* (project-wide convention —
no asyncio pytest marker — see 06-RESEARCH Validation Architecture).
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.position.state import PositionPhase
from bot.risk.events import OrderIntent
from bot.signal.events import BarEvent, SignalEvent
from tests.backtester.fixtures import make_ahead_only_5m_dataset

mod = pytest.importorskip("backtester.execution")

SimulatedExecution = mod.SimulatedExecution
SimulatedGateway = mod.SimulatedGateway


class _FakeFeed:
    """Minimal next_bar(code, after) double over the ahead-only fixture bars.

    Deliberately hand-rolled (not backtester.feed.SimulatedBarFeed) so this test file
    depends only on fixtures.py, per 06-01's key_links (test_execution.py -> fixtures.py).
    """

    def __init__(self, bars):
        self._bars = bars

    def next_bar(self, code, after):
        candidates = [b for b in self._bars if b["code"] == code and b["time_key"] > after]
        return min(candidates, key=lambda b: b["time_key"]) if candidates else None


def _make_intent(bar: dict) -> OrderIntent:
    """Build a real OrderIntent whose source_signal.bar is the given signal bar."""
    signal_bar = BarEvent(**bar)
    signal = SignalEvent(
        code=bar["code"], bar=signal_bar, premarket_high=100.0,
        hod=bar["hod"], lod=bar["lod"], rvol=3.0, emitted_at=datetime.now(),
    )
    return OrderIntent(
        code=bar["code"], entry_price=bar["close"], stop_price=bar["low"] * 0.99,
        quantity=10, equity_used=100_000.0, risk_dollars=1_000.0,
        notional=bar["close"] * 10, emitted_at=datetime.now(),
        source_signal=signal, intent_id="INTENT-TEST-1",
    )


def test_consume_intent_fills_at_bar_n_plus_1_open_not_intent_entry_price():
    bars = make_ahead_only_5m_dataset()
    bar_n = bars[3]       # close=105.00 (the signal bar -- would-be look-ahead price)
    bar_n_plus_1 = bars[4]  # open=103.50 (the correct fill price)
    assert bar_n["close"] != bar_n_plus_1["open"], "fixture must have a materially different N+1 open"

    intent = _make_intent(bar_n)
    execution = SimulatedExecution(_FakeFeed(bars), slippage_usd=0.0)

    fill = asyncio.run(execution.consume_intent(intent))

    assert fill is not None
    assert fill.avg_fill_price == bar_n_plus_1["open"], (
        "fill must use bar N+1's open, never intent.entry_price (look-ahead bug)"
    )
    assert fill.avg_fill_price != intent.entry_price
    assert fill.is_entry is True
    assert fill.filled_qty == intent.quantity
    assert fill.code == intent.code


def test_consume_intent_returns_none_when_signal_is_on_the_last_bar():
    bars = make_ahead_only_5m_dataset()
    last_bar = bars[-1]  # no N+1 bar exists after this one

    intent = _make_intent(last_bar)
    execution = SimulatedExecution(_FakeFeed(bars), slippage_usd=0.0)

    fill = asyncio.run(execution.consume_intent(intent))

    assert fill is None, "a signal on the last bar has no next-bar open -- must abandon (D-05), not fill"


def test_manage_exit_fills_at_next_bar_open_and_returns_int_qty():
    bars = make_ahead_only_5m_dataset()
    bar_n = bars[3]         # "current" bar the harness has told execution about via on_bar
    bar_n_plus_1 = bars[4]  # open=103.50 -- the correct symmetric-N+1 exit fill price

    execution = SimulatedExecution(_FakeFeed(bars), slippage_usd=0.0)
    execution.on_bar(bar_n)  # harness wiring: record the latest bar seen for this code

    filled_qty = asyncio.run(
        execution.manage_exit(
            code=bar_n["code"], qty=5, side="SELL",
            escalation_step=0.01, escalation_cadence=1.0, ttl=5.0,
        )
    )

    assert isinstance(filled_qty, int)
    assert filled_qty == 5
    assert len(execution.exit_fills) == 1
    exit_row = execution.exit_fills[0]
    assert exit_row["code"] == bar_n["code"]
    assert exit_row["exit_price"] == bar_n_plus_1["open"]
    assert exit_row["qty"] == 5
    assert exit_row in execution.fills


def test_simulated_gateway_get_positions_excludes_awaiting_fill_and_closed():
    fake_manager = SimpleNamespace(
        _positions={
            "US.ACTIVE1": SimpleNamespace(phase=PositionPhase.ACTIVE),
            "US.CLOSED1": SimpleNamespace(phase=PositionPhase.CLOSED),
        }
    )
    gateway = SimulatedGateway(position_manager_ref=lambda: fake_manager)

    ret, positions_df = asyncio.run(gateway.get_positions())

    assert ret == 0
    assert list(positions_df["code"]) == ["US.ACTIVE1"]


def test_simulated_gateway_get_equity_returns_fixed_100k():
    gateway = SimulatedGateway(position_manager_ref=lambda: None)

    equity = asyncio.run(gateway.get_equity())

    assert equity == 100_000.0
