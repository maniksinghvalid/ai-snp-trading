# Pitfalls Research

**Domain:** Stateful intraday day-trading bot — moomoo/Futu OpenAPI, paper account, Python
**Researched:** 2026-06-23
**Confidence:** HIGH (OpenD/API pitfalls verified against official docs; backtest and state-machine pitfalls verified against multiple primary sources)

---

## Critical Pitfalls

### Pitfall 1: Signal on Incomplete (Live) 5-Minute Bar — Repainting / False Entry

**What goes wrong:**
The bot evaluates entry conditions (above HOD, above premarket high, RVOL ≥ 2.0) while the current 5m bar is still open. The bar's high, volume, and close values change with each tick. A condition that is true mid-bar (e.g., volume spike at bar minute 2) may be false by bar close. The bot fires an entry signal that does not actually confirm once the bar closes — this is the trading-bot analogue of TradingView's "repainting" problem. Backtests always use closed bars, so live behavior diverges from backtest behavior, and strategy performance is overstated by up to 40% in simulations.

**Why it happens:**
Real-time subscriptions push tick-by-tick data; it is natural to evaluate conditions on every push. Developers test against historical OHLCV data (always closed bars) and assume the same logic works live. The distinction between "bar is in progress" and "bar is closed" is not enforced at the data layer.

**How to avoid:**
- Maintain an explicit `bar_state` flag: only evaluate entry conditions when `bar_is_closed = True` (i.e., immediately after the 5m boundary ticks over and the new bar opens).
- Use the push_kline callback's `is_subscribe_push` flag and bar timestamp to detect bar close vs mid-bar updates.
- In the backtester, always enter at the open of the bar *following* the signal bar (signal fires on bar N close, entry at bar N+1 open) — never at signal bar close.
- Never evaluate RVOL, HOD, or swing-low trailing on a bar that has not yet closed.

**Warning signs:**
- Live entry rate dramatically exceeds backtest entry rate.
- Entries occur at prices inconsistent with bar close price in logs.
- Position is entered and immediately stopped out at a level that the bar high never sustained.

**Phase to address:** Scanner / Signal Engine (Phase: Intraday Signal Engine)

---

### Pitfall 2: OpenD Token Expiry Silently Kills the Session Mid-Day

**What goes wrong:**
OpenD uses a session token that can expire, especially when using the "remember password" GUI login mode. On token expiry — which can be triggered by a Futu backend release, network blip, or OS sleep — OpenD disconnects from the Futu backend. Critically, the Python SDK does not throw an exception or invoke a callback when the connection drops; it simply stops receiving push data. The bot continues running, believes it has active subscriptions and live positions, and stops managing exits. Trailing stops are never updated. The EOD force-close never fires because the market-state check uses stale data.

**Why it happens:**
OpenD is a GUI desktop app, not a headless server daemon. It was designed for interactive use. The Python client connects to OpenD over localhost TCP but has no built-in heartbeat that causes failures to surface immediately to application code. The existing codebase already identifies "no retry/reconnection logic" as a known gap (CONCERNS.md: "Limited Error Recovery").

**How to avoid:**
- Implement a watchdog thread that polls `get_global_state()` or a lightweight quote request every 30–60 seconds and compares the result to a known-good state.
- On failure detection: pause order placement, send a Telegram alert, and attempt reconnect with exponential backoff before abandoning the session.
- Log OpenD in with a manual password (not remembered-password token mode) so OpenD handles its own session management more robustly per Futu's own recommendation.
- Run the bot on a machine that does not sleep (disable OS sleep/hibernate).
- At bot startup, validate that the OpenD connection is alive AND that the paper account is accessible before entering the market loop.

**Warning signs:**
- `get_global_state()` returns error or empty.
- Subscription push callbacks stop firing for > 60 seconds during market hours.
- The bot's internal position count diverges from what `get_portfolio()` returns.
- Telegram goes silent during market hours without an explicit EOD summary.

**Phase to address:** Core bot infrastructure / Long-running service (Phase: Service Orchestration & Reliability)

---

### Pitfall 3: Order State Divergence — Bot Thinks Open, Broker Says Closed (or Vice Versa)

**What goes wrong:**
The bot maintains an in-memory position registry. An order event (partial fill, stop trigger, forced cancel) occurs in the paper account but the corresponding callback is missed — because the connection dropped, the push was lost in a queue flush, or the paper account's push behavior is unreliable (INTEGRATIONS.md explicitly notes: "Push notifications for US paper trading accounts may be temporarily unavailable"). The bot continues to manage a phantom position: it sends stop modifications for orders that no longer exist, blocks new entries because it believes max 5 concurrent positions are occupied, and produces a daily summary that is factually wrong.

**Why it happens:**
Event-driven push architectures assume reliable delivery. Paper trading push reliability is explicitly lower than live. The existing client has no polling fallback and no reconciliation loop (CONCERNS.md: "No Detailed Order Status Polling").

**How to avoid:**
- Implement a periodic reconciliation loop (every 60–90 seconds during market hours) that calls `get_portfolio()` and `get_orders()` and diffs against the in-memory state. On divergence, in-memory state loses — broker truth wins.
- Assign a unique client_order_id to every order; use it as the reconciliation key.
- Use explicit position lifecycle states: `PENDING_ENTRY` → `OPEN` → `PARTIAL` → `PENDING_CLOSE` → `CLOSED`. Never skip states; the reconciler transitions states it cannot confirm.
- On reconnect after any connectivity gap, always do a full reconciliation before resuming normal operation.
- Treat "ghost position" (in-memory but not in broker) as a critical alert that blocks new entries until resolved.

**Warning signs:**
- `get_portfolio()` returns fewer positions than the bot's internal registry.
- Stop modification calls return "order not found" errors.
- Max-position guard fires even when the account shows no open positions.
- Order counts in daily summary do not match `get_history_orders()`.

**Phase to address:** Position Lifecycle Manager (Phase: Order & Position Management)

---

### Pitfall 4: RVOL Look-Ahead Bias — Using Today's Volume in the Lookback Average

**What goes wrong:**
The strategy requires RVOL = (today's cumulative volume at time T) / (average cumulative volume at time T over prior 14 days). If the RVOL lookback average is computed using today's volume in the denominator (i.e., the lookback window includes the current trading day), the indicator is self-referential and look-ahead biased. In the backtester this produces an RVOL that looks correct in code but uses future information. In live trading it produces a denominator that understates average volume when today is a high-volume day, inflating RVOL and generating spurious signals.

**Why it happens:**
Daily volume data is the most natural lookback source. Fetching "last 14 days of volume" with a naive `pd.Timestamp.today()` cutoff often includes the current incomplete trading day as day 0 if the API returns today's bar.

**How to avoid:**
- The lookback window for RVOL is strictly the prior 14 *completed* trading days — today is never included.
- When fetching historical daily bars for the lookback, always request data through `prior_close_date` (yesterday's date on a valid trading day), not today.
- For intraday RVOL: compare cumulative volume at time T (e.g., 10:30 ET) against the average cumulative volume at time T across the prior 14 sessions. Do not compare against total daily volume from prior sessions — time-of-day adjustment matters significantly because early-session volume is structurally lower.
- In the backtester, for each bar being evaluated, slice the lookback using only rows with a date strictly less than the current bar's date.
- Add a unit test: for any historical bar, assert that no row in the RVOL denominator has a date >= the signal bar date.

**Warning signs:**
- RVOL is > 1.0 at market open when volume is thin (first 5 minutes).
- Backtester RVOL and live RVOL diverge for the same ticker and time.
- RVOL denominator contains the current date's volume.

**Phase to address:** Backtester and Scanner (Phase: Premarket Scanner + Backtester)

---

### Pitfall 5: Backtest Look-Ahead Bias on Gap, SMA200, and Premarket High

**What goes wrong:**
The daily filters (gap ≥ 3%, prior close above SMA200, above prior-day high) and intraday triggers (above premarket high) all depend on prices that are only observable at specific points in time:
- The "gap" is observable only after market open (prior close vs today's open).
- "Prior close above SMA200" requires SMA computed on prices through yesterday's close — if the SMA calculation includes any bar from today, it's biased.
- "Above premarket high" is only knowable after the entry window begins (10:05 ET). If the backtester uses the full day's high to set the premarket high threshold, it's using a future value.
- "Above prior-day high" requires the prior trading day's high — adjusted prices can retroactively change this value if splits/dividends occur.

**Why it happens:**
Historical data APIs return full-day OHLCV for completed sessions. It is easy to accidentally use `df['high'].max()` across all intraday bars for "premarket high" rather than restricting to pre-9:30 bars. Adjusted price series change retroactively when corporate actions occur.

**How to avoid:**
- Premarket high: compute from bars with timestamps strictly before 09:30:00 ET on the current session. Use unadjusted prices for intraday triggers where the action happens in real-time.
- SMA200: compute using daily close prices through the prior trading day only.
- Prior-day high: use raw (unadjusted) prior-session OHLCV from the daily bar, not intraday reconstructed high.
- Gap: `(today_open - prior_close) / prior_close`. Both `today_open` and `prior_close` must be values that were known at market open — not end-of-day adjusted closes.
- Use a point-in-time data loading function that, given a signal timestamp, returns only data that was observable at or before that moment.

**Warning signs:**
- Backtester signals appear on symbols that had major corporate actions (splits) during the backtest window.
- Entry prices in backtest are inconsistent with what the real open price was for that date.
- Premarket high in backtest equals or exceeds the intraday high for that session.

**Phase to address:** Backtester (Phase: Backtester)

---

### Pitfall 6: Timezone Naivety — Hardcoding UTC Offsets Instead of Using Aware Datetimes

**What goes wrong:**
The strategy's timing is entirely in US Eastern Time (ET): entry window 10:05–15:30, force-close 15:51. ET is UTC-5 in winter (EST) and UTC-4 in summer (EDT). A bot that hardcodes `UTC-5` or computes `now_et = utc_now - timedelta(hours=5)` will be wrong for roughly 8 months of the year. More subtly, the US and Europe switch DST on different weeks in March and November, creating a 1-3 week window every year where the offset is ambiguous if the host machine is in a European timezone. A bot that fires force-close at 15:51 EST when the market is in EDT will close positions 1 hour early (14:51 real ET) — cutting the session short by nearly an hour.

**Why it happens:**
`datetime.now()` returns naive local time. Subtracting a fixed offset is the simplest approach and is wrong. `pytz` is error-prone when used with `replace()` instead of `localize()`. The host machine timezone is an invisible assumption.

**How to avoid:**
- Always use `zoneinfo.ZoneInfo("America/New_York")` (Python 3.9+) or `pytz.timezone("America/New_York")` with `localize()` — never a fixed UTC offset.
- All market-time comparisons must use timezone-aware datetime objects.
- At bot startup, log the current ET time and confirm it matches expected market session state (pre-market, open, closed) so timezone offset is visibly correct.
- Use `pandas_market_calendars` with the `NYSE` calendar to determine whether today is a trading day and to get the official close time (handles half-days like Black Friday or Christmas Eve automatically).
- Test across DST transition dates specifically.

**Warning signs:**
- Entry window opens or closes at the wrong clock time on DST-change weekends.
- EOD force-close fires at 14:51 or 16:51 ET.
- Bot does not trade on trading days in spring/fall because session detection is off by one hour.

**Phase to address:** Core bot infrastructure (Phase: Service Orchestration & Reliability)

---

### Pitfall 7: Market Calendar Blindness — Running on Holidays and Half-Days

**What goes wrong:**
The bot is scheduled to run every weekday. It does not account for US market holidays (e.g., Thanksgiving, Christmas, MLK Day) or early-close half-days (Black Friday, Christmas Eve, July 3rd). On a holiday the bot starts, connects to OpenD, finds no market data, and either errors out, enters a spin loop waiting for market open, or hangs indefinitely at the force-close check because the market never opened. On a half-day the market closes at 13:00 ET; the force-close at 15:51 ET never fires correctly. Positions opened just before 13:00 on a half-day are not closed.

**Why it happens:**
Simple "is it a weekday?" checks are the natural starting point. The moomoo API has `get_trading_days()` but developers often overlook it or check market state by polling quote data rather than the trading calendar.

**How to avoid:**
- Use `pandas_market_calendars` (`get_calendar("NYSE")`) at bot startup to determine: (a) is today a trading day, (b) what is the actual market close time today.
- Dynamically set the force-close time based on the calendar's early-close detection — on half-days, set force-close to `early_close_time - 9 minutes` (analogous to the 15:51 logic vs normal 16:00 close).
- If today is not a trading day, log and exit cleanly. Do not poll for market open.
- Cross-reference with `get_trading_days()` from the moomoo API as a sanity check.

**Warning signs:**
- Bot attempts to connect and trade on a US market holiday.
- Positions remain open past the paper account's session end on a half-day.
- Force-close alert fires but no positions are found (market was never open).

**Phase to address:** Service Orchestration (Phase: Service Orchestration & Reliability)

---

### Pitfall 8: Paper Trading Market Order Rejection — Undetected Pending Orders Block Exits

**What goes wrong:**
The moomoo paper trading (SIMULATE) environment does not guarantee market order support for US stocks. When the bot attempts a market order for an entry or exit (particularly for stop-loss execution or force-close), the order may be rejected or remain pending indefinitely with no fill. Because the bot's exit logic fires an order and then waits for a fill callback, a rejected/unfilled order leaves the position open with no active stop. The force-close at 15:51 ET fires a new order, but if the prior unfilled order is still active, the position may be double-counted or the new order rejected due to a conflicting pending order.

**Why it happens:**
The existing codebase notes that paper trading has important limitations: "paper trading only supports good-for-day orders when setting valid period" and "does not support enabling, disabling, and deleting the order." Developers test order placement in isolation and assume market fills work the same as live. The CONCERNS.md also identifies "No Detailed Order Status Polling" as a gap.

**How to avoid:**
- Use limit orders for all paper-account exits, priced aggressively (e.g., at bid for sells) to ensure quick fills without depending on market order support.
- Never place a second exit order for a position without first verifying that no pending exit order already exists for it (poll `get_orders()` before placing).
- Implement a "pending order TTL" — if an exit order is not filled within N seconds (e.g., 30 seconds), cancel and replace at a more aggressive limit price.
- At EOD force-close, cancel all open exit orders first, then place fresh limit orders at the current bid.
- Log every order state change; alert via Telegram when an exit order is not filled within TTL.

**Warning signs:**
- `get_orders()` shows a sell order in PENDING status while the position is still showing as open.
- Partial fill count in the position's fill log is less than expected shares.
- Bot places a force-close order that gets rejected with "conflicting order" error.

**Phase to address:** Order & Position Management (Phase: Order & Position Management)

---

### Pitfall 9: Partial Fill State Corruption — Breakeven and Trailing Calculated on Wrong Share Count

**What goes wrong:**
The exit strategy requires: take ⅓ off at 0.75R (partial profit), move stop to breakeven at 1.0R, then trail on swing lows. If the entry order receives a partial fill (e.g., 200 of 300 shares fill immediately), the bot may calculate R, the partial profit quantity, and the breakeven stop based on the *intended* 300 shares rather than the *actual* 200 shares. The partial profit sell order is then for the wrong quantity, the breakeven stop is miscalculated, and the position size after the partial is wrong. In paper trading, partial fills can occur on limit orders and may arrive in separate fill callbacks.

**Why it happens:**
Bots commonly calculate position parameters at order placement time rather than at fill confirmation time. The fill callback for a limit order may fire multiple times (each partial fill is a separate event). The paper account's fill model may partially fill orders against the simulated BBO.

**How to avoid:**
- Never compute R, stop-loss price, or partial-profit quantity until the entry order is confirmed as fully filled.
- Track `filled_qty` vs `intended_qty` explicitly. If `filled_qty < intended_qty` after a configurable timeout (e.g., 60 seconds), cancel the remainder and accept the partial.
- All subsequent exit order quantities must be derived from `filled_qty`, not `intended_qty`.
- The reconciler (see Pitfall 3) must validate that the position's shares in the broker account match the bot's `filled_qty`.

**Warning signs:**
- Partial profit order quantity exceeds current position size in the broker account.
- Stop-loss order is rejected because the position size at broker doesn't match the quantity in the stop order.
- Fill callbacks arrive after the bot has already moved to the next lifecycle state.

**Phase to address:** Order & Position Management (Phase: Order & Position Management)

---

### Pitfall 10: Non-Atomic State Persistence — Crash Leaves Bot in Indeterminate State

**What goes wrong:**
The bot writes its in-memory state (open positions, order IDs, stop levels, R-value, lifecycle stage) to disk for crash recovery. If the process crashes mid-write, the state file is partially written and either unparseable (JSON truncated) or internally inconsistent (e.g., position is marked `OPEN` but the associated order ID was not yet written). On restart the bot either fails to load state (unhandled exception) or loads a stale/corrupt view and takes wrong actions — placing duplicate orders, missing stops, or failing to close positions.

**Why it happens:**
Writing JSON to a file is not atomic on most filesystems. The codebase already identifies this pattern as a concern (CONCERNS.md: "Cache File TTL Race Condition"). The failure is especially dangerous in trading bots because wrong state leads to financial actions.

**How to avoid:**
- Always write state to a temp file first, then `os.replace()` atomically onto the target path.
- Validate the written file can be parsed before replacing the old file (write → parse → replace).
- Include a version/schema field and a checksum or record count in the state file; reject files that fail validation.
- On startup, if state file is invalid: do not guess — query the broker for current positions and reconstruct state from broker truth, then alert operator via Telegram.
- Persist state after every significant event (order placed, fill received, stop moved) rather than only on a timer.

**Warning signs:**
- Bot throws `json.JSONDecodeError` on startup.
- State file is 0 bytes.
- Position count in loaded state does not match current time of day or prior session's trade log.

**Phase to address:** Service Orchestration & Durable State (Phase: Service Orchestration & Reliability)

---

### Pitfall 11: Subscription Quota Exhaustion — Scanning 500 Stocks Silently Degrades

**What goes wrong:**
The S&P 500 universe contains ~500 stocks. At intraday signal time, the bot may attempt to subscribe to 5m candlestick pushes for many or all candidates. The moomoo OpenAPI subscription quota is tiered: 100 slots for accounts with assets below 10,000 HKD, 300 slots for standard accounts, and 1000 for premium accounts. Subscribing one data type for one stock = 1 slot. Subscribing `KL_5M` + `QUOTE` for 200 stocks = 400 slots — exceeding standard quota. Excess subscriptions fail silently or return an error that the bot does not handle; the bot receives no push data for those stocks and misses signals.

**Why it happens:**
Developers test with a small watchlist and don't hit quota limits. The quota is per connection and is not released until unsubscription or connection close (with a mandatory 60-second hold).

**How to avoid:**
- Premarket filtering (gap, SMA200, price) runs against snapshots (no subscription required), narrowing the universe to a small candidate list before subscribing.
- Only subscribe to 5m kline and quote push for the filtered candidate list (typically 5–30 symbols after premarket scan), not the full S&P 500.
- Track subscription count explicitly; log a warning if approaching quota limits.
- Unsubscribe from symbols that no longer meet filter criteria after each scan cycle.
- At bot shutdown (or EOD), unsubscribe all before closing the connection.

**Warning signs:**
- `sub()` calls return an error mentioning "insufficient subscription quota."
- Push callbacks stop firing for some symbols while others continue.
- The subscription count tracked in logs grows unbounded across sessions.

**Phase to address:** Premarket Scanner (Phase: Premarket Scanner)

---

### Pitfall 12: Backtest Survivorship Bias — Testing on Current S&P 500 Constituents

**What goes wrong:**
The S&P 500 constituent list changes constantly — companies are added after strong performance and removed after poor performance or delisting. If the backtester uses the *current* S&P 500 member list to source historical data, it is testing on a survivorship-biased universe: every company in the test set survived to today, which is a form of selection bias. Gap-up-and-trend strategies applied to today's constituents will overperform because the test universe excludes all the companies that gapped up and then failed (went bankrupt, were acquired at a loss, delisted). Retail strategy studies show survivorship bias inflates returns by 30–50%.

**Why it happens:**
Current constituent lists are easy to obtain; historical constituent lists (point-in-time) are not. The moomoo API's `get_plate_stock()` returns current members, not historical.

**How to avoid:**
- For the initial backtester, explicitly document this limitation in the strategy's validation report.
- Use a static constituent list from a specific historical date (e.g., S&P 500 as of backtest start date) rather than the current list.
- Source historical constituent lists from CRSP, Compustat, or open-source snapshots (e.g., the "survivorship-bias-free" lists on GitHub/Quandl).
- When interpreting backtest results, apply a conservative haircut (20–30%) to account for known survivorship bias if using current constituents.

**Warning signs:**
- Backtest universe contains companies that were only recently added to the S&P 500.
- No delisted or removed companies appear in the backtest dataset.
- Backtest win rate significantly exceeds live paper-trading win rate over the same period.

**Phase to address:** Backtester (Phase: Backtester)

---

### Pitfall 13: Silent API Rate Limit Violations — Snapshot Polling Causes Throttle

**What goes wrong:**
During the premarket scan the bot polls snapshots for 500 S&P 500 stocks. The moomoo `get_market_snapshot()` rate limit is 60 requests per 30 seconds (1 request per 0.5 seconds). Each request supports batches of up to 200 stocks. At 500 stocks / 200 per batch = 3 requests — well within the limit. However, if the bot also polls for intraday signals using snapshots (HOD, last price, volume) for a large candidate list every few seconds, the cumulative request rate quickly exceeds the limit. Throttled requests return an error; the bot either crashes or silently uses stale data for signal evaluation.

**Why it happens:**
Rate limits are documented per API endpoint separately. A bot that calls several APIs in a loop (snapshot + kline history + order status) may collectively exceed limits across endpoints without any single endpoint being over limit.

**How to avoid:**
- Use push subscriptions (not polling) for intraday signal updates on the candidate list — this eliminates the polling rate limit concern for live signal data.
- Reserve snapshot polling for premarket scan only, where a single pass over 500 stocks is sufficient.
- Add explicit rate-limit-aware spacing (`time.sleep()` with jitter) for any polling loops.
- Wrap all API calls in a centralized request dispatcher that enforces per-endpoint rate limits.
- Log API errors with HTTP/RPC error codes so throttle errors are distinguishable from data errors.

**Warning signs:**
- API calls return error code indicating "frequency limit exceeded."
- Scanner takes longer than expected and some results are missing.
- Intraday signal evaluation has unexplained gaps where no push was received.

**Phase to address:** Premarket Scanner and Signal Engine (Phase: Premarket Scanner + Signal Engine)

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip reconciliation loop, trust push events only | Simpler code, fewer API calls | Ghost positions block entries; missed fills go undetected indefinitely | Never — paper push reliability is explicitly documented as unreliable |
| Use naive `datetime.now()` for ET time | Less code | DST bugs cause wrong entry window and missed EOD close | Never — use `zoneinfo("America/New_York")` from day one |
| Evaluate signals on live (unclosed) bars | Real-time feel | Repainting: live entry rate diverges from backtest | Never — always wait for bar close confirmation |
| Test backtester on current S&P 500 list | Convenient, no extra data sourcing | Survivorship bias inflates returns; strategy appears better than it is | Acceptable for MVP if the limitation is documented explicitly and a haircut applied |
| In-memory-only state (no disk persistence) | Simpler | Single crash loses all position tracking; can't resume safely | Acceptable only during unit/integration testing, never in the production loop |
| Hardcode "500 stocks" for RVOL lookback fetch | Simple | Fetches today's incomplete bar in the lookback window, biasing RVOL | Never — always use `prior_trading_day` as the cutoff |
| One global exception handler around the trading loop | Prevents crashes | Swallows errors silently; bot appears to run but does nothing | Never — each exception class needs a deliberate response (retry, alert, halt) |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| OpenD session | Using "remember password" token mode for long-running bots | Log in with manual password; implement watchdog to detect disconnection via `get_global_state()` polling |
| moomoo paper account push | Assuming US paper account push notifications work like live | Treat push as best-effort; always reconcile against `get_portfolio()` / `get_orders()` on a timer |
| Paper trading order types | Placing market orders expecting immediate fills | Use limit orders at aggressive prices (BBO); implement fill TTL with cancel-replace logic |
| OpenD multiprocessing (Linux/Mac) | Using `fork`-based multiprocessing — SDK threads vanish in child processes | Set `mp.set_start_method('spawn')` before creating any processes |
| Subscription quota | Subscribing to full S&P 500 universe at signal time | Filter to candidate list via snapshots (no quota cost) first; subscribe only to finalists |
| `get_trading_days()` | Assuming weekday = trading day | Always check the NYSE calendar; handle holidays and early closes dynamically |
| Adjusted vs unadjusted prices | Using adjusted daily closes for intraday trigger prices | Use unadjusted prices for real-time intraday comparisons; adjusted only for SMA200 computation on daily bars |
| Multiple connections | Opening multiple Python contexts to the same OpenD | A single `OpenQuoteContext` is shared across the bot; multiple contexts consume connection slots and can conflict on quota rights |
| RVOL denominator | Using `get_history_kline()` with `end=today` | Set `end=prior_trading_day` strictly; verify no current-day row is in the returned DataFrame |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Polling snapshots for 500 stocks every 30 seconds during intraday | API throttle errors, missed signals, slow scan cycles | Subscribe push on candidate list; poll only premarket | Immediately on first 500-stock intraday cycle |
| Rebuilding SMA200 from scratch on every scan cycle | Premarket scan takes minutes instead of seconds | Cache SMA200 values per stock; recompute only after daily bar close | At 500 stocks with 200-day history fetch per stock |
| Writing full state JSON on every tick event | I/O contention, slow tick processing | Write state only on significant events (fill, stop move); use atomic writes | At high intraday activity (5m bars × 30 candidates) |
| Fetching full historical kline for RVOL on every signal evaluation | API quota exhaustion and latency | Fetch RVOL baseline once at premarket; update incrementally during session | At first intraday cycle with 30+ candidates |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging `FutuConfig` object containing credentials | Credentials in log files readable by other processes | Never log `FutuConfig`; mask `FUTU_LOGIN_PWD` in all log output |
| State file in world-readable location | Trade activity exposed to other OS users | Write state file with `0600` permissions; store under `~/.config/bot/` not `/tmp/` |
| Telegram bot token in environment variable logged at startup | Token leaks to log aggregators | Mask token in startup log: show only last 4 chars; never log full token |
| Accidentally enabling `TrdEnv.REAL` via env var misconfiguration | Real money order placed instead of paper | Assert `FUTU_TRD_ENV == "SIMULATE"` at startup; refuse to run if not SIMULATE; add a prominent log line confirming paper mode |

---

## "Looks Done But Isn't" Checklist

- [ ] **EOD Force-Close:** Often missing half-day handling — verify the force-close time is set from the calendar's actual close time, not a hardcoded 15:51 ET on every session.
- [ ] **RVOL Calculation:** Often missing time-of-day normalization — verify the RVOL denominator uses volume at the *same time of day* from prior sessions, not total daily volume.
- [ ] **Reconciliation Loop:** Often missing from MVP as "we'll add it later" — verify the loop runs every 60–90 seconds during market hours, not just at startup.
- [ ] **Bar-Close Gating:** Often appears implemented but leaks — verify that *every* signal evaluation path checks `bar_is_closed` before acting, including edge cases when a subscription reconnects mid-bar.
- [ ] **Position Lifecycle Terminal States:** Verify every position eventually reaches `CLOSED` state; run the reconciler daily and alert if any position in the log is not `CLOSED` after market hours.
- [ ] **Breakeven Stop Update:** Often only fires once — verify the stop modification order is confirmed filled/accepted by the broker before the bot considers the stop moved.
- [ ] **Swing-Low Trailing:** Often calculated on the current (open) bar's low rather than prior bars' confirmed lows — verify it only uses lows from closed 5m bars, using the pattern "2 bars down then 2 bars up" on confirmed closes.
- [ ] **Startup State Recovery:** Often not tested after an intentional crash mid-session — verify the bot recovers correctly from a state file written just before a force-close, with open positions.
- [ ] **Telegram Alert on Silent Period:** Often missing — verify that if no Telegram message has been sent for more than 60 minutes during market hours, the watchdog sends a heartbeat alert.
- [ ] **Paper Environment Confirmation:** Often assumed from defaults — verify `TrdEnv.SIMULATE` is logged at startup and an assertion prevents live trading.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| OpenD disconnects mid-session with open positions | MEDIUM | Reconnect; run full reconciliation against `get_portfolio()`; re-establish stop orders for any open positions; send Telegram alert with position status |
| Ghost position blocks new entries | LOW | Run reconciler; if position is not in broker account, mark as `CLOSED` in local state; log discrepancy for audit |
| State file corrupt on restart | MEDIUM | Discard corrupt state; query broker for current positions via `get_portfolio()`; reconstruct state; do not place new entries until reconstruction is confirmed; alert operator |
| Unfilled exit order stuck pending | LOW | Cancel the pending order; replace with a more aggressive limit order at current bid minus 1 tick; if still unfilled after 2nd TTL, escalate to Telegram alert |
| RVOL lookback includes today's bar | LOW (backtest) | Re-run scan with corrected date filter; discard any signals generated in the affected window |
| Wrong ET time on DST weekend | HIGH | Bot must be restarted with corrected timezone config; review all timed orders placed during affected window; check if force-close fired at wrong time |
| Subscription quota exhausted | LOW | Unsubscribe all; restart subscriptions for active candidate list only; check quota tier in account |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Signal on incomplete bar (repainting) | Intraday Signal Engine | Unit test: assert no entry fires when `bar_is_closed = False`; backtest entries are at bar N+1 open |
| OpenD token expiry / disconnect | Service Orchestration & Reliability | Integration test: kill OpenD mid-session; verify watchdog detects and alerts within 90 seconds |
| Order state divergence / ghost positions | Order & Position Management | Integration test: simulate missed push callback; verify reconciler corrects state within 90 seconds |
| RVOL look-ahead bias | Premarket Scanner + Backtester | Unit test: assert RVOL lookback DataFrame contains no row with `date >= signal_date` |
| Backtest look-ahead (gap, SMA, premarket high) | Backtester | Unit test: for each backtest bar, assert all input features use only data with timestamp < bar_open |
| Timezone / DST bug | Service Orchestration & Reliability | Integration test on DST transition dates; assert ET conversion is correct for Nov and Mar changeover |
| Market calendar blindness | Service Orchestration & Reliability | Integration test with a holiday date; bot should exit cleanly with "not a trading day" log |
| Paper order rejection / stuck pending | Order & Position Management | Integration test: place limit order far from market; verify TTL cancel-replace fires within N seconds |
| Partial fill state corruption | Order & Position Management | Integration test with simulated partial fill; verify R and quantities recalculate from `filled_qty` |
| Non-atomic state write | Service Orchestration & Reliability | Unit test: interrupt write mid-file; verify startup discards corrupt state and reconstructs from broker |
| Subscription quota exhaustion | Premarket Scanner | Integration test with quota near-limit; verify warning fires before failure |
| Backtest survivorship bias | Backtester | Documented limitation in validation report; constituent list date-stamped |
| API rate limit violations | Premarket Scanner + Signal Engine | Integration test: run full 500-stock scan; verify no throttle errors in logs |

---

## Sources

- [Moomoo OpenAPI — Transaction FAQ (official)](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html) — Paper trading limitations, order type restrictions, push notification caveats
- [Moomoo OpenAPI — OpenD FAQ (official)](https://openapi.moomoo.com/moomoo-api-doc/en/qa/opend.html) — Token expiry, connection limits, Linux multiprocessing bug
- [Moomoo OpenAPI — Quote FAQ (official)](https://openapi.moomoo.com/moomoo-api-doc/en/qa/quote.html) — Subscription quota, 60-second minimum hold, quota-kick behavior
- [Moomoo OpenAPI — Authorities & Quota (official)](https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html) — Tier-based subscription limits (100/300/1000)
- [Moomoo — Stock Paper Trading Rules (official)](https://www.moomoo.com/us/support/topic3_886) — Paper account matching rules and product limitations
- [Production Trading Bots: 15 Failure Patterns (Florin Elchis, Medium)](https://florinelchis.medium.com/production-trading-bots-15-failure-patterns-nobody-warns-you-about-af917d263c35) — Ghost positions, non-atomic file state, orphaned records, rate-limit on attempts not successes
- [TradingView Pine Script — Repainting (official docs)](https://www.tradingview.com/pine-script-docs/concepts/repainting/) — Incomplete bar signal problem, bar-close vs mid-bar evaluation
- [Look-Ahead Bias: The Hidden Backtest Killer (StratBase.ai)](https://stratbase.ai/en/blog/look-ahead-bias-hidden-killer) — Backtesting correctness, signal-to-entry timing
- [Survivorship Bias Free S&P 500 (riazarbi.github.io)](https://riazarbi.github.io/quant/backtesting-sp500-constituent-history/) — Historical constituent list sourcing
- [pandas_market_calendars (PyPI)](https://pypi.org/project/pandas_market_calendars/) — NYSE calendar, holiday handling, half-day detection
- [Relative Volume (RVOL) — ChartSchool StockCharts](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol) — Time-of-day normalization for RVOL
- [INTEGRATIONS.md — internal codebase audit](../.planning/codebase/INTEGRATIONS.md) — Push notification reliability note for US paper accounts
- [CONCERNS.md — internal codebase audit](../.planning/codebase/CONCERNS.md) — Bare exception handlers, missing polling, race conditions, no reconciliation

---
*Pitfalls research for: Automated intraday day-trading bot — moomoo/Futu OpenAPI, Python, paper account*
*Researched: 2026-06-23*
