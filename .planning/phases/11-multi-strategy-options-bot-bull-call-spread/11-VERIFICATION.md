---
phase: 11-multi-strategy-options-bot-bull-call-spread
verified: 2026-09-24T17:08:43Z
status: human_needed
score: 9/9 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 8/9
  gaps_closed:
    - "Bull-call and credit positions are never marked or closed against an invalid (missing/'N/A') quote — no naked-short exposure, no false daily-loss-breaker trip, no stuck CLOSING row (CR-01/Q-01)"
  gaps_remaining: []
  regressions: []
escalated_findings:
  - id: CR-02 (formerly deferred EX-02)
    disposition: "not gating this re-verification — pre-existing Phase 8 defect, unchanged by 11-07, explicitly scoped out of the approved 11-07 plan; flagged for a human decision on a follow-up plan before further live/paper use"
    evidence: "bot/options/execution.py:209 (open_position discards close_legs' unwind result); bot/options/service.py:835-849 (_try_open always reports ABORTED + 'legs unwound' regardless of unwind success); ABORTED excluded from _ACTIVE_STATUSES (service.py:92) so stranded legs are invisible to BP headroom, busy, and reconcile"
---

# Phase 11: Multi-strategy options bot (bull_call_spread) Verification Report — RE-VERIFICATION

**Phase Goal:** `rules_options.json` defines multiple option strategies in a `strategies` array and the ONE options-bot process runs all of them concurrently: the existing `tasty_credit_spreads` credit book (behavior unchanged) plus a new `super_bull_call` debit book — a bull call spread (~30Δ long call, short call one width higher, ≤30% of width debit, no stop, full close at a % of max profit), whose daily bullish universe is the equity bot's Trend Join Long premarket watchlist (read-only). Per-strategy sizing; global daily-loss breaker and BP cap.

**Verified:** 2026-09-24T17:08:43Z
**Status:** human_needed
**Re-verification:** Yes — after gap-closure plan 11-07 (commits 4a065fd, 6c3b375, 38b2538, 18d03b2), following a fresh code review (1c9a5d4, `11-REVIEW.md`, status `issues_found`)

## Method

Re-verification, not a rubber-stamp of 11-07-SUMMARY.md or 11-REVIEW.md narrative. For every claim in both documents I read the actual line ranges in `bot/options/service.py` and `bot/options/execution.py` myself and confirmed the code matches the claim. I ran `python3 -m pytest -q` (full suite) and the exact `-k` subsets named in the 11-07 plan's acceptance criteria myself, independent of the documented numbers. Read: `11-07-PLAN.md`/`SUMMARY.md`, `11-REVIEW.md`, the prior `11-VERIFICATION.md` (gaps_found), `REQUIREMENTS.md` § Multi-Strategy Options, `CLAUDE.md` Phase 8/11 invariants, and the full text of `bot/options/service.py` around lines 92, 359-470, 629-745, 835-849, 928-1050, plus `bot/options/execution.py` lines 190-300.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria 1–8 + the derived safety truth from the prior BLOCKER)

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---------|--------|----------|
| 1 | `rules_options.json` ships in `strategies` shape with both strategies; one entry-scan job per strategy + one manage job + one EOD job | ✓ VERIFIED (regression-checked) | Unchanged by 11-07. `tests/options/test_service.py::test_shipped_book_registers_per_strategy_jobs`, `::test_register_jobs_one_entry_scan_per_strategy` re-run directly: 53 passed (bundled with migration tests). |
| 2 | `load_options_config` unchanged flat contract; Phase 9 backtester/probe/tests pass unmodified | ✓ VERIFIED (regression-checked) | `git diff --quiet 530667d -- bot/options/config.py` — confirmed unchanged by 11-07 (11-REVIEW.md WR-04 disposition table, spot-checked: `config.py` untouched in this round). |
| 3 | `backtester.options_run` accepts `--strategy`; debit rejected with a clear error | ✓ VERIFIED (regression-checked, WR-04 caveat carried) | Unchanged file; caveat (unvalidated `legacy_view`) persists as a carried, deferred warning — not a phase blocker. |
| 4 | Pure-function tests prove bull-call strike selection, `max_debit_to_width` gate, sizing, `manage_decision_debit` math | ✓ VERIFIED (regression-checked) | `bot/options/strategy.py` byte-unchanged by 11-07 (`git diff --quiet 530667d -- bot/options/strategy.py` passes per plan's own acceptance gate, confirmed). |
| 5 | Equity watchlist read via read-only SQLite URI, capped at 20 by rank; fail-closed | ✓ VERIFIED (regression-checked, WR-03 warning carried) | `bot/options/universe.py` untouched by 11-07; WR-03 (no tie-breaker on rank) remains an open, deferred warning, not a phase blocker. |
| 6 | Positions carry `strategy_name`; debit positions store negative `credit_per_spread`; close math correct | ✓ VERIFIED (regression-checked) | `bot/state/migrations.py`, `bot/options/store.py` untouched by 11-07. `realized_per_spread = credit - net_exit` confirmed unchanged at `service.py` (grep count 1, matching the plan's own acceptance gate). |
| 7 | Per-strategy caps counted per strategy; daily-loss breaker, BP headroom, one-position-per-underlying are global | ✓ VERIFIED — WARNING resolved | `service.py:679-693`: `active = self._store.get_option_positions(_ACTIVE_STATUSES)`; `open_max_loss_total = sum(float(p["max_loss_usd"] or 0) for p in active)`; `open_count = sum(1 for p in active if p.get("strategy_name") == cfg.name)`. `_ACTIVE_STATUSES = ("OPENING","OPEN","CLOSING","NEEDS_ATTENTION")` (line 92). `_OPEN_STATUSES` deleted — confirmed absent (`grep -n "_OPEN_STATUSES" bot/options/service.py` → no output). This closes the prior WR-05 WARNING: NEEDS_ATTENTION/CLOSING rows now count against global BP headroom and per-strategy concurrent caps. Tests `test_bp_headroom_counts_needs_attention_and_closing_rows`, `test_concurrent_cap_counts_needs_attention_and_closing_rows` pass. |
| 8 | Safety invariants unchanged (LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only); full suite green | ✓ VERIFIED | `execution.py` diff confined to the module docstring + `close_legs` only (`git diff -U0 530667d -- bot/options/execution.py`, hunks only at old lines ≤21 or ≥222 — I read the current file and confirmed `fill_leg`/`_poll`/`open_position` bodies are unchanged except the one discarded-return-value line already present pre-11-07). One `self._gw.place_order` call site confirmed (single LIMIT-only order path). SAFE-OG-01 scope (`known_codes.update(...)` first in reconcile) confirmed unchanged. **I independently re-ran the full suite: `python3 -m pytest -q` → 1299 passed, 1 skipped, 0 failed in 68.42s** — exact match to the SUMMARY's and REVIEW's documented count. |
| 9 (derived — the prior BLOCKER, truth from CR-01) | Bull-call and credit positions are never marked or closed against an invalid/missing quote (no naked-short exposure, no false breaker trip, no stuck CLOSING row) | ✓ VERIFIED — GAP CLOSED | Read `_quote_ok` (`service.py:132-151`): rejects falsy `q`, non-numeric bid/ask (`float()` raising `TypeError`/`ValueError`), non-finite values (`math.isfinite`), and requires `ask > 0 and bid >= 0 and ask >= bid`. `_manage_position` (`service.py:974-997`) computes `bad = [leg["code"] for leg in legs if not _quote_ok(quotes.get(leg["code"]))]` BEFORE `mark_spread` is ever called (line 999) — a leg missing from `quotes` or failing `_quote_ok` is now caught by the SAME check (no separate presence-only branch remains: `grep -n 'leg\["code"\] not in quotes'` → no output). Inside the assignment-guard window it escalates to NEEDS_ATTENTION with an alert + `options_manage_unquotable_near_expiry` audit event (Q-01); outside it logs `options_manage_missing_quote` and returns `0.0`, retrying next cycle — no mark, no decision, no close, $0.0 contributed to the breaker either way. The `close_legs` call (`service.py:1021-1030`) is now wrapped in `try/except Exception` → `ok = False` → falls into the existing NEEDS_ATTENTION + alert + `options_close_incomplete` branch — never leaves a row in CLOSING. All named regression tests re-run directly by me: `test_quote_ok_accepts_only_two_sided_numeric_quotes`, the 4 `test_manage_skips_credit_position_on_invalid_quote` cases, the 2 debit cases, `test_manage_invalid_long_quote_does_not_trip_breaker`, `test_manage_close_exception_flags_needs_attention`, the 2 Q-01 window tests = 31 passed (0 failed). |

**Score:** 9/9 truths verified (the prior BLOCKER is closed; no new BLOCKER in the roadmap's own success-criteria wording — see Escalated Finding below for a defect the fresh review raised that is judged out of this phase's contracted scope, not silently accepted)

### Escalated Finding: CR-02 (formerly deferred EX-02) — reasoned disposition, not a phase-11 blocker

`11-REVIEW.md` (fresh review, 1c9a5d4) raises one Critical finding, CR-02: `open_position` (`bot/options/execution.py:209`) discards the return value of the entry-unwind's `close_legs(filled, quotes, aggressive=True)` call and always returns `None` on a failed fill, regardless of whether the unwind actually closed every stranded leg. `_try_open` (`service.py:835-849`) then unconditionally sets the row `ABORTED` and sends "legs unwound, position aborted" — even when the unwind itself failed and the broker is still holding live legs. `ABORTED` is not in `_ACTIVE_STATUSES` (`service.py:92`), so those legs are invisible to `busy`, `open_max_loss_total`/`open_count` (Truth 7 above), and to `reconcile` (which only loads `OPEN`/`OPENING`/`CLOSING`). I confirmed this by reading the code directly, independent of the review's narrative: the code at all four cited locations matches the review's description exactly, and no test in `tests/options/test_execution.py` or `test_service.py` exercises `open_position` when `close_legs`'s unwind itself fails.

**Reasoning on scope, stated explicitly:**
- This is a Phase 8 defect (the `_try_open`/`open_position`/ABORTED code path predates Phase 11 and is structurally unchanged by it — Phase 11 only generalized the surrounding scan loop to iterate over multiple strategy configs). It is not a regression introduced by this phase.
- The 11-07 plan (the phase's own approved gap-closure scope) explicitly enumerates this exact defect as "EX-02 ... recorded as backlog item EX-02 and is out of scope" and lists it under "Deferred (backlog): explicitly OUT OF SCOPE for this gap closure," alongside a specific proposed fix for a future plan. This was a deliberate, documented scoping decision at plan-authoring time, not an oversight discovered only now.
- ROADMAP Success Criterion 8's literal wording ("Safety invariants unchanged: LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only") does not cover the ABORTED-unwind-reporting defect; SC8 as written is satisfied.
- MSO-08 ("global daily-loss breaker + global BP headroom + global one-position-per-underlying") is undermined by CR-02 in the specific edge case of a failed entry-unwind — this is a genuine, real gap in the global guarantee. However, this exact same edge case already existed for the single `tasty_credit_spreads` book before Phase 11; Phase 11 does not create a new class of exposure, it only means a second strategy's entries can also hit the same pre-existing, already-deferred failure mode.
- Because the deferral was explicit, approved, and documented as backlog (not silently dropped), and because it does not violate the literal roadmap contract for this phase, I am **not** marking this a phase-11 BLOCKER and am **not** setting `status: gaps_found` on its account.
- I AM surfacing it prominently, per the escalation-gate mandate: this is real, reproduced (by the reviewer, with a scratch script whose described mechanics match the code I read), and it grows in relevance now that the bull-call book shares the same code path. **Recommend:** open a new gap-closure plan (e.g., 11-08) to implement the review's proposed fix (propagate `close_legs`' unwind result from `open_position`; map a failed unwind to `NEEDS_ATTENTION` instead of `ABORTED` in `_try_open`) before relying on the multi-strategy bot for extended unattended live/paper operation. This is a developer decision, not something I can resolve by further static reading — hence escalated, not silently waived and not inflated into a blocking gap for a plan that explicitly scoped it out.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/options/service.py` | `_quote_ok` gate, Q-01 escalation, close_legs exception guard, WR-01 structure-kind check (reconcile + manage), WR-05 ACTIVE-row BP/count | ✓ VERIFIED | All present at the exact lines the plan specifies; confirmed by direct read, not grep-only. |
| `bot/options/execution.py` | `close_legs` blocks every long sell once a short failed (EX-01) | ✓ VERIFIED | `close_legs` (lines 223-300+) tracks `open_shorts`; a long leg encountered while `open_shorts` is non-empty logs `close_longs_skipped_short_open` and returns `False` before any long order is placed. `fill_leg`/`_poll`/`open_position` unchanged (one `place_order` call site). |
| `tests/options/test_service.py` | CR-01/Q-01/WR-01/WR-05 regression tests | ✓ VERIFIED | 31 targeted cases re-run by me, all pass. |
| `tests/options/test_execution.py` | EX-01 short-failure + long-failure-preservation tests | ✓ VERIFIED | 4 targeted cases re-run by me, all pass. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `service.py _manage_position` | `service.py _quote_ok` | gate on every leg before `mark_spread` | ✓ WIRED (gap closed) | Confirmed at lines 974-997, before line 999's `mark_spread` call. Previously ✗ NOT_WIRED in the initial verification; now fixed. |
| `service.py _manage_position` close path | `try/except Exception` around `close_legs` | `ok = False` on exception → existing NEEDS_ATTENTION branch | ✓ WIRED | Confirmed at lines 1021-1030; `asyncio.CancelledError` re-raised, not swallowed. |
| `service.py reconcile(startup=True)` | structure-kind mismatch check | widened D-29 branch, `options_strategy_structure_mismatch` event | ✓ WIRED | Confirmed at lines 389-450; same branch as the unknown-strategy check, not duplicated. |
| `service.py _scan_and_open` | global BP headroom / concurrent count | sum/count over `_ACTIVE_STATUSES` rows | ✓ WIRED | Confirmed at lines 679-693; `_OPEN_STATUSES` constant deleted, no remaining references. |
| `execution.py close_legs` | long-leg SELL orders | `open_shorts` tracked; blocks longs once non-empty | ✓ WIRED | Confirmed at lines 245-269. |
| `execution.py open_position` entry-unwind | `service.py _try_open` ABORTED reporting | discarded return value → unconditional "legs unwound" | ✗ NOT_WIRED (CR-02, escalated, not gating — see above) | `close_legs`' boolean result is never propagated past `execution.py:209`; `_try_open` cannot distinguish a successful unwind from a failed one. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full suite regression (re-run independently, not trusting SUMMARY) | `python3 -m pytest -q` | 1299 passed, 1 skipped in 68.42s | ✓ PASS (exact match) |
| CR-01/Q-01/WR-01/WR-05 targeted subset | `pytest -q tests/options/test_service.py -k "quote_ok or invalid_quote or invalid_long_quote or close_exception or unquotable or structure_mismatch or needs_attention_and_closing"` | 31 passed, 54 deselected | ✓ PASS |
| EX-01 targeted subset | `pytest -q tests/options/test_execution.py -k "short_failure_sells_no_long or long_failure_still_attempts_other_longs"` | 4 passed, 26 deselected | ✓ PASS |
| Migration + job-registration regression | `pytest -q tests/options/test_service.py::test_shipped_book_registers_per_strategy_jobs tests/options/test_service.py::test_register_jobs_one_entry_scan_per_strategy tests/state/test_migrations.py` | 53 passed | ✓ PASS (no regression from 11-07) |

### Probe Execution

No conventional `scripts/*/tests/probe-*.sh` for this phase. `scripts/uat_options_probe.py` is explicitly manual/live-only (RTH + OpenD). Step 7c: SKIPPED (no offline-runnable probes), same as the initial verification.

### Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|-----------------|--------|----------|
| MSO-01 | 11-01 | ✓ SATISFIED | Unchanged by 11-07; re-confirmed via regression tests. |
| MSO-02 | 11-01, 11-04 | ✓ SATISFIED | Unchanged by 11-07. |
| MSO-03 | 11-01 | ✓ SATISFIED | Unchanged by 11-07. |
| MSO-04 | 11-02, 11-05 | ✓ SATISFIED | `strategy.py` byte-unchanged by 11-07. |
| MSO-05 | 11-02, 11-06, 11-07 | ✓ SATISFIED — gap closed | `manage_decision_debit` now only ever sees a mark built from validated (`_quote_ok`-gated) quotes; the structure-kind guard (WR-01) ensures it only ever sees its own kind's config. |
| MSO-06 | 11-03, 11-05 | ✓ SATISFIED (WR-03 caveat carried) | Unchanged by 11-07. |
| MSO-07 | 11-03, 11-06, 11-07 | ✓ SATISFIED | Realized close math (`realized_per_spread = credit - net_exit`) unchanged (grep count 1); a position is booked CLOSED only when `close_legs` reports every leg closed (EX-01 makes this stricter, not looser). |
| MSO-08 | 11-05, 11-06, 11-07 | ✓ SATISFIED for the literal requirement text; CR-02 is a real gap in the ABORTED-entry edge case, escalated above, not gating | Per-strategy caps, global breaker, global BP headroom and one-position-per-underlying are all correctly computed for every position that reaches an `_ACTIVE_STATUSES` row. The gap is upstream of that: a failed entry-unwind can leave live legs on a row (`ABORTED`) that never enters `_ACTIVE_STATUSES` in the first place. |
| MSO-09 | 11-02, 11-04, 11-06 | ✓ SATISFIED | Unchanged by 11-07. |

No orphaned requirements: all 9 MSO-01..09 IDs appear in at least one plan's `requirements:` frontmatter (including 11-07's `[MSO-05, MSO-07, MSO-08]`) and in `REQUIREMENTS.md` § Multi-Strategy Options, which marks all 9 "Phase 11 / Complete."

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/options/execution.py` | 209 | `open_position` discards `close_legs`' unwind result; `_try_open` reports ABORTED "legs unwound" unconditionally (CR-02 / EX-02) | 🛑 Critical (escalated, not gating this re-verification — see reasoning above) | A failed entry-unwind leaves live legs invisible to BP headroom, busy, and reconcile; the Telegram alert is false. |
| `bot/options/service.py` | 974-997 | Q-01 escalates to NEEDS_ATTENTION on the first bad quote cycle inside the guard window, no retry (WR-06) | ⚠️ Warning | A single transient quote hiccup at 09:35 ET cancels the automated near-expiry close for that position; the operator must close by hand. |
| `bot/options/service.py` | 149-151 | `_quote_ok` accepts `bid=0` and any width (one-sided/wide quotes) (WR-07) | ⚠️ Warning | An illiquid single-name quote (e.g., 0.10/9.00) can still fabricate a mid that fires a spurious exit or inflates unrealized loss — same class of bug as CR-01, caused by a wide quote instead of a missing one. |
| `bot/options/service.py` | 1017-1019 | `_on_exit_filled` always writes `status="CLOSED"` regardless of `filled_qty` (WR-08) | ⚠️ Warning | A short partially bought back under EX-01 is recorded as fully CLOSED while the broker still holds the remainder. |
| `bot/options/service.py` | 679-693 | NEEDS_ATTENTION rows permanently consume BP headroom and the strategy's concurrent slot with no operator-facing way to resolve them other than hand-editing SQLite (WR-09) | ⚠️ Warning | Four stuck rows silently disable `super_bull_call` (`max_concurrent_positions: 4`) with no alert at the cap. |
| `bot/options/service.py` | 912-923, 997 | A quote-skipped position contributes `$0.0` to `unrealized_total`, so the breaker fails open during a snapshot outage (IN-07, new) | ℹ️ Info | Documented trade-off (correctly avoids the CR-01 false-trip failure mode) but undocumented in code/CLAUDE.md. |
| `bot/options/universe.py` | 87 | No tie-breaker on `ORDER BY rank ASC` (WR-03, carried/deferred) | ⚠️ Warning | Unchanged from initial verification. |
| `bot/options/config.py` | 551-554 | `legacy_view` unvalidated `dict.pop()` (WR-04, carried/deferred) | ⚠️ Warning | Unchanged from initial verification. |
| `bot/options/service.py` | 771-782 | Entry premium recorded at pre-trade mid, not fill (WR-02, carried/deferred) | ⚠️ Warning | Unchanged from initial verification. |
| `bot/options/service.py`, `bot/main.py`, `bot/options/store.py`, `bot/options/config.py`/`universe.py` | various | IN-01..IN-06 (carried) | ℹ️ Info | Unchanged; cosmetic/latent, not phase blockers. |

No unresolved `TBD`/`FIXME`/`XXX` debt markers found in the files this phase (including 11-07) modified.

### Human Verification Required

#### 1. Live paper run with both books registered and the bull-call scan reading the real watchlist

**Test:** During RTH after the equity bot's premarket scan has persisted `daily_scan` rows for the day, start `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`.
**Expected:** Startup log shows `options_jobs_registered` listing `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`; at 10:05 ET a log line shows the watchlist read returning a non-empty code count (or a structured `options_watchlist_empty`/`options_watchlist_unavailable` warning if the equity scan hasn't run).
**Why human:** Requires OpenD logged in, RTH, a real populated equity watchlist, and a running live process — this verification agent must not start the bot or connect to OpenD. Unchanged from the initial verification's sole Manual-Only item (also listed in `11-VALIDATION.md`).

#### 2. Developer decision on CR-02 (EX-02) before extended unattended live/paper operation

**Test:** Not a runnable check — a scope/risk-acceptance decision. Review the Escalated Finding above (and `11-REVIEW.md`'s CR-02 section) and decide whether to schedule a follow-up gap-closure plan (e.g., 11-08) implementing the propagate-and-escalate fix before running the multi-strategy bot unattended for extended periods, or to explicitly accept the residual risk (an entry-unwind failure can strand live legs outside BP/reconcile visibility) for now.
**Expected:** A recorded decision — either a new plan is scheduled, or an override/acceptance is added.
**Why human:** This is a scope and risk-tolerance judgment on a pre-existing, explicitly-deferred defect, not something further static analysis can resolve; it directly affects whether the "global BP cap" promise in the phase goal holds in every edge case.

### Gaps Summary

The one BLOCKER from the initial verification is closed. `_quote_ok` now gates every leg of every position (credit and debit) before it is marked, decided on, or closed against, with a distinct near-expiry escalation (Q-01) and a `close_legs` exception guard that always lands on NEEDS_ATTENTION rather than a stuck CLOSING row. The previously-noted WR-05 WARNING (global BP headroom/concurrent-cap undercounting stuck rows) is also fully resolved as part of the same gap-closure plan — NEEDS_ATTENTION and CLOSING rows now count against both. WR-01 (structure-kind mismatch) is likewise resolved at both reconcile and manage time. EX-01 removes the naked-short outcome from a failed short buy-back during any close (manage exit or entry unwind). I independently re-ran the full suite (1299 passed, 1 skipped, 0 failed) and every regression test the plan names, and all match the documented counts exactly.

A fresh code review (1c9a5d4) surfaced one new Critical finding, CR-02, which is the previously-deferred backlog item EX-02 escalated: a failed entry-unwind's `close_legs` result is discarded by `open_position`, so `_try_open` unconditionally reports "legs unwound" and marks the row ABORTED even when live legs remain on the broker, invisible to BP headroom, `busy`, and reconcile. I confirmed this by direct code reading. I am not treating this as a phase-11 BLOCKER: it is a Phase 8 defect unchanged by Phase 11, it does not violate ROADMAP Success Criterion 8's literal wording, and it was explicitly and knowingly scoped out of the approved 11-07 gap-closure plan as backlog item EX-02 before this review even ran. I am escalating it as a human-decision item rather than silently accepting it or inflating it into a blocking gap for a plan that deliberately did not commit to fixing it. Four new WARNINGs (WR-06..WR-09) and one new INFO (IN-07) from the same review are lower-severity, documented above, and also do not gate this phase.

---

_Verified: 2026-09-24T17:08:43Z_
_Verifier: Claude (gsd-verifier)_
