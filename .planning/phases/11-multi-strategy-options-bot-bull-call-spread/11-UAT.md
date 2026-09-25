---
status: testing
phase: 11-multi-strategy-options-bot-bull-call-spread
source: [11-VERIFICATION.md]
started: 2026-09-24T17:10:54Z
updated: 2026-09-24T21:45:00Z
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
result: pass — operator decision 2026-09-24: schedule gap-closure plan 11-08 covering CR-02 + WR-06 + WR-07 (11-08-PLAN.md)

### 3. Developer decision on CR-03 (fill_leg leaves the working order live when a status poll raises)
expected: A recorded decision — schedule a follow-up gap-closure plan (e.g. 11-09: fill_leg cancels its working order on any exception after place_order; the UNWIND INCOMPLETE / close-error alerts warn that a bot-placed order may still be working) before relying on the "close manually" alert unattended, or explicitly accept the residual risk
result: pass — operator decision 2026-09-25: fold into gap-closure plan 11-09 (11-09-PLAN.md)

### 4. Developer decision on WR-10 / WR-11 (near-expiry escalation gaps introduced by 11-08)
expected: A recorded decision — fold into the same follow-up plan (WR-10: expiry-day escalation must not depend on one skippable APScheduler cycle; WR-11: a persistent snapshot outage must eventually count/alert), defer explicitly, or accept
result: pass — operator decision 2026-09-25: fold into gap-closure plan 11-09 (11-09-PLAN.md)

### 5. Developer decision on CR-04 (fill_leg's TTL path swallows a failed cancel, then places the next attempt)
expected: A recorded decision — schedule one narrowly-scoped fix (fill_leg raises instead of placing the next attempt when the TTL cancel failed and the order is not fully filled, so the 11-09 cancel-on-error path retries/audits it and callers escalate; replace test_cancel_failure_does_not_break_the_loop), or explicitly accept the residual risk (a clean ABORTED/CLOSED outcome can hide a live, untracked order that may fill into a naked short)
result: pass — operator decision 2026-09-25: fixed via quick task 260925-goi (commits 2cb1d0f, 7689f2b; .planning/quick/260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow/)

## Summary

total: 5
passed: 4
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps
