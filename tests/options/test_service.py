#!/usr/bin/env python3
"""
tests/options/test_service.py — OptionsBot lifecycle, reconcile, formatters, jobs.

The store is a REAL OptionsStore on a tmp-path DB (status transitions are the
thing under test, so a mock would prove nothing); the gateway, alerter and
kill switch are mocks with AsyncMock async methods.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import bot.options.service as service
from bot.options.service import (
    OptionsBot,
    _fmt_entry,
    _fmt_exit,
    _fmt_summary,
    _group_rows_by_underlying,
    _ivp_pct,
    _ivr_pct,
    _options_html,
)
from bot.options.store import OptionsStore


_ET = ZoneInfo("America/New_York")

SHORT_P = "US.SPY260320P600000"
LONG_P = "US.SPY260320P594000"


def _run(coro):
    return asyncio.run(coro)


# ============================================================
# Fixtures / builders
# ============================================================

@pytest.fixture
def store(tmp_path):
    st = OptionsStore(str(tmp_path / "options.db")).open()
    yield st
    st.close()


@pytest.fixture
def alerter():
    return MagicMock(send=AsyncMock(), _enabled=True)


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.connect = MagicMock()
    gw.close = MagicMock()
    gw.get_option_positions = AsyncMock(return_value={})
    gw.get_stock_ids = AsyncMock(return_value={})
    gw.screen_options = AsyncMock(return_value=[])
    gw.get_market_snapshot = AsyncMock(return_value=(0, []))
    return gw


@pytest.fixture
def make_bot(options_cfg, gateway, store, alerter):
    def _make(cfg=None):
        return OptionsBot(
            cfg=cfg or options_cfg,
            gateway=gateway,
            store=store,
            kill_switch=MagicMock(triggered=True, check_file=MagicMock(return_value=False)),
            alerter=alerter,
        )
    return _make


def _pos(position_id="P1", status="OPEN", **over) -> dict:
    pos = {
        "position_id": position_id,
        "underlying": "US.SPY",
        "structure": "put_credit_spread",
        "expiry": "2026-03-20",
        "dte_at_entry": 45,
        "ivr_at_entry": 32.0,
        "credit_per_spread": 2.0,
        "width": 6.0,
        "qty": 2,
        "max_loss_usd": 800.0,
        "status": status,
        "opened_at": "2026-08-17T10:00:00-04:00",
    }
    pos.update(over)
    return pos


def _leg(leg_id, position_id="P1", side="SELL", code=SHORT_P, **over) -> dict:
    leg = {
        "leg_id": leg_id,
        "position_id": position_id,
        "code": code,
        "right": "P",
        "strike": 600.0,
        "side": side,
        "qty": 2,
        "status": "FILLED",
    }
    leg.update(over)
    return leg


def _seed_open_spread(store, position_id="P1", **over):
    store.insert_option_position(_pos(position_id, **over))
    store.insert_option_leg(_leg("L1", position_id, side="BUY", code=LONG_P, strike=594.0))
    store.insert_option_leg(_leg("L2", position_id, side="SELL", code=SHORT_P))


def _statuses(store, status):
    return [p["position_id"] for p in store.get_option_positions((status,))]


# ============================================================
# Reconcile
# ============================================================

def test_reconcile_flags_qty_mismatch_and_alerts_once(make_bot, store, gateway, alerter):
    _seed_open_spread(store)
    # Broker shows only one of the two short contracts.
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -1})
    bot = make_bot()

    _run(bot.reconcile())

    assert _statuses(store, "NEEDS_ATTENTION") == ["P1"]
    assert _statuses(store, "OPEN") == []
    assert alerter.send.await_count == 1

    # Second pass: the row is no longer OPEN, so nothing is re-alerted.
    _run(bot.reconcile())
    assert alerter.send.await_count == 1


def test_reconcile_matching_quantities_leaves_position_open(make_bot, store, gateway, alerter):
    _seed_open_spread(store)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    bot = make_bot()

    _run(bot.reconcile())

    assert _statuses(store, "OPEN") == ["P1"]
    alerter.send.assert_not_awaited()


def test_reconcile_ignores_broker_codes_absent_from_db(make_bot, store, gateway, alerter):
    _seed_open_spread(store)
    gateway.get_option_positions = AsyncMock(return_value={
        LONG_P: 2, SHORT_P: -2,
        "US.AAPL260320C300000": -5,   # the human's own position on the shared account
    })
    bot = make_bot()

    _run(bot.reconcile())

    assert _statuses(store, "OPEN") == ["P1"]
    alerter.send.assert_not_awaited()
    # Nothing was written for the foreign code.
    all_codes = {
        leg["code"]
        for p in store.get_option_positions(("OPEN", "NEEDS_ATTENTION", "CLOSED"))
        for leg in p["legs"]
    }
    assert "US.AAPL260320C300000" not in all_codes


@pytest.mark.parametrize("status", ["OPENING", "CLOSING"])
def test_reconcile_startup_flags_incomplete_row(make_bot, store, gateway, alerter, status):
    _seed_open_spread(store, status=status)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    bot = make_bot()

    _run(bot.reconcile(startup=True))

    assert _statuses(store, "NEEDS_ATTENTION") == ["P1"]
    assert alerter.send.await_count == 1


# ============================================================
# Pure helpers
# ============================================================

def test_iv_helpers_scale_fraction_to_percent_and_pass_none():
    assert _ivr_pct({"u_iv_rank": 0.066}) == pytest.approx(6.6)
    assert _ivp_pct({"u_iv_percentile": 0.5}) == pytest.approx(50.0)
    assert _ivr_pct({"u_iv_rank": None}) is None
    assert _ivp_pct({}) is None


def test_group_rows_by_underlying_drops_rows_without_stock_id():
    rows = [{"u_stock_id": 1, "code": "a"}, {"u_stock_id": None, "code": "b"},
            {"u_stock_id": 1, "code": "c"}]
    grouped = _group_rows_by_underlying(rows)
    assert list(grouped) == [1]
    assert [r["code"] for r in grouped[1]] == ["a", "c"]


def test_formatters_escape_free_text_fields():
    pos = _pos(underlying="<script>x</script>", structure="iron_condor & co")
    legs = [{"side": "SELL", "code": "<b>evil</b>", "strike": 600.0}]

    entry = _fmt_entry(pos, legs)
    assert "<script>" not in entry
    assert "&lt;script&gt;" in entry
    assert "&lt;b&gt;evil&lt;/b&gt;" in entry
    assert "&amp;" in entry

    exit_txt = _fmt_exit(pos, "<i>profit</i>", 120.0, 50.0)
    assert "<i>profit</i>" not in exit_txt
    assert "+120.00" in exit_txt

    summary = _fmt_summary([pos], [], -50.0)
    assert "<script>" not in summary
    assert "-50.00" in summary

    doc = _options_html([pos], [{**pos, "close_reason": "<x>", "realized_pnl_usd": 1.0}],
                        "2026-08-17")
    assert "<script>" not in doc
    assert "&lt;x&gt;" in doc


# ============================================================
# Session window
# ============================================================

def _freeze(monkeypatch, when, trading=True):
    monkeypatch.setattr(service, "now_et", lambda: when)
    monkeypatch.setattr(service, "is_trading_day", lambda d: trading)
    monkeypatch.setattr(service, "get_market_close_et", lambda d: "16:00")


def test_is_rth_now_false_on_non_trading_day(make_bot, monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 15, 12, 0, tzinfo=_ET), trading=False)
    assert make_bot()._is_rth_now() is False


def test_is_rth_now_false_before_manage_start(make_bot, monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 17, 9, 34, tzinfo=_ET))
    assert make_bot()._is_rth_now() is False


def test_is_rth_now_false_inside_close_buffer(make_bot, monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 17, 15, 56, tzinfo=_ET))
    assert make_bot()._is_rth_now() is False


def test_is_rth_now_true_mid_session(make_bot, monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 17, 12, 0, tzinfo=_ET))
    assert make_bot()._is_rth_now() is True
