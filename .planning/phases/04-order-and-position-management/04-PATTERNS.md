# Phase 4: Order and Position Management - Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 12 new/modified files across 4 slices
**Analogs found:** 12 / 12

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bot/execution/events.py` | model/event | event-driven | `bot/risk/events.py` | exact |
| `bot/execution/engine.py` | service | event-driven + request-response | `bot/gateway/gateway.py` (async run_in_executor) + `bot/signal/bar_aggregator.py` (loop + handler pattern) | role-match |
| `bot/position/state.py` | model/FSM | event-driven | `bot/risk/events.py` (dataclass) + `bot/signal/events.py` (enum use) | exact |
| `bot/position/manager.py` | service | event-driven | `bot/signal/bar_aggregator.py` (bar-event consumer) | role-match |
| `bot/gateway/gateway.py` (modify) | service | request-response | `bot/gateway/gateway.py` lines 416–486 (`subscribe`/`unsubscribe` deferred-import pattern) | exact |
| `bot/state/migrations.py` (modify) | config/migration | batch | `bot/state/migrations.py` lines 116–177 (`_migration_0002`/`_migration_0003` callable pattern) | exact |
| `bot/state/store.py` (modify) | service | CRUD | `bot/state/store.py` lines 127–227 (StateStore + `atomic_write_json`) | exact |
| `bot/config/loader.py` (modify) | config | transform | `bot/config/loader.py` lines 63–172 (`StrategyConfig` dataclass + loader) | exact |
| `bot/config/schema.py` (modify) | config | transform | `bot/config/schema.py` lines 17–60 (nested object property pattern) | exact |
| `rules.json` (modify) | config | — | `rules.json` (existing execution block extension) | exact |
| `tests/execution/test_engine.py` | test | event-driven | existing `tests/` patterns | role-match |
| `tests/position/test_fsm.py` + `test_manager.py` | test | event-driven | existing `tests/` patterns | role-match |

---

## Pattern Assignments

### `bot/execution/events.py` (model, event-driven)

**Analog:** `bot/risk/events.py` (lines 1–52)

**Imports pattern** (lines 1–14 of analog):
```python
#!/usr/bin/env python3
"""
bot.execution.events — FillEvent dataclass (Phase 4, EXEC-05).
...
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
```

**Core dataclass pattern** (lines 17–51 of analog — copy structure, swap fields):
```python
@dataclass
class FillEvent:
    """Emitted by ExecutionEngine when a fill (full or partial) is detected.

    Keyed by order_id (EXEC-05) — never by quantity.
    Consumed by PositionManager.on_fill().
    """
    order_id: str           # broker order_id — primary reconciliation key
    intent_id: str          # corresponding pending_intents.intent_id
    code: str               # Moomoo-format stock code
    filled_qty: int         # total filled quantity (cumulative)
    avg_fill_price: float   # average fill price across all fill rows for this order_id
    is_entry: bool          # True = entry fill; False = exit fill
    fill_time: datetime     # create_time of the latest fill row for this order
```

**Key differences from OrderIntent:** FillEvent carries `order_id` (the broker key) and `is_entry` flag; no `source_signal` or sizing fields. The `Optional` import from `bot/signal/events.py` line 15 is needed if `exit_order_id` is nullable elsewhere.

---

### `bot/position/state.py` (model/FSM, event-driven)

**Analog:** `bot/signal/events.py` (lines 1–84) for dataclass shape; `bot/risk/events.py` for field annotation style.

**Imports pattern** (copy from `bot/signal/events.py` lines 1–15):
```python
#!/usr/bin/env python3
"""
bot.position.state — PositionState FSM dataclass and PositionPhase enum (Phase 4).
...
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
```

**Enum pattern** — `str, Enum` with string values (copy convention from moomoo SDK enum usage throughout codebase):
```python
class PositionPhase(str, Enum):
    AWAITING_FILL = "AWAITING_FILL"
    ACTIVE        = "ACTIVE"
    PARTIAL_TAKEN = "PARTIAL_TAKEN"
    BREAKEVEN     = "BREAKEVEN"
    TRAILING      = "TRAILING"
    CLOSED        = "CLOSED"
```

**Core dataclass pattern** (mirrors `OrderIntent` at `bot/risk/events.py` lines 17–51):
```python
@dataclass
class PositionState:
    """Per-position FSM state. Persisted to StateStore on every transition.

    entry_order_id: broker order_id from place_order() — fill reconciliation key (EXEC-05).
    exit_order_id: broker order_id for any current open exit order (or None).
    trail_stop never decreases — max(persisted_stop, new_swing_low) on every update (D-11).
    """
    position_id: str
    code: str
    phase: PositionPhase
    entry_price: float
    initial_stop: float
    trail_stop: float
    full_quantity: int
    remaining_quantity: int
    entry_order_id: str
    exit_order_id: Optional[str] = None
    avg_fill_price: Optional[float] = None
    opened_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

---

### `bot/position/manager.py` (service, event-driven)

**Analog:** `bot/signal/bar_aggregator.py` lines 1–99 (bar-event consumer class pattern) + `bot/gateway/gateway.py` lines 223–300 (class init + logger pattern)

**Imports pattern** (lines 1–29 of bar_aggregator.py analog):
```python
#!/usr/bin/env python3
"""
bot.position.manager — PositionManager: processes FillEvent + BarEvent, persists FSM transitions.
...
"""
import asyncio
from typing import Dict, List, Optional

from bot.execution.events import FillEvent
from bot.position.state import PositionPhase, PositionState
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import get_market_close_et
from bot.state.store import StateStore

_logger = get_logger(__name__)
```

**Class init pattern** (mirrors `MoomooGateway.__init__` at `bot/gateway/gateway.py` lines 238–241):
```python
class PositionManager:
    def __init__(self, store: StateStore, engine, cfg) -> None:
        self._store = store
        self._engine = engine   # ExecutionEngine — injected; not imported circularly
        self._cfg = cfg         # StrategyConfig
        self._positions: Dict[str, PositionState] = {}
```

**on_bar close-based FSM transitions** (from RESEARCH.md Pattern PositionManager.on_bar):
```python
async def on_bar(self, bar: "BarEvent") -> None:
    pos = self._positions.get(bar.code)
    if pos is None or pos.phase in (PositionPhase.AWAITING_FILL, PositionPhase.CLOSED):
        return
    close = bar.close
    R = pos.entry_price - pos.initial_stop
    if pos.phase == PositionPhase.ACTIVE:
        if close <= pos.trail_stop:
            await self._trigger_stop_out(pos)
        elif close >= pos.entry_price + 0.75 * R:
            await self._trigger_partial_profit(pos)
    elif pos.phase == PositionPhase.PARTIAL_TAKEN:
        if close <= pos.trail_stop:
            await self._trigger_stop_out(pos)
        elif close >= pos.entry_price + 1.0 * R:
            await self._trigger_breakeven(pos)
    elif pos.phase in (PositionPhase.BREAKEVEN, PositionPhase.TRAILING):
        if close <= pos.trail_stop:
            await self._trigger_stop_out(pos)
        else:
            new_swing = self._strategy.compute_swing_low_2_2(self._get_bar_df(bar.code))
            if new_swing is not None:
                pos.trail_stop = max(pos.trail_stop, new_swing)  # D-11: never loosen
                await self._persist_position(pos)
```

**Persist pattern** — SQLite first, then in-memory (Pitfall G / WR-03):
```python
async def _persist_position(self, pos: PositionState) -> None:
    """Update DB first, then in-memory object (Pitfall G: DB is source of truth)."""
    conn = self._store.conn
    conn.execute(
        """UPDATE positions SET phase=?, trail_stop=?, remaining_quantity=?,
           exit_order_id=?, avg_fill_price=?, updated_at=?
           WHERE position_id=?""",
        (pos.phase.value, pos.trail_stop, pos.remaining_quantity,
         pos.exit_order_id, pos.avg_fill_price,
         now_et().isoformat(), pos.position_id)
    )
    conn.commit()
    append_audit({"event": "fsm_transition", "code": pos.code,
                  "phase": pos.phase.value, "trail_stop": pos.trail_stop})
```

---

### `bot/execution/engine.py` (service, event-driven + request-response)

**Analog:** `bot/gateway/gateway.py` lines 304–545 (async run_in_executor pattern, class structure) + `bot/signal/bar_aggregator.py` (poll loop + asyncio.run_coroutine_threadsafe bridge)

**Imports pattern**:
```python
#!/usr/bin/env python3
"""
bot.execution.engine — ExecutionEngine: OrderIntent → broker order → FillEvent.
...
"""
import asyncio
from typing import Optional

from bot.execution.events import FillEvent
from bot.risk.events import OrderIntent
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger

_logger = get_logger(__name__)
```

**TTL poll loop pattern** (from RESEARCH.md Pattern 5 — entry TTL):
```python
async def _manage_entry_order(self, intent: OrderIntent) -> Optional[FillEvent]:
    """Place a marketable-limit entry; poll for fills until TTL/max retries.
    All tunables from self._cfg (rules.json execution block — CFG-01).
    Returns FillEvent on fill (full or partial per D-06), None if abandoned (D-05).
    """
    # D-04: price at/through current ask + buffer
    ask_price = await self._get_ask_price(intent.code)
    limit_price = ask_price + self._cfg.entry_limit_buffer_usd
    order_id = await self._gw.place_order(
        intent.code, intent.quantity, limit_price, _TrdSide_BUY
    )
    for attempt in range(self._cfg.entry_max_retries + 1):
        deadline = asyncio.get_event_loop().time() + self._cfg.entry_ttl_seconds
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(self._cfg.entry_poll_interval_seconds)
            fills = await self._gw.get_order_fills()
            matched = [f for f in fills if str(f.get("order_id", "")) == order_id]
            if matched:
                total_filled = sum(int(f.get("qty", 0) or 0) for f in matched)
                avg_price = (sum(float(f.get("qty",0))*float(f.get("price",0))
                                 for f in matched) / total_filled)
                await self._gw.cancel_order(order_id)  # cancel unfilled remainder (D-06)
                return FillEvent(order_id=order_id, intent_id=intent.intent_id,
                                 code=intent.code, filled_qty=int(total_filled),
                                 avg_fill_price=avg_price, is_entry=True,
                                 fill_time=now_et())
        await self._gw.cancel_order(order_id)
        if attempt < self._cfg.entry_max_retries:
            ask_price = await self._get_ask_price(intent.code)
            limit_price = ask_price + self._cfg.entry_limit_buffer_usd
            order_id = await self._gw.place_order(
                intent.code, intent.quantity, limit_price, _TrdSide_BUY
            )
        else:
            return None  # abandoned (D-05)
```

**Deferred TrdSide import** (mirrors `bot/gateway/gateway.py` line 433 deferred import pattern):
```python
# Module-level sentinel (resolved lazily to avoid import failure without moomoo-api)
_TrdSide_BUY = None

def _get_trd_side_buy():
    global _TrdSide_BUY
    if _TrdSide_BUY is None:
        from moomoo import TrdSide
        _TrdSide_BUY = TrdSide.BUY
    return _TrdSide_BUY
```

**Audit pattern at every order/fill** (mirrors `bot/safety/audit_log.py` lines 31–50):
```python
append_audit({"event": "place_order", "code": intent.code,
              "order_id": order_id, "price": limit_price, "qty": intent.quantity})
append_audit({"event": "fill_detected", "order_id": order_id,
              "filled_qty": total_filled, "avg_price": avg_price})
```

---

### `bot/gateway/gateway.py` — add `place_order`, `cancel_order`, `get_order_fills`, `get_order_status` (modify)

**Analog:** `bot/gateway/gateway.py` lines 416–486 (`subscribe`/`unsubscribe`) — exact pattern to copy.

**Deferred-import + run_in_executor pattern** (lines 431–451 of existing file):
```python
async def subscribe(self, codes: list, subtypes: list = None) -> None:
    from moomoo import SubType, Session          # ← deferred import (line 433)
    ...
    loop = asyncio.get_running_loop()           # ← line 438
    def _subscribe_blocking():                  # ← nested blocking closure (line 440)
        ret, msg = self._quote_ctx.subscribe(...)
        _check_ret(ret, msg, "subscribe")       # ← _check_ret on every SDK call (line 449)
    await loop.run_in_executor(None, _subscribe_blocking)  # ← line 451
    _logger.info("subscribed_k5m", ...)        # ← structlog after success (line 452)
```

**New `place_order` method** — copy deferred-import + inner closure pattern (RESEARCH.md Pattern 1, lines 381–407):
```python
async def place_order(self, code: str, qty: int, price: float, trd_side) -> str:
    """Place a marketable-limit order. Returns order_id string.
    Uses OrderType.NORMAL always — never MARKET (EXEC-02).
    Deferred import so test env without moomoo-api still imports gateway.
    """
    from moomoo import OrderType                  # deferred
    loop = asyncio.get_running_loop()

    def _place_blocking():
        ret, data = self._trade_ctx.place_order(
            price=float(price), qty=int(qty), code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,          # ALWAYS NORMAL (EXEC-02)
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "place_order")
        from bot.safety.audit_log import append_audit
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        order_id = str(row.get("order_id", "") or row.get("orderID", ""))
        append_audit({"event": "place_order", "code": code, "qty": qty,
                      "price": price, "order_id": order_id})
        return order_id

    return await loop.run_in_executor(None, _place_blocking)
```

**New `cancel_order` method** — cancel IS `modify_order(op=CANCEL)` (verified: `skills/moomooapi/scripts/trade/cancel_order.py` lines 56–63):
```python
async def cancel_order(self, order_id: str) -> None:
    from moomoo import ModifyOrderOp              # deferred
    loop = asyncio.get_running_loop()

    def _cancel_blocking():
        ret, data = self._trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,     # SDK requires these even for cancel (verified: cancel_order.py lines 56-63)
            price=0,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "cancel_order")

    await loop.run_in_executor(None, _cancel_blocking)
```

**New `get_order_fills` method** — lambda form (mirrors `get_equity` at lines 341–348):
```python
async def get_order_fills(self, refresh_cache: bool = True) -> list:
    """deal_list_query — MUST pass refresh_cache=True for SIMULATE (Pitfall B)."""
    loop = asyncio.get_running_loop()
    ret, data = await loop.run_in_executor(
        None,
        lambda: self._trade_ctx.deal_list_query(
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
            refresh_cache=refresh_cache,    # MANDATORY for SIMULATE
        )
    )
    _check_ret(ret, data, "deal_list_query")
    if data is None or len(data) == 0:
        return []
    return [
        {k: row.get(k) for k in ["deal_id", "order_id", "code", "qty",
                                  "price", "trd_side", "create_time"]}
        for _, row in data.iterrows()
    ]
```

**New `get_order_status` method** — same lambda form:
```python
async def get_order_status(self, order_id: str = "") -> list:
    """order_list_query — MUST pass refresh_cache=True for SIMULATE (Pitfall B)."""
    loop = asyncio.get_running_loop()
    ret, data = await loop.run_in_executor(
        None,
        lambda: self._trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
            refresh_cache=True,             # MANDATORY for SIMULATE
        )
    )
    _check_ret(ret, data, "order_list_query")
    if data is None or len(data) == 0:
        return []
    return [
        {k: row.get(k) for k in ["order_id", "code", "order_status", "qty",
                                  "dealt_qty", "dealt_avg_price", "trd_side"]}
        for _, row in data.iterrows()
    ]
```

**Insertion point:** Add all four methods after line 486 (`unsubscribe`), before `reconcile_once` at line 492. Maintain the section comment header style (`# ----`).

---

### `bot/state/migrations.py` — add `_migration_0004` (modify)

**Analog:** `bot/state/migrations.py` lines 107–177 (`_migration_0002` and `_migration_0003`)

**Callable migration pattern** (lines 116–126 for `_migration_0002`):
```python
_DAILY_SCAN_0002_COLUMNS = (
    ("prior_day_high", "REAL"),
    ...
)

def _migration_0002(conn: sqlite3.Connection) -> None:
    """... idempotent (D-08, WR-03)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_scan)")}
    for col, decl in _DAILY_SCAN_0002_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE daily_scan ADD COLUMN {col} {decl}")
```

**Migration 0004 implementation** (copy `_migration_0002` pattern exactly, swap table + columns):
```python
_POSITIONS_0004_COLUMNS = (
    ("entry_order_id", "TEXT"),   # broker order_id for entry (EXEC-05)
    ("exit_order_id",  "TEXT"),   # open exit order_id (nullable)
    ("avg_fill_price", "REAL"),   # actual fill price (D-06)
)

def _migration_0004(conn: sqlite3.Connection) -> None:
    """Add Phase 4 FSM and order-tracking columns to positions table.

    Idempotent: each ALTER guarded by column-existence check (WR-03 pattern).
    The existing `phase` column already exists (migration 0001, line 41) and
    holds FSM phase strings — PositionPhase enum values are string-compatible.
    Uses conn.execute() (not executescript) for atomic commit with user_version
    bump (WR-03 note at lines 143-152).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0004_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
```

**MIGRATIONS list and CURRENT_VERSION** (lines 180–187 of existing file):
```python
# Before (line 180-187):
MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,
    _migration_0003,
]
CURRENT_VERSION = 3

# After:
MIGRATIONS = [
    _MIGRATION_0001,
    _migration_0002,
    _migration_0003,
    _migration_0004,   # Phase 4: add entry_order_id, exit_order_id, avg_fill_price
]
CURRENT_VERSION = 4
```

---

### `bot/state/store.py` — new position accessor methods (modify)

**Analog:** `bot/state/store.py` lines 127–227 (existing `StateStore` class)

**New accessor pattern** — follow the class's existing `conn` property style (lines 165–177); add methods after line 227:
```python
def upsert_position(self, pos: "PositionState") -> None:
    """Insert or replace a PositionState row atomically.
    Called from PositionManager._persist_position — DB written first (Pitfall G).
    """
    self._conn.execute(
        """INSERT INTO positions
           (position_id, code, phase, entry_price, initial_stop, trail_stop,
            full_quantity, remaining_quantity, entry_order_id, exit_order_id,
            avg_fill_price, opened_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(position_id) DO UPDATE SET
             phase=excluded.phase, trail_stop=excluded.trail_stop,
             remaining_quantity=excluded.remaining_quantity,
             exit_order_id=excluded.exit_order_id,
             avg_fill_price=excluded.avg_fill_price,
             updated_at=excluded.updated_at""",
        (pos.position_id, pos.code, pos.phase.value, pos.entry_price,
         pos.initial_stop, pos.trail_stop, pos.full_quantity,
         pos.remaining_quantity, pos.entry_order_id, pos.exit_order_id,
         pos.avg_fill_price,
         pos.opened_at.isoformat() if pos.opened_at else None,
         pos.updated_at.isoformat() if pos.updated_at else None)
    )
    self._conn.commit()

def get_open_positions(self) -> list:
    """Return all non-CLOSED position rows as dicts (for startup reconciliation)."""
    self._conn.row_factory = sqlite3.Row
    rows = self._conn.execute(
        "SELECT * FROM positions WHERE phase != 'CLOSED'"
    ).fetchall()
    return [dict(r) for r in rows]
```

---

### `bot/config/loader.py` — add `execution` block to `StrategyConfig` (modify)

**Analog:** `bot/config/loader.py` lines 63–172 (existing `StrategyConfig` and `load_strategy_config`)

**StrategyConfig extension** — append new fields after line 99 (`max_trades_per_day`):
```python
    # ---- execution (Phase 4 tunables — CFG-01, D-05/D-07/D-08) ----
    entry_limit_buffer_usd: float       # execution.entry_limit_buffer_usd
    entry_ttl_seconds: float            # execution.entry_ttl_seconds
    entry_max_retries: int              # execution.entry_max_retries
    entry_poll_interval_seconds: float  # execution.entry_poll_interval_seconds
    exit_limit_buffer_usd: float        # execution.exit_limit_buffer_usd
    exit_ttl_seconds: float             # execution.exit_ttl_seconds
    exit_escalation_step_usd: float     # execution.exit_escalation_step_usd
    exit_escalation_cadence_seconds: float  # execution.exit_escalation_cadence_seconds
    force_close_escalation_step_usd: float          # execution.force_close_escalation_step_usd
    force_close_escalation_cadence_seconds: float   # execution.force_close_escalation_cadence_seconds
```

**Loader extension** — add `execution` block mapping after line 162 (inside `load_strategy_config`), copying the exact `float()`/`int()` cast pattern at lines 151–171:
```python
    ex_cfg = data.get("execution", {})
    # (then pass fields into StrategyConfig constructor)
    entry_limit_buffer_usd=float(ex_cfg["entry_limit_buffer_usd"]),
    entry_ttl_seconds=float(ex_cfg["entry_ttl_seconds"]),
    entry_max_retries=int(ex_cfg["entry_max_retries"]),
    entry_poll_interval_seconds=float(ex_cfg["entry_poll_interval_seconds"]),
    exit_limit_buffer_usd=float(ex_cfg["exit_limit_buffer_usd"]),
    exit_ttl_seconds=float(ex_cfg["exit_ttl_seconds"]),
    exit_escalation_step_usd=float(ex_cfg["exit_escalation_step_usd"]),
    exit_escalation_cadence_seconds=float(ex_cfg["exit_escalation_cadence_seconds"]),
    force_close_escalation_step_usd=float(ex_cfg["force_close_escalation_step_usd"]),
    force_close_escalation_cadence_seconds=float(ex_cfg["force_close_escalation_cadence_seconds"]),
```

---

### `bot/config/schema.py` — add `execution` block (modify)

**Analog:** `bot/config/schema.py` lines 17–60 (existing nested object property blocks)

**Schema extension** — add to `SCHEMA["properties"]` after the existing `risk` block, following the exact nested-object pattern:
```python
"execution": {
    "type": "object",
    "required": [
        "entry_limit_buffer_usd", "entry_ttl_seconds", "entry_max_retries",
        "entry_poll_interval_seconds", "exit_limit_buffer_usd", "exit_ttl_seconds",
        "exit_escalation_step_usd", "exit_escalation_cadence_seconds",
        "force_close_escalation_step_usd", "force_close_escalation_cadence_seconds"
    ],
    "properties": {
        "entry_limit_buffer_usd":              {"type": "number"},
        "entry_ttl_seconds":                   {"type": "number"},
        "entry_max_retries":                   {"type": "number"},
        "entry_poll_interval_seconds":         {"type": "number"},
        "exit_limit_buffer_usd":               {"type": "number"},
        "exit_ttl_seconds":                    {"type": "number"},
        "exit_escalation_step_usd":            {"type": "number"},
        "exit_escalation_cadence_seconds":     {"type": "number"},
        "force_close_escalation_step_usd":     {"type": "number"},
        "force_close_escalation_cadence_seconds": {"type": "number"}
    }
},
```

Also add `"execution"` to `SCHEMA["required"]` list (line 19).

---

### `rules.json` — add `execution` block (modify)

**Analog:** Existing `rules.json` nested block structure (all blocks use JSON object + numeric values)

**New block** (RESEARCH.md §Rules.json Tunables — Phase 4 Additions):
```json
"execution": {
    "entry_limit_buffer_usd": 0.05,
    "entry_ttl_seconds": 20,
    "entry_max_retries": 2,
    "entry_poll_interval_seconds": 5,
    "exit_limit_buffer_usd": 0.05,
    "exit_ttl_seconds": 15,
    "exit_escalation_step_usd": 0.10,
    "exit_escalation_cadence_seconds": 10,
    "force_close_escalation_step_usd": 0.20,
    "force_close_escalation_cadence_seconds": 15
}
```

---

### `tests/execution/test_engine.py` + `tests/position/test_fsm.py` + `tests/position/test_manager.py` (test)

**Analog:** Existing test files in `tests/` (same pytest conventions used throughout)

**Test file pattern** — copy shebang, module docstring, and import style from any existing test file:
```python
#!/usr/bin/env python3
"""
tests.execution.test_engine — Unit tests for ExecutionEngine (EXEC-01..05).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
```

**Config swap pattern** (RESEARCH.md §Established Patterns — config-drivenness proven behaviorally):
```python
def _make_cfg(**overrides):
    """Build a minimal StrategyConfig with execution defaults, overridable per test."""
    base = {...defaults...}
    base.update(overrides)
    return base
```

**In-memory SQLite for state tests** — use `StateStore(db_path=":memory:")` (mirrors the `BOT_STATE_DB` env var override pattern from `bot/state/store.py` line 40).

---

## Shared Patterns

### Structlog Usage
**Source:** `bot/safety/logger.py` lines 161–170; `bot/gateway/gateway.py` line 44
**Apply to:** `bot/execution/engine.py`, `bot/position/manager.py`
```python
from bot.safety.logger import get_logger
_logger = get_logger(__name__)

# Usage:
_logger.info("event_name", key=value, ...)
_logger.warning("event_name", key=value, exc_info=True)
_logger.error("event_name", exc_info=True)
```

### Audit Log on Every Order/Fill/Transition
**Source:** `bot/safety/audit_log.py` lines 31–50
**Apply to:** `bot/execution/engine.py` (every `place_order`, fill detected, order cancelled), `bot/position/manager.py` (every FSM transition), `bot/gateway/gateway.py` new methods
```python
from bot.safety.audit_log import append_audit
append_audit({"event": "place_order", "code": code, "order_id": order_id,
              "qty": qty, "price": price})
# append_audit is silent-on-failure (try/except pass at line 43) — never blocks execution
```

### ET Timestamps
**Source:** `bot/safety/et_helpers.py` — `now_et()`
**Apply to:** All `updated_at`, `opened_at`, `fill_time` fields; force-close time comparison
```python
from bot.safety.et_helpers import now_et
ts = now_et().isoformat()
```

### Calendar-Aware Force-Close Time
**Source:** `bot/scanner/calendar.py` — `get_market_close_et(dt)` returns `"16:00"` or `"13:00"`
**Apply to:** `bot/position/manager.py` `force_close_all()` / `get_force_close_time_et()`
```python
from bot.scanner.calendar import get_market_close_et
close_hhmm = get_market_close_et(today)  # "16:00" normal day, "13:00" half-day
# force_close = market_close - 9 min  (15:51 or 12:51)
```

### Kill-Switch Flush Registration
**Source:** `bot/safety/kill_switch.py` lines 97–106 (`register_flush`)
**Apply to:** `bot/position/manager.py` — register `flush_all()` in main.py startup (04-04)
```python
ks.register_flush(position_manager.flush_all)
# flush_all must be zero-argument callable; persists all in-flight FSM state (SAFE-04)
```

### `refresh_cache=True` Mandatory on Paper Account
**Source:** RESEARCH.md §SDK API Signatures §7; `bot/gateway/gateway.py` line 347
**Apply to:** All new gateway methods: `get_order_fills`, `get_order_status`, and the existing `get_positions` call in startup reconciliation (must add `refresh_cache=True` if not already present)
```python
# MANDATORY: omitting refresh_cache returns stale OpenD-cached data on SIMULATE (Pitfall B)
self._trade_ctx.deal_list_query(..., refresh_cache=True)
self._trade_ctx.order_list_query(..., refresh_cache=True)
self._trade_ctx.position_list_query(refresh_cache=True)  # also in startup reconcile
```

### Never-Loosen-Stop Invariant
**Source:** RESEARCH.md Pitfall D; CONTEXT.md D-11
**Apply to:** `bot/position/manager.py` trail update AND startup reconciliation
```python
# On every trail update AND on restart:
pos.trail_stop = max(pos.trail_stop, new_swing_low)
# The max() is the same formula for both paths — D-11 literal
```

### DB-First State Write
**Source:** RESEARCH.md Pitfall G; `bot/state/store.py` design
**Apply to:** `bot/position/manager.py` every `_persist_position` call, every transition
```python
# Pattern: UPDATE SQLite FIRST, then update Python object
conn.execute("UPDATE positions SET phase=? ... WHERE position_id=?", (...))
conn.commit()
pos.phase = new_phase   # only AFTER commit
```

---

## No Analog Found

None — all Phase 4 files have close analogs in the existing codebase.

---

## Metadata

**Analog search scope:**
- `bot/gateway/gateway.py` (545 lines — read in full)
- `bot/risk/events.py` (52 lines — read in full)
- `bot/signal/events.py` (84 lines — read in full)
- `bot/state/migrations.py` (228 lines — read in full)
- `bot/state/store.py` (227 lines — read in full)
- `bot/safety/audit_log.py` (51 lines — read in full)
- `bot/safety/kill_switch.py` (188 lines — read in full)
- `bot/config/loader.py` (173 lines — read in full)
- `bot/config/schema.py` (first 60 lines)
- `bot/signal/bar_aggregator.py` (first 100 lines)
- `bot/safety/logger.py` (171 lines — read in full)

**Files scanned:** 11 source files
**Pattern extraction date:** 2026-06-24
