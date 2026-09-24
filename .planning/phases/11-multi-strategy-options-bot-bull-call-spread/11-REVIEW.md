---
phase: 11-multi-strategy-options-bot-bull-call-spread
reviewed: 2026-09-24T15:51:54Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - backtester/options_run.py
  - bot/main.py
  - bot/options/config.py
  - bot/options/schema.py
  - bot/options/service.py
  - bot/options/store.py
  - bot/options/strategy.py
  - bot/options/universe.py
  - bot/state/migrations.py
  - docs/research/2026-09-24-super-bull-call-spread.md
  - rules_options.json
  - tests/backtester/options/test_options_run.py
  - tests/options/conftest.py
  - tests/options/test_config.py
  - tests/options/test_dispatch.py
  - tests/options/test_service.py
  - tests/options/test_store.py
  - tests/options/test_strategy.py
  - tests/options/test_universe.py
  - tests/state/test_migrations.py
findings:
  critical: 1
  warning: 5
  info: 6
  total: 12
status: issues_found
---

# Phase 11: Code Review Report

**Reviewed:** 2026-09-24T15:51:54Z
**Depth:** standard (diff `940243a..HEAD`, full files for context)
**Files Reviewed:** 20
**Status:** issues_found

## Summary

The phase-11 diff mostly matches the locked decisions. The D-19 sign convention is applied in the right places: `credit_per_spread = -debit` on open, `manage_decision_debit(mark, -credit, ...)` on manage, and the unchanged `credit - net_exit` close math gives the correct realized P&L for a debit spread. Per-strategy counters and global counters are split correctly per D-22. D-29 is wired into startup reconcile. `load_options_config` still returns the same flat config, and the shipped tasty view matches the pre-change file field for field. The equity DB is only opened through a `mode=ro` URI. The full suite passes (1265 passed, 1 skipped).

Five things were verified by running them:
- A live-DB read-only query showing rank collisions in `daily_scan`.
- A `legacy_view` KeyError traceback reached through `backtester.options_run`.
- `legacy_view` silently overriding a per-strategy global knob that the live loader rejects.
- The read-only WAL open behavior.
- The test suite run.

The main problem is in manage. A leg with no usable quote is marked as worth $0. This produces false exits and false daily-breaker trips, and it can leave a position stuck in CLOSING or turn it into a naked short. The rest of the findings are about places where the recorded risk or P&L differs from what is actually open on the broker, and about the equity-watchlist read returning rows in an order that is not deterministic.

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: A leg with no quote is marked as worth $0, which causes false exits, false breaker trips, and naked-short or stuck-CLOSING outcomes

**File:** `bot/options/service.py:858-860, 901-918` (with `bot/options/strategy.py:287-292` and `bot/options/execution.py:79`)

**Issue:** `_manage_position` only checks that each leg's code is present in `quotes`. It does not check that the quote is usable. The snapshot DataFrame is passed through raw (`bid_price`/`ask_price`). `strategy._as_float` documents that the SDK returns a literal `'N/A'` on untraded strikes, and it converts `None`, `''`, `'N/A'` and `0` to `0.0`. That leg's mid then goes into `mark_spread` as $0. For the new debit path:

- **Short call unquoted:** `mark = -mid(long)`, so the spread's value is overstated as the full long-call mid. `manage_decision_debit` can fire `profit_target` early. `close_legs` then works the short leg first:
  - If the value is `'N/A'`, `float(bid)` in `fill_leg` raises `ValueError`. The row was already set to `CLOSING` (line 920). The per-position `except` swallows the error, and the row stays `CLOSING` with no alert. The manage loop only reads `OPEN`, so the position is orphaned until the next restart. It also drops out of BP headroom, which only counts `OPEN`/`OPENING`.
  - If the value is numeric `0`, the buy-back is priced at $0.01–$0.11, is never filled, and is abandoned. `close_legs` then sells the long call anyway ("one leg failing does not stop the rest"), leaving a **naked short call**. That breaks the defined-risk invariant.
- **Long call unquoted:** `mark = +mid(short)`, so `(credit - mark)*100*qty` gives an unrealized loss of about `-(debit + short_mid)*100*qty`, roughly -$1,780 for the 5-lot worked example. Two such positions summed at line 875 trip the global `$2,000` daily-loss breaker when no loss has occurred, which blocks every strategy for the rest of the day.

The credit path has the same flaw (it predates this phase). Phase 11 makes it much more likely to be hit, because it adds single-name S&P option chains, which are thinner than the ETF chains.

**Fix:** Refuse to mark or act on any leg without a two-sided numeric quote. Also, never leave a row in CLOSING because of an exception:
```python
def _quote_ok(q):
    try:
        bid, ask = float(q.get("bid")), float(q.get("ask"))
    except (TypeError, ValueError):
        return False
    return ask > 0 and bid >= 0 and ask >= bid

if any(leg["code"] not in quotes or not _quote_ok(quotes[leg["code"]]) for leg in legs):
    _logger.warning("options_manage_missing_quote", position_id=pid)
    return 0.0
...
self._store.set_position_status(pid, "CLOSING")
try:
    ok = await self._executor.close_legs(...)
except Exception:
    ok = False      # falls through to the NEEDS_ATTENTION + alert branch
```
Also filter out rows with a missing or `'N/A'` quote when building `quotes` (lines 857-860).

## Warnings

### WR-01: D-29 only checks the strategy name, so a strategy whose structure changed leaves its open positions unmanaged

**File:** `bot/options/service.py:389, 893-916`

**Issue:** The startup guard only asks whether `pos["strategy_name"]` is in the book. `_manage_position` picks the decision function from `pos["structure"]` but reads parameters from the strategy's current config. Suppose the operator keeps a name but changes its `structure.type`, for example `super_bull_call` switched to a credit structure, or tasty switched to `bull_call_spread`. Then a pre-existing position is decided with the other kind's config:
- `manage_decision(..)` reads `cfg.profit_target_pct_of_credit`, which is `None`, so `None / 100` raises TypeError.
- `manage_decision_debit(..)` reads `cfg.profit_target_pct_of_max`, which is `None`, with the same result.

This happens on every 5-minute cycle. The error is logged and swallowed. Only `assignment_guard` still works, because it is checked first. The position never takes profit or exits on DTE, and its unrealized P&L is left out of the breaker total. This is what D-29 is meant to prevent ("never managed with another strategy's parameters").

**Fix:** In the startup reconcile, also flag NEEDS_ATTENTION when `pos["structure"] != self._strategies[name].structure_type`. In `_manage_position`, add the same check as defense in depth, and return 0.0 with a warning when it fails.

### WR-02: Entry premium is recorded at the pre-trade mid, not the fill, so the 1/4 rule, max loss, BP cap and realized P&L are all off

**File:** `bot/options/service.py:720-732, 742-745, 952`; `bot/options/strategy.py:434-436`

**Issue:** `credit_per_spread = -sel["debit"]` and `max_loss_usd = debit*100*qty` both use the chain mid. `LegExecutor` always buys at mid + $0.02 or worse, and can escalate to mid + $0.11 on each leg. It sells at mid − $0.02 or worse. So the debit actually paid can be up to about $0.22 per spread more than the recorded value.

- The 1/4-rule gate (`debit <= 0.30 × width`) is only enforced on the mid. A $3.00 mid on a $10 spread passes, but can fill at $3.22 (32%).
- `max_loss_usd`, which feeds the global BP headroom, understates the actual risk.
- `realized_per_spread = credit - net_exit` is overstated by at least $0.04 per spread on every trade, and the daily breaker inherits that bias.

The per-leg `entry_price` values are already persisted (`_on_filled` → `set_leg_entry`) but are never used. The credit path has the same bias (it predates this phase), and the new debit path copies it.

**Fix:** After `open_position` returns `filled`, recompute the premium from the fills and update the row before `set_position_status(..., "OPEN")`:
```python
net = sum(f["entry_price"] for f in filled if f["side"] == "SELL") - \
      sum(f["entry_price"] for f in filled if f["side"] == "BUY")
max_loss = (-net if is_debit else sel["width"] - net) * 100 * qty
# store.update_position_premium(position_id, credit_per_spread=net, max_loss_usd=max_loss)
```
Optionally, for a debit, abort or alert if the filled debit breaks `max_debit_to_width × width`.

### WR-03: The equity-watchlist read returns rows in a non-deterministic, mixed order because intraday rescans reuse the same rank numbers

**File:** `bot/options/universe.py:86-89`

**Issue:** The equity bot's intraday rescan runs every 30 minutes from 09:55 ET (`rules.json:66-68`), so it has already run before the 10:05 bull-call scan. It upserts into the same `daily_scan(scan_date, code)` rows with its own rank sequence starting at 1, and it never deletes rows. A read-only query of the live DB for 2026-09-17 shows premarket and intraday rows sharing ranks 1, 2, 3, 5, 6, 7, 9 and 10 (for example `US.MRNA|1|intraday` and `US.OMC|1|premarket`).

`ORDER BY rank ASC LIMIT 20` has no tie-breaker. As a result:
- Which 20 names are returned, and in what order the per-day slots go to them, depends on SQLite's row order.
- Premarket names ranked 16–20 are dropped in favour of intraday names.
- Names that a later pass dropped are still included.

The query does match D-17 word for word, but it does not meet D-17's intent of a rank-ordered premarket watchlist.

**Fix:** At minimum, make the order deterministic: `ORDER BY rank ASC, gap_pct DESC, code ASC`. Better, choose the intended semantics explicitly. One option is `WHERE scan_date=? AND scan_pass='premarket'`. Note that the upsert overwrites `scan_pass`, so a separate premarket snapshot may be needed. Another option is `ORDER BY gap_pct DESC` across all passes. Then update D-17 to record the choice.

### WR-04: `legacy_view` runs before any validation, so bad input crashes `options_run` and backtests can silently diverge from the live config

**File:** `bot/options/config.py:533-554`; `backtester/options_run.py:228-232`

**Issue:**
1. `legacy_view` indexes `raw["risk"]` and `raw["service"]` and calls `src.pop(key)` without checking the keys exist. A strategies-shape file that is missing `risk.max_bp_usage_pct` fails with a raw `KeyError` traceback (verified). This breaks the module's stated contract of "exits 1 with `[ERROR]` rather than a raw stack trace". `options_run` only catches `ConfigError`.
2. If a strategy sets a global knob inside its own block (for example `strategies[0].sizing.sizing_equity_usd = 5000`), `legacy_view` silently replaces it with the global value (it returned `100000`). The live loader rejects the same file with `ConfigError` (T-11-02). The backtest then runs a config that the bot would refuse to start with.

**Fix:** Before projecting, validate the raw strategies-shape dict with the same path as the bot. For example, call `_validate(raw, STRATEGIES_SCHEMA)` and `_check_strategy(s)` inside `legacy_view` and raise `ConfigError`, or have `options_run` call `load_options_book(args.rules)` first. Also catch `KeyError` and `TypeError` around `legacy_view` in `options_run` and print `[ERROR]`.

### WR-05: Global BP headroom ignores NEEDS_ATTENTION and CLOSING positions that are still open on the broker

**File:** `bot/options/service.py:631-635`

**Issue:** `open_max_loss_total` only sums rows in `_OPEN_STATUSES = ("OPEN", "OPENING")`. Rows in NEEDS_ATTENTION or CLOSING are still live broker exposure, but they are excluded from the D-22 global BP cap. These rows come from D-29 unknown-strategy flags, from reconcile mismatches and from incomplete closes, plus the stuck-CLOSING case in CR-01. D-29 adds a new, bulk path into NEEDS_ATTENTION. On a restart after removing a strategy, every one of that strategy's positions leaves the headroom calculation, and `size_position`/`size_debit_position` can then open up to the full 25% on top of them. The same exclusion applies to the per-strategy `open_count` (lines 639-642).

**Fix:** Sum `max_loss_usd` over every active status, which is what `busy` already does:
```python
open_max_loss_total = sum(float(p["max_loss_usd"] or 0) for p in active)
```
Consider the same change for `open_count`, so that a strategy with stuck positions cannot keep adding new ones.

## Info

### IN-01: The EOD HTML "Credit" column shows negative numbers for debit positions

**File:** `bot/options/service.py:245, 271`
**Issue:** The Telegram lines use `_premium_label` ("debit 1.96"), but the HTML report prints the raw signed `credit_per_spread` (`-1.96`) under a "Credit" header. D-24 parity is met in form but not in meaning.
**Fix:** Render `_premium_label(p.get("credit_per_spread") or 0)` in that cell and rename the header to "Premium".

### IN-02: `insert_option_position` silently assigns an unset strategy_name to tasty

**File:** `bot/options/store.py:69`
**Issue:** Any row inserted with `strategy_name=None` gets the SQL default `'tasty_credit_spreads'`. A future caller that forgets the field on a bull-call row would have it managed with tasty's config (see WR-01), with no error. The default is only correct for rows written before the migration.
**Fix:** Raise `ValueError` in `insert_option_position` when `strategy_name` is missing. Keep the SQL default for historical rows only.

### IN-03: `main.py` dispatch crashes on a JSON file whose top level is not an object

**File:** `bot/main.py:80`
**Issue:** `data.get(...)` and `"strategies" in data` assume a dict. A top-level array or string raises an uncaught AttributeError or TypeError instead of `[ERROR] ... exit 1`.
**Fix:** `if not isinstance(data, dict): print("[ERROR] ...", file=sys.stderr); sys.exit(1)`.

### IN-04: `equity_state_db` ignores the `BOT_STATE_DB` override that the equity bot honours

**File:** `bot/options/config.py:53, 421`; `bot/options/universe.py:51`
**Issue:** The equity bot resolves its DB through `resolve_db_path()` (`BOT_STATE_DB` env var first). The options bot always uses `service.equity_state_db`, resolved relative to the working directory. If the equity bot runs with the env override, the bull-call strategy reads a different or stale DB and trades on it, or fails closed with zero entries. Only a log line would show this.
**Fix:** Document the coupling in CLAUDE.md, or log the resolved absolute path at startup in `options_jobs_registered`.

### IN-05: The provenance doc does not list the 60% profit target as a deviation

**File:** `docs/research/2026-09-24-super-bull-call-spread.md:21, 46, 56`
**Issue:** The video's fixed-target rule is 40–50% of max profit, but the bot ships 60 (D-16). The Deviations section only contrasts the target with the sell-half runner. D-27 requires every deviation to be listed.
**Fix:** Add a bullet: "Profit target 60% of max vs the video's 40–50% fixed-target band."

### IN-06: A blocking sqlite read with a 5 s busy timeout runs on the asyncio loop

**File:** `bot/options/service.py:586-589`; `bot/options/universe.py:57`
**Issue:** The `ponytail:` comment assumes the read takes under a millisecond. However, `timeout_s=5.0` lets it block the event loop for up to 5 s while the equity DB is locked (for example during a WAL checkpoint or recovery). That stalls the kill-switch poll and all other jobs.
**Fix:** Either lower `timeout_s` to about 0.5 s, or use `await asyncio.get_running_loop().run_in_executor(None, read_equity_watchlist, ...)`.

---

_Reviewed: 2026-09-24T15:51:54Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
