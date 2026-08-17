---
phase: 09-options-backtester
reviewed: 2026-08-17T19:34:44Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - backtester/options/data.py
  - backtester/options/greeks.py
  - backtester/options/engine.py
  - backtester/options/report.py
  - backtester/options_run.py
  - backtester/massive.py
  - tests/backtester/options/conftest.py
  - tests/backtester/options/test_data.py
  - tests/backtester/options/test_greeks.py
  - tests/backtester/options/test_engine.py
  - tests/backtester/options/test_report.py
  - tests/backtester/options/test_options_run.py
findings:
  critical: 1
  warning: 5
  info: 9
  total: 15
status: issues_found
---

# Phase 9: Code Review Report

**Reviewed:** 2026-08-17T19:34:44Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Reviewed the offline options backtester (data layer, BS greeks/IV, daily replay engine, report glue, CLI, and the Massive data-source extension) plus its tests. Cross-checked against `bot/options/strategy.py` (imported, not copied — verified by identity tests), `bot/options/service.py:451-541,770-826` (cap-gate order and P&L formula), and `backtester/report.py` (only the four private ratio helpers are imported).

What holds up: the P&L sign/multiplier (`(credit - net_exit) * 100 * qty`) matches service.py; adverse-slippage direction is correct on both open and close; expiry settlement sign convention matches `mark_spread`; the entry-scan gate order (breaker -> breaker-tripped -> per-day cap -> sorted loop with break-on-cap / continue-if-open -> gate -> expiry -> strikes -> size) matches `_scan_and_open`; exact-day bar lookup for entry selection is enforced structurally; `--set` type coercion is backstopped by the jsonschema type check in `load_options_config`; the API key is header-only and never lands in a URL, log line, or filename; cache filenames are regex-guarded against path separators.

Key concerns: (1) positions still open at `--end` are silently dropped from trades.csv/summary.json, biasing every reported metric optimistically (winners are closed early by profit-target, losers linger); (2) `implied_vol` returns ~5.0 instead of `None` for a NaN price/spot, which would poison the IV-rank series for a full window; (3) the daily-loss breaker ignores unrealized P&L, diverging from the live manage-path trip; (4) `fetch_contracts` uses `expired=true` only, so any window whose look-forward reaches past "today" silently loses its chain; (5) the option-bar cache never stores empty results and is keyed on the exact `[warmup_start, end]` window, so re-runs and IS/OOS arms re-hit the rate-limited API for the same contracts — this compounds the known eager-fetch infeasibility.

## Critical Issues

### CR-01: Positions still open at `--end` are silently excluded from every reported metric

**File:** `backtester/options/engine.py:166-169`, `backtester/options/report.py:52-69,92-151`, `backtester/options_run.py:251-269`
**Issue:** `engine.run(days)` replays `[start, end]` and only `_record_close` appends to `trade_log`. Anything in `self._open` when the loop ends is never marked, never settled, and never reported — `write_options_report` sees only closed trades. Because `manage_decision` books winners at the 50% profit target early and lets losers ride to `manage_dte`/expiry, the survivor set at `--end` is systematically skewed toward open losers, so `total_pnl_usd`, `win_rate`, `profit_factor`, drawdown and Sharpe/Sortino are all biased upward. With 45-DTE entries and up to `max_concurrent_positions` (8) open, this can be a material fraction of a ~10-month OOS window's trades. The hypotheses doc (docs/research/2026-08-17-options-backtest-hypotheses.md:48) states "no OOS trade can be left artificially open/unsettled" — the engine does not enforce that. summary.json also carries no `open_positions_at_end` count, so a reader cannot detect the omission.
**Fix:** After the replay loop, mark every remaining position at the last available quotes and record it with a distinct exit reason so it is visible and auditable, e.g. in `OptionsBacktestEngine`:
```python
def close_open_at_end(self, day: str) -> None:
    """Mark-to-market every still-open position on `day` (exit_reason='end_of_window')."""
    for code, pos in list(self._open.items()):
        chain = self.chains[code]
        quotes, carried = {}, False
        for leg in pos["legs"]:
            close = chain.bar_close(leg["code"], day)
            if close is None:
                close = chain.last_known_close(leg["code"], day); carried = True
            if close is None:
                break
            bid, ask = synthesize_bid_ask(close, self.spread_pct)
            quotes[leg["code"]] = {"bid": bid, "ask": ask}
        else:
            net_exit = 0.0
            for leg in pos["legs"]:
                q = quotes[leg["code"]]; mid = (q["bid"] + q["ask"]) / 2
                fill = leg_fill_price(mid, "BUY" if leg["side"] == "SELL" else "SELL", self.slippage_usd)
                net_exit += fill if leg["side"] == "SELL" else -fill
            self._record_close(code, pos, day, "end_of_window", net_exit,
                               self.commission_per_leg * len(pos["legs"]) * pos["qty"], carried)
```
Call it from `options_run.main` after `engine.run(...)` with `trading_days(args.start, args.end)[-1]`, and add `open_positions_at_end: len(engine._open)` to `extra_assumptions` so any un-markable residue is still visible in summary.json. Alternatively report `unrealized_open_usd`/`open_positions_at_end` separately if end-of-window closes must not count as trades — but they must not be silently dropped.

## Warnings

### WR-01: `implied_vol` returns ~5.0 (not `None`) for a NaN price or spot — violates the fail-closed contract and can poison the IV-rank series

**File:** `backtester/options/greeks.py:97-125`
**Issue:** Every comparison against NaN is False, so `price < floor` never triggers, `f_lo * f_hi > 0` never triggers, and the bisection loop drifts `lo -> hi` until `(hi - lo) < tol` and returns `mid ~= 4.9999997`. Verified: `implied_vol(float('nan'), 100, 100, 45/365.25, 0.045, 'C') == 4.999999701982737`, likewise for a NaN spot. NaN inputs are reachable: `OptionChainSource.load` does `float(row["Close"])` on frames that came from a CSV cache (an empty cell parses to NaN) or from a Massive payload with `c: null` (`r.get(key)` -> None -> NaN column). One such value in `atm_iv` appends ~5.0 to `_iv_series`, after which `iv_rank` normalises against max=5.0 for the next 252 observations and IVR collapses toward 0 — silently suppressing every entry for a year of replay. In `build_rows` a NaN close also yields a garbage delta that can win `_closest_delta` for the short strike (then fails `leg_is_liquid` on the NaN bid, killing the whole underlying for that day).
**Fix:** Guard at the top of `implied_vol` (and `atm_iv`/`build_rows` callers inherit it):
```python
if t_years <= 0 or not (price == price) or not (spot == spot) or spot <= 0 or strike <= 0:
    return None
```
and in `OptionChainSource.load` skip rows whose Close is NaN/non-positive (`if not (close == close) or close <= 0: continue`). Add a `test_iv_nan_inputs_fail_closed` case.

### WR-02: Daily-loss breaker ignores unrealized P&L — cap-gate divergence from `service.py`

**File:** `backtester/options/engine.py:313-324,334-336`
**Issue:** Live has two trip points: `_job_entry_scan` calls `_check_daily_breaker(today, 0.0)` (realized-only) AND `_job_manage` calls `_check_daily_breaker(today, unrealized_total)` (service.py:718) after marking every open position — a large open drawdown blocks entries for the rest of the day even with zero realized losses. `_manage_day` computes `mark`/`credit` for every position (line 243-244) but discards the unrealized total, and `_check_daily_breaker` sums realized `trade_log` rows only. The docstring cites only the entry-scan call and omits the manage-path trip. Net effect: the backtester keeps opening spreads on days where the live bot would have been breaker-locked, overstating trade count precisely on the worst days.
**Fix:** Have `_manage_day` accumulate `unrealized += (credit - mark) * _CONTRACT_MULTIPLIER * pos["qty"]` for every position it does NOT close (mirroring `_manage_position`'s return value), stash it as `self._unrealized_today`, and evaluate the breaker as `realized_today + self._unrealized_today > limit` — with the realized-only check kept as well (live evaluates both; either can trip).

### WR-03: `fetch_contracts` requests `expired=true` only — a window whose look-forward reaches past "today" silently loses its chain

**File:** `backtester/massive.py:190-195`; caller `backtester/options/data.py:150-151`
**Issue:** On the Massive/Polygon contracts endpoint `expired` is a filter, not an "include" flag: `expired=true` returns ONLY contracts that have already expired as of the request. `load()` requests expiries up to `end + max_dte`; if any of those are still live at run time (any `--end` later than today minus `max_dte`), those series are simply absent — no error, fewer candidates, `pick_expiry` may return `None` or a worse expiry, and the run reports as if the chain were thin. 09-RESEARCH.md:398-399 explicitly calls for `expired=true` and `expired=false` unioned; the implementation does half. The shipped IS/OOS windows (ending 2025-07-31 / 2026-06-15) happen to be fully expired as of 2026-08-17, so this is latent, but any operator re-run with a fresher `--end` produces silently wrong results.
**Fix:** In `fetch_contracts`, run the same paginated loop for both `expired=true` and `expired=false` and dedupe by ticker (the existing `deduped` dict already handles the union); or, when `expiry_lte` <= today, keep the single call and add a loud `ValueError` in `OptionChainSource.load` when `expiry_lte > date.today()` explaining that unexpired series will be missing.

### WR-04: Option-bar cache never stores empty results and is keyed on the exact `[warmup_start, end]` window — compounds the eager-fetch rate-limit problem

**File:** `backtester/massive.py:236-248` (also `cached_bars` :164-176); `backtester/options/data.py:171`; `backtester/options_run.py:236`
**Issue:** (a) `cached_option_bars` only writes the CSV `if not frame.empty`. Plan 09-01's own probe (data.py:29-35) shows far-OTM contracts return ZERO rows for entire months, and the strike band admits tens of thousands of such contracts — every re-run re-fetches every empty contract at ~5 req/min. (b) The filename embeds `start`/`end`, and `start` here is `warmup_start`, which changes with `--start` and `--iv-warmup-days`; the IS and OOS arms therefore share no cache entries for the same `O:` ticker even where their date ranges overlap. Both behaviours multiply the number of live requests the orchestrator already flagged as infeasible.
**Fix:** Cache negative results (write the empty frame with its header, or a zero-byte sentinel, and treat "file exists" as a hit); key the option-bar cache on the ticker only (the contract's full life is at most a few hundred bars — fetch `[listing, expiry]` once and slice in memory), e.g. `f"{occ_ticker.replace(':', '_')}_1d.csv"` covering `[expiry - 400d, expiry]`, then `frame.loc[start:end]` in `OptionChainSource.load`.

### WR-05: `main` writes `config.json` into a run dir and only then validates it — a rejected override still leaves a half-populated run directory, and no run-level exception boundary exists

**File:** `backtester/options_run.py:214-223,243-251`
**Issue:** `os.makedirs(out_dir)` + `config.json` write happen before `load_options_config`, before the API-key check, and before any data fetch, so every failed invocation litters `backtester/results/options/<uuid>/` with a config-only directory (the test suite codifies this ordering). Separately, `engine.run(...)`, `_prime_iv_series`, and `write_options_report` sit outside any `try`, so a `KeyError`/`TypeError` from a malformed cached CSV (e.g. missing `Close` column) escapes as a stack trace instead of the `[ERROR]` + exit 1 contract the module promises. Not a security issue (operator-supplied `--out`), but it degrades the "1 on any validation/config/network failure" contract stated in the docstring.
**Fix:** Validate the effective config in-memory first (`load_options_config` accepts a path — write to a `tempfile.NamedTemporaryFile` or add a `load_options_config_from_dict` shim), create `out_dir` only after config + API key succeed, and wrap the replay/report block in `try/except Exception as exc: print(f"[ERROR] {exc}", file=sys.stderr); return 1`.

## Info

### IN-01: Warm-up day list drops the last real warm-up day when `--start` is not an NYSE trading day

**File:** `backtester/options_run.py:249`
**Issue:** `trading_days(warmup_start, args.start)[:-1]` assumes `args.start` is itself a trading day (so `[:-1]` removes it). If `--start` is a Saturday/holiday, the last element is the final warm-up day, which is dropped; the IV series is primed with `warmup_days - 1` observations.
**Fix:** `warmup_days = [d for d in trading_days(warmup_start, args.start) if d < args.start]`.

### IN-02: `--set` accepts `NaN`/`Infinity` literals that pass the numeric schema type

**File:** `backtester/options_run.py:95-98`
**Issue:** `json.loads("NaN")` -> `float('nan')` (Python's json is permissive) and jsonschema `"type": "number"` accepts it, so `--set entry.ivr_min=NaN` yields `ivr < NaN` always False and the gate passes unconditionally. Operator-only, but a silent footgun.
**Fix:** `json.loads(value, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"--set {arg!r}: {c} is not allowed")))`.

### IN-03: Unused strategy imports and production-unused ticker helpers

**File:** `backtester/options/data.py:47` (`is_monthly_expiry`), `backtester/options/engine.py:45,46` (`is_monthly_expiry`, `leg_is_liquid`), `backtester/options/data.py:55-77` (`parse_massive_ticker`/`format_massive_ticker` only referenced from tests)
**Issue:** The imports exist to satisfy identity tests, not because the module calls them; a reader assumes engine.py applies `leg_is_liquid` itself (it does not — `pick_strikes` does). The two ticker helpers have no production caller.
**Fix:** Either use them (e.g. `parse_massive_ticker` to validate contract records in `load()` instead of trusting `contract_type`/`strike_price` fields) or drop them and point the identity test at the functions the module actually calls.

### IN-04: `dte_to_years` imports inside the function body on every call

**File:** `backtester/options/greeks.py:131`
**Issue:** `from bot.options.strategy import option_dte` inside a hot helper (called per contract per day). No circular-import reason exists (data.py already imports strategy at module level).
**Fix:** Move the import to the module header.

### IN-05: Late expiry settlement uses a post-expiry underlying close

**File:** `backtester/options/engine.py:217-224`
**Issue:** If the underlying has no bar on the expiry date, the position settles on the next day with a close from AFTER expiry. Rare (holiday-shifted expiries are stored with their actual date), but the settle price is then not the expiry-day price.
**Fix:** Settle against `last_known_close`-style carry of the underlying at-or-before expiry, or record `settled_late=True` in the trade row.

### IN-06: Strike-band narrowing uses the whole window's (future) close range

**File:** `backtester/options/data.py:144-146`
**Issue:** `closes.min()/max()` over `[warmup_start, end]` includes closes after day t. It only widens the candidate set (today's close is always inside the band), so it cannot exclude a strike near today's price and does not change `pick_strikes`' selection in practice, but it is a documented-look-ahead-free module quietly using future data.
**Fix:** Note the exception in the class docstring next to `last_known_close`, or narrow per-expiry against the closes observed up to that expiry.

### IN-07: `_prime_iv_series` does not prime `_prior_close`, so day-1 `change_pct` is `None`

**File:** `backtester/options_run.py:155-171`; `backtester/options/engine.py:187-189`
**Issue:** The fear gate (`change_pct <= -fear_drop_pct`) cannot fire on the first replay day because the previous close was never recorded during warm-up.
**Fix:** In `_prime_iv_series`, also `engine._prior_close[code] = underlying_px` for each warm-up day.

### IN-08: `fetch_contracts` sort key raises on a `null` `strike_price`

**File:** `backtester/massive.py:202-209`
**Issue:** `r.get("strike_price", 0)` returns `None` when the key is present with a null value; sorting then raises `TypeError` for the whole page. `load()` already skips such rows, but the crash happens earlier, in the fetch.
**Fix:** `r.get("strike_price") or 0`.

### IN-09: Breaker P&L includes commissions; live realized P&L does not

**File:** `backtester/options/engine.py:318-320` vs `bot/options/service.py:775-781`
**Issue:** `pnl_usd` in `trade_log` is net of open+close commissions, while `realized_pnl_usd` stored live is `(credit - net_exit) * 100 * qty` with no commission term. The breaker therefore trips slightly earlier offline. Small, but it is a documented "byte-for-byte" claim that is not quite byte-for-byte.
**Fix:** Sum `pnl_usd + commission_usd` in `_check_daily_breaker`, or state the divergence in the module docstring's list.

---

_Reviewed: 2026-08-17T19:34:44Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
