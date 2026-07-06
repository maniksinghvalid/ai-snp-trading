---
status: complete
phase: 07-strategy-optimization
source: [07-VERIFICATION.md]
started: 2026-07-04T00:00:00Z
updated: 2026-07-06T09:02:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Broker Stop-Market SIMULATE Support

expected: |
  With the bot running on SIMULATE and holding a test position:
  1. A protective stop order appears in the Moomoo/OpenD order list after a fill
     (if use_broker_stop_orders=true is correct for SIMULATE)
  2. OR no stop order appears and the bot's _on_quote quote-tick fallback is the
     active stop path (if SIMULATE ignores Stop-Market orders)

  Acceptance: confirm which path is active and set rules.json execution.use_broker_stop_orders
  accordingly (true = broker path, false = quote-tick fallback).

result: pass
confirmed: |
  Placed a probe Stop-Market SELL (OrderType.STOP, aux_price set) directly via the SDK against
  a held manual position (US.CLOV, 1 share, acc 1727266, SIMULATE) — same call shape as
  Gateway.place_stop_order. Broker returned an explicit synchronous rejection:
  "Paper trading does not support Stop order." No order was created (verified via order_list_query
  — only a pre-existing unrelated MARA fill was present). This is a definitive, non-ambiguous
  result: SIMULATE does not honor Stop-Market orders at all.
  Action taken: set execution.use_broker_stop_orders=false in rules.json (was true), so all stop
  invalidation now routes through the quote-tick fallback (_on_quote in bot/position/manager.py),
  which is the path proven to work on this broker environment.
  Note: the live bot process (PID 5586) was already running when this was tested and reads
  rules.json once at startup — it will need a restart to pick up this config change.

## Summary

total: 1
passed: 1
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

