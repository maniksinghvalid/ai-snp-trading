#!/usr/bin/env python3
"""
tests/ibs/conftest.py — Shared fixtures for the IBS bot tests.

Provides:
  ibs_rules — the canonical rules_ibs.json content as a plain dict (a LITERAL,
              never derived from loader code, so the shipped-file drift guard
              in test_schema_config.py is not circular)
  ibs_cfg   — a loaded IbsConfig built from ibs_rules in tmp_path
  ibs_store / ibs_alerter / ibs_gateway / ibs_kill_switch / make_ibs_bot /
  make_snapshot_row / make_positions_df / set_now — IbsBot service fixtures
  (bot.ibs.service is imported lazily inside fixture bodies)
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.ibs.config import load_ibs_config

UNIVERSE = [
    "US.SPY", "US.QQQ", "US.IWM", "US.DIA", "US.XLK", "US.XLF",
    "US.XLE", "US.XLV", "US.XLI", "US.XLY", "US.XLP", "US.XLU",
    "US.XLB", "US.TLT", "US.GLD", "US.EFA", "US.EEM",
]


@pytest.fixture
def ibs_rules():
    """Return the canonical rules_ibs.json content as a dict (mutate a deepcopy)."""
    return {
        "strategy_name": "ibs_etf_mean_reversion",
        "universe": list(UNIVERSE),
        "signal": {
            "ibs_entry_max": 0.20,
            "ibs_exit_min": 0.80,
            "max_hold_trading_days": 10,
            "max_snapshot_age_s": 900,
        },
        "risk": {
            "sizing_equity_usd": 100000,
            "position_pct_of_equity": 10,
            "max_concurrent_positions": 10,
        },
        "execution": {
            "entry_limit_buffer_usd": 0.05,
            "exit_limit_buffer_usd": 0.05,
            "order_ttl_seconds": 20,
            "poll_interval_seconds": 5,
            "escalation_step_usd": 0.05,
            "max_reprices": 3,
        },
        "service": {
            "arm_time_et": "09:00",
            "decision_before_close_min": 10,
            "hard_cancel_before_close_min": 1,
            "eod_report_after_close_min": 5,
            "misfire_grace_s": 120,
            "watchdog_poll_interval_s": 60,
            "watchdog_reconnect_initial_s": 5,
            "watchdog_reconnect_cap_s": 300,
            "state_db": "data/ibs_state.db",
            "kill_file": ".bot_kill_ibs",
            "report_dir": "reports/ibs",
            "log_file": "ibs.log",
        },
    }


@pytest.fixture
def ibs_cfg(ibs_rules, tmp_path):
    """Return a loaded IbsConfig built from ibs_rules."""
    p = tmp_path / "rules_ibs.json"
    p.write_text(json.dumps(ibs_rules), encoding="utf-8")
    return load_ibs_config(str(p))


# ============================================================
# IbsBot service fixtures (lazy imports of bot.ibs.service)
# ============================================================

@pytest.fixture
def ibs_store(tmp_path):
    from bot.ibs.store import IbsStore
    st = IbsStore(str(tmp_path / "ibs.db")).open()
    yield st
    st.close()


@pytest.fixture
def ibs_alerter():
    return MagicMock(send=AsyncMock(), _enabled=True)


@pytest.fixture
def make_positions_df():
    def _make(holdings):
        rows = [
            {"code": c, "qty": float(q), "cost_price": 100.0,
             "position_side": "LONG" if q >= 0 else "SHORT"}
            for c, q in holdings.items()
        ]
        return pd.DataFrame(rows, columns=["code", "qty", "cost_price", "position_side"])
    return _make


@pytest.fixture
def make_snapshot_row():
    def _make(code, last, high, low, update_time="2026-10-05 15:49:58.123",
              suspension=False, bid=None, ask=None):
        return {
            "code": code, "update_time": update_time, "last_price": last,
            "high_price": high, "low_price": low, "suspension": suspension,
            "bid_price": bid, "ask_price": ask, "volume": 1000,
        }
    return _make


@pytest.fixture
def ibs_gateway(make_positions_df):
    gw = MagicMock()
    gw.connect = MagicMock()
    gw.close = MagicMock()
    gw.get_positions = AsyncMock(return_value=(0, make_positions_df({})))
    gw.get_market_snapshot = AsyncMock(return_value=(0, pd.DataFrame([])))
    gw.place_order = AsyncMock()
    gw.cancel_order = AsyncMock()
    gw.get_order_status = AsyncMock()
    return gw


@pytest.fixture
def ibs_kill_switch():
    return MagicMock(triggered=False, check_file=MagicMock(return_value=False))


@pytest.fixture
def make_ibs_bot(ibs_cfg, ibs_gateway, ibs_store, ibs_kill_switch, ibs_alerter):
    def _make(cfg=None):
        from bot.ibs.service import IbsBot
        return IbsBot(
            cfg=cfg or ibs_cfg, gateway=ibs_gateway, store=ibs_store,
            kill_switch=ibs_kill_switch, alerter=ibs_alerter,
        )
    return _make


@pytest.fixture
def set_now(monkeypatch):
    def _set(dt):
        monkeypatch.setattr("bot.ibs.service.now_et", lambda: dt)
    return _set
