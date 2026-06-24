---
phase: 03-intraday-signal-and-risk-engine
reviewed: 2026-06-24T15:56:52Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - bot/gateway/gateway.py
  - bot/risk/__init__.py
  - bot/risk/events.py
  - bot/risk/risk_engine.py
  - bot/signal/__init__.py
  - bot/signal/bar_aggregator.py
  - bot/signal/events.py
  - bot/signal/signal_engine.py
  - bot/state/migrations.py
  - tests/gateway/test_gateway.py
  - tests/risk/test_risk_engine.py
  - tests/signal/test_bar_aggregator.py
  - tests/signal/test_signal_engine.py
  - tests/state/test_migrations.py
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-24T15:56:52Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

This phase implements the intraday signal pipeline (BarAggregator, SignalEngine),
the risk-sizing engine (RiskEngine + OrderIntent), the broker gateway, and the
Phase 3 SQLite migration. The paper-safety posture (SIMULATE default, no real
order path, parameterized SQL) is generally sound, and SQL injection was not found
— all StateStore writes use bound parameters.

However, the central correctness invariant for this phase — SIG-02 no-repaint —
is **violated** in `bar_aggregator.py`: the emitted closed-bar `BarEvent` carries
the *new* (still-open) bar's OHLCV instead of the bar that just closed. Because
`entry_price` and the I1/I2 filters are derived from `bar.close`, this propagates
a wrong price into both the gate decision and the position-sizing math. This is the
most serious finding and is not covered by any existing test. Two other blockers
concern the daily-cap burst guard being double-counted and a non-atomic migration
that can wedge startup.

## Critical Issues

### CR-01: SIG-02 no-repaint violated — closed BarEvent carries the NEW bar's OHLCV

**File:** `bot/signal/bar_aggregator.py:186-228`
**Issue:**
When `time_key` advances, the code correctly identifies that `prev_time_key`
(bar A) has closed, but it then builds `bar_data` for bar A using the OHLCV
values parsed from the **current row** — which is the *first push of the new bar
B*, not bar A:

```python
closed_time_key = prev_time_key          # bar A
...
bar_data = {
    "code": code,
    "time_key": closed_time_key,         # A's timestamp
    "open": open_, "high": high,         # but B's OHLCV!
    "low": low, "close": close, "volume": volume,
    "hod": self._hod.get(code, 0.0),     # already absorbed B's high
    "lod": self._lod.get(code, 0.0),     # already absorbed B's low
}
```

The inline comment at lines 199-204 acknowledges this ("OHLCV values below come
from the CURRENT row ... For the closed bar, we use what we have"), but that is
exactly the repaint SIG-02 forbids. Consequences:

- `SignalEvent.bar.close` is bar B's close, so `passes_intraday_filters`
  (I1: `close > premarket_high`, I2: `close >= hod`) evaluate against the wrong
  bar — a setup can fire or be suppressed on a price that never closed.
- `RiskEngine.on_signal` sets `entry_price = signal.bar.close`
  (`risk_engine.py:105`), so the 1%-risk and 10%-notional sizing, the
  `notional`, and the persisted `entry_price` are all computed from the wrong
  price. This is direct financial-correctness corruption.
- `hod`/`lod` are likewise post-advance values that include bar B's first tick,
  so even the session stats attached to the "closed" bar are off by one bar.

The existing test `test_bar_close_fires_once_on_advance` only asserts
`time_key`, and `test_hod_lod_tracking` only checks `hod >= 160` and a `lod`
that happens to be unaffected — so the bug is invisible to the suite.

**Fix:** Buffer the in-progress bar's latest OHLCV per code and emit *that*
snapshot when the bar closes, rather than the new bar's first push. For example,
keep `self._cur_bar[code] = {open, high, low, close, volume, time_key}` updated
on every same-`time_key` push, and on advance emit the stored snapshot for
`prev_time_key`:

```python
# On every push, before the advance check:
self._cur_bar[code] = {
    "open": open_, "high": high, "low": low,
    "close": close, "volume": volume, "time_key": time_key,
}
...
# On advance, emit the SAVED snapshot for the bar that just closed:
closed = self._cur_bar_prev.get(code)  # snapshot captured at last push of bar A
bar_data = {"code": code, "time_key": closed_time_key, **closed_ohlcv, ...}
```

HOD/LOD must also be computed from values up to and including bar A only (update
the running stats *after* emitting, or snapshot them at the last push of the
closing bar).

---

### CR-02: Daily-cap burst guard is double-counted — `_pending_count` incremented twice per intent

**File:** `bot/signal/signal_engine.py:463` and `bot/risk/risk_engine.py:191-192`
**Issue:**
`SignalEngine.on_bar` increments `self._pending_count` directly when it emits a
SignalEvent (line 463). Then `RiskEngine.on_signal`, after building the
OrderIntent, calls `self._signal_engine.note_intent_emitted()`
(`risk_engine.py:191-192`), which increments `_pending_count` **again**
(`signal_engine.py:107-114`). For every emitted intent the tally therefore
advances by 2, not 1.

This breaks the D-09/RISK-05 daily cap: with `max_trades_per_day = 5`, the gate
`filled_count + _pending_count < max_trades_per_day` will block after only
~2–3 real entries instead of 5. The bot will silently under-trade and stop
entering well before the configured daily limit. (The same double-count also
makes `note_intent_resolved`'s single-decrement insufficient to unwind a
resolved intent.)

Note the two unit tests do not catch this because they exercise the two engines
in isolation: `test_session_pending_tally_increments_on_emit` only drives
`SignalEngine` (no RiskEngine wired, so `note_intent_emitted` never fires), and
`test_daily_cap_independent_of_concurrent` in the risk suite uses a `MagicMock`
signal_engine whose `note_intent_emitted` does not touch `_pending_count`. The
real wired pipeline double-counts.

**Fix:** Pick a single owner of the tally. Either remove the direct
`self._pending_count += 1` in `on_bar` and rely solely on RiskEngine calling
`note_intent_emitted()` (preferred, since "only emitted *intents* consume a
slot" per D-11), or stop calling `note_intent_emitted()` from RiskEngine.
Whichever is removed, update the docstrings that claim a single increment.

---

### CR-03: `_migration_0003` runs `executescript` inside the runner's transaction — breaks the WR-03 atomicity guarantee and can wedge startup

**File:** `bot/state/migrations.py:152-169` (and runner at `:208-219`)
**Issue:**
`run_migrations` deliberately runs callable migrations on the open transaction so
the `PRAGMA user_version = {i}` bump commits atomically with the DDL
(documented at lines 209-213). But `_migration_0003` calls
`conn.executescript(...)`, and `sqlite3.Connection.executescript()` issues an
**implicit COMMIT** of any pending transaction before executing. This means the
`CREATE TABLE` statements are committed *before* the `PRAGMA user_version = 3`
runs. If the process crashes between `_migration_0003` returning and the
`user_version` bump/commit, the tables exist but `user_version` stays at 2.

The tables use `CREATE TABLE IF NOT EXISTS`, so a re-run would not raise here —
but the stated WR-03 invariant ("user_version bump commits atomically with the
DDL ... the two never diverge") is not actually upheld for migration 0003, and
the comment at lines 145-151 asserting atomicity is incorrect. More importantly,
the same `executescript` implicit-commit footgun is exactly what the codebase
went to lengths to avoid in `_migration_0002` by using guarded
`conn.execute(...)` calls. Migration 0003 reintroduces it.

**Fix:** Replace the single `executescript` with individual `conn.execute(...)`
statements (one per `CREATE TABLE IF NOT EXISTS`), matching the `_migration_0002`
pattern. `conn.execute` does not force an implicit commit, so the DDL and the
`user_version` bump commit together:

```python
def _migration_0003(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_trade_count (
        session_date TEXT PRIMARY KEY,
        filled_count INTEGER NOT NULL DEFAULT 0,
        updated_at   TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_intents (
        intent_id   TEXT PRIMARY KEY,
        code        TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'PENDING',
        entry_price REAL NOT NULL,
        stop_price  REAL NOT NULL,
        quantity    INTEGER NOT NULL,
        emitted_at  TEXT NOT NULL,
        resolved_at TEXT
    )""")
```

## Warnings

### WR-01: `get_acc_list` / `get_positions` use `get_event_loop()` instead of `get_running_loop()`

**File:** `bot/gateway/gateway.py:296, 304, 328, 397, 425, 466`
**Issue:** All async wrappers obtain the loop via `asyncio.get_event_loop()`.
Under Python 3.10+ `get_event_loop()` is deprecated when called from inside a
running coroutine without a set loop and will eventually be removed; it can also
return the *wrong* loop if these coroutines are ever awaited from a thread other
than the one that created the loop. Since every one of these methods is already
running inside `async def` (a running loop is guaranteed), `get_running_loop()`
is correct and future-proof.
**Fix:** Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in
all six async wrappers.

### WR-02: BarAggregator fire-and-forgets the coroutine future — exceptions in `on_bar`/`on_signal` are silently dropped

**File:** `bot/signal/bar_aggregator.py:226-228`
**Issue:** `asyncio.run_coroutine_threadsafe(self._on_bar_closed(bar_data), self._loop)`
returns a `concurrent.futures.Future` that is discarded. If `on_bar` (and the
RiskEngine pipeline it drives) raises — e.g. a StateStore write error, a gateway
exception, a sizing bug — the exception is captured in the unretrieved future and
never surfaces. For an unattended trading bot, a silently swallowed signal-path
exception means missed entries with zero operator visibility. The class docstring
markets this as "fire-and-forget", but a trading decision path should not lose
errors silently.
**Fix:** Attach a done-callback that logs any exception, e.g.
`fut = asyncio.run_coroutine_threadsafe(...); fut.add_done_callback(_log_if_exc)`
where `_log_if_exc` calls `f.exception()` and logs it via structlog. This keeps
the SDK thread non-blocking (T-03-03) while ensuring failures are recorded.

### WR-03: `_handle_row` mutates per-code state from the SDK push thread while the asyncio loop reads it — no synchronization

**File:** `bot/signal/bar_aggregator.py:135-239` (writers) vs `bot/signal/signal_engine.py` (readers)
**Issue:** The class docstring claims the per-code dicts are "written only from
the SDK push thread (GIL protects)" and treats this as single-writer-safe.
That is true for the dicts themselves, but the *values* placed into `bar_data`
(notably `hod`/`lod`) are read on the asyncio loop thread inside `on_bar` after
the bridge. Because HOD/LOD are mutated on the push thread on *every* tick
(`:157`, `:163-167`) and the closed-bar event captures them by snapshotting into
a fresh dict at emit time, the snapshot is consistent — but any later code that
reads `agg._hod[code]` directly off-thread would observe a value that has since
advanced. The current code happens to snapshot into `bar_data` so it is safe
today, but the "GIL protects" justification is too broad and will mislead future
edits (e.g. adding a method that returns `self._lod[code]` for the live stop).
**Fix:** Tighten the docstring to state precisely what is safe (the snapshot dict
passed across the bridge is immutable post-construction) and add a guard rail:
never expose the mutable `_hod`/`_lod` dicts to off-thread readers; pass snapshots
only.

### WR-04: `get_equity` upper-bound guard rejects legitimately large accounts and hard-codes a $10M ceiling unrelated to the $100k sizing basis

**File:** `bot/gateway/gateway.py:53, 349-357`
**Issue:** `_IMPLAUSIBLE_HIGH = 10_000_000.0` causes `get_equity` to silently fall
back to $100k whenever real `total_assets` exceeds $10M. On a paper account this
is unlikely, but the failure mode is dangerous: instead of degrading to a safe
*small* number, it substitutes a *fabricated* equity that then drives live
position sizing (1% risk + 10% notional). If a SIMULATE account were ever funded
above $10M, every trade would be mis-sized against $100k with no error raised —
only a warning log. The bound is also a magic number disconnected from the
documented $100k sizing basis.
**Fix:** Reconsider whether an upper bound should fall back to a *fabricated*
equity at all, versus refusing to size (return None / skip the trade) on an
implausible read. At minimum, source the bound from config rather than a module
constant, and document why $10M is the chosen ceiling.

### WR-05: `fetch_premarket_highs` / concurrent-cap code use `row.get(...)` on pandas rows where `.get` has fallback semantics that mask missing columns

**File:** `bot/signal/signal_engine.py:170-171, 392`
**Issue:** `row.get("code")` and `row.get("pre_high_price")` are called on
`iterrows()` Series. `pandas.Series.get` returns `None` for a missing *index
label*, so a snapshot DataFrame that lacks the `pre_high_price` column entirely
(e.g. SDK field rename, or an error payload coerced to a DataFrame) yields
`None` for every row and silently produces an empty premarket-high map — the bot
then takes no trades all day with only an INFO log per row. The `hasattr(row, "get")`
branch always takes the `.get` path for Series, so the `else` branch
(`row["code"]`, which *would* raise KeyError and surface the schema problem) is
dead code.
**Fix:** Validate the expected columns are present once, up front
(`if "pre_high_price" not in data.columns: log WARNING and bail`), rather than
relying on per-row `.get` fallbacks that turn a schema mismatch into a silent
no-trade day.

### WR-06: `_get_filled_count` and rvol lookup use `now_et().date()` keyed by ET date, but `daily_trade_count`/`daily_scan` rows may be written under a different date convention

**File:** `bot/signal/signal_engine.py:316, 432` and `bot/state/migrations.py:27`
**Issue:** `session_date_str = now_et().date().isoformat()` keys both the rvol
baseline lookup and the filled-count read by the *ET* calendar date. Migration
0001's design note states "All TEXT date/time fields use ISO-8601 strings (UTC)"
(`migrations.py:27`). If Phase 2's scan writer or Phase 4's fill counter persist
`scan_date` / `session_date` using a UTC date, then late-session bars (after
20:00 ET, when UTC has rolled to the next day — though outside the entry window,
this still matters for any pre-15:30 ET trading near a UTC midnight boundary in
other DST states) would key a different date string and miss the row. The rvol
lookup returning no row → `signal_skipped_no_rvol_baseline` → silent no-trade.
The two date conventions (ET vs UTC) are not reconciled in this phase and the
mismatch is latent.
**Fix:** Pin a single, documented date convention for all `*_date` keys (ET
session date is the natural choice given the strategy is ET-anchored) and assert
the Phase 2 writer uses the same. Add a test that writes a `daily_scan` row and
reads it back through `on_bar` using the same `now_et()`-derived key.

## Info

### IN-01: `bot/risk/__init__.py` docstring claims RiskEngine is exported but it is not

**File:** `bot/risk/__init__.py:6-9`
**Issue:** The module docstring says it "Provides RiskEngine ... and the
OrderIntent dataclass" and "Exports: OrderIntent". RiskEngine is only importable
via `bot.risk.risk_engine`, not `bot.risk`. Minor doc-vs-reality drift; tests
import from the submodule so nothing breaks.
**Fix:** Either re-export `RiskEngine` and `OrderIntent` in `__init__.py` or
correct the docstring to list only what is actually exported.

### IN-02: Unused imports in signal_engine and gateway

**File:** `bot/signal/signal_engine.py:19` (`sqlite3`), `:20` (`time` is used,
`datetime` unused), `bot/gateway/gateway.py:19` (`field`)
**Issue:** `sqlite3` is imported but never referenced in signal_engine (queries go
through `self._store.conn`). `datetime` is imported but only `time` is used.
`field` from dataclasses is imported in gateway but no field default factory is
used. Dead imports.
**Fix:** Remove unused imports.

### IN-03: Stale/inaccurate comment references a specific symbol in generic gate code

**File:** `bot/signal/signal_engine.py:388`
**Issue:** The comment `# Also check if US.AAPL itself is already in open positions`
hard-codes `US.AAPL` in what is generic per-code logic. Copy-paste residue from a
test scenario; misleading when reading the gate for any other code.
**Fix:** Change to `# Also check if this code is already in an open position`.

### IN-04: BarEvent/SignalEvent docstrings describe `hod` as "running max of closed-bar highs" but implementation uses every pushed (mid-bar) high

**File:** `bot/signal/events.py:33` vs `bot/signal/bar_aggregator.py:154-157`
**Issue:** `BarEvent.hod` is documented as "Session running max of all *closed-bar*
highs" but `_handle_row` updates `_hod` on **every** push including mid-bar ticks
(`:154-157`). The emitted HOD therefore includes in-progress highs, contradicting
the docstring. (This overlaps CR-01: once CR-01 is fixed to snapshot at bar close,
the docstring and behavior should be reconciled together.)
**Fix:** Align the docstring and implementation — decide whether HOD is over
closed bars or all ticks, and make both say the same thing.

---

_Reviewed: 2026-06-24T15:56:52Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
