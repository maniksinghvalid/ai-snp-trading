#!/usr/bin/env python3
"""
tests/ibs/test_strategy.py — Unit tests for the pure IBS strategy core (IBS-03, D-02..D-05, D-16).

Never reads the wall clock: `now` is a fixed ET datetime.
"""
import dataclasses
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from bot.ibs.strategy import (
    compute_ibs,
    decide_entries,
    decide_exits,
    parse_snapshot,
    size_position,
    trading_days_held,
)

NOW = datetime(2026, 10, 5, 15, 50, tzinfo=ZoneInfo("America/New_York"))
NAN = float("nan")
INF = float("inf")


# ---------------------------------------------------------------- compute_ibs

@pytest.mark.parametrize("last,high,low,expected", [
    (105, 110, 100, 0.5),
    (100, 110, 100, 0.0),
    (110, 110, 100, 1.0),
])
def test_compute_ibs_values(last, high, low, expected):
    assert compute_ibs(last, high, low) == pytest.approx(expected)


@pytest.mark.parametrize("last,high,low", [
    (None, 110, 100),
    (105, None, 100),
    (105, 110, None),
    (NAN, 110, 100),
    (105, INF, 100),
    (105, 110, NAN),
    (0, 110, 100),
    (105, 0, 100),
    (105, 110, 0),
    (-5, 110, 100),
    (105, 110, -1),
    (100, 100, 100),     # high == low
    (100, 90, 110),      # high < low
    (99, 110, 100),      # last < low
    (111, 110, 100),     # last > high
    ("N/A", 110, 100),
])
def test_compute_ibs_none(last, high, low):
    assert compute_ibs(last, high, low) is None


# --------------------------------------------------------------- parse_snapshot

def _row(code="US.SPY", **kw):
    row = {"code": code, "update_time": "2026-10-05 15:49:58.123",
           "last_price": 105.0, "high_price": 110.0, "low_price": 100.0,
           "suspension": False}
    row.update(kw)
    return row


def test_parse_snapshot_valid():
    quotes, skipped = parse_snapshot([_row()], NOW, 900)
    assert skipped == {}
    q = quotes["US.SPY"]
    assert q["ibs"] == pytest.approx(0.5)
    assert q["last"] == 105.0 and q["high"] == 110.0 and q["low"] == 100.0
    assert q["update_time"] == "2026-10-05 15:49:58.123"


def test_parse_snapshot_numpy_floats_accepted():
    row = _row(last_price=np.float64(105.0), high_price=np.float64(110.0),
               low_price=np.float64(100.0), suspension=np.bool_(False))
    quotes, skipped = parse_snapshot([row], NOW, 900)
    assert "US.SPY" in quotes and skipped == {}


@pytest.mark.parametrize("row,reason", [
    (_row(last_price="N/A"), "bad_price"),
    (_row(last_price=NAN), "bad_price"),
    (_row(low_price=0), "bad_price"),
    (_row(high_price=100.0), "no_range"),
    (_row(last_price=111.0), "last_outside_range"),
    (_row(last_price=99.0), "last_outside_range"),
    (_row(suspension=True), "suspended"),
    (_row(update_time="garbage"), "bad_update_time"),
    (_row(update_time=None), "bad_update_time"),
    (_row(update_time="2026-10-02 15:59:59"), "stale_date"),
    (_row(update_time="2026-10-05 15:30:00"), "stale_age"),
])
def test_parse_snapshot_skips(row, reason):
    quotes, skipped = parse_snapshot([row], NOW, 900)
    assert quotes == {}
    assert skipped == {"US.SPY": reason}


def test_parse_snapshot_missing_suspension_fails_closed():
    row = _row()
    del row["suspension"]
    quotes, skipped = parse_snapshot([row], NOW, 900)
    assert quotes == {} and skipped == {"US.SPY": "suspended"}


def test_parse_snapshot_absent_code_in_neither_and_empty_code_ignored():
    quotes, skipped = parse_snapshot([_row(), _row(code="")], NOW, 900)
    assert set(quotes) == {"US.SPY"} and skipped == {}
    assert "US.QQQ" not in quotes and "US.QQQ" not in skipped


def test_parse_snapshot_clock_skew_negative_age_allowed():
    quotes, _ = parse_snapshot([_row(update_time="2026-10-05 15:50:03.000")], NOW, 900)
    assert "US.SPY" in quotes


def test_parse_snapshot_mixed_rows():
    quotes, skipped = parse_snapshot(
        [_row(), _row(code="US.QQQ", suspension=True)], NOW, 900)
    assert set(quotes) == {"US.SPY"} and skipped == {"US.QQQ": "suspended"}


# ------------------------------------------------------------------ decide_exits

def test_decide_exits_mixed(ibs_cfg):
    out = decide_exits(
        ["US.SPY", "US.QQQ", "US.IWM", "US.DIA"],
        {"US.SPY": 0.85, "US.QQQ": 0.5, "US.DIA": 0.1},
        {"US.SPY": 1, "US.QQQ": 10, "US.IWM": 10, "US.DIA": 2},
        {"US.DIA"}, ibs_cfg)
    assert out == [("US.SPY", "ibs"), ("US.QQQ", "time"),
                   ("US.IWM", "time"), ("US.DIA", "retry")]


def test_decide_exits_boundaries(ibs_cfg):
    out = decide_exits(["US.SPY", "US.QQQ"],
                       {"US.SPY": 0.80, "US.QQQ": 0.5},
                       {"US.SPY": 1, "US.QQQ": 9}, set(), ibs_cfg)
    assert out == []


def test_decide_exits_unknown_ibs_before_time_holds(ibs_cfg):
    assert decide_exits(["US.IWM"], {}, {"US.IWM": 3}, set(), ibs_cfg) == []


def test_decide_exits_ibs_wins_over_time(ibs_cfg):
    out = decide_exits(["US.SPY"], {"US.SPY": 0.9}, {"US.SPY": 10}, set(), ibs_cfg)
    assert out == [("US.SPY", "ibs")]


# ---------------------------------------------------------------- decide_entries

def test_decide_entries_threshold_exclusion_order(ibs_cfg):
    uni = ibs_cfg.universe
    ibs = {"US.SPY": 0.20, "US.QQQ": 0.1, "US.IWM": 0.05, "US.DIA": 0.15,
           "US.XLK": None, "US.XLF": 0.5}
    out = decide_entries(ibs, {"US.DIA"}, 10, ibs_cfg, uni)
    assert out == ["US.IWM", "US.QQQ"]


def test_decide_entries_tie_by_universe_index(ibs_cfg):
    uni = ibs_cfg.universe
    ibs = {"US.IWM": 0.1, "US.QQQ": 0.1, "US.SPY": 0.1}
    assert decide_entries(ibs, set(), 10, ibs_cfg, uni) == ["US.SPY", "US.QQQ", "US.IWM"]
    assert decide_entries(ibs, set(), 1, ibs_cfg, uni) == ["US.SPY"]


def test_decide_entries_truncates_and_nonpositive_slots(ibs_cfg):
    uni = ibs_cfg.universe
    ibs = {"US.SPY": 0.01, "US.QQQ": 0.02, "US.IWM": 0.03}
    assert decide_entries(ibs, set(), 2, ibs_cfg, uni) == ["US.SPY", "US.QQQ"]
    assert decide_entries(ibs, set(), 0, ibs_cfg, uni) == []
    assert decide_entries(ibs, set(), -3, ibs_cfg, uni) == []


# ----------------------------------------------------------------- size_position

@pytest.mark.parametrize("price,shares", [
    (50.05, 199), (770.65, 12), (10001, 0), (0, 0), (-1, 0), (NAN, 0), (INF, 0),
])
def test_size_position(ibs_cfg, price, shares):
    assert size_position(price, ibs_cfg) == shares


def test_size_position_follows_cfg(ibs_cfg):
    cfg = dataclasses.replace(ibs_cfg, position_pct_of_equity=5)
    assert size_position(100.0, cfg) == 50


# ------------------------------------------------------------- trading_days_held

def test_trading_days_held():
    sessions = [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)]
    assert trading_days_held(date(2026, 11, 24), date(2026, 11, 30), sessions) == 3
    assert trading_days_held(date(2026, 11, 30), date(2026, 11, 30), sessions) == 0
    assert trading_days_held(date(2026, 11, 25), date(2026, 11, 30), sessions) == 2
