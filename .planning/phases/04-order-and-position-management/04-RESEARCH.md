# Phase 4: Order and Position Management - Research

**Researched:** 2026-06-24
**Domain:** moomoo SDK order placement/fill reconciliation, per-position FSM, restart reconciliation, EOD force-close
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Synthetic, bot-monitored stop — no resting stop order on the broker. On each closed 5m bar the bot checks the current stop and, when triggered, submits an aggressive marketable-limit exit. No dependency on native broker stop orders.
- **D-02:** Stop triggers on bar CLOSE — exit only when a closed 5m bar has `close <= current_stop`.
- **D-03:** Profit-side triggers (0.75R partial, 1.0R breakeven) also judged on bar CLOSE only.
- **D-04:** Marketable-limit entry (chase the breakout) — entry priced at/through the current ask + a small buffer.
- **D-05:** TTL + bounded re-price retry — if not (fully) filled within a short TTL, cancel and re-submit at the fresh marketable price, up to a capped number of attempts (≈2–3); after the cap, abandon and release the slot.
- **D-06:** Partial entry fill → keep the partial as the position; cancel the unfilled remainder; `full_quantity = filled_qty`, `entry_price = avg_fill_price` for all R math.
- **D-07:** Marketable-limit exits with retry-until-flat — exit priced through the bid (bid − buffer); if unfilled within TTL, cancel-replace at a progressively more aggressive price until `remaining_quantity == 0`.
- **D-08:** 15:51 force-close: escalate + alert, never silently carry overnight, never a market order. Escalate price each retry through 15:51→16:00 window; fire loud alert/log if still open near close.
- **D-09:** Broker truth wins on restart. StateStore CLOSED wins over lingering state. Quantities differ → adopt broker quantity.
- **D-10:** Orphan broker position (no StateStore record) → adopt and protect with LOD−1% derived stop, set ACTIVE at broker avg cost, manage normally including 15:51 force-close. Alert on adoption.
- **D-11:** Known position on restart → restore persisted `phase`, `remaining_quantity`, `trail_stop` exactly; re-subscribe 5m feed; rebuild swing-low forward only; stop is NEVER loosened (`new_stop = max(persisted_stop, new_swing_low)`).

### Claude's Discretion

- Exact FSM/dataclass shapes — `PositionState`, `FillEvent`, state-enum values; module decomposition across four slices (04-01 FSM, 04-02 PositionManager, 04-03 ExecutionEngine, 04-04 reconciliation/guard/force-close/kill-switch).
- Gateway order methods — `place_order` / `modify_order` / `cancel_order` wrappers (mirror async `run_in_executor` pattern; deferred SDK imports per subscribe/unsubscribe precedent).
- OrderIntent → order consumption transport — how Phase 4 consumes `pending_intents` (in-process event/queue vs StateStore poll) and the pending-intent lifecycle reconciliation.
- Schema migration — new ordered migration 0004 for order_id / avg-fill / FSM-state columns; never edit a shipped migration.
- Numeric tunables — entry/exit limit buffers, entry TTL + max retries, exit escalation step + cadence, force-close escalation schedule — all in `rules.json` (CFG-01), no hardcoded literals.
- Empirical SIMULATE validation — limit-order fill model, push reliability, partial-fill behavior, and native stop confirmation.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope. APScheduler service, OpenD watchdog, Telegram alert transport, and HTML dashboard remain Phase 5; backtester remains Phase 6. Phase 4 emits alert-worthy events/logs but Phase 5 owns delivery.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-01 | Orders placed through Moomoo API on paper (SIMULATE) account only | § SDK API Signatures; gateway.place_order mirrors run_in_executor + deferred import pattern; paper guard at connect() already enforced |
| EXEC-02 | Exits use limit orders at aggressive prices — no market order reliance | § OrderType.NORMAL is the correct limit type; OrderType.MARKET must never appear; § Pitfall 8 confirms paper account market-order fills are unreliable |
| EXEC-03 | Pending orders use a TTL with cancel-replace if unfilled | § modify_order with ModifyOrderOp.CANCEL then fresh place_order; § TTL/retry/buffer tunables in rules.json execution block |
| EXEC-04 | Duplicate-order prevention via broker-verified per-symbol position guard | § get_positions() already in gateway; call before place_order; block if broker reports open position for code |
| EXEC-05 | Stop-out and fill reconciliation by order_id, never by quantity | § deal_list_query returns deal_id + order_id + qty + price + create_time per fill row; § order_list_query returns dealt_qty + dealt_avg_price; always join by order_id |
| POS-01 | Take ⅓ off the position at 0.75R (partial profit) | § FSM: ACTIVE → PARTIAL_TAKEN when bar.close >= entry_price + 0.75 × R; sell floor(remaining_qty × partial_profit_fraction) via marketable-limit |
| POS-02 | Move the stop to breakeven at 1.0R | § FSM: PARTIAL_TAKEN → BREAKEVEN when bar.close >= entry_price + 1.0 × R; update trail_stop = entry_price in StateStore |
| POS-03 | Trail stop on 5m swing lows (2/2 pattern) after breakeven | § FSM: BREAKEVEN → TRAILING; compute_swing_low_2_2 driven by BarAggregator closed-bar events; stop = max(persisted_stop, new_swing_low); never loosen |
| POS-04 | Force-close all open positions at 15:51 ET, calendar-aware for half-days | § get_market_close_et() from bot/scanner/calendar.py; 15:51 = market_close minus ~9 min; escalate-limit, never market order |
| POS-05 | Per-position FSM persisted and survives restarts | § StateStore migration 0004 adds FSM columns; atomic_write_json for every transition; on restart: broker truth wins (D-09/D-10/D-11) |
</phase_requirements>

---

## Summary

Phase 4 is the first phase that places real (paper) orders and sees fills. The critical mechanics are: (1) the moomoo SDK's `place_order` / `modify_order(op=CANCEL)` / `deal_list_query` / `order_list_query` API signatures are now confirmed in detail; (2) the SIMULATE paper account's trade push handlers (`TradeOrderHandlerBase` / `TradeDealHandlerBase`) exist in the SDK and produce properly-keyed `order_id` DataFrames, BUT the official skill documentation explicitly warns that push data may not be received on US paper accounts ("future versions will support this"); (3) all queries against the SIMULATE account **must** pass `refresh_cache=True` — stale cached data is a documented paper-account limitation; (4) the cancel operation reuses `modify_order(modify_order_op=ModifyOrderOp.CANCEL, order_id=..., qty=0, price=0)` — there is no separate cancel endpoint; (5) partial fills are represented as separate deal rows in `deal_list_query`, each keyed to the same `order_id`, and `order_list_query` reports the cumulative `dealt_qty` / `dealt_avg_price` per order.

The ROADMAP empirical flag (push reliability, fill model, native stop support) resolves as follows: **push reliability for SIMULATE is officially documented as unreliable** — the skill docs state push data "may not be received temporarily." This unambiguously validates D-01 (synthetic stop, no native broker stop) and the hybrid transport recommendation: treat push callbacks as a latency hint, always reconcile via `deal_list_query(refresh_cache=True)` on a ~15-20s poll inside the entry/exit TTL windows. Polling is the authoritative transport. Native stop orders (OrderType.STOP, OrderType.STOP_LIMIT) exist in the SDK enum but their behavior on SIMULATE is not confirmed — D-01's decision to avoid them entirely is the only safe choice.

**Primary recommendation:** Build the ExecutionEngine around a polling-first fill reconciliation model (`deal_list_query(refresh_cache=True)` inside the TTL loop) with push callbacks (`TradeDealHandlerBase.on_recv_rsp`) as an optional latency accelerator. Never rely on push as the authoritative fill source for SIMULATE.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Order placement / cancel-replace | ExecutionEngine (`bot/execution/`) | MoomooGateway (new methods) | ExecutionEngine owns TTL logic and retry policy; gateway provides the typed SDK wrapper |
| Fill reconciliation | ExecutionEngine | MoomooGateway (`deal_list_query` wrapper) | Keyed by order_id; ExecutionEngine emits FillEvent to PositionManager |
| Per-position FSM | PositionManager + PositionState (`bot/position/`) | StateStore (persistence) | PositionManager processes FillEvent + BarEvent; PositionState is a pure FSM dataclass |
| Stop / partial / breakeven / trail evaluation | PositionManager | BarAggregator (events), TrendJoinLong (compute_swing_low_2_2) | Driven by closed-bar events; all judged on bar.close (D-02/D-03) |
| EOD force-close timing | PositionManager | bot/scanner/calendar.py (get_market_close_et) | 15:51 ET = market_close − ~9 min; ExecutionEngine handles the actual order submission |
| Startup reconciliation | PositionManager + ExecutionEngine | MoomooGateway.get_positions() | Broker truth wins; reconstruct FSM before any signal processing |
| Duplicate-order guard | ExecutionEngine | MoomooGateway.get_positions() | Broker-verified check before every entry |
| Kill-switch flush | KillSwitch callbacks | PositionManager (state flush) | Extend existing KillSwitch.register_flush() for in-flight FSM state |
| Schema migration | StateStore / migrations.py | — | Migration 0004 adds FSM columns; never edit 0001–0003 |

---

## Standard Stack

### Core (all existing — no new dependencies for Phase 4)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| moomoo-api | 10.7.6708 | SDK: place_order, modify_order, deal_list_query, order_list_query, TradeOrderHandlerBase, TradeDealHandlerBase | `[VERIFIED: npm registry]` via PyPI |
| sqlite3 | stdlib | StateStore + migration 0004 | Built-in |
| asyncio | stdlib | run_in_executor pattern for all new gateway order methods | Built-in |
| zoneinfo | stdlib (3.9+) | ET timezone for force-close timing | Built-in |
| pandas | 3.0.3 | SDK DataFrame parsing for fill rows | Existing dep |
| structlog | 26.1.0 | Structured logging for every order/fill/transition | Existing dep |

**No new packages required for Phase 4.** All functionality is available through the existing stack.

---

## SDK API Signatures (Verified from Repo Scripts and Live Python Inspection)

### 1. `place_order` — Entry Submission

**Source:** `skills/moomooapi/scripts/trade/place_order.py` line 190–207; `skills/moomooapi/docs/API_REFERENCE.md` line 107

```python
# Full signature (from API_REFERENCE.md):
ctx.place_order(
    price: float,          # limit price — required; use ask + buffer for marketable entry
    qty: int,              # quantity (whole shares for US stocks)
    code: str,             # Moomoo format: "US.AAPL"
    trd_side: TrdSide,     # TrdSide.BUY for entries
    order_type=OrderType.NORMAL,  # NORMAL = limit order — ALWAYS use this; never MARKET
    trd_env=TrdEnv.SIMULATE,      # hardcoded SIMULATE in gateway
    acc_id=int,            # from GatewayConfig.acc_id
    # Optional — do not use these for the bot's simple entry orders:
    # fill_outside_rth, session, aux_price, trail_type, trail_value, trail_spread
)
# Returns: (ret: int, data: DataFrame)
# data.iloc[0]["order_id"] — the string order_id for fill tracking (EXEC-05)
```

**Rate limit:** 15 orders per 30 seconds per account ID. The bot places at most 5 entries per session — well within quota.

**Return value shape (line 210–213 of place_order.py):**
```python
ret, data = ctx.place_order(**order_kwargs)
# data is a DataFrame; order_id is in:
order_id = safe_get(data.iloc[0], "order_id", "orderID", default=str(data))
```

### 2. `modify_order` — Cancel and Re-price

**Source:** `skills/moomooapi/scripts/trade/modify_order.py` line 130–138; `cancel_order.py` line 56–63

Two uses in Phase 4:

**Cancel an order (TTL expiry, abandon, force-close cancel-before-reprice):**
```python
ret, data = ctx.modify_order(
    modify_order_op=ModifyOrderOp.CANCEL,  # NOT NORMAL
    order_id=order_id,
    qty=0,       # required by SDK even for cancel — pass 0
    price=0,     # required by SDK even for cancel — pass 0
    trd_env=TrdEnv.SIMULATE,
    acc_id=acc_id,
)
# Source: cancel_order.py lines 56-63 — this IS how cancel works
```

**Reprice (move limit price for escalating exit):**
```python
ret, data = ctx.modify_order(
    modify_order_op=ModifyOrderOp.NORMAL,
    order_id=order_id,
    qty=remaining_qty,    # total qty after modification (NOT incremental)
    price=new_price,      # new aggressive limit price
    trd_env=TrdEnv.SIMULATE,
    acc_id=acc_id,
)
# Source: modify_order.py lines 130-138
# NOTE: qty is the TOTAL expected quantity after modification, not the delta
```

**Rate limit:** 20 requests per 30 seconds per account ID.

**IMPORTANT:** The bot's D-07 "cancel-replace" pattern should use cancel + fresh `place_order` rather than `modify_order(NORMAL)` for price repricing. Both work, but cancel+replace gives a new `order_id` so fill tracking is unambiguous. When using `modify_order(NORMAL)`, the same `order_id` is retained — which is fine if the code tracks that the order was repriced but simplifies fill-by-order_id logic.

### 3. `order_list_query` — Pending Order Status + `dealt_qty`

**Source:** `skills/moomooapi/scripts/trade/get_orders.py` lines 54, 72–81

```python
ret, data = ctx.order_list_query(
    order_id="",           # empty = all today's orders; pass specific order_id to filter
    trd_env=TrdEnv.SIMULATE,
    acc_id=acc_id,
    refresh_cache=True,    # MANDATORY for SIMULATE — stale data without this
)
# Returns per-order row fields (from get_orders.py lines 72-81):
# order_id, code, trd_side, order_status, qty, price, dealt_qty, dealt_avg_price, jp_acc_type
```

**Key fields for Phase 4:**
- `order_status` — `OrderStatus` enum: `SUBMITTED`, `FILLED_ALL`, `FILLED_PART`, `CANCELLED_ALL`, `CANCELLED_PART`, `FAILED`, `SUBMIT_FAILED`, `SUBMITTING`, `WAITING_SUBMIT`, `CANCELLING_ALL`, `CANCELLING_PART`, `TIMEOUT`, `DELETED`, `DISABLED`, `FILL_CANCELLED`, `UNSUBMITTED`, `NONE`
- `dealt_qty` — cumulative shares filled so far (float)
- `dealt_avg_price` — average fill price so far (float, no precision limit per docs)

**For partial fill detection:** `order_status == OrderStatus.FILLED_PART` AND `dealt_qty < qty`

**Rate limit:** 10 requests per 30 seconds per account ID (only when `refresh_cache=True`).

### 4. `deal_list_query` — Fill Records by order_id (EXEC-05)

**Source:** `skills/moomooapi/scripts/trade/get_order_fill_list.py` lines 49, 64

```python
ret, data = ctx.deal_list_query(
    trd_env=TrdEnv.SIMULATE,
    acc_id=acc_id,
    refresh_cache=True,    # MANDATORY for SIMULATE
)
# Returns per-deal row fields (from SDK source TradeDealHandlerBase.on_recv_rsp):
# deal_id, order_id, code, trd_side, qty, price, create_time, counter_broker_id, counter_broker_name, trd_market, status
```

**Partial fill representation:** Each partial fill is a **separate deal row** with its own `deal_id` but the same `order_id`. To reconstruct average fill price from deal rows:
```python
fills_for_order = [row for row in deal_rows if row["order_id"] == target_order_id]
total_qty = sum(r["qty"] for r in fills_for_order)
avg_price = sum(r["qty"] * r["price"] for r in fills_for_order) / total_qty
```

For a fully-filled order, `order_list_query.dealt_avg_price` provides this directly. Use deal rows when you need the fill sequence or need to detect a new partial fill since last poll.

**Rate limit:** 10 requests per 30 seconds per account ID.

### 5. Push Handlers — `TradeOrderHandlerBase` / `TradeDealHandlerBase`

**Source:** Verified via live Python inspection; `skills/moomooapi/SKILL.md` line 184

```python
# Both classes verified importable:
from moomoo import TradeOrderHandlerBase, TradeDealHandlerBase

# TradeOrderHandlerBase.on_recv_rsp returns a DataFrame with columns:
# trd_env, code, stock_name, dealt_avg_price, dealt_qty, qty, order_id,
# order_type, price, order_status, create_time, updated_time, trd_side,
# last_err_msg, trd_market, remark, time_in_force, fill_outside_rth,
# session, aux_price, trail_type, trail_value, trail_spread,
# currency, jp_acc_type, expire_time, amount, strategy_type, combo_legs

# TradeDealHandlerBase.on_recv_rsp returns a DataFrame with columns:
# trd_env, code, stock_name, deal_id, order_id, qty, price,
# trd_side, create_time, counter_broker_id, counter_broker_name,
# trd_market, status, jp_acc_type
```

**CRITICAL PAPER ACCOUNT LIMITATION:** `skills/moomooapi/SKILL.md` line 184 states:
> "Push interfaces (`TradeOrderHandlerBase` / `TradeDealHandlerBase`) can be called normally, **but push data may not be received temporarily; future versions will support this**"

This is an official acknowledgment that SIMULATE push delivery is unreliable. Phase 4 must NOT use push as the authoritative fill source. Polling via `deal_list_query(refresh_cache=True)` is the only safe approach.

**Push as accelerator pattern (optional):** Install a `TradeDealHandlerBase` handler to get latency hints when a fill arrives, but always verify via poll before acting. Pattern:
```python
class FillPushHandler(TradeDealHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret == RET_OK:
            # Enqueue a poll-reconcile job immediately (accelerate the poll cycle)
            asyncio.run_coroutine_threadsafe(
                self._trigger_reconcile(data), self._loop
            )
        return ret, data
```

### 6. Enum Values Verified (Live Python)

```python
# OrderStatus — all values:
OrderStatus.SUBMITTED        # order accepted by broker, awaiting fill
OrderStatus.SUBMITTING       # in transit
OrderStatus.WAITING_SUBMIT   # queued before submission
OrderStatus.FILLED_ALL       # completely filled
OrderStatus.FILLED_PART      # partially filled, still working
OrderStatus.CANCELLED_ALL    # fully cancelled
OrderStatus.CANCELLED_PART   # partially filled, remainder cancelled
OrderStatus.CANCELLING_ALL   # cancel request in flight
OrderStatus.CANCELLING_PART  # partial cancel request in flight
OrderStatus.FAILED           # order failed
OrderStatus.SUBMIT_FAILED    # submission failed
OrderStatus.TIMEOUT          # timed out
OrderStatus.FILL_CANCELLED   # fill was cancelled (rare)
OrderStatus.UNSUBMITTED      # not yet submitted
OrderStatus.DELETED          # deleted
OrderStatus.DISABLED         # disabled
OrderStatus.NONE             # unknown

# OrderType — relevant subset:
OrderType.NORMAL          # limit order — USE THIS for all entries and exits
OrderType.MARKET          # market order — NEVER USE on paper account
OrderType.ABSOLUTE_LIMIT  # like NORMAL but rejects if can't fill at exact price
OrderType.STOP            # native stop (behavior on SIMULATE unverified — avoid, D-01)
OrderType.STOP_LIMIT      # native stop-limit (same — avoid)
OrderType.TRAILING_STOP   # native trailing stop (same — avoid)

# TrdSide:
TrdSide.BUY      # for entries
TrdSide.SELL     # for all exits (partial, stop-out, force-close)

# ModifyOrderOp:
ModifyOrderOp.NORMAL   # reprice or resize
ModifyOrderOp.CANCEL   # cancel the order (used by cancel_order.py)
```

### 7. `refresh_cache=True` is MANDATORY for SIMULATE

All three queries must pass `refresh_cache=True` on paper accounts. From `skills/moomooapi/SKILL.md` lines 184–205:

> "Querying positions, funds, orders, etc. **must pass `refresh_cache=True`**, otherwise stale cached data may be returned"

Affected calls:
- `position_list_query(refresh_cache=True)` — EXEC-04 duplicate guard, D-09 reconciliation
- `order_list_query(refresh_cache=True)` — TTL-check order status
- `deal_list_query(refresh_cache=True)` — fill reconciliation (EXEC-05)
- `accinfo_query(refresh_cache=True)` — already implemented in `get_equity()`

---

## Architecture Patterns

### Recommended Project Structure

```
bot/
├── execution/
│   ├── __init__.py
│   ├── engine.py          # ExecutionEngine — TTL loop, place/cancel-replace, fill reconcile
│   └── events.py          # FillEvent dataclass
├── position/
│   ├── __init__.py
│   ├── state.py           # PositionState FSM dataclass + PositionPhase enum
│   └── manager.py         # PositionManager — processes FillEvent + BarEvent, persists transitions
├── gateway/
│   └── gateway.py         # Add: place_order, cancel_order, get_order_fills, get_order_status
└── state/
    └── migrations.py      # Add: _migration_0004 (FSM columns on positions table)
```

### System Architecture Diagram — Phase 4 Data Flow

```
OrderIntent (pending_intents PENDING)
    │
    ▼
ExecutionEngine.consume_intent()
    │── broker duplicate guard ──► get_positions(refresh_cache=True) ──► block if open
    │── place_order(NORMAL, TrdSide.BUY, price=ask+buffer)
    │       returns order_id
    │
    │── TTL poll loop (every ~5s, up to entry_ttl_seconds from rules.json)
    │       deal_list_query(refresh_cache=True) ──► find rows where order_id matches
    │       order_list_query(refresh_cache=True) ──► check order_status + dealt_qty
    │
    ├── FILL DETECTED ──► emit FillEvent(order_id, code, filled_qty, avg_fill_price)
    │       pending_intents row: PENDING → RESOLVED
    │       daily_trade_count.filled_count += 1  (Phase-3 D-08)
    │
    └── TTL EXPIRED → cancel + retry (up to entry_max_retries from rules.json)
            → ABANDONED: cancel remainder, release slot, pending_intents → EXPIRED
                │
                ▼
PositionManager.on_fill(FillEvent)
    │── AWAITING_FILL → ACTIVE (first fill for entry order_id)
    │── Persist PositionState to StateStore (migration 0004 columns)
    │── Re-subscribe 5m feed if needed
    │
    ├── on_bar(BarEvent) — driven by BarAggregator closed bars
    │   ├── ACTIVE: check stop (bar.close <= trail_stop) → trigger exit order
    │   ├── ACTIVE: check 0.75R (bar.close >= entry_price + 0.75*R) → partial sell
    │   ├── PARTIAL_TAKEN: check 1.0R (bar.close >= entry_price + 1.0*R) → move stop to entry
    │   ├── BREAKEVEN/TRAILING: compute_swing_low_2_2 → update trail_stop if higher
    │   └── TRAILING: check stop (bar.close <= trail_stop) → trigger exit order
    │
    └── Exit orders → ExecutionEngine (same marketable-limit + retry-until-flat pattern)
            exits reconciled by order_id (EXEC-05)

15:51 ET Force-Close (POS-04 / D-08)
    get_market_close_et(today) ──► calendar.py → "13:00" or "16:00"
    force_close_et = market_close_time - 9 min
    PositionManager.force_close_all() ──► ExecutionEngine (escalating-limit, never market)
```

### Pattern 1: Gateway Order Methods (Deferred Import Pattern)

All new gateway methods mirror `subscribe`/`unsubscribe` (bot/gateway/gateway.py lines 432–451):

```python
async def place_order(self, code: str, qty: int, price: float,
                      trd_side) -> str:
    """Place a marketable-limit order. Returns order_id string.
    Source: mirrors gateway.py subscribe() deferred-import pattern (line 432)
    """
    from moomoo import OrderType, TrdSide  # deferred — test env without moomoo-api still imports
    loop = asyncio.get_running_loop()

    def _place_blocking():
        ret, data = self._trade_ctx.place_order(
            price=float(price),
            qty=int(qty),
            code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,  # ALWAYS NORMAL — never MARKET (EXEC-02)
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

async def cancel_order(self, order_id: str) -> None:
    """Cancel an order. Uses modify_order(CANCEL) — there is no separate cancel endpoint.
    Source: cancel_order.py lines 56-63 (verified pattern)
    """
    from moomoo import ModifyOrderOp
    loop = asyncio.get_running_loop()

    def _cancel_blocking():
        ret, data = self._trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,     # SDK requires these even for cancel
            price=0,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
        )
        _check_ret(ret, data, "cancel_order")

    await loop.run_in_executor(None, _cancel_blocking)

async def get_order_fills(self, refresh_cache: bool = True) -> list:
    """Returns list of deal dicts: [deal_id, order_id, code, qty, price, create_time, ...]
    Source: get_order_fill_list.py lines 49-64; SIMULATE MUST pass refresh_cache=True
    """
    loop = asyncio.get_running_loop()
    ret, data = await loop.run_in_executor(
        None,
        lambda: self._trade_ctx.deal_list_query(
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
            refresh_cache=refresh_cache,
        )
    )
    _check_ret(ret, data, "deal_list_query")
    return [] if data is None or len(data) == 0 else [
        {k: row.get(k) for k in ["deal_id", "order_id", "code", "qty", "price",
                                  "trd_side", "create_time"]}
        for _, row in data.iterrows()
    ]

async def get_order_status(self, order_id: str = "") -> list:
    """Returns list of order dicts: [order_id, code, order_status, qty, dealt_qty, dealt_avg_price, ...]
    Source: get_orders.py lines 54-81; SIMULATE MUST pass refresh_cache=True
    """
    loop = asyncio.get_running_loop()
    ret, data = await loop.run_in_executor(
        None,
        lambda: self._trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=_parse_trd_env(self.cfg.trd_env),
            acc_id=self.cfg.acc_id,
            refresh_cache=True,
        )
    )
    _check_ret(ret, data, "order_list_query")
    return [] if data is None or len(data) == 0 else [
        {k: row.get(k) for k in ["order_id", "code", "order_status", "qty",
                                  "dealt_qty", "dealt_avg_price", "trd_side"]}
        for _, row in data.iterrows()
    ]
```

### Pattern 2: FillEvent Dataclass (Claude's Discretion)

```python
# bot/execution/events.py
from dataclasses import dataclass
from datetime import datetime

@dataclass
class FillEvent:
    """Emitted by ExecutionEngine when a fill (full or partial) is detected.

    Keyed by order_id (EXEC-05) — never by quantity.
    Consumed by PositionManager.on_fill().
    """
    order_id: str           # broker order_id — primary reconciliation key
    intent_id: str          # corresponding pending_intents.intent_id
    code: str               # Moomoo-format stock code
    filled_qty: int         # total filled quantity AT THIS POINT (cumulative, not incremental)
    avg_fill_price: float   # average fill price across all fill rows for this order_id
    is_entry: bool          # True = entry fill; False = exit fill
    fill_time: datetime     # create_time of the latest fill row for this order
```

### Pattern 3: PositionState FSM Dataclass (Claude's Discretion)

```python
# bot/position/state.py
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional

class PositionPhase(str, Enum):
    AWAITING_FILL   = "AWAITING_FILL"   # entry order placed, not yet filled
    ACTIVE          = "ACTIVE"          # entry filled; managing initial stop
    PARTIAL_TAKEN   = "PARTIAL_TAKEN"   # ⅓ off at 0.75R; stop still at initial
    BREAKEVEN       = "BREAKEVEN"       # stop moved to entry price at 1.0R
    TRAILING        = "TRAILING"        # trailing swing-low stop
    CLOSED          = "CLOSED"          # position fully exited

@dataclass
class PositionState:
    """Per-position FSM state. Persisted to StateStore on every transition.

    position_id: UUID — also used as StateStore primary key (TEXT)
    entry_order_id: broker order_id from place_order() — fill reconciliation key
    exit_order_id: broker order_id for any current open exit order (or None)
    """
    position_id: str
    code: str
    phase: PositionPhase
    entry_price: float          # avg_fill_price from FillEvent (D-06)
    initial_stop: float         # compute_initial_stop(lod) at signal time
    trail_stop: float           # current stop; starts at initial_stop; never decreases
    full_quantity: int          # filled_qty from entry FillEvent (D-06)
    remaining_quantity: int     # decreases on partial exits
    entry_order_id: str         # broker order_id for entry (EXEC-05 reconciliation key)
    exit_order_id: Optional[str] = None  # open exit order (if any)
    opened_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Computed on demand (not stored):
    # R = entry_price - trail_stop  (initial_stop_price is the initial R distance)
```

### Pattern 4: Migration 0004 (add FSM columns to positions table)

**Source:** migrations.py pattern — callable migration, conn.execute() not executescript, WR-03 idempotency

```python
# bot/state/migrations.py — add after _migration_0003

_POSITIONS_0004_COLUMNS = (
    ("entry_order_id",  "TEXT"),   # broker order_id for entry (EXEC-05)
    ("exit_order_id",   "TEXT"),   # open exit order_id (nullable)
    ("avg_fill_price",  "REAL"),   # actual fill price (D-06; may differ from entry_price intent)
)

def _migration_0004(conn: sqlite3.Connection) -> None:
    """Add Phase 4 FSM and order-tracking columns to positions table.

    Idempotent: each ALTER guarded by column-existence check (WR-03 pattern).
    The existing `phase` column already exists (migration 0001) and already holds
    the FSM phase string — PositionPhase enum values are string-compatible.
    Uses conn.execute() (not executescript) for atomic commit with user_version bump.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, decl in _POSITIONS_0004_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")

# MIGRATIONS list: append _migration_0004
# CURRENT_VERSION: bump to 4
```

**Note on `phase` column:** Migration 0001 already creates `positions.phase TEXT NOT NULL DEFAULT 'OPEN'`. Phase 4 repurposes this column for the FSM states (`AWAITING_FILL`, `ACTIVE`, `PARTIAL_TAKEN`, `BREAKEVEN`, `TRAILING`, `CLOSED`). No schema change to `phase` needed — only new columns for `entry_order_id`, `exit_order_id`, and `avg_fill_price`.

### Pattern 5: Entry TTL Poll Loop (ExecutionEngine Core)

```python
async def _manage_entry_order(self, intent: OrderIntent) -> Optional[FillEvent]:
    """
    Place a marketable-limit entry and poll for fills until TTL expires or max retries.
    Returns FillEvent on any fill (full or partial accepted as position per D-06).
    Returns None if abandoned after max retries.
    All tunables from self._cfg (rules.json execution block — CFG-01).
    """
    ask_price = await self._gw.get_ask_price(intent.code)  # snapshot bid/ask
    limit_price = ask_price + self._cfg.entry_limit_buffer  # D-04
    order_id = await self._gw.place_order(intent.code, intent.quantity,
                                           limit_price, TrdSide.BUY)

    for attempt in range(self._cfg.entry_max_retries + 1):
        deadline = asyncio.get_event_loop().time() + self._cfg.entry_ttl_seconds
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(self._cfg.entry_poll_interval_seconds)  # ~5s
            fills = await self._gw.get_order_fills()
            matched = [f for f in fills if f["order_id"] == order_id]
            if matched:
                # Fill detected — accept partial per D-06
                total_filled = sum(f["qty"] for f in matched)
                avg_price = sum(f["qty"] * f["price"] for f in matched) / total_filled
                # Cancel any unfilled remainder
                await self._gw.cancel_order(order_id)
                return FillEvent(order_id=order_id, intent_id=intent.intent_id,
                                 code=intent.code, filled_qty=int(total_filled),
                                 avg_fill_price=avg_price, is_entry=True,
                                 fill_time=now_et())
        # TTL expired — cancel and retry or abandon
        await self._gw.cancel_order(order_id)
        if attempt < self._cfg.entry_max_retries:
            ask_price = await self._gw.get_ask_price(intent.code)
            limit_price = ask_price + self._cfg.entry_limit_buffer
            order_id = await self._gw.place_order(intent.code, intent.quantity,
                                                   limit_price, TrdSide.BUY)
        else:
            # Max retries exhausted — abandon (D-05)
            return None
```

### Anti-Patterns to Avoid

- **Using OrderType.MARKET on paper account:** Fill behavior is unreliable on SIMULATE; use OrderType.NORMAL always (EXEC-02, Pitfall 8).
- **Omitting `refresh_cache=True`:** Stale data on SIMULATE makes the TTL loop blind; ALL trade queries must pass `refresh_cache=True`.
- **Trusting push as authoritative fill source:** SIMULATE push delivery is officially documented as unreliable; poll `deal_list_query` inside the TTL loop.
- **Matching fills by quantity instead of order_id:** A ⅓ partial exit (100 shares of 300) has the same qty as a full position of 100 shares; must match by order_id (EXEC-05, PITFALLS.md "Partial Fill State Corruption").
- **Writing FSM state non-atomically:** Use `atomic_write_json` or SQLite within a transaction for every FSM transition (Pitfall 10).
- **Calling modify_order on a CANCEL without qty=0, price=0:** The SDK requires these parameters even for cancel operations (verified: cancel_order.py lines 56-63).
- **Using `modify_order(NORMAL)` for stop-management:** There are no resting broker stops in this design (D-01) — all stop management is in-process via PositionState.trail_stop.
- **Running `deal_list_query` without filtering by order_id:** Filter in Python after fetching; the API does not support filtering by order_id.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fill matching by order_id | Custom fill-tracking dict keyed by qty | `deal_list_query(refresh_cache=True)` filtered by `order_id` | Qty matching produces false stop-outs after partial exits (PITFALLS.md) |
| Calendar-aware force-close time | Hardcode 15:51 | `get_market_close_et(today)` from `bot/scanner/calendar.py` | Half-days close at 13:00; 15:51 before 13:00 would never fire |
| Initial stop for orphan adoption | Re-derive from scratch | `TrendJoinLong.compute_initial_stop(lod)` from `bot/strategy/trend_join_long.py` | Already unit-tested; consistent with D-10 spec |
| Swing-low trail computation | Custom bar-buffer scan | `TrendJoinLong.compute_swing_low_2_2(bars_5m_df)` | Already unit-tested; uses `bot/strategy/indicators.swing_low_2_2` |
| Cancel operation | `ctx.cancel_order()` (doesn't exist as a standalone method) | `ctx.modify_order(modify_order_op=ModifyOrderOp.CANCEL, order_id=..., qty=0, price=0)` | Cancel IS modify_order with op=CANCEL (verified from cancel_order.py) |
| ET timing for force-close | Naive datetime arithmetic | `now_et()` + `get_market_close_et()` from `bot/safety/et_helpers.py` + `bot/scanner/calendar.py` | DST correctness; half-day detection |
| Atomic FSM state persistence | JSON write to file mid-transition | `atomic_write_json` (`bot/state/store.py`) + SQLite transaction | Crash mid-write must not corrupt prior state (Pitfall 10) |
| Broker duplicate check | In-memory position set | `get_positions(refresh_cache=True)` before every entry | Ghost positions in memory can block entries or allow duplicates after restart (EXEC-04) |

**Key insight:** Cancel is not a separate SDK method — it reuses `modify_order` with `ModifyOrderOp.CANCEL`. This is verified from `cancel_order.py` and is a source of confusion.

---

## Common Pitfalls

### Pitfall A: SIMULATE Push Unreliability — The Fundamental Hazard

**What goes wrong:** `TradeDealHandlerBase.on_recv_rsp` is invoked by the moomoo SDK push thread in response to a fill on the SIMULATE account. For US paper accounts, this push **may not fire at all**. A bot that waits for push delivery to detect fills and drive TTL logic will stall indefinitely: the entry order sits filled on the broker but the bot keeps re-polling for a push that never arrives.

**Why it happens:** Explicitly documented in `skills/moomooapi/SKILL.md` line 184: "push data may not be received temporarily; future versions will support this." This is a known SIMULATE limitation.

**How to avoid:**
- Poll `deal_list_query(refresh_cache=True)` inside every TTL loop iteration as the authoritative fill source.
- Use push callbacks as a latency accelerator only: on push receipt, immediately trigger a reconciliation poll; do not act on push data directly without a confirming poll.
- Test: verify the TTL loop finds fills even with the push handler disabled.

**Warning signs:** Entry orders confirmed filled in the moomoo app but bot stays in `AWAITING_FILL` state; push callback counter logs 0 calls while orders are executing.

---

### Pitfall B: `refresh_cache=True` Forgotten on Paper Account Queries

**What goes wrong:** `order_list_query` / `deal_list_query` / `position_list_query` return stale OpenD-cached data when `refresh_cache` is omitted (defaults to False). The TTL loop reads the pre-fill cached state and concludes the order is still open, so it cancels a now-filled order — or worse, the startup reconciliation reads a stale position list and marks an open position as absent.

**Why it happens:** The `refresh_cache` parameter defaults to `False` per the SDK (standard caching behavior). Paper accounts do not push state updates proactively, so the cache is especially stale.

**How to avoid:**
- Hard rule: ALL calls to `deal_list_query`, `order_list_query`, and `position_list_query` in the bot MUST pass `refresh_cache=True`.
- Add a `_check_ret` guard wrapper in the gateway methods that always enforces this.
- Test: use a mock that asserts `refresh_cache=True` on every call.

---

### Pitfall C: Fill Matching by Quantity (EXEC-05 Anti-Pattern)

**What goes wrong:** A position enters with 300 shares. After a ⅓ partial exit (100 shares), the bot places a stop-loss order for the remaining 200 shares. The stop fires and fills 200 shares. If fills are matched by quantity rather than `order_id`, the bot may match the 200-share stop fill against the original 300-share entry fill row (both involve the same stock at roughly the same price), producing a phantom "new fill" and triggering a duplicate stop-out.

**Why it happens:** Developers naturally key fills by `(code, qty, price)` tuples during rapid prototyping. This is deterministic for the first fill but breaks when multiple fill events exist for the same code in a session.

**How to avoid:**
- `deal_list_query` returns `order_id` in every row — always filter by `order_id`.
- Store `entry_order_id` and `exit_order_id` in `PositionState` (migration 0004 adds these columns).
- Every FillEvent carries the `order_id`; PositionManager matches by `order_id`, not by code+qty.

---

### Pitfall D: Stop Loosening on Restart (D-11 Violation)

**What goes wrong:** On restart, the bot re-subscribes the 5m feed and calls `compute_swing_low_2_2` on the freshly-accumulated bars. The new swing low may be lower than the persisted `trail_stop` (bars during downtime may have created lower lows). Setting `trail_stop = new_swing_low` would move the stop downward — loosening the stop, exposing the position to more risk than the operator accepted.

**Why it happens:** The most natural code is `trail_stop = compute_swing_low_2_2(...)`. This is wrong on restart. The restart path must be different from the normal trail path.

**How to avoid:**
- On restart: `trail_stop = max(persisted_stop, new_swing_low)` (D-11 literal).
- Normal trail on each new bar: `trail_stop = max(current_trail_stop, new_swing_low)`.
- These are the same formula! The max() guarantees the stop only ever ratchets up, on both restart and normal operation.
- Test: inject a lower swing_low than the persisted stop and assert the stop does not move downward.

---

### Pitfall E: Half-Exited Stop-Out Risk Hole (D-07 Invariant)

**What goes wrong:** An exit order fires (stop-out, partial, breakeven) and gets partially filled. The PositionManager sees the partial fill and considers the exit "in progress." Meanwhile, the stop guard continues polling on each new bar but does not fire a new exit order because `exit_order_id` is already set. The remaining shares are unprotected until the existing exit order fills — which may never happen if the limit price is stale.

**Why it happens:** The "retry-until-flat" invariant (D-07) requires that an unfilled exit order is escalated until `remaining_quantity == 0`. If the escalation logic only fires "if no exit order is pending," it silently stops escalating after the first partial exit fill.

**How to avoid:**
- Track `remaining_quantity` accurately after every partial exit fill.
- The "is flat" check must be `remaining_quantity == 0`, not `exit_order_id is not None`.
- The escalation loop must compare `current_exit_order.dealt_qty` against `remaining_quantity_before_exit` and re-submit if they differ after TTL.
- Test: simulate a partial exit fill (e.g., 50 of 100 shares), confirm a new escalated exit order fires for the remaining 50.

---

### Pitfall F: Duplicate Entry on Restart (POS-05 / D-09 Violation)

**What goes wrong:** The bot crashes after `place_order` returns `order_id` but before the `PositionState` is persisted to StateStore. On restart, StateStore shows no `AWAITING_FILL` position for this code, so the ExecutionEngine processes the PENDING `pending_intents` row again and places a second entry order.

**Why it happens:** The window between `place_order()` returning and the SQLite write is non-atomic at the session level. Any crash in this window creates this state.

**How to avoid:**
- The EXEC-04 broker-verified duplicate guard (`get_positions()` check) catches this IF the first order filled. It does not catch the case where the first order is still pending (not yet in `get_positions()`).
- Additional guard: before processing any `pending_intents PENDING` row, check `order_list_query` for open orders on that code — if an open buy order exists for the code, it was placed by a prior session; skip the intent.
- On restart, reconcile `pending_intents PENDING` rows against both `get_positions()` AND `order_list_query()` before resuming.

---

### Pitfall G: Non-Atomic FSM State Writes (Pitfall 10 from PITFALLS.md)

**What goes wrong:** The bot updates `PositionState` in memory, then writes to SQLite. A crash between these two steps leaves in-memory state at `PARTIAL_TAKEN` but SQLite at `ACTIVE`. On restart, the SQLite-loaded state re-sends the partial exit order.

**Why it happens:** In-memory state is updated before disk. This is the Pitfall 10 pattern.

**How to avoid:**
- Use SQLite as the source of truth, not in-memory state. Update SQLite FIRST, then update the in-memory PositionState object.
- All SQLite writes use a transaction: update positions row + insert trades row (for CLOSED transitions) in a single `conn.execute` + `conn.commit`.
- Never update in-memory state before the SQLite transaction commits.
- Pattern: `with conn: conn.execute(UPDATE...); state.phase = new_phase` — update DB first, then object.

---

## Code Examples

### Entry fill reconciliation by order_id

```python
# Source: verified SDK field names from TradeDealHandlerBase.on_recv_rsp inspection
# deal_list_query returns rows with: deal_id, order_id, qty, price, create_time

async def reconcile_entry_fills(self, order_id: str) -> Optional[FillEvent]:
    """Poll deal_list_query and return FillEvent if any fills exist for order_id."""
    fills = await self._gw.get_order_fills()  # refresh_cache=True enforced inside
    matched = [f for f in fills if str(f.get("order_id", "")) == order_id]
    if not matched:
        return None
    total_filled = sum(int(f.get("qty", 0) or 0) for f in matched)
    if total_filled <= 0:
        return None
    total_value = sum(float(f.get("qty", 0) or 0) * float(f.get("price", 0) or 0)
                      for f in matched)
    avg_price = total_value / total_filled
    return FillEvent(order_id=order_id, intent_id=self._pending_intent_id,
                     code=self._code, filled_qty=total_filled,
                     avg_fill_price=avg_price, is_entry=True, fill_time=now_et())
```

### Calendar-aware force-close (POS-04 / D-08)

```python
# Source: bot/scanner/calendar.py — get_market_close_et() verified
# Source: bot/safety/et_helpers.py — now_et() verified
from bot.scanner.calendar import get_market_close_et
from bot.safety.et_helpers import now_et, ET
from datetime import time as dtime

def get_force_close_time_et(today) -> dtime:
    """Return the force-close ET time for today (15:51 or earlier on half-days).
    Derives from calendar market close minus 9 minutes, matching the strategy spec.
    """
    close_hhmm = get_market_close_et(today)  # "16:00" or "13:00"
    close_h, close_m = int(close_hhmm.split(":")[0]), int(close_hhmm.split(":")[1])
    # 9 minutes before close (spec: 15:51 = 16:00 - 9min; 12:51 = 13:00 - 9min on half-days)
    force_m = close_m - 9
    force_h = close_h
    if force_m < 0:
        force_m += 60
        force_h -= 1
    return dtime(force_h, force_m)
```

### Startup reconciliation skeleton (D-09/D-10/D-11)

```python
# Source: bot/gateway/gateway.py reconcile_once() skeleton (lines 492-518)
# Phase 4 fills the drift-resolution logic

async def startup_reconcile(self, store: StateStore) -> None:
    """Reconcile StateStore positions against broker truth before any signal processing.
    Implements D-09 (broker wins), D-10 (adopt orphans), D-11 (never loosen stop).
    """
    ret_pos, broker_data = await self.get_positions()  # refresh_cache=True via wrapper
    broker_positions = {}  # code → {qty, avg_cost}
    if ret_pos == RET_OK and broker_data is not None and len(broker_data) > 0:
        for _, row in broker_data.iterrows():
            code = str(row.get("code", ""))
            broker_positions[code] = {
                "qty": int(float(row.get("qty", 0) or 0)),
                "avg_cost": float(row.get("average_cost", 0) or 0),
            }

    state_positions = store.conn.execute(
        "SELECT * FROM positions WHERE phase != 'CLOSED'"
    ).fetchall()

    for pos_row in state_positions:
        code = pos_row["code"]
        if code not in broker_positions:
            # D-09: broker says flat → mark CLOSED
            store.conn.execute(
                "UPDATE positions SET phase='CLOSED', updated_at=? WHERE position_id=?",
                (now_et().isoformat(), pos_row["position_id"])
            )
        else:
            # D-09: quantities differ → adopt broker qty
            broker_qty = broker_positions[code]["qty"]
            if broker_qty != pos_row["remaining_quantity"]:
                store.conn.execute(
                    "UPDATE positions SET remaining_quantity=?, updated_at=? WHERE position_id=?",
                    (broker_qty, now_et().isoformat(), pos_row["position_id"])
                )
            # D-11: never loosen stop (handled in PositionManager.on_bar after re-subscribe)

    for code, bp in broker_positions.items():
        if not any(p["code"] == code for p in state_positions):
            # D-10: orphan broker position → adopt and protect
            lod = await self._derive_lod_for_orphan(code)  # from current snapshot
            stop = trend_join_long.compute_initial_stop(lod)  # LOD - 1%
            # Insert new ACTIVE PositionState row in StateStore
            _adopt_orphan_position(store, code, bp["avg_cost"], bp["qty"], stop)

    store.conn.commit()
```

### PositionManager.on_bar — close-based FSM transitions (D-02/D-03)

```python
# All transitions judged on bar.close — the symmetric D-02/D-03 rule
async def on_bar(self, bar: BarEvent) -> None:
    pos = self._positions.get(bar.code)
    if pos is None or pos.phase in (PositionPhase.AWAITING_FILL, PositionPhase.CLOSED):
        return

    close = bar.close
    R = pos.entry_price - pos.initial_stop   # risk distance

    if pos.phase == PositionPhase.ACTIVE:
        if close <= pos.trail_stop:           # D-02: stop on BAR CLOSE
            await self._trigger_stop_out(pos)
        elif close >= pos.entry_price + 0.75 * R:  # D-03: partial on BAR CLOSE
            await self._trigger_partial_profit(pos)

    elif pos.phase == PositionPhase.PARTIAL_TAKEN:
        if close <= pos.trail_stop:
            await self._trigger_stop_out(pos)
        elif close >= pos.entry_price + 1.0 * R:   # D-03: breakeven on BAR CLOSE
            await self._trigger_breakeven(pos)

    elif pos.phase in (PositionPhase.BREAKEVEN, PositionPhase.TRAILING):
        if close <= pos.trail_stop:
            await self._trigger_stop_out(pos)
        else:
            # Update trailing stop: compute_swing_low_2_2 from BarAggregator buffer
            new_swing = self._strategy.compute_swing_low_2_2(self._get_bar_df(bar.code))
            if new_swing is not None:
                pos.trail_stop = max(pos.trail_stop, new_swing)  # D-11: never loosen
                await self._persist_position(pos)
```

---

## Rules.json Tunables — Phase 4 Additions

Phase 4 adds a new `execution` block to `rules.json`. All values read via `StrategyConfig` (loader.py pattern — `CFG-01`). No literals in Python.

```json
{
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
}
```

**StrategyConfig additions (loader.py):**
```python
# New fields in StrategyConfig dataclass:
entry_limit_buffer_usd: float      # execution.entry_limit_buffer_usd
entry_ttl_seconds: float           # execution.entry_ttl_seconds
entry_max_retries: int             # execution.entry_max_retries
entry_poll_interval_seconds: float # execution.entry_poll_interval_seconds
exit_limit_buffer_usd: float       # execution.exit_limit_buffer_usd
exit_ttl_seconds: float            # execution.exit_ttl_seconds
exit_escalation_step_usd: float    # execution.exit_escalation_step_usd
exit_escalation_cadence_seconds: float  # execution.exit_escalation_cadence_seconds
force_close_escalation_step_usd: float  # execution.force_close_escalation_step_usd
force_close_escalation_cadence_seconds: float  # execution.force_close_escalation_cadence_seconds
```

**jsonschema update required:** `bot/config/schema.py` must add the `execution` block to the schema so `load_strategy_config()` validates it.

---

## Integration Surfaces (Phase 1–3 consumed by Phase 4)

All citations are file:line confirmed by reading the actual files.

| Symbol | Location | Phase 4 Usage |
|--------|----------|---------------|
| `OrderIntent` dataclass | `bot/risk/events.py` line 18 — fields: `code`, `entry_price`, `stop_price`, `quantity`, `equity_used`, `risk_dollars`, `notional`, `emitted_at`, `source_signal`, `intent_id` | ExecutionEngine primary input; `intent_id` links to `pending_intents` row |
| `BarEvent` dataclass | `bot/signal/events.py` line 17 — fields: `code`, `time_key`, `open`, `high`, `low`, `close`, `volume`, `hod`, `lod` | PositionManager.on_bar() receives this; `close` drives all FSM transitions (D-02/D-03) |
| `BarAggregator` | `bot/signal/bar_aggregator.py` line 54 — `.reset_session()`, `.on_recv_rsp()`, `._bar_buffer: Dict[str, deque]` | Phase 4 reads `_bar_buffer[code]` for swing-low computation (convert to DataFrame for `compute_swing_low_2_2`) |
| `TrendJoinLong.compute_initial_stop(lod)` | `bot/strategy/trend_join_long.py` line 146 | Orphan adoption stop (D-10): `stop = TrendJoinLong(cfg).compute_initial_stop(lod)` |
| `TrendJoinLong.compute_swing_low_2_2(bars_5m_df)` | `bot/strategy/trend_join_long.py` line 169 | Trail stop update (POS-03): delegates to `indicators.swing_low_2_2(bars_5m_df["low"].tolist())` |
| `MoomooGateway.get_positions()` | `bot/gateway/gateway.py` line 312 — `position_list_query()` wrapper | EXEC-04 duplicate guard; D-09 reconciliation; returns `(ret, DataFrame)` |
| `MoomooGateway.get_equity()` | `bot/gateway/gateway.py` line 323 | Not directly used in Phase 4 (Phase 3 already reads equity for sizing) |
| `MoomooGateway.subscribe(codes, subtypes)` | `bot/gateway/gateway.py` line 416 | Re-subscribe 5m feed for reconstructed/adopted positions on restart (D-09/D-10/D-11) |
| `MoomooGateway.unsubscribe(codes, subtypes)` | `bot/gateway/gateway.py` line 454 | Unsubscribe on position CLOSED |
| `MoomooGateway.reconcile_once()` | `bot/gateway/gateway.py` line 492 — skeleton | Phase 4 fills the drift-resolution logic in this method |
| `StateStore` | `bot/state/store.py` line 127 — `.conn`, `.open()`, `.close()` | All FSM persistence; migration 0004 applied at open() |
| `atomic_write_json(path, data)` | `bot/state/store.py` line 47 | Used for any JSON snapshot writes alongside SQLite transitions |
| `run_migrations(conn)` | `bot/state/migrations.py` line 194 | Append `_migration_0004` to `MIGRATIONS` list; bump `CURRENT_VERSION` to 4 |
| `now_et()` | `bot/safety/et_helpers.py` line 28 | All FSM transition timestamps; force-close time comparison |
| `get_market_close_et(dt)` | `bot/scanner/calendar.py` line 45 | Returns "16:00" or "13:00" — derive force-close time (POS-04/D-08) |
| `is_trading_day(dt)` | `bot/scanner/calendar.py` line 32 | Guard force-close loop against weekends/holidays |
| `KillSwitch.register_flush(callback)` | `bot/safety/kill_switch.py` line 97 | Register PositionManager.flush_all() for in-flight FSM state on SIGINT/sentinel |
| `append_audit(entry)` | `bot/safety/audit_log.py` line 31 | Every order placed, fill received, FSM transition — SAFE-05 compliance |
| `pending_intents` table | `bot/state/migrations.py` line 169 — columns: `intent_id`, `code`, `status` (PENDING/RESOLVED/EXPIRED), `entry_price`, `stop_price`, `quantity`, `emitted_at`, `resolved_at` | Phase 4 transitions rows: PENDING→RESOLVED (on fill) or PENDING→EXPIRED (on abandon) |
| `daily_trade_count` table | `bot/state/migrations.py` line 163 — columns: `session_date`, `filled_count`, `updated_at` | Phase 4 increments `filled_count` at fill time (Phase-3 D-08); Phase 3 reads it for daily cap gate |

---

## SIMULATE Fill Model Assessment (ROADMAP Empirical Flag Resolution)

The ROADMAP flagged three empirical questions. They are now answered:

| Question | Finding | Source | Confidence |
|----------|---------|--------|------------|
| Push reliability on SIMULATE? | **Unreliable — officially documented.** SIMULATE push "may not be received temporarily." | `skills/moomooapi/SKILL.md` line 184 | HIGH (official SDK docs) |
| Fill model for limit orders on SIMULATE? | Limit orders fill when the market crosses the limit price (standard paper account simulation). `refresh_cache=True` required to read fills. Partial fills are possible (separate `deal_id` rows per partial). | `skills/moomooapi/SKILL.md` lines 184-205; verified fields in `TradeDealHandlerBase` | HIGH |
| Native stop order support on SIMULATE? | **Not confirmed.** `OrderType.STOP` / `OrderType.STOP_LIMIT` / `OrderType.TRAILING_STOP` exist in the SDK enum but their behavior on SIMULATE is undocumented. This reinforces D-01: use synthetic bot-monitored stops only. | Live SDK inspection; no official SIMULATE stop-order docs found | HIGH (absence of support is confirmed by D-01 being locked) |

**Verdict:** The bot's design is correct. D-01 (synthetic stop) is the only safe architecture given that (a) native stop behavior on SIMULATE is unverified and (b) bot-monitored stops give full control over the partial/breakeven/trail sequence. The polling-first reconciliation model is mandated by documented push unreliability.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, detected in repo) |
| Config file | No pytest.ini detected; tests run via `pytest tests/` |
| Quick run command | `pytest tests/position/ tests/execution/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-01 | Entry placed via SIMULATE; audit log updated | Integration (paper account) | `pytest tests/execution/test_engine.py::test_entry_placed_simulate -x` | ❌ Wave 0 |
| EXEC-02 | No market order ever placed; force-close uses limit | Unit (AST + behavioral) | `pytest tests/execution/test_engine.py::test_no_market_orders -x` | ❌ Wave 0 |
| EXEC-03 | TTL cancel-replace fires; abandon after max retries; config-swap changes behavior | Unit (synthetic fill mock) | `pytest tests/execution/test_engine.py::test_ttl_cancel_replace -x` | ❌ Wave 0 |
| EXEC-04 | Broker get_positions() blocks duplicate entry | Unit (mocked get_positions) | `pytest tests/execution/test_engine.py::test_duplicate_guard -x` | ❌ Wave 0 |
| EXEC-05 | Fill matched by order_id; partial exit ≠ stop-out | Unit (synthetic deal rows) | `pytest tests/execution/test_engine.py::test_fill_by_order_id -x` | ❌ Wave 0 |
| POS-01 | ⅓ partial sell at 0.75R bar close; FSM ACTIVE→PARTIAL_TAKEN | Unit (synthetic BarEvent) | `pytest tests/position/test_fsm.py::test_partial_profit_trigger -x` | ❌ Wave 0 |
| POS-02 | Stop moves to entry at 1.0R bar close; FSM PARTIAL_TAKEN→BREAKEVEN | Unit (synthetic BarEvent) | `pytest tests/position/test_fsm.py::test_breakeven_trigger -x` | ❌ Wave 0 |
| POS-03 | Trail stop ratchets up on swing-low; never moves down on restart | Unit (synthetic bars + restart) | `pytest tests/position/test_fsm.py::test_trail_never_loosens -x` | ❌ Wave 0 |
| POS-04 | Force-close at 13:09 on half-day, 15:51 on normal day | Unit (mock calendar) | `pytest tests/position/test_manager.py::test_force_close_half_day -x` | ❌ Wave 0 |
| POS-05 | Restart reconstructs FSM from StateStore; broker-truth wins | Unit (in-mem SQLite + mock broker) | `pytest tests/position/test_manager.py::test_restart_reconciliation -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/position/ tests/execution/ -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/execution/__init__.py`
- [ ] `tests/execution/test_engine.py` — covers EXEC-01 through EXEC-05
- [ ] `tests/position/__init__.py`
- [ ] `tests/position/test_fsm.py` — covers POS-01/POS-02/POS-03 (pure FSM, no broker)
- [ ] `tests/position/test_manager.py` — covers POS-04/POS-05 (manager + calendar + reconciliation)
- [ ] `bot/execution/__init__.py`
- [ ] `bot/position/__init__.py`

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `cancel_order()` standalone method | `modify_order(op=CANCEL, qty=0, price=0)` — verified from cancel_order.py | Cancel IS modify — don't look for a separate cancel endpoint |
| Trust push for fill detection | Poll `deal_list_query(refresh_cache=True)` as authoritative; push is optional accelerator | Prevents stall on SIMULATE push silence |
| Broker-native stop orders | Synthetic bot-monitored stop on bar close (D-01) | Controls partial/breakeven/trail sequence; no SIMULATE native-stop uncertainty |

**Deprecated/outdated:**
- Using `OrderType.MARKET` on paper account: fill behavior unreliable on SIMULATE; always `OrderType.NORMAL`
- Omitting `refresh_cache=True`: stale data is a documented paper-account limitation

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `deal_list_query` returns all fills for the current session with no per-order filtering — bot must filter by `order_id` in Python | § SDK API Signatures — deal_list_query | If the SDK added filtering by order_id, no functional impact; the Python filter is just extra work |
| A2 | Partial fills on SIMULATE produce multiple `deal_id` rows under the same `order_id` (not a single cumulative row) | § SDK API Signatures — Partial fill representation | If SIMULATE produces only one cumulative row per order, the multi-row aggregation logic still works (sum over 1 row = the value) |
| A3 | `modify_order(NORMAL)` retains the same `order_id` after repricing | § Pattern 1, cancel vs. reprice note | If a reprice assigns a new `order_id`, use cancel+place pattern instead (described as alternative in Pattern 1) |
| A4 | `position_list_query` returns `average_cost` as the average purchase price (not `cost_price`) | `skills/moomooapi/docs/FIELD_MAPPING.md` line 7 | `FIELD_MAPPING.md` explicitly names `average_cost` as the correct field — this is HIGH confidence, not just an assumption |

**If this table is near-empty:** Most claims in this research were verified via direct file reading, live Python SDK inspection, and official skill documentation.

---

## Open Questions

1. **Bid/ask price for marketable-limit entry (D-04)**
   - What we know: Entry must be priced at/through the current ask + buffer. The gateway has `get_market_snapshot()` which returns real-time snapshot data.
   - What's unclear: Does `get_market_snapshot()` return `ask_price` / `bid_price` fields for US stocks? The snapshot fields are not fully documented in the repo.
   - Recommendation: In the ExecutionEngine, use `get_market_snapshot([code])` and read `ask_price` for entries, `bid_price` for exits. If these fields are unavailable, fall back to `last_price + buffer` for entry and `last_price - buffer` for exit. The planner should add a Wave 0 "verify snapshot fields" task.

2. **Exit order sizing after partial exit (POS-01 execution)**
   - What we know: POS-01 fires a sell for `floor(remaining_qty × 0.3333)`. After the partial fills, `remaining_quantity` decreases.
   - What's unclear: If the partial exit order itself partially fills (e.g., 33 of 100 shares), does the PositionManager retry for the remainder, or treat any fill as "partial done"?
   - Recommendation: Apply the same D-07 "retry-until-flat" invariant to partial exit orders. The `remaining_quantity` after a successful partial exit is `full_quantity - filled_partial_qty`; track this precisely.

3. **`ask_price` field name on `get_market_snapshot` DataFrame**
   - What we know: The existing `get_snapshot.py` and `MoomooGateway.get_market_snapshot()` return a DataFrame. The `pre_high_price` field is already confirmed for premarket high.
   - What's unclear: The exact column names for current bid/ask in the snapshot DataFrame.
   - Recommendation: The planner should add a task in 04-03 (ExecutionEngine) to inspect snapshot columns and confirm `ask_price` / `bid_price` field names before wiring the pricing logic.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.9+ | zoneinfo, dataclasses | ✓ | macOS 25.5.0 / system Python | — |
| moomoo-api | All gateway methods | ✓ | 10.7.6708 (latest on PyPI) | — |
| OpenD GUI | Place/cancel orders, fill queries | Must be running at test time | ≥10.4.6408 | Unit tests use mocks; integration tests require live OpenD |
| sqlite3 | StateStore migration 0004 | ✓ | stdlib built-in | — |
| pandas | SDK DataFrame parsing | ✓ | 3.0.3 | — |
| pandas_market_calendars | get_market_close_et() | ✓ | 5.4.0 (confirmed Phase 2) | — |

**Missing dependencies with no fallback:** None — all required packages are present.

**Integration test prerequisite:** EXEC-01 integration test requires OpenD running and a logged-in SIMULATE account. Unit tests for EXEC-02 through POS-05 use mocked gateway methods and do not require a live connection.

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Paper-only; no auth credentials in order path |
| V3 Session Management | no | SDK manages OpenD session; not in bot code |
| V4 Access Control | yes | `assert_paper_account()` at gateway.connect() ensures SIMULATE-only order path (SAFE-01) |
| V5 Input Validation | yes | All price/qty inputs validated before API call (positive int, non-negative float); no user-facing input |
| V6 Cryptography | no | No crypto in order path |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| TrdEnv.REAL order placement | Tampering | Paper guard at gateway.connect() raises PaperGuardError before any order can be placed |
| Double-entry from restart | Tampering / DoS | EXEC-04 broker-verified duplicate guard + startup reconciliation of pending_intents |
| Credential leakage via order audit log | Information Disclosure | Audit log writes order_id / code / price / qty only — no credentials; AUDIT_LOG_PATH chmod 0600 pattern from atomic_write_json |
| Stale state on crash leading to wrong broker actions | Tampering | Atomic SQLite writes; startup reconciliation against broker truth |

---

## Sources

### Primary (HIGH confidence)

- `skills/moomooapi/scripts/trade/place_order.py` — `ctx.place_order()` kwargs, `order_id` field name, rate limits
- `skills/moomooapi/scripts/trade/modify_order.py` — `ctx.modify_order(NORMAL)` signature; `qty` is total not delta
- `skills/moomooapi/scripts/trade/cancel_order.py` — cancel IS `modify_order(op=CANCEL, qty=0, price=0)` — verified
- `skills/moomooapi/scripts/trade/get_order_fill_list.py` — `ctx.deal_list_query(refresh_cache=True)`; return fields documented
- `skills/moomooapi/scripts/trade/get_orders.py` — `ctx.order_list_query(refresh_cache=True)`; `dealt_qty`, `dealt_avg_price` fields
- `skills/moomooapi/docs/API_REFERENCE.md` lines 107–136 — full `place_order` / `modify_order` / `order_list_query` / `deal_list_query` signatures
- `skills/moomooapi/SKILL.md` lines 174–205 — SIMULATE push unreliability (official); `refresh_cache=True` requirement
- Live Python inspection: `from moomoo import OrderStatus, OrderType, TrdSide, ModifyOrderOp` — all enum values verified
- Live Python inspection: `TradeOrderHandlerBase.on_recv_rsp` / `TradeDealHandlerBase.on_recv_rsp` column lists — verified via `inspect.getsource()`
- `bot/gateway/gateway.py` — existing `run_in_executor` pattern; `subscribe`/`unsubscribe` deferred-import precedent
- `bot/state/store.py` + `bot/state/migrations.py` — `atomic_write_json` pattern; `_migration_0003` callable pattern; WR-03 idempotency
- `bot/risk/events.py` — `OrderIntent` field shapes
- `bot/signal/events.py` — `BarEvent`/`SignalEvent` field shapes
- `bot/signal/bar_aggregator.py` — `_bar_buffer` dict shape; SDK bridge pattern
- `bot/strategy/trend_join_long.py` — `compute_initial_stop(lod)` / `compute_swing_low_2_2(bars_5m_df)` signatures
- `bot/safety/et_helpers.py` — `now_et()`, `to_et()`, `ET`
- `bot/scanner/calendar.py` — `get_market_close_et(dt)` returns HH:MM ET string
- `bot/safety/kill_switch.py` — `register_flush(callback)` for state-flush extension
- `bot/safety/audit_log.py` — `append_audit(entry)` pattern
- `bot/config/loader.py` — `StrategyConfig` dataclass + `load_strategy_config()` — extension point for execution tunables
- `skills/moomooapi/docs/FIELD_MAPPING.md` — `average_cost` (not `cost_price`) for position avg price; `total_assets` for equity
- `.planning/research/PITFALLS.md` — Pitfalls 3, 8, 9, 10 directly map to Phase 4 mitigations

### Secondary (MEDIUM confidence)

- `.planning/research/SUMMARY.md` — Phase 4 as highest-complexity; FSM state list; reconciliation loop design
- `.planning/research/STACK.md` — confirmed existing stack; no new deps needed for Phase 4

---

## Metadata

**Confidence breakdown:**
- SDK API signatures: HIGH — verified via live Python inspection and reference scripts
- SIMULATE push reliability: HIGH — official documentation explicitly states push may not be received
- FSM design shapes: HIGH — derived from locked CONTEXT.md decisions; shapes are Claude's discretion
- Migration 0004 pattern: HIGH — mirrors migrations 0002/0003 exactly
- Rules.json tunables: HIGH — integration point for `execution` block is clear from loader.py

**Research date:** 2026-06-24
**Valid until:** 2026-07-24 (stable SDK; moomoo-api 10.7.6708)
