---
slug: daily-summary-trade-count-cap
status: resolved
trigger: "Daily Summary 'Trades:' count and 'Realized PnL:' undercount on days with more than 20 closed trades. format_daily_summary derives n_trades = len(trades_rows) and realized_pnl = sum over trades_rows, but the caller (bot/service/bot.py EOD routine) feeds it StateStore.get_closed_trades(today) which runs SELECT * FROM trades WHERE DATE(closed_at)=? ORDER BY closed_at DESC LIMIT 20. A display-capped query is being reused for aggregation, so any day with >20 closed trades reports at most 20 trades and the PnL of only the most-recent 20."
created: 2026-06-30
updated: 2026-06-30
---

# Debug Session: daily-summary-trade-count-cap

## Symptoms

- **Expected behavior:** The Daily Summary "Trades: N (W/L)" and "Realized PnL: $X" should reflect ALL trades closed during the session date — full count and full summed realized PnL — regardless of how many there are.
- **Actual behavior:** Both metrics are derived from `get_closed_trades(today)`, which is capped at `LIMIT 20`. On a day with >20 closed trades the summary reports at most 20 trades and sums the realized PnL of only the 20 most-recent (`ORDER BY closed_at DESC`) trades. Wins/losses are likewise computed off the capped sample.
- **Error messages:** None — silent numeric undercount. No exception; the figures look valid.
- **Timeline:** Latent since `get_closed_trades` (display query) was reused by `format_daily_summary` for aggregation. Never surfaced because no live day has exceeded 20 closed trades yet (daily trade cap / low fill volume on paper). Sibling of the Open Risk stop-key bug in the same summary (resolved/open-risk-stop-key-bug.md, fixed in commit 79be4a5).
- **Reproduction:** Insert >20 rows into `trades` with `closed_at` on the same date, run the EOD report routine (bot/service/bot.py:692-705), and observe "Trades:" capped at 20 and "Realized PnL:" summing only the latest 20.

## Current Focus

- hypothesis: `StateStore.get_closed_trades(session_date)` (bot/state/store.py:340-362) ends with `ORDER BY closed_at DESC LIMIT 20` — a query shaped for a bounded display list. `format_daily_summary` (bot/service/alerter.py) consumes its full result as the authoritative trade set: `n_trades = len(trades_rows)`, `wins = sum(... r_multiple>0)`, `realized_pnl = sum((exit-entry)*qty)`. The 20-row cap therefore truncates count, W/L, and PnL on busy days.
- test: A test that seeds >20 closed trades for one date and asserts the summary's Trades count and Realized PnL reflect ALL of them (not 20). Must FAIL against current code (caps at 20) and PASS after the fix.
- expecting: Before fix — count == 20 and PnL == sum of latest 20. After fix — count == total and PnL == sum of all.
- next_action: Confirm the LIMIT 20 in get_closed_trades and that bot.py's EOD routine is the only summary caller; decide the fix shape (uncapped aggregate path vs SQL COUNT/SUM) without breaking any other consumer of get_closed_trades that relies on the 20-row cap; write the failing test; apply; run full suite.
- reasoning_checkpoint:
    hypothesis: "get_closed_trades (store.py:358) ends with LIMIT 20; format_daily_summary
      derives n_trades/wins/pnl from len(trades_rows)/iteration over the capped list;
      bot.py EOD passes that capped list as trades_rows — so >20 closed trades in a
      session yields a count of 20 and pnl of only the 20 most-recent rows."
    confirming_evidence:
      - "store.py:358 — SELECT * FROM trades ... LIMIT 20 — cap confirmed in code"
      - "alerter.py:199 — n_trades = len(trades_rows) (trusts list as complete)"
      - "alerter.py:203 — realized_pnl = sum over trades_rows (not SQL SUM)"
      - "bot.py:692 — passes get_closed_trades(today) as trades_rows"
      - "report.py:93 — also calls get_closed_trades; only for HTML display (bounded OK)"
    falsification_test: "Seed 21 trades in StateStore; if get_daily_trade_stats returns
      trade_count=21 and get_closed_trades returns 20 rows, the cap is confirmed; if
      both return 21 the hypothesis is wrong."
    fix_rationale: "Adding get_daily_trade_stats using SQL COUNT/SUM (no LIMIT) provides
      accurate aggregates without loading all rows; updating format_daily_summary to
      consume that dict (not trades_rows) breaks the dependency on the bounded list."
    blind_spots: "report.py also calls get_closed_trades — its display table will still
      show at most 20 rows (intentional); the HTML trade table in bot.py _build_daily_html
      similarly gets the bounded list (accepted design choice)."
- tdd_checkpoint:
    test_file: "tests/state/test_store.py::TestGetDailyTradeStats"
    test_name: "test_get_daily_trade_stats_returns_all_trades_beyond_limit_20 (+ 2 companions)"
    status: "green"
    failure_output: "AttributeError: 'StateStore' object has no attribute 'get_daily_trade_stats' (RED confirmed before fix)"

## Evidence

- timestamp: 2026-06-30 — bot/state/store.py:340-362 `get_closed_trades(session_date)` runs `SELECT * FROM trades WHERE DATE(closed_at) = ? ORDER BY closed_at DESC LIMIT 20`. Cap confirmed during the open-risk-stop-key-bug investigation.
- timestamp: 2026-06-30 — bot/service/alerter.py format_daily_summary derives n_trades = len(trades_rows), wins = count(r_multiple>0), realized_pnl = sum((exit_price-entry_price)*quantity) over the passed rows — it trusts the row list as complete.
- timestamp: 2026-06-30 — Caller bot/service/bot.py:692-705 (EOD report routine) passes `self._store.get_closed_trades(today)` as trades_rows. Verify whether any OTHER caller relies on get_closed_trades' 20-row cap (e.g. a recent-trades display) before changing its limit in place.

## Design considerations for the fix

- Do NOT silently widen get_closed_trades' LIMIT if another caller depends on the bounded list. Prefer either (a) a dedicated uncapped aggregate (e.g. get_daily_trade_stats returning count/wins/pnl via SQL COUNT/SUM), or (b) a separate uncapped fetch used only by the summary. SQL aggregation also avoids loading thousands of rows into memory on a busy day.
- Keep wins/losses, count, and realized PnL consistent — all three must come from the same uncapped source.
- Note: a daily trade cap exists elsewhere (daily_trade_count), but it bounds NEW entries, not closed-trade rows for a date, so it does not protect this path.

## Eliminated

## Resolution

root_cause: "get_closed_trades (store.py:358) ends with LIMIT 20 (a display cap).
  format_daily_summary was deriving n_trades/wins/pnl by iterating that capped list,
  so any session with >20 closed trades silently underreported count, W/L, and PnL."
fix: "1. Added StateStore.get_daily_trade_stats(session_date) using SQL COUNT/SUM
  (no LIMIT) — returns {trade_count, wins, losses, realized_pnl} from the database
  without loading rows into memory. 2. Changed format_daily_summary signature from
  (trades_rows, open_positions) to (trade_stats: dict, open_positions) — it now
  reads pre-aggregated values from the dict. 3. Updated bot.py EOD routine to call
  get_daily_trade_stats(today) and pass the result to format_daily_summary; the
  bounded get_closed_trades result is still used for the HTML display table.
  4. Updated alert_summary wrapper to match new signature."
verification: "TDD: 3 new tests in tests/state/test_store.py::TestGetDailyTradeStats
  confirmed RED before fix (AttributeError), GREEN after. Full suite: 477 passed,
  1 skipped, 0 failed (previously 474 tests — 3 new). No regressions."
files_changed:
  - bot/state/store.py
  - bot/service/alerter.py
  - bot/service/bot.py
  - tests/state/test_store.py
  - tests/service/test_alerter.py
  - tests/service/test_bot.py

live_verification: "2026-06-30 — Orchestrator seeded 21 closed trades (15 win / 6 loss)
  for one date in a throwaway StateStore and exercised the real methods: get_closed_trades
  returned 20 rows (display cap preserved, intentional); get_daily_trade_stats returned
  {trade_count:21, wins:15, losses:6, realized_pnl:90.0}; format_daily_summary(stats, [])
  rendered 'Trades: 21 (15W / 6L)' and 'Realized PnL: +$90.00'. Confirms the summary now
  reflects ALL trades, not the capped 20. Human-verify checkpoint satisfied."
