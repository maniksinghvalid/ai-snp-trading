# Massive API Backtest Engine Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Phase-06 backtester with a Massive API (massive.com, formerly Polygon.io) historical-data source, configurable capital/commission/slippage, and a full performance-metrics report (equity curve, Sharpe, CAGR, per-symbol breakdown).

**Architecture:** The backtester already exists (`backtester/{run,feed,harness,execution,report}.py`) and reuses the production pipeline (`TrendJoinLong` → `SignalEngine` → `RiskEngine` → `PositionManager`) with no-look-ahead N+1-open fills. This plan adds: (1) `backtester/massive.py`, a pure data source returning DataFrames shaped exactly like the yfinance frames every existing consumer already accepts (`get_ticker_frame` accepts any `{symbol: DataFrame}` dict — verified in `bot/scanner/fetcher.py:334`); (2) a `source="massive"` path in `SimulatedBarFeed`; (3) cost/capital CLI knobs threaded into the harness/report; (4) an expanded `report.py`. **No changes to `bot/` production code** except none — the strategy logic is reused, not modified.

**Tech Stack:** Python 3.x, stdlib `urllib`/`json`/`csv`, pandas, pandas-market-calendars, pytest (all already present — **zero new dependencies**).

## How the current strategy decides (documented per spec Objective 1)

- **Entry:** Premarket scan (`bot/scanner/scanner.py`) filters S&P 500 names by D1 (above prior-day high), D2 (prior close > SMA200), D3 (gap ≥ 3%), price ≥ $3, ranked by gap, capped at top 20. Intraday on 5m bars (`bot/signal/signal_engine.py`): I1 close > premarket high, I2 close > today HOD, I3 RVOL-TOD ≥ 2.0, entry window 10:05–15:30 ET, Gate 4 concurrent-cap, Gate 7 daily circuit breaker (−2R).
- **Position sizing:** `bot/risk/risk_engine.py` — risk 1% of `risk.sizing_equity_usd` ($100k) per trade, stop at LOD − 1%, position capped at 10% of portfolio, max 5 concurrent / 5 trades per day.
- **Exit:** `partial_be_trail` FSM (`bot/position/manager.py`) — ⅓ off at +0.75R, breakeven stop at +1R, then 5m swing-low trail; hard force-close 15:51 ET.
- **Risk management:** daily −2R circuit breaker, kill switch (live only), paper-only guard.
All parameters come from `rules.json` via `bot/config/loader.py` — the backtester loads the same file with the same loader.

## Global Constraints

- **Zero new third-party dependencies** — HTTP via stdlib `urllib.request`; pandas / pandas-market-calendars / pytest already in `requirements*.txt`.
- **Secrets via env only** — `MASSIVE_API_KEY=YOUR_API_KEY_HERE` placeholder goes in `.env.example` (root; `.env` is already gitignored). The key is sent via `Authorization: Bearer` **header, never in a URL query string**.
- **Broker-free backtests** — nothing under `backtester/` may import `bot.gateway` (existing invariant, test-enforced by `tests/backtester/test_run.py:97`).
- **`rules.json` stays the single strategy source of truth** — backtest-only assumptions (source, costs, capital override) are CLI flags, not new rules.json keys.
- **No look-ahead** — preserve N+1-open fills (`backtester/execution.py`), `date < scan_date` baseline cutoffs, per-day premarket-high freeze, session-boundary guard.
- **Interval is 5m, fail-closed** — `--interval` exists for spec compliance but any value other than `5m` errors out (the Trend Join Long FSM is defined on 5m bars).
- **Tests never hit the network** — fakes/monkeypatch only; all new tests under `tests/backtester/`.
- **Style per CLAUDE.md** — `#!/usr/bin/env python3`, snake_case, module docstrings, `[ERROR]`/`[WARN]` to stderr, exit codes 0/1, dash-separated long CLI options.
- **Backward compatibility** — existing `compute_metrics`/`write_report` call sites (default args) must keep returning the existing 5 keys with unchanged values; existing tests assert per-key, so added keys are safe.
- One commit per task, conventional-commit messages.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backtester/massive.py` | Create | Massive REST fetch, Bearer auth, 429 retry, pagination, CSV read-through cache, key resolution |
| `.env.example` | Create | Env template incl. `MASSIVE_API_KEY=YOUR_API_KEY_HERE` (fixes dangling `deploy/run_forever.sh` reference) |
| `backtester/feed.py` | Modify | `source="massive"` load path (one 5m + one daily fetch per symbol, sliced 3 ways) |
| `backtester/report.py` | Modify | Full metric set, equity curve, per-symbol breakdown, commission model, `opened_at` CSV column |
| `backtester/harness.py` | Modify | `slippage_usd` pass-through; `opened_at` in trade-log rows |
| `backtester/run.py` | Modify | `--source/--interval/--starting-capital/--commission-per-share/--slippage-usd`; summary printed to stdout |
| `backtester/README.md` | Create | Key setup, running, interpreting results, assumptions/limitations |
| `tests/backtester/test_massive.py` | Create | Data-source unit tests (fake HTTP) |
| `tests/backtester/test_feed_massive.py` | Create | Feed massive-path tests (fake source) |
| `tests/backtester/test_report.py` | Modify | New metric/equity-curve/exposure tests |
| `tests/backtester/test_run.py` | Modify | CLI validation + plumbing tests |
| `tests/backtester/test_harness.py` | Modify | One-line `opened_at` assertion |

Verified interface facts the tasks rely on:
- `get_ticker_frame(data, sym)` does `data[sym]` with `KeyError/TypeError` catch and lowercases columns → a plain `{sym: DataFrame}` dict works everywhere (`bot/scanner/fetcher.py:334`).
- `_compute_tod_baselines` wants Title-Case `"Volume"` and handles ET/UTC/naive indexes (`bot/scanner/scanner.py:49`).
- `_evaluate_symbol(sym, data, cfg, scan_date, today_price)` routes `data` through `get_ticker_frame` and applies `date < scan_date` internally (`bot/scanner/scanner.py:115`).
- `StrategyConfig` is a non-frozen dataclass → `cfg.sizing_equity_usd` may be overridden post-load (`bot/config/loader.py:76`).
- `SimulatedExecution.__init__(self, feed, slippage_usd=0.0)` already supports slippage; the harness just never passes it (`backtester/execution.py:37`).
- `harness.setup_day` calls `feed.daily_bars(symbols)`/`feed.intraday_5m_for_tod(symbols)` **once per replay day** → the massive path must serve these from preloaded memory or it burns the 5-req/min free-tier limit.

---

### Task 1: Massive data source (`backtester/massive.py`) + `.env.example`

**Files:**
- Create: `backtester/massive.py`
- Create: `.env.example`
- Test: `tests/backtester/test_massive.py`

**Interfaces:**
- Consumes: `bot.safety.et_helpers.ET` (ZoneInfo), stdlib `urllib`/`json`, pandas.
- Produces (Task 2 relies on these exact signatures):
  - `MassiveDataSource(api_key: str, cache_dir: str = "backtester/cache/massive")`
  - `MassiveDataSource.cached_bars(yf_symbol: str, interval_tag: str, multiplier: int, timespan: str, start: str, end: str) -> pd.DataFrame` — Title-Case `Open/High/Low/Close/Volume` columns, tz-aware **ET** DatetimeIndex, sorted, deduped; empty frame (with columns) when no data.
  - `load_massive_api_key(env_path: str = ".env") -> str` — raises `MassiveApiError` on missing/placeholder key.
  - `MassiveApiError(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `tests/backtester/test_massive.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/backtester/test_massive.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'backtester.massive'`

- [ ] **Step 3: Write the implementation**

Create `backtester/massive.py`:

```python
#!/usr/bin/env python3
"""
backtester.massive — Massive API (massive.com, formerly Polygon.io) historical bar source.

Fetches split-adjusted OHLCV aggregate bars from the Massive REST API
(GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}) and
returns pandas DataFrames shaped EXACTLY like the yfinance frames the rest of
the backtester already consumes: Title-Case columns (Open/High/Low/Close/Volume)
and a tz-aware ET DatetimeIndex — so bot.scanner.fetcher.get_ticker_frame,
_evaluate_symbol, and _compute_tod_baselines all work on them unchanged (they
accept any {symbol: DataFrame} mapping).

Auth: "Authorization: Bearer <MASSIVE_API_KEY>" header ONLY — the key is never
placed in a URL query string. Resolution order: MASSIVE_API_KEY env var, then a
MASSIVE_API_KEY= line in ./.env (deploy convention: run_forever.sh sources .env;
the fallback covers ad-hoc CLI runs where the operator didn't source it).

Caching: read-through CSV cache under cache_dir (one file per
symbol/interval/range), mirroring SimulatedBarFeed's yfinance cache discipline,
so repeat backtests never re-hit the API (free tier: ~5 requests/min).
HTTP 429 responses are retried with a Retry-After-aware backoff.

Imports nothing from the broker gateway layer — pure data source.

Exports: MassiveDataSource, MassiveApiError, load_massive_api_key
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import pandas as pd

from bot.safety.et_helpers import ET

BASE_URL = "https://api.massive.com"
_PAGE_LIMIT = 50_000
_MAX_RETRIES = 5
_RETRY_FALLBACK_SLEEP_S = 15.0  # free tier is ~5 req/min; used when no Retry-After header

# Same guard as backtester.feed._SYMBOL_RE (T-06-03): cache filenames interpolate
# the symbol — restrict to ticker-shaped characters, no path separators.
_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]+")

_COLUMN_MAP = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}


class MassiveApiError(Exception):
    """Raised on a missing/placeholder API key or a non-retryable API failure."""


def load_massive_api_key(env_path: str = ".env") -> str:
    """Resolve MASSIVE_API_KEY from the environment, else from a ./.env line.

    Raises MassiveApiError (never returns a dummy) when the key is absent or
    still the YOUR_API_KEY_HERE placeholder — fail-closed, loud.
    """
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key and os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MASSIVE_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key or key == "YOUR_API_KEY_HERE":
        raise MassiveApiError(
            "MASSIVE_API_KEY is not set. Add it to .env (see .env.example) or "
            "export MASSIVE_API_KEY=<your key> — get one at https://massive.com."
        )
    return key


class MassiveDataSource:
    """Massive aggregates fetcher + CSV read-through cache."""

    def __init__(self, api_key: str, cache_dir: str = "backtester/cache/massive"):
        self._api_key = api_key
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    def _get_json(self, url: str) -> dict:
        """GET url with Bearer auth; retry 429s with Retry-After-aware backoff."""
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._api_key}"}
        )
        for attempt in range(_MAX_RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES - 1:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        sleep_s = float(retry_after)
                    except (TypeError, ValueError):
                        sleep_s = _RETRY_FALLBACK_SLEEP_S
                    time.sleep(sleep_s)
                    continue
                raise MassiveApiError(
                    f"Massive API HTTP {exc.code} for {url.split('?')[0]}"
                ) from exc
            except urllib.error.URLError as exc:
                raise MassiveApiError(f"Massive API unreachable: {exc.reason}") from exc
        raise MassiveApiError("Massive API: retries exhausted (rate limited)")

    # --------------------------------------------------------
    # Fetch + frame construction
    # --------------------------------------------------------

    @staticmethod
    def _massive_ticker(yf_symbol: str) -> str:
        """yfinance class-share symbols use '-' (BRK-B); Massive uses '.' (BRK.B)."""
        return yf_symbol.replace("-", ".")

    def fetch_bars(self, yf_symbol, multiplier, timespan, start, end) -> pd.DataFrame:
        """All aggregate bars for [start, end] as a Title-Case, ET-indexed DataFrame.

        Follows next_url pagination (auth stays in the header); dedupes on
        timestamp; sorts ascending. Returns an EMPTY frame (correct columns)
        when the API reports no results — callers check .empty, never KeyError.
        """
        url = (
            f"{BASE_URL}/v2/aggs/ticker/{self._massive_ticker(yf_symbol)}"
            f"/range/{multiplier}/{timespan}/{start}/{end}"
            f"?adjusted=true&sort=asc&limit={_PAGE_LIMIT}"
        )
        rows = []
        while url:
            payload = self._get_json(url)
            rows.extend(payload.get("results") or [])
            url = payload.get("next_url")
        if not rows:
            return pd.DataFrame(columns=list(_COLUMN_MAP.values()))
        frame = pd.DataFrame(
            {name: [r.get(key) for r in rows] for key, name in _COLUMN_MAP.items()},
            index=pd.to_datetime([r["t"] for r in rows], unit="ms", utc=True).tz_convert(ET),
        )
        return frame[~frame.index.duplicated(keep="first")].sort_index()

    def cached_bars(self, yf_symbol, interval_tag, multiplier, timespan, start, end) -> pd.DataFrame:
        """Read-through CSV cache around fetch_bars.

        interval_tag: cache filename tag ("5m" / "1d") — kept distinct from the
        yfinance cache by living under this source's own cache_dir.
        """
        if not _SYMBOL_RE.fullmatch(yf_symbol):
            raise ValueError(f"invalid symbol {yf_symbol!r} for cache filename (T-06-03)")
        path = os.path.join(self.cache_dir, f"{yf_symbol}_{interval_tag}_{start}_{end}.csv")
        if os.path.exists(path):
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
            # Mixed EDT/EST offsets in one file can parse to an object index —
            # normalise so downstream .time/.date slicing always works.
            frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(ET)
            return frame
        frame = self.fetch_bars(yf_symbol, multiplier, timespan, start, end)
        if not frame.empty:
            frame.to_csv(path)
        return frame
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/backtester/test_massive.py -v`
Expected: all PASS

- [ ] **Step 5: Create `.env.example`** (repo root; `.env` is already gitignored — verified):

```
# AI S&P Trading Bot — environment template.
# Copy to .env, fill in real values, then: chmod 600 .env
# deploy/run_forever.sh sources .env at startup (D-13: secrets via env vars only).

# --- Trading (paper only in this milestone) ---
PAPER_TRADING=true
FUTU_TRD_ENV=SIMULATE
FUTU_ACC_ID=0

# --- Telegram alerts ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# --- Massive API (https://massive.com) — historical market data for the backtester ---
MASSIVE_API_KEY=YOUR_API_KEY_HERE
```

- [ ] **Step 6: Commit**

```bash
git add backtester/massive.py tests/backtester/test_massive.py .env.example
git commit -m "feat(backtester): Massive API data source with CSV cache + .env.example key placeholder"
```

---

### Task 2: `SimulatedBarFeed` massive-source path

**Files:**
- Modify: `backtester/feed.py`
- Test: `tests/backtester/test_feed_massive.py` (new)

**Interfaces:**
- Consumes: `MassiveDataSource.cached_bars(...)` from Task 1 (injected instance — tests pass a fake).
- Produces (harness/run.py rely on these — public surface unchanged except the constructor):
  - `SimulatedBarFeed(codes, start, end, cache_dir=CACHE_DIR, source="yfinance", massive=None)` — `source="massive"` requires a `MassiveDataSource`-like object, else `ValueError`.
  - All existing accessors (`replay`, `next_bar`, `daily_bars`, `intraday_5m_for_tod`, `synthetic_today_price`, `premarket_highs`) behave identically; on the massive path `daily_bars`/`intraday_5m_for_tod` return preloaded `{yf_symbol: DataFrame}` dicts with **no network per call**.

- [ ] **Step 1: Write the failing tests**

Create `tests/backtester/test_feed_massive.py`:

```python
#!/usr/bin/env python3
"""tests.backtester.test_feed_massive — SimulatedBarFeed source="massive" path (no network).

Fixed historical dates on purpose: the massive path must NOT apply the yfinance
~60-day _enforce_window guard, so dates far in the past are exactly the case to
prove. 2025-03-07/10/11/12 are real NYSE trading days (post-DST: EDT offsets).
"""
import pandas as pd
import pytest

from bot.safety.et_helpers import ET
from backtester.feed import BacktestWindowError, SimulatedBarFeed

PRIOR = "2025-03-07"  # prior session — feeds TOD baselines, must never replay
DAY1, DAY2 = "2025-03-10", "2025-03-11"


def _frame(rows):
    """rows: (iso_ts_et, o, h, l, c, v) -> Title-Case frame with tz-aware ET index."""
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=pd.DatetimeIndex([pd.Timestamp(r[0], tz=ET) for r in rows]),
    )


class FakeMassive:
    """cached_bars stand-in: canned frames keyed by interval_tag; records calls."""

    def __init__(self, frames_5m, frames_daily):
        self._by_tag = {"5m": frames_5m, "1d": frames_daily}
        self.calls = []

    def cached_bars(self, sym, interval_tag, multiplier, timespan, start, end):
        self.calls.append((sym, interval_tag, start, end))
        return self._by_tag[interval_tag].get(sym, pd.DataFrame())


def _fake(sym="AAPL"):
    five = _frame([
        (f"{PRIOR} 09:30:00", 1.0, 1.0, 1.0, 1.0, 100),      # prior RTH (TOD only)
        (f"{DAY1} 09:00:00", 9.0, 9.5, 8.9, 9.2, 500),       # premarket
        (f"{DAY1} 09:30:00", 10.0, 10.5, 9.8, 10.2, 1000),
        (f"{DAY1} 09:35:00", 10.2, 10.8, 10.1, 10.6, 1200),
        (f"{DAY1} 16:00:00", 99.0, 99.0, 99.0, 99.0, 1),     # after-hours: excluded
        (f"{DAY2} 09:30:00", 11.0, 11.5, 10.9, 11.2, 900),
    ])
    daily = _frame([(f"{PRIOR} 00:00:00", 9.0, 9.0, 9.0, 9.0, 10_000)])
    return FakeMassive({sym: five}, {sym: daily})


def test_massive_replay_bars_are_rth_in_range_only(tmp_path):
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=_fake())
    bars = list(feed.replay(DAY1))
    assert [b["time_key"] for b in bars] == [f"{DAY1} 09:30:00", f"{DAY1} 09:35:00"]
    assert bars[0]["hod"] == 10.5 and bars[1]["cum_volume"] == 2200
    assert list(feed.replay(DAY2))[0]["time_key"] == f"{DAY2} 09:30:00"


def test_massive_premarket_highs_and_synthetic_today_price(tmp_path):
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=_fake())
    assert feed.premarket_highs(DAY1) == {"US.AAPL": 9.5}
    tp = feed.synthetic_today_price("US.AAPL", DAY1)
    assert tp.today_price == 9.2 and tp.today_high == 9.5


def test_massive_daily_and_tod_accessors_use_preloaded_frames(tmp_path):
    fake = _fake()
    feed = SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                            source="massive", massive=fake)
    n_fetches = len(fake.calls)
    daily = feed.daily_bars()
    tod = feed.intraday_5m_for_tod()
    assert len(fake.calls) == n_fetches  # no new fetches per accessor call
    assert "Volume" in daily["AAPL"].columns
    tod_keys = [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in tod["AAPL"].index]
    assert f"{PRIOR} 09:30:00" in tod_keys           # padded prior session kept
    assert f"{DAY1} 09:00:00" not in tod_keys        # premarket excluded
    assert f"{DAY1} 16:00:00" not in tod_keys        # after-hours excluded


def test_massive_skips_window_guard_but_keeps_coverage_guard(tmp_path):
    # DAY1/DAY2 are far outside yfinance's ~60-day window: massive loads fine...
    SimulatedBarFeed(["US.AAPL"], DAY1, DAY2, cache_dir=str(tmp_path),
                     source="massive", massive=_fake())
    # ...but a trading day with zero bars anywhere still fails loudly (CR-03).
    with pytest.raises(BacktestWindowError):
        SimulatedBarFeed(["US.AAPL"], DAY1, "2025-03-12", cache_dir=str(tmp_path),
                         source="massive", massive=_fake())


def test_massive_requires_source_instance(tmp_path):
    with pytest.raises(ValueError):
        SimulatedBarFeed(["US.AAPL"], DAY1, DAY1, cache_dir=str(tmp_path),
                         source="massive")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/backtester/test_feed_massive.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'source'`

- [ ] **Step 3: Implement the feed changes**

In `backtester/feed.py`:

3a. Add module constants below `_RTH_OPEN` (feed.py:75):

```python
_RTH_END = datetime.strptime("16:00", "%H:%M").time()

# Massive-source fetch padding: daily bars need >=200 prior sessions for SMA200
# (~290 calendar days) and the 5m fetch needs >=14 prior sessions for the
# RVOL-TOD baseline. Calendar-day pads with margin.
_MASSIVE_DAILY_PAD_DAYS = 400
_MASSIVE_TOD_PAD_DAYS = 30
```

3b. Replace `__init__` (keep everything through `os.makedirs`, then branch):

```python
    def __init__(self, codes: List[str], start: str, end: str, cache_dir: str = CACHE_DIR,
                 source: str = "yfinance", massive=None):
        """codes: moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
        start/end: "YYYY-MM-DD" strings bounding the requested 5m range.
        cache_dir: directory for the yfinance CSV read-through cache.
        source: "yfinance" (default, rolling ~60-day 5m window) or "massive"
            (Massive API — deep history; run.py resolves MASSIVE_API_KEY).
        massive: MassiveDataSource instance, required when source == "massive"
            (injected so tests can pass a fake; it owns its own CSV cache).
        """
        self.codes = list(codes)
        for code in self.codes:
            if not _SYMBOL_RE.fullmatch(self._yf_symbol(code)):
                raise ValueError(
                    f"invalid symbol {code!r}: cache filenames accept only [A-Z0-9.-] (T-06-03)"
                )
        self.start = start
        self.end = end
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._bars_by_code: Dict[str, list] = {}
        self._premarket_bars_by_code: Dict[str, list] = {}
        self._source = source
        self._massive = massive
        self._massive_daily: Dict[str, pd.DataFrame] = {}
        self._massive_tod: Dict[str, pd.DataFrame] = {}
        if source == "massive":
            if massive is None:
                raise ValueError('source="massive" requires a MassiveDataSource instance')
            self._load_massive()
        else:
            self._load_5m()
            self._load_premarket()
        self._enforce_coverage()
```

3c. Add `_load_massive` after `_load_premarket`:

```python
    def _load_massive(self) -> None:
        """Massive-source load: ONE 5m fetch + ONE daily fetch per symbol (CSV-cached).

        The single extended-hours 5m frame per symbol is sliced three ways:
          - RTH bars within [start, end]        -> replay bars (_materialize_bars)
          - pre-09:30 bars within [start, end]  -> premarket bars (Gate 1 / TodayPrice)
          - RTH bars over the padded range      -> RVOL-TOD baseline frames
        The daily fetch covers [start - _MASSIVE_DAILY_PAD_DAYS, end] so
        _evaluate_symbol's SMA200 / date < scan_date cutoff has real history for
        every replay day (a from-now yfinance period="1y" fetch would hold zero
        history for a deep-past window).

        No _enforce_window() here — that guard models yfinance's rolling window;
        Massive history is bounded by the account's subscription, and
        _enforce_coverage() still fails loudly per missing trading day.
        """
        start_dt = datetime.strptime(self.start, "%Y-%m-%d")
        start_d = start_dt.date()
        end_d = datetime.strptime(self.end, "%Y-%m-%d").date()
        tod_start = (start_dt - timedelta(days=_MASSIVE_TOD_PAD_DAYS)).date().isoformat()
        daily_start = (start_dt - timedelta(days=_MASSIVE_DAILY_PAD_DAYS)).date().isoformat()

        for code in self.codes:
            sym = self._yf_symbol(code)
            moomoo_code = yfinance_to_moomoo(sym)

            daily = self._massive.cached_bars(sym, "1d", 1, "day", daily_start, self.end)
            if not daily.empty:
                self._massive_daily[sym] = daily

            full_5m = self._massive.cached_bars(sym, "5m", 5, "minute", tod_start, self.end)
            if full_5m.empty:
                continue

            times = full_5m.index.time
            dates = full_5m.index.date
            rth_mask = (times >= _RTH_OPEN) & (times < _RTH_END)
            pre_mask = times < _RTH_OPEN
            in_range = (dates >= start_d) & (dates <= end_d)

            rth_padded = full_5m[rth_mask]
            if not rth_padded.empty:
                self._massive_tod[sym] = rth_padded  # prior sessions feed TOD baselines

            replay = get_ticker_frame({sym: full_5m[rth_mask & in_range]}, sym)
            if replay is not None and not replay.empty:
                self._bars_by_code[moomoo_code] = self._materialize_bars(sym, replay)

            pre = get_ticker_frame({sym: full_5m[pre_mask & in_range]}, sym)
            if pre is not None and not pre.empty:
                pre = pre.sort_index().dropna(subset=["open", "high", "low", "close", "volume"])
                self._premarket_bars_by_code[moomoo_code] = [
                    {
                        "time_key": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                    for ts, row in pre.iterrows()
                ]
```

3d. Route the two setup-data accessors through the preloaded dicts — replace the bodies of `daily_bars` and `intraday_5m_for_tod` (feed.py:352-364):

```python
    def daily_bars(self, codes: Optional[List[str]] = None):
        """Raw daily-bar mapping for _evaluate_symbol (get_ticker_frame handles dicts).

        massive source: the preloaded {yf_symbol: frame} dict — NO network per
        call (the harness calls this once per replay day; refetching would burn
        the API rate limit day after day for identical data).
        """
        if self._source == "massive":
            return dict(self._massive_daily)
        codes = codes or self.codes
        yf_symbols = [self._yf_symbol(c) for c in codes]
        data, _failed = download_daily_bars(yf_symbols)
        return data

    def intraday_5m_for_tod(self, codes: Optional[List[str]] = None):
        """Raw prepost-free 5m mapping for _compute_tod_baselines (capital "Volume")."""
        if self._source == "massive":
            return dict(self._massive_tod)
        codes = codes or self.codes
        yf_symbols = [self._yf_symbol(c) for c in codes]
        data, _failed = download_intraday_5m(yf_symbols)
        return data
```

Also update the module docstring's first paragraph to mention the massive source (one sentence: "Supports two sources: yfinance (default, rolling ~60-day 5m window) and the Massive API via backtester.massive (deep history).").

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/backtester/test_feed_massive.py tests/backtester/test_feed.py -v`
Expected: all PASS (existing yfinance-path tests must stay green — the default-source branch is byte-identical behavior).

- [ ] **Step 5: Commit**

```bash
git add backtester/feed.py tests/backtester/test_feed_massive.py
git commit -m "feat(backtester): SimulatedBarFeed massive source — deep-history replay past the yfinance window"
```

---

### Task 3: Full performance metrics + equity curve (`backtester/report.py`)

**Files:**
- Modify: `backtester/report.py` (full rewrite, backward-compatible surface)
- Test: `tests/backtester/test_report.py` (append new tests; existing per-key assertions stay untouched)

**Interfaces:**
- Consumes: harness trade-log dicts — keys `code, entry_price, exit_price, quantity, exit_reason, r_multiple, closed_at` and (Task 4 adds) `opened_at`. `closed_at`/`opened_at` may be `datetime` or `"YYYY-MM-DD HH:MM:SS"` strings.
- Produces (run.py in Task 4 calls exactly this):
  - `compute_metrics(trades, starting_capital=100_000.0, commission_per_share=0.0, start=None, end=None) -> dict` — old keys (`win_rate, avg_r_multiple, profit_factor, max_drawdown_usd, total_trades`) unchanged at defaults, plus: `starting_capital_usd, final_portfolio_value_usd, net_pnl_usd, total_commission_usd, total_return_pct, cagr_pct, sharpe_ratio, max_drawdown_pct, avg_win_usd, avg_loss_usd, exposure_pct, num_trading_days, per_symbol`.
  - `build_equity_curve(trades, starting_capital, commission_per_share=0.0, start=None, end=None) -> list[tuple[str, float]]`
  - `write_report(trades, output_dir, starting_capital=100_000.0, commission_per_share=0.0, start=None, end=None) -> dict` — writes `trades.csv` (now incl. `opened_at` column), `equity_curve.csv`, `summary.json`.

- [ ] **Step 1: Write the failing tests** — append to `tests/backtester/test_report.py` (add `from tests.backtester.fixtures import recent_session_days` and `build_equity_curve = mod.build_equity_curve` next to the existing module-level aliases at test_report.py:24):

```python
def test_metrics_costs_capital_and_extended_keys():
    trades = make_trade_log()
    m = compute_metrics(trades, starting_capital=100_000.0, commission_per_share=0.005)
    # 4 trades, qty 100/100/200/200 -> 600 shares x 2 sides x $0.005 = $6.00
    assert m["total_commission_usd"] == pytest.approx(6.0)
    # gross pnl = 1000-500+800-1000 = 300 -> net 294; per-trade net: +999,-501,+798,-1002
    assert m["net_pnl_usd"] == pytest.approx(294.0)
    assert m["final_portfolio_value_usd"] == pytest.approx(100_294.0)
    assert m["total_return_pct"] == pytest.approx(0.294)
    assert m["avg_win_usd"] == pytest.approx((999 + 798) / 2)
    assert m["avg_loss_usd"] == pytest.approx((-501 - 1002) / 2)
    assert m["profit_factor"] == pytest.approx((999 + 798) / 1503)
    assert m["per_symbol"]["US.AAA"]["total_trades"] == 1
    assert m["per_symbol"]["US.AAA"]["net_pnl_usd"] == pytest.approx(999.0)


def test_default_args_keep_legacy_gross_values():
    m = compute_metrics(make_trade_log())
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(1.2)
    assert m["max_drawdown_usd"] == pytest.approx(1000.0)


def test_equity_curve_flat_day_carry_and_sharpe():
    day1, day2 = recent_session_days(2)
    trades = [
        {"code": "US.AAA", "entry_price": 100.0, "exit_price": 110.0, "quantity": 100,
         "exit_reason": "TRAIL", "r_multiple": 2.0, "closed_at": f"{day1} 10:00:00"},
    ]
    curve = build_equity_curve(trades, 100_000.0, start=day1, end=day2)
    assert curve == [(day1, 101_000.0), (day2, 101_000.0)]  # flat day carries forward
    m = compute_metrics(trades, starting_capital=100_000.0, start=day1, end=day2)
    assert m["num_trading_days"] == 2
    assert m["max_drawdown_pct"] == 0.0
    assert m["sharpe_ratio"] > 0.0


def test_exposure_merges_overlapping_intervals():
    day1 = recent_session_days(1)[0]
    trades = [
        {"code": "US.AAA", "entry_price": 1.0, "exit_price": 1.0, "quantity": 1,
         "exit_reason": "STOP", "r_multiple": 0.0,
         "opened_at": f"{day1} 10:00:00", "closed_at": f"{day1} 11:00:00"},
        {"code": "US.BBB", "entry_price": 1.0, "exit_price": 1.0, "quantity": 1,
         "exit_reason": "STOP", "r_multiple": 0.0,
         "opened_at": f"{day1} 10:30:00", "closed_at": f"{day1} 11:30:00"},
    ]
    m = compute_metrics(trades, start=day1, end=day1)
    # union = 10:00-11:30 = 1.5h of one 6.5h session
    assert m["exposure_pct"] == pytest.approx(100.0 * 1.5 / 6.5)


def test_empty_trades_extended_zeroes():
    m = compute_metrics([], starting_capital=50_000.0)
    assert m["final_portfolio_value_usd"] == 50_000.0
    assert m["per_symbol"] == {} and m["total_trades"] == 0


def test_write_report_emits_equity_curve_and_opened_at_column(tmp_path):
    write_report(make_trade_log(), str(tmp_path))
    header = (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "opened_at" in header.split(",")
    curve_lines = (tmp_path / "equity_curve.csv").read_text(encoding="utf-8").strip().splitlines()
    assert curve_lines[0] == "date,equity"
    assert len(curve_lines) >= 2
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/backtester/test_report.py -v`
Expected: new tests FAIL (`TypeError: compute_metrics() got an unexpected keyword argument` / missing keys); existing 3 tests PASS.

- [ ] **Step 3: Rewrite `backtester/report.py`**

Full new content:

```python
#!/usr/bin/env python3
"""
backtester.report — performance metrics + trade CSV / equity-curve / summary.json writer.

Operates purely on the harness-supplied trade-log list (dicts with keys: code,
entry_price, exit_price, quantity, exit_reason, r_multiple, closed_at, and —
since the costs/capital expansion — opened_at). Never reads StateStore (nothing
in bot/ writes the trades table for a backtest; the harness list is the truth).

Cost model (report-layer only — documented assumption): commissions do NOT feed
back into position sizing or the Gate 7 circuit breaker during the replay;
slippage is applied at fill time by SimulatedExecution, commissions here.
Per-trade net pnl:
    (exit_price - entry_price) * quantity - commission_per_share * quantity * 2
(entry shares + total exit shares across partial legs = quantity each way).

avg_r_multiple stays GROSS (it is the strategy's R math, mirrored from the
replay's own Gate 7 accounting); every dollar metric (win rate, profit factor,
drawdowns, equity curve, Sharpe, CAGR) is NET of commissions.

The equity curve is REALIZED-ONLY, marked daily: the strategy force-closes all
positions intraday (time_filter.force_close_et), so end-of-day equity has no
open-position mark-to-market component by construction.

Exports: compute_metrics, build_equity_curve, write_report
"""
import csv
import json
import os
from collections import defaultdict
from datetime import datetime

import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")

_RTH_SECONDS_PER_DAY = 6.5 * 3600.0  # 09:30-16:00 ET regular session
_TRADING_DAYS_PER_YEAR = 252


def _net_pnl(trade: dict, commission_per_share: float) -> float:
    """Realized pnl net of commissions; derived, never read from a stored field."""
    gross = (trade["exit_price"] - trade["entry_price"]) * trade["quantity"]
    return gross - commission_per_share * trade["quantity"] * 2


def _to_dt(value) -> datetime:
    """Accept a datetime or a 'YYYY-MM-DD HH:MM:SS[...]' string (trade-log convention)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")


def _day_str(value) -> str:
    return str(value)[:10]


def build_equity_curve(trades: list, starting_capital: float,
                       commission_per_share: float = 0.0,
                       start=None, end=None) -> list:
    """[("YYYY-MM-DD", end-of-day equity), ...] over every NYSE day in [start, end].

    Days with no closed trades carry the prior equity forward (bot is in cash
    overnight by construction). start/end default to the first/last trade's
    close date when not supplied. Returns [] with no trades and no range.
    """
    pnl_by_day = defaultdict(float)
    for t in trades:
        pnl_by_day[_day_str(t["closed_at"])] += _net_pnl(t, commission_per_share)

    if start is None or end is None:
        traded_days = sorted(pnl_by_day)
        if not traded_days:
            return []
        start = start or traded_days[0]
        end = end or traded_days[-1]

    days = [d.strftime("%Y-%m-%d") for d in _NYSE.valid_days(start_date=start, end_date=end)]
    curve = []
    equity = float(starting_capital)
    for day in days:
        equity += pnl_by_day.get(day, 0.0)
        curve.append((day, equity))
    return curve


def _sharpe_ratio(curve: list, starting_capital: float) -> float:
    """Annualised Sharpe (rf=0) from daily equity returns (flat days = 0 return)."""
    equities = [starting_capital] + [e for _, e in curve]
    returns = [
        equities[i] / equities[i - 1] - 1.0
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = variance ** 0.5
    if std == 0:
        return 0.0
    return (mean / std) * (_TRADING_DAYS_PER_YEAR ** 0.5)


def _exposure_pct(trades: list, n_trading_days: int) -> float:
    """% of total RTH time with >=1 open position (union of [opened_at, closed_at]).

    Trades lacking opened_at (pre-expansion logs) are skipped — exposure is then
    a lower bound, never a crash.
    """
    intervals = []
    for t in trades:
        if not t.get("opened_at") or not t.get("closed_at"):
            continue
        opened, closed = _to_dt(t["opened_at"]), _to_dt(t["closed_at"])
        if closed > opened:
            intervals.append([opened, closed])
    if not intervals or n_trading_days <= 0:
        return 0.0
    intervals.sort()
    merged = [intervals[0]]
    for current in intervals[1:]:
        if current[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], current[1])
        else:
            merged.append(current)
    in_market = sum((e - s).total_seconds() for s, e in merged)
    return 100.0 * in_market / (n_trading_days * _RTH_SECONDS_PER_DAY)


def _win_loss_stats(pnls: list) -> dict:
    """win_rate / avg_win / avg_loss / profit_factor over a net-pnl list."""
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = sum(wins) / gross_loss
    else:
        # No losing dollars: inf for any non-empty trade set (legacy semantics).
        profit_factor = float("inf") if pnls else 0.0
    return {
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "avg_win_usd": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_usd": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": profit_factor,
    }


def compute_metrics(trades: list, starting_capital: float = 100_000.0,
                    commission_per_share: float = 0.0,
                    start=None, end=None) -> dict:
    """Full performance metrics for the run + per-symbol breakdown.

    trades: closed-trade dicts in chronological (exit-time) order — required
        for the trade-sequence max_drawdown_usd calculation.
    starting_capital / commission_per_share / start / end: reporting
        assumptions ("YYYY-MM-DD" window; defaults to the traded span).

    Backward-compatible keys (unchanged values at the default arguments):
    win_rate, avg_r_multiple, profit_factor, max_drawdown_usd, total_trades.
    Empty trade list returns a zeroed dict without raising.
    """
    curve = build_equity_curve(trades, starting_capital, commission_per_share, start, end)

    if not trades:
        return {
            "win_rate": 0.0, "avg_r_multiple": 0.0, "profit_factor": 0.0,
            "max_drawdown_usd": 0.0, "total_trades": 0,
            "starting_capital_usd": float(starting_capital),
            "final_portfolio_value_usd": float(starting_capital),
            "net_pnl_usd": 0.0, "total_commission_usd": 0.0,
            "total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0, "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "exposure_pct": 0.0, "num_trading_days": len(curve),
            "per_symbol": {},
        }

    pnls = [_net_pnl(t, commission_per_share) for t in trades]
    wl = _win_loss_stats(pnls)

    # Trade-sequence drawdown in dollars (pre-existing semantics, kept verbatim;
    # trades must be in chronological exit order).
    cum = peak = max_dd_usd = 0.0
    for pnl in pnls:
        cum += pnl
        peak = max(peak, cum)
        max_dd_usd = max(max_dd_usd, peak - cum)

    # Daily-equity drawdown in percent (peak-relative, starting capital included).
    eq_peak = float(starting_capital)
    max_dd_pct = 0.0
    for _, equity in curve:
        eq_peak = max(eq_peak, equity)
        if eq_peak > 0:
            max_dd_pct = max(max_dd_pct, 100.0 * (eq_peak - equity) / eq_peak)

    net_pnl = sum(pnls)
    final_value = float(starting_capital) + net_pnl
    total_return_pct = 100.0 * net_pnl / starting_capital if starting_capital > 0 else 0.0

    cagr_pct = 0.0
    if curve and final_value > 0 and starting_capital > 0:
        span_days = (
            datetime.strptime(curve[-1][0], "%Y-%m-%d")
            - datetime.strptime(curve[0][0], "%Y-%m-%d")
        ).days + 1
        if span_days >= 1:
            cagr_pct = 100.0 * ((final_value / starting_capital) ** (365.25 / span_days) - 1.0)

    per_symbol = {}
    for code in sorted({t["code"] for t in trades}):
        sym_pnls = [_net_pnl(t, commission_per_share) for t in trades if t["code"] == code]
        stats = _win_loss_stats(sym_pnls)
        stats["total_trades"] = len(sym_pnls)
        stats["net_pnl_usd"] = sum(sym_pnls)
        per_symbol[code] = stats

    return {
        "win_rate": wl["win_rate"],
        "avg_r_multiple": sum(t["r_multiple"] for t in trades) / len(trades),
        "profit_factor": wl["profit_factor"],
        "max_drawdown_usd": max_dd_usd,
        "total_trades": len(trades),
        "starting_capital_usd": float(starting_capital),
        "final_portfolio_value_usd": final_value,
        "net_pnl_usd": net_pnl,
        "total_commission_usd": commission_per_share * sum(t["quantity"] for t in trades) * 2,
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "sharpe_ratio": _sharpe_ratio(curve, float(starting_capital)),
        "max_drawdown_pct": max_dd_pct,
        "avg_win_usd": wl["avg_win_usd"],
        "avg_loss_usd": wl["avg_loss_usd"],
        "exposure_pct": _exposure_pct(trades, len(curve)),
        "num_trading_days": len(curve),
        "per_symbol": per_symbol,
    }


_CSV_FIELDS = ["code", "opened_at", "entry_price", "exit_price", "quantity",
               "exit_reason", "r_multiple", "closed_at"]


def write_report(trades: list, output_dir: str, starting_capital: float = 100_000.0,
                 commission_per_share: float = 0.0, start=None, end=None) -> dict:
    """Write trades.csv + equity_curve.csv + summary.json to output_dir.

    Creates output_dir if absent (operator-chosen local path, T-06-08).
    trades.csv: one row per trade (stdlib csv.DictWriter); rows missing
        opened_at (older logs) get an empty cell, never a crash.
    equity_curve.csv: (date, equity) per NYSE day — independently inspectable.
    summary.json: compute_metrics dict. json.dump defaults to allow_nan=True,
        so profit_factor == inf round-trips as the JSON token `Infinity`.

    Returns the metrics dict.
    """
    os.makedirs(output_dir, exist_ok=True)

    metrics = compute_metrics(trades, starting_capital, commission_per_share, start, end)
    curve = build_equity_curve(trades, starting_capital, commission_per_share, start, end)

    with open(os.path.join(output_dir, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for trade in trades:
            writer.writerow({k: trade.get(k) for k in _CSV_FIELDS})

    with open(os.path.join(output_dir, "equity_curve.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "equity"])
        writer.writerows(curve)

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return metrics
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/backtester/test_report.py -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add backtester/report.py tests/backtester/test_report.py
git commit -m "feat(backtester): full metrics — equity curve, Sharpe, CAGR, exposure, per-symbol, commissions"
```

---

### Task 4: Cost/capital knobs + CLI wiring (`harness.py`, `run.py`)

**Files:**
- Modify: `backtester/harness.py` (constructor + trade-log capture)
- Modify: `backtester/run.py` (flags, massive wiring, report kwargs, stdout summary)
- Test: `tests/backtester/test_run.py` (append), `tests/backtester/test_harness.py` (one assertion)

**Interfaces:**
- Consumes: Task 1 `MassiveDataSource`/`load_massive_api_key`/`MassiveApiError`; Task 2 feed constructor; Task 3 `write_report` kwargs.
- Produces: `BacktestHarness(cfg, feed, store, slippage_usd: float = 0.0)`; trade-log rows now include `"opened_at"`; CLI: `--source {yfinance,massive}`, `--interval` (5m only), `--starting-capital`, `--commission-per-share`, `--slippage-usd`; `main()` prints the metrics dict as JSON to stdout.

- [ ] **Step 1: Write the failing tests** — append to `tests/backtester/test_run.py` (it already imports `run_mod`; add at module top if missing: `import os` and `RULES_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "rules.json")`):

```python
def test_interval_other_than_5m_rejected(tmp_path, capsys):
    rc = run_mod.main([
        "--symbols", "US.AAPL", "--start", "2025-03-10", "--end", "2025-03-10",
        "--output-dir", str(tmp_path), "--interval", "1m",
    ])
    assert rc == 1
    assert "5m" in capsys.readouterr().err


def test_negative_costs_rejected(tmp_path, capsys):
    rc = run_mod.main([
        "--symbols", "US.AAPL", "--start", "2025-03-10", "--end", "2025-03-10",
        "--output-dir", str(tmp_path), "--commission-per-share", "-0.01",
    ])
    assert rc == 1


def test_massive_source_without_api_key_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env fallback in the tmp cwd
    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        (tmp_path / "rules.json").write_text(f.read())
    rc = run_mod.main([
        "--symbols", "US.AAPL", "--start", "2025-03-10", "--end", "2025-03-10",
        "--output-dir", str(tmp_path / "out"), "--source", "massive",
    ])
    assert rc == 1
    assert "MASSIVE_API_KEY" in capsys.readouterr().err


def test_flags_plumb_capital_slippage_and_report_kwargs(monkeypatch, tmp_path):
    captured = {}

    class FakeFeed:
        def __init__(self, *a, **k):
            pass

    class FakeHarness:
        def __init__(self, cfg, feed, store, slippage_usd=0.0):
            captured["cfg"] = cfg
            captured["slippage_usd"] = slippage_usd
            self.trade_log = []

        def setup_day(self, day, symbols):
            pass

        async def run(self):
            pass

    def fake_write_report(trades, output_dir, **kwargs):
        captured["report_kwargs"] = kwargs
        return {}

    monkeypatch.chdir(tmp_path)
    with open(RULES_JSON_PATH, encoding="utf-8") as f:
        (tmp_path / "rules.json").write_text(f.read())
    monkeypatch.setattr(run_mod, "SimulatedBarFeed", FakeFeed)
    monkeypatch.setattr(run_mod, "BacktestHarness", FakeHarness)
    monkeypatch.setattr(run_mod, "write_report", fake_write_report)

    rc = run_mod.main([
        "--symbols", "US.AAPL", "--start", "2025-03-10", "--end", "2025-03-11",
        "--output-dir", str(tmp_path / "out"),
        "--starting-capital", "55000", "--commission-per-share", "0.005",
        "--slippage-usd", "0.02",
    ])
    assert rc == 0
    assert captured["cfg"].sizing_equity_usd == 55_000.0
    assert captured["slippage_usd"] == 0.02
    assert captured["report_kwargs"] == {
        "starting_capital": 55_000.0,
        "commission_per_share": 0.005,
        "start": "2025-03-10",
        "end": "2025-03-11",
    }
```

Also in `tests/backtester/test_harness.py`, inside `test_full_replay_produces_a_closed_trade_filled_at_next_bar_open` (test_harness.py:284), directly after the line `trade = harness.trade_log[0]` (test_harness.py:296), add:

```python
    assert trade.get("opened_at") is not None, (
        "trade_log rows must carry opened_at (exposure metrics, trades.csv column)"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/backtester/test_run.py tests/backtester/test_harness.py -v`
Expected: the 4 new run tests FAIL (unknown `--interval` argument exits 2 → assertion on rc; plumbing test fails on `slippage_usd`); the harness test FAILS on the new `opened_at` assertion.

- [ ] **Step 3: Implement harness changes** — in `backtester/harness.py`:

3a. Constructor (harness.py:79): change the signature and execution construction:

```python
    def __init__(self, cfg, feed, store, slippage_usd: float = 0.0) -> None:
        """cfg: StrategyConfig (rules.json, same loader as bot/main.py).
        feed: SimulatedBarFeed — replay()/next_bar()/daily_bars()/etc.
        store: StateStore opened at a SCRATCH db_path (never data/bot_state.db —
               06-RESEARCH Pitfall 4; caller's responsibility, mirrors bot/main.py's
               StateStore(db_path=...).open() call-site discipline).
        slippage_usd: adverse per-share slippage passed to SimulatedExecution
               (entries slip up, exits slip down — T-06-11).
        """
```

and replace `self.sim_execution = SimulatedExecution(feed)` with:

```python
        self.sim_execution = SimulatedExecution(feed, slippage_usd=slippage_usd)
```

3b. Trade-log capture (harness.py:406): add `opened_at` to the appended dict:

```python
            self.trade_log.append({
                "code": code,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "quantity": pos.full_quantity,
                "exit_reason": pos.pending_exit_reason,
                "r_multiple": r_multiple,
                "opened_at": pos.opened_at,
                "closed_at": pos.updated_at,
            })
```

(`store.record_trade` keeps its existing arguments — the `trades` DB table has no `opened_at` column and Gate 7 doesn't need one.)

- [ ] **Step 4: Implement run.py changes** — in `backtester/run.py`:

4a. Imports: add `import json` to the stdlib block and extend the backtester imports:

```python
from backtester.feed import BacktestWindowError, SimulatedBarFeed
from backtester.harness import BacktestHarness
from backtester.massive import MassiveApiError, MassiveDataSource, load_massive_api_key
from backtester.report import write_report
```

4b. `build_arg_parser` — add after the `--output-dir` argument:

```python
    parser.add_argument(
        "--source", choices=["yfinance", "massive"], default="yfinance",
        help="Historical data provider (massive: deep history, needs MASSIVE_API_KEY)",
    )
    parser.add_argument(
        "--interval", default="5m",
        help="Bar interval; only 5m is supported (Trend Join Long is a 5m-bar FSM)",
    )
    parser.add_argument(
        "--starting-capital", type=float, default=None,
        help="Override risk.sizing_equity_usd for sizing AND reporting (default: rules.json)",
    )
    parser.add_argument(
        "--commission-per-share", type=float, default=0.0,
        help="Per-share commission, charged on entry and exit shares (report-layer)",
    )
    parser.add_argument(
        "--slippage-usd", type=float, default=0.0,
        help="Adverse per-share slippage applied to every simulated fill",
    )
```

4c. `main()` — extend the V5 validation block (immediately after `symbols = _parse_symbols(args.symbols)` inside the same `try`, or as plain checks right after it):

```python
    if args.interval != "5m":
        print(
            f"[ERROR] --interval must be 5m (Trend Join Long is a 5m-bar strategy), "
            f"got {args.interval!r}",
            file=sys.stderr,
        )
        return 1
    if args.commission_per_share < 0 or args.slippage_usd < 0:
        print("[ERROR] --commission-per-share and --slippage-usd must be >= 0", file=sys.stderr)
        return 1
    if args.starting_capital is not None and args.starting_capital <= 0:
        print("[ERROR] --starting-capital must be > 0", file=sys.stderr)
        return 1
```

4d. `main()` — after the successful `cfg = load_strategy_config(...)`:

```python
    if args.starting_capital is not None:
        cfg.sizing_equity_usd = args.starting_capital
    starting_capital = cfg.sizing_equity_usd or 100_000.0
```

4e. `main()` — replace the feed construction try-block and the tail of the function:

```python
    try:
        if args.source == "massive":
            massive = MassiveDataSource(load_massive_api_key())
            feed = SimulatedBarFeed(
                symbols, args.start, args.end, source="massive", massive=massive
            )
        else:
            feed = SimulatedBarFeed(symbols, args.start, args.end)
    except (BacktestWindowError, MassiveApiError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    harness = BacktestHarness(cfg, feed, store, slippage_usd=args.slippage_usd)
    for day in _trading_days(args.start, args.end):
        harness.setup_day(day, symbols)

    asyncio.run(harness.run())

    metrics = write_report(
        harness.trade_log, args.output_dir,
        starting_capital=starting_capital,
        commission_per_share=args.commission_per_share,
        start=args.start, end=args.end,
    )
    print(json.dumps(metrics, indent=2, default=str))
    return 0
```

- [ ] **Step 5: Run the full backtester suite**

Run: `python3 -m pytest tests/backtester/ -v`
Expected: all PASS (including the pre-existing end-to-end test `test_end_to_end_run_writes_summary_and_nonempty_trades_csv`, which now also exercises the default values of the new kwargs).

- [ ] **Step 6: Commit**

```bash
git add backtester/harness.py backtester/run.py tests/backtester/test_run.py tests/backtester/test_harness.py
git commit -m "feat(backtester): CLI knobs — --source massive, starting capital, commission, slippage; opened_at in trade log"
```

---

### Task 5: Documentation + validation runs + full-suite verification

**Files:**
- Create: `backtester/README.md`
- No source changes — validation only.

**Interfaces:**
- Consumes: everything above.
- Produces: operator documentation; recorded validation results for the final summary.

- [ ] **Step 1: Write `backtester/README.md`**

```markdown
# Backtester — Trend Join Long offline replay

Replays the SAME production strategy pipeline (`TrendJoinLong` → `SignalEngine` →
`RiskEngine` → `PositionManager`) against historical 5m bars, with simulated
next-bar-open fills. `rules.json` (loaded by the same `bot/config` loader the
live bot uses) is the single source of strategy truth.

## Data sources

| Source | History depth | Setup |
|---|---|---|
| `yfinance` (default) | rolling ~60 calendar days of 5m bars | none |
| `massive` | years (per your massive.com plan) | API key, below |

### Configuring the Massive API key

1. Get a key at https://massive.com.
2. `cp .env.example .env` (repo root) and replace `MASSIVE_API_KEY=YOUR_API_KEY_HERE`
   with your real key, or `export MASSIVE_API_KEY=...` in your shell.
3. Never commit `.env` (it is gitignored). The key is sent only in an
   `Authorization: Bearer` header, never in a URL.

Downloaded bars are cached as CSV under `backtester/cache/` (yfinance) and
`backtester/cache/massive/` (Massive), so repeat runs cost zero API calls.
Free-tier Massive is rate-limited (~5 req/min); the first fetch of a large
symbol list is throttled automatically (HTTP 429 retry with backoff).

## Running a backtest

```bash
python3 -m backtester.run \
  --symbols US.AAPL,US.MSFT,US.NVDA \
  --start 2025-03-10 --end 2025-06-30 \
  --source massive \
  --starting-capital 100000 \
  --commission-per-share 0.005 \
  --slippage-usd 0.01 \
  --output-dir backtester/results/q2
```

Flags: `--source {yfinance,massive}`, `--interval` (5m only — the strategy FSM
is defined on 5m bars), `--starting-capital` (overrides `risk.sizing_equity_usd`
for sizing and reporting), `--commission-per-share`, `--slippage-usd`,
`--rules-json` (default `rules.json`).

## Output (in --output-dir)

- `summary.json` — overall + per-symbol metrics (also printed to stdout):
  total/annualized return, win rate, trade count, avg win/loss, max drawdown
  ($ and %), Sharpe, profit factor, exposure %, final portfolio value.
- `trades.csv` — per-trade record: code, opened_at, entry/exit price, quantity,
  exit_reason, r_multiple, closed_at.
- `equity_curve.csv` — end-of-day realized equity per NYSE trading day.

## Interpreting results

- **Zero trades is a common, valid outcome.** The strategy is highly selective
  (gap >= 3%, close > SMA200, RVOL >= 2.0, capped watchlist); most symbols on
  most days never qualify. Distinguish this from data problems: data gaps fail
  LOUDLY (`BacktestWindowError` naming the missing trading days).
- `avg_r_multiple` is gross (strategy R math); all dollar metrics are net of
  commissions; slippage is baked into fill prices.
- Sharpe/CAGR over short windows are noisy — treat sub-quarter values as
  indicative only.

## Assumptions and limitations

- Fills at the NEXT bar's open ± slippage (no intrabar fills; no look-ahead).
- Premarket highs are approximated from the data source's premarket 5m bars,
  not the live broker's `pre_high_price` tape.
- Commissions are applied in the report layer only — they do not feed back
  into position sizing or the daily circuit breaker during the replay.
- Equity curve is realized-only (valid because every position force-closes
  intraday at 15:51 ET).
- Replays the current `partial_be_trail` exit model only; 5m bars only.
- Backtest runs never touch the broker, the live state DB, or place orders.
```

- [ ] **Step 2: Run the complete project test suite**

Run: `python3 -m pytest tests/ -q`
Expected: all tests pass (632+ pre-existing + new ones). Fix any regression before proceeding.

- [ ] **Step 3: Real-data validation run (yfinance source, recent window)**

Pick the last ~10 NYSE trading days (e.g. via `python3 -c "import pandas_market_calendars as m; d=m.get_calendar('NYSE').valid_days(start_date='2026-07-20', end_date='2026-08-11'); print(d[-10].date(), d[-1].date())"`), then:

```bash
python3 -m backtester.run --symbols US.AAPL,US.MSFT,US.NVDA,US.TSLA,US.AMD \
  --start <start> --end <end> --output-dir backtester/validation_run \
  --commission-per-share 0.005 --slippage-usd 0.01
```

Record in the final summary, clearly separated: (a) strategy-logic validation (suite results), (b) backtest results (the metrics JSON — zero trades is a valid outcome of the strict filters), (c) any data/API issues hit, (d) assumptions (as listed in the README). If `MASSIVE_API_KEY` is present in the environment or `.env`, additionally smoke-test `--source massive` over a ~5-day historical window with 1–2 symbols; if it is not, state that Massive integration is verified by unit tests and the placeholder is awaiting the operator's key.

- [ ] **Step 4: Commit**

```bash
git add backtester/README.md
git commit -m "docs(backtester): README — Massive key setup, running backtests, interpreting results"
```

---

## Self-Review (performed at plan time)

- **Spec coverage:** strategy documentation (plan preamble, README) ✓; reusable engine (pre-existing, extended) ✓; Massive API + key placeholder (Tasks 1) ✓; configurable tickers/dates/interval/capital/costs (Task 4) ✓; production-logic reuse (unchanged harness pipeline) ✓; no look-ahead/chronology (preserved invariants + fixed-date tests) ✓; missing/duplicate/invalid data (coverage guard, dedupe, dropna) ✓; caching (Task 1 CSV read-through) ✓; separation of concerns (massive=retrieval, feed/harness=execution+simulation, report=analysis) ✓; metrics incl. equity curve + per-trade record (Task 3) ✓; validation with clear categories (Task 5) ✓; tests (every task) ✓; CLI entry + README + final summary (Tasks 4–5) ✓.
- **Placeholder scan:** none — every step has concrete code or an exact command.
- **Type consistency:** `cached_bars(sym, tag, multiplier, timespan, start, end)` used identically in Task 1 (definition), Task 2 (feed + FakeMassive); `write_report(trades, output_dir, starting_capital=, commission_per_share=, start=, end=)` identical in Tasks 3 (definition) and 4 (call + plumbing test); `BacktestHarness(cfg, feed, store, slippage_usd=)` identical in Task 4 code and test.
