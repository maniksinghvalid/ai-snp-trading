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

Also exposes the two Phase 9 options endpoints (D-05): `/v3/reference/
options/contracts` (`fetch_contracts`/`cached_contracts`, unions `expired=
true` and `expired=false` since `expired` is a filter not an include-flag,
JSON read-through cache since the payload is a record list, not a bar frame)
and per-contract `O:…` daily aggregates (`cached_option_bars`, CSV
read-through cache keyed by TICKER ONLY — not the request window — via the
existing `fetch_bars` machinery, with negative caching: an empty result is
written to disk too, so a thin/illiquid contract is never re-requested,
T-09-13/WR-04).

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

# Same T-06-03 rationale: occ_ticker is interpolated into a cache filename
# (backtester/options/data.py builds these from Massive contract records).
_OPTION_TICKER_RE = re.compile(r"O:[A-Z]+\d{6}[CP]\d{8}")

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

    # --------------------------------------------------------
    # Options: contracts reference + O: daily aggregates (D-05, Phase 9)
    # --------------------------------------------------------

    def fetch_contracts(self, underlying, expiry_gte, expiry_lte) -> list:
        """All `/v3/reference/options/contracts` results for `underlying` with
        expiration in [expiry_gte, expiry_lte], deduped by ticker and sorted
        by (expiration_date, contract_type, strike_price).

        `expired` is a FILTER on this endpoint, not an "include" flag —
        `expired=true` alone silently drops every series still live as of
        the request (WR-03). Issues BOTH `expired=true` and `expired=false`
        passes (each independently paginated via next_url, auth stays in
        the header) and unions the results by ticker, so a window whose
        expiry range reaches into "still live" territory loses no series.
        """
        rows = []
        for expired_flag in ("true", "false"):
            url = (
                f"{BASE_URL}/v3/reference/options/contracts"
                f"?underlying_ticker={underlying}&expired={expired_flag}"
                f"&expiration_date.gte={expiry_gte}&expiration_date.lte={expiry_lte}"
                f"&limit=1000"
            )
            while url:
                payload = self._get_json(url)
                rows.extend(payload.get("results") or [])
                url = payload.get("next_url")
        deduped = {r["ticker"]: r for r in rows if r.get("ticker")}
        return sorted(
            deduped.values(),
            key=lambda r: (
                r.get("expiration_date", ""),
                r.get("contract_type", ""),
                r.get("strike_price") or 0,
            ),
        )

    def cached_contracts(self, underlying, expiry_gte, expiry_lte) -> list:
        """Read-through JSON cache around fetch_contracts (D-06).

        JSON, not CSV, because the payload is a record list, not a bar frame.
        """
        if not _SYMBOL_RE.fullmatch(underlying):
            raise ValueError(f"invalid underlying {underlying!r} for cache filename (T-06-03)")
        path = os.path.join(
            self.cache_dir, f"contracts_{underlying}_{expiry_gte}_{expiry_lte}.json"
        )
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        results = self.fetch_contracts(underlying, expiry_gte, expiry_lte)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f)
        return results

    @staticmethod
    def _expiry_from_occ_ticker(occ_ticker: str) -> str:
        """ISO expiry date embedded in an `O:` OCC-style ticker (already
        validated by `_OPTION_TICKER_RE`) — a local regex parse rather than
        importing `backtester.options.data.parse_massive_ticker` (a higher
        layer than this module)."""
        m = re.match(r"O:[A-Z]+(\d{2})(\d{2})(\d{2})[CP]\d{8}", occ_ticker)
        yy, mm, dd = m.groups()
        return f"20{yy}-{mm}-{dd}"

    def option_bar_cache_path(self, occ_ticker) -> str:
        """Cache filename for `occ_ticker`, exposed so callers (e.g.
        `OptionChainSource._ensure_bars`) can check cache-hit/miss without
        duplicating this format string."""
        return os.path.join(self.cache_dir, f"{occ_ticker.replace(':', '_')}_1d.csv")

    def cached_option_bars(self, occ_ticker, start, end) -> pd.DataFrame:
        """Read-through CSV cache around fetch_bars for a single `O:` ticker.

        Cache filename is keyed on TICKER ONLY (D-06/WR-04) — a contract's
        whole life is at most a few hundred bars, so fetching once and
        slicing in memory means extending `--end` in a later run never
        re-fetches, and overlapping IS/OOS windows share the same file.
        `fetch_end` is capped at the contract's own expiry (parsed from the
        ticker) so a run whose `--end` reaches past expiry never asks the
        API for bars that cannot exist.

        Negative caching (WR-04): the cache file is ALWAYS written, even for
        an empty result (header-only CSV) — a thin/illiquid contract that
        legitimately has zero bars in its life is remembered forever, not
        re-requested on every re-run.

        # ponytail: the cache is keyed by ticker, so run windows in
        # chronological order (earliest --start first); a later run that
        # requests an earlier --start than what's already cached will NOT
        # backfill the missing early bars — it will just return the
        # narrower cached range. Widen with `rm` on the specific cache file
        # if an earlier start is genuinely needed.

        Same Title-Case ET-indexed frame shape as cached_bars — fetch_bars
        already handles the `O:` prefix unchanged (_massive_ticker only
        rewrites `-`), so this delegates to it directly.
        """
        if not _OPTION_TICKER_RE.fullmatch(occ_ticker):
            raise ValueError(f"invalid option ticker {occ_ticker!r} for cache filename (T-06-03)")
        path = self.option_bar_cache_path(occ_ticker)
        if os.path.exists(path):
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
            if frame.empty:
                return frame
            frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(ET)
            return frame.loc[start:end]
        fetch_end = min(end, self._expiry_from_occ_ticker(occ_ticker))
        frame = self.fetch_bars(occ_ticker, 1, "day", start, fetch_end)
        frame.to_csv(path)  # ALWAYS write — negative caching (WR-04)
        return frame.loc[start:end] if not frame.empty else frame
