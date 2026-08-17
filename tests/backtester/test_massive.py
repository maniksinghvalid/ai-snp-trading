#!/usr/bin/env python3
"""tests.backtester.test_massive — MassiveDataSource unit tests (no network)."""
import json
import urllib.error
from datetime import datetime

import pandas as pd
import pytest

from bot.safety.et_helpers import ET
from backtester.massive import (
    MassiveApiError,
    MassiveDataSource,
    load_massive_api_key,
)


def _ms(y, m, d, hh, mm):
    """Epoch milliseconds for an ET wall-clock moment (Massive 't' field unit)."""
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp() * 1000)


def _bar(t, o=10.0, h=11.0, l=9.0, c=10.5, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "vw": c, "n": 5}


def test_fetch_bars_builds_titlecase_et_frame(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    payload = {"results": [_bar(_ms(2025, 3, 10, 9, 30)),
                           _bar(_ms(2025, 3, 10, 9, 35), c=10.7)]}
    monkeypatch.setattr(src, "_get_json", lambda url: payload)
    frame = src.fetch_bars("AAPL", 5, "minute", "2025-03-10", "2025-03-10")
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(frame.index.tz) == "America/New_York"
    assert frame.index[0].hour == 9 and frame.index[0].minute == 30
    assert frame.iloc[1]["Close"] == 10.7


def test_fetch_bars_paginates_and_dedupes(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    t0, t1 = _ms(2025, 3, 10, 9, 30), _ms(2025, 3, 10, 9, 35)
    calls = []

    def fake_get(url):
        calls.append(url)
        if url == "next-page":
            return {"results": [_bar(t0), _bar(t1)]}  # t0 repeated across pages
        return {"results": [_bar(t0)], "next_url": "next-page"}

    monkeypatch.setattr(src, "_get_json", fake_get)
    frame = src.fetch_bars("AAPL", 5, "minute", "2025-03-10", "2025-03-10")
    assert len(calls) == 2
    assert len(frame) == 2  # deduped on timestamp


def test_class_share_symbol_maps_dash_to_dot(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    seen = []

    def fake_get(url):
        seen.append(url)
        return {"results": []}

    monkeypatch.setattr(src, "_get_json", fake_get)
    frame = src.fetch_bars("BRK-B", 1, "day", "2025-03-10", "2025-03-10")
    assert "/ticker/BRK.B/" in seen[0]
    assert frame.empty and list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_cached_bars_round_trip_never_refetches(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    payload = {"results": [_bar(_ms(2025, 3, 10, 9, 30))]}
    monkeypatch.setattr(src, "_get_json", lambda url: payload)
    first = src.cached_bars("AAPL", "5m", 5, "minute", "2025-03-10", "2025-03-10")

    def boom(url):
        raise AssertionError("cache hit must not refetch")

    monkeypatch.setattr(src, "_get_json", boom)
    second = src.cached_bars("AAPL", "5m", 5, "minute", "2025-03-10", "2025-03-10")
    assert len(second) == len(first) == 1
    assert str(second.index.tz) == "America/New_York"  # normalised on cache read


def test_429_retries_then_succeeds(monkeypatch, tmp_path):
    src = MassiveDataSource("k", cache_dir=str(tmp_path))
    payload = {"results": [_bar(_ms(2025, 3, 10, 9, 30))]}
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
    assert src._get_json("https://api.massive.com/v2/aggs/x") == payload
    assert attempts["n"] == 2


def test_non_429_http_error_raises_massive_api_error(monkeypatch, tmp_path):
    src = MassiveDataSource("bad-key", cache_dir=str(tmp_path))

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)

    monkeypatch.setattr("backtester.massive.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(MassiveApiError):
        src._get_json("https://api.massive.com/v2/aggs/x")


def test_load_api_key_env_dotenv_and_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("MASSIVE_API_KEY", "from-env")
    assert load_massive_api_key(env_path=str(tmp_path / ".env")) == "from-env"

    monkeypatch.delenv("MASSIVE_API_KEY")
    env_file = tmp_path / ".env"
    env_file.write_text('MASSIVE_API_KEY="from-file"\n')
    assert load_massive_api_key(env_path=str(env_file)) == "from-file"

    env_file.write_text("MASSIVE_API_KEY=YOUR_API_KEY_HERE\n")
    with pytest.raises(MassiveApiError):
        load_massive_api_key(env_path=str(env_file))
