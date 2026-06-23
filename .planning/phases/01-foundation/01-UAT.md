---
status: testing
phase: 01-foundation
source: [01-VERIFICATION.md]
started: 2026-06-23T18:46:24Z
updated: 2026-06-23T18:46:24Z
---

## Current Test

number: 1
name: End-to-end paper guard with a live OpenD instance
expected: |
  With OpenD running and logged into a SIMULATE (paper) account and PAPER_TRADING=true /
  FUTU_TRD_ENV=SIMULATE, MoomooGateway.connect() succeeds and assert_paper_account() passes
  via the real get_acc_list() → DataFrame → guard path. With a REAL account selected, or a
  flag/account-environment mismatch, connect() hard-fails with PaperGuardError BEFORE any
  order path is reachable, and a paper_guard_refusal entry is appended to the JSONL audit log.
awaiting: user response

## Tests

### 1. End-to-end paper guard with a live OpenD instance
expected: With OpenD running on 127.0.0.1:11111 logged into a SIMULATE account and PAPER_TRADING=true, MoomooGateway.connect() + assert_paper_account() pass against the real broker get_acc_list() path. With a REAL account or a flag/account mismatch, it hard-fails with PaperGuardError before any order path, and writes a paper_guard_refusal audit entry. (SAFE-01/SAFE-02)
result: [pending]

### 2. KillSwitch graceful shutdown under real OS signal delivery
expected: Running the kill switch inside a live asyncio event loop and sending SIGINT (Ctrl-C) — and separately touching the sentinel file — triggers exactly one clean shutdown: the shutdown Event is set, registered flush callback runs, a structured shutdown line is logged, and a shutdown entry is appended to the audit log, with no deadlock (RLock fix) and no double-trigger. (SAFE-04/SAFE-05)
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
