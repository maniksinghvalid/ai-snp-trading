---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
reviewed: 2026-10-04T21:10:00-07:00
iteration: 3
depth: standard
diff_range: 728ee7a..HEAD
files_reviewed: 9
files_reviewed_list:
  - bot/ibs/service.py
  - bot/ibs/execution.py
  - bot/ibs/store.py
  - scripts/uat_ibs_probe.py
  - deploy/com.bot.ibs.plist
  - deploy/IBS-RUNBOOK.md
  - tests/ibs/test_service.py
  - tests/ibs/test_service_entries.py
  - tests/ibs/test_operator_tooling.py
findings:
  critical: 0
  warning: 0
  info: 3
  total: 3
status: clean
---

# Phase 12: Code Review Report (iteration 3, final)

**Depth:** standard — **Diff:** `728ee7a..HEAD` (7 test/fix pairs plus docs) — **Status:** clean (no Critical or Warning findings; 3 Info items remain, one of which — IN-13 — was applied by the orchestrator in b1de276).

History: iteration 1 (`12-REVIEW.iter2.md`) found 2 critical / 6 warning / 5 info; iteration 2 (`12-REVIEW.iter3.md`) found 0 / 1 / 6 after the first fix pass; this pass found 0 / 0 / 3 after the second fix pass. Fix reports: `12-REVIEW-FIX.md` (iteration 2, with the iteration-1 table carried forward), `12-REVIEW-FIX.iter2.md` (iteration 1).

## Summary

All seven iteration-2 findings are closed; no regressions from the fixes.

Verification run: `python3 -m pytest -q` → **1715 passed, 1 skipped**; `plutil -lint deploy/com.bot.ibs.plist` OK (the new comment block has no `--` inside it); `grep -rnE "OrderType\.MARKET|force_close|unlock_trade|<script" bot/ibs` empty; `git diff --stat 5b8b4d2 -- bot/options` empty; no strategy number added to Python; no new `rules_ibs.json` knob (`ExitTimeOut` is launchd-only).

## Iteration-2 verdicts

| ID | Verdict | Evidence |
|----|---------|----------|
| WR-07 | CLOSED | `reconcile` (~L303-315) alerts once per code per ET date via meta key `ibs_unmanaged_alerted:<code>`; never trades or adopts the holding. Tests: `test_unmanaged_universe_holding_alerts_once_per_session` (startup → decision → second ET date), `test_aborted_entry_that_filled_alerts_and_is_never_traded`. "Entry not placed" wording now "no order confirmed ... check moomoo". |
| IN-10 | CLOSED | `_order_outcome` (L483-490) falls back to order `qty` only for `FILLED_ALL` with `dealt_qty` 0; `CANCELLED_PART`/`CANCELLED_ALL` keep reported `dealt_qty`; missing price → WR-04 limit fallback. Test: `test_entry_timeout_filled_all_with_lagging_dealt_qty_is_opened`. |
| IN-06 | CLOSED | `_flag_unknown` logs `exc_info=sys.exc_info()[0] is not None`; `_job_hard_cancel` passes `at_close=True` and its loop runs outside any `except`; alert says "verify position and orders in moomoo". |
| IN-07 | CLOSED | Both mid-order handlers (L558, L650) catch `(Exception, asyncio.CancelledError)`; CancelledError flags the row then re-raises; SystemExit/KeyboardInterrupt/GeneratorExit propagate with no await — row stays OPENING/CLOSING and startup reconcile flags it NEEDS_ATTENTION (parametrized test, SELL and BUY). |
| IN-08 | CLOSED | plist `ExitTimeOut` = 60, pinned by test; lint passes. |
| IN-09 | CLOSED | `_active_rows` prints the absolute DB path + "DB guard not applied" when absent; `None` with a note on `sqlite3.OperationalError` (widened to `DatabaseError` in b1de276); `_refusal` returns "IBS DB unreadable" when the file exists but rows are `None`; runbook says run from repo root. Tests cover wrong-cwd and unmigrated DB. |
| IN-11 | CLOSED | `_work` logs `ibs_order_settled_after_error` at error level when the exception is a `TimeoutError` and more than one order was placed (Python 3.14: `asyncio.TimeoutError` is the builtin). WR-07 alert backstops an order that was never recorded. |

## Regression probes (all negative)

- **WR-07 dedupe key:** keyed on `now_et().date()` per code; startup/decision/EOD on the same ET date share it (≤ 1 alert/day/code); a holding persisting across ET midnight alerts once on the new date (documented, intended). Rows flipped to NEEDS_ATTENTION earlier in the same `reconcile` are in the active set (`ACTIVE_STATUSES` includes NEEDS_ATTENTION; query runs after the flip loop) → no double alert.
- **Decision-path cost / WR-01 retry:** `TelegramAlerter.send` never raises (ALERT-04), 10 s timeout, only on the first alert per code per day; `get_meta`/`set_meta` cheap and lock-guarded; a WR-01 retry re-running `reconcile` finds the key set → no duplicate alert; reconcile exception path unchanged.
- **FILLED_ALL rule vs partial fills:** partial-then-cancelled arrives as `CANCELLED_PART`/`CANCELLED_ALL`, untouched by the rule; `FILLED_PART` is not terminal; sweep uses only `outcome[2] > 0` to choose DONE vs CANCELLED.
- **IN-07 and SystemExit:** non-cancellation BaseException skips the row flag by design; row flagged at next startup; nothing lost silently.
- **IN-09 vs old behaviour:** `--live-1lot` with an unreadable existing DB used to crash (fail closed); it now refuses explicitly — not weaker. Read-only probe is more tolerant, as intended.

## Info

### IN-12: Unmanaged-holding alert can fire once for a fully filled exit if the broker position read lags
**File:** `bot/ibs/service.py:303-315` — A just-CLOSED row is no longer active; if the broker read still shows the shares (settlement lag) at the EOD reconcile ~5 min after the close, one "unmanaged holding" alert fires. Unlikely; wording already says "check moomoo". **Fix:** none needed; if noisy, skip codes closed today.

### IN-13: Probe aborts on a non-OperationalError sqlite failure — APPLIED (b1de276)
**File:** `scripts/uat_ibs_probe.py:101` — now `except sqlite3.DatabaseError` (superclass; also covers "file is not a database"/"malformed").

### IN-14: The unreadable-DB note prints twice during `--live-1lot`
**File:** `scripts/uat_ibs_probe.py:124` — `_refusal` calls `_active_rows` again after the probe section already printed the note. Cosmetic. **Fix:** none required.

Previously deferred and not re-raised (non-blocking): IN-01 (anchor limits on bid/ask instead of `last` — calibrate with the RTH probe first), IN-03 (shutdown cosmetics), IN-04 (research-script hygiene; D-19 keeps the scripts unchanged).

---
_Reviewed: 2026-10-04 (iteration 3, final)_
_Reviewer: Claude (gsd-code-reviewer, sonnet, standard depth; transcribed by the orchestrator because the reviewer harness cannot write .md files)_
