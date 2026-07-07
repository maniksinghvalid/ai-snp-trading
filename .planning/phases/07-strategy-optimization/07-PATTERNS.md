# Phase 07: Strategy Optimization — Pattern Map

**Mapped:** 2026-07-03
**Files analyzed:** 14 new/modified files
**Analogs found:** 14 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bot/signal/bar_aggregator.py` | service | event-driven | self (extend) | exact |
| `bot/signal/events.py` | model | — | self (extend) | exact |
| `bot/signal/signal_engine.py` | service | event-driven | self (extend — Gate 7) | exact |
| `bot/gateway/gateway.py` | service | request-response | self (extend — place_stop_order) | exact |
| `bot/state/migrations.py` | migration | batch | self (extend — migration 0005) | exact |
| `bot/state/store.py` | service | CRUD | self (extend — TOD + circuit-breaker methods) | exact |
| `bot/scanner/fetcher.py` | utility | batch | self (extend — download_intraday_5m) | exact |
| `bot/scanner/scanner.py` | service | batch | self (extend — TOD baseline computation) | exact |
| `bot/position/manager.py` | service | event-driven | self (extend — broker stop wiring) | exact |
| `bot/config/loader.py` | config | — | self (extend — new StrategyConfig fields) | exact |
| `bot/config/schema.py` | config | — | self (extend — new JSON schema fields) | exact |
| `rules.json` | config | — | self (extend — new keys) | exact |
| `tests/signal/test_bar_aggregator.py` | test | — | self (extend) | exact |
| `tests/signal/test_signal_engine.py` | test | — | self (extend) | exact |
| `tests/gateway/test_gateway.py` | test | — | self (extend) | exact |
| `tests/position/test_manager.py` | test | — | self (extend) | exact |
| `tests/state/test_store.py` | test | — | self (extend) | exact |
| `tests/state/test_migrations.py` | test | — | self (extend) | exact |

---

## Pattern Assignments

### `bot/signal/bar_aggregator.py` — add `_session_volume` accumulator + `cum_volume` in bar_data

**Analog:** self — extend `_handle_row` and `reset_session`

**Existing `__init__` state dict pattern** (lines 119–124):
```python
self._last_time_key: Dict[str, str] = {}
self._seen_time_keys: Dict[str, Set[str]] = {}
self._hod: Dict[str, float] = {}
self._lod: Dict[str, float] = {}
self._cur_bar: Dict[str, dict] = {}
self._bar_buffer: Dict[str, deque] = {}
```
New field to add in the same block:
```python
self._session_volume: Dict[str, int] = {}
```

**Existing `reset_session` pattern** (lines 126–139):
```python
def reset_session(self) -> None:
    self._last_time_key.clear()
    self._seen_time_keys.clear()
    self._hod.clear()
    self._lod.clear()
    self._cur_bar.clear()
    self._bar_buffer.clear()
    _logger.info("bar_aggregator_session_reset")
```
Add to the clear block (same pattern):
```python
self._session_volume.clear()
```

**Existing closed-bar `bar_data` dict construction** (lines 289–299) — the dict to extend:
```python
bar_data = {
    "code": code,
    "time_key": closed_time_key,
    "open": closed_ohlcv["open"],
    "high": closed_ohlcv["high"],
    "low": closed_ohlcv["low"],
    "close": closed_ohlcv["close"],
    "volume": closed_ohlcv["volume"],
    "hod": snap_hod,
    "lod": snap_lod,
}
```
Insert the accumulator update BEFORE constructing `bar_data` (after `closed_ohlcv` is read, lines ~263–265):
```python
closed_vol = closed_ohlcv["volume"]
self._session_volume[code] = self._session_volume.get(code, 0) + closed_vol
```
Then add to `bar_data`:
```python
"cum_volume": self._session_volume[code],
```

---

### `bot/signal/events.py` — add `cum_volume` field to `BarEvent`

**Analog:** self — extend the `BarEvent` dataclass

**Existing `BarEvent` dataclass** (lines 17–50) — copy the field pattern exactly:
```python
@dataclass
class BarEvent:
    code: str
    time_key: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    hod: float
    lod: float
```
Add after `volume: int`:
```python
cum_volume: int = 0   # cumulative session volume through and including this bar (Phase 7 RVOL-TOD)
```
Use `= 0` default so existing tests that construct `BarEvent` positionally or by keyword without `cum_volume` continue to work.

---

### `bot/signal/signal_engine.py` — add Gate 7 (circuit breaker)

**Analog:** self — extend `on_bar` gate stack; Gate 6 pattern is the direct model

**Existing Gate 6 (daily-cap)** — the pattern for Gate 7 (lines 563–579):
```python
# Gate 6: Daily new-entry cap (RISK-05 / D-09 burst guard)
filled_count = self._get_filled_count(session_date_str)
total_entries = filled_count + self._pending_count

if total_entries >= self._cfg.max_trades_per_day:
    _logger.info(
        "signal_skipped_daily_cap",
        code=code,
        filled_count=filled_count,
        pending_count=self._pending_count,
        total_entries=total_entries,
        max_trades_per_day=self._cfg.max_trades_per_day,
        reason="daily entry cap reached (RISK-05/D-09) — no further entries this session",
    )
    return None
```

**Gate 7 insert — copy the Gate 6 short-circuit shape exactly; add after Gate 3 (entry window)
and before Gate 4 (broker get_positions call) to skip SDK round-trip on tripped days:**
```python
# Gate 7: Daily -2R circuit breaker (RISK-CIRCUIT / D-05/D-06/D-07)
if self._is_circuit_breaker_tripped(session_date_str):
    _logger.info(
        "signal_skipped_circuit_breaker",
        code=code,
        reason="daily -2R circuit breaker tripped — no new entries this session",
    )
    return None
```

**New private method — `_is_circuit_breaker_tripped`:**
```python
def _is_circuit_breaker_tripped(self, session_date_str: str) -> bool:
    """Return True if the -2R daily circuit breaker is active for this session.

    Check order (D-07 restart-persistence):
      1. If meta.circuit_breaker_tripped_date == today → already tripped, return True.
      2. If stored date is from a prior session → auto-reset (D-07), return False.
      3. Query today's realized P&L; if <= -2R threshold → trip, persist, return True.
      4. Otherwise return False.

    Callers: on_bar Gate 7 only. Never increments _pending_count.
    """
    stored_date = self._store.get_circuit_breaker_date()
    if stored_date == session_date_str:
        return True
    if stored_date is not None and stored_date < session_date_str:
        self._store.clear_circuit_breaker()

    stats = self._store.get_daily_trade_stats(session_date_str)
    realized = stats.get("realized_pnl", 0.0)
    one_r = (self._cfg.max_risk_per_trade_pct / 100.0) * (
        self._cfg.sizing_equity_usd if self._cfg.sizing_equity_usd else 100_000.0
    )
    threshold = -(getattr(self._cfg, "daily_circuit_breaker_r", 2.0)) * one_r

    if realized <= threshold:
        self._store.set_circuit_breaker_date(session_date_str)
        _logger.warning(
            "circuit_breaker_tripped",
            realized_pnl=realized,
            threshold=threshold,
            session_date=session_date_str,
        )
        return True

    return False
```

**RVOL-TOD update inside Gate 2 — replace the single-line `rvol = event.volume / rvol_baseline` (line 466):**
```python
# I3-TOD: look up the time-of-day bucketed baseline when available.
# time_key format is "YYYY-MM-DD HH:MM:00" — extract "HH:MM" for bucket lookup.
time_bucket = event.time_key[11:16]  # "HH:MM"
tod_baseline = self._store.get_tod_baseline(session_date_str, code, time_bucket)
if tod_baseline > 0.0:
    rvol = event.cum_volume / tod_baseline   # TOD-normalized (primary path)
else:
    rvol = event.volume / rvol_baseline if rvol_baseline > 0 else 0.0  # legacy fallback
```

**`session_date_str` convention (WR-06 — already established at line 455):**
```python
session_date_str = now_et().date().isoformat()
```
All store lookups (`get_rvol_baseline`, `get_circuit_breaker_date`, `get_tod_baseline`,
`get_daily_trade_stats`) use this same ET-date string.

---

### `bot/gateway/gateway.py` — add `place_stop_order` method

**Analog:** `place_order` (lines 624–674) — copy the run_in_executor + deferred-import pattern exactly

**`place_order` pattern to copy** (lines 624–674):
```python
async def place_order(self, code: str, qty: int, price: float, trd_side) -> str:
    from moomoo import OrderType  # deferred import — test-env compatible
    loop = asyncio.get_running_loop()

    def _place_blocking():
        ret, data = self._trade_ctx.place_order(
            price=float(price),
            qty=int(qty),
            code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "place_order")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        order_id = str(row.get("order_id", "") or row.get("orderID", ""))
        from bot.safety.audit_log import append_audit
        append_audit({
            "event": "place_order",
            "code": code,
            "qty": int(qty),
            "price": float(price),
            "order_id": order_id,
        })
        return order_id

    order_id = await loop.run_in_executor(None, _place_blocking)
    _logger.info("order_placed", code=code, qty=qty, price=price, order_id=order_id)
    return order_id
```

**New `place_stop_order` — copy the above shape, change `order_type` and add `aux_price`:**
```python
async def place_stop_order(self, code: str, qty: int, stop_price: float, trd_side) -> str:
    """Place a Stop-Market protective sell order (EXEC-02 amendment D-01).

    OrderType.STOP with aux_price=stop_price. When market touches aux_price,
    the order converts to a market order. Long-position stops use TrdSide.SELL.
    EXEC-02 exception: stop-market is the ONLY allowed market-order type.

    Returns broker-assigned order_id.
    Raises GatewayError on non-RET_OK.
    """
    from moomoo import OrderType  # deferred import
    loop = asyncio.get_running_loop()

    def _blocking():
        ret, data = self._trade_ctx.place_order(
            price=0.0,                          # market price when triggered (D-01)
            qty=int(qty),
            code=code,
            trd_side=trd_side,                  # TrdSide.SELL for long-position protection
            order_type=OrderType.STOP,          # EXEC-02 amendment — only allowed market type
            aux_price=float(stop_price),        # trigger price = trail_stop
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "place_stop_order")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        order_id = str(row.get("order_id", "") or row.get("orderID", ""))
        from bot.safety.audit_log import append_audit
        append_audit({
            "event": "stop_order_placed",
            "code": code,
            "qty": int(qty),
            "stop_price": float(stop_price),
            "order_id": order_id,
        })
        return order_id

    order_id = await loop.run_in_executor(None, _blocking)
    _logger.info("stop_order_placed", code=code, qty=qty, stop_price=stop_price, order_id=order_id)
    return order_id
```

**`cancel_order` pattern** (lines 676–707) — reused unchanged for D-04 trail-sync cancel step:
```python
async def cancel_order(self, order_id: str) -> None:
    from moomoo import ModifyOrderOp
    loop = asyncio.get_running_loop()

    def _cancel_blocking():
        ret, data = self._trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "cancel_order")

    await loop.run_in_executor(None, _cancel_blocking)
    _logger.info("order_cancelled", order_id=order_id)
```

---

### `bot/state/migrations.py` — add migration 0005

**Analog:** `_migration_0004` (lines 203–217) — callable with `PRAGMA table_info` guard. Copy this pattern exactly.

**`_migration_0004` pattern to copy** (lines 203–217):
```python
_POSITIONS_0004_COLUMNS = (
    ("entry_order_id", "TEXT"),
    ("exit_order_id",  "TEXT"),
    ("avg_fill_price", "REAL"),
)

def _migration_0004(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0004_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
```

**New `_migration_0005`** — `CREATE TABLE` (idempotent via IF NOT EXISTS) + ALTER TABLE guard:
```python
_POSITIONS_0005_COLUMNS = (
    ("broker_stop_order_id", "TEXT"),   # broker order_id of the live protective stop (D-01/D-04)
)

def _migration_0005(conn: sqlite3.Connection) -> None:
    """Add Phase 7 tod_baselines table + broker_stop_order_id column on positions.

    tod_baselines: stores 14-day average cumulative session volume per
    (scan_date, code, time_bucket) for the RVOL-TOD gate (SIG-RVOL-TOD).

    broker_stop_order_id: nullable column on positions — null before the first
    stop is placed; set to the broker order_id after place_stop_order() succeeds (D-01).

    Uses CREATE TABLE IF NOT EXISTS for the new table (idempotent without a guard).
    Uses PRAGMA table_info guard on the ALTER TABLE (WR-03 pattern from 0002/0004).
    Uses conn.execute() not executescript() for atomic commit with user_version bump (WR-03).
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS tod_baselines (
        scan_date    TEXT NOT NULL,
        code         TEXT NOT NULL,
        time_bucket  TEXT NOT NULL,
        cum_vol_mean REAL NOT NULL,
        PRIMARY KEY (scan_date, code, time_bucket)
    )""")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0005_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
```

**MIGRATIONS list update** (line 220–225) — append to list, bump CURRENT_VERSION:
```python
MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,
    _migration_0003,
    _migration_0004,
    _migration_0005,   # Phase 7: tod_baselines + broker_stop_order_id
]

CURRENT_VERSION = 5
```

---

### `bot/state/store.py` — add TOD baseline CRUD + circuit-breaker meta methods

**Analog:** `get_rvol_baseline` (lines 601–619) — scalar fetch with `self._lock`. Copy exactly.

**`get_rvol_baseline` pattern** (lines 601–619):
```python
def get_rvol_baseline(self, scan_date: str, code: str) -> float:
    with self._lock:
        row = self._conn.execute(
            "SELECT rvol_baseline FROM daily_scan WHERE scan_date=? AND code=?",
            (scan_date, code),
        ).fetchone()
    return float(row[0]) if row is not None else 0.0
```

**New `get_tod_baseline`** — same lock + parameterized query pattern:
```python
def get_tod_baseline(self, scan_date: str, code: str, time_bucket: str) -> float:
    """Return TOD cumulative-volume baseline for (scan_date, code, time_bucket).

    Returns 0.0 when no row exists (caller falls back to legacy RVOL).
    scan_date: ET date ISO string (e.g. "2026-07-03").
    time_bucket: "HH:MM" (e.g. "10:05").
    """
    with self._lock:
        row = self._conn.execute(
            "SELECT cum_vol_mean FROM tod_baselines WHERE scan_date=? AND code=? AND time_bucket=?",
            (scan_date, code, time_bucket),
        ).fetchone()
    return float(row[0]) if row is not None else 0.0
```

**`upsert_position` pattern** (lines 291–362 — the UPSERT with ON CONFLICT pattern):
```python
self._conn.execute(
    """INSERT INTO positions (...) VALUES (...)
       ON CONFLICT(position_id) DO UPDATE SET ...""",
    (...),
)
self._conn.commit()
```

**New `upsert_tod_baselines`** — batch upsert using the same ON CONFLICT pattern:
```python
def upsert_tod_baselines(self, scan_date: str, code: str, baselines: dict) -> None:
    """Upsert TOD cumulative-volume baselines for a scan candidate.

    baselines: dict of {"HH:MM": float} — time_bucket → 14-day avg cum_vol.
    Uses INSERT OR REPLACE for batch upsert (simpler than ON CONFLICT UPDATE
    when replacing the entire row is acceptable — no partial-update needed).
    Commits once after the batch (not per row).
    """
    with self._lock:
        self._conn.executemany(
            """INSERT OR REPLACE INTO tod_baselines
               (scan_date, code, time_bucket, cum_vol_mean)
               VALUES (?, ?, ?, ?)""",
            [(scan_date, code, bucket, val) for bucket, val in baselines.items()],
        )
        self._conn.commit()
```

**Circuit-breaker meta methods — copy `get_daily_trade_stats` lock pattern** (lines 386–405):
```python
# get_daily_trade_stats uses: with self._lock: ... self._conn.execute(...).fetchone()
```

**New circuit-breaker methods:**
```python
def get_circuit_breaker_date(self) -> Optional[str]:
    """Return the ET date string when the breaker last tripped, or None (D-07)."""
    with self._lock:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='circuit_breaker_tripped_date'"
        ).fetchone()
    return row[0] if row else None

def set_circuit_breaker_date(self, date_str: str) -> None:
    """Persist the breaker trip date to the meta table (upsert, D-07)."""
    with self._lock:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('circuit_breaker_tripped_date', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (date_str,),
        )
        self._conn.commit()

def clear_circuit_breaker(self) -> None:
    """Remove the breaker state row (auto-reset on new session, D-07)."""
    with self._lock:
        self._conn.execute(
            "DELETE FROM meta WHERE key='circuit_breaker_tripped_date'"
        )
        self._conn.commit()
```

---

### `bot/scanner/fetcher.py` — add `download_intraday_5m`

**Analog:** `download_intraday_1m` (lines 507–543) — copy this function, change `download_kwargs`

**`download_intraday_1m` pattern to copy** (lines 507–543):
```python
def download_intraday_1m(
    yf_symbols: list,
    threads: int = 5,
    degradation_threshold: float = 0.10,
) -> Tuple[object, Set[str]]:
    return _download_batch(
        yf_symbols,
        threads=threads,
        degradation_threshold=degradation_threshold,
        download_kwargs={"period": "1d", "interval": "1m", "prepost": True},
        abort_event="intraday_scan_aborted_data_degradation",
        partial_event="intraday_partial_data",
        degradation_message="Intraday data degradation",
    )
```

**New `download_intraday_5m`** — same shape, change kwargs for 30-day 5m history:
```python
def download_intraday_5m(
    yf_symbols: list,
    threads: int = 5,
    degradation_threshold: float = 0.10,
) -> Tuple[object, Set[str]]:
    """Download ~30-calendar-day 5m intraday OHLCV bars for TOD baseline computation.

    Uses yf.download(interval="5m", period="30d") — gives ~21 trading sessions,
    sufficient for the 14-session TOD lookback (SIG-RVOL-TOD). prepost=False:
    only regular-session bars needed for the cumulative volume baseline.

    Mirrors download_intraday_1m contract: same _download_batch delegate,
    same degradation gate, same failed-set return.
    """
    return _download_batch(
        yf_symbols,
        threads=threads,
        degradation_threshold=degradation_threshold,
        download_kwargs={"period": "30d", "interval": "5m", "prepost": False},
        abort_event="tod_baseline_scan_aborted_data_degradation",
        partial_event="tod_baseline_partial_data",
        degradation_message="TOD baseline 5m data degradation",
    )
```

---

### `bot/scanner/scanner.py` — add TOD baseline computation at premarket scan

**Analog:** existing per-candidate loop that computes `rvol_baseline_val` (find the per-candidate yfinance processing block). The TOD computation attaches to the same loop.

**Pattern to follow** — existing `rvol_baseline` computation in the scanner (per RESEARCH.md Finding C-4, `scanner.py` line ~138):
```python
rvol_baseline_val = float(prior_sorted["volume"].mean())
```

**New helper `_compute_tod_baselines(daily_frame_5m, lookback_days=14)`** to add to scanner:
```python
def _compute_tod_baselines(daily_frame_5m, lookback_days: int = 14) -> dict:
    """Compute 14-session average cumulative volume at each 5m time bucket.

    daily_frame_5m: DataFrame with DatetimeIndex (ET-localised or UTC), columns
        including "Volume". Assumes regular-session bars only (prepost=False).

    Returns dict of {"HH:MM": float} — time_bucket → mean cumulative volume across
    the most recent lookback_days trading sessions.

    Algorithm:
      1. Localise index to ET if needed.
      2. Group by date, compute running cumsum of Volume within each session.
      3. For each time bucket (HH:MM), collect the session-end cumsum values.
      4. Average across the most recent lookback_days sessions.
    """
    import pandas as pd
    from bot.safety.et_helpers import ET_TZ  # or pytz.timezone("America/New_York")

    df = daily_frame_5m.copy()
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(ET_TZ)
    elif str(df.index.tzinfo) != str(ET_TZ):
        df.index = df.index.tz_convert(ET_TZ)

    df["date"] = df.index.date
    df["bucket"] = df.index.strftime("%H:%M")

    # Compute running cumulative volume within each session
    df["cum_vol"] = df.groupby("date")["Volume"].cumsum()

    # Pivot: for each (date, bucket) take the last cum_vol (= cumulative up to that bucket)
    pivot = df.groupby(["date", "bucket"])["cum_vol"].last().unstack(level="bucket")

    # Use only the most recent lookback_days sessions
    recent = pivot.tail(lookback_days)

    return recent.mean().to_dict()  # {"HH:MM": mean_cum_vol}
```

**Call site — after the per-candidate daily-bar processing block, copy the `rvol_baseline_val` write-to-store pattern:**
```python
# Compute and store TOD baselines (SIG-RVOL-TOD)
tod_baselines = _compute_tod_baselines(frame_5m, lookback_days=cfg.rvol_lookback_days)
if tod_baselines:
    store.upsert_tod_baselines(scan_date_str, code, tod_baselines)
```

---

### `bot/position/manager.py` — add broker stop placement + trail-sync cancel-replace

**Analog:** `_trigger_breakeven` (lines 651–669) and `_trigger_stop_out` (lines 671–738) — the FSM transition pattern

**Existing `_trigger_breakeven` FSM transition pattern** (lines 651–669):
```python
def _trigger_breakeven(self, pos: PositionState, time_key: str) -> None:
    pos.updated_at = now_et()
    self._persist_position(pos, event="breakeven")
    _logger.info("fsm_breakeven", code=pos.code, trail_stop=pos.trail_stop, time_key=time_key)
```

**New async `_place_broker_stop` method — modelled on `_trigger_stop_out` async pattern:**
```python
async def _place_broker_stop(self, pos: PositionState) -> None:
    """Place a broker-side protective stop order and record the order_id on pos.

    Called after entry fill (AWAITING_FILL → ACTIVE) when use_broker_stop_orders=True.
    Uses gateway.place_stop_order(). Persists broker_stop_order_id DB-first (D-07).
    No-ops when gateway is None (test/offline context).

    Pitfall 2 (RESEARCH): never call before entry fill is confirmed — only call
    from _on_entry_fill() or equivalent ACTIVE-entry hook.
    """
    if self._engine is None:
        return
    from moomoo import TrdSide  # deferred import
    try:
        order_id = await self._gateway.place_stop_order(
            code=pos.code,
            qty=pos.remaining_quantity,
            stop_price=pos.trail_stop,
            trd_side=TrdSide.SELL,   # long-position protective stop (Pitfall 7)
        )
        pos.broker_stop_order_id = order_id
        self._persist_position(pos, event="broker_stop_placed")
        _logger.info("broker_stop_placed", code=pos.code, stop_price=pos.trail_stop, order_id=order_id)
    except Exception:
        _logger.warning("broker_stop_placement_failed", code=pos.code, exc_info=True)
```

**New async `_sync_broker_stop` method (D-04 trail-sync — cancel-replace):**
```python
async def _sync_broker_stop(self, pos: PositionState) -> None:
    """Cancel-replace the broker stop to mirror the updated trail_stop (D-04).

    Pattern: cancel existing stop → re-query dealt_qty (finding-1.4) → place new stop.
    The brief no-stop window is an accepted cost (D-04). The bar-close FSM's stop
    check remains the redundant backstop (D-03).
    """
    if not getattr(pos, "broker_stop_order_id", None) or self._engine is None:
        return
    old_id = pos.broker_stop_order_id
    try:
        await self._gateway.cancel_order(old_id)
    except Exception:
        pass  # already cancelled or filled — proceed to re-place

    # Finding-1.4: re-query dealt_qty post-cancel before placing replacement
    # (mirrors execution/engine.py cancel + dealt_qty re-query pattern)
    await self._place_broker_stop(pos)  # places new stop at current trail_stop
```

**Finding-1.4 cancel + dealt_qty re-query pattern from `execution/engine.py`** (lines 274–310):
```python
# After cancel:
try:
    await self._gw.cancel_order(order_id)
except Exception:
    pass  # remainder may already be fully filled — swallow
# Then re-query:
total_filled = int(row.get("dealt_qty", 0) or 0)
```

---

### `bot/config/loader.py` — add new `StrategyConfig` fields

**Analog:** existing field additions at lines 96–134 — copy the flat-field + Optional pattern

**Existing field pattern for optional risk keys** (lines 131–134):
```python
# ---- risk (optional / 260702-ick RISK-01) ----
sizing_equity_usd: Optional[float] = 100_000.0
```

**New fields to add to `StrategyConfig` dataclass** — following the same grouped + comment convention:
```python
# ---- risk (Phase 7 additions) ----
daily_circuit_breaker_r: float = 2.0      # risk.daily_circuit_breaker_r (D-05)

# ---- execution (Phase 7 additions) ----
use_broker_stop_orders: bool = True        # execution.use_broker_stop_orders (D-02 path selector)

# ---- intraday_filters (Phase 7 additions) ----
rvol_tod_lookback_days: int = 14           # intraday_filters.I3_rvol_tod_lookback_days
```

**New loader mapping lines** — copy the pattern at lines 205–209:
```python
sizing_equity_usd=rk.get("sizing_equity_usd", 100_000),
```
Add:
```python
daily_circuit_breaker_r=float(rk.get("daily_circuit_breaker_r", 2.0)),
use_broker_stop_orders=bool(ex_cfg.get("use_broker_stop_orders", True)),
rvol_tod_lookback_days=int(inf.get("I3_rvol_tod_lookback_days", 14)),
```

---

### `rules.json` — add new config keys

**Analog:** existing `risk` and `execution` blocks. Add keys alongside existing siblings.

**New keys to add:**
```json
"risk": {
  "daily_circuit_breaker_r": 2.0
},
"execution": {
  "use_broker_stop_orders": true
},
"intraday_filters": {
  "I3_rvol_tod_lookback_days": 14
}
```

---

## Shared Patterns

### Lock pattern for all new StateStore methods
**Source:** `bot/state/store.py` — `get_rvol_baseline` (lines 601–619)
**Apply to:** `get_tod_baseline`, `upsert_tod_baselines`, `get_circuit_breaker_date`, `set_circuit_breaker_date`, `clear_circuit_breaker`
```python
with self._lock:
    # ... self._conn.execute(...) ...
    self._conn.commit()   # only on writes
```

### Deferred moomoo SDK import pattern
**Source:** `bot/gateway/gateway.py` `place_order` (line 645), `cancel_order` (line 691)
**Apply to:** `place_stop_order` and any new gateway method that uses SDK enums
```python
from moomoo import OrderType  # deferred import — mirrors subscribe() pattern
```

### `run_in_executor` async wrapper pattern
**Source:** `bot/gateway/gateway.py` (lines 647–672)
**Apply to:** `place_stop_order`, any new gateway blocking SDK call
```python
loop = asyncio.get_running_loop()

def _blocking():
    ...

result = await loop.run_in_executor(None, _blocking)
```

### Audit log on every order placement
**Source:** `bot/gateway/gateway.py` `place_order` (lines 662–669)
**Apply to:** `place_stop_order`
```python
from bot.safety.audit_log import append_audit
append_audit({"event": "stop_order_placed", "code": code, ...})
```

### ET date string for all session-scoped keys
**Source:** `bot/signal/signal_engine.py` (line 455), `bot/state/store.py`
**Apply to:** all new `scan_date` / `session_date_str` usages in scanner and signal engine
```python
session_date_str = now_et().date().isoformat()
```

### Callable migration + PRAGMA table_info guard (WR-03)
**Source:** `bot/state/migrations.py` `_migration_0002` (lines 116–126), `_migration_0004` (lines 203–217)
**Apply to:** `_migration_0005` — any new ALTER TABLE column addition
```python
existing = {row[1] for row in conn.execute("PRAGMA table_info(table_name)")}
for col, decl in COLUMNS_TUPLE:
    if col not in existing:
        conn.execute(f"ALTER TABLE table_name ADD COLUMN {col} {decl}")
```

### Logger calls (structured, keyword-only)
**Source:** All existing files — e.g., `signal_engine.py` line 435
**Apply to:** all new methods
```python
_logger.info("event_name", code=code, field=value, reason="...")
_logger.warning("event_name", exc_info=True)
```

---

## No Analog Found

None — all Phase 7 files are extensions of existing modules with well-established patterns.

---

## Metadata

**Analog search scope:** `bot/`, `tests/`
**Files read:** bar_aggregator.py, events.py, signal_engine.py, gateway.py, migrations.py, store.py, fetcher.py, loader.py, manager.py (partial)
**Pattern extraction date:** 2026-07-03
