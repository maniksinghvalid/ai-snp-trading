---
status: testing
phase: 11-multi-strategy-options-bot-bull-call-spread
source: [11-VERIFICATION.md]
started: 2026-09-24T17:10:54Z
updated: 2026-09-24T17:10:54Z
---

## Current Test

number: 1
name: Live paper run with both books registered and the bull-call scan reading the real watchlist
expected: |
  During RTH, after the equity bot's premarket scan has persisted daily_scan rows, start
  `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`.
  Startup log shows options_jobs_registered listing options_entry_scan_tasty_credit_spreads,
  options_entry_scan_tasty_credit_spreads_2, options_entry_scan_super_bull_call, options_manage, options_eod;
  at 10:05 ET a log line shows the watchlist read returning a non-empty code count (or a structured
  options_watchlist_empty / options_watchlist_unavailable warning if the equity scan hasn't run).
awaiting: user response

## Tests

### 1. Live paper run with both books registered and the bull-call scan reading the real watchlist
expected: options_jobs_registered lists the 5 job ids (3 entry scans, options_manage, options_eod); at 10:05 ET the bull-call scan logs the watchlist read (non-empty count, or a structured options_watchlist_empty/options_watchlist_unavailable warning)
result: [pending]

### 2. Developer decision on CR-02 (EX-02) before extended unattended live/paper operation
expected: A recorded decision — either a follow-up gap-closure plan (e.g. 11-08: propagate close_legs' unwind result out of open_position; a failed unwind lands NEEDS_ATTENTION + alert instead of ABORTED "legs unwound") is scheduled, or the residual risk (an entry-unwind failure can strand live legs outside BP/busy/reconcile visibility) is explicitly accepted
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
