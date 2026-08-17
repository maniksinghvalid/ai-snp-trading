#!/usr/bin/env python3
"""tests.backtester.options.test_data — backtester.options.data unit tests (no network)."""
import json
import urllib.error
from datetime import date, datetime

import pytest

import bot.options.strategy as live_strategy
import backtester.options.data as data
from backtester.massive import MassiveDataSource
from bot.safety.et_helpers import ET


@pytest.mark.parametrize(
    "root,expiry,right,strike",
    [
        ("SPY", date(2026, 9, 18), "P", 450.0),
        ("SPY", date(2026, 9, 18), "C", 402.5),
        ("QQQE", date(2025, 6, 20), "C", 100.0),
        ("GDX", date(2025, 6, 20), "P", 13.0),
    ],
)
def test_ticker_roundtrip(root, expiry, right, strike):
    ticker = data.format_massive_ticker(root, expiry, right, strike)
    parsed = data.parse_massive_ticker(ticker)
    assert parsed == {"root": root, "expiry": expiry, "right": right, "strike": strike}
    assert data.format_massive_ticker(**{**parsed, "root": parsed["root"]}) == ticker


@pytest.mark.parametrize(
    "bad_ticker",
    ["US.SPY260918P00450000", "O:SPY260918X00450000", "not-a-ticker", "O:SPY2609P00450000"],
)
def test_parse_malformed_ticker_raises(bad_ticker):
    with pytest.raises(ValueError):
        data.parse_massive_ticker(bad_ticker)


def test_data_imports_strategy_not_copies():
    assert data.is_monthly_expiry is live_strategy.is_monthly_expiry
    assert data.option_dte is live_strategy.option_dte


# ============================================================
# T-09-02: MassiveDataSource.cached_contracts / cached_option_bars (D-05, D-06)
# ============================================================

def _contract(ticker, expiry, right, strike):
    return {
        "cfi": "OCASPS", "contract_type": "call" if right == "C" else "put",
        "exercise_style": "american", "expiration_date": expiry,
        "primary_exchange": "BATO", "shares_per_contract": 100,
        "strike_price": strike, "ticker": ticker, "underlying_ticker": "SPY",
    }


def _option_bar(t_ms, c=10.5, v=100):
    return {"t": t_ms, "o": 10.0, "h": 11.0, "l": 9.0, "c": c, "v": v, "vw": c, "n": 5}


def test_fetch_contracts_paginates_dedupes_sorts(tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    c1 = _contract("O:SPY250620C00600000", "2025-06-20", "C", 600)
    c2 = _contract("O:SPY250620P00500000", "2025-06-20", "P", 500)
    calls = []

    def fake_get(url):
        calls.append(url)
        if url == "next-page":
            return {"results": [c2, c1]}  # c1 repeated across pages
        return {"results": [c1], "next_url": "next-page"}

    src._get_json = fake_get
    results = src.fetch_contracts("SPY", "2025-01-01", "2025-03-31")
    assert len(calls) == 2
    # same expiration_date; sorted by (expiration_date, contract_type, strike) ->
    # "call" < "put" alphabetically, so the call sorts first regardless of strike.
    assert [r["ticker"] for r in results] == ["O:SPY250620C00600000", "O:SPY250620P00500000"]


def test_cached_contracts_round_trip_never_refetches(tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    c1 = _contract("O:SPY250620C00600000", "2025-06-20", "C", 600)
    src._get_json = lambda url: {"results": [c1]}
    first = src.cached_contracts("SPY", "2025-01-01", "2025-03-31")

    def boom(url):
        raise AssertionError("cache hit must not refetch")

    src._get_json = boom
    second = src.cached_contracts("SPY", "2025-01-01", "2025-03-31")
    assert first == second == [c1]


def test_cached_contracts_rejects_bad_underlying(tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    with pytest.raises(ValueError):
        src.cached_contracts("SPY/../etc", "2025-01-01", "2025-03-31")


def test_cached_option_bars_round_trip_never_refetches(tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    t = int(datetime(2025, 6, 10, 9, 30, tzinfo=ET).timestamp() * 1000)
    src._get_json = lambda url: {"results": [_option_bar(t)]}
    first = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert list(first.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert first.iloc[0]["Close"] == 10.5
    assert first.iloc[0]["Volume"] == 100

    def boom(url):
        raise AssertionError("cache hit must not refetch")

    src._get_json = boom
    second = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert second.iloc[0]["Close"] == first.iloc[0]["Close"]
    assert str(second.index.tz) == "America/New_York"


def test_cached_option_bars_rejects_bad_ticker(tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    with pytest.raises(ValueError):
        src.cached_option_bars("../../etc/passwd", "2025-06-01", "2025-06-20")
    with pytest.raises(ValueError):
        src.cached_option_bars("US.SPY250620C00600000", "2025-06-01", "2025-06-20")


def test_option_bars_429_retries_then_succeeds(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    t = int(datetime(2025, 6, 10, 9, 30, tzinfo=ET).timestamp() * 1000)
    payload = {"results": [_option_bar(t)]}
    attempts = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(request, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "rate limited", {}, None)
        return FakeResp()

    monkeypatch.setattr("backtester.massive.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("backtester.massive.time.sleep", lambda s: None)
    frame = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert attempts["n"] == 2
    assert not frame.empty
