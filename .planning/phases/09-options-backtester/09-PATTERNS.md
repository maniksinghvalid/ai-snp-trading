# Phase 9: Options backtester - Pattern Map

**Mapped:** 2026-08-17
**Files analyzed:** 10 new (`backtester/options/{__init__,data,greeks,engine}.py`,
`backtester/options_run.py`, `tests/backtester/options/{__init__,conftest,test_data,test_greeks,
test_engine,test_options_run}.py`) + 1 doc (`docs/research/2026-MM-DD-options-backtest-hypotheses.md`)
+ 1 possible small extension (`backtester/massive.py`)
**Analogs found:** 10 / 10

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `backtester/options/__init__.py` | config/package-marker | — | `backtester/__init__.py` (empty) | exact |
| `backtester/options/data.py` | service (data fetch/cache) | file-I/O + request-response (HTTP) | `backtester/massive.py` | role-match (extend, don't fork) |
| `backtester/options/greeks.py` | utility (pure math) | transform | none (new pure-math module) — closest structural analog is `bot/options/strategy.py`'s pure-function style | partial (style-only) |
| `backtester/options/engine.py` | service (daily replay loop) | batch / event-driven (day-by-day) | `backtester/harness.py` | role-match |
| `backtester/options_run.py` | CLI / composition root | request-response (argparse → run → write) | `backtester/run.py` | exact |
| `tests/backtester/options/__init__.py` | test | — | `tests/backtester/__init__.py` | exact |
| `tests/backtester/options/conftest.py` | test fixture | — | `tests/options/conftest.py` + `tests/backtester/fixtures.py` | role-match |
| `tests/backtester/options/test_data.py` | test | file-I/O/mocked-HTTP | `tests/backtester/test_massive.py` | exact |
| `tests/backtester/options/test_greeks.py` | test | transform | `tests/options/test_strategy.py` (pure-function fixture style) | role-match |
| `tests/backtester/options/test_engine.py` | test | batch | `tests/backtester/test_harness.py` | exact |
| `tests/backtester/options/test_options_run.py` | test | CLI | none direct — `backtester/run.py`'s `main(argv=None)` design makes it directly testable; no existing `test_run.py` found (check before writing — if absent, model on `test_harness.py`'s harness-construction style) | partial |
| `docs/research/2026-MM-DD-options-backtest-hypotheses.md` | doc | — | `docs/research/2026-08-17-tastylive-options-research.md` | role-match |
| `backtester/massive.py` (extend) | service (HTTP+cache) | request-response + file-I/O | itself (add `cached_contracts`, `cached_option_bars` following `cached_bars`) | exact (self-extend) |

## Pattern Assignments

### `backtester/options/data.py` (service, file-I/O + HTTP)

**Analog:** `backtester/massive.py` (read in full, 165 lines)

**Imports pattern** (lines 27-36):
```python
import json
import os
import re
import time
import urllib.error
import urllib.request

import pandas as pd

from bot.safety.et_helpers import ET
```

**Auth/HTTP pattern** (`MassiveDataSource._get_json`, lines 88-111): Bearer header only,
never a URL query param; 429s retried with `Retry-After`-aware backoff, `_MAX_RETRIES = 5`,
`_RETRY_FALLBACK_SLEEP_S = 15.0`. Reuse this exact method — do not reimplement; add new
methods to `MassiveDataSource` (`cached_contracts`, `cached_option_bars`) that call
`self._get_json` the same way `fetch_bars` does.

**Pagination pattern** (`fetch_bars`, lines 122-145): `while url: payload = self._get_json(url);
rows.extend(payload.get("results") or []); url = payload.get("next_url")` — same loop shape
required for `/v3/reference/options/contracts` pagination (D-05).

**Cache pattern** (`cached_bars`, lines 147-165):
```python
def cached_bars(self, yf_symbol, interval_tag, multiplier, timespan, start, end) -> pd.DataFrame:
    if not _SYMBOL_RE.fullmatch(yf_symbol):
        raise ValueError(f"invalid symbol {yf_symbol!r} for cache filename (T-06-03)")
    path = os.path.join(self.cache_dir, f"{yf_symbol}_{interval_tag}_{start}_{end}.csv")
    if os.path.exists(path):
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(ET)
        return frame
    frame = self.fetch_bars(yf_symbol, multiplier, timespan, start, end)
    if not frame.empty:
        frame.to_csv(path)
    return frame
```
New `cached_contracts(underlying, start, end)` and `cached_option_bars(occ_ticker, start, end)`
should follow this exact read-through-CSV/JSON shape under `backtester/cache/massive/`, with a
cache key that includes endpoint + params (D-06) — e.g.
`f"contracts_{underlying}_{start}_{end}.json"` / `f"O_{occ_ticker}_{start}_{end}.csv"`.
Symbol-safety guard (`_SYMBOL_RE.fullmatch`) must be applied to any user-controlled string
used in a cache filename, per the `T-06-03` precedent comment.

**OCC/Massive ticker parse pattern:** RESEARCH.md's Code Examples section already has a
ready-to-use `parse_massive_ticker`/`format_massive_ticker` pair (regex `^O:([A-Z]+)(\d{2})
(\d{2})(\d{2})([CP])(\d{8})$`) mirroring `bot/gateway/gateway.py`'s `_OPTION_CODE_EXPIRY_RE` —
copy that directly into `data.py`.

---

### `backtester/options/greeks.py` (utility, pure transform)

**Analog:** none in-repo (new pure-math module); style precedent is `bot/options/strategy.py`'s
"fail closed, never raise into caller, return `None`/`0.0` on invalid input" convention.

RESEARCH.md's §Code Examples already contains the exact functions to write verbatim:
`_norm_cdf`, `bs_price`, `bs_delta`, `implied_vol` (bisection, `lo=1e-4, hi=5.0`), and
`dte_to_years` (calendar-day `/365.25`, matching `bot.options.strategy.option_dte`'s
convention). No further analog search needed — `math.erf`-based `N(x)`, stdlib only (D-09).

IVR: implement as min-max normalization (RESEARCH.md A1/Pitfall 4 — `(iv − min)/(max − min) ×
100` over the trailing 252-day window), per CONTEXT.md D-10's final locked wording.

---

### `backtester/options/engine.py` (service, daily replay loop)

**Analog:** `backtester/harness.py` (447 lines) for the "import the pure strategy, drive it
with a caller-owned loop" shape; `bot/options/service.py` for the exact gate order to mirror
(D-13) and the exact P&L formula (D-11/D-12).

**Import-not-copy pattern** (mirrors `backtester/harness.py`'s
`from bot.strategy.trend_join_long import TrendJoinLong`):
```python
# backtester/options/engine.py — Source: bot/options/strategy.py (D-02)
from bot.options.strategy import (
    is_monthly_expiry, option_dte, pick_expiry, passes_entry_gate,
    leg_is_liquid, pick_strikes, size_position, mark_spread, manage_decision,
)
from bot.options.config import load_options_config, OptionsConfig
```
A test must assert identity, not just behavior: `backtester.options.engine.pick_strikes is
bot.options.strategy.pick_strikes` (OBT-01, per RESEARCH.md Pattern 1).

**Gate order to mirror** (`bot/options/service.py:467-541`, D-13) — implement in this exact
sequence per trading day: daily-loss breaker check → per-day new-position cap →
per-underlying loop (`break` on cap hit, `continue` if already has a position) →
`passes_entry_gate` → `pick_expiry` → `pick_strikes` → `size_position` (folds BP cap
internally). Iterate underlyings via `sorted(symbols)` (deterministic; document the divergence
from live's `sorted(stock_id)` — RESEARCH.md Pitfall 3).

**Fill/settlement pattern** (RESEARCH.md §Code Examples, mirrors `bot/options/service.py:770-776`'s
realized-P&L formula exactly):
```python
def synthesize_bid_ask(close: float, spread_pct: float) -> tuple:
    half = close * spread_pct / 200.0
    return max(close - half, 0.01), close + half

def leg_fill_price(mid: float, side: str, slippage_usd: float) -> float:
    return mid + slippage_usd if side == "BUY" else mid - slippage_usd

def settle_at_expiry(legs: list, underlying_close: float) -> float:
    total = 0.0
    for leg in legs:
        intrinsic = (max(underlying_close - leg["strike"], 0.0) if leg["right"] == "C"
                     else max(leg["strike"] - underlying_close, 0.0))
        total += intrinsic if leg["side"] == "SELL" else -intrinsic
    return total
```

**Row-dict shape parity (Pattern 2, from RESEARCH.md):** rows fed to `pick_strikes` /
`leg_is_liquid` need keys `code`, `right` ("C"/"P"), `strike`, `delta`, `bid`, `ask`,
`open_interest`; `u` dict fed to `passes_entry_gate` needs `ivr_pct`, `ivp_pct`, `change_pct`
already in 0-100 percent units (fraction→percent conversion is the caller's/engine's job, same
as live `bot/options/service.py:85-115`).

**Report-glue pattern** (D-16, mirrors `backtester/report.py` but does NOT call its public API):
```python
# Source: backtester/report.py — import module-private ratio helpers only
from backtester.report import _sharpe_ratio, _sortino_ratio, _calmar_ratio, _win_loss_stats
```
Do NOT import/call `compute_metrics`/`write_report`/`build_equity_curve`/`_net_pnl` — those
assume `(exit_price - entry_price) * quantity` per-share stock P&L (`backtester/report.py:41-45,
58-69`), which is wrong for a multi-leg credit spread (RESEARCH.md Pitfall 2). Engine computes
its own dollar P&L per closed trade using the `(credit - net_exit) * 100 * qty` formula from
`bot/options/service.py:770-776`, then builds a bare `[(date, equity)]` curve (see RESEARCH.md's
`build_options_equity_curve` example) before handing it to the imported ratio functions.

---

### `backtester/options_run.py` (CLI / composition root)

**Analog:** `backtester/run.py` (224 lines, read in full)

**Structure to mirror exactly** — argparse builder, date/symbol validation before any fetch,
config load via the phase's own loader, engine construction, `json.dumps(metrics, indent=2,
default=str)` on stdout, `return 0/1` exit-code style (not `sys.exit` inline):
```python
# backtester/run.py:43-84 argparse shape
parser.add_argument("--symbols", required=True, ...)
parser.add_argument("--start", required=True, ...)
parser.add_argument("--end", required=True, ...)
parser.add_argument("--rules-json", default="rules.json", ...)   # -> "--rules" per D-01, default rules_options.json
parser.add_argument("--output-dir", ...)                          # -> backtester/results/options/<run-id>/
```
`--set entry.ivr_min=20`-style override parsing (D-15) is new (no existing analog does dotted-
path CLI overrides) — write a small `apply_overrides(cfg, set_args)` helper in `options_run.py`
itself; keep it inline, no new module (still inside the "≤5 modules" budget).

**Validation-before-fetch pattern** (`run.py:130-165`): validate dates/symbols/numeric flags
with plain `ValueError` → `print(f"[ERROR] {exc}", file=sys.stderr); return 1` BEFORE
`load_strategy_config`/any network call — same ordering for `options_run.py` (validate before
`load_options_config`, before any `MassiveDataSource` construction).

**Main-loop wiring** (`run.py:193-219`): construct data source → construct engine → loop
`_trading_days(start, end)` (reuse `pandas_market_calendars.get_calendar("NYSE")`, exact same
`_trading_days` helper) → run → write trades.csv/summary.json/config.json → print metrics JSON.

---

### `tests/backtester/options/test_data.py` (test, mocked-HTTP)

**Analog:** `tests/backtester/test_massive.py`

**No-network mock pattern** (lines 31, 50, 64, 73, 79, 85-118):
```python
monkeypatch.setattr(src, "_get_json", lambda url: payload)
# or for pagination / retry tests:
monkeypatch.setattr("backtester.massive.urllib.request.urlopen", fake_urlopen)
monkeypatch.setattr("backtester.massive.time.sleep", lambda s: None)
```
`test_429_retries_then_succeeds` (line 85) is the exact pattern to replicate for the two new
`cached_contracts`/`cached_option_bars` methods — no live network calls in tests (D-18).

---

### `tests/backtester/options/test_engine.py` (test, batch/import-identity)

**Analog:** `tests/backtester/test_harness.py`

Follow its style of asserting the harness "reuses" the real strategy functions by import
identity / behavioral parity against the real functions, not a parallel copy — apply the same
to `backtester/options/engine.py`'s imports from `bot.options.strategy`.

---

### `tests/backtester/options/conftest.py` (test fixture)

**Analog:** `tests/options/conftest.py` + `tests/options/test_strategy.py::_grid` (lines 60-71,
per RESEARCH.md Pattern 2) — the option-chain-grid fixture helper that builds rows with keys
`code`, `right`, `strike`, `delta`, `bid`, `ask`, `open_interest` matching the live
`screen_options` shape; adapt for backtest-derived (BS-computed) rows instead of live-fetched
ones.

---

### `docs/research/2026-MM-DD-options-backtest-hypotheses.md`

**Analog:** `docs/research/2026-08-17-tastylive-options-research.md` (structure/tone reference)
— write the hypotheses doc (H1 IVR 20 vs 30, H2 delta 0.16 vs 0.20, H3 IC vs PCS) BEFORE any
real-data run (D-14), following that doc's provenance-citation and confidence-labeling style.

## Shared Patterns

### Import-not-copy (cross-cutting: engine.py, greeks.py touch points)
**Source:** `backtester/harness.py` top-of-file imports
**Apply to:** `backtester/options/engine.py` — every strategy decision function must be an
`from bot.options.strategy import ...` line, never reimplemented.

### Bearer-auth + 429-backoff HTTP
**Source:** `backtester/massive.py::MassiveDataSource._get_json` (lines 88-111)
**Apply to:** `backtester/options/data.py` — extend `MassiveDataSource`, don't fork a parallel
HTTP client.

### CSV/JSON read-through cache under `backtester/cache/massive/`
**Source:** `backtester/massive.py::cached_bars` (lines 147-165)
**Apply to:** `backtester/options/data.py`'s new `cached_contracts`/`cached_option_bars`.

### Ratio-math reuse without P&L-shape reuse
**Source:** `backtester/report.py` lines 97 (`_sharpe_ratio`), 110 (`_sortino_ratio`), 131
(`_calmar_ratio`), 164 (`_win_loss_stats`)
**Apply to:** `backtester/options/engine.py`'s report glue — import these four names explicitly;
never call `compute_metrics`/`write_report`/`build_equity_curve`/`_net_pnl` (stock-P&L-shaped).

### NYSE trading-day stepping
**Source:** `backtester/run.py::_trading_days` (lines 111-118), `mcal.get_calendar("NYSE")`
**Apply to:** `backtester/options_run.py` (day loop), `backtester/options/greeks.py` (252-day
IVR trailing window), `backtester/options/engine.py` (daily replay loop) — one shared calendar
object pattern, not three separate date-math implementations.

### CLI composition-root shape (argparse → validate → load config → run → write report)
**Source:** `backtester/run.py::main` (lines 121-219)
**Apply to:** `backtester/options_run.py`.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `backtester/options/greeks.py` | utility | transform | No existing Black-Scholes/IV module in this repo; RESEARCH.md's §Code Examples already supplies ready-to-copy stdlib implementations — use those directly rather than searching further. |
| `--set key=value` CLI override parser in `options_run.py` | CLI helper | transform | No existing dotted-path config-override parser in the codebase; write inline, small (a few lines), no new module needed. |

## Metadata

**Analog search scope:** `backtester/`, `bot/options/`, `bot/gateway/`, `tests/backtester/`,
`tests/options/`, `docs/research/`
**Files scanned:** `backtester/massive.py`, `backtester/harness.py`, `backtester/run.py`,
`backtester/report.py`, `backtester/compare.py`, `bot/options/strategy.py`,
`bot/options/service.py`, `bot/gateway/gateway.py`, `tests/backtester/test_massive.py`,
`tests/backtester/test_harness.py`, `tests/options/conftest.py`, `tests/options/test_strategy.py`
**Pattern extraction date:** 2026-08-17
