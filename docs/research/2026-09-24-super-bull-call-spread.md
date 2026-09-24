# Super Bull Call Spread (Options With Ravish) — strategy provenance — 2026-09-24

## Source

- Video: "Super Bull Call Spread: My Favorite Low Risk, High Reward Options Strategy"
- Channel: Options With Ravish
- Published: 2026-09-19, length 11:05
- URL: https://www.youtube.com/watch?v=VZ1MbM3UQ5Q
- Retrieval: auto-transcript pulled 2026-09-24 via the Apify `streamers/youtube-scraper`
  actor. The rules below are paraphrased from that transcript; the transcript itself is
  not stored in this repo.

## Distilled mechanical rules (as stated in the video)

- Buy a call around 30 delta; sell a call roughly 10 points further out of the money, same
  expiration. His example uses ~30 DTE, but he permits anywhere from 0DTE to 6 months.
- "1/4 rule": pay about 25% of the spread's width as the debit (e.g. $2.50 on a $10-wide
  spread, roughly 3:1 reward:risk); up to ~30% is acceptable, never more.
- "Position for zero": no stop loss and no adjustments — the debit paid is the entire risk,
  so size the trade to a loss you can accept.
- Management: either a fixed profit target (40-50% of max profit, for a higher win rate) or
  hold to +100% and sell half, letting the rest ride toward max profit (which needs the
  stock above the short strike into expiry, once theta turns favorable).
- Universe: high-momentum single names (his examples: NVDA, SNDK, TSM). The entry trigger is
  discretionary — he enters "when I'm super bullish" on a name.

## Worked example

NVDA 225/235 call spread: buy the 225 call at 3.58, sell the 235 call at 1.62.
Debit = 3.58 - 1.62 = 1.96. Width = 235 - 225 = 10. Max profit = 10 - 1.96 = 8.04 per
spread. Max loss = 1.96 x 100 = 196 per spread. debit/width = 0.196, which is <= 0.30, so
the 1/4-rule gate passes. At 1% of $100,000 risked, that sizes to 5 spreads
($1,000 / $196 = 5.10, floored).

## How the bot implements it

| Video rule | `super_bull_call` config key (value) | Code |
|---|---|---|
| ~30-delta long call | `structure.long_delta` (0.30) | `bot/options/strategy.py::_pick_bull_call` |
| ~10-point wing, same expiry | `structure.wing_width_pct_of_underlying` (4.5) + `structure.min_wing_width_usd` (2.0) | `_pick_bull_call` (`_pick_wing`) |
| 1/4 rule (~25%, up to ~30%) | `structure.max_debit_to_width` (0.30) | `_pick_bull_call` debit gate |
| ~30 DTE example | `entry.target_dte` (30), `min_dte` (21), `max_dte` (45), `prefer_monthly` (true) | `bot/options/strategy.py::pick_expiry` |
| Liquidity (implicit — "liquid, single names") | `entry.max_spread_pct_of_mid` (5.0), `max_spread_abs_usd` (0.05), `min_open_interest` (500) | `bot/options/strategy.py::leg_is_liquid` |
| Discretionary entry timing | `entry.entry_scan_et` ("10:05") | scan scheduling (service) |
| Position sizing | `sizing.max_risk_per_trade_pct` (1.0), `max_concurrent_positions` (4), `max_new_positions_per_day` (2) | `bot/options/strategy.py::size_debit_position` |
| Fixed profit target | `manage.profit_target_pct_of_max` (60) | `bot/options/strategy.py::manage_decision_debit` |
| No forced time exit | `manage.manage_dte` (null) | `manage_decision_debit` |
| Pin/assignment risk | `manage.assignment_guard_dte` (1) | `manage_decision_debit` |

## Deviations from the video

- **Entry trigger**: the video's discretionary "when I'm super bullish" call is replaced by
  the equity bot's Trend Join Long premarket watchlist (`universe_source: equity_watchlist`),
  read from `data/bot_state.db` (read-only), top 20 by rank, scanned at 10:05 ET. An empty or
  missing watchlist means zero entries that day — no discretionary override exists.
- **Management**: the bot always fully closes at 60% of max profit instead of the video's
  "hold to +100%, sell half, ride the rest." A sell-half runner needs partial-close quantity
  mutation across the store/reconcile/P&L path, which is deferred to a future phase.
- **Width**: 4.5% of the underlying price with a $2 floor, instead of a fixed ~10 points.
  This reproduces roughly $10 on a ~$220 stock (matching the worked example) while scaling
  sensibly to cheaper or pricier underlyings.
- **DTE window**: a 21-45 DTE window targeting 30, preferring monthlies, instead of the
  video's full 0DTE-6-month range.
- **Additions not in the video**: per-leg liquidity gates (spread %/absolute floor, open
  interest), a 1-DTE assignment guard close, at most one position per underlying across both
  the options books, and a shared global daily-loss breaker and buying-power cap with
  `tasty_credit_spreads`.
- **Unchanged from the video**: no stop loss — the debit paid is the whole risk, and sizing
  is the only risk control.

## Not validated

No backtest exists yet for this strategy. The Phase 9 options backtester replays credit
structures only and rejects `bull_call_spread` (a bull-call leg model and a watchlist replay
source are deferred future work). Live paper trading results are the first evidence for this
strategy's real-world behavior.
