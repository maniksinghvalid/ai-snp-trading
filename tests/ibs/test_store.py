#!/usr/bin/env python3
"""tests/ibs/test_store.py — IbsStore on a tmp DB (IBS-06, D-05, D-09, ruling 4)."""
import sqlite3

import pytest

from bot.ibs.store import IbsStore


@pytest.fixture
def store(tmp_path):
    s = IbsStore(str(tmp_path / "ibs.db")).open()
    yield s
    s.close()


def _pos(pid="P1", code="US.XLU", status="OPENING", qty=12, **kw):
    row = {
        "position_id": pid, "code": code, "qty": qty,
        "entry_date": "2026-10-01", "status": status,
    }
    row.update(kw)
    return row


def test_open_creates_tables(store):
    names = {r[0] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ibs_positions", "ibs_trades", "ibs_orders"} <= names
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_insert_and_get_active_roundtrip(store):
    store.insert_position(_pos())
    rows = store.get_active_positions()
    assert len(rows) == 1
    assert isinstance(rows[0], dict)
    assert rows[0]["code"] == "US.XLU" and rows[0]["exit_pending"] == 0


@pytest.mark.parametrize("status", ["OPENING", "OPEN", "CLOSING", "NEEDS_ATTENTION"])
def test_duplicate_active_code_raises(store, status):
    store.insert_position(_pos("P1", status=status))
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_position(_pos("P2", status="OPENING"))


def test_new_row_allowed_after_aborted_or_closed(store):
    store.insert_position(_pos("P1"))
    store.set_position_status("P1", "ABORTED")
    store.insert_position(_pos("P2"))
    store.set_position_status("P2", "CLOSED", closed_at="2026-10-05T15:55:00", close_reason="ibs")
    store.insert_position(_pos("P3"))
    assert [p["position_id"] for p in store.get_active_positions()] == ["P3"]
    closed = store.get_positions(("CLOSED",))
    assert closed[0]["close_reason"] == "ibs" and closed[0]["closed_at"]


def test_get_positions_empty_and_filter(store):
    store.insert_position(_pos("P1", "US.SPY", status="OPEN"))
    store.insert_position(_pos("P2", "US.QQQ", status="CLOSED"))
    assert store.get_positions(()) == []
    assert [p["position_id"] for p in store.get_positions(("CLOSED",))] == ["P2"]


def test_mark_opened(store):
    store.insert_position(_pos())
    store.mark_opened("P1", 12, 770.10, "O1")
    p = store.get_active_positions()[0]
    assert (p["status"], p["qty"], p["entry_price"], p["entry_order_id"]) == ("OPEN", 12, 770.10, "O1")


def test_mark_exit_pending_keeps_first(store):
    store.insert_position(_pos())
    store.mark_exit_pending("P1", "time", "2026-10-05")
    store.mark_exit_pending("P1", "retry", "2026-10-06")
    p = store.get_active_positions()[0]
    assert p["exit_pending"] == 1
    assert p["exit_reason"] == "time"
    assert p["exit_decided_date"] == "2026-10-05"


def test_record_exit_fill_partial_then_full(store):
    store.insert_position(_pos())
    store.mark_opened("P1", 12, 770.10, "O1")
    store.mark_exit_pending("P1", "time", "2026-10-05")

    assert store.record_exit_fill("P1", "T1", 5, 780.10, "2026-10-05", "2026-10-05T15:55:00") == 7
    p = store.get_active_positions()[0]
    assert (p["qty"], p["status"], p["exit_pending"]) == (7, "OPEN", 1)
    trades = store.get_trades_on("2026-10-05")
    assert len(trades) == 1
    assert trades[0]["qty"] == 5 and trades[0]["reason"] == "time"
    assert trades[0]["pnl_usd"] == pytest.approx((780.10 - 770.10) * 5)

    assert store.record_exit_fill("P1", "T2", 7, 775.10, "2026-10-06", "2026-10-06T15:55:00") == 0
    assert store.get_active_positions() == []
    p = store.get_positions(("CLOSED",))[0]
    assert (p["qty"], p["exit_pending"], p["close_reason"]) == (0, 0, "time")
    assert p["closed_at"] == "2026-10-06T15:55:00"
    assert p["realized_pnl_usd"] == pytest.approx(50.0 + 35.0)


def test_record_exit_fill_rejects_nonpositive(store):
    store.insert_position(_pos())
    store.mark_opened("P1", 12, 770.10, "O1")
    with pytest.raises(ValueError):
        store.record_exit_fill("P1", "T1", 0, 780.0, "2026-10-05", "x")
    assert store.get_trades_on("2026-10-05") == []
    assert store.get_active_positions()[0]["qty"] == 12


def test_insert_order_upserts(store):
    o = {"order_id": "O1", "position_id": "P1", "code": "US.XLU", "side": "BUY",
         "qty": 12, "status": "WORKING", "session_date": "2026-10-05"}
    store.insert_order(o)
    store.insert_order(dict(o, status="DONE"))
    rows = store.get_orders(("DONE", "WORKING"))
    assert len(rows) == 1 and rows[0]["status"] == "DONE"


def test_close_working_orders_scoped(store):
    base = {"code": "US.XLU", "side": "BUY", "qty": 1, "status": "WORKING",
            "session_date": "2026-10-05"}
    store.insert_order(dict(base, order_id="O1", position_id="P1"))
    store.insert_order(dict(base, order_id="O2", position_id="P2"))
    assert store.close_working_orders("P1") == ["O1"]
    assert [o["order_id"] for o in store.get_orders(("WORKING",))] == ["O2"]
    assert [o["order_id"] for o in store.get_orders(("DONE",))] == ["O1"]
    assert store.get_orders(()) == []


def test_set_order_status(store):
    store.insert_order({"order_id": "O1", "code": "US.XLU", "side": "SELL",
                        "status": "WORKING", "session_date": "2026-10-05"})
    store.set_order_status("O1", "CANCELLED")
    assert store.get_orders(("CANCELLED",))[0]["order_id"] == "O1"


def test_realized_pnl_on(store):
    assert store.get_realized_pnl_on("2026-10-05") == 0.0
    store.insert_position(_pos())
    store.mark_opened("P1", 10, 100.0, "O1")
    store.record_exit_fill("P1", "T1", 10, 101.0, "2026-10-05", "x")
    assert store.get_realized_pnl_on("2026-10-05") == pytest.approx(10.0)
    assert store.get_realized_pnl_on("2026-10-06") == 0.0


def test_meta_upsert(store):
    assert store.get_meta("nope") is None
    store.set_meta("ibs_decision_date", "2026-10-05")
    store.set_meta("ibs_decision_date", "2026-10-06")
    assert store.get_meta("ibs_decision_date") == "2026-10-06"


def test_quote_character_roundtrip(store):
    store.insert_position(_pos("P1", "US.X'Y"))
    assert store.get_active_positions()[0]["code"] == "US.X'Y"
    store.set_meta("k'; DROP TABLE meta;--", "v'")
    assert store.get_meta("k'; DROP TABLE meta;--") == "v'"
