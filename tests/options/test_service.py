#!/usr/bin/env python3
"""
tests/options/test_service.py — OptionsBot lifecycle, reconcile, formatters, jobs.

The store is a REAL OptionsStore on a tmp-path DB (status transitions are the
thing under test, so a mock would prove nothing); the gateway, alerter and
kill switch are mocks with AsyncMock async methods.
"""
import asyncio
from datetime import date, datetime
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


# ============================================================
# Job registration
# ============================================================

def test_register_jobs_registers_the_four_job_ids(make_bot):
    bot = make_bot()
    bot._register_jobs()
    assert {j.id for j in bot._scheduler.get_jobs()} == {
        "options_entry_scan", "options_entry_scan_2", "options_manage", "options_eod",
    }


def test_register_jobs_skips_second_scan_when_unset(make_bot, options_cfg):
    from dataclasses import replace
    bot = make_bot(replace(options_cfg, second_entry_scan_et=None))
    bot._register_jobs()
    ids = {j.id for j in bot._scheduler.get_jobs()}
    assert "options_entry_scan_2" not in ids
    assert "options_entry_scan" in ids


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
