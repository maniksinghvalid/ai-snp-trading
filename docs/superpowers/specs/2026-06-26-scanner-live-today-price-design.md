# Scanner Live "Today" Price — Design

**Date:** 2026-06-26
**Status:** Approved (design); implementation pending GSD plan
**Area:** `bot/scanner/` (fetcher, scanner, strategy filters consume it)

## Problem

The premarket scan and every early-session scan produced an **empty watchlist** →
nothing subscribed → no 5m bars → no signals → no entries.

Root cause: `_evaluate_symbol` requires a yfinance **daily** bar dated `scan_date`
(today) to read `today_open` (gap / D3) and `today_close` (D1 live proxy). yfinance
publishes the current day's *daily* bar with a multi-hour lag (observed ~11:00 ET on
2026-06-26). So the premarket scan (08:30 ET) and intraday rescans through ~10:50 ET
skipped **every** symbol with `symbol_skipped_no_today_bar`
([bot/scanner/scanner.py:126](../../../bot/scanner/scanner.py)), and the bot was stopped
~11:03 ET before the post-publication rescan could recover.

Verified empirically (2026-06-26):
- `yf.download(interval="1d")` today-bar present only after ~11:00 ET.
- `yf.download(interval="1m", prepost=True)` returns 440 bars for today starting 04:00 ET
  (premarket) — i.e. live/premarket prices ARE available premarket via the 1m feed.

## Goal

Source today's open / current price from a **live** path so the premarket and at-open
scans build a real watchlist, WITHOUT changing the strategy's gap definition, ranking,
no-look-ahead guarantees, or `rules.json` as the single source of strategy params, and
WITHOUT consuming Moomoo broker quota (per the CLAUDE.md data/execution split).

## Decisions (from brainstorming)

1. **Live price source: yfinance 1m intraday (`prepost=True`).** Keeps scanning on
   yfinance (honors the quota-avoidance constraint), confirmed available premarket.
   Rejected: Moomoo `get_market_snapshot` for ~500 symbols (quota risk, conflicts with
   the data/execution split).
2. **Premarket gap semantics: premarket-last as proxy, exact open after 09:30.**
   - Before 09:30 ET: `today_open = today_price =` latest premarket 1m close (provisional
     gap-up watchlist; standard premarket %-change behavior).
   - At/after 09:30 ET: `today_open =` open of the first regular-session 1m bar (≥09:30),
     giving the authoritative gap; intraday rescans refine the premarket watchlist.
3. **Hybrid fetch.** Slow-moving inputs (prior close, prior-day high, SMA200, RVOL
   baseline) stay on yfinance **daily** (rows strictly `date < scan_date`). Only today's
   open/current/high move to the 1m source.

## Architecture

Two data sources feed `_evaluate_symbol`, split by input volatility:

| Input | Source | No-look-ahead |
|---|---|---|
| prior close, prior-day high, SMA200, RVOL baseline | yfinance **daily** (`date < scan_date`) | unchanged — strict cutoff |
| today open / current price / today HOD | yfinance **1m** (`prepost=True`), today only | it IS today's live data |

### Components (all in `bot/scanner/`)

1. **`fetcher.py`**
   - `download_intraday_1m(symbols) -> (data, failed_set)` —
     `yf.download(interval="1m", period="1d", prepost=True, group_by="ticker",
     auto_adjust=True, ...)`. Mirrors `download_daily_bars` shape/contract (returns the
     yf object + failed set; logs an audit entry).
   - `resolve_today_price(frame_1m, scan_ts, now_et) -> TodayPrice | None` — pure
     function (no network, no `now()` internally — clock is injected). Returns a small
     struct: `today_open`, `today_price` (latest 1m close), `today_high`.
     - At/after 09:30 ET (per `now_et`): `today_open` = open of first 1m bar with ET time
       ≥ 09:30; `today_price` = latest 1m close; `today_high` = max high of regular-session
       bars.
     - Premarket (`now_et` < 09:30 ET): `today_open = today_price =` latest premarket 1m
       close; `today_high` = premarket high.
     - Empty/None frame, or no usable bar for the current phase → return None.

2. **`scanner.py` `_evaluate_symbol`**
   - Gains a `today_price: TodayPrice` parameter. The caller (`compute_candidates` /
     scan orchestrators) resolves it per-symbol via `resolve_today_price(...)` from the
     batched 1m object and passes it in; `_evaluate_symbol` no longer reads today from the
     daily frame. This keeps `_evaluate_symbol` deterministic and unit-testable (no network,
     no clock).
   - Replace the daily today-row lookup (`today_rows = frame.index[... == scan_ts ...]`,
     scanner.py:126) with the injected `today_price` struct.
   - Build synthetic `today_row` = `{open: today_open, close: today_price, high: today_high}`.
   - Keep `daily_2row = [prior_row, today_row]` → `TrendJoinLong.passes_daily_filters(...)`
     and `gap_pct = (today_open - prior_close)/prior_close * 100` **unchanged**. Only the
     SOURCE of today's numbers changes; gap definition, D1/D2/D3, and `gap_pct` ranking
     are identical.
   - New fail-closed skip log: `symbol_skipped_no_intraday_price` (replaces the
     `no_today_bar` cause).

3. **`run_premarket_scan` / `run_intraday_rescan`**
   - Fetch the 1m frame once per scan (batched via `download_intraday_1m`), thread it to
     `_evaluate_symbol` alongside the existing daily fetch. Daily fetch path unchanged.

## Data Flow (per scan)

```
run_premarket_scan / run_intraday_rescan(scan_date, now_et)
  ├─ universe  = fetch_sp500_symbols()
  ├─ daily     = download_daily_bars(universe)          # 1y/1d — slow inputs
  ├─ intraday  = download_intraday_1m(universe)         # 1d/1m, prepost — NEW
  └─ for each symbol:
        daily_frame = get_ticker_frame(daily, sym)                  # prior close/high, SMA200, RVOL (date < today)
        today       = resolve_today_price(get_ticker_frame(intraday, sym), scan_ts, now_et)
        if daily_frame is None or today is None: skip (fail-closed)
        today_row   = {open: today.today_open, close: today.today_price, high: today.today_high}
        candidate   = _evaluate_symbol(...)             # gap_pct, D1/D2/D3, ranking — UNCHANGED
  └─ rank by gap_pct DESC → top-20 → persist → subscribe
```

## Error Handling / Degradation

- **Per-symbol:** missing/empty 1m frame or no usable bar for the current phase →
  `resolve_today_price` returns None → skip with `symbol_skipped_no_intraday_price`
  warning (same fail-closed contract as the prior `no_today_bar`).
- **Whole-fetch:** if `download_intraday_1m` yields nothing for the entire universe
  (yfinance outage), raise the existing `ScanDegradationError` so the scan fails loudly
  instead of silently emptying the watchlist (distinct signal from "no candidates passed").
- **rules.json** remains the single source of strategy params — no new thresholds; gap
  definition unchanged.

## Testing

- **Unit `resolve_today_price`:** synthetic 1m frames — (a) premarket-only before 09:30 →
  `today_open == latest premarket close`; (b) frame spanning 09:30 → `today_open == first
  regular-session bar open` (NOT premarket); (c) empty/None → None.
- **Unit `_evaluate_symbol`:** inject gap-up today price → candidate emitted with correct
  `gap_pct`; gap below `D3` → skipped; assert daily slow-inputs still use `date < scan_date`
  (no-look-ahead).
- **Integration (regression for THIS bug):** premarket-time scan with gap-up 1m data
  produces a **non-empty** watchlist.
- Full suite stays green (currently 440 passed / 1 skipped).

## Backtest Seam (no work now)

Phase 6 backtester is unbuilt. `resolve_today_price` takes the 1m frame + clock as
arguments (no internal `now()`/network) so a future backtester can replay historical 1m
without touching live code. No backtester implementation in this change.

## Out of Scope

- Moomoo snapshot path / hybrid top-N broker confirmation (rejected for quota).
- RVOL today-volume computation (unchanged — handled downstream in the signal engine).
- Single-instance guard, Telegram config, prior debug fixes (separate items).
- Phase 6 backtester.
