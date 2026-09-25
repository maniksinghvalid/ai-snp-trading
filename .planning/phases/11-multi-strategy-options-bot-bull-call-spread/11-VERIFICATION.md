---
phase: 11-multi-strategy-options-bot-bull-call-spread
verified: 2026-09-25T15:10:00Z
status: human_needed
score: 9/9 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 9/9
  gaps_closed:
    - "CR-03: fill_leg now cancels its own working order (shielded) on any exception or task cancellation after place_order returns an order_id, then re-raises the original exception; it never converts an exception into a return value. Confirmed by direct code read of execution.py:108-190 and by re-running the reviewer's own repro test (test_unwind_poll_error_cancels_the_unwind_order, passes)."
    - "WR-10: an expiry-day position that any manage cycle could not manage now gets a one-time 'Options expiry warning' alert (_warn_expiry_unmanaged, service.py:1273) independent of how many later cycles APScheduler drops; the automated assignment_guard close stays armed. Confirmed by direct read and by re-running test_expiry_day_unquotable_warns_before_cutoff_even_if_final_cycle_is_skipped and test_expiry_day_warning_keeps_the_automated_close."
    - "WR-11: a persistent snapshot outage now increments a process-level counter (self._snapshot_outage_cycles, service.py:1022-1046) and alerts once at the existing 3-cycle threshold, re-arming after any clean cycle. Confirmed by direct read and by re-running test_snapshot_outage_alerts_once_after_consecutive_cycles_and_rearms."
    - "IN-08: the near-expiry miss streak is now scoped to the ET session date (self._quote_miss_streak[pid] = (today, streak), service.py:1151); a day-before-expiry streak cannot escalate the first expiry-day miss. Confirmed by direct read and by re-running test_manage_near_expiry_streak_resets_on_a_new_session."
  gaps_remaining: []
  regressions: []
escalated_findings:
  - id: CR-04 (fresh finding, 11-REVIEW.md 11dfe7e, fourth review)
    disposition: "not gating this re-verification — a pre-existing Phase 8 fill_leg defect (execution.py:132-135, the TTL-path cancel_order wrapped in `except Exception: pass` — introduced in commit 7624ea9, before phase 11, byte-identical text before and after 11-09's wrapping try/except was added around it). Its consequence is real and reproduced (an uncancelled TTL-expiry order can remain live while fill_leg escalates to a NEW order on the next attempt; if the old order later fills, the account can hold an untracked naked short), and it sits in the exact function 11-09 just hardened for the adjacent (exception) path — but it is not introduced by phase 11 or by 11-09, does not violate the literal wording of any ROADMAP Success Criterion or the literal REQUIREMENTS.md MSO-07 text (which is specifically about the strategy_name column and the debit sign convention, not about cancel-confirmation), and resolving it is a scope/timing decision, not something further static reading can settle. Flagged for a human decision, following the same escalation-gate treatment CR-02 and CR-03 received in the prior two rounds. The operator has stated an intent to stop the phase-11 review/fix loop after 11-09 — this is surfaced as new information for that decision, not as a re-opening of the loop."
    evidence: "bot/options/execution.py:130-151 (TTL path): `try: await self._gw.cancel_order(order_id) / except Exception: pass` swallows any cancel failure; the subsequent `_poll` re-read, if it shows 0 dealt, falls through to `if attempt < cfg.max_retries:` which places a NEW order at an escalated price on the next loop iteration WITHOUT any confirmation that the previous order was actually cancelled. This logic is textually unchanged from commit 7624ea9 (Phase 8) — 11-09's diff added a wrapping try/except BaseException AROUND this block (for the exception path, CR-03) but did not touch the block's own content, confirmed by `git show a1f89d7:bot/options/execution.py` showing byte-identical TTL-cancel-swallow text. `git log --diff-filter=A --oneline -- bot/options/execution.py` shows the file (and this line) originates at 7624ea9, well before Phase 11. The existing (unmodified) test `test_cancel_failure_does_not_break_the_loop` (tests/options/test_execution.py:167-170) pins exactly this: with `cancel_order` always raising and `dealt=0`, `fill_leg` still returns `None` ('nothing filled') after exhausting retries, with no record that up to 3 prior orders may still be resting unconfirmed. The reviewer's reproduction (scratchpad/repro_ttl_cancel_swallow.py, not re-run by me — I verified the mechanism by code read and by the pinned test instead) shows the same pattern on the entry-unwind path producing a clean 'ABORTED' row while a live SELL order remains on the broker. The new super_bull_call book enters through this identical fill_leg code path (open_position and close_legs both call fill_leg for every leg of both strategies) — this is not tasty_credit_spreads-specific."
---

# Phase 11: Multi-strategy options bot (bull_call_spread) Verification Report — THIRD RE-VERIFICATION

**Phase Goal:** `rules_options.json` defines multiple option strategies in a `strategies` array and the ONE options-bot process runs all of them concurrently: the existing `tasty_credit_spreads` credit book (behavior unchanged) plus a new `super_bull_call` debit book — a bull call spread (~30Δ long call, short call one width higher, ≤30% of width debit, no stop, full close at a % of max profit), whose daily bullish universe is the equity bot's Trend Join Long premarket watchlist (read-only). Per-strategy sizing; global daily-loss breaker and BP cap.

**Verified:** 2026-09-25T15:10:00Z
**Status:** human_needed
**Re-verification:** Yes — after gap-closure plan 11-09 (commits ef38a74, 6a3d067, bcd1c0a), following a fourth code review (11dfe7e, `11-REVIEW.md`, status `issues_found`)

## Method

Not a rubber-stamp of 11-09-SUMMARY.md, 11-REVIEW.md (fourth review), or the prior 11-VERIFICATION.md's narrative. For every claim I read the actual current code myself: `bot/options/execution.py` in full (`fill_leg`'s new per-attempt try/except structure, its unchanged TTL-cancel block, `open_position`, `close_legs`), and the relevant `bot/options/service.py` sections (`_WORKING_ORDERS_HINT` and its 3 alert sites, `_warn_expiry_unmanaged`, the `_manage_once` snapshot-outage counter, `_manage_position`'s session-scoped streak). I independently re-ran the full suite (`python3 -m pytest -q` myself, not trusting the SUMMARY's or review's documented counts) and confirmed the exact `git diff --stat` scope of every file touched since the third-review baseline (06b6787). I traced the origin of the fourth review's new CR-04 finding via `git log --diff-filter=A` and `git show` to confirm it is pre-existing Phase 8 code, independent of the review's own narrative. Read: `11-09-PLAN.md` (in full, both pages), `11-09-SUMMARY.md`, `11-REVIEW.md` (fourth review, in full), the prior `11-VERIFICATION.md` (`human_needed`, CR-03/WR-10/WR-11 escalated), `11-UAT.md` (CR-03/WR-10/WR-11 decisions recorded as passed — folded into 11-09), `REQUIREMENTS.md` § Multi-Strategy Options (exact MSO-01..09 text), `ROADMAP.md` § Phase 11 (goal + exact 8 success-criteria wording), and `CLAUDE.md` Phase 8/11 invariants.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria 1–8, plus the derived safety truth and the 11-09 must-haves)

| # | Truth (ROADMAP SC / 11-09 must-have) | Status | Evidence |
|---|---------|--------|----------|
| 1 | `rules_options.json` ships in `strategies` shape with both strategies; one entry-scan job per strategy + one manage job + one EOD job | ✓ VERIFIED (regression-checked) | Unchanged by 11-09. `git diff 06b6787..HEAD --stat` touches only `bot/options/execution.py` and `bot/options/service.py`; `rules_options.json`, `schema.py`, `bot/main.py` byte-unchanged. |
| 2 | `load_options_config` unchanged flat contract; Phase 9 backtester/probe/tests pass unmodified | ✓ VERIFIED (regression-checked) | `config.py` untouched by 11-09. |
| 3 | `backtester.options_run` accepts `--strategy`; debit rejected with a clear error | ✓ VERIFIED (regression-checked, WR-04 caveat carried, deferred) | Unchanged file. |
| 4 | Pure-function tests prove bull-call strike selection, `max_debit_to_width` gate, sizing, `manage_decision_debit` math | ✓ VERIFIED (regression-checked) | `bot/options/strategy.py` untouched by 11-09. |
| 5 | Equity watchlist read via read-only SQLite URI, capped at 20 by rank; fail-closed | ✓ VERIFIED (regression-checked, WR-03 caveat carried, deferred) | `bot/options/universe.py` untouched by 11-09. |
| 6 | Positions carry `strategy_name`; debit positions store negative `credit_per_spread`; close math correct | ✓ VERIFIED (regression-checked) | `bot/state/migrations.py`, `bot/options/store.py` untouched by 11-09. |
| 7 | Per-strategy caps counted per strategy; daily-loss breaker, BP headroom, one-position-per-underlying are global | ✓ VERIFIED (regression-checked) | `_ACTIVE_STATUSES`/`_scan_and_open` logic untouched by 11-09 (confirmed: 11-09's diff is confined to `fill_leg` and the manage/alert sections of `service.py` listed below). |
| 8 | Safety invariants unchanged (LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only); full suite green | ✓ VERIFIED | I read `git diff -U0 06b6787..HEAD -- bot/options/execution.py` myself: hunks confined to the module docstring and `fill_leg`'s body (l.79-190); `_poll` (202-211), `open_position` (217-304) and `close_legs` (306-380) are byte-unchanged. `grep -c "self._gw.place_order" bot/options/execution.py` = 1 (single LIMIT-only order path). `close_legs` still buys back shorts before selling longs and blocks every long once a short is open (EX-01, unchanged). SAFE-OG-01 reconcile scope unaffected. **I independently re-ran the full suite myself: `python3 -m pytest -q` → 1345 passed, 1 skipped, 0 failed in 53.82s** — exact match to the SUMMARY's and fourth review's documented count. |
| 9 (11-09 must-have) | CR-03: `fill_leg` cancels (shielded) any order it placed on any exception/cancellation after `place_order`, then re-raises the ORIGINAL exception; never converts an exception into a return value | ✓ VERIFIED | Read `execution.py:108-190` directly: `except GeneratorExit: raise` then `except BaseException:` → `await asyncio.shield(self._gw.cancel_order(order_id))` (success → `leg_cancelled_on_error` warning; failure → `leg_cancel_on_error_failed` error log + audit event) → bare `raise`. Re-ran `test_unwind_poll_error_cancels_the_unwind_order`, `test_fill_leg_cancels_working_order_when_an_await_raises` (3 cases), `test_fill_leg_cancels_working_order_on_task_cancellation`, `test_fill_leg_cancel_failure_on_error_is_logged_audited_and_original_raised` directly — all pass. |
| 10 (11-09 must-have) | CR-03 hand-off alerts name leg codes and end with `_WORKING_ORDERS_HINT` | ✓ VERIFIED | `grep -c "_WORKING_ORDERS_HINT" bot/options/service.py` = 4 (definition line 97 + 3 alert sites: reconcile restart l.530, `_try_open` UNWIND INCOMPLETE l.927, `_manage_position` close-incomplete l.1234). Re-ran `test_incomplete_unwind_alert_warns_about_working_orders`, `test_reconcile_restart_alert_warns_about_working_orders` (2 cases), `test_manage_close_poll_error_cancels_the_order_and_flags_needs_attention` — all pass. |
| 11 (11-09 must-have) | WR-10: on expiry day, the first unmanageable manage cycle sends ONE expiry warning independent of later dropped cycles; no status change; automated close stays armed | ✓ VERIFIED | Read `_warn_expiry_unmanaged` (`service.py:1273-1301`), guarded by `self._expiry_warned` (a set, never cleared mid-process); called from the retry branch (`no_quote`, l.1167) and the outage branch (`snapshot_outage`, l.1063), both gated `dte <= 0`. Re-ran `test_expiry_day_unquotable_warns_before_cutoff_even_if_final_cycle_is_skipped` and `test_expiry_day_warning_keeps_the_automated_close` — both pass. |
| 12 (11-09 must-have) | WR-11: a persistent snapshot outage counts at the process level and alerts once at the 3-cycle threshold, re-arming on any clean cycle; never touches the per-position streak | ✓ VERIFIED | Read `_manage_once`'s snapshot loop (`service.py:1022-1046`): `self._snapshot_outage_cycles` reset to 0 on a clean cycle, incremented on any failed chunk, alert fires at `== _QUOTE_MISS_ESCALATE_CYCLES`. Re-ran `test_snapshot_outage_alerts_once_after_consecutive_cycles_and_rearms` and the deliberately-changed `test_manage_snapshot_outage_never_escalates_near_expiry` — both pass. |
| 13 (11-09 must-have) | IN-08: the near-expiry streak is scoped to one ET session; a day-before-expiry streak cannot escalate the first expiry-day miss | ✓ VERIFIED | `self._quote_miss_streak[pid] = (today, streak)` at `service.py:1151`, read against `today = now_et().date()` passed down from `_job_manage`. Re-ran `test_manage_near_expiry_streak_resets_on_a_new_session` — passes. |
| 14 (derived — the CR-01/Q-01/WR-07 safety truth, held since round 2) | Bull-call and credit positions are never marked or closed against an invalid/missing quote | ✓ VERIFIED — held | `_quote_ok`/`_quote_markable` gate selection unchanged by 11-09 (not in `git diff --stat` scope). |

**Score:** 9/9 ROADMAP Success Criteria + all 5 of 11-09's own must-have truths (CR-03, CR-03 alerts, WR-10, WR-11, IN-08) verified by direct code read and independent test re-run. No literal-wording violation of any ROADMAP Success Criterion or MSO requirement. One finding from the fourth review (CR-04) is escalated below as a human-decision item, not treated as failing any of the truths above — see explicit reasoning.

### Escalated Finding — reasoned disposition, not a phase-11 blocker (read this before trusting the score)

**CR-04 — the TTL-path `cancel_order` failure is still swallowed; a clean unwind/close can hide a live, untracked order on the broker**

I confirmed this by reading `bot/options/execution.py` directly, independent of the review's narrative:

- `fill_leg`'s TTL-expiry branch (`execution.py:130-151`, unchanged by 11-09) does: `try: await self._gw.cancel_order(order_id) / except Exception: pass    # already fully filled / already cancelled — swallow (engine parity)`. It then re-reads via `_poll`. If `dealt_qty == 0` it falls through to `if attempt < cfg.max_retries:`, which escalates the price and loops back to `order_id = await self._gw.place_order(...)` — placing a SECOND order — with no confirmation that the first order was actually cancelled versus the cancel call having simply failed (rate limit, OpenD timeout, broker rejection).
- I traced this line's history: `git log --diff-filter=A --oneline -- bot/options/execution.py` shows the file (and this exact swallow) originates at `7624ea9`, the Phase 8 commit that first wrote `LegExecutor`. `git show a1f89d7:bot/options/execution.py` (pre-Phase-11) shows byte-identical text for this block. 11-09's diff wraps a NEW `try/except BaseException` AROUND this block (for the adjacent exception path, CR-03) but does not alter the block's own content — confirmed both by direct read and by the plan's own acceptance-criteria awk check restricting the diff to `fill_leg`'s body.
- The existing, unmodified test `test_cancel_failure_does_not_break_the_loop` (`tests/options/test_execution.py:167-170`) pins exactly this behavior: with `cancel_order` always raising and `dealt=0`, `fill_leg` still returns `None` ("nothing filled") after exhausting retries — with no log, no audit, and no signal that up to `max_retries` prior orders may still be resting unconfirmed on the broker.
- The consequence, per the fourth review's reproduction (which I verified by mechanism, not by re-running the reviewer's scratchpad script): on the entry-unwind path, a failed TTL-cancel on a short leg can produce a clean `None` unwind result → the row is marked ABORTED ("legs unwound, position aborted") → but the un-cancelled short order can still be live on the broker with no tracking, no alert, and no reconcile visibility (ABORTED rows are excluded from reconcile's OPEN/OPENING/CLOSING scope by design). On the close path, an analogous failure can let a row be booked CLOSED while an earlier, uncancelled order for the same leg is still working.

**Reasoning on scope, stated explicitly:**
- The defect is in `fill_leg`'s TTL-path cancel-swallow, **byte-unchanged since Phase 8** (confirmed above) — it is not introduced by Phase 11 or by 11-09's own commits. This is the same class of pre-existing-defect disposition CR-03 received in the prior verification round, now applied to the adjacent code path in the same function.
- It does not violate the literal wording of ROADMAP Success Criterion 8 ("LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only; full suite green") — the order IS a LIMIT order, the ordering IS longs-first/shorts-first, SAFE-OG-01's scope is unaffected, and the full suite is green.
- It does not violate the literal REQUIREMENTS.md text of MSO-07 ("`option_positions.strategy_name` ... debit positions store negative `credit_per_spread` ... existing close math yields correct realized P&L") — that requirement is specifically about the strategy_name column and the debit sign convention, not about order-cancellation confirmation. (11-09-PLAN's own prose paraphrased a broader "restore MSO-07: a close whose status poll failed is never booked CLOSED" purpose statement that is not the literal REQUIREMENTS.md wording; I am verifying against the literal wording, per this verifier's mandate.)
- Unlike CR-02/CR-03, this was NOT scoped into 11-09's must-haves before this review ran — it is genuinely fresh, surfaced only after 11-09 shipped, when the fourth review examined the adjacent TTL-path code while confirming CR-03's fix. There has been no operator decision on it yet.
- The new `super_bull_call` book is NOT exempt: `open_position` and `close_legs` both call `fill_leg` for every leg of both strategies, so this exposure applies equally to both books, not just `tasty_credit_spreads`.
- Following the SAME escalation-gate methodology applied to CR-02 and CR-03 in the prior two rounds (surface prominently, do not silently accept, do not block a phase whose literal contract is satisfied and whose common-case behavior is test-covered and correct), I am marking this a **human-decision item, not a gaps_found BLOCKER** — resolving it is a scope/timing decision (a further follow-up plan, if the operator chooses to reopen the loop) rather than something further static reading can settle for me. I am also respecting the operator's stated intent (recorded in this task's instructions) to stop the review/fix loop after 11-09 — surfacing this clearly, with reasoning, is what lets that decision be an informed one rather than a silent gap.
- **Recommend:** before extended unattended live/paper operation of the multi-strategy bot, the operator should decide whether to (a) accept this as a residual risk (it requires a specific unlucky timing — a TTL-cancel failure, which shares the same rate-limit/OpenD-timeout causes already named for CR-03 — layered with a subsequent successful escalation-order fill or a subsequent poll showing 0 dealt), or (b) schedule one more narrowly-scoped fix (the review's proposed patch: raise instead of retry when the TTL cancel's success is unconfirmed and `dealt_qty` is still 0, routing the failure through the now-hardened CR-03 exception path instead of the silent swallow).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/options/execution.py` | `fill_leg`'s per-attempt `try`/`except GeneratorExit`/`except BaseException` wrapping everything after `place_order`; docstring "Guarantee (CR-03)" | ✓ VERIFIED | Read directly, lines 108-190; `_poll`, `open_position`, `close_legs` byte-unchanged (confirmed via `git diff -U0 06b6787..HEAD`). |
| `bot/options/service.py` | `_WORKING_ORDERS_HINT`; `_warn_expiry_unmanaged`; `self._snapshot_outage_cycles`; `self._quote_miss_streak` as `(date, int)` tuples | ✓ VERIFIED | All present at the lines the plan specifies; confirmed by direct read. |
| `tests/options/test_execution.py` | CR-03 regression tests including the reviewer's unwind repro | ✓ VERIFIED | `test_unwind_poll_error_cancels_the_unwind_order` and the other 5 CR-03 cases exist and pass. |
| `tests/options/test_service.py` | CR-03/IN-08/WR-10/WR-11 regression tests | ✓ VERIFIED | All 9 named new tests exist by name (`grep` confirmed) and pass. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `execution.py fill_leg`'s post-placement work | guaranteed cancel on any exception/cancellation | `except BaseException: await asyncio.shield(self._gw.cancel_order(order_id)); raise` | ✓ WIRED (gap closed — was CR-03, escalated last round) | Confirmed by direct read and independent test re-run. |
| `execution.py fill_leg`'s TTL-expiry path | guaranteed cancel confirmation before the next order is placed | *(none — the `except Exception: pass` swallow is unconfirmed)* | ✗ NOT_WIRED (CR-04, escalated, not gating — see above) | The module docstring's "Guarantee (CR-03)" paragraph claims every order is cancelled before the method returns or raises "unless it filled" — this holds for the exception path but not for a TTL-cancel whose failure is silently swallowed and then superseded by a new order placement. |
| `service.py _manage_position`/`_manage_once` | `_warn_expiry_unmanaged` | `await self._warn_expiry_unmanaged(pos, codes, reason, today)` gated by `dte <= 0` | ✓ WIRED | Confirmed at both call sites (l.1063, l.1167). |
| `service.py _manage_once` snapshot loop | process-level outage alert | `self._snapshot_outage_cycles == _QUOTE_MISS_ESCALATE_CYCLES` | ✓ WIRED | Confirmed at l.1022-1046. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full suite regression (re-run independently, not trusting SUMMARY/REVIEW) | `python3 -m pytest -q` | 1345 passed, 1 skipped in 53.82s | ✓ PASS (exact match) |
| CR-03 executor-level fix | `pytest -q tests/options/test_execution.py -k "cancels_working_order or cancel_failure_on_error or unwind_poll_error"` | all pass | ✓ PASS |
| CR-03 service-level alerts | `pytest -q tests/options/test_service.py -k "warns_about_working_orders or close_poll_error"` | all pass | ✓ PASS |
| WR-10 / expiry warning | `pytest -q tests/options/test_service.py -k "expiry_day_unquotable or expiry_day_warning"` | all pass | ✓ PASS |
| WR-11 / snapshot outage | `pytest -q tests/options/test_service.py -k "outage_alerts_once or expiry_day_snapshot_outage or snapshot_outage_never"` | all pass | ✓ PASS |
| IN-08 / session-scoped streak | `pytest -q tests/options/test_service.py -k "streak_resets"` | all pass | ✓ PASS |
| CR-04 pinned-unsafe-behavior confirmation (not fixed by 11-09; existing test still encodes it) | Direct code read of `execution.py:130-151` + re-read of the UNMODIFIED `test_cancel_failure_does_not_break_the_loop` | Confirmed: `fill_leg` returns `None` after a TTL-cancel failure with `dealt=0`, with no log/audit/signal that a prior order may still be live | ✗ CONFIRMED DEFECT, not gating (escalated) |

### Probe Execution

No conventional `scripts/*/tests/probe-*.sh` for this phase. `scripts/uat_options_probe.py` is explicitly manual/live-only (RTH + OpenD). Step 7c: SKIPPED (no offline-runnable probes), same as all four prior verification rounds.

### Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|-----------------|--------|----------|
| MSO-01 | 11-01 | ✓ SATISFIED | Unchanged by 11-09; re-confirmed via regression tests. |
| MSO-02 | 11-01, 11-04 | ✓ SATISFIED | Unchanged by 11-09. |
| MSO-03 | 11-01 | ✓ SATISFIED | Unchanged by 11-09. |
| MSO-04 | 11-02, 11-05 | ✓ SATISFIED | `strategy.py` byte-unchanged by 11-09. |
| MSO-05 | 11-02, 11-06, 11-07, 11-08, 11-09 | ✓ SATISFIED — strengthened | An expiry-day debit position that cannot be managed now gets an operator alert on its first unmanageable cycle (WR-10), not silently skipped; the automated assignment_guard close stays armed. CR-04 (escalated) is a distinct, adjacent risk in the shared order-placement engine, not in `manage_decision_debit`'s check ordering or exit logic — MSO-05's literal text ("exits in order assignment guard → profit target → optional DTE exit; no stop loss") is unaffected. |
| MSO-06 | 11-03, 11-05 | ✓ SATISFIED (WR-03 caveat carried, deferred) | Unchanged by 11-09. |
| MSO-07 | 11-03, 11-06, 11-07, 11-08, 11-09 | ✓ SATISFIED | Literal text ("`strategy_name` idempotent migration; debit positions store negative `credit_per_spread`; existing close math yields correct realized P&L") — unaffected by CR-04, which concerns order-cancellation confirmation in the shared execution engine, not the strategy_name column or the close-math sign convention. |
| MSO-08 | 11-05, 11-06, 11-07, 11-08, 11-09 | ✓ SATISFIED | Per-strategy caps, global breaker, global BP headroom and one-position-per-underlying computation unchanged by 11-09 (not in diff scope); alerts continue to name strategy and codes, now with the added `_WORKING_ORDERS_HINT` and expiry/outage alerts. |
| MSO-09 | 11-02, 11-04, 11-06 | ✓ SATISFIED | Unchanged by 11-09. |

No orphaned requirements: all 9 MSO-01..09 IDs appear in at least one plan's `requirements:` frontmatter (including 11-09's `[MSO-05, MSO-07, MSO-08]`) and in `REQUIREMENTS.md` § Multi-Strategy Options, which marks all 9 "Phase 11 / Complete."

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/options/execution.py` | TTL-expiry cancel, l.132-135 | `except Exception: pass` swallows a cancel failure before the loop escalates to a NEW order placement with no confirmation the old one is gone (CR-04, escalated, not gating — see above; pre-existing Phase 8 code) | 🛑 Critical (escalated, not gating this re-verification) | A silent TTL-cancel failure can leave a resting order unconfirmed while the loop places another; on the entry-unwind path this can hide behind a clean 'ABORTED' outcome, on the close path behind a clean 'CLOSED' outcome — both outside reconcile's later visibility. |
| `bot/options/service.py` | `self._snapshot_outage_cycles`, l.1022-1046 | Not session-scoped and not reset on an empty book (IN-12, new, carried/deferred) | ℹ️ Info | Two effects, both fail toward the human per the review: an early alert if two outage cycles happen close to a session boundary, or a missed alert on day 2 of a multi-day outage (the expiry-day warning still covers the expiry-day case). |
| `bot/options/service.py` | `_warn_expiry_unmanaged` and its outage-branch call site, l.1062-1063, 1273-1300 | Wording says "expires today" even at `dte < 0`; the outage branch's `date.fromisoformat`/warn call sits outside the per-position `try` (IN-13, new, carried/deferred) | ℹ️ Info | Low risk: rows are bot-written, so a malformed expiry is unlikely; the wording issue is cosmetic. |
| `bot/options/service.py` | ~919-928 | The CR-02 NEEDS_ATTENTION alert lists every original leg code, not which legs are actually still open (IN-09, carried/deferred) | ℹ️ Info | Operator visibility gap; unchanged. |
| `bot/options/universe.py` | 87 | No tie-breaker on `ORDER BY rank ASC` (WR-03, carried/deferred) | ⚠️ Warning | Unchanged across all four verification rounds. |
| `bot/options/config.py` | ~514, `options_run.py` ~229-230 | `legacy_view` unvalidated (WR-04, carried/deferred) | ⚠️ Warning | Unchanged. |
| `bot/options/service.py` | ~838-848 | Entry premium recorded at pre-trade mid, not fill (WR-02, carried/deferred) | ⚠️ Warning | Unchanged. |
| `bot/options/service.py` | ~1214-1216 | A partial short buy-back is recorded as fully CLOSED (WR-08, carried/deferred) | ⚠️ Warning | Unchanged; CR-04's close-path scenario compounds this same class of risk. |
| `bot/options/service.py` | ~745-756 | NEEDS_ATTENTION rows permanently consume BP headroom and a concurrent slot with no operator-facing resolve path (WR-09, carried/deferred) | ⚠️ Warning | Unchanged; 11-09 does not add new NEEDS_ATTENTION paths (`set_position_status(pid, "NEEDS_ATTENTION")` count stays 5, confirmed). |
| various | — | IN-01..IN-07, IN-10, IN-11, EX-03 (carried, deferred) | ℹ️ Info | Unchanged; cosmetic/latent, not phase blockers. |

No unresolved `TBD`/`FIXME`/`XXX` debt markers found in `bot/options/execution.py` or `bot/options/service.py` (confirmed via direct grep).

### Human Verification Required

#### 1. Live paper run with both books registered and the bull-call scan reading the real watchlist

**Test:** During RTH after the equity bot's premarket scan has persisted `daily_scan` rows for the day, start `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`.
**Expected:** Startup log shows `options_jobs_registered` listing `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`; at 10:05 ET a log line shows the watchlist read returning a non-empty code count (or a structured `options_watchlist_empty`/`options_watchlist_unavailable` warning if the equity scan hasn't run).
**Why human:** Requires OpenD logged in, RTH, a real populated equity watchlist, and a running live process — this verification agent must not start the bot or connect to OpenD. Unchanged and still pending across all four verification rounds (11-UAT.md item 1).

#### 2. Developer decision on CR-04 (TTL-path cancel-swallow can leave a resting order behind a clean ABORTED/CLOSED outcome)

**Test:** Not a runnable check — a scope/risk-acceptance decision. Review the Escalated Finding section above (and `11-REVIEW.md`'s CR-04 section) and decide whether to accept the residual risk given the stated intent to stop the phase-11 review/fix loop after 11-09, or to schedule one more narrowly-scoped follow-up fixing the specific TTL-cancel-confirmation gap (routing an unconfirmed TTL-cancel through the now-hardened CR-03 exception path instead of silently escalating to a new order).
**Expected:** A recorded decision — either a new follow-up plan is scheduled, or an override/acceptance is added to this or a future VERIFICATION.md.
**Why human:** Same class of decision as CR-02's and CR-03's disposition in the prior two rounds — a pre-existing defect whose consequence is real and reproduced, but whose resolution is a scope/timing/risk-tolerance judgment, not something further static analysis can resolve unilaterally. This is presented as new information for the operator's already-stated intent to stop the loop, not as a demand to reopen it.

### Gaps Summary

CR-03, WR-10, WR-11 and IN-08 (the prior round's four escalated findings, folded into plan 11-09 by operator decision on 2026-09-25) are now closed. I confirmed each fix by direct code read plus independent test re-run, not by trusting 11-09-SUMMARY.md or the fourth review's narrative: `fill_leg` cancels (shielded) any order it placed on any exception or task cancellation after `place_order`, then re-raises without ever converting the exception into a return value; three hand-off alerts now name leg codes and carry `_WORKING_ORDERS_HINT`; an expiry-day position that any manage cycle cannot manage gets a one-time warning independent of dropped cycles, with the automated close staying armed; a persistent snapshot outage now alerts once at the process level and re-arms after any clean cycle; and the near-expiry miss streak is now scoped to the ET trading session. I independently re-ran the full suite (1345 passed, 1 skipped, 0 failed) and every named regression subset, and every result matches the SUMMARY's and fourth review's documented counts exactly.

A fresh (fourth) code review (11dfe7e) surfaced one new Critical finding, CR-04, in the adjacent TTL-expiry cancel path of the exact function 11-09 just hardened for the exception path. I confirmed it by direct code reading and by tracing its git history: the swallow (`except Exception: pass` around the TTL `cancel_order` call, followed by an unconfirmed fall-through to placing a new order) is byte-unchanged Phase 8 code (commit 7624ea9), not introduced by phase 11 or by 11-09, and it is pinned — not merely hypothesized — by the existing, unmodified test `test_cancel_failure_does_not_break_the_loop`. It does not violate the literal wording of ROADMAP Success Criterion 8 or the literal REQUIREMENTS.md text of MSO-05/MSO-07/MSO-08, and none of the 14 observable truths established above fail as a result. It affects both strategy books equally, since both route every leg through the same `fill_leg`.

Following the same escalation-gate methodology applied to CR-02 and CR-03 in the prior two rounds, I am marking CR-04 as a human-decision item, not a gaps_found BLOCKER — because it is pre-existing rather than introduced by this phase's own commits, because it does not violate the literal roadmap/requirements contract, and because resolving it is a scope/risk-tolerance decision, not a code-correctness question I can resolve unilaterally. I am not silently accepting it either: it is surfaced above with concrete evidence (the exact lines, the git-history trace, and the pinning test), explicitly so the operator's stated intent to stop the review/fix loop after 11-09 is an informed decision rather than a blind one.

---

_Verified: 2026-09-25T15:10:00Z_
_Verifier: Claude (gsd-verifier)_
