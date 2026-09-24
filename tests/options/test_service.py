#!/usr/bin/env python3
"""
tests/options/test_service.py — OptionsBot lifecycle, reconcile, formatters, jobs.

The store is a REAL OptionsStore on a tmp-path DB (status transitions are the
thing under test, so a mock would prove nothing); the gateway, alerter and
kill switch are mocks with AsyncMock async methods.
"""
import asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import bot.options.service as service
from bot.options.config import load_options_book
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
REPO_ROOT = Path(__file__).resolve().parents[2]

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
    def _make(cfg=None, strategies=None):
        return OptionsBot(
            cfg=cfg or options_cfg,
            gateway=gateway,
            store=store,
            kill_switch=MagicMock(triggered=True, check_file=MagicMock(return_value=False)),
            alerter=alerter,
            strategies=strategies,
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


def _seed_open_spread(store, position_id="P1", qty=2, **over):
    store.insert_option_position(_pos(position_id, qty=qty, **over))
    store.insert_option_leg(
        _leg(f"{position_id}-L1", position_id, side="BUY", code=LONG_P,
             strike=594.0, qty=qty)
    )
    store.insert_option_leg(
        _leg(f"{position_id}-L2", position_id, side="SELL", code=SHORT_P, qty=qty)
    )


def _statuses(store, status):
    return [p["position_id"] for p in store.get_option_positions((status,))]


# NVDA worked example (CONTEXT.md "Specific Ideas"): buy 225 @ 3.58, sell 235
# @ 1.62 -> debit 1.96, width 10.0, max profit 8.04.
LONG_C = "US.NVDA260918C225000"
SHORT_C = "US.NVDA260918C235000"


def _seed_bull_spread(store, position_id="B1", qty=5, **over) -> None:
    pos = {
        "position_id": position_id,
        "underlying": "US.NVDA",
        "structure": "bull_call_spread",
        "strategy_name": "super_bull_call",
        "expiry": "2026-09-18",
        "credit_per_spread": -1.96,
        "width": 10.0,
        "qty": qty,
        "max_loss_usd": 980.0,
        "status": "OPEN",
    }
    pos.update(over)
    store.insert_option_position(pos)
    store.insert_option_leg(
        _leg(f"{position_id}-L1", position_id, side="BUY", code=LONG_C,
             right="C", strike=225.0, qty=qty)
    )
    store.insert_option_leg(
        _leg(f"{position_id}-L2", position_id, side="SELL", code=SHORT_C,
             right="C", strike=235.0, qty=qty)
    )


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


def test_reconcile_startup_flags_unknown_strategy(make_bot, store, gateway, alerter, monkeypatch):
    audit_events = []
    monkeypatch.setattr(service, "append_audit", audit_events.append)
    _seed_open_spread(store, "R1", strategy_name="retired_strategy")
    _seed_open_spread(store, "T1")
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    bot = make_bot()

    _run(bot.reconcile(startup=True))

    assert _statuses(store, "NEEDS_ATTENTION") == ["R1"]
    assert _statuses(store, "OPEN") == ["T1"]
    assert alerter.send.await_count == 1
    assert "retired_strategy" in alerter.send.await_args[0][0]
    unknown = [e for e in audit_events if e["event"] == "options_unknown_strategy"]
    assert len(unknown) == 1
    assert unknown[0]["strategy_name"] == "retired_strategy"


def test_reconcile_steady_state_leaves_unknown_strategy_for_startup_only(
    make_bot, store, gateway, alerter,
):
    _seed_open_spread(store, "R1", strategy_name="retired_strategy")
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    bot = make_bot()

    _run(bot.reconcile())

    assert _statuses(store, "OPEN") == ["R1"]
    alerter.send.assert_not_awaited()


# ============================================================
# Pure helpers
# ============================================================

def test_iv_helpers_scale_fraction_to_percent_and_pass_none():
    assert _ivr_pct({"u_iv_rank": 0.066}) == pytest.approx(6.6)
    assert _ivp_pct({"u_iv_percentile": 0.5}) == pytest.approx(50.0)
    assert _ivr_pct({"u_iv_rank": None}) is None


def test_change_pct_scales_fraction_to_percent():
    """Verified live 2026-08-17: u_change_ratio 0.01392 == +1.39% (a fraction)."""
    from bot.options.service import _change_pct
    assert _change_pct({"u_change_ratio": 0.01392}) == pytest.approx(1.392)
    assert _change_pct({"u_change_ratio": -0.025}) == pytest.approx(-2.5)
    assert _change_pct({"u_change_ratio": "N/A"}) is None
    assert _change_pct({"u_change_ratio": None}) is None


def test_entry_scan_fear_knob_uses_percent_change(make_bot, store, gateway, monkeypatch):
    """IVR 22% fails ivr_min=30 but a -2.5% day (fraction -0.025 from the SDK)
    lowers the gate to fear_ivr_min=20 → the scan proceeds to build a spread."""
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch, ivr_frac=0.22, change_ratio=-0.025)
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan())

    assert _statuses(store, "OPEN") != []
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


# ============================================================
# Job registration
# ============================================================

def test_register_jobs_registers_the_four_job_ids(make_bot):
    bot = make_bot()
    bot._register_jobs()
    assert {j.id for j in bot._scheduler.get_jobs()} == {
        "options_entry_scan_tasty_credit_spreads",
        "options_entry_scan_tasty_credit_spreads_2",
        "options_manage", "options_eod",
    }


def test_register_jobs_skips_second_scan_when_unset(make_bot, options_cfg):
    from dataclasses import replace
    bot = make_bot(replace(options_cfg, second_entry_scan_et=None))
    bot._register_jobs()
    ids = {j.id for j in bot._scheduler.get_jobs()}
    assert "options_entry_scan_tasty_credit_spreads_2" not in ids
    assert "options_entry_scan_tasty_credit_spreads" in ids


def test_register_jobs_one_entry_scan_per_strategy(make_bot, options_book):
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    bot._register_jobs()
    jobs = {j.id: j for j in bot._scheduler.get_jobs()}
    assert set(jobs) == {
        "options_entry_scan_tasty_credit_spreads",
        "options_entry_scan_tasty_credit_spreads_2",
        "options_entry_scan_super_bull_call",
        "options_manage", "options_eod",
    }
    bull_job = jobs["options_entry_scan_super_bull_call"]
    assert bull_job.args == ("super_bull_call",)
    assert "hour='10'" in str(bull_job.trigger)
    assert "minute='5'" in str(bull_job.trigger)


# ============================================================
# Entry scan
# ============================================================

TODAY = date(2026, 8, 17)
EXPIRY = "2026-10-01"          # 45 DTE from TODAY — the configured target
SESSION_NOON = datetime(2026, 8, 17, 12, 0, tzinfo=_ET)

# strike -> (right, delta, bid, ask). The short strikes sit on the configured
# 0.16 delta; the wings are one 6.00-wide listed strike away (1% of a 600
# underlying), and every quote clears the conftest liquidity gate.
_GRID = {
    594.0: ("P", -0.06, 0.50, 0.54),
    600.0: ("P", -0.16, 2.00, 2.04),
    610.0: ("C", 0.16, 1.00, 1.04),
    616.0: ("C", 0.06, 0.20, 0.24),
}


def _chain(stock_id=1, u_price=600.0, ivr_frac=0.35, change_ratio=0.0, right=None):
    """Build screen_options rows for one underlying (optionally one right)."""
    rows = []
    for strike, (r, delta, bid, ask) in _GRID.items():
        if right is not None and r != right:
            continue
        rows.append({
            "code": f"US.SPY261001{r}{int(strike * 1000)}",
            "right": r, "strike": strike, "expiry": EXPIRY, "dte": 45,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "iv": 0.2, "open_interest": 5000, "delta": delta,
            "u_stock_id": stock_id, "u_price": u_price, "u_iv": 0.2,
            "u_iv_rank": ivr_frac, "u_iv_percentile": ivr_frac,
            "u_change_ratio": change_ratio,
        })
    return rows


def _wire_scan(bot, gateway, monkeypatch, **chain_kw):
    """Point the gateway at a one-underlying chain and freeze the clock."""
    _freeze(monkeypatch, SESSION_NOON)
    gateway.get_stock_ids = AsyncMock(return_value={"US.SPY": 1})
    gateway.screen_options = AsyncMock(
        side_effect=lambda ids, right, *a, **k: _chain(right=right, **chain_kw)
    )
    bot._entries_enabled = True
    bot._kill_switch.triggered = False


def _fake_executor(legs_filled=True, close_ok=True, exit_prices=None):
    """Executor double that drives the persistence callbacks like the real one."""
    async def _open(legs, qty, quotes, on_leg_placed=None, on_leg_filled=None):
        out = []
        for i, leg in enumerate(legs):
            if on_leg_placed is not None:
                await on_leg_placed(leg, f"OID{i}")
            if on_leg_filled is not None:
                await on_leg_filled(leg, f"OID{i}", leg["mid"], qty)
            out.append({**leg, "order_id": f"OID{i}", "entry_price": leg["mid"],
                        "filled_qty": qty})
        return out if legs_filled else None

    async def _close(legs, quotes, aggressive=False, on_leg_placed=None, on_leg_filled=None):
        for i, leg in enumerate(legs):
            if on_leg_placed is not None:
                await on_leg_placed(leg, f"XOID{i}")
            if close_ok and on_leg_filled is not None:
                price = (exit_prices or {}).get(leg["code"], 0.0)
                await on_leg_filled(leg, f"XOID{i}", price, leg["qty"])
        return close_ok

    return MagicMock(open_position=AsyncMock(side_effect=_open),
                     close_legs=AsyncMock(side_effect=_close))


def test_entry_scan_skips_entirely_when_breaker_tripped(make_bot, store, gateway, monkeypatch):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    store.set_meta("options_breaker_date", TODAY.isoformat())

    _run(bot._job_entry_scan())

    gateway.screen_options.assert_not_awaited()
    assert store.get_option_positions(("OPENING", "OPEN")) == []


def test_entry_scan_trips_breaker_on_realized_loss_with_empty_book(
    make_bot, store, gateway, alerter, monkeypatch,
):
    """A bad morning that closed everything at a loss must block the afternoon
    scan even though no manage tick ran with an OPEN position (or the bot
    restarted): the scan guard evaluates realized P&L itself."""
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    store.insert_option_position(_pos(
        "OLD", status="CLOSED", underlying="US.QQQ",
        closed_at=f"{TODAY.isoformat()}T11:00:00-04:00",
        close_reason="stop_loss", realized_pnl_usd=-2500.0,
    ))

    _run(bot._job_entry_scan())

    assert store.get_meta("options_breaker_date") == TODAY.isoformat()
    gateway.screen_options.assert_not_awaited()
    assert alerter.send.await_count == 1
    assert "daily loss limit" in alerter.send.await_args[0][0]


def test_manage_arms_breaker_with_empty_book(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    store.insert_option_position(_pos(
        "OLD", status="CLOSED", underlying="US.QQQ",
        closed_at=f"{TODAY.isoformat()}T11:00:00-04:00",
        close_reason="stop_loss", realized_pnl_usd=-2500.0,
    ))
    gateway.get_option_positions = AsyncMock(return_value={})
    bot = make_bot()

    _run(bot._job_manage())

    assert store.get_meta("options_breaker_date") == TODAY.isoformat()
    gateway.get_market_snapshot.assert_not_awaited()


def test_entry_scan_blocked_by_kill_switch(make_bot, store, gateway, monkeypatch):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._kill_switch.triggered = True

    _run(bot._job_entry_scan())

    gateway.screen_options.assert_not_awaited()
    assert store.get_option_positions(("OPENING", "OPEN")) == []


def test_entry_scan_blocked_before_readiness_gate(make_bot, store, gateway, monkeypatch):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._entries_enabled = False       # readiness gate has not passed yet

    _run(bot._job_entry_scan())

    gateway.screen_options.assert_not_awaited()
    assert store.get_option_positions(("OPENING", "OPEN")) == []


def test_entry_scan_returns_immediately_off_a_trading_day(make_bot, gateway, monkeypatch):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    monkeypatch.setattr(service, "is_trading_day", lambda d: False)

    _run(bot._job_entry_scan())

    gateway.get_stock_ids.assert_not_awaited()
    gateway.screen_options.assert_not_awaited()


def test_entry_scan_opens_position_and_persists_leg_progress(
    make_bot, store, gateway, alerter, monkeypatch,
):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan())

    opened = store.get_option_positions(("OPEN",))
    assert len(opened) == 1
    pos = opened[0]
    assert pos["underlying"] == "US.SPY"
    assert pos["structure"] == "iron_condor"
    assert pos["expiry"] == EXPIRY
    assert pos["dte_at_entry"] == 45
    assert pos["qty"] == 2
    assert pos["ivr_at_entry"] == pytest.approx(35.0)      # 0.35 fraction -> percent
    assert pos["credit_per_spread"] == pytest.approx(2.30)
    assert pos["width"] == pytest.approx(6.0)
    assert pos["max_loss_usd"] == pytest.approx((6.0 - 2.30) * 100 * 2)

    # Long wings first, and every leg walked PENDING -> WORKING -> FILLED.
    assert [leg["side"] for leg in pos["legs"]] == ["BUY", "BUY", "SELL", "SELL"]
    assert {leg["status"] for leg in pos["legs"]} == {"FILLED"}
    assert all(leg["entry_order_id"] for leg in pos["legs"])
    assert all(leg["entry_price"] is not None for leg in pos["legs"])

    assert alerter.send.await_count == 1
    assert "Options entry" in alerter.send.await_args[0][0]


def test_entry_scan_skips_underlying_that_already_has_a_position(
    make_bot, store, gateway, monkeypatch,
):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    _seed_open_spread(store, "EXISTING")

    _run(bot._job_entry_scan())

    assert [p["position_id"] for p in store.get_option_positions(("OPEN",))] == ["EXISTING"]
    bot._executor.open_position.assert_not_awaited()


def test_entry_scan_respects_per_day_cap(make_bot, store, gateway, monkeypatch):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    # cfg.max_new_positions_per_day == 2: two rows already opened today.
    for pid in ("A", "B"):
        store.insert_option_position(
            _pos(pid, status="CLOSED", underlying=f"US.{pid}",
                 opened_at=f"{TODAY.isoformat()}T10:00:00-04:00")
        )

    _run(bot._job_entry_scan())

    gateway.screen_options.assert_not_awaited()
    bot._executor.open_position.assert_not_awaited()


def test_entry_scan_aborts_position_when_open_fails(
    make_bot, store, gateway, alerter, monkeypatch,
):
    bot = make_bot()
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor(legs_filled=False)

    _run(bot._job_entry_scan())

    assert store.get_option_positions(("OPEN",)) == []
    aborted = store.get_option_positions(("ABORTED",))
    assert len(aborted) == 1
    assert aborted[0]["close_reason"] == "open_failed"
    assert aborted[0]["closed_at"]
    assert alerter.send.await_count == 1
    assert "entry failed" in alerter.send.await_args[0][0]


# ============================================================
# Multi-strategy entry scan (bull call / per-strategy dispatch)
# ============================================================

# NVDA worked example (CONTEXT.md "Specific Ideas"): buy 225 @ 3.58, sell 235
# @ 1.62 -> debit 1.96, width 10.0, 1/4-rule debit/width 0.196 <= 0.30 passes.
_NVDA_CALLS = {
    215.0: (0.45, 7.40, 7.44),
    220.0: (0.38, 5.20, 5.24),
    225.0: (0.30, 3.56, 3.60),
    230.0: (0.24, 2.48, 2.52),
    235.0: (0.18, 1.60, 1.64),
    240.0: (0.12, 1.00, 1.04),
}


def _bull_chain(stock_id=7, root="NVDA", u_price=222.0, ivr_frac=0.05):
    """Build screen_options call rows for one bull-call underlying."""
    rows = []
    for strike, (delta, bid, ask) in _NVDA_CALLS.items():
        rows.append({
            "code": f"US.{root}260918C{int(strike * 1000)}",
            "right": "C", "strike": strike, "expiry": "2026-09-18", "dte": 32,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "iv": 0.2, "open_interest": 5000, "delta": delta,
            "u_stock_id": stock_id, "u_price": u_price, "u_iv": 0.2,
            "u_iv_rank": ivr_frac, "u_iv_percentile": ivr_frac,
            "u_change_ratio": 0.0,
        })
    return rows


def _wire_bull(bot, gateway, monkeypatch, codes=("US.NVDA",), stock_ids=None, chains=None):
    """Point the gateway/reader at a watchlist + call chain(s); freeze the clock.

    Monkeypatches `service.read_equity_watchlist` with a recorder that returns
    `list(codes)`; returns the recorder's (db_path, scan_date_iso) call list.
    """
    _freeze(monkeypatch, SESSION_NOON)
    stock_ids = stock_ids if stock_ids is not None else {c: 7 + i for i, c in enumerate(codes)}
    chains = chains if chains is not None else {
        stock_ids[c]: _bull_chain(stock_id=stock_ids[c], root=c.split(".")[-1])
        for c in codes
    }

    calls = []

    def _reader(db_path, scan_date_iso, *a, **k):
        calls.append((db_path, scan_date_iso))
        return list(codes)

    monkeypatch.setattr(service, "read_equity_watchlist", _reader)
    gateway.get_stock_ids = AsyncMock(return_value=dict(stock_ids))
    gateway.screen_options = AsyncMock(
        side_effect=lambda ids, right, *a, **k: [
            row for sid in ids for row in chains.get(sid, [])
        ]
    )
    bot._entries_enabled = True
    bot._kill_switch.triggered = False
    return calls


def test_bull_call_entry_opens_debit_spread_from_watchlist(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    calls = _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan("super_bull_call"))

    opened = store.get_option_positions(("OPEN",))
    assert len(opened) == 1
    pos = opened[0]
    assert pos["strategy_name"] == "super_bull_call"
    assert pos["structure"] == "bull_call_spread"
    assert pos["underlying"] == "US.NVDA"
    assert pos["credit_per_spread"] == pytest.approx(-1.96)
    assert pos["width"] == pytest.approx(10.0)
    assert pos["qty"] == 5
    assert pos["max_loss_usd"] == pytest.approx(980.0)
    assert [leg["side"] for leg in pos["legs"]] == ["BUY", "SELL"]
    assert [leg["strike"] for leg in pos["legs"]] == [225.0, 235.0]

    gateway.screen_options.assert_awaited_once_with([7], "C", 21, 45, 0.05, 0.50)
    assert calls == [("data/bot_state.db", "2026-08-17")]

    assert alerter.send.await_count == 1
    body = alerter.send.await_args[0][0]
    assert "super_bull_call" in body
    assert "debit 1.96" in body


def test_bull_call_entry_empty_watchlist_opens_nothing(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch, codes=())
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan("super_bull_call"))

    gateway.get_stock_ids.assert_not_awaited()
    gateway.screen_options.assert_not_awaited()
    assert store.get_option_positions(("OPEN",)) == []


def test_bull_call_entry_iterates_watchlist_in_rank_order(
    options_book, make_bot, store, gateway, monkeypatch,
):
    from dataclasses import replace
    bull_cfg = replace(options_book.strategies[1], max_new_positions_per_day=1)
    strategies = (options_book.strategies[0], bull_cfg)
    bot = make_bot(bull_cfg, strategies=strategies)
    _wire_bull(
        bot, gateway, monkeypatch,
        codes=("US.BBB", "US.AAA"),
        stock_ids={"US.BBB": 9, "US.AAA": 3},
    )
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan("super_bull_call"))

    opened = store.get_option_positions(("OPEN",))
    assert [p["underlying"] for p in opened] == ["US.BBB"]


def test_credit_entry_unchanged_with_two_strategy_book(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()

    _run(bot._job_entry_scan("tasty_credit_spreads"))

    opened = store.get_option_positions(("OPEN",))
    assert len(opened) == 1
    pos = opened[0]
    assert pos["strategy_name"] == "tasty_credit_spreads"
    assert pos["structure"] == "iron_condor"
    assert pos["expiry"] == EXPIRY
    assert pos["credit_per_spread"] == pytest.approx(2.30)
    assert pos["width"] == pytest.approx(6.0)
    assert pos["qty"] == 2
    assert [leg["side"] for leg in pos["legs"]] == ["BUY", "BUY", "SELL", "SELL"]


# ============================================================
# Per-strategy caps vs global breaker/BP/one-per-underlying (D-22)
# ============================================================

def test_bull_call_per_day_cap_is_per_strategy(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    for pid in ("A", "B"):
        store.insert_option_position(_pos(
            pid, status="CLOSED", underlying=f"US.{pid}",
            opened_at=f"{TODAY.isoformat()}T10:00:00-04:00",
            strategy_name="tasty_credit_spreads",
        ))

    _run(bot._job_entry_scan("super_bull_call"))

    opened = [p for p in store.get_option_positions(("OPEN",))
              if p["strategy_name"] == "super_bull_call"]
    assert [p["underlying"] for p in opened] == ["US.NVDA"]


def test_credit_per_day_cap_ignores_other_strategies(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    for pid in ("A", "B"):
        store.insert_option_position(_pos(
            pid, status="CLOSED", underlying=f"US.{pid}",
            opened_at=f"{TODAY.isoformat()}T10:00:00-04:00",
            strategy_name="super_bull_call",
        ))

    _run(bot._job_entry_scan("tasty_credit_spreads"))

    opened = [p for p in store.get_option_positions(("OPEN",))
              if p["strategy_name"] == "tasty_credit_spreads"]
    assert [p["underlying"] for p in opened] == ["US.SPY"]


def test_bull_call_concurrent_cap_is_per_strategy(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    for i in range(4):
        _seed_open_spread(
            store, f"B{i}", strategy_name="super_bull_call",
            underlying=f"US.OTH{i}", max_loss_usd=100.0,
        )

    _run(bot._job_entry_scan("super_bull_call"))

    bot._executor.open_position.assert_not_awaited()
    assert "US.NVDA" not in {p["underlying"] for p in store.get_option_positions(("OPEN",))}


def test_credit_concurrent_cap_ignores_other_strategies(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    _wire_scan(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    for i in range(8):
        _seed_open_spread(
            store, f"B{i}", strategy_name="super_bull_call",
            underlying=f"US.OTH{i}", max_loss_usd=100.0,
        )

    _run(bot._job_entry_scan("tasty_credit_spreads"))

    opened = [p for p in store.get_option_positions(("OPEN",))
              if p["strategy_name"] == "tasty_credit_spreads"]
    assert [p["underlying"] for p in opened] == ["US.SPY"]


def test_one_position_per_underlying_is_global(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    _seed_open_spread(
        store, "T1", strategy_name="tasty_credit_spreads", underlying="US.NVDA",
    )

    _run(bot._job_entry_scan("super_bull_call"))

    bot._executor.open_position.assert_not_awaited()
    assert [p["strategy_name"] for p in store.get_option_positions(("OPEN",))
            if p["underlying"] == "US.NVDA"] == ["tasty_credit_spreads"]


def test_bp_headroom_is_global(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    _seed_open_spread(
        store, "T1", strategy_name="tasty_credit_spreads",
        underlying="US.QQQ", max_loss_usd=24900.0,
    )

    _run(bot._job_entry_scan("super_bull_call"))

    bot._executor.open_position.assert_not_awaited()
    assert "US.NVDA" not in {p["underlying"] for p in store.get_option_positions(("OPEN",))}


def test_daily_breaker_blocks_every_strategy(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    calls = _wire_bull(bot, gateway, monkeypatch)
    store.set_meta("options_breaker_date", TODAY.isoformat())

    _run(bot._job_entry_scan("super_bull_call"))

    assert calls == []
    gateway.screen_options.assert_not_awaited()


# ============================================================
# Manage
# ============================================================

def _snapshot(gateway, quotes):
    rows = [{"code": code, "bid_price": q["bid"], "ask_price": q["ask"]}
            for code, q in quotes.items()]
    gateway.get_market_snapshot = AsyncMock(return_value=(0, rows))


def test_manage_closes_on_profit_target_and_records_realized(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1", qty=2, expiry=EXPIRY, credit_per_spread=2.0)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    # mark = 0.60 - 0.20 = 0.40 → 80% of the 2.00 credit captured.
    _snapshot(gateway, {SHORT_P: {"bid": 0.55, "ask": 0.65},
                        LONG_P: {"bid": 0.15, "ask": 0.25}})
    bot = make_bot()
    bot._executor = _fake_executor(exit_prices={SHORT_P: 0.60, LONG_P: 0.20})

    _run(bot._job_manage())

    closed = store.get_option_positions(("CLOSED",))
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "profit_target"
    # (2.00 credit - 0.40 net exit) * 100 * 2 spreads
    assert closed[0]["realized_pnl_usd"] == pytest.approx(320.0)
    assert {leg["status"] for leg in closed[0]["legs"]} == {"CLOSED"}
    assert alerter.send.await_count == 1
    assert "Options exit" in alerter.send.await_args[0][0]


def test_manage_flags_needs_attention_when_close_incomplete(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1", qty=2, expiry=EXPIRY, credit_per_spread=2.0)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    _snapshot(gateway, {SHORT_P: {"bid": 0.55, "ask": 0.65},
                        LONG_P: {"bid": 0.15, "ask": 0.25}})
    bot = make_bot()
    bot._executor = _fake_executor(close_ok=False)

    _run(bot._job_manage())

    assert _statuses(store, "NEEDS_ATTENTION") == ["P1"]
    assert store.get_option_positions(("CLOSED",)) == []
    assert alerter.send.await_count == 1
    assert "NEEDS ATTENTION" in alerter.send.await_args[0][0]


def test_manage_trips_daily_loss_breaker_once(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    # A held position (mark 1.90 vs 2.00 credit → no exit decision) plus a
    # realized loss beyond 2% of the 100k sizing equity.
    _seed_open_spread(store, "P1", qty=1, expiry=EXPIRY, credit_per_spread=2.0)
    store.insert_option_position(_pos(
        "OLD", status="CLOSED", underlying="US.QQQ",
        closed_at=f"{TODAY.isoformat()}T11:00:00-04:00",
        close_reason="stop_loss", realized_pnl_usd=-2500.0,
    ))
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 1, SHORT_P: -1})
    _snapshot(gateway, {SHORT_P: {"bid": 2.05, "ask": 2.15},
                        LONG_P: {"bid": 0.15, "ask": 0.25}})
    bot = make_bot()
    bot._executor = _fake_executor()

    _run(bot._job_manage())

    assert store.get_meta("options_breaker_date") == TODAY.isoformat()
    assert _statuses(store, "OPEN") == ["P1"]          # still held, not closed
    assert alerter.send.await_count == 1
    assert "daily loss limit" in alerter.send.await_args[0][0]

    _run(bot._job_manage())                            # second tick: no re-alert
    assert alerter.send.await_count == 1


# ============================================================
# Multi-strategy manage dispatch (D-15, D-19, D-21, D-29)
# ============================================================

def test_manage_closes_bull_call_at_profit_target_of_max(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_bull_spread(store)
    gateway.get_option_positions = AsyncMock(return_value={LONG_C: 5, SHORT_C: -5})
    _snapshot(gateway, {LONG_C: {"bid": 7.95, "ask": 8.05},
                        SHORT_C: {"bid": 1.15, "ask": 1.25}})
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor(exit_prices={LONG_C: 8.00, SHORT_C: 1.20})

    _run(bot._job_manage())

    closed = store.get_option_positions(("CLOSED",))
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "profit_target"
    assert closed[0]["realized_pnl_usd"] == pytest.approx(2420.0)
    assert alerter.send.await_count == 1
    body = alerter.send.await_args[0][0]
    assert "super_bull_call" in body
    assert "of max profit" in body
    assert "60.2" in body


def test_manage_bull_call_never_stops_out(
    options_book, make_bot, store, gateway, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_bull_spread(store)
    gateway.get_option_positions = AsyncMock(return_value={LONG_C: 5, SHORT_C: -5})
    # Spread worth 0.25 vs the 1.96 paid -- a deep loss, still no stop-loss branch.
    _snapshot(gateway, {LONG_C: {"bid": 0.25, "ask": 0.35},
                        SHORT_C: {"bid": 0.04, "ask": 0.06}})
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor()

    _run(bot._job_manage())

    assert _statuses(store, "OPEN") == ["B1"]
    bot._executor.close_legs.assert_not_awaited()


def test_manage_bull_call_assignment_guard(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_bull_spread(store, expiry="2026-08-18")   # 1 DTE on 2026-08-17
    gateway.get_option_positions = AsyncMock(return_value={LONG_C: 5, SHORT_C: -5})
    _snapshot(gateway, {LONG_C: {"bid": 3.56, "ask": 3.60},
                        SHORT_C: {"bid": 1.60, "ask": 1.64}})
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor(exit_prices={LONG_C: 3.58, SHORT_C: 1.62})

    _run(bot._job_manage())

    closed = store.get_option_positions(("CLOSED",))
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "assignment_guard"
    assert bot._executor.close_legs.await_args.kwargs["aggressive"] is True


def test_manage_dispatches_each_position_to_its_own_strategy(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1", qty=2, expiry=EXPIRY, credit_per_spread=2.0)
    _seed_bull_spread(store, "B1")
    gateway.get_option_positions = AsyncMock(return_value={
        LONG_P: 2, SHORT_P: -2, LONG_C: 5, SHORT_C: -5,
    })
    _snapshot(gateway, {
        SHORT_P: {"bid": 2.05, "ask": 2.15}, LONG_P: {"bid": 0.15, "ask": 0.25},
        LONG_C: {"bid": 7.95, "ask": 8.05}, SHORT_C: {"bid": 1.15, "ask": 1.25},
    })
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    bot._executor = _fake_executor(exit_prices={LONG_C: 8.00, SHORT_C: 1.20})

    _run(bot._job_manage())

    assert _statuses(store, "OPEN") == ["P1"]
    closed = store.get_option_positions(("CLOSED",))
    assert [p["position_id"] for p in closed] == ["B1"]
    assert closed[0]["close_reason"] == "profit_target"


def test_manage_skips_position_with_unknown_strategy(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "R1", strategy_name="retired_strategy")
    bot = make_bot()
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]
    quotes = {SHORT_P: {"bid": 0.55, "ask": 0.65}, LONG_P: {"bid": 0.15, "ask": 0.25}}

    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["R1"]


# ============================================================
# CR-01 / Q-01: quote validity in the shared manage path
# ============================================================

@pytest.mark.parametrize("q, expected", [
    (None, False),
    ({}, False),
    ({"bid": None, "ask": 1.0}, False),
    ({"bid": "", "ask": 1.0}, False),
    ({"bid": "N/A", "ask": 1.0}, False),
    ({"bid": 1.0, "ask": "N/A"}, False),
    ({"bid": float("nan"), "ask": 1.0}, False),
    ({"bid": 1.0, "ask": float("inf")}, False),
    ({"bid": "abc", "ask": 1.0}, False),
    ({"bid": 0, "ask": 0}, False),
    ({"bid": 0.30, "ask": 0.20}, False),
    ({"bid": -0.05, "ask": 0.10}, False),
    ({"bid": 0.55, "ask": 0.65}, True),
    ({"bid": 0, "ask": 0.05}, True),
    ({"bid": 1.0, "ask": 1.0}, True),
    ({"bid": "0.55", "ask": "0.65"}, True),
])
def test_quote_ok_accepts_only_two_sided_numeric_quotes(q, expected):
    assert service._quote_ok(q) is expected


@pytest.mark.parametrize("bad_long_quote", [
    {"bid": "N/A", "ask": "N/A"},
    None,
    {"bid": 0, "ask": 0},
    {"bid": 0.30, "ask": 0.20},
], ids=["na_bid", "missing_leg", "zero_quote", "crossed"])
def test_manage_skips_credit_position_on_invalid_quote(
    make_bot, store, monkeypatch, bad_long_quote,
):
    log = MagicMock()
    monkeypatch.setattr(service, "_logger", log)
    _seed_open_spread(store, "P1", qty=2, expiry=EXPIRY, credit_per_spread=2.0)
    bot = make_bot()
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]

    quotes = {SHORT_P: {"bid": 0.55, "ask": 0.65}}
    if bad_long_quote is not None:
        quotes[LONG_P] = bad_long_quote

    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["P1"]
    log.warning.assert_any_call(
        "options_manage_missing_quote", position_id="P1", codes=[LONG_P],
    )


@pytest.mark.parametrize("long_quote, short_quote, bad_code", [
    ({"bid": "N/A", "ask": "N/A"}, {"bid": 1.15, "ask": 1.25}, LONG_C),
    ({"bid": 7.95, "ask": 8.05}, {"bid": "N/A", "ask": "N/A"}, SHORT_C),
], ids=["long_na", "short_na"])
def test_manage_skips_debit_position_on_invalid_quote(
    options_book, make_bot, store, monkeypatch, long_quote, short_quote, bad_code,
):
    log = MagicMock()
    monkeypatch.setattr(service, "_logger", log)
    _seed_bull_spread(store)
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]

    quotes = {LONG_C: long_quote, SHORT_C: short_quote}
    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["B1"]
    log.warning.assert_any_call(
        "options_manage_missing_quote", position_id="B1", codes=[bad_code],
    )


def test_manage_invalid_long_quote_does_not_trip_breaker(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_bull_spread(store, qty=10, max_loss_usd=1960.0)
    gateway.get_option_positions = AsyncMock(return_value={LONG_C: 10, SHORT_C: -10})
    _snapshot(gateway, {LONG_C: {"bid": "N/A", "ask": "N/A"},
                        SHORT_C: {"bid": 1.15, "ask": 1.25}})
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor()

    _run(bot._job_manage())

    assert store.get_meta("options_breaker_date") is None
    alerter.send.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["B1"]


def test_manage_close_exception_flags_needs_attention(
    make_bot, store, gateway, alerter, monkeypatch,
):
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1", qty=2, expiry=EXPIRY, credit_per_spread=2.0)
    audit_events = []
    monkeypatch.setattr(service, "append_audit", audit_events.append)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})
    _snapshot(gateway, {SHORT_P: {"bid": 0.55, "ask": 0.65},
                        LONG_P: {"bid": 0.15, "ask": 0.25}})
    bot = make_bot()
    bot._executor = MagicMock(
        close_legs=AsyncMock(side_effect=ValueError("could not convert string to float: 'N/A'")),
    )

    _run(bot._job_manage())

    assert _statuses(store, "NEEDS_ATTENTION") == ["P1"]
    assert _statuses(store, "CLOSING") == []
    assert alerter.send.await_count == 1
    assert "NEEDS ATTENTION" in alerter.send.await_args[0][0]
    incomplete = [e for e in audit_events if e["event"] == "options_close_incomplete"]
    assert len(incomplete) == 1
    assert incomplete[0]["position_id"] == "P1"


def test_manage_unquotable_leg_inside_guard_window_flags_needs_attention(
    options_book, make_bot, store, alerter, monkeypatch,
):
    audit_events = []
    monkeypatch.setattr(service, "append_audit", audit_events.append)
    _seed_bull_spread(store, expiry="2026-08-18")   # 1 DTE on TODAY
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]

    quotes = {LONG_C: {"bid": 3.56, "ask": 3.60}, SHORT_C: {"bid": "N/A", "ask": "N/A"}}
    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "NEEDS_ATTENTION") == ["B1"]
    assert alerter.send.await_count == 1
    body = alerter.send.await_args[0][0]
    assert "NEEDS ATTENTION" in body
    assert SHORT_C in body
    near_expiry = [
        e for e in audit_events if e["event"] == "options_manage_unquotable_near_expiry"
    ]
    assert len(near_expiry) == 1
    assert near_expiry[0]["position_id"] == "B1"
    assert near_expiry[0]["codes"] == [SHORT_C]
    assert near_expiry[0]["dte"] == 1


def test_manage_unquotable_leg_outside_guard_window_stays_open(
    options_book, make_bot, store, alerter, monkeypatch,
):
    _seed_bull_spread(store, expiry="2026-08-19")   # 2 DTE on TODAY
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]

    quotes = {LONG_C: {"bid": 7.95, "ask": 8.05}, SHORT_C: {"bid": "N/A", "ask": "N/A"}}
    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["B1"]
    alerter.send.assert_not_awaited()


# ============================================================
# WR-01: structure-kind mismatch (D-29 completion)
# ============================================================

def test_reconcile_startup_flags_structure_mismatch(
    options_book, make_bot, store, gateway, alerter, monkeypatch,
):
    audit_events = []
    monkeypatch.setattr(service, "append_audit", audit_events.append)
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    gateway.get_option_positions = AsyncMock(return_value={LONG_P: 2, SHORT_P: -2})

    _seed_open_spread(store, "T1")                                      # control
    _seed_open_spread(store, "M1", strategy_name="super_bull_call")      # credit under debit
    _seed_bull_spread(store, "X1", strategy_name="tasty_credit_spreads")  # debit under credit

    _run(bot.reconcile(startup=True))

    assert sorted(_statuses(store, "NEEDS_ATTENTION")) == ["M1", "X1"]
    assert _statuses(store, "OPEN") == ["T1"]
    assert alerter.send.await_count == 2
    mismatches = [e for e in audit_events if e["event"] == "options_strategy_structure_mismatch"]
    assert len(mismatches) == 2
    assert not [e for e in audit_events if e["event"] == "options_unknown_strategy"]
    m1_event = next(e for e in mismatches if e["position_id"] == "M1")
    assert m1_event["structure"] == "put_credit_spread"
    assert m1_event["configured_structure"] == "bull_call_spread"
    assert m1_event["strategy_name"] == "super_bull_call"


@pytest.mark.parametrize("seed", ["credit_row_under_debit_strategy", "debit_row_under_credit_strategy"])
def test_manage_skips_structure_mismatch(
    options_book, make_bot, store, monkeypatch, seed,
):
    log = MagicMock()
    monkeypatch.setattr(service, "_logger", log)
    if seed == "credit_row_under_debit_strategy":
        _seed_open_spread(store, "M1", expiry=EXPIRY, strategy_name="super_bull_call")
    else:
        _seed_bull_spread(store, "M1", strategy_name="tasty_credit_spreads")
    bot = make_bot(options_book.strategies[0], strategies=options_book.strategies)
    bot._executor = _fake_executor()
    pos = store.get_option_positions(("OPEN",))[0]

    quotes = {
        SHORT_P: {"bid": 0.55, "ask": 0.65}, LONG_P: {"bid": 0.15, "ask": 0.25},
        LONG_C: {"bid": 7.95, "ask": 8.05}, SHORT_C: {"bid": 1.15, "ask": 1.25},
    }
    result = _run(bot._manage_position(pos, quotes, TODAY))

    assert result == 0.0
    bot._executor.close_legs.assert_not_awaited()
    assert _statuses(store, "OPEN") == ["M1"]
    assert any(
        c.args and c.args[0] == "options_manage_structure_mismatch"
        for c in log.warning.call_args_list
    )


# ============================================================
# WR-05: stuck rows still count against BP headroom + concurrent cap (D-22)
# ============================================================

def test_bp_headroom_counts_needs_attention_and_closing_rows(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    _seed_open_spread(
        store, "N1", status="NEEDS_ATTENTION", strategy_name="tasty_credit_spreads",
        underlying="US.QQQ", max_loss_usd=12450.0,
    )
    _seed_open_spread(
        store, "C1", status="CLOSING", strategy_name="tasty_credit_spreads",
        underlying="US.IWM", max_loss_usd=12450.0,
    )

    _run(bot._job_entry_scan("super_bull_call"))

    bot._executor.open_position.assert_not_awaited()
    assert "US.NVDA" not in {p["underlying"] for p in store.get_option_positions(("OPEN",))}


def test_concurrent_cap_counts_needs_attention_and_closing_rows(
    options_book, make_bot, store, gateway, monkeypatch,
):
    bot = make_bot(options_book.strategies[1], strategies=options_book.strategies)
    _wire_bull(bot, gateway, monkeypatch)
    bot._executor = _fake_executor()
    for i in range(4):
        _seed_bull_spread(
            store, f"S{i}",
            status=("NEEDS_ATTENTION" if i < 2 else "CLOSING"),
            underlying=f"US.OTH{i}", max_loss_usd=100.0,
        )

    _run(bot._job_entry_scan("super_bull_call"))

    bot._executor.open_position.assert_not_awaited()
    assert "US.NVDA" not in {p["underlying"] for p in store.get_option_positions(("OPEN",))}


# ============================================================
# EOD
# ============================================================

def test_eod_sends_summary_and_writes_report(
    make_bot, store, gateway, alerter, options_cfg, monkeypatch, tmp_path,
):
    from dataclasses import replace
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1")
    store.insert_option_position(_pos(
        "OLD", status="CLOSED", underlying="US.QQQ",
        closed_at=f"{TODAY.isoformat()}T11:00:00-04:00",
        close_reason="profit_target", realized_pnl_usd=150.0,
    ))
    report_dir = tmp_path / "reports" / "options"
    bot = make_bot(replace(options_cfg, report_dir=str(report_dir)))

    _run(bot._job_eod())

    assert alerter.send.await_count == 1
    body = alerter.send.await_args[0][0]
    assert "Options EOD" in body
    assert "+150.00" in body
    written = (report_dir / f"{TODAY.isoformat()}.html").read_text(encoding="utf-8")
    assert "US.SPY" in written and "profit_target" in written
    assert (report_dir / "latest.html").exists()


def test_eod_summary_and_report_show_strategy_names(
    options_book, make_bot, store, gateway, alerter, monkeypatch, tmp_path,
):
    from dataclasses import replace
    _freeze(monkeypatch, SESSION_NOON)
    _seed_open_spread(store, "P1")
    _seed_bull_spread(store, "B1")
    store.insert_option_position({
        "position_id": "B2",
        "underlying": "US.NVDA",
        "structure": "bull_call_spread",
        "strategy_name": "super_bull_call",
        "expiry": "2026-09-18",
        "credit_per_spread": -1.96,
        "width": 10.0,
        "qty": 5,
        "max_loss_usd": 980.0,
        "status": "CLOSED",
        "closed_at": f"{TODAY.isoformat()}T11:00:00-04:00",
        "close_reason": "profit_target",
        "realized_pnl_usd": 2420.0,
    })
    report_dir = tmp_path / "reports" / "options"
    cfg = replace(options_book.strategies[0], report_dir=str(report_dir))
    bot = make_bot(cfg, strategies=options_book.strategies)

    _run(bot._job_eod())

    body = alerter.send.await_args[0][0]
    assert "tasty_credit_spreads" in body
    assert "super_bull_call" in body
    assert "debit 1.96" in body
    assert "+2,420.00" in body
    written = (report_dir / f"{TODAY.isoformat()}.html").read_text(encoding="utf-8")
    assert "<th>Strategy</th>" in written
    assert "super_bull_call" in written


def test_formatters_escape_strategy_name():
    pos = _pos(strategy_name="<b>x</b>")
    legs = [{"side": "SELL", "code": "X", "strike": 1.0}]

    entry = _fmt_entry(pos, legs)
    exit_txt = _fmt_exit(pos, "profit_target", 100.0, 50.0, basis="credit")
    summary = _fmt_summary([pos], [], 0.0)
    doc = _options_html(
        [pos], [{**pos, "close_reason": "x", "realized_pnl_usd": 1.0}], "2026-08-17",
    )

    for out in (entry, exit_txt, summary, doc):
        assert "<b>x</b>" not in out
        assert "&lt;b&gt;x&lt;/b&gt;" in out


# ============================================================
# main() composition (D-08, D-20, D-25)
# ============================================================

def test_main_builds_one_bot_for_every_strategy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "configure_logging", MagicMock())
    monkeypatch.setattr(service, "MoomooGateway", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(service, "get_gateway_config", MagicMock(return_value=MagicMock()))
    store_mock = MagicMock()
    store_mock.open = MagicMock(return_value=store_mock)
    store_cls = MagicMock(return_value=store_mock)
    monkeypatch.setattr(service, "OptionsStore", store_cls)
    monkeypatch.setattr(service, "OpenDWatchdog", MagicMock())
    bot_cls = MagicMock()
    monkeypatch.setattr(service, "OptionsBot", bot_cls)
    monkeypatch.setattr(service.asyncio, "run", lambda coro: None)

    service.main(str(REPO_ROOT / "rules_options.json"))

    bot_cls.assert_called_once()
    kwargs = bot_cls.call_args.kwargs
    assert [c.name for c in kwargs["strategies"]] == [
        "tasty_credit_spreads", "super_bull_call",
    ]
    assert kwargs["cfg"].name == "tasty_credit_spreads"
    store_cls.assert_called_once_with("data/options_state.db")


def test_main_exits_1_on_config_error(monkeypatch, tmp_path, capsys):
    import json
    bad = json.loads((REPO_ROOT / "rules_options.json").read_text(encoding="utf-8"))
    bad["strategies"][1]["name"] = bad["strategies"][0]["name"]
    path = tmp_path / "rules_options.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(service, "configure_logging", MagicMock())

    with pytest.raises(SystemExit) as exc_info:
        service.main(str(path))

    assert exc_info.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_shipped_book_registers_per_strategy_jobs(make_bot):
    book = load_options_book(str(REPO_ROOT / "rules_options.json"))
    bot = make_bot(book.strategies[0], strategies=book.strategies)
    bot._register_jobs()
    assert {j.id for j in bot._scheduler.get_jobs()} == {
        "options_entry_scan_tasty_credit_spreads",
        "options_entry_scan_tasty_credit_spreads_2",
        "options_entry_scan_super_bull_call",
        "options_manage", "options_eod",
    }
