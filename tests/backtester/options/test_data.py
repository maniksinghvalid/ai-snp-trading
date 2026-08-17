#!/usr/bin/env python3
"""tests.backtester.options.test_data — backtester.options.data unit tests (no network)."""
import json
import urllib.error
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import bot.options.strategy as live_strategy
from bot.options.config import load_options_config
from bot.options.strategy import is_monthly_expiry, option_dte, pick_expiry
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


# ============================================================
# T-09-03: OptionChainSource (D-07, D-08)
# ============================================================

def _bars_frame(day_closes: dict) -> pd.DataFrame:
    """{"YYYY-MM-DD": close} -> Title-Case ET-indexed frame (cached_bars shape)."""
    days = sorted(day_closes)
    idx = pd.DatetimeIndex(
        [datetime.strptime(d, "%Y-%m-%d").replace(hour=16, tzinfo=ET) for d in days]
    )
    closes = [day_closes[d] for d in days]
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [100.0] * len(days)},
        index=idx,
    )


def _raw_contract(ticker, expiry, right, strike):
    return {
        "ticker": ticker, "contract_type": "call" if right == "C" else "put",
        "expiration_date": expiry, "strike_price": strike,
    }


class _FakeSource:
    """Hand-built fake exposing cached_contracts/cached_option_bars/cached_bars
    — no network, no real MassiveDataSource HTTP path (per T-09-03 <action>)."""

    def __init__(self, contracts, option_bars, underlying_bars):
        self._contracts = contracts
        self._option_bars = option_bars
        self._underlying_bars = underlying_bars
        self.contracts_calls = 0
        self.option_bars_calls = []

    def cached_contracts(self, underlying, expiry_gte, expiry_lte):
        self.contracts_calls += 1
        return self._contracts

    def cached_option_bars(self, ticker, start, end):
        self.option_bars_calls.append(ticker)
        return self._option_bars.get(
            ticker, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        )

    def cached_bars(self, symbol, interval_tag, multiplier, timespan, start, end):
        return self._underlying_bars


# strike 100 monthly call, expiry 2025-07-18 (third Friday of July 2025);
# used by the no-lookahead / dte-window tests below.
_MONTHLY_EXPIRY = date(2025, 7, 18)
assert is_monthly_expiry(_MONTHLY_EXPIRY)  # fixture sanity, not itself a test
_FAR_MONTHLY_EXPIRY = date(2025, 12, 19)  # third Friday of December 2025
assert is_monthly_expiry(_FAR_MONTHLY_EXPIRY)
_NON_MONTHLY_EXPIRY = date(2025, 7, 25)  # fourth Friday of July 2025
assert not is_monthly_expiry(_NON_MONTHLY_EXPIRY)


def test_no_lookahead_contracts_for_day():
    """A leak contract carries a wildly different t+1 close (1.00 vs 99.00);
    a future-only and a past-only contract must both be absent on day t."""
    leak_ticker = "O:SPY250718C00100000"
    future_only_ticker = "O:SPY250718C00105000"
    past_only_ticker = "O:SPY250718C00095000"
    contracts = [
        _raw_contract(leak_ticker, "2025-07-18", "C", 100),
        _raw_contract(future_only_ticker, "2025-07-18", "C", 105),
        _raw_contract(past_only_ticker, "2025-07-18", "C", 95),
    ]
    option_bars = {
        leak_ticker: _bars_frame({"2025-06-10": 1.00, "2025-06-11": 99.00}),
        future_only_ticker: _bars_frame({"2025-06-11": 50.00}),
        past_only_ticker: _bars_frame({"2025-06-09": 50.00}),
    }
    underlying = _bars_frame({"2025-06-09": 100.0, "2025-06-10": 100.0, "2025-06-11": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    rows = chain.contracts_for_day("2025-06-10", 30, 60)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {leak_ticker}
    leak_row = rows[0]
    assert leak_row["close"] == 1.00  # never the t+1 close (99.00) — the leak proof
    assert leak_row["right"] == "C"
    assert leak_row["strike"] == 100.0
    assert leak_row["expiry"] == _MONTHLY_EXPIRY
    assert leak_row["dte"] == option_dte(_MONTHLY_EXPIRY, date(2025, 6, 10))
    assert leak_row["volume"] == 100.0


def test_dte_window_excludes_out_of_range_contracts():
    near_ticker = "O:SPY250718C00100000"
    far_ticker = "O:SPY251219C00100000"
    contracts = [
        _raw_contract(near_ticker, "2025-07-18", "C", 100),
        _raw_contract(far_ticker, "2025-12-19", "C", 100),
    ]
    option_bars = {
        near_ticker: _bars_frame({"2025-06-10": 5.0}),
        far_ticker: _bars_frame({"2025-06-10": 30.0}),
    }
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    rows = chain.contracts_for_day("2025-06-10", 30, 60)
    assert {r["ticker"] for r in rows} == {near_ticker}  # far_ticker's dte (~192d) excluded


def test_expiries_for_day_feeds_pick_expiry():
    near_ticker = "O:SPY250718C00100000"
    far_ticker = "O:SPY251219C00100000"
    contracts = [
        _raw_contract(near_ticker, "2025-07-18", "C", 100),
        _raw_contract(far_ticker, "2025-12-19", "C", 100),
    ]
    option_bars = {
        near_ticker: _bars_frame({"2025-06-10": 5.0}),
        far_ticker: _bars_frame({"2025-06-10": 30.0}),
    }
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    expiries = chain.expiries_for_day("2025-06-10")
    assert expiries == sorted(expiries)
    assert (_MONTHLY_EXPIRY, option_dte(_MONTHLY_EXPIRY, date(2025, 6, 10))) in expiries

    cfg = load_options_config()
    picked = pick_expiry(expiries, date(2025, 6, 10), cfg)
    assert picked == _MONTHLY_EXPIRY  # only in-window expiry within [30, 60] dte


def test_underlying_close_present_and_absent():
    underlying = _bars_frame({"2025-06-10": 101.5})
    source = _FakeSource([], {}, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)
    assert chain.underlying_close("2025-06-10") == 101.5
    assert chain.underlying_close("2025-06-11") is None  # absent day


def test_monthly_narrowing_covers_dte_window():
    """Every decision-day t across a year has at least one monthly expiry in
    [t+min_dte, t+max_dte] (rules_options.json: 30..60) — proves prefer_monthly
    narrowing at load() time cannot change pick_expiry's answer."""
    cfg = load_options_config()
    assert (cfg.min_dte, cfg.max_dte) == (30, 60)
    today = date(2025, 1, 1)
    for _ in range(365):
        window_has_monthly = any(
            is_monthly_expiry(today + timedelta(days=d))
            for d in range(cfg.min_dte, cfg.max_dte + 1)
        )
        assert window_has_monthly, f"no monthly expiry in window for {today}"
        today += timedelta(days=1)


def test_load_fetches_contracts_once_and_monthly_only():
    monthly_ticker = "O:SPY250718C00100000"
    non_monthly_ticker = "O:SPY250725C00100000"
    contracts = [
        _raw_contract(monthly_ticker, "2025-07-18", "C", 100),
        _raw_contract(non_monthly_ticker, "2025-07-25", "C", 100),
    ]
    option_bars = {
        monthly_ticker: _bars_frame({"2025-06-10": 5.0}),
        non_monthly_ticker: _bars_frame({"2025-06-10": 5.0}),
    }
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60, prefer_monthly=True)

    assert source.contracts_calls == 1  # ONE contracts-reference call per underlying
    assert monthly_ticker in source.option_bars_calls
    assert non_monthly_ticker not in source.option_bars_calls  # narrowed out before fetch


def test_last_known_close_walks_backward_for_manage_only():
    ticker = "O:SPY250718C00100000"
    contracts = [_raw_contract(ticker, "2025-07-18", "C", 100)]
    option_bars = {ticker: _bars_frame({"2025-06-09": 3.0})}
    underlying = _bars_frame({"2025-06-09": 100.0, "2025-06-10": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    assert chain.bar_close(ticker, "2025-06-10") is None  # exact-key: no bar on t
    assert chain.last_known_close(ticker, "2025-06-10") == 3.0  # carries forward from t-1
    assert chain.last_known_close(ticker, "2025-06-08") is None  # nothing before window
