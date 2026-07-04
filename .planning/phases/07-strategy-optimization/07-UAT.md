---
status: testing
phase: 07-strategy-optimization
source: [07-VERIFICATION.md]
started: 2026-07-04T00:00:00Z
updated: 2026-07-04T00:00:00Z
---

## Current Test

number: 1
name: Broker Stop-Market SIMULATE Support
expected: |
  On a live SIMULATE session with an open position, calling place_stop_order
  either executes as a Stop-Market order (in which case execution.use_broker_stop_orders=true
  is correct and the broker path is the active stop path), OR the SIMULATE account
  silently ignores/rejects it (in which case set execution.use_broker_stop_orders=false
  in rules.json to route all stop invalidation through the quote-tick fallback _on_quote).
  Both paths are fully implemented and tested — this test determines which is active.
awaiting: user response

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

result: [pending]

## Summary

total: 1
passed: 0
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
