#!/usr/bin/env python3
"""
tests/options/test_store.py — OptionsStore persistence (option_positions/legs/meta).

The store is built on a tmp-path DB; migrations run on open() and create the
v6 option tables.
"""
import pytest

from bot.options.store import OptionsStore


# ============================================================
# Fixtures / helpers
# ============================================================

@pytest.fixture
def store(tmp_path):
    st = OptionsStore(str(tmp_path / "options.db")).open()
    yield st
    st.close()


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


def _leg(leg_id, position_id="P1", side="SELL", **over) -> dict:
    leg = {
        "leg_id": leg_id,
        "position_id": position_id,
        "code": "US.SPY260320P600000",
        "right": "P",
        "strike": 600.0,
        "side": side,
        "qty": 2,
        "status": "PENDING",
    }
    leg.update(over)
    return leg


# ============================================================
# Insert + joined read
# ============================================================

def test_insert_position_and_legs_reads_back_joined(store):
    store.insert_option_position(_pos())
    store.insert_option_leg(_leg("L1", side="BUY", strike=594.0))
    store.insert_option_leg(_leg("L2", side="SELL", strike=600.0))

    rows = store.get_option_positions(("OPEN",))
    assert len(rows) == 1
    pos = rows[0]
    assert pos["position_id"] == "P1"
    assert pos["underlying"] == "US.SPY"
    assert [leg["leg_id"] for leg in pos["legs"]] == ["L1", "L2"]   # rowid order
    assert pos["legs"][0]["right"] == "P"
    assert pos["legs"][0]["side"] == "BUY"


def test_omitted_nullable_columns_default_to_none(store):
    store.insert_option_position({
        "position_id": "P9", "underlying": "US.QQQ", "structure": "iron_condor",
        "expiry": "2026-03-20", "status": "OPENING",
    })
    pos = store.get_option_positions(("OPENING",))[0]
    assert pos["closed_at"] is None
    assert pos["realized_pnl_usd"] is None
    assert pos["legs"] == []


def test_status_filter(store):
    store.insert_option_position(_pos("P1", status="OPEN"))
    store.insert_option_position(_pos("P2", status="CLOSING"))
    store.insert_option_position(_pos("P3", status="CLOSED"))
    store.insert_option_position(_pos("P4", status="ABORTED"))

    ids = {p["position_id"] for p in store.get_option_positions(("OPEN", "CLOSING"))}
    assert ids == {"P1", "P2"}


def test_empty_statuses_returns_empty_list(store):
    store.insert_option_position(_pos())
    assert store.get_option_positions(()) == []


# ============================================================
# Partial leg updates
# ============================================================

def test_set_leg_entry_updates_only_given_fields(store):
    store.insert_option_position(_pos())
    store.insert_option_leg(_leg("L1", entry_price=1.5, status="WORKING"))

    store.set_leg_entry("L1", order_id="X")

    leg = store.get_option_positions(("OPEN",))[0]["legs"][0]
    assert leg["entry_order_id"] == "X"
    assert leg["entry_price"] == 1.5        # untouched
    assert leg["status"] == "WORKING"       # untouched


def test_set_leg_entry_all_none_is_a_noop(store):
    store.insert_option_position(_pos())
    store.insert_option_leg(_leg("L1", entry_order_id="X"))

    store.set_leg_entry("L1")

    leg = store.get_option_positions(("OPEN",))[0]["legs"][0]
    assert leg["entry_order_id"] == "X"


def test_set_leg_entry_writes_all_three(store):
    store.insert_option_position(_pos())
    store.insert_option_leg(_leg("L1"))

    store.set_leg_entry("L1", order_id="O1", price=2.25, status="FILLED")

    leg = store.get_option_positions(("OPEN",))[0]["legs"][0]
    assert (leg["entry_order_id"], leg["entry_price"], leg["status"]) == ("O1", 2.25, "FILLED")


def test_set_leg_exit_updates_only_given_fields(store):
    store.insert_option_position(_pos())
    store.insert_option_leg(_leg("L1", exit_price=0.4, status="CLOSING"))

    store.set_leg_exit("L1", order_id="E1", status="CLOSED")

    leg = store.get_option_positions(("OPEN",))[0]["legs"][0]
    assert leg["exit_order_id"] == "E1"
    assert leg["exit_price"] == 0.4         # untouched
    assert leg["status"] == "CLOSED"


# ============================================================
# Position status
# ============================================================

def test_set_position_status_writes_all_close_fields(store):
    store.insert_option_position(_pos())

    store.set_position_status(
        "P1", "CLOSED",
        closed_at="2026-09-01T15:00:00-04:00",
        close_reason="profit_target",
        realized_pnl_usd=210.0,
    )

    pos = store.get_option_positions(("CLOSED",))[0]
    assert pos["status"] == "CLOSED"
    assert pos["closed_at"] == "2026-09-01T15:00:00-04:00"
    assert pos["close_reason"] == "profit_target"
    assert pos["realized_pnl_usd"] == 210.0


def test_set_position_status_only_status(store):
    store.insert_option_position(_pos(realized_pnl_usd=5.0))

    store.set_position_status("P1", "NEEDS_ATTENTION")

    pos = store.get_option_positions(("NEEDS_ATTENTION",))[0]
    assert pos["status"] == "NEEDS_ATTENTION"
    assert pos["realized_pnl_usd"] == 5.0    # untouched
    assert pos["closed_at"] is None


# ============================================================
# Aggregates
# ============================================================

def test_get_realized_pnl_on_sums_only_that_date(store):
    store.insert_option_position(_pos("P1", status="CLOSED",
                                      closed_at="2026-08-17T15:00:00-04:00",
                                      realized_pnl_usd=100.0))
    store.insert_option_position(_pos("P2", status="CLOSED",
                                      closed_at="2026-08-17T16:00:00-04:00",
                                      realized_pnl_usd=-40.0))
    store.insert_option_position(_pos("P3", status="CLOSED",
                                      closed_at="2026-08-18T10:00:00-04:00",
                                      realized_pnl_usd=999.0))

    assert store.get_realized_pnl_on("2026-08-17") == 60.0


def test_get_realized_pnl_on_empty_is_zero(store):
    assert store.get_realized_pnl_on("2026-08-17") == 0.0


def test_count_opened_on(store):
    store.insert_option_position(_pos("P1", opened_at="2026-08-17T10:00:00-04:00"))
    store.insert_option_position(_pos("P2", opened_at="2026-08-17T14:30:00-04:00"))
    store.insert_option_position(_pos("P3", opened_at="2026-08-18T10:00:00-04:00"))

    assert store.count_opened_on("2026-08-17") == 2
    assert store.count_opened_on("2026-08-19") == 0


# ============================================================
# Generic meta
# ============================================================

def test_meta_round_trip_and_upsert(store):
    assert store.get_meta("options_breaker_date") is None

    store.set_meta("options_breaker_date", "2026-08-17")
    assert store.get_meta("options_breaker_date") == "2026-08-17"

    store.set_meta("options_breaker_date", "2026-08-18")     # upsert, no IntegrityError
    assert store.get_meta("options_breaker_date") == "2026-08-18"


def test_meta_does_not_collide_with_circuit_breaker_helper(store):
    store.set_circuit_breaker_date("2026-08-17")
    store.set_meta("options_breaker_date", "2026-08-18")
    assert store.get_circuit_breaker_date() == "2026-08-17"
    assert store.get_meta("options_breaker_date") == "2026-08-18"
