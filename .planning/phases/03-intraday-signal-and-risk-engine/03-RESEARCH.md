# Phase 3: Intraday Signal and Risk Engine — Research

**Researched:** 2026-06-24
**Domain:** Python asyncio signal engine — moomoo SDK push handler → bar-close gating → intraday filters → risk sizing → OrderIntent emission
**Confidence:** HIGH (SDK field names verified via official docs; thread-bridge pattern verified via Python stdlib docs and prior research; accinfo/snapshot fields confirmed via official Moomoo API reference)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01:** Premarket high captured via one batched `get_market_snapshot` for the ≤20 watchlist codes near 09:30 ET, reading `pre_high_price`, then frozen for the session. No extended-hours subscription required.

**D-02:** HOD (I2) tracked as a running max of regular-session 5m bar highs inside `BarAggregator`, updated as each bar closes. No extra broker calls.

**D-03:** If premarket high is undefined, zero, or unavailable for a code, the signal engine emits no entry signal for that code that session. Never fall back to prior-day high.

**D-04:** "Equity" for 1%-risk sizing = `total_assets` from `accinfo_query` (net liquidation value = cash + market value of open positions).

**D-05:** Always read live equity on every sizing decision (`refresh_cache=True`). Fallback: $100,000 if the query fails or returns an implausible value.

**D-06:** A new gateway funds/equity query method is required — `MoomooGateway` currently has no `accinfo_query` wrapper. Must mirror the existing `run_in_executor` async pattern.

**D-07:** Round share qty DOWN to whole shares. When 1%-risk and 10%-notional cap disagree, take the smaller. If result < 1 share, emit no `OrderIntent` and log the reason.

**D-08:** The authoritative daily-entry counter is incremented at fill (Phase 4), persisted in StateStore. Authoritative counter measures entries taken, not setups seen.

**D-09:** Phase 3 enforces cap via `(filled_count) + (pending_intents_emitted_this_session)` < `max_trades_per_day` to prevent burst over-emission before any fill registers.

**D-10:** Re-entry allowed only when broker truth shows the code is flat (`get_positions()`) AND it has no unresolved pending intent. Still bounded by the daily cap.

**D-11:** Signals blocked by concurrent-cap or risk-rejection count toward neither the daily cap nor the pending tally. Only emitted `OrderIntent`s consume an entry slot.

**D-12 (Claude's Discretion — defaulted):** Each `OrderIntent` is both logged to structlog AND persisted to StateStore as a "pending intent" record. This satisfies RISK-03 criterion #5 and enables D-09 pending-intent gating. Lifecycle: emitted → resolved/filled/expired (Phase 4 closes them).

### Claude's Discretion

- `BarAggregator` internals — `CurKlineHandlerBase` subclass mechanics, SDK-thread→asyncio bridge, reconnect mid-bar handling.
- Snapshot field availability at ≤20-code scale — confirmed `pre_high_price` is the correct field.
- Exact `SignalEvent` / `OrderIntent` dataclass shapes and module decomposition across 03-01/03-02/03-03 slices.
- Entry-window time-gate boundary handling (10:05/15:30 ET inclusive/exclusive).
- Which gateway funds-query field maps to "total net assets" on SIMULATE (confirmed `total_assets`), and the implausible-value threshold for $100k fallback.

### Deferred Ideas (OUT OF SCOPE)

Order placement, position lifecycle FSM, fill matching, broker-reconciliation drift logic (Phase 4). APScheduler service, OpenD watchdog, Telegram alerts (Phase 5). Backtester (Phase 6). Phase 3 places no orders and sees no fills.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SIG-02 | Entry signals evaluated only on closed 5m bars — never mid-bar (no repainting) | Timestamp-advance detection in `BarAggregator.on_recv_rsp`; conservative reconnect dedup |
| SIG-03 | Entry triggers when price is above premarket high, above today's HOD, and intraday RVOL ≥ 2.0, within 10:05–15:30 ET | `passes_intraday_filters()` already built; `BarAggregator` feeds it; entry window via `et_helpers.now_et()` |
| SIG-04 | No new entries when 5 concurrent positions open or after 15:30 ET cutoff | Concurrent-position gate via `gateway.get_positions()`; time gate via `et_helpers` |
| RISK-01 | Position sized to risk 1% of current account equity (live, not cached) | New `gateway.get_equity()` calling `accinfo_query(refresh_cache=True)`; field `total_assets` |
| RISK-02 | Position notional capped at 10% of portfolio value | `cfg.max_position_size_pct / 100 * equity` cap applied before emitting intent |
| RISK-03 | Initial stop computed as low-of-day − 1% | `TrendJoinLong.compute_initial_stop(lod)` already built; `OrderIntent` carries stop_price + quantity |
| RISK-04 | Maximum 5 concurrent positions enforced at order-submission time | Concurrent cap from `get_positions()` length vs `cfg.max_concurrent_positions` |
| RISK-05 | Daily new-entry cap (`max_trades_per_day`, default 5) enforced separately from concurrent cap | Persisted in StateStore migration 0003; Phase 3 enforces via D-09 pending-tally gate |
</phase_requirements>

---

## Summary

Phase 3 wires three slices — `BarAggregator` (03-01), `SignalEngine` (03-02), and `RiskEngine` (03-03) — into a pipeline that turns live closed 5m bar events from the moomoo SDK push thread into verified, correctly-sized `OrderIntent` dataclasses logged to structlog and persisted to StateStore. No orders are placed; no fills are seen. The phase does not implement anything at the strategy-logic level (`passes_intraday_filters`, `compute_initial_stop` already exist in `bot/strategy/`); it orchestrates around that logic.

The two ROADMAP research flags are resolved concretely:

**Flag 1 — Reconnect mid-bar bar-close handling:** The moomoo SDK pushes mid-bar updates to `CurKlineHandlerBase.on_recv_rsp` on every server tick (not only on bar close). Bar-close detection must therefore be implemented in application code via timestamp-advance: `BarAggregator` maintains `_last_time_key: dict[str, str]` per code and fires strategy evaluation only when `time_key` changes from the stored value. On reconnect (when `is_first_push=True` is set), the SDK re-pushes the most recent cached bar — which is the current incomplete bar if reconnection happens mid-bar. The conservative mitigation is: record `_seen_time_keys: set[str]` per code during the session; on any push, only fire strategy evaluation if (a) `time_key` advances AND (b) the new `time_key` has not previously triggered an evaluation. This dual-guard guarantees no double-fire and no mid-bar evaluation even across reconnects. [CITED: openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html — is_first_push re-pushes last cached data on reconnect]

**Flag 2 — Snapshot field availability:** The exact field name for premarket high in `get_market_snapshot` is `pre_high_price` (not `premarket_high` or `pre_market_high`). This field is populated for US stocks and does not require an extended-hours subscription — the snapshot API returns it alongside regular OHLCV for US codes. The 20-code batch well within the 400-code per-request limit. For equity sizing, the confirmed field from `accinfo_query` for SIMULATE accounts is `total_assets` (= cash + securities market value = net liquidation value), matching the FIELD_MAPPING.md entry "Total Assets → `total_assets` — Account net asset value". [CITED: openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html, skills/moomooapi/docs/FIELD_MAPPING.md]

**Primary recommendation:** Build BarAggregator with a dual-guard (`time_key` advance + session-level `seen_time_keys` dedup set), bridge the SDK callback thread to asyncio via `loop.call_soon_threadsafe()` (or a `queue.Queue` drain loop), and feed the closed bar into SignalEngine then RiskEngine as a pure data pipeline — no broker mutations until Phase 4.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Bar-close detection | `bot/signal/bar_aggregator.py` | — | Bridges SDK push thread to asyncio; owns bar-state tracking |
| Premarket-high snapshot (D-01) | `bot/gateway/gateway.py` (new `get_snapshot()` method) | — | One-shot broker read at 09:30 ET; gateway owns all broker I/O |
| HOD tracking (D-02) | `bot/signal/bar_aggregator.py` | — | Running max of closed bar highs; self-contained in the aggregator |
| Intraday filter evaluation (I1/I2/I3) | `bot/signal/signal_engine.py` | `bot/strategy/trend_join_long.py` (called) | Engine orchestrates; `passes_intraday_filters()` provides filter math |
| Entry-window time gate | `bot/signal/signal_engine.py` | `bot/safety/et_helpers.py` (used) | Signal engine gates on ET window; et_helpers provides DST-correct now |
| Concurrent-position gate (RISK-04/SIG-04) | `bot/signal/signal_engine.py` | `bot/gateway/gateway.py` (broker truth) | Signal engine checks live position count before emitting |
| Daily-entry-cap gate (RISK-05, D-08/D-09) | `bot/signal/signal_engine.py` | `bot/state/store.py` (persisted) | Cap enforced in engine via StateStore counter + in-memory pending tally |
| Live equity read (RISK-01) | `bot/gateway/gateway.py` (new `get_equity()`) | — | New method mirroring run_in_executor pattern; calls accinfo_query |
| Trade sizing math (RISK-01/02/07) | `bot/risk/risk_engine.py` | — | Pure math: 1% risk, 10% notional cap, round-down qty, <1-share guard |
| Initial stop computation (RISK-03) | `bot/risk/risk_engine.py` | `bot/strategy/trend_join_long.py` (called) | Engine invokes `compute_initial_stop(lod)`; stop price in OrderIntent |
| OrderIntent emission + logging | `bot/risk/risk_engine.py` | `bot/state/store.py` (persisted) | RiskEngine emits and persists; structlog audit per D-12/RISK-03 |
| StateStore migration (daily counter + pending intent) | `bot/state/migrations.py` (new 0003) | — | Migration 0003 adds two new tables following established pattern |

---

## Standard Stack

### Core (no new packages — everything reuses Phase 1/2 foundations)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| moomoo-api | 10.7.6708 | `CurKlineHandlerBase` push handler, `accinfo_query`, `get_market_snapshot` | Already installed; the sole broker SDK |
| asyncio (stdlib) | Python 3.9+ built-in | Event loop, `call_soon_threadsafe`, async/await throughout | Native; gateway pattern established in Phase 1 |
| queue (stdlib) | Python 3.9+ built-in | Optional: thread-safe `queue.Queue` as alternative bridge to asyncio drain loop | Native; no extra install |
| pandas | 3.0.3 | DataFrame parsing from SDK callbacks (`data.iloc[0]` pattern) | Already installed |
| dataclasses (stdlib) | Python 3.7+ | `SignalEvent`, `OrderIntent` typed dataclasses | Established pattern: `FutuConfig`, `GatewayConfig` use same |
| structlog | 26.1.0 | OrderIntent audit logging (D-12, RISK-03 #5) | Already installed from Phase 1 |
| sqlite3 (stdlib) | built-in | StateStore migration 0003 (daily counter + pending intent) | Already in use |
| zoneinfo (stdlib) | Python 3.9+ | ET time gate (`now_et()` from `et_helpers.py`) | Already in use in et_helpers.py |

### No New Packages Required

Phase 3 introduces zero new `pip install` dependencies. All capabilities are covered by the existing stack. The only additions are new modules within `bot/`.

---

## Package Legitimacy Audit

> No new packages are installed in Phase 3. This section is intentionally empty — all capabilities use existing dependencies already approved in Phase 1 (Wave 0 of Phase 2 package gate).

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram — Phase 3 Data Flow

```
SDK Push Thread (moomoo SDK internal)
        │
        │  on_recv_rsp(rsp_pb) — CurKlineHandlerBase subclass
        │
        ▼
  BarAggregator._handle_push(data)
        │
        │  time_key advance detection  ← _last_time_key[code]
        │  session dedup guard         ← _seen_time_keys[code]
        │  HOD update                  ← _hod[code] = max(hod, bar.high)
        │
        │  [bar not closed: return silently]
        │
        │  [bar closed: call_soon_threadsafe →]
        ▼
  asyncio Event Loop (main thread)
        │
        │  BarEvent(code, bar_ohlcv, hod, time_key)
        ▼
  SignalEngine.on_bar(event)
        │
        ├─ premarket_high = _premarket_highs[code]     (frozen at 09:30)
        ├─ passes = TrendJoinLong.passes_intraday_filters(...)
        ├─ in_window = now_et() in [10:05, 15:30]
        ├─ concurrent_ok = len(get_positions()) < max_concurrent_positions
        ├─ daily_cap_ok = filled_count + pending_count < max_trades_per_day
        │
        │  [any gate fails: return, no signal]
        │
        │  [all gates pass: emit SignalEvent]
        ▼
  RiskEngine.on_signal(event)
        │
        ├─ equity = await gateway.get_equity()         (live accinfo_query)
        ├─ stop_price = TrendJoinLong.compute_initial_stop(lod)
        ├─ risk_dollars = equity * max_risk_per_trade_pct / 100
        ├─ risk_qty = floor(risk_dollars / (entry_price - stop_price))
        ├─ notional_cap_qty = floor(equity * max_position_size_pct/100 / entry_price)
        ├─ qty = min(risk_qty, notional_cap_qty)
        │
        │  [qty < 1: log "under_budget", emit no intent]
        │
        │  [qty >= 1: emit OrderIntent + structlog + StateStore persist]
        ▼
  OrderIntent  ── structlog audit (D-12 / RISK-03 #5)
              └── StateStore pending_intents table (migration 0003)
```

### Recommended Project Structure (new modules only)

```
bot/
├── signal/
│   ├── __init__.py
│   ├── bar_aggregator.py   # CurKlineHandlerBase subclass; bar-close detection; HOD tracking
│   ├── signal_engine.py    # SignalEvent emission; I1/I2/I3 + gates
│   └── events.py           # SignalEvent, BarEvent dataclasses
├── risk/
│   ├── __init__.py
│   ├── risk_engine.py      # OrderIntent emission; sizing math; equity read
│   └── events.py           # OrderIntent dataclass
tests/
├── signal/
│   ├── test_bar_aggregator.py
│   └── test_signal_engine.py
└── risk/
    └── test_risk_engine.py
```

### Pattern 1: BarAggregator — Timestamp-Advance Bar-Close Detection with Session Dedup

**What:** `CurKlineHandlerBase` subclass that holds `_last_time_key`, `_seen_time_keys`, and `_hod` per code. Fires asyncio callback only when `time_key` advances AND has not been seen this session.

**When to use:** Only bar-close gating use case. Never evaluate strategy mid-bar.

**Example:**
```python
# Source: moomoo SDK CurKlineHandlerBase pattern (push_kline.py reference)
import asyncio
import threading
from typing import Callable, Dict, Optional, Set
from moomoo import CurKlineHandlerBase, RET_OK

class BarAggregator(CurKlineHandlerBase):
    """Bar-close detection via timestamp advance with session dedup (SIG-02).

    on_recv_rsp fires on EVERY SDK tick for the current bar — not just on close.
    A bar is considered closed only when the time_key advances to a new value.
    On reconnect, the SDK re-pushes the last cached bar (is_first_push=True
    behaviour); the _seen_time_keys guard prevents double-firing an already-
    processed bar even if time_key advances to a value we already handled.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_bar_closed: Callable,  # async coroutine
    ) -> None:
        super().__init__()
        self._loop = loop
        self._on_bar_closed = on_bar_closed
        # Per-code mutable state — only written from SDK push thread
        self._last_time_key: Dict[str, str] = {}   # last seen time_key per code
        self._seen_time_keys: Dict[str, Set[str]] = {}  # all time_keys evaluated
        self._hod: Dict[str, float] = {}           # running HOD per code

    def reset_session(self) -> None:
        """Clear per-session state at market open. Thread-safe via GIL (dict clear)."""
        self._last_time_key.clear()
        self._seen_time_keys.clear()
        self._hod.clear()

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK or data is None or len(data) == 0:
            return ret, data

        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        code = str(row.get("code", ""))
        time_key = str(row.get("time_key", ""))
        if not code or not time_key:
            return ret, data

        prev_time_key = self._last_time_key.get(code)

        # Update HOD on every push — current bar's high may increase mid-bar
        high = float(row.get("high", 0) or 0)
        self._hod[code] = max(self._hod.get(code, 0.0), high)

        if prev_time_key is None:
            # First push for this code — record time_key, no bar closed yet
            self._last_time_key[code] = time_key
            if code not in self._seen_time_keys:
                self._seen_time_keys[code] = set()
            return ret, data

        if time_key == prev_time_key:
            # Same bar, mid-bar update — ignore for strategy evaluation
            return ret, data

        # time_key advanced — a bar has closed
        closed_time_key = prev_time_key
        if code not in self._seen_time_keys:
            self._seen_time_keys[code] = set()

        if closed_time_key not in self._seen_time_keys[code]:
            # Not yet processed — fire strategy evaluation
            self._seen_time_keys[code].add(closed_time_key)
            bar_data = {
                "code": code,
                "time_key": closed_time_key,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": int(row.get("volume", 0) or 0),
                "hod": self._hod.get(code, 0.0),
            }
            # Bridge SDK thread → asyncio: call_soon_threadsafe is the correct
            # primitive for scheduling a coroutine from a non-asyncio thread.
            # asyncio.run_coroutine_threadsafe is the alternative when a
            # Future result is needed; here fire-and-forget suffices.
            asyncio.run_coroutine_threadsafe(
                self._on_bar_closed(bar_data), self._loop
            )

        # Update last time_key to the new bar's time
        self._last_time_key[code] = time_key
        return ret, data
```

### Pattern 2: Thread → Asyncio Bridge (call_soon_threadsafe vs run_coroutine_threadsafe)

**What:** Two stdlib primitives for calling into an asyncio event loop from a non-asyncio thread. The moomoo SDK fires `on_recv_rsp` on its own background thread; the asyncio event loop runs on the main bot thread. These must never be called from within asyncio context itself.

**When to use:**
- `loop.call_soon_threadsafe(callback, *args)` — schedule a synchronous callback; use when the bridge callback is non-async (e.g., `queue.put_nowait`).
- `asyncio.run_coroutine_threadsafe(coro, loop)` — schedule a coroutine; returns a `concurrent.futures.Future`. Use when the SDK thread needs to await a result or when firing an async coroutine directly from the SDK thread.

**Example:**
```python
# Source: Python stdlib asyncio docs — asyncio.run_coroutine_threadsafe
import asyncio

# Called from SDK background thread (on_recv_rsp):
asyncio.run_coroutine_threadsafe(
    signal_engine.on_bar_closed(bar_data),   # coroutine
    loop,                                     # the asyncio event loop object
)

# Alternative via queue.Queue (decouples SDK thread from asyncio entirely):
# SDK thread:
bar_queue.put_nowait(bar_data)   # no blocking — never block in on_recv_rsp

# asyncio coroutine (drain loop):
async def _drain_bar_queue():
    while True:
        try:
            bar_data = bar_queue.get_nowait()
            await signal_engine.on_bar_closed(bar_data)
        except queue.Empty:
            await asyncio.sleep(0.05)
```

**Note:** Never use `asyncio.get_event_loop()` from inside the SDK callback thread — that raises a `DeprecationWarning` in Python 3.10+ and is incorrect. The event loop must be obtained in the main asyncio thread and passed to `BarAggregator.__init__()` via `asyncio.get_event_loop()` at setup time. [CITED: docs.python.org/3/library/asyncio-threading.html]

### Pattern 3: New Gateway Equity Query Method (D-06)

**What:** Add `get_equity()` to `MoomooGateway` mirroring the existing `run_in_executor` async pattern. Calls `accinfo_query(trd_env=..., acc_id=..., refresh_cache=True)` and reads `total_assets`.

**Example:**
```python
# Mirrors: bot/gateway/gateway.py get_positions() pattern
async def get_equity(self) -> float:
    """Read live total net assets from the SIMULATE account (RISK-01, D-04/D-05).

    Calls accinfo_query(refresh_cache=True) and reads the total_assets field,
    which equals cash + securities market value (net liquidation value per
    FIELD_MAPPING.md). Falls back to _EQUITY_FALLBACK if the query fails or
    returns an implausible value (D-05).

    Returns:
        float — account equity in USD. Minimum 1.0 (guards against zero-divide).
    """
    _EQUITY_FALLBACK = 100_000.0
    _IMPLAUSIBLE_THRESHOLD = 1_000.0   # < $1,000 is implausible for this paper acct

    loop = asyncio.get_event_loop()
    try:
        ret, data = await loop.run_in_executor(
            None,
            lambda: self._trade_ctx.accinfo_query(
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
                refresh_cache=True,
            )
        )
        if ret != RET_OK or data is None or len(data) == 0:
            _logger.warning("equity_query_failed", ret=ret, fallback=_EQUITY_FALLBACK)
            return _EQUITY_FALLBACK

        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        total_assets = float(row.get("total_assets", 0) or 0)

        if total_assets < _IMPLAUSIBLE_THRESHOLD:
            _logger.warning(
                "equity_implausible",
                total_assets=total_assets,
                fallback=_EQUITY_FALLBACK,
            )
            return _EQUITY_FALLBACK

        return max(total_assets, 1.0)   # guard zero-divide in sizing math

    except Exception:
        _logger.warning("equity_query_exception", exc_info=True, fallback=_EQUITY_FALLBACK)
        return _EQUITY_FALLBACK
```

### Pattern 4: SignalEvent and OrderIntent Dataclasses

```python
# bot/signal/events.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class BarEvent:
    """Closed 5m bar delivered from BarAggregator to SignalEngine."""
    code: str
    time_key: str          # "YYYY-MM-DD HH:MM:SS" US ET (from SDK)
    open: float
    high: float
    low: float
    close: float
    volume: int
    hod: float             # running HOD at bar close (D-02)

@dataclass
class SignalEvent:
    """I1/I2/I3 + time-gate passed — passed to RiskEngine."""
    code: str
    bar: BarEvent
    premarket_high: float  # D-01 frozen value
    hod: float             # same as bar.hod at signal time
    lod: float             # bar.low (current bar's low — LOD at signal time)
    rvol: float            # pre-computed RVOL from watchlist baseline
    emitted_at: datetime   # now_et() at emission

# bot/risk/events.py
@dataclass
class OrderIntent:
    """Verified, sized trade intent (no order placed until Phase 4)."""
    code: str
    entry_price: float     # bar.close at signal time (indicative; Phase 4 may adjust)
    stop_price: float      # compute_initial_stop(lod) — RISK-03
    quantity: int          # risk-sized, round-down, >= 1 (D-07)
    equity_used: float     # equity value used for sizing (for audit)
    risk_dollars: float    # equity * max_risk_per_trade_pct/100
    notional: float        # entry_price * quantity
    emitted_at: datetime   # now_et() at emission
    source_signal: SignalEvent  # the SignalEvent that triggered this
    intent_id: str         # UUID — primary key in pending_intents table (D-12)
```

### Pattern 5: StateStore Migration 0003 — Daily Counter + Pending Intents

**What:** New ordered migration step following the `_migration_0002` callable pattern. Adds two tables: `daily_trade_count` (date + filled_count + session_date) and `pending_intents` (UUID, code, status, emitted_at, resolved_at). Never edits `_MIGRATION_0001` or `_migration_0002`.

```python
# bot/state/migrations.py — add migration 0003 (Phase 3, D-08/D-12)

_PENDING_INTENTS_0003_COLUMNS_pending_intents = [
    # table: pending_intents
    # intent_id: UUID string, primary key
    # code: stock code e.g. "US.AAPL"
    # status: "PENDING" | "RESOLVED" | "EXPIRED"
    # entry_price, stop_price, quantity: sizing snapshot for audit
    # emitted_at, resolved_at: ISO-8601 UTC strings
]

def _migration_0003(conn: sqlite3.Connection) -> None:
    """Add Phase 3 daily-trade counter and pending-intent tables (D-08, D-12).

    Idempotent: each CREATE TABLE uses IF NOT EXISTS so re-running after a
    partial failure does not raise 'table already exists'.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_trade_count (
            session_date    TEXT PRIMARY KEY,   -- "YYYY-MM-DD" ET date
            filled_count    INTEGER NOT NULL DEFAULT 0,
            updated_at      TEXT NOT NULL       -- ISO-8601 UTC
        );

        CREATE TABLE IF NOT EXISTS pending_intents (
            intent_id       TEXT PRIMARY KEY,   -- UUID
            code            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'PENDING',
            entry_price     REAL NOT NULL,
            stop_price      REAL NOT NULL,
            quantity        INTEGER NOT NULL,
            emitted_at      TEXT NOT NULL,      -- ISO-8601 UTC
            resolved_at     TEXT               -- NULL until Phase 4 resolves
        );
    """)
```

### Anti-Patterns to Avoid

- **Mid-bar strategy evaluation:** Never call `passes_intraday_filters()` when `time_key` has not advanced. Every evaluation path must go through the `BarAggregator` timestamp-advance check.
- **`asyncio.get_event_loop()` from SDK callback thread:** In Python 3.10+, calling this from a non-asyncio thread raises `DeprecationWarning` and may return the wrong loop. Always pass the loop via `BarAggregator.__init__()`.
- **Blocking in `on_recv_rsp`:** The SDK fires `on_recv_rsp` on its push thread. Any blocking call in that method blocks the SDK from processing subsequent pushes for all subscribed codes. The bridge call (`call_soon_threadsafe` or `run_coroutine_threadsafe`) must be non-blocking. [CITED: STACK.md — "never block in on_recv_rsp"]
- **Calling `accinfo_query` without `refresh_cache=True`:** OpenD caches account data; a stale cache is returned without this flag. The cache may be minutes old — live equity is required for RISK-01.
- **Reading `total_assets` of 0 as a valid value:** Zero `total_assets` on a SIMULATE account typically indicates a failed query or uninitialized account. Apply the `< $1,000` implausible guard and fall back to $100k (D-05).
- **Sizing from intended quantity, not rounded-down:** Always `math.floor()` the quantity before emitting `OrderIntent`. Over-risking by even 1 share violates RISK-01.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| DST-correct ET time for entry-window gate | Custom UTC-offset math | `et_helpers.now_et()` (`bot/safety/et_helpers.py`) | Already built, DST-tested (SVC-04) |
| Intraday filter math (I1/I2/I3) | Reimplementing filter conditions | `TrendJoinLong.passes_intraday_filters()` | Already built, config-driven, unit-tested (Phase 1) |
| Initial stop price | `lod * (1 - some_pct)` hardcoded | `TrendJoinLong.compute_initial_stop(lod)` | Already built; reads `cfg.initial_stop_pct` (RISK-03) |
| Strategy config constants | Python literals | `StrategyConfig` fields via `load_strategy_config()` | CFG-01 requirement; no literals in engine code |
| Atomic StateStore writes | Manual file I/O | `StateStore.conn.execute()` within transaction | SQLite WAL + existing migration pattern; proven |
| Premarket high for codes with no premarket | Fallback to prior-day high | Emit no signal (D-03) | Conservative correctness requirement — never trade on incomplete reference |
| RVOL calculation | Re-fetch/recompute each bar | Read `rvol_baseline` from `daily_scan` table (Phase 2 D-08) | Scanner already persists `rvol_baseline`; recomputing each bar wastes quota |

**Key insight:** The intraday filter functions, stop formula, and config params are all in `bot/strategy/`; Phase 3's job is to correctly orchestrate data flow INTO them, not to duplicate the math.

---

## Common Pitfalls

### Pitfall 1: Double-Firing on SDK Reconnect (SIG-02 violation)

**What goes wrong:** On any disconnection and reconnection between the Python process and OpenD, `is_first_push=True` causes the SDK to re-push the most recently cached bar (which is typically the current incomplete bar or the most recently closed bar). If `BarAggregator` only tracks `_last_time_key`, it sees the `time_key` of the re-pushed bar, determines it has "advanced" from nothing (because state was reset on reconnect), and fires strategy evaluation on what may be a bar that was already evaluated before the disconnect.

**Why it happens:** `is_first_push=True` is set in `MoomooGateway.subscribe()` (`is_first_push=True`) to get initial data. The reconnect pushes the cache regardless of prior bot state.

**How to avoid:** The `_seen_time_keys` per-code set persists across reconnects for the session lifetime. Do not reset `BarAggregator` state on reconnect — only reset at `reset_session()` (start of day). A re-pushed bar with a `time_key` already in `_seen_time_keys[code]` is silently ignored.

**Warning signs:** Two `SignalEvent`s emitted for the same code within the same 5m bar window. Duplicate log lines for the same `time_key`.

### Pitfall 2: RVOL Baseline Mismatch (SIG-03 false signals)

**What goes wrong:** Phase 3 reads `rvol_baseline` from the `daily_scan` table (Phase 2 D-08). If the scanner ran after a partial session (e.g., during an intraday rescan), `rvol_baseline` may reflect a different lookback end date than expected, producing a stale denominator.

**Why it happens:** `rvol_baseline` is computed at scan time with a `prior_trading_day` cutoff. An intraday rescan at 10:30 ET uses the same prior-day cutoff as the premarket scan — this is correct. The pitfall is code that recalculates RVOL using `now()` as the denominator end date, accidentally including today's partial data.

**How to avoid:** Phase 3 NEVER recomputes RVOL — it reads `rvol_baseline` from StateStore as-is. The intraday RVOL ratio is `(current_bar_volume / rvol_baseline) * normalization_factor`. Do not update `rvol_baseline` from Phase 3.

**Warning signs:** RVOL values > 100x on low-volume codes; I3 firing at market open when volume is thin.

### Pitfall 3: entry_price Used for Stop Sizing Before LOD Is Known

**What goes wrong:** `compute_initial_stop(lod)` takes LOD as input. If Phase 3 passes `bar.low` (the current closed bar's low) as `lod`, this is approximately correct only for the first signal of the session. After the first bar, LOD is the minimum low across all session bars, not just the current bar's low.

**Why it happens:** `BarAggregator` tracks `hod` (running max of highs) but has no equivalent `lod` running minimum. Signal is evaluated on bar close; the bar's low is easily confused with the session LOD.

**How to avoid:** `BarAggregator` must also track `_lod: Dict[str, float]` as a running min of session-bar lows, updated on each closed bar. The `BarEvent` carries both `bar.low` (closed bar low) and `lod` (session running min). `SignalEvent.lod` is the running minimum — that is what goes into `compute_initial_stop(lod)`.

**Warning signs:** Initial stop prices vary widely between signals on the same code; stop computed from a bar mid-session that was not actually the session low.

### Pitfall 4: Concurrent-Position Count from Stale Cache

**What goes wrong:** `gateway.get_positions()` calls `position_list_query()`. Without `refresh_cache=True`, OpenD returns cached data that may be stale by minutes, especially on a SIMULATE account where push reliability is explicitly lower. The concurrent cap check uses a stale position count, potentially allowing a 6th entry when 5 positions are actually open.

**Why it happens:** The existing `get_positions()` in `gateway.py` does not pass `refresh_cache=True` to `position_list_query()`.

**How to avoid:** For the concurrent-cap check in Phase 3, pass `refresh_cache=True` to `position_list_query()`. This is consistent with the `accinfo_query` pattern in `get_portfolio.py` (line 79: `refresh_cache=True`). Update `get_positions()` in the gateway to pass `refresh_cache=True`, or add a separate `get_positions_live()` variant.

**Warning signs:** More than 5 positions open in the broker account while the bot logs "concurrent cap not reached." Positions present in broker but not returned by the gateway query.

### Pitfall 5: Daily Cap Counting Intents Instead of Fills

**What goes wrong:** D-08 specifies that the authoritative daily counter is incremented at fill (Phase 4). If Phase 3 increments the `daily_trade_count.filled_count` column on every emitted `OrderIntent`, the counter is wrong — it counts intents, not fills, and will refuse new entries even when all intents were rejected by the broker or expired.

**Why it happens:** The D-09 gating logic (pending tally + filled count) is easy to conflate with the D-08 authoritative counter.

**How to avoid:** Phase 3 manages only the in-memory pending tally (`self._pending_count`). It reads `daily_trade_count.filled_count` from StateStore (written by Phase 4 on each fill). It never writes to `daily_trade_count`. Phase 3's check is: `filled_count + self._pending_count < max_trades_per_day`. The `_pending_count` increments on each `OrderIntent` emission and decrements when Phase 4 resolves or expires a `pending_intents` record.

**Warning signs:** `daily_trade_count.filled_count` advances without fills ever occurring. Daily entries are exhausted after the first bars of the session.

### Pitfall 6: entry_window Boundary Off-by-One

**What goes wrong:** The entry window is "10:05–15:30 ET". The question is whether 10:05:00 exactly and 15:30:00 exactly are IN or OUT of the window. The wrong boundary causes strategy divergence: a signal at exactly 15:30:00 fires vs doesn't fire.

**Why it happens:** `>=` vs `>` and `<` vs `<=` semantics are not specified in the time filter strings in `rules.json`.

**How to avoid:** Define the canonical boundary once in `SignalEngine`: `earliest_entry_et` is INCLUSIVE (signal at 10:05:00 is valid), `latest_entry_et` is EXCLUSIVE (signal at 15:30:00 is NOT valid — the bar that closes at exactly 15:30:00 is the last valid bar, not a new entry). This matches the strategy intent: "within 10:05–15:30 ET" means the last bar that opens before 15:30 is the last valid entry bar. Write a table-driven test asserting behaviour at 10:04:59 (out), 10:05:00 (in), 15:29:59 (in), 15:30:00 (out).

**Warning signs:** Signals fire at 10:04:xx ET; signals do NOT fire at 10:05:00 ET; or vice versa for 15:30.

---

## Code Examples

### Premarket Snapshot — Reading pre_high_price

```python
# Source: VERIFIED via openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html
# Field name: pre_high_price (NOT premarket_high or pre_market_high)
# Available without extended-hours subscription for US stocks

async def fetch_premarket_highs(self, codes: list) -> dict:
    """Return {code: pre_high_price} for the watchlist. D-01."""
    loop = asyncio.get_event_loop()
    ret, data = await loop.run_in_executor(
        None, lambda: self._quote_ctx.get_market_snapshot(codes)
    )
    if ret != RET_OK or data is None:
        return {}

    result = {}
    for i in range(len(data)):
        row = data.iloc[i] if hasattr(data, "iloc") else data[i]
        code = str(row.get("code", ""))
        pre_high = float(row.get("pre_high_price", 0) or 0)
        # D-03: exclude codes with no/zero premarket activity
        if code and pre_high > 0:
            result[code] = pre_high
    return result
```

### RiskEngine Sizing Formula

```python
# Source: REQUIREMENTS.md RISK-01/02/03, CONTEXT.md D-07
import math

def compute_order_intent(
    signal: SignalEvent,
    equity: float,
    cfg: StrategyConfig,
) -> Optional[OrderIntent]:
    """Size a trade from a signal. Returns None if qty < 1 (D-07)."""
    entry_price = signal.bar.close
    stop_price = trend_join_long.compute_initial_stop(signal.lod)

    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        _logger.warning("non_positive_stop_distance", code=signal.code)
        return None

    # 1% risk sizing (RISK-01)
    risk_dollars = equity * cfg.max_risk_per_trade_pct / 100.0
    risk_qty = math.floor(risk_dollars / stop_distance)

    # 10% notional cap (RISK-02)
    notional_cap = equity * cfg.max_position_size_pct / 100.0
    notional_cap_qty = math.floor(notional_cap / entry_price)

    # Take the smaller (D-07)
    qty = min(risk_qty, notional_cap_qty)

    if qty < 1:
        _logger.info(
            "intent_skipped_under_budget",
            code=signal.code,
            risk_qty=risk_qty,
            notional_cap_qty=notional_cap_qty,
        )
        return None

    return OrderIntent(
        code=signal.code,
        entry_price=entry_price,
        stop_price=stop_price,
        quantity=qty,
        equity_used=equity,
        risk_dollars=risk_dollars,
        notional=entry_price * qty,
        emitted_at=now_et(),
        source_signal=signal,
        intent_id=str(uuid.uuid4()),
    )
```

### Entry-Window Time Gate

```python
# Source: bot/safety/et_helpers.py (ET, now_et) + CONTEXT.md time_filter
from datetime import time
from bot.safety.et_helpers import now_et, ET

def _in_entry_window(cfg: StrategyConfig) -> bool:
    """Return True if current ET time is within [earliest_entry, latest_entry).

    earliest_entry is inclusive; latest_entry is exclusive.
    Parses HH:MM strings from cfg (e.g. "10:05", "15:30").
    """
    now = now_et().time()
    h_e, m_e = map(int, cfg.earliest_entry_et.split(":"))
    h_l, m_l = map(int, cfg.latest_entry_et.split(":"))
    earliest = time(h_e, m_e)
    latest = time(h_l, m_l)
    return earliest <= now < latest
```

### RVOL Computation from Watchlist Baseline

```python
# Phase 3 reads rvol_baseline from StateStore (Phase 2 D-08 persisted it).
# Intraday RVOL = cumulative_volume_so_far / rvol_baseline
# rvol_baseline was computed at scan time as average volume at the same
# approximate time of day over the prior rvol_lookback_days sessions.
# Phase 3 simply reads the stored value — no re-fetch, no recalculation.

def load_watchlist(store: StateStore, session_date: str) -> dict:
    """Load today's watchlist from StateStore including rvol_baseline.

    Returns {moomoo_code: {prior_day_high, prior_close, sma200, rvol_baseline}}
    """
    rows = store.conn.execute(
        "SELECT code, prior_day_high, prior_close, sma200, rvol_baseline "
        "FROM daily_scan WHERE scan_date = ? ORDER BY rank",
        (session_date,)
    ).fetchall()
    return {
        r[0]: {
            "prior_day_high": r[1],
            "prior_close": r[2],
            "sma200": r[3],
            "rvol_baseline": r[4],
        }
        for r in rows
    }
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Evaluating signals on every tick / push event | Evaluate only on timestamp advance (bar close) | Industry standard for bar-by-bar systems | Eliminates repainting / mid-bar false signals |
| Polling broker for live data during signal path | SDK push for bar data; one-shot snapshot for premarket high | Moomoo SDK design pattern | Avoids quota exhaustion on 20-code watchlist |
| asyncio.get_event_loop() from any thread | Pass loop explicitly; use call_soon_threadsafe from non-asyncio threads | Python 3.10+ deprecation | Eliminates RuntimeWarning; correct across threads |
| Fixed equity baseline ($100k) for sizing | Live accinfo_query(refresh_cache=True) at every sizing decision | RISK-01 requirement | Sizing tracks real P&L through the session |

**Deprecated / avoid:**
- `asyncio.get_event_loop()` from non-asyncio threads: deprecated in Python 3.10+; use `asyncio.get_running_loop()` from asyncio context, or pass the loop object explicitly.
- `utcnow()`: deprecated in Python 3.12+; confirmed by Phase 1 decision log — use `datetime.now(timezone.utc)`.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `pre_high_price` is populated in `get_market_snapshot` without an extended-hours subscription for US stocks near 09:30 ET | Standard Stack / Code Examples | Pre_high_price returns 0 or N/A; all watchlist codes would be excluded from signal evaluation (D-03) — conservative but session-wide miss. Mitigation: log count of codes with valid pre_high_price at 09:30; alert operator if zero |
| A2 | The SDK fires `on_recv_rsp` on mid-bar tick updates (not only at bar close) | Pattern 1 / Pitfall 1 | If SDK fires ONLY at bar close, the dedup logic in `_seen_time_keys` is redundant but harmless. Cannot be verified from SDK docs without empirical test. Mitigation: timestamp-advance detection is correct regardless |
| A3 | `is_first_push=True` in `gateway.subscribe()` causes re-push of the current incomplete bar on reconnect | Pitfall 1 | If re-push does not include incomplete bars (only last complete bar), dedup is harmless. Conservative handling is safe either way |
| A4 | `total_assets` from `accinfo_query` on a SIMULATE account equals cash + market value of open positions (net liquidation value) | Pattern 3 / D-04 | Confirmed via FIELD_MAPPING.md ("Total Assets → total_assets — Account net asset value"). MEDIUM risk: SIMULATE account may return 0 if unfunded; $100k fallback covers this |
| A5 | `$1,000` is a reasonable implausible-value threshold for the paper account equity fallback | Pattern 3 / D-05 | If the operator funds the SIMULATE account to less than $1,000, all sizing calls fall back to $100k — consistent over-sizing. Operator should fund to ~$100k per CLAUDE.md constraint |

**If this table is empty:** — it is not; A1–A5 above require confirmation during Phase 3 execution (first live test run verifies A1 concretely).

---

## Open Questions (RESOLVED)

1. **Does `pre_high_price` return a value before premarket trading has occurred?**
   - What we know: The field exists and is documented for US stocks. The plan calls for reading it "near 09:30 ET" (premarket ends at 09:30). If no premarket trades occurred for a specific watchlist code, `pre_high_price` may return 0 or N/A.
   - What's unclear: Whether 0 vs N/A vs a non-zero placeholder is returned for illiquid codes with no premarket activity.
   - RESOLVED: D-03 governs — any code where `pre_high_price <= 0` (zero, N/A, or unavailable) is excluded from signal evaluation for the session; never fall back to prior-day high. The fetch helper logs a count of codes with valid `pre_high_price` at 09:30 so the operator can see which codes lacked premarket data. Implemented in 03-01 Task 3 (`MoomooGateway.get_market_snapshot` — raw broker read, wave 1 so the consumer can use it) + 03-02 Task 1 (`fetch_premarket_highs` session-init reads `pre_high_price`, applies the D-03 exclusion, and freezes the rest).

2. **How does `BarAggregator` receive a `bars_5m` DataFrame for `passes_intraday_filters()`?**
   - What we know: `passes_intraday_filters(code, bars_5m, ...)` expects a DataFrame of closed 5m bars. The `BarAggregator` receives bars one at a time. A rolling buffer of recent closed bars must be maintained.
   - What's unclear: How many bars to keep (the RVOL computation and swing-low trail need varying lookbacks). Phase 3 only needs the last closed bar's close for I1/I2 and the running RVOL from the baseline — the full multi-bar `bars_5m` is mainly needed for Phase 4 swing-low trailing.
   - RESOLVED: `BarAggregator` maintains a per-code `deque(maxlen=50)` of closed-bar dicts (≈4h+ of 5m history). `passes_intraday_filters()` receives a DataFrame constructed from this deque. Implemented in 03-01 Task 2.

3. **What is the correct `lod` to pass to `compute_initial_stop()`?**
   - What we know: `lod` should be the low-of-day — the minimum low across all regular-session bars so far. The `BarAggregator` must track this running minimum alongside the HOD maximum.
   - What's unclear: Whether the regular-session LOD should include the very first bar (which may be influenced by the open auction) or whether it should be the LOD since 09:35 ET.
   - RESOLVED: the `lod` passed to `compute_initial_stop()` is the session running-min across ALL regular-session 5m bars from the FIRST K_5M bar of the session (typically the `09:30–09:35 ET` bar) — NOT a single bar's low. This is the most conservative LOD and aligns with the strategy description. `BarAggregator` tracks `_lod[code]` as a running min updated on every push from the first bar; `SignalEvent.lod` carries this session running-min and is what flows into `compute_initial_stop(lod)`. Implemented in 03-01 Task 2.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.9+ | `zoneinfo`, `asyncio.get_event_loop()` deprecation awareness | Assumed ✓ | System Python (≥3.9 per Phase 1) | — |
| moomoo-api | `CurKlineHandlerBase`, `accinfo_query`, `get_market_snapshot` | ✓ | 10.7.6708 | — |
| OpenD (127.0.0.1:11111) | All gateway calls | Checked at `MoomooGateway.connect()` | — | No fallback — bot is non-functional without it |
| SIMULATE paper account | `get_equity()`, `get_positions()` | Assumed ✓ (operator funded) | — | $100k equity fallback (D-05) |

**Missing dependencies with no fallback:** OpenD must be running. All other dependencies are stdlib or already installed.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x (existing, from Phase 1) |
| Config file | None — pytest auto-discovers |
| Quick run command | `pytest tests/signal/ tests/risk/ -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIG-02 | Bar-close fires only on timestamp advance, never mid-bar | unit | `pytest tests/signal/test_bar_aggregator.py::test_no_signal_mid_bar -x` | ❌ Wave 0 |
| SIG-02 | Session dedup prevents double-fire on reconnect re-push | unit | `pytest tests/signal/test_bar_aggregator.py::test_no_double_fire_on_reconnect -x` | ❌ Wave 0 |
| SIG-03 | Signal emits only when all 3 filters + time gate pass | unit | `pytest tests/signal/test_signal_engine.py::test_all_gates_required -x` | ❌ Wave 0 |
| SIG-03 | Entry window boundary: 10:04:59 out, 10:05:00 in, 15:29:59 in, 15:30:00 out | unit | `pytest tests/signal/test_signal_engine.py::test_entry_window_boundaries -x` | ❌ Wave 0 |
| SIG-04 | No signal when concurrent position count >= max_concurrent_positions | unit | `pytest tests/signal/test_signal_engine.py::test_concurrent_cap -x` | ❌ Wave 0 |
| RISK-01 | Sizing uses live equity, not cached | unit (mock gateway) | `pytest tests/risk/test_risk_engine.py::test_live_equity_called -x` | ❌ Wave 0 |
| RISK-01 | $100k fallback when equity query fails or returns implausible value | unit | `pytest tests/risk/test_risk_engine.py::test_equity_fallback -x` | ❌ Wave 0 |
| RISK-02 | Notional cap applied when it is smaller than 1%-risk qty | unit | `pytest tests/risk/test_risk_engine.py::test_notional_cap -x` | ❌ Wave 0 |
| RISK-03 | OrderIntent carries correct stop_price and quantity | unit | `pytest tests/risk/test_risk_engine.py::test_intent_fields_correct -x` | ❌ Wave 0 |
| RISK-04 | No OrderIntent when concurrent positions >= cap | unit | `pytest tests/signal/test_signal_engine.py::test_concurrent_cap -x` | ❌ Wave 0 |
| RISK-05 | No OrderIntent when daily cap reached; closing a position does not reset cap | unit | `pytest tests/risk/test_risk_engine.py::test_daily_cap_independent_of_concurrent -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/signal/ tests/risk/ -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/signal/__init__.py` — package init
- [ ] `tests/signal/test_bar_aggregator.py` — covers SIG-02 (timestamp advance, reconnect dedup, HOD tracking)
- [ ] `tests/signal/test_signal_engine.py` — covers SIG-03, SIG-04 (gate combinations, boundary times, concurrent cap, daily cap)
- [ ] `tests/risk/__init__.py` — package init
- [ ] `tests/risk/test_risk_engine.py` — covers RISK-01..05 (sizing math, notional cap, <1-share guard, equity fallback, intent fields)
- [ ] `bot/signal/__init__.py`, `bot/risk/__init__.py` — new package inits
- [ ] Migration 0003 test: verify `daily_trade_count` and `pending_intents` tables created idempotently

---

## Security Domain

Phase 3 introduces no new authentication surfaces. Applicable constraints:

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V5 Input Validation | Yes | Validate `pre_high_price > 0` before storing; validate `total_assets > _IMPLAUSIBLE_THRESHOLD` before using; validate `quantity >= 1` before emitting |
| V6 Cryptography | No | No new crypto; `intent_id` uses `uuid.uuid4()` (not a secret) |
| V2 Authentication | No | No new auth paths; uses existing OpenD session |

**New threat pattern for Phase 3:**

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SDK pushes malformed DataFrame row (corrupt data) | Tampering | `try/except` around all `row.get()` calls; default to 0 on any failure; never let one bad push crash the aggregator |
| `accinfo_query` returns implausibly large equity ($1B+) | Tampering / data error | Apply upper bound too: `if total_assets > 10_000_000: use fallback` (upper implausible threshold prevents outsized position sizing) |

---

## Project Constraints (from CLAUDE.md)

These directives are required by `CLAUDE.md` and override default research recommendations:

| Directive | Impact on Phase 3 |
|-----------|-------------------|
| Python 3.6+ (tested modern 3.x) | No walrus operator, no structural pattern matching; use stdlib only |
| Must reuse `moomoo-api` SDK — no rewrite of broker access | `BarAggregator` must be a `CurKlineHandlerBase` subclass; gateway wraps SDK directly |
| `skills/moomooapi` is a CLI reference — do NOT import from it | All new gateway methods go in `bot/gateway/gateway.py`, not `skills/` |
| `FUTU_TRD_ENV=SIMULATE` — paper only | `accinfo_query` and `position_list_query` always called with `trd_env=TrdEnv.SIMULATE` |
| All strategy parameters from `rules.json` (CFG-01) | No numeric literals in signal/risk engine: all thresholds from `cfg.*` |
| ET timezone throughout (US Eastern) | Entry-window gate uses `now_et()` from `et_helpers.py`; time_key from SDK is already ET for US stocks |
| `FUTU_ACC_ID` must be set explicitly | `get_equity()` reads `self.cfg.acc_id`; never uses default 0 in production |
| snake_case files, PascalCase classes, UPPER_SNAKE constants | `bar_aggregator.py`, `signal_engine.py`, `risk_engine.py`; `BarAggregator`, `SignalEngine`, `RiskEngine` |
| Module docstrings on all files, function docstrings on all public functions | All new modules must include docstrings |
| SDK imports deferred inside methods (gateway pattern) | `CurKlineHandlerBase` import inside `BarAggregator.__init__` or method, not at module top level |

---

## Sources

### Primary (HIGH confidence)
- `openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html` — field names including `pre_high_price`; confirmed available for US stocks
- `openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html` — `is_first_push` reconnect behaviour: "last data before disconnection will be pushed again"
- `openapi.moomoo.com/moomoo-api-doc/en/quick/strategy-sample.html` — `time_key` advance pattern for bar-close detection; `CurKlineHandlerBase.on_recv_rsp` usage
- `skills/moomooapi/docs/FIELD_MAPPING.md` — `total_assets` = "Account net asset value" for `accinfo_query`; confirmed for SIMULATE
- `skills/moomooapi/scripts/subscribe/push_kline.py` — exact `CurKlineHandlerBase` subclass pattern; `on_recv_rsp` signature; `RET_OK` check
- `skills/moomooapi/scripts/trade/get_portfolio.py` — `accinfo_query` with `refresh_cache=True`; `total_assets` field access pattern
- `bot/gateway/gateway.py` — `run_in_executor` async pattern; `_check_ret`, `_parse_trd_env` helpers
- `bot/state/migrations.py` — callable migration pattern (`_migration_0002`); `PRAGMA user_version` bump; idempotent `ALTER TABLE` guard
- `bot/strategy/trend_join_long.py` — `passes_intraday_filters()` and `compute_initial_stop()` signatures and semantics
- `bot/config/loader.py` — `StrategyConfig` fields: `rvol_min`, `max_risk_per_trade_pct`, `max_position_size_pct`, `max_concurrent_positions`, `max_trades_per_day`, `earliest_entry_et`, `latest_entry_et`
- `docs.python.org/3/library/asyncio-threading.html` — `loop.call_soon_threadsafe()` and `asyncio.run_coroutine_threadsafe()` semantics for cross-thread asyncio scheduling

### Secondary (MEDIUM confidence)
- `openapi.moomoo.com/moomoo-api-doc/en/quote/update-kl.html` — `CurKlineHandlerBase` callback fields (`time_key`, `open`, `high`, `low`, `close`, `volume`, `k_type`, `last_close`) [CITED]
- `openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html` — Transaction FAQ; push reliability note for US paper accounts [CITED via PITFALLS.md]
- `skills/moomooapi/docs/API_LIMITS.md` — `get_market_snapshot` max 400 codes per request (≤20 codes is well within limit) [VERIFIED: codebase]

### Tertiary (LOW confidence)
- WebSearch results on `CurKlineHandlerBase` `time_key` advance bar-close detection — multiple community examples confirm timestamp-advance pattern; no contradictory source found
- WebSearch results on `asyncio.run_coroutine_threadsafe` from trading bot SDK threads — confirms this is the correct primitive; no deadlock risk when called from a non-asyncio thread with an explicit loop reference

---

## Metadata

**Confidence breakdown:**
- SDK field names (`pre_high_price`, `total_assets`): HIGH — verified via official docs and existing codebase (FIELD_MAPPING.md, get_portfolio.py)
- Thread→asyncio bridge pattern: HIGH — Python stdlib docs are canonical; pattern is widely used
- Bar-close detection via timestamp advance: HIGH — confirmed by official strategy sample; pattern is unambiguous
- Reconnect mid-bar behaviour: MEDIUM — `is_first_push` documented; exact incomplete-bar re-push timing is assumed (A2/A3)
- `pre_high_price` without extended subscription: MEDIUM — field documented; subscription requirement not explicitly stated; A1 logs zero-value guard as mitigation
- Migration 0003 shape: HIGH — follows established callable-migration pattern exactly

**Research date:** 2026-06-24
**Valid until:** 2026-08-01 (moomoo SDK field names stable; SDK version pinned at 10.7.6708)
