---
status: resolved
slug: paper-deal-list-unsupported
trigger: |
  Fill detection is broken on paper (SIMULATE). get_order_fills() calls the SDK's
  deal_list_query, which Futu paper trading does NOT support (live: ret=-1,
  data="Paper trading does not support deal data."). Both fill-detection sites use it:
  engine.py:397 (exit fills in manage_exit) and engine.py:252 (entry fills in
  consume_intent). Result: every exit crashes (force_close_exit_error, GatewayError:
  deal_list_query failed) and every entry would crash the same way. Since this milestone
  is paper-only, the entire trade loop's fill-confirmation is non-functional. Fix: detect
  fills via order_list_query's dealt_qty (the existing get_order_status() works on paper —
  empirically confirmed: order_list_query returns rows incl. dealt_qty/dealt_avg_price).
  Also fold in a one-time startup warning when the Telegram alerter is disabled
  (observability: currently only logged at DEBUG, invisible at INFO).
created: 2026-06-25
updated: 2026-06-25T22:00:00Z
---

## Symptoms

- expected: On a SIMULATE/paper account, after the bot places an exit order (force-close,
  stop-out, or partial scale-out) it polls for the fill, detects dealt quantity, updates
  remaining_quantity to broker truth, and (on full close) fires an exit alert. Entries
  likewise place an order and confirm the fill.
- actual: The fill-detection poll raises GatewayError immediately. force_close_all catches
  it as force_close_exit_error and the position is left un-confirmed. No exit completes; no
  exit alert fires. The same crash would hit entry fill detection in consume_intent.
- error_messages: |
  force_close_exit_error (warning) code=US.NVDY qty=26
    bot/position/manager.py:368 force_close_all → await self._engine.manage_exit(...)
    bot/execution/engine.py:397 manage_exit → fills = await self._gw.get_order_fills()
    bot/gateway/gateway.py:672 get_order_fills → _check_ret(ret, data, "deal_list_query")
    bot/gateway/gateway.py:204 _check_ret → raise GatewayError(...)
    GatewayError: deal_list_query failed: ret=-1, data=Paper trading does not support deal data.
- timeline: Surfaced 2026-06-25 19:51 UTC (15:51 ET force_close job) during live SIMULATE.
  Latent since the execution engine was built — deal_list_query was assumed to work on
  SIMULATE (docstrings even claim "Pitfall A/B" support), but it does not. Never worked
  end-to-end on paper.
- reproduction: |
  Empirically confirmed against the paper account (acc 1727266) via OpenSecTradeContext:
    deal_list_query(trd_env=SIMULATE, ...)  → ret != RET_OK, "Paper trading does not support deal data."
    order_list_query(trd_env=SIMULATE, ...) → ret == RET_OK, returns rows with columns incl.
                                              dealt_qty, dealt_avg_price.

## Suspected Root Cause (confirmed empirically)

Futu/Moomoo paper (SIMULATE) accounts do not support deal_list_query (no deal/execution
records). The execution engine detects fills exclusively via get_order_fills() →
deal_list_query at engine.py:252 (entry) and engine.py:397 (exit). On paper this always
raises GatewayError. The supported alternative is order_list_query (get_order_status()),
which returns per-order dealt_qty / dealt_avg_price and already works on paper (and is
already used at engine.py:161 for the duplicate-order pre-check).

## Evidence

- timestamp: 2026-06-25 — live log: force_close_exit_error with GatewayError
  "deal_list_query failed: ret=-1, data=Paper trading does not support deal data."
- timestamp: 2026-06-25 — direct probe: deal_list_query FAILS, order_list_query SUCCEEDS
  (5 rows, columns include dealt_qty, dealt_avg_price) on acc 1727266.
- timestamp: 2026-06-25 — engine.py uses get_order_fills() at lines 252 (entry) and 397
  (exit); get_order_status() (order_list_query) already exists in gateway.py and is used
  at engine.py:161.

## Eliminated

(none yet)

## Current Focus

status: FIXED — awaiting human verification
hypothesis_confirmed: deal_list_query (get_order_fills) is unsupported on SIMULATE.
  Both fill-detection sites in engine.py now use get_order_status(order_id) →
  order_list_query with cumulative dealt_qty. No cross-round double-count.
next_action: user confirms force_close_all no longer raises GatewayError on next
  market session, or inspects test results.

reasoning_checkpoint:
  hypothesis: "deal_list_query (called via get_order_fills()) is unsupported on SIMULATE.
    All fill detection must switch to order_list_query (get_order_status()) at both sites."
  confirming_evidence:
    - "Live probe: deal_list_query ret=-1 'Paper trading does not support deal data.'"
    - "Live log: force_close_exit_error with GatewayError from deal_list_query at engine.py:397"
    - "order_list_query works on SIMULATE — 5 rows returned including dealt_qty, dealt_avg_price"
    - "get_order_status() already used at engine.py:161 for duplicate guard — same call pattern"
  falsification_test: "After fix, test_paper_fill_exit_no_deal_list_query drives manage_exit
    with get_order_fills raising GatewayError — assert no raise, total_filled == 300"
  fix_rationale: "Replace the root cause (calling deal_list_query via get_order_fills) at
    both engine.py sites with order_list_query via get_order_status. Entry loop returns on
    first dealt_qty > 0 (no double-count risk). Exit loop reads cumulative dealt_qty for
    each placed order_id independently (different order_ids per outer iteration — CR-02)."
  blind_spots: "Real-account path: deal_list_query works on REAL. Since this milestone is
    SIMULATE-only, replacement is correct. A future REAL-account milestone would need to
    evaluate whether get_order_fills should be restored for REAL or whether order_list_query
    is also sufficient there."

## Resolution

root_cause: |
  Futu/Moomoo SIMULATE (paper) accounts do not support deal_list_query (ret=-1,
  "Paper trading does not support deal data."). ExecutionEngine._manage_entry_order
  (entry fill loop) and manage_exit (exit fill loop) both called
  self._gw.get_order_fills() → deal_list_query, crashing every fill poll on the
  only supported trading environment.

fix: |
  Replaced get_order_fills()/deal_list_query with get_order_status(order_id)/
  order_list_query at BOTH sites in bot/execution/engine.py.
  - Entry loop: polls get_order_status(order_id), reads cumulative dealt_qty from
    the matched row, returns FillEvent on first dealt_qty > 0 (no double-count risk
    — returns immediately on first detection). avg_fill_price read from dealt_avg_price.
  - Exit loop: polls get_order_status(order_id) per outer iteration (one order_id
    per outer loop), reads cumulative dealt_qty for that order_id, breaks on > 0.
    Each outer iteration's fill is independent (different order_ids) so total_filled
    accumulates without double-count (EXEC-05 / CR-02 preserved).
  Also: added one-time startup WARNING in bot/service/bot.py when TelegramAlerter
  is disabled (alerter._enabled is False), surfacing the silent disabled state at
  INFO-visible log level without changing the no-op send() behavior.

verification: |
  Full test suite: 440 passed, 1 skipped (was 437 passed, 1 skipped before fix).
  3 new regression tests lock the paper-fill path:
    - test_paper_fill_entry_no_deal_list_query: entry fill with get_order_fills raising
      GatewayError → no raise, FillEvent with correct dealt_qty
    - test_paper_fill_exit_no_deal_list_query: manage_exit with get_order_fills raising
      GatewayError → no raise, total_filled == 300
    - test_paper_fill_exit_partial_then_full: 300-share exit across 2 outer iterations
      (100 partial + 200 remainder) → total_filled == 300, no double-count

files_changed:
  - bot/execution/engine.py
  - bot/service/bot.py
  - tests/execution/test_engine.py

commits:
  - bd191a3: fix(engine): switch fill detection from deal_list_query to order_list_query
  - 81f6d73: feat(bot): emit one-time WARNING at startup when Telegram alerter is disabled

live_confirmation_pending: |
  True live confirmation — observing exit_fill_detected / force_close_filled (not
  force_close_exit_error) on the SIMULATE account — still awaits a market session with
  an open position at 15:51 ET. The 3 paper-fill regression tests stand in for it:
  they drive both fill paths against a gateway test-double whose deal_list_query raises
  GatewayError, proving the live crash path no longer executes. Independent re-check
  confirmed get_order_fills/deal_list_query appear only in comments and the now-unused
  gateway definition — never on any live fill-poll path. Startup alerter-disabled
  warning verified at bot/service/bot.py:804.
