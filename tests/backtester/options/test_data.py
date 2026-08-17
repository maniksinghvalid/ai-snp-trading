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
    """expired=true is paginated in full, THEN expired=false is fetched
    (WR-03 union) -- three total requests: true page 1, true page 2
    (next-page, c1 repeated), false page 1 (empty)."""
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    c1 = _contract("O:SPY250620C00600000", "2025-06-20", "C", 600)
    c2 = _contract("O:SPY250620P00500000", "2025-06-20", "P", 500)
    responses = iter([
        {"results": [c1], "next_url": "next-page"},
        {"results": [c2, c1]},  # c1 repeated across pages
        {"results": []},        # expired=false pass
    ])
    calls = []

    def fake_get(url):
        calls.append(url)
        return next(responses)

    src._get_json = fake_get
    results = src.fetch_contracts("SPY", "2025-01-01", "2025-03-31")
    assert len(calls) == 3
    # same expiration_date; sorted by (expiration_date, contract_type, strike) ->
    # "call" < "put" alphabetically, so the call sorts first regardless of strike.
    assert [r["ticker"] for r in results] == ["O:SPY250620C00600000", "O:SPY250620P00500000"]


def test_fetch_contracts_unions_expired_true_and_false(tmp_path):
    """WR-03: a series still live as of the request must not be silently
    dropped -- expired=true and expired=false are both queried and unioned."""
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    expired_c = _contract("O:SPY250620C00600000", "2025-06-20", "C", 600)
    live_c = _contract("O:SPY261231P00500000", "2026-12-31", "P", 500)
    seen_urls = []

    def fake_get(url):
        seen_urls.append(url)
        if "expired=true" in url:
            return {"results": [expired_c]}
        return {"results": [live_c]}

    src._get_json = fake_get
    results = src.fetch_contracts("SPY", "2025-01-01", "2026-12-31")
    assert any("expired=true" in u for u in seen_urls)
    assert any("expired=false" in u for u in seen_urls)
    assert {r["ticker"] for r in results} == {expired_c["ticker"], live_c["ticker"]}


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


def test_cached_option_bars_caches_empty_result_no_refetch(tmp_path):
    """WR-04 negative caching: a thin/illiquid contract with zero bars for
    its whole life must be remembered (header-only CSV), never re-requested."""
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    calls = []

    def fake_get(url):
        calls.append(url)
        return {"results": []}

    src._get_json = fake_get
    first = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert first.empty
    assert len(calls) == 1

    def boom(url):
        raise AssertionError("negative cache hit must not refetch")

    src._get_json = boom
    second = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert second.empty


def test_cached_option_bars_keyed_by_ticker_extending_end_never_refetches(tmp_path):
    """WR-04: cache is keyed on the ticker only -- a later call with a wider
    --end (but the same ticker) is served entirely from the cached file."""
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    t = int(datetime(2025, 6, 10, 9, 30, tzinfo=ET).timestamp() * 1000)
    calls = []

    def fake_get(url):
        calls.append(url)
        return {"results": [_option_bar(t)]}

    src._get_json = fake_get
    first = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-15")
    assert len(calls) == 1
    assert not first.empty

    def boom(url):
        raise AssertionError("cache hit (wider end, same ticker) must not refetch")

    src._get_json = boom
    second = src.cached_option_bars("O:SPY250620C00600000", "2025-06-01", "2025-06-20")
    assert second.iloc[0]["Close"] == first.iloc[0]["Close"]


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

    def option_bar_cache_path(self, ticker):
        """Never a real on-disk path -- every fetch counts as a "request",
        never a "cache_hits", in fetch_stats (fine, unasserted by default)."""
        return f"/nonexistent-fake-cache/{ticker}"

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


def test_no_lookahead_rows_for():
    """A leak contract carries a wildly different t+1 close (1.00 vs 99.00);
    a future-only contract (no bar on day t) must be absent from rows_for's
    result even though it IS inside the OTM band."""
    leak_ticker = "O:SPY250718C00100000"
    future_only_ticker = "O:SPY250718C00105000"
    contracts = [
        _raw_contract(leak_ticker, "2025-07-18", "C", 100),
        _raw_contract(future_only_ticker, "2025-07-18", "C", 105),
    ]
    option_bars = {
        leak_ticker: _bars_frame({"2025-06-10": 1.00, "2025-06-11": 99.00}),
        future_only_ticker: _bars_frame({"2025-06-11": 50.00}),
    }
    underlying = _bars_frame({"2025-06-09": 100.0, "2025-06-10": 100.0, "2025-06-11": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    rows = chain.rows_for("2025-06-10", _MONTHLY_EXPIRY, 100.0, band_pct=10.0)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {leak_ticker}
    leak_row = rows[0]
    assert leak_row["close"] == 1.00  # never the t+1 close (99.00) — the leak proof
    assert leak_row["right"] == "C"
    assert leak_row["strike"] == 100.0
    assert leak_row["expiry"] == _MONTHLY_EXPIRY
    assert leak_row["dte"] == option_dte(_MONTHLY_EXPIRY, date(2025, 6, 10))
    assert leak_row["volume"] == 100.0


def test_load_narrows_contracts_to_dte_relevant_expiry_range():
    """load() narrows self._contracts to expiries in
    [start+min_dte, end+max_dte] -- a far-out contract's whole expiry is
    dropped at load() time, before any bar is ever fetched (T-09-13)."""
    near_ticker = "O:SPY250718C00100000"
    far_ticker = "O:SPY251219C00100000"
    contracts = [
        _raw_contract(near_ticker, "2025-07-18", "C", 100),
        _raw_contract(far_ticker, "2025-12-19", "C", 100),
    ]
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, {}, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    assert source.option_bars_calls == []  # load() never fetches a per-contract bar
    expiries = chain.expiries_for_day("2025-06-10")
    assert {e for e, _ in expiries} == {_MONTHLY_EXPIRY}  # far_ticker's expiry excluded


def test_expiries_for_day_feeds_pick_expiry():
    near_ticker = "O:SPY250718C00100000"
    contracts = [_raw_contract(near_ticker, "2025-07-18", "C", 100)]
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, {}, underlying)  # no bar fetch needed for existence
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)

    expiries = chain.expiries_for_day("2025-06-10")
    assert expiries == sorted(expiries)
    assert (_MONTHLY_EXPIRY, option_dte(_MONTHLY_EXPIRY, date(2025, 6, 10))) in expiries

    cfg = load_options_config()
    picked = pick_expiry(expiries, date(2025, 6, 10), cfg)
    assert picked == _MONTHLY_EXPIRY  # only in-window expiry within [30, 60] dte


def test_rows_for_fetches_only_target_expiry_in_band_and_memoises():
    """Lazy fetch proof (VERIFICATION gap 3, D-18): a synthetic reference
    with 3 expiries x a wide strike ladder -- rows_for(exp2) must fetch bars
    ONLY for exp2's in-band tickers, never exp1's/exp3's, and never an
    out-of-band exp2 strike. A second rows_for call on the same
    expiry/band issues ZERO new fetches (in-memory memoisation)."""
    day = "2025-06-10"
    spot = 100.0
    exp1, exp2, exp3 = date(2025, 7, 18), date(2025, 8, 15), date(2025, 9, 19)
    strikes = [80, 90, 95, 100, 105, 110, 120]  # in a 10% call band: only 100/105/110

    contracts, option_bars = [], {}
    for exp in (exp1, exp2, exp3):
        for strike in strikes:
            ticker = data.format_massive_ticker("SPY", exp, "C", strike)
            contracts.append(_raw_contract(ticker, exp.isoformat(), "C", strike))
            option_bars[ticker] = _bars_frame({day: 1.0})

    underlying = _bars_frame({day: spot})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=1, max_dte=200)

    expiries = chain.expiries_for_day(day)
    assert {e for e, _ in expiries} == {exp1, exp2, exp3}

    rows = chain.rows_for(day, exp2, spot, band_pct=10.0)
    exp2_in_band_tickers = {
        data.format_massive_ticker("SPY", exp2, "C", s)
        for s in strikes if spot * 0.995 <= s <= spot * 1.10
    }
    assert exp2_in_band_tickers == {
        data.format_massive_ticker("SPY", exp2, "C", s) for s in (100, 105, 110)
    }
    assert {r["ticker"] for r in rows} == exp2_in_band_tickers
    assert set(source.option_bars_calls) == exp2_in_band_tickers  # ONLY exp2's in-band tickers

    calls_before = len(source.option_bars_calls)
    chain.rows_for(day, exp2, spot, band_pct=10.0)  # second call, same expiry/band
    assert len(source.option_bars_calls) == calls_before  # zero new fetches (memoised)


def test_underlying_close_present_and_absent():
    underlying = _bars_frame({"2025-06-10": 101.5})
    source = _FakeSource([], {}, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)
    assert chain.underlying_close("2025-06-10") == 101.5
    assert chain.underlying_close("2025-06-11") is None  # absent day


def test_monthly_narrowing_covers_dte_window():
    """rules_options.json's [min_dte, max_dte]=30..60 window is NOT wide
    enough to always contain a monthly expiry (e.g. today=2025-04-17: the May
    monthly falls 1 day before the window opens, the June monthly 4 days
    after it closes) — proving the plan's original "hard-drop non-monthly at
    load()" premise is false for real calendar alignments (Rule 1 bug found
    via this test, see OptionChainSource.load()'s DEVIATION docstring).

    So instead: prove `load()` keeps the non-monthly candidate on exactly
    this adversarial day, and `pick_expiry` (unmodified, D-02) still returns
    a sensible answer despite zero monthlies being in range — the narrowing
    genuinely "cannot change pick_expiry's answer" because it never discards
    a valid candidate in the first place.
    """
    cfg = load_options_config()
    assert (cfg.min_dte, cfg.max_dte) == (30, 60)
    today = date(2025, 4, 17)
    window_has_monthly = any(
        is_monthly_expiry(today + timedelta(days=d))
        for d in range(cfg.min_dte, cfg.max_dte + 1)
    )
    assert not window_has_monthly  # the adversarial day this test exploits

    in_window_expiry = today + timedelta(days=45)  # dte=45, land inside [30, 60]
    while is_monthly_expiry(in_window_expiry):
        in_window_expiry += timedelta(days=1)
    non_monthly_ticker = data.format_massive_ticker("SPY", in_window_expiry, "C", 100)
    contracts = [_raw_contract(
        non_monthly_ticker, in_window_expiry.isoformat(), "C", 100
    )]
    option_bars = {non_monthly_ticker: _bars_frame({today.isoformat(): 5.0})}
    underlying = _bars_frame({today.isoformat(): 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", today.isoformat(), today.isoformat())
    chain.load(min_dte=cfg.min_dte, max_dte=cfg.max_dte, prefer_monthly=cfg.prefer_monthly)

    expiries = chain.expiries_for_day(today.isoformat())
    assert expiries  # the non-monthly candidate was NOT discarded at load() time
    picked = pick_expiry(expiries, today, cfg)
    assert picked == in_window_expiry  # pick_expiry falls back correctly, unmodified


def test_load_fetches_contracts_reference_once_no_bar_fetch():
    """load() keeps both monthly and non-monthly candidates within the
    DTE-relevant expiry range (the plan's original 'monthly-only at load()'
    narrowing was dropped as a Rule 1 bug fix, T-09-03), and fetches ZERO
    per-contract bars at load() time -- bars are fetched later, per decision
    day, by rows_for (lazy design, T-09-13)."""
    monthly_ticker = "O:SPY250718C00100000"
    non_monthly_ticker = "O:SPY250725C00100000"
    contracts = [
        _raw_contract(monthly_ticker, "2025-07-18", "C", 100),
        _raw_contract(non_monthly_ticker, "2025-07-25", "C", 100),
    ]
    underlying = _bars_frame({"2025-06-10": 100.0})
    source = _FakeSource(contracts, {}, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60, prefer_monthly=True)

    assert source.contracts_calls == 1  # ONE contracts-reference call per underlying
    assert source.option_bars_calls == []  # load() never fetches a single per-contract bar
    tickers = {c["ticker"] for c in chain._contracts}
    assert tickers == {monthly_ticker, non_monthly_ticker}  # both kept, not discarded


def test_last_known_close_walks_backward_for_manage_only():
    ticker = "O:SPY250718C00100000"
    contracts = [_raw_contract(ticker, "2025-07-18", "C", 100)]
    option_bars = {ticker: _bars_frame({"2025-06-09": 3.0})}
    underlying = _bars_frame({"2025-06-09": 100.0, "2025-06-10": 100.0})
    source = _FakeSource(contracts, option_bars, underlying)
    chain = data.OptionChainSource(source, "SPY", "2025-06-01", "2025-06-30")
    chain.load(min_dte=30, max_dte=60)
    chain._ensure_bars([ticker])  # populate bars for MANAGE-time lookups (lazy design)

    assert chain.bar_close(ticker, "2025-06-10") is None  # exact-key: no bar on t
    assert chain.last_known_close(ticker, "2025-06-10") == 3.0  # carries forward from t-1
    assert chain.last_known_close(ticker, "2025-06-08") is None  # nothing before window
