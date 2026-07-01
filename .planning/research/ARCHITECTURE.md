# Architecture Research

**Domain:** Automated intraday trading bot — single-strategy, long-running supervised service
**Researched:** 2026-06-23
**Confidence:** HIGH (core patterns verified against freqtrade, QuantStart event-driven design, moomoo API docs)

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         TradingBot (Orchestrator)                        │
│   Main process · owns all components · drives the daily schedule         │
├────────────┬───────────────┬──────────────┬──────────────┬───────────────┤
│  Scanner   │ SignalEngine  │  RiskEngine  │  Execution   │PositionManager│
│ (premarket)│  (5m loop)    │  (sizing)    │  (orders)    │ (per-position │
│            │               │              │              │  state FSM)   │
├────────────┴───────────────┴──────────────┴──────────────┴───────────────┤
│                         BarAggregator                                    │
│         Wraps moomoo CurKlineHandlerBase · K_5M push → on_bar_close()   │
├──────────────────────────────────────────────────────────────────────────┤
│                         MoomooGateway                                    │
│   Thin wrapper over skills/moomooapi · quote_ctx + trade_ctx lifecycle   │
├──────────────────────────────────────────────────────────────────────────┤
│               OpenD  127.0.0.1:11111  (external, pre-running)           │
└──────────────────────────────────────────────────────────────────────────┘
                                    │ alerts (fire-and-forget)
                                    ▼
                          TelegramAlerter (async)
                                    │
                          Telegram Bot API

┌──────────────────────────────────────────────────────────────────────────┐
│                     StateStore  (SQLite via sqlite3)                     │
│  positions table · trades table · daily_scan table · bar_cache table    │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                     Backtester  (offline, same process or CLI)          │
│  SimulatedBarFeed → StrategyCore (shared ABC) → SimulatedExecution      │
│  → PositionManager (same class as live) → TradeLog                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Boundary — What It Does NOT Do |
|-----------|---------------|-------------------------------|
| **TradingBot** | Daily schedule (premarket → intraday loop → EOD close), lifecycle start/stop, wires components together | No strategy logic, no order calls directly |
| **Scanner** | Premarket: fetch S&P 500 constituent list; apply D1/D2/D3 daily filters; produce watchlist for the day | No intraday filters, no order placement |
| **SignalEngine** | On each closed 5m bar: apply I1/I2/I3 intraday filters; emit `SignalEvent(BUY)` when all pass | No position sizing, no order routing |
| **RiskEngine** | Size each trade (1% account risk, 10% max notional, 5 max concurrent positions); approve/reject entry; compute initial stop price | No market data, no order calls |
| **ExecutionEngine** | Place, modify, and cancel orders via MoomooGateway; translate logical intents (place/modify_stop/cancel) into SDK calls; record fills | No strategy logic, no position tracking |
| **PositionManager** | Owns per-position `PositionState` FSMs; processes fills + bar events to advance state (OPEN → PARTIAL → BREAKEVEN → TRAIL → CLOSED); computes stop updates and partial-exit orders | No order routing directly — emits `OrderIntent` to ExecutionEngine |
| **BarAggregator** | Subscribes K_5M via `CurKlineHandlerBase`; detects bar close by timestamp advance; delivers `BarEvent(closed_bar)` to SignalEngine and PositionManager | No strategy logic |
| **MoomooGateway** | Persistent `OpenQuoteContext` + `OpenSecTradeContext`; exposes typed methods (get_snapshot, get_kline_history, subscribe, place_order, modify_order, cancel_order, get_positions); wraps `create_quote_context()` / `create_trade_context()` from existing `skills/moomooapi/scripts/common.py` | No business logic; no state |
| **StateStore** | SQLite persistence for open positions, completed trades, daily scan results; allows restart recovery; append-only trade log | No computation |
| **TelegramAlerter** | Fire-and-forget async Telegram pushes for entries, exits (partial/breakeven/trail/stop/force-close), and daily summary | No blocking calls in the trade loop |
| **StrategyCore (ABC)** | Shared abstract base: `on_bar(bar) → List[Signal]`; `compute_stop(position, bars) → price`; `should_partial(position) → bool`; `should_breakeven(position) → bool` — implemented once, used by both live and backtester | Defined in `bot/strategy/base.py`; no I/O |
| **Backtester** | Offline harness feeding historical 5m bars through `StrategyCore` + `PositionManager`; `SimulatedExecution` replaces `ExecutionEngine`; produces trade log + metrics | No network calls, no OpenD dependency |

## Recommended Project Structure

```
ai-snp-trading-claude/
├── bot/
│   ├── __init__.py
│   ├── main.py                   # Entry point; constructs TradingBot and runs asyncio loop
│   ├── config.py                 # Typed config dataclass; reads .env / environment variables
│   │
│   ├── strategy/
│   │   ├── base.py               # StrategyCore ABC — shared with backtester
│   │   ├── trend_join_long.py    # Concrete implementation of StrategyCore
│   │   └── indicators.py         # Pure functions: SMA200, RVOL, swing_low_2_2, etc.
│   │
│   ├── scanner.py                # Premarket scan; S&P 500 list + D1/D2/D3 daily filters
│   ├── signal_engine.py          # Intraday 5m loop; I1/I2/I3 filters → SignalEvent
│   ├── risk_engine.py            # Position sizing; concurrent-position gate; stop computation
│   │
│   ├── position/
│   │   ├── manager.py            # PositionManager; owns all PositionState objects
│   │   ├── state.py              # PositionState FSM (OPEN, PARTIAL, BREAKEVEN, TRAIL, CLOSED)
│   │   └── models.py             # Dataclasses: Position, Fill, OrderIntent
│   │
│   ├── execution.py              # ExecutionEngine; translates OrderIntents → moomoo API calls
│   ├── bar_aggregator.py         # CurKlineHandlerBase subclass; K_5M subscription + bar-close detection
│   ├── gateway.py                # MoomooGateway; persistent contexts; thin typed wrapper
│   │
│   ├── state_store.py            # SQLite persistence; positions + trades + scan results
│   ├── alerter.py                # TelegramAlerter; python-telegram-bot async send_message
│   │
│   └── scheduler.py              # TradingBot orchestrator; APScheduler cron jobs; daily lifecycle
│
├── backtester/
│   ├── __init__.py
│   ├── run.py                    # CLI entry point; loads CSV/parquet data, runs simulation
│   ├── feed.py                   # SimulatedBarFeed; replays historical 5m bars as BarEvents
│   ├── execution.py              # SimulatedExecution; fill at bar-close price + slippage model
│   └── report.py                 # Trade metrics: win rate, avg R, max drawdown, PnL curve
│
├── data/
│   ├── sp500_constituents.csv    # Cached S&P 500 list (refreshed weekly)
│   └── historical/               # 5m bar CSV/parquet files for backtesting
│
├── logs/
│   ├── bot.log                   # Rotating file log; INFO+ in prod
│   └── trades.jsonl              # Append-only trade event log (mirrors StateStore)
│
├── state.db                      # SQLite state file (gitignored)
├── .env                          # FUTU_* env vars + TELEGRAM_* (gitignored)
└── skills/                       # Existing moomooapi skill — not modified
```

### Structure Rationale

- **bot/strategy/** is isolated with no I/O dependencies so the backtester imports it unchanged.
- **bot/position/** is its own package because the FSM and models are non-trivial; separating `state.py` from `manager.py` keeps per-position logic testable in isolation.
- **backtester/** is a sibling package, not nested inside `bot/`, making the offline/online split explicit. It imports from `bot/strategy/` and `bot/position/` but never from `bot/gateway.py` or `bot/execution.py`.
- **skills/** is left untouched; `bot/gateway.py` imports directly from `skills/moomooapi/scripts/common.py`.

## Architectural Patterns

### Pattern 1: Event-Driven Internal Bus

**What:** Components communicate through a lightweight in-process event queue (Python `asyncio.Queue` or synchronous `collections.deque`). Events are typed dataclasses: `BarEvent`, `SignalEvent`, `OrderIntent`, `FillEvent`, `AlertEvent`. No component calls another component's methods directly except through emitting events.

**When to use:** This system has clear directional data flow (bar → signal → risk → order → fill → position → alert). Event types document the contract; components are independently testable by injecting synthetic events.

**Trade-offs:** Adds indirection but eliminates tight coupling. For a single-process bot with <5 concurrent positions, there is no performance downside.

**Example:**
```python
# bot/position/models.py
from dataclasses import dataclass

@dataclass
class BarEvent:
    code: str          # "US.AAPL"
    time_key: str      # "2026-06-23 10:05:00" — the bar's open time
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_closed: bool    # True when timestamp has advanced past this bar

@dataclass
class SignalEvent:
    code: str
    entry_price: float  # ask at signal time
    lod: float          # low-of-day for stop calculation
    rvol: float

@dataclass
class OrderIntent:
    code: str
    action: str         # "PLACE_ENTRY" | "MODIFY_STOP" | "PARTIAL_EXIT" | "CLOSE_ALL"
    quantity: int
    price: float        # limit price or stop price depending on action
    position_id: str
```

### Pattern 2: Per-Position Finite State Machine

**What:** Each open position is a `PositionState` object with an explicit state enum and a `on_bar(bar) → List[OrderIntent]` method. The state machine owns all transition logic; the `PositionManager` just iterates over active positions on each bar event.

**When to use:** The Trend Join Long exit rules (partial at 0.75R → breakeven at 1.0R → trailing 5m swing low) are sequential and stateful. An FSM makes the transitions explicit and prevents logic bleeding across states.

**States and transitions:**
```
OPEN
  └─ on fill confirmed → ACTIVE
       ├─ price reaches 0.75R → emit PARTIAL_EXIT intent → PARTIAL_TAKEN
       │      └─ price reaches 1.0R → emit MODIFY_STOP(breakeven) → BREAKEVEN
       │              └─ each bar close → update trail stop to swing_low_2_2 → TRAILING
       │                      └─ price < trail stop → emit CLOSE intent → CLOSED
       └─ price < initial stop → emit CLOSE intent → STOPPED_OUT
  Force-close (15:51 ET): any non-CLOSED state → emit CLOSE_ALL → FORCE_CLOSED
```

**Example:**
```python
# bot/position/state.py
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List
from bot.position.models import BarEvent, OrderIntent

class PositionPhase(Enum):
    AWAITING_FILL = auto()
    ACTIVE         = auto()
    PARTIAL_TAKEN  = auto()
    BREAKEVEN      = auto()
    TRAILING       = auto()
    CLOSED         = auto()

@dataclass
class PositionState:
    position_id: str
    code: str
    entry_price: float
    initial_stop: float
    full_quantity: int
    remaining_quantity: int
    phase: PositionPhase = PositionPhase.AWAITING_FILL
    trail_stop: float = 0.0
    risk_per_share: float = field(init=False)

    def __post_init__(self):
        self.risk_per_share = self.entry_price - self.initial_stop

    def on_bar(self, bar: BarEvent, swing_low: float) -> List[OrderIntent]:
        intents: List[OrderIntent] = []
        current_price = bar.close
        r_multiple = (current_price - self.entry_price) / self.risk_per_share

        if self.phase == PositionPhase.ACTIVE:
            if current_price < self.initial_stop:
                intents.append(OrderIntent(self.code, "CLOSE", self.remaining_quantity, current_price, self.position_id))
                self.phase = PositionPhase.CLOSED
            elif r_multiple >= 0.75:
                partial_qty = round(self.full_quantity * 0.3333)
                intents.append(OrderIntent(self.code, "PARTIAL_EXIT", partial_qty, current_price, self.position_id))
                self.remaining_quantity -= partial_qty
                self.phase = PositionPhase.PARTIAL_TAKEN

        elif self.phase == PositionPhase.PARTIAL_TAKEN:
            if current_price < self.initial_stop:
                intents.append(OrderIntent(self.code, "CLOSE", self.remaining_quantity, current_price, self.position_id))
                self.phase = PositionPhase.CLOSED
            elif r_multiple >= 1.0:
                intents.append(OrderIntent(self.code, "MODIFY_STOP", 0, self.entry_price, self.position_id))
                self.trail_stop = self.entry_price
                self.phase = PositionPhase.BREAKEVEN

        elif self.phase in (PositionPhase.BREAKEVEN, PositionPhase.TRAILING):
            new_stop = max(self.trail_stop, swing_low)
            if new_stop > self.trail_stop:
                intents.append(OrderIntent(self.code, "MODIFY_STOP", 0, new_stop, self.position_id))
                self.trail_stop = new_stop
            if current_price < self.trail_stop:
                intents.append(OrderIntent(self.code, "CLOSE", self.remaining_quantity, current_price, self.position_id))
                self.phase = PositionPhase.CLOSED
            elif self.phase == PositionPhase.BREAKEVEN:
                self.phase = PositionPhase.TRAILING

        return intents
```

### Pattern 3: StrategyCore ABC for Live/Backtest Parity

**What:** The strategy-specific computation (indicator calculation, signal condition evaluation, stop computation) lives in `bot/strategy/base.py` as an abstract base class. Both the live `SignalEngine` and the `Backtester` inject the same `TrendJoinLong` concrete implementation. The execution environment is injected separately and swapped: `ExecutionEngine` in live, `SimulatedExecution` in backtest.

**When to use:** Always, for any strategy that needs historical validation. The goal stated in QuantStart's event-driven backtesting series is explicit: "minimise duplication of code between the backtesting element and the live execution element." Freqtrade's IStrategy achieves this; so does backtrader's Strategy ABC.

**Trade-offs:** Requires discipline to keep I/O out of the ABC. Any network call inside `on_bar()` breaks the backtester. Enforce this with a rule: `StrategyCore` methods accept only plain data (bars as dataclasses or DataFrames), never context objects.

**Example:**
```python
# bot/strategy/base.py
from abc import ABC, abstractmethod
from typing import List, Optional
import pandas as pd
from bot.position.models import BarEvent, SignalEvent

class StrategyCore(ABC):

    @abstractmethod
    def passes_daily_filters(self, code: str, daily_data: pd.DataFrame, sma200: float) -> bool:
        """D1/D2/D3 checks. Returns True if the stock is valid for today's watchlist."""

    @abstractmethod
    def passes_intraday_filters(self, code: str, bars_5m: pd.DataFrame,
                                premarket_high: float, hod: float, rvol: float) -> bool:
        """I1/I2/I3 checks on a closed 5m bar. Returns True if entry conditions are met."""

    @abstractmethod
    def compute_initial_stop(self, lod: float) -> float:
        """LOD - 1% rule."""

    @abstractmethod
    def compute_swing_low_2_2(self, bars_5m: pd.DataFrame) -> Optional[float]:
        """Returns the 5m swing low using 2-bar-left / 2-bar-right confirmation, or None if unavailable."""
```

```python
# bot/strategy/trend_join_long.py
import pandas as pd
from typing import Optional
from bot.strategy.base import StrategyCore

class TrendJoinLong(StrategyCore):

    def passes_daily_filters(self, code, daily_data, sma200):
        row = daily_data.iloc[-1]
        prior_day_high = daily_data.iloc[-2]["high"]
        prior_close    = daily_data.iloc[-2]["close"]
        gap_pct = (row["open"] - prior_close) / prior_close * 100
        return (
            row["close"] > prior_day_high and
            prior_close > sma200 and
            gap_pct >= 3.0 and
            row["close"] >= 3.0
        )

    def passes_intraday_filters(self, code, bars_5m, premarket_high, hod, rvol):
        current_close = bars_5m.iloc[-1]["close"]
        return (
            current_close > premarket_high and
            current_close >= hod and
            rvol >= 2.0
        )

    def compute_initial_stop(self, lod):
        return lod * 0.99

    def compute_swing_low_2_2(self, bars_5m):
        if len(bars_5m) < 5:
            return None
        lows = bars_5m["low"].values
        # Pivot low at index i: low[i] < low[i-1], low[i-2] AND low[i] < low[i+1], low[i+2]
        # With closed bar at -1, look at index -3 as potential pivot (bars[-5] to [-1] available)
        i = len(lows) - 3
        if i >= 2:
            pivot = lows[i]
            if (pivot < lows[i-1] and pivot < lows[i-2] and
                    pivot < lows[i+1] and pivot < lows[i+2]):
                return float(pivot)
        return None
```

### Pattern 4: Bar-Close Detection via Timestamp Advance

**What:** The moomoo K_5M subscription pushes updates for the current forming bar multiple times per 5-minute period. There is no `is_closed` flag in the push payload. Bar closure is detected by tracking the last-seen `time_key` — when a new push arrives with a `time_key` different from the previous one, the previous bar is now closed and ready for strategy evaluation.

**When to use:** Always required when using `CurKlineHandlerBase` for strategy decisions. Acting on in-progress bars produces false signals because `high`, `low`, and `close` values are still moving.

**Trade-offs:** One bar of latency (strategy acts at bar N+1 based on bar N's data). This is correct behavior for a closed-bar strategy. The alternative — polling `get_cur_kline()` on a timer — is less reliable and wastes rate-limit quota.

**Example:**
```python
# bot/bar_aggregator.py
from moomoo import CurKlineHandlerBase, RET_OK
from bot.position.models import BarEvent

class BarAggregator(CurKlineHandlerBase):
    def __init__(self, on_bar_close_callback):
        super().__init__()
        self._last_time_key: dict[str, str] = {}      # code → last seen time_key
        self._last_bar: dict[str, dict] = {}           # code → last bar data
        self._callback = on_bar_close_callback         # async-safe callable

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            return ret_code, data
        for _, row in data.iterrows():
            code     = row["code"]
            time_key = row["time_key"]
            prev_key = self._last_time_key.get(code)
            if prev_key is not None and time_key != prev_key:
                # Previous bar is now closed — deliver it
                closed = self._last_bar[code]
                event = BarEvent(
                    code=code,
                    time_key=closed["time_key"],
                    open=closed["open"],
                    high=closed["high"],
                    low=closed["low"],
                    close=closed["close"],
                    volume=int(closed["volume"]),
                    is_closed=True,
                )
                self._callback(event)           # dispatches into the event queue
            self._last_time_key[code] = time_key
            self._last_bar[code] = row.to_dict()
        return RET_OK, data
```

## Data Flow

### Daily Lifecycle — Full Data Flow

```
06:00 ET  TradingBot.premarket_job()
          │
          ├─ Scanner.run()
          │    ├─ MoomooGateway.get_plate_stock("US.S&P500") → 500 codes
          │    ├─ MoomooGateway.get_snapshot(codes) → price, gap%, prior_day_high
          │    ├─ MoomooGateway.get_kline_history(codes, K_DAY, 200) → SMA200
          │    ├─ TrendJoinLong.passes_daily_filters() per stock
          │    └─ StateStore.save_scan_results(watchlist)
          │
          ▼
09:30 ET  TradingBot.market_open_job()
          │
          ├─ MoomooGateway.subscribe(watchlist, SubType.K_5M)   ← bar feed starts
          └─ MoomooGateway.subscribe(watchlist, SubType.QUOTE)  ← LOD/HOD/RVOL feed
          │
          │  [10:05 ET → entry window opens]
          ▼
          BarAggregator.on_recv_rsp() [moomoo SDK thread]
          │  timestamp advance detected → BarEvent(closed_bar)
          │
          ▼
          asyncio event queue
          │
          ▼
10:05–15:30 ET  SignalEngine.on_bar(BarEvent)
          │    ├─ skip if outside entry window OR max_positions already at limit
          │    ├─ fetch live HOD, premarket_high, RVOL from MoomooGateway.get_snapshot()
          │    ├─ TrendJoinLong.passes_intraday_filters()
          │    └─ emit SignalEvent if passes
          │
          ▼
          RiskEngine.on_signal(SignalEvent)
          │    ├─ gate: concurrent position count < 5
          │    ├─ compute shares = floor(account_equity * 0.01 / risk_per_share)
          │    ├─ gate: notional < account_equity * 0.10
          │    └─ emit OrderIntent(PLACE_ENTRY, code, shares, price, stop)
          │
          ▼
          ExecutionEngine.on_order_intent(OrderIntent)
          │    ├─ MoomooGateway.place_order(code, BUY, shares, limit_price)
          │    └─ emit FillEvent when confirmed
          │
          ▼
          PositionManager.on_fill(FillEvent)
          │    ├─ create PositionState(AWAITING_FILL → ACTIVE)
          │    ├─ StateStore.save_position()
          │    └─ TelegramAlerter.send_entry_alert()
          │
          ▼
  [Every closed 5m bar thereafter]
          PositionManager.on_bar(BarEvent) for each ACTIVE position
          │    ├─ StrategyCore.compute_swing_low_2_2(bars_5m)
          │    ├─ PositionState.on_bar(bar, swing_low) → List[OrderIntent]
          │    └─ for each intent: ExecutionEngine.on_order_intent(intent)
          │              └─ MoomooGateway.modify_order / cancel+replace / place_sell
          │                          └─ FillEvent → StateStore.update_position()
          │                                       └─ TelegramAlerter.send_exit_alert()
          │
          ▼
15:51 ET  TradingBot.force_close_job()
          │    ├─ PositionManager.get_all_active()
          │    ├─ for each: ExecutionEngine.on_order_intent(CLOSE_ALL)
          │    └─ TelegramAlerter.send_daily_summary()
          │
          ▼
16:00 ET  TradingBot.eod_job()
               ├─ MoomooGateway.unsubscribe_all()
               └─ StateStore.archive_day()
```

### Backtester Data Flow

```
run.py
  │
  ▼
SimulatedBarFeed.load(csv_path)   [reads historical 5m OHLCV]
  │
  ▼
  for bar in feed:
      SignalEngine.on_bar(bar)    [same class, same TrendJoinLong instance]
        └─ passes_intraday_filters() → SignalEvent
              └─ RiskEngine.size_trade() → OrderIntent(PLACE_ENTRY)
                    └─ SimulatedExecution.fill_at_bar_close()  [replaces MoomooGateway]
                          └─ PositionManager.on_fill()          [same class as live]
                                └─ PositionState.on_bar()       [same FSM]
                                      └─ TradeLog.record()

report.py
  ├─ win_rate, avg_r, max_drawdown, sharpe
  └─ per-trade CSV
```

### State Management

```
StateStore (SQLite, single writer)
  ├─ positions        (position_id, code, phase, entry_price, stop, qty_remaining, ...)
  ├─ trades           (closed positions; immutable append-only)
  ├─ daily_scan       (date, watchlist codes)
  └─ bar_cache        (last N bars per code; used for swing-low computation on restart)

On restart:
  PositionManager.load_from_store()
    └─ reconstructs PositionState objects from DB
    └─ re-subscribes bar feed for open position codes
    └─ queries current market prices to reconcile stop levels
```

## Scaling Considerations

This is a single-operator bot. Scaling concerns are narrow and specific:

| Concern | At launch (≤5 positions) | If watchlist grows (500+ scanned, ~20 subscriptions) |
|---------|--------------------------|-----------------------------------------------------|
| Subscription quota | Trivially fine; <30 K_5M subscriptions | Still fine at standard tier (300 quota); scan-time snapshot polling avoids needing subscriptions during premarket |
| API rate limits | 60 req/30s for snapshots; batch 100 codes per call → 5 calls for S&P 500 | Batch calls; add 1s sleep between batches; premarket has plenty of time |
| Bar callback throughput | moomoo SDK delivers callbacks on its own thread; asyncio queue decouples it from the main loop | Single-threaded event loop is sufficient for ≤50 subscribed codes |
| StateStore | SQLite single-writer is fine for ≤10 writes/minute | No scaling concern at this volume |
| Force-close latency | 5 positions × 1 cancel+market-order each = ~5 sequential calls; well within 15:51 deadline | If position count grew large, parallelize with `asyncio.gather`; not needed now |

### Concurrency Model

Use a **single asyncio event loop** for the entire bot:
- `TradingBot`, `SignalEngine`, `RiskEngine`, `PositionManager`, `ExecutionEngine`, `TelegramAlerter` are all async coroutines running in the same loop.
- `BarAggregator.on_recv_rsp()` is called by the moomoo SDK on a background thread. Use `loop.call_soon_threadsafe(queue.put_nowait, event)` to bridge the SDK thread into the asyncio loop safely.
- `APScheduler` with `AsyncIOScheduler` schedules the daily lifecycle jobs (premarket, market-open, force-close, EOD) as cron triggers inside the same loop.
- No multiprocessing needed. The bot's bottleneck is network I/O, not CPU.

## Anti-Patterns

### Anti-Pattern 1: Polling Instead of K_5M Subscription

**What people do:** Run a `while True: sleep(300); fetch_kline()` loop to simulate bar events.

**Why it's wrong:** Polling with `get_cur_kline()` fires on a wall-clock schedule, not on bar boundaries. If a bar closes at 10:05:00 but the poll runs at 10:04:57, the bar used for signals is stale. Polling also burns API rate-limit quota (60 req/30s) that premarket batching needs. The moomoo K_5M subscription push fires on bar update — the timestamp-advance detection pattern gets bar-close precision with zero quota cost.

**Do this instead:** Subscribe `SubType.K_5M` once at market open; use `BarAggregator` with timestamp-advance detection to deliver `BarEvent(is_closed=True)` events. Unsubscribe at EOD.

### Anti-Pattern 2: Putting Strategy Logic in the Execution Layer

**What people do:** Embed trailing-stop math or partial-exit conditions directly inside the order placement code.

**Why it's wrong:** The same logic then cannot be run offline in the backtester without OpenD. It also makes unit testing impossible without a live connection. When the strategy rules change, two code paths diverge.

**Do this instead:** All strategy math lives in `bot/strategy/` (pure functions, no I/O). `PositionState.on_bar()` calls strategy functions and returns `OrderIntent` objects. `ExecutionEngine` only knows how to translate `OrderIntent` → moomoo API call.

### Anti-Pattern 3: Stop Management via Broker-Side Stop Orders

**What people do:** Place a native stop-loss order with the broker and update it via `modify_order()` on each bar.

**Why it's wrong:** The moomoo paper-account (SIMULATE) environment has limited stop-order support and the behavior of stop-order fills on paper may not match real execution. More importantly, the trailing stop in this strategy requires custom logic (swing low 2/2 pattern) that a native trailing-stop order type cannot express. Finally, moomoo's `modify_order()` only works on pending orders; a stop that has already been triggered cannot be modified.

**Do this instead:** Track the stop price in `PositionState`. On each bar close, evaluate whether the price has crossed the stop. If yes, emit a market-sell `OrderIntent`. This gives full control over stop logic and makes backtesting exact, since the simulated execution uses the same crossing test.

### Anti-Pattern 4: Skipping Durable State

**What people do:** Keep position state only in memory; assume the process runs uninterrupted all day.

**Why it's wrong:** macOS goes to sleep, OpenD crashes, network drops. If the bot restarts mid-session with an open position but no state, it either re-enters (double position) or ignores the position entirely (no stop management). Both are safety failures.

**Do this instead:** Write position state to `StateStore` (SQLite) on every state transition. On startup, `PositionManager.load_from_store()` reconstructs all in-flight positions before re-subscribing to bar feeds.

### Anti-Pattern 5: Blocking the Event Loop with API Calls

**What people do:** Call `quote_ctx.get_snapshot()` synchronously inside `on_bar_close()`.

**Why it's wrong:** The moomoo SDK's context methods are synchronous and can block for 100–500ms. Calling them inside the asyncio event loop will stall all coroutines, causing missed bar events for other symbols and delayed order placement.

**Do this instead:** Wrap SDK calls in `asyncio.get_event_loop().run_in_executor(None, sdk_call)` inside `MoomooGateway` async methods. This offloads blocking network I/O to a thread pool and keeps the event loop non-blocking.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| OpenD (127.0.0.1:11111) | Persistent `OpenQuoteContext` + `OpenSecTradeContext`; reuse existing `create_quote_context()` / `create_trade_context()` from `skills/moomooapi/scripts/common.py` | Must be running before bot starts; add pre-flight connectivity check on startup |
| Telegram Bot API | `python-telegram-bot` v20+ async; `Bot.send_message()` via `await`; fire-and-forget in `TelegramAlerter` | Telegram token + chat_id in `.env`; never block trade loop waiting for Telegram ACK |
| moomoo SDK (CurKlineHandlerBase) | `quote_ctx.set_handler(bar_aggregator)` then `quote_ctx.subscribe(codes, [SubType.K_5M])` | SDK callbacks run on SDK thread; bridge to asyncio with `call_soon_threadsafe` |

### Internal Boundaries

| Boundary | Communication | Rule |
|----------|---------------|------|
| BarAggregator → event loop | `loop.call_soon_threadsafe(queue.put_nowait, bar_event)` | BarAggregator is the ONLY component that crosses SDK-thread / asyncio boundary |
| SignalEngine → RiskEngine | `SignalEvent` dataclass in queue | RiskEngine is downstream consumer; no back-channel |
| RiskEngine → ExecutionEngine | `OrderIntent` dataclass in queue | ExecutionEngine never calls RiskEngine |
| PositionManager → ExecutionEngine | `OrderIntent` dataclass in queue | Same channel as RiskEngine; execution is the single sink |
| ExecutionEngine → PositionManager | `FillEvent` dataclass in queue | Fills flow back upstream only through events, not direct method calls |
| PositionManager → TelegramAlerter | `AlertEvent` dataclass; alerter reads from its own sub-queue | Alerts are lossy-OK; a failed Telegram push must never block position management |
| bot/ → backtester/ | Import `bot.strategy.*` and `bot.position.*` only | backtester never imports `bot.gateway`, `bot.execution`, or `bot.scheduler` |

## Build Order Implications

Build bottom-up, inner layers first, so each phase has something runnable and testable before the next layer is added:

1. **MoomooGateway + StateStore** — foundational I/O layer; nothing else can be tested without it.
2. **StrategyCore ABC + TrendJoinLong + indicators** — pure logic; unit-testable with synthetic DataFrames; no broker dependency.
3. **Scanner** — uses MoomooGateway + StrategyCore daily filters; first end-to-end premarket path.
4. **BarAggregator** — K_5M subscription + bar-close detection; validates the real-time data path.
5. **SignalEngine + RiskEngine** — wires BarAggregator output to strategy; produces OrderIntents.
6. **PositionState FSM + PositionManager** — the most complex logic; build with unit tests against the FSM before wiring broker.
7. **ExecutionEngine** — translates OrderIntents to moomoo API; test against paper account.
8. **TelegramAlerter** — last; it has zero functional dependencies on the trade loop.
9. **TradingBot (Scheduler)** — wires everything together; integration test the full daily lifecycle.
10. **Backtester** — imports from bot/strategy/ and bot/position/; validates strategy on historical data.

## Sources

- [Moomoo API: Real-time Candlestick Callback](https://openapi.moomoo.com/moomoo-api-doc/en/quote/update-kl.html) — CurKlineHandlerBase pattern, available fields (HIGH confidence — official docs)
- [Moomoo API: Subscribe and Unsubscribe](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html) — SubType.K_5M subscription parameters (HIGH confidence — official docs)
- [Moomoo API: Authorities and Quota](https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html) — subscription quota tiers (HIGH confidence — official docs)
- [QuantStart: Event-Driven Backtesting with Python Part I](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I/) — event queue pattern, DataHandler/Strategy/Portfolio/ExecutionHandler, live-backtest parity (HIGH confidence — authoritative reference)
- [Freqtrade Architecture on DeepWiki](https://deepwiki.com/freqtrade/freqtrade) — IStrategy unified interface, DataProvider, Trade persistence in SQLite (MEDIUM confidence — DeepWiki synthesis of open-source project)
- [Building a Crypto Trading Bot: Abstract Base Classes](https://quantitativepy.substack.com/p/building-a-crypto-trading-bot-from-cfb) — strategy ABC enforcement pattern (MEDIUM confidence — practitioner article)
- [APScheduler documentation](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html) — AsyncIOScheduler for cron-style intraday scheduling (HIGH confidence — official docs)
- [Futu OpenAPI: Get Real-time Candlestick](https://openapi.futunn.com/futu-api-doc/en/quote/get-kl.html) — bar data fields (HIGH confidence — official docs)

---
*Architecture research for: AI S&P Trading Bot — Trend Join Long, automated intraday, moomoo/Futu paper account*
*Researched: 2026-06-23*
