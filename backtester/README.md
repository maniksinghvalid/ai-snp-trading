# Backtester — Trend Join Long offline replay

Replays the SAME production strategy pipeline (`TrendJoinLong` → `SignalEngine` →
`RiskEngine` → `PositionManager`) against historical 5m bars, with simulated
next-bar-open fills. `rules.json` (loaded by the same `bot/config` loader the
live bot uses) is the single source of strategy truth.

## Data sources

| Source | History depth | Setup |
|---|---|---|
| `yfinance` (default) | rolling ~60 calendar days of 5m bars | none |
| `massive` | years (per your massive.com plan) | API key, below |

### Configuring the Massive API key

1. Get a key at https://massive.com.
2. `cp .env.example .env` (repo root) and replace `MASSIVE_API_KEY=YOUR_API_KEY_HERE`
   with your real key, or `export MASSIVE_API_KEY=...` in your shell.
3. Never commit `.env` (it is gitignored). The key is sent only in an
   `Authorization: Bearer` header, never in a URL.

Downloaded bars are cached as CSV under `backtester/cache/` (yfinance) and
`backtester/cache/massive/` (Massive), so repeat runs cost zero API calls.
Free-tier Massive is rate-limited (~5 req/min); the first fetch of a large
symbol list is throttled automatically (HTTP 429 retry with backoff).

## Running a backtest

```bash
python3 -m backtester.run \
  --symbols US.AAPL,US.MSFT,US.NVDA \
  --start 2025-03-10 --end 2025-06-30 \
  --source massive \
  --starting-capital 100000 \
  --commission-per-share 0.005 \
  --slippage-usd 0.01 \
  --output-dir backtester/results/q2
```

Flags: `--source {yfinance,massive}`, `--interval` (5m only — the strategy FSM
is defined on 5m bars), `--starting-capital` (overrides `risk.sizing_equity_usd`
for sizing and reporting), `--commission-per-share`, `--slippage-usd`,
`--rules-json` (default `rules.json`).

## Output (in --output-dir)

- `summary.json` — overall + per-symbol metrics (also printed to stdout):
  total/annualized return, win rate, trade count, avg win/loss, max drawdown
  ($ and %), Sharpe, profit factor, exposure %, final portfolio value.
- `trades.csv` — per-trade record: code, opened_at, entry/exit price, quantity,
  exit_reason, r_multiple, closed_at.
- `equity_curve.csv` — end-of-day realized equity per NYSE trading day.

## Interpreting results

- **Zero trades is a common, valid outcome.** The strategy is highly selective
  (gap >= 3%, close > SMA200, RVOL >= 2.0, capped watchlist); most symbols on
  most days never qualify. Distinguish this from data problems: data gaps fail
  LOUDLY (`BacktestWindowError` naming the missing trading days).
- `avg_r_multiple` is gross (strategy R math); all dollar metrics are net of
  commissions; slippage is baked into fill prices.
- Sharpe/CAGR over short windows are noisy — treat sub-quarter values as
  indicative only.

## Assumptions and limitations

- Fills at the NEXT bar's open ± slippage (no intrabar fills; no look-ahead).
- Premarket highs are approximated from the data source's premarket 5m bars,
  not the live broker's `pre_high_price` tape.
- Commissions are applied in the report layer only — they do not feed back
  into position sizing or the daily circuit breaker during the replay.
- Equity curve is realized-only (valid because every position force-closes
  intraday at 15:51 ET).
- Replays the current `partial_be_trail` exit model only; 5m bars only.
- Backtest runs never touch the broker, the live state DB, or place orders.
