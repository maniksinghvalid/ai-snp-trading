---
phase: 11-multi-strategy-options-bot-bull-call-spread
plan: 08
subsystem: options-bot
tags: [options, execution, service, unwind, quote-validity, telegram-alerts, gap-closure]

# Dependency graph
requires:
  - phase: 11-07
    provides: OptionsBot multi-strategy service (D-21/D-22/D-29), _quote_ok (CR-01), UAT persistence
provides:
  - "open_position False/None execution contract (CR-02): live-exposure unwind failures are never silently reported as a clean abort"
  - "_try_open NEEDS_ATTENTION branch on incomplete unwind, counted against BP/busy/concurrent-cap in the same scan"
  - "WR-06 per-position quote-miss streak + expiry-session final-cycle escalation rule; snapshot-outage skip"
  - "WR-07 _quote_markable strict width/two-sidedness gate for every manage decision outside the assignment-guard window"
affects: [11-09-if-any, future-options-gap-closure, WR-08, WR-09, IN-07, EX-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Tri-state executor return (list | None | False) for distinguishing 'nothing left open' from 'still live exposure' after a failed multi-leg unwind"
    - "Per-position in-memory streak counter (self._quote_miss_streak) gating escalation on N consecutive counted cycles, not the first miss"
    - "Gate-selection-by-window pattern: `gate = _quote_ok if in_guard else _quote_markable`, one place defines the window"

key-files:
  created: []
  modified:
    - bot/options/execution.py
    - bot/options/service.py
    - scripts/uat_options_probe.py
    - tests/options/test_execution.py
    - tests/options/test_service.py

key-decisions:
  - "CR-02 signalling: `return None if unwound else False` in open_position's unwind branch — the smallest change, not an exception or a sentinel object (an exception would leave the row OPENING with no alert; a sentinel adds an importable symbol for the same information)."
  - "CR-02 leg recording: no new persistence. The NEEDS_ATTENTION row already carries every leg code (inserted before the first order), sufficient for busy/BP/concurrent-cap and operator visibility. Exact per-leg remainders deferred to WR-08 (accepted as T-11-45: max_loss_usd can over-count by about one leg's premium x qty x 100)."
  - "_try_open returns `pos` (not a new dict) on an incomplete unwind, so _scan_and_open's existing `if pos is not None:` counts it against opened_today/open_count/open_max_loss_total for the rest of the SAME scan, exactly as _ACTIVE_STATUSES counts it on every later scan (D-22)."
  - "WR-06 rule: escalate at _QUOTE_MISS_ESCALATE_CYCLES=3 consecutive COUNTED cycles (10 min at the 5-min manage interval), OR on the expiry session's final manage cycle (dte<=0 and now+manage_interval_min >= the manage cutoff) — the latter only matters on a late start/restart on expiry day where fewer than 3 counted cycles remain. A snapshot outage (failed chunk) is never counted (WR-06 explicitly excludes it, unlike a per-position missing-leg quote inside a successful chunk)."
  - "WR-07 width rule: `ask - bid <= max(0.5 * mid, $0.10) + 1e-9` (the review's formula; 10x the entry gate's 5%-of-mid and 2x its $0.05, since marking rejects ABSURD quotes, not illiquid-but-real ones). 'Two-sided' interpretation: bid=0 is accepted ONLY when ask<=$0.10 (not a literal `bid>0` on every leg), because a literal rule would strand a legitimately one-sided far-OTM wing (0.00/0.05) outside the guard window and block tasty's dte_exit/profit_target for weeks. The operator can tighten this later by adding `bid > 0 and` to `_quote_markable`."
  - "WR-07 gate per call site: `gate = _quote_ok if in_guard else _quote_markable`, computed once from `in_guard = dte <= strat_cfg.assignment_guard_dte`. This supersedes 11-07's literal key-link pattern `_quote_ok\\(quotes\\.get\\(` — the gate is now chosen dynamically, not hardcoded to `_quote_ok` at every call site."

requirements-completed: [MSO-05, MSO-07, MSO-08]

duration: ~25min
completed: 2026-09-24
---

# Phase 11 Plan 08: Gap Closure (CR-02, WR-06, WR-07) Summary

**Fixed a false "unwound" entry-failure alert (CR-02), a one-bad-cycle assignment-guard escalation (WR-06), and a one-sided/wide quote fabricating a false profit_target or breaker trip (WR-07) in the options bot's execution/manage paths.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-09-24
- **Tasks:** 3
- **Files modified:** 5 (bot/options/execution.py, bot/options/service.py, scripts/uat_options_probe.py, tests/options/test_execution.py, tests/options/test_service.py)

## Red evidence

- **Red evidence (Task 1):** `python3 -m pytest -q tests/options/test_execution.py tests/options/test_service.py -k "incomplete_unwind or clean_unwind"` against unmodified 7104bad sources → **5 failed, 1 passed** (the pass is the preservation test `test_clean_unwind_keeps_aborted_contract`).
- **Red evidence (Task 2):** `python3 -m pytest -q tests/options/test_service.py -k "near_expiry or snapshot_outage"` against the Task-1 service.py → **5 failed**.
- **Red evidence (Task 3):** `python3 -m pytest -q tests/options/test_service.py -k "markable or one_sided or wide_short"` against the Task-2 service.py → **19 failed, 2 passed** (the 2 passes are the two `test_manage_guard_window_closes_despite_one_sided_far_otm_wing` preservation cases, which pin the unchanged guard-window half of the rule and cannot be red by design).

## Final full-suite counts

`python3 -m pytest -q` → **1330 passed, 1 skipped, 0 failed** (baseline was 1299 passed, 1 skipped; net +31 = -1 replaced test + 32 new: 3 execution + 6 CR-02 + 5 WR-06 + 21 WR-07 = 3+6+5+21=35 collected cases across new functions, minus the 1 replaced function; matches the plan's "1299 - 1 + 32 = 1330" accounting).

`python3 -m pytest -q tests/options tests/backtester/options` → 454 passed (sampling gate, run after each task commit).

## Accomplishments

- **CR-02 closed:** `LegExecutor.open_position` now returns `False` (never conflated with the clean-unwind `None`) when the entry unwind leaves legs open on the broker or itself raises. `OptionsBot._try_open` maps `False` to a NEEDS_ATTENTION row with a truthful "UNWIND INCOMPLETE" alert and a distinct `options_entry_unwind_incomplete` audit event, and returns `pos` so the SAME scan's BP headroom / busy / concurrent-cap accounting sees the live exposure immediately, not just on the next scan. The clean-unwind ABORTED contract is byte-for-byte unchanged (preservation test). The UAT probe (the only other `open_position` caller) now handles the `False` branch instead of crashing on `TypeError` while iterating a bare `False`.
- **WR-06 closed:** near-expiry Q-01 escalation now requires `_QUOTE_MISS_ESCALATE_CYCLES` (3) consecutive counted bad-quote manage cycles, or the expiry session's final manage cycle — a single transient miss (e.g. the 09:35 price-discovery gap) no longer cancels the automated `assignment_guard` close 10 minutes early. A failed snapshot chunk is logged per stranded position (`options_manage_snapshot_outage`) and skips `_manage_position` entirely, so it never counts toward or resets the streak.
- **WR-07 closed:** outside the assignment-guard window, `_quote_markable` (a stricter width/two-sidedness gate) now guards every `mark_spread` / decision / non-aggressive close — a one-sided (`bid=0`, `ask>$0.10`) or absurdly wide quote can no longer fabricate a mid that trips a false `profit_target` or inflates the breaker's unrealized P&L. Inside the guard window the looser `_quote_ok` still applies (the decision there is always `assignment_guard`, independent of the mark), so a legitimately one-sided far-OTM wing still closes.

## Task Commits

Each task was committed atomically:

1. **Task 1: CR-02 incomplete entry unwind returns False, never ABORTED** — `3f32b6a` (fix)
2. **Task 2: WR-06 near-expiry escalation needs a streak or final cycle** — `7a6744f` (fix)
3. **Task 3: WR-07 `_quote_markable` gates every decision outside the guard window** — `026844c` (fix)

_Note: all three tasks' test additions (`tests/options/test_execution.py`, `tests/options/test_service.py`) were staged together with Task 1's source commit (`3f32b6a`), since the plan's tests were written up front before any source edit. Each task's `-k` RED check was still run against the correct pre-edit source state at the time (Task 1 against 7104bad via a temporary `git checkout --` revert of the day's execution.py edit; Tasks 2/3 against the accumulating service.py). This is a commit-boundary deviation only — the diffs, RED evidence, and final acceptance-criteria greps all match the plan exactly._

## Files Created/Modified

- `bot/options/execution.py` — `open_position`'s unwind branch now catches the unwind's exception (or reads its bool return) and returns `None if unwound else False`; module + method docstrings updated. `fill_leg`, `_poll`, `close_legs` byte-unchanged (confirmed via `git diff -U0 7104bad` hunk-range check); exactly one `self._gw.place_order` call site.
- `bot/options/service.py` — new module constants `_QUOTE_MISS_ESCALATE_CYCLES`, `_MARK_MAX_SPREAD_FRAC_OF_MID`, `_MARK_MAX_SPREAD_FLOOR_USD`; new pure helpers `_manage_cutoff` (extracted from `_is_rth_now`, behavior-identical) and `_quote_markable`; new instance attribute `self._quote_miss_streak`; `_try_open`'s new `if filled is False:` NEEDS_ATTENTION branch; `_manage_once`'s new snapshot-outage skip; `_manage_position`'s streak/final-cycle escalation logic and `gate = _quote_ok if in_guard else _quote_markable` selection.
- `scripts/uat_options_probe.py` — new `if filled is False:` branch ahead of the unchanged `if filled is None:` branch (insertion only, confirmed via diff).
- `tests/options/test_execution.py` — added `test_open_position_incomplete_unwind_returns_false` (3 parametrized cases).
- `tests/options/test_service.py` — added CR-02 section (3 tests, one preservation), WR-06 section (4 tests, 6 collected cases; removed the now-obsolete `test_manage_unquotable_leg_inside_guard_window_flags_needs_attention`, which encoded the WR-06 defect), WR-07 section (5 tests, 21 collected cases, 2 preservation).

## Decisions Made

See `key-decisions` in the frontmatter above (CR-02 return contract, `return pos`, the WR-06 streak/final-cycle rule, the WR-07 width rule and its "two-sided" interpretation). One additional note for future context: **11-07's key-link pattern `_quote_ok\(quotes\.get\(` is now superseded** — `_manage_position` no longer hardcodes `_quote_ok` at the manage-decision call site; the gate is chosen once via `gate = _quote_ok if in_guard else _quote_markable`, with `_quote_ok` retained only as the base validity check and the guard-window gate.

## Deviations from Plan

None beyond the commit-boundary note above (test files landed in Task 1's commit rather than being split three ways) — no Rule 1/2/3/4 auto-fixes were needed; every behavior matched the plan's exact specification (verified line-by-line against the interfaces section before writing each test).

## Known Stubs

None.

## Threat Flags

None — every new surface (the `False` unwind-outcome branch, the WR-06 streak, the WR-07 width gate) is covered by the plan's own `<threat_model>` (T-11-36 through T-11-50); no new trust boundary was introduced.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. Offline-only plan (no OpenD connection, no bot start, no `--live-1lot` probe run).

## Next Phase Readiness

- CR-02, WR-06 and WR-07 are closed; MSO-05, MSO-07 and MSO-08 hold as stated in the plan's `must_haves`.
- Deferred backlog (not gaps for this plan, recorded for future gap closures): WR-02, WR-03, WR-04, WR-08, WR-09 (now higher priority — this plan adds two more paths into NEEDS_ATTENTION), IN-01..IN-07, EX-03 (an entry-side exception before any unwind attempt is not yet routed through the False path).
- No blockers for the next phase; the options bot's execution/manage contracts are now internally consistent (tri-state `open_position`, streak-gated near-expiry escalation, width-gated marking).

---
*Phase: 11-multi-strategy-options-bot-bull-call-spread*
*Completed: 2026-09-24*

## Self-Check: PASSED

All 5 modified files and the SUMMARY.md exist on disk; all 3 task commit hashes
(3f32b6a, 7a6744f, 026844c) verified present in `git log --oneline --all`.
