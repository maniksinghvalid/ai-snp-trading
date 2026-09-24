---
phase: 11-multi-strategy-options-bot-bull-call-spread
verified: 2026-09-24T21:40:00Z
status: human_needed
score: 9/9 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 9/9
  gaps_closed:
    - "CR-02 (EX-02): a failed entry unwind is now reported truthfully as NEEDS_ATTENTION (live exposure), never ABORTED 'legs unwound'; the row counts against BP headroom, busy, and the per-strategy concurrent cap in the same scan and every later one"
  gaps_remaining: []
  regressions: []
escalated_findings:
  - id: CR-03 (fresh finding, 11-REVIEW.md 06b6787, third review)
    disposition: "not gating this re-verification — pre-existing Phase 8 fill_leg defect (the poll loop has no try/finally around get_order_status; an exception there skips the TTL-path cancel_order and leaves the just-placed order resting on the broker), structurally unchanged by 11-08. Its consequence is newly operator-facing because 11-08 added a 'close manually' alert on this exact path that says nothing about a possibly-still-resting order. Flagged for a human decision on a follow-up plan (11-09) before further live/paper use, same escalation-gate treatment CR-02 received last round."
    evidence: "bot/options/execution.py fill_leg (no try/finally around the poll loop; _poll calls gateway.get_order_status with no try/except) and close_legs' unwind branch (execution.py:216-231, catches Exception but never cancels the just-placed order); service.py _try_open's new NEEDS_ATTENTION 'UNWIND INCOMPLETE — close manually' alert (service.py:902-926) does not warn the operator that an order the bot placed may still be working"
  - id: WR-10 (new, introduced by 11-08's WR-06 fix)
    disposition: "not gating — narrow edge case (a skipped final manage cycle on expiry day), reproduced by the reviewer, judged a WARNING not a BLOCKER because the common case (a bad quote inside the guard window) is now handled correctly by the streak; recorded as a human-decision item alongside CR-03 and WR-11"
    evidence: "service.py:1091-1096 (final_cycle depends on exactly one scheduled fire in [15:50,15:55) on expiry day; max_instances=1 on options_manage (service.py:589) can drop that fire if the prior cycle overruns, or a snapshot-chunk failure on that cycle skips the position before the final-cycle test runs)"
  - id: WR-11 (new, introduced by 11-08's WR-06 fix)
    disposition: "not gating — a persistent snapshot outage (not a per-position missing-leg quote) was already unescalated pre-11-08 in most respects, but 11-08 explicitly removed the last alerting path for it (previously a failed chunk produced 'bad' for every leg, escalating positions inside the guard window); recorded as a human-decision item"
    evidence: "service.py:983-1006 (_manage_once): unsnapped codes are logged once per cycle via options_manage_snapshot_outage but never counted, never alerted, and the skip happens even on the expiry-day final cycle; bot/service/watchdog.py's OpenDWatchdog (cited by T-11-46 as the mitigation) only polls get_global_state and cannot see a quote-rights/subscription-scoped failure while OpenD stays connected and logged in"
---

# Phase 11: Multi-strategy options bot (bull_call_spread) Verification Report — SECOND RE-VERIFICATION

**Phase Goal:** `rules_options.json` defines multiple option strategies in a `strategies` array and the ONE options-bot process runs all of them concurrently: the existing `tasty_credit_spreads` credit book (behavior unchanged) plus a new `super_bull_call` debit book — a bull call spread (~30Δ long call, short call one width higher, ≤30% of width debit, no stop, full close at a % of max profit), whose daily bullish universe is the equity bot's Trend Join Long premarket watchlist (read-only). Per-strategy sizing; global daily-loss breaker and BP cap.

**Verified:** 2026-09-24T21:40:00Z
**Status:** human_needed
**Re-verification:** Yes — after gap-closure plan 11-08 (commits 3f32b6a, 7a6744f, 026844c, 284d0e3), following a third code review (06b6787, `11-REVIEW.md`, status `issues_found`)

## Method

Not a rubber-stamp of 11-08-SUMMARY.md, 11-REVIEW.md, or the prior 11-VERIFICATION.md's narrative. For every claim in all three I read the actual current code myself: `bot/options/execution.py` in full (`open_position`'s unwind branch, `fill_leg`, `_poll`, `close_legs`), `bot/options/service.py` around `_try_open` (lines 885-930), `_scan_and_open` (700-745), `_manage_once` (980-1013), `_manage_position` (1035-1135), and the module constants/helpers (`_QUOTE_MISS_ESCALATE_CYCLES`, `_MARK_MAX_SPREAD_FRAC_OF_MID`, `_MARK_MAX_SPREAD_FLOOR_USD`, `_manage_cutoff`, `_quote_markable`). I independently re-ran the full suite and the plan's own named `-k` regression subsets rather than trusting the documented counts. I read `bot/gateway/gateway.py`'s `get_order_status`/`GatewayError` docs to confirm CR-03's mechanics independent of the review's narrative. Read: `11-08-PLAN.md`/`SUMMARY.md`, `11-REVIEW.md` (third review), the prior `11-VERIFICATION.md` (`human_needed`, CR-02 escalated), `11-UAT.md` (CR-02 decision recorded as passed), `REQUIREMENTS.md` § Multi-Strategy Options, `ROADMAP.md` § Phase 11 (goal + 8 success criteria, literal wording), and `CLAUDE.md` Phase 8/11 invariants.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria 1–8 + the derived safety truth from CR-01/Q-01)

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---------|--------|----------|
| 1 | `rules_options.json` ships in `strategies` shape with both strategies; one entry-scan job per strategy + one manage job + one EOD job | ✓ VERIFIED (regression-checked) | Unchanged by 11-08. Confirmed via the full suite (job-registration tests included) and `git diff f7ceede..HEAD --stat` — no touch to `rules_options.json`, `schema.py`, `bot/main.py`. |
| 2 | `load_options_config` unchanged flat contract; Phase 9 backtester/probe/tests pass unmodified | ✓ VERIFIED (regression-checked) | `config.py` untouched by 11-08 (not in `files_modified`; confirmed no diff). |
| 3 | `backtester.options_run` accepts `--strategy`; debit rejected with a clear error | ✓ VERIFIED (regression-checked, WR-04 caveat carried, deferred) | Unchanged file. |
| 4 | Pure-function tests prove bull-call strike selection, `max_debit_to_width` gate, sizing, `manage_decision_debit` math | ✓ VERIFIED (regression-checked) | `bot/options/strategy.py` untouched by 11-08 (confirmed: not in `files_modified`, and `11-08-PLAN.md`'s own read-only citation of `strategy.py` for reference only). |
| 5 | Equity watchlist read via read-only SQLite URI, capped at 20 by rank; fail-closed | ✓ VERIFIED (regression-checked, WR-03 caveat carried, deferred) | `bot/options/universe.py` untouched by 11-08. |
| 6 | Positions carry `strategy_name`; debit positions store negative `credit_per_spread`; close math correct | ✓ VERIFIED (regression-checked) | `bot/state/migrations.py`, `bot/options/store.py` untouched by 11-08. |
| 7 | Per-strategy caps counted per strategy; daily-loss breaker, BP headroom, one-position-per-underlying are global | ✓ VERIFIED (regression-checked, WR-05 resolved prior round, held) | `service.py:700-741` (`_scan_and_open`): `active = self._store.get_option_positions(_ACTIVE_STATUSES)`; `open_max_loss_total`/`open_count` computed over `active`; `_ACTIVE_STATUSES = ("OPENING","OPEN","CLOSING","NEEDS_ATTENTION")` (line 92, unchanged by 11-08). NEEDS_ATTENTION rows created by the new CR-02 branch are counted by this same, unmodified logic — confirmed by `test_incomplete_unwind_counts_against_bp_in_the_same_scan` (re-run directly, passes). |
| 8 | Safety invariants unchanged (LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only); full suite green | ✓ VERIFIED | I read `git diff -U0 f7ceede..HEAD -- bot/options/execution.py` myself: hunks are confined to the module docstring and `open_position`'s unwind branch (lines ~156-240); `fill_leg` (52-139), `_poll` (141-150) and `close_legs` (button-to-button) are byte-unchanged, confirmed by direct read — I found `grep -c "self._gw.place_order" bot/options/execution.py` = 1 (single LIMIT-only order path, `place_order(code, int(qty), price, trd_side)`, no market-order path exists). `close_legs` still buys back shorts before selling longs and blocks every long once a short is open (EX-01, lines 245-269, unchanged). SAFE-OG-01 reconcile scope unaffected (reconcile still loads only OPEN/OPENING/CLOSING at startup, OPEN in steady state — `service.py` reconcile block unchanged by 11-08). **I independently re-ran the full suite myself: `python3 -m pytest -q` → 1330 passed, 1 skipped, 0 failed in 52.38s** — exact match to the SUMMARY's and third review's documented count. |
| 9 (derived — the CR-01/Q-01 safety truth, closed two rounds ago) | Bull-call and credit positions are never marked or closed against an invalid/missing quote (no naked-short exposure from a bad mark, no false breaker trip, no stuck CLOSING row) | ✓ VERIFIED — held, and strengthened by WR-07 | `_quote_ok` (base validity, still the guard-window gate) and the new `_quote_markable` (width/two-sidedness gate for every decision outside the guard window, `service.py:174-188`) are selected once per `_manage_position` call via `gate = _quote_ok if in_guard else _quote_markable` (`service.py:1078`). I read `_quote_markable`'s formula directly: `ask - bid <= max(0.5 × mid, $0.10) + 1e-9`, rejecting a one-sided quote with `ask > $0.10` or an absurdly wide 0.10/9.00-style quote from ever reaching `mark_spread`, the decision functions, or a non-aggressive `close_legs`. This is a stricter, additional gate layered on top of the previously-verified CR-01/Q-01 mechanism, not a replacement — the guard-window `close_legs(aggressive=True)` path is unaffected (both decision functions return `"assignment_guard"` first whenever `dte <= assignment_guard_dte`, confirmed read in `strategy.py`). Targeted tests re-run directly by me: 21 passed (`-k "markable or one_sided or wide_short"`). |

**Score:** 9/9 truths verified. No literal-wording violation of any ROADMAP Success Criterion or MSO requirement. Three findings from the third review (CR-03, WR-10, WR-11) are escalated below as human-decision items, not treated as failing any of the 9 truths above — see explicit reasoning.

### Escalated Findings — reasoned disposition, not phase-11 blockers (read this before trusting the score)

**CR-03 — `fill_leg`'s poll loop has no guaranteed cancel-on-exception; the new "close manually" alert can point at a still-live order**

I confirmed this by reading `bot/options/execution.py` directly, independent of the review's narrative:
- `fill_leg` (lines ~93-139) places the order, then loops `await self._gw.get_order_status(order_id)` via the un-guarded helper `_poll` (lines 141-150). Neither call site nor `_poll` itself has a `try/except` or `try/finally` around the poll. `bot/gateway/gateway.py`'s `get_order_status` docstring confirms it raises `GatewayError` "immediately (or after exhausting retries for the rate-limit case)" for any non-rate-limit error, or an exhausted rate-limit retry.
- The ONLY `cancel_order` call site in `fill_leg` is on the **TTL-expiry** path (line 116, inside the normal, non-exceptional flow). If `get_order_status` raises instead of returning, that branch is never reached and the order is never cancelled — the exception propagates straight out of `fill_leg`.
- In `open_position`'s unwind branch, this exception is now caught by 11-08's new `except Exception:` (execution.py ~222-230), which sets `unwound = False` and returns `False` — but it does NOT attempt `cancel_order` on whatever order `fill_leg` had just placed for the unwind leg. `close_legs`' own `except`-free body has the same property on the manage-close path (`service.py`'s `options_close_error` branch at ~1156-1170 catches the exception at the caller, again without cancelling).
- `_try_open`'s new NEEDS_ATTENTION alert (`service.py:902-926`, added by 11-08 for exactly this scenario) tells the operator "UNWIND INCOMPLETE — legs still open ({codes}); close manually" — it does not warn that an order the bot itself placed to close those legs may still be resting at the broker. An operator who manually closes the named legs, followed by that resting order later filling, can end up naked short — which breaks the bot's own defined-risk invariant.

**Reasoning on scope, stated explicitly:**
- The defect is in `fill_leg`/`_poll`, both **byte-unchanged by 11-08** (confirmed above under Truth 8) — this is unchanged Phase 8 code, not a Phase 11 or 11-08 regression.
- It does not violate the literal wording of ROADMAP Success Criterion 8 ("LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only; full suite green") — none of those specific invariants are affected; the order IS a LIMIT order, the ordering IS longs-first/shorts-first, SAFE-OG-01's reconcile scope is unaffected.
- Unlike CR-02, this was NOT a finding that had already been scoped out by name in an approved plan before this review ran — it is genuinely fresh, surfaced only after 11-08 shipped, when the third review examined the new NEEDS_ATTENTION/"close manually" alert path CR-02's own fix introduced. There has been no operator decision on it yet (11-UAT.md's only recorded decision is on CR-02, already closed).
- Following the SAME escalation-gate methodology the prior verification round applied to CR-02 (surface prominently, do not silently accept, do not block a phase that has not scoped this in), I am marking this a **human-decision item, not a gaps_found BLOCKER**, precisely because: it is pre-existing rather than introduced by this phase's own commits, and resolving it is a scope/timing decision (a follow-up plan, e.g. 11-09) rather than something further static reading can settle for me.
- **Recommend:** schedule a gap-closure plan (11-09) implementing the review's proposed fix — wrap `fill_leg`'s post-placement logic in `try/except BaseException: await asyncio.shield(self._gw.cancel_order(order_id))` on every exit path, and name any order that may still be live in both the CR-02 NEEDS_ATTENTION alert and the `options_close_error` alert — before relying on the multi-strategy bot for extended unattended live/paper operation, especially through periods of API rate-limit pressure (the order-status budget is shared with the equity bot on the same account).

**WR-10 and WR-11 — the WR-06 fix (streak/final-cycle escalation) has narrow gaps where a near-expiry position can go completely unescalated**

I confirmed both directly in `service.py`:
- WR-10: `final_cycle` (`service.py:1091-1096`) is true only when a manage cycle actually executes in the roughly 5-minute window before the manage cutoff on expiry day. `options_manage`'s `IntervalTrigger` has `max_instances=1` (confirmed at job registration), so a slow-closing prior cycle, or a snapshot-chunk failure on that specific cycle, can mean no cycle ever satisfies the final-cycle test — the position then rides to expiry with the streak still below 3 and zero alerts sent.
- WR-11: `_manage_once`'s `unsnapped` handling (`service.py:983-1006`) logs `options_manage_snapshot_outage` per stranded position but never increments any counter and never alerts, on any cycle including the final one. `OpenDWatchdog` (cited as the mitigation for this exact scenario in the plan's own threat model, T-11-46) only polls `get_global_state()` (connection/login state) and cannot observe a persistent quote-subscription-scoped failure while OpenD otherwise stays connected.
- Both ARE genuine behavioral changes introduced by 11-08: before 11-08, ANY bad quote inside the guard window (including one caused by a failed snapshot chunk, which produced `bad` for every affected leg under the pre-11-08 code) escalated immediately with an alert. 11-08's fix correctly removes the over-eager immediate escalation for the common transient-miss case, but in doing so also removed the fallback alerting path for these two edge cases.
- **Disposition:** WARNING-level, not BLOCKER, because: (a) the common case — a single bad quote or a handful of bad quotes inside the guard window — is now handled correctly and is the scenario the prior verification round's gap (WR-06) was about; (b) both edge cases require a specific unlucky timing (a skipped final cycle, or a snapshot outage that happens to coincide with or persist through the guard window) rather than being the typical path; (c) the review itself classifies both as Warning, not Critical. I am not inflating them to BLOCKER status, but I am not silently accepting them either — they are recorded below as human-decision items alongside CR-03, since fixing them is also a scope/priority decision (the review's proposed fixes — widening the final-cycle window, and alerting once on a threshold of outage cycles — are reasonable but not yet operator-approved).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/options/execution.py` | `open_position` returns `False` (not `None`) on an incomplete unwind; docstrings updated | ✓ VERIFIED | Read directly: `return None if unwound else False` (execution.py), `except Exception:` sets `unwound = False` with `"open_position_unwind_error"` log on an unwind exception. `fill_leg`/`_poll`/`close_legs` bodies otherwise byte-unchanged; single `place_order` call site confirmed. |
| `bot/options/service.py` | `_try_open` NEEDS_ATTENTION branch (CR-02); `_quote_miss_streak` + final-cycle rule + snapshot-outage skip (WR-06); `_quote_markable` strict gate (WR-07) | ✓ VERIFIED | All present at the exact lines the plan specifies; confirmed by direct read: `if filled is False:` block (~902-926), `_manage_once`'s `unsnapped` skip (~983-1006), `_manage_position`'s streak/`final_cycle`/`gate` selection (~1078-1135). |
| `scripts/uat_options_probe.py` | Honours the `False` return (NEEDS_ATTENTION, not a crash on iterating a bare `False`) | ✓ VERIFIED | `if filled is False:` branch at line 209, ahead of the unchanged `if filled is None:` branch; `python3 -m py_compile scripts/uat_options_probe.py` succeeds. |
| `tests/options/test_execution.py`, `tests/options/test_service.py` | CR-02/WR-06/WR-07 regression tests | ✓ VERIFIED | All four named CR-02 tests exist (`grep` confirmed by name); targeted `-k` subsets re-run by me: 6 + 5 + 21 = 32 passed, matching the plan's collected-case count. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `execution.py open_position` entry-unwind | `service.py _try_open` | `return None if unwound else False` → `if filled is False:` NEEDS_ATTENTION branch | ✓ WIRED (gap closed) | Previously ✗ NOT_WIRED (CR-02, escalated) in the prior verification; now confirmed fixed at both ends by direct read. |
| `service.py _try_open`'s NEEDS_ATTENTION row | global BP headroom / busy / per-strategy concurrent cap | `_ACTIVE_STATUSES` inclusion, unchanged `_scan_and_open` logic | ✓ WIRED | Confirmed via `test_incomplete_unwind_counts_against_bp_in_the_same_scan`, re-run directly, passes. |
| `service.py _manage_position` | `_quote_ok` (guard window) / `_quote_markable` (elsewhere) | `gate = _quote_ok if in_guard else _quote_markable` | ✓ WIRED | Confirmed at line 1078; supersedes the prior round's hardcoded `_quote_ok(quotes.get(` pattern, as the SUMMARY itself notes. |
| `service.py _manage_position` streak escalation | Q-01 NEEDS_ATTENTION + alert | `streak >= _QUOTE_MISS_ESCALATE_CYCLES or final_cycle` | ✓ WIRED, with two edge-case gaps (WR-10, WR-11 — see Escalated Findings) | Wired for the common case; the two edge cases are a reliability gap in the wiring's coverage, not a missing wire. |
| `execution.py fill_leg`'s poll loop | guaranteed order cancellation on every exit path | *(none — no try/finally)* | ✗ NOT_WIRED (CR-03, escalated, not gating — see above) | The module docstring's own claim ("Guarantee: no resting order is left behind on any return path") is contradicted by the code: the guarantee holds for the TTL path only, not for an exception from `get_order_status`. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full suite regression (re-run independently, not trusting SUMMARY/REVIEW) | `python3 -m pytest -q` | 1330 passed, 1 skipped in 52.38s | ✓ PASS (exact match) |
| CR-02 / clean-unwind preservation targeted subset | `pytest -q tests/options/test_execution.py tests/options/test_service.py -k "incomplete_unwind or clean_unwind"` | 6 passed, 140 deselected | ✓ PASS |
| WR-06 targeted subset | `pytest -q tests/options/test_service.py -k "near_expiry or snapshot_outage"` | 5 passed, 108 deselected | ✓ PASS |
| WR-07 targeted subset | `pytest -q tests/options/test_service.py -k "markable or one_sided or wide_short"` | 21 passed, 92 deselected | ✓ PASS |
| CR-03 reproduction (fill_leg exception leaves order resting) | Direct code read of `fill_leg`/`_poll` (no runnable regression test exists for this path yet) | Confirmed: only one `cancel_order` call site, on the TTL path; no try/finally around the poll | ✗ CONFIRMED DEFECT, not gating (escalated) |

### Probe Execution

No conventional `scripts/*/tests/probe-*.sh` for this phase. `scripts/uat_options_probe.py` is explicitly manual/live-only (RTH + OpenD). Step 7c: SKIPPED (no offline-runnable probes), same as both prior verification rounds.

### Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|-----------------|--------|----------|
| MSO-01 | 11-01 | ✓ SATISFIED | Unchanged by 11-08; re-confirmed via regression tests. |
| MSO-02 | 11-01, 11-04 | ✓ SATISFIED | Unchanged by 11-08. |
| MSO-03 | 11-01 | ✓ SATISFIED | Unchanged by 11-08. |
| MSO-04 | 11-02, 11-05 | ✓ SATISFIED | `strategy.py` byte-unchanged by 11-08. |
| MSO-05 | 11-02, 11-06, 11-07, 11-08 | ✓ SATISFIED — strengthened | `manage_decision_debit` now only ever sees a mark built from `_quote_markable`-gated (outside guard) or `_quote_ok`-gated (inside guard) quotes; its assignment-guard exit is not cancelled by one transient bad-quote cycle (WR-06). |
| MSO-06 | 11-03, 11-05 | ✓ SATISFIED (WR-03 caveat carried, deferred) | Unchanged by 11-08. |
| MSO-07 | 11-03, 11-06, 11-07, 11-08 | ✓ SATISFIED | Realized close math unchanged; a NEEDS_ATTENTION entry row (both the CR-02 unwind-incomplete kind and the Q-01 near-expiry kind) has no `closed_at`, so `get_realized_pnl_on` never books it. |
| MSO-08 | 11-05, 11-06, 11-07, 11-08 | ✓ SATISFIED — the CR-02 gap this required is now closed | Per-strategy caps, global breaker, global BP headroom and one-position-per-underlying are correctly computed for every position that reaches an `_ACTIVE_STATUSES` row, INCLUDING the CR-02 NEEDS_ATTENTION row (previously the gap: an ABORTED row escaped this accounting entirely). Residual: WR-10/WR-11 are narrow reliability gaps in the escalation path that feeds one input (near-expiry quote validity) into this accounting, not in the accounting itself; CR-03 is a resting-order risk that arises after a NEEDS_ATTENTION/ABORTED determination, not in the caps/breaker/BP logic. Neither undermines the literal MSO-08 text. |
| MSO-09 | 11-02, 11-04, 11-06 | ✓ SATISFIED | Unchanged by 11-08. |

No orphaned requirements: all 9 MSO-01..09 IDs appear in at least one plan's `requirements:` frontmatter (including 11-08's `[MSO-05, MSO-07, MSO-08]`) and in `REQUIREMENTS.md` § Multi-Strategy Options, which marks all 9 "Phase 11 / Complete."

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/options/execution.py` | `fill_leg`'s poll loop (no try/finally); unwind `except` branch ~222-230 | `fill_leg` leaves a just-placed order resting on the broker if `get_order_status` raises; 11-08's new "close manually" alert does not warn of this (CR-03) | 🛑 Critical (escalated, not gating this re-verification — see reasoning above) | An operator following the CR-02 alert to close legs manually can be surprised by a bot-placed order filling afterward, producing a naked short — breaking the defined-risk invariant. |
| `bot/options/service.py` | `_manage_position` final-cycle test, ~1091-1096; job registration `max_instances=1` | Expiry-day escalation depends on exactly one scheduled manage cycle landing in a 5-minute window; that cycle can be skipped (WR-10, new — introduced by 11-08) | ⚠️ Warning | An unquotable position with a low miss-streak can expire completely unmanaged and unalerted. |
| `bot/options/service.py` | `_manage_once`, ~983-1006 | A persistent snapshot-chunk failure is logged but never counted or alerted, even on the expiry-day final cycle (WR-11, new — introduced by 11-08; the fallback alerting path this replaced was removed) | ⚠️ Warning | A quote-subscription-scoped outage that persists while OpenD stays connected leaves every OPEN position, including near-expiry ones, unmanaged and silent; the watchdog cannot see this class of failure. |
| `bot/options/service.py` | `self._quote_miss_streak`, ~1085-1086 | The streak dict is not date-scoped, so it can carry overnight (IN-08, new) | ℹ️ Info | A late-day miss the day before expiry plus one 09:35 miss on expiry day can escalate immediately instead of retrying, which fails toward the human — not a safety gap. |
| `bot/options/service.py`, `execution.py` | ~908-913, ~218-219 | The CR-02 NEEDS_ATTENTION record/alert lists every original leg code, not which legs are actually still open (IN-09, new) | ℹ️ Info | Operator visibility gap; the broker remains the source of truth for quantities per the plan's own documented decision. |
| `bot/options/service.py` | ~1126 | `options_manage_missing_quote` now fires for both "no quote" and "quote present but unmarkable" cases with the same event name (IN-10, new) | ℹ️ Info | Minor triage ambiguity; no safety impact. |
| `scripts/uat_options_probe.py` | ~226, 238 (pre-existing, probe-only) | The UAT probe's own close path is not guarded against an unvalidated 'N/A' snapshot (IN-11, pre-existing) | ℹ️ Info | Manual-probe-only path; does not affect the automated bot. |
| `bot/options/universe.py` | 87 | No tie-breaker on `ORDER BY rank ASC` (WR-03, carried/deferred) | ⚠️ Warning | Unchanged across all three verification rounds. |
| `bot/options/config.py` | ~514, `options_run.py` ~229-230 | `legacy_view` unvalidated (WR-04, carried/deferred) | ⚠️ Warning | Unchanged. |
| `bot/options/service.py` | ~826-833 | Entry premium recorded at pre-trade mid, not fill (WR-02, carried/deferred) | ⚠️ Warning | Unchanged. |
| `bot/options/service.py` | ~1147-1149 | A partial short buy-back is recorded as fully CLOSED (WR-08, carried/deferred, priority raised) | ⚠️ Warning | Unchanged; 11-08 adds two more paths into NEEDS_ATTENTION that make WR-09 (below) more pressing. |
| `bot/options/service.py` | ~733-741 | NEEDS_ATTENTION rows permanently consume BP headroom and a concurrent slot with no operator-facing resolve path (WR-09, carried/deferred, priority raised) | ⚠️ Warning | 11-08 adds two more ways into NEEDS_ATTENTION (the CR-02 unwind and the WR-06 escalation); this warning is now more consequential than when first raised. |
| various | — | IN-01..IN-07, EX-03 (carried, deferred) | ℹ️ Info | Unchanged; cosmetic/latent, not phase blockers. |

No unresolved `TBD`/`FIXME`/`XXX` debt markers found in any file this phase (including 11-08) modified.

### Human Verification Required

#### 1. Live paper run with both books registered and the bull-call scan reading the real watchlist

**Test:** During RTH after the equity bot's premarket scan has persisted `daily_scan` rows for the day, start `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`.
**Expected:** Startup log shows `options_jobs_registered` listing `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`; at 10:05 ET a log line shows the watchlist read returning a non-empty code count (or a structured `options_watchlist_empty`/`options_watchlist_unavailable` warning if the equity scan hasn't run).
**Why human:** Requires OpenD logged in, RTH, a real populated equity watchlist, and a running live process — this verification agent must not start the bot or connect to OpenD. Unchanged and still pending across all three verification rounds.

#### 2. Developer decision on CR-03 (fill_leg resting-order-on-exception) before relying on the "close manually" alert

**Test:** Not a runnable check — a scope/risk-acceptance decision. Review the Escalated Findings section above (and `11-REVIEW.md`'s CR-03 section) and decide whether to schedule a follow-up gap-closure plan (e.g., 11-09) implementing the guaranteed-cancel-on-exception fix and naming any possibly-live order in the NEEDS_ATTENTION/close-error alerts, before running the multi-strategy bot unattended for extended periods — or to explicitly accept the residual risk for now.
**Expected:** A recorded decision — either a new plan is scheduled, or an override/acceptance is added to this or a future VERIFICATION.md.
**Why human:** Same class of decision as CR-02's disposition last round — a pre-existing defect whose consequence is newly operator-facing, not something further static analysis can resolve.

#### 3. Developer decision on WR-10 / WR-11 (near-expiry escalation reliability gaps introduced by 11-08)

**Test:** Not a runnable check. Review whether the narrow expiry-day timing gap (WR-10: a skipped final manage cycle) and the persistent-snapshot-outage silence (WR-11) are acceptable residual risk given the common case is now correctly handled, or whether they should be folded into the same follow-up plan as CR-03.
**Expected:** A recorded decision — schedule, defer explicitly, or accept.
**Why human:** Risk-tolerance judgment on edge-case timing/outage scenarios that require an operational decision about acceptable unattended-operation risk, not a code-correctness question I can resolve unilaterally.

### Gaps Summary

CR-02 (the prior round's single escalated finding) is now closed and I confirmed the fix by direct code read plus independent test re-run — `open_position` returns `False` (never conflated with the clean-unwind `None`) whenever an entry unwind leaves legs open on the broker or itself raises, `_try_open` maps that to a truthful NEEDS_ATTENTION row that is counted against BP headroom, busy, and the per-strategy concurrent cap in the SAME scan (not just the next one), and the clean-unwind ABORTED contract is preserved byte-for-byte. I independently re-ran the full suite (1330 passed, 1 skipped, 0 failed) and every named regression subset the plan and both summaries cite, and every count matches exactly.

A fresh (third) code review (06b6787) surfaced one new Critical finding (CR-03) and two new Warnings (WR-10, WR-11) in the same NEEDS_ATTENTION/near-expiry-escalation surface that 11-08 just touched. I confirmed all three by direct code reading, independent of the review's narrative:
- **CR-03** is a pre-existing Phase 8 `fill_leg`/`_poll` defect (no guaranteed order cancellation when a status poll raises) that 11-08 did not introduce and does not touch, but whose consequence is now more directly operator-facing because 11-08 added a "close manually" alert on exactly the path this defect can leave a resting order behind.
- **WR-10 and WR-11 ARE genuinely introduced by 11-08** — they are narrow gaps in the new streak/final-cycle escalation logic where a near-expiry position can go completely unescalated in specific timing/outage scenarios, regressing (only in those scenarios) the "always alert" behavior that existed before 11-08's fix for the over-eager WR-06 escalation.

None of these three violate the literal wording of any ROADMAP Success Criterion or MSO requirement text, and none of the 9 previously-established observable truths fail as a result. Following the same escalation-gate methodology applied to CR-02 last round, I am marking all three as human-decision items (not gaps_found BLOCKERs) because resolving them requires an operator scope/timing decision, not further static verification — and because inflating a pre-existing defect or a narrow edge-case regression into a phase-blocking gap, when the phase's own literal contract is satisfied and the common-case behavior is demonstrably correct and test-covered, would not be a fair reading of the roadmap's contract either. I am not silently accepting them: they are surfaced prominently above, with concrete reproduction evidence and a recommended follow-up plan (11-09).

---

_Verified: 2026-09-24T21:40:00Z_
_Verifier: Claude (gsd-verifier)_
