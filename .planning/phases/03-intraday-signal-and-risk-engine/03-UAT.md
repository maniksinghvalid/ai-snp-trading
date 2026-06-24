---
status: testing
phase: 03-intraday-signal-and-risk-engine
source: [03-VERIFICATION.md]
started: 2026-06-24T16:17:14Z
updated: 2026-06-24T16:17:14Z
---

## Current Test

number: 1
name: End-to-End Signal and Intent Pipeline (Live Broker)
expected: |
  Start OpenD connected to a Moomoo SIMULATE account; run the bot's signal/risk
  pipeline against live intraday 5m bar data; wait for a closed bar that meets all
  three intraday filters (price above premarket high, above HOD, RVOL >= 2.0) within
  10:05–15:30 ET. Structlog shows `bar_closed`, `signal_emitted`, then
  `order_intent_emitted` for the same ticker; a row appears in `pending_intents` with
  `status=PENDING` and the correct `stop_price` and `quantity`; no position appears in
  the SIMULATE broker account.
awaiting: user response

## Tests

### 1. End-to-End Signal and Intent Pipeline (Live Broker)
expected: Structlog shows `bar_closed`, `signal_emitted`, then `order_intent_emitted` for the same ticker; a `pending_intents` row exists with `status=PENDING` and the correct `stop_price` and `quantity`; no position appears in the SIMULATE broker account.
result: [pending]

### 2. Premarket High Freeze at Market Open (Live Broker)
expected: Near 09:30 ET with the actual watchlist, `fetch_premarket_highs()` freezes only codes with a non-zero `pre_high_price`; codes missing/zero/NaN `pre_high_price` are excluded. Structlog shows `premarket_highs_fetched` with valid_count > 0 and `premarket_high_excluded` for any zero/NaN code; subsequent `on_bar()` uses the frozen highs for the I1 filter.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
