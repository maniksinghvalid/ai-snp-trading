---
phase: quick-260926-kvt
plan: 01
status: complete
subsystem: options
tags: [options, safety, broker-reconcile, entry-guard, regex]

# Dependency graph
requires:
  - phase: 08-options-premium-selling
    provides: OptionsBot._try_open entry path, MoomooGateway.get_option_positions, one-position-per-underlying busy-set gate
provides:
  - Per-candidate broker-holding guard in OptionsBot._try_open that fails closed
affects: [options-bot-entry, options-safety, phase-08-options-premium-selling]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fresh per-candidate broker read (not one scan-level snapshot) immediately before the first DB write, so a slow prior candidate's fill wait cannot go stale"
    - "Fail-closed on broker read exception: log + return None, never proceed to insert rows or call the executor"

key-files:
  created: []
  modified:
    - bot/options/service.py
    - tests/options/test_service.py

key-decisions:
  - "Guard placed after `if qty < 1: return None` and before `position_id = uuid4().hex` so a skip writes zero rows and calls zero executor methods"
  - "_OPTION_UNDERLYING_RE captures the ticker before the 6-digit expiry (mirrors gateway._OPTION_CODE_RE) so US.TLTW is never attributed to US.TLT"
  - "Skip is logged only (warning + no Telegram alert, no append_audit) since it is a non-event, not a trade action"

patterns-established:
  - "Broker-truth check happens per-candidate inside _try_open, not once per scan in _scan_and_open, to avoid staleness from earlier candidates' fill-wait latency"

requirements-completed: [QUICK-260926-kvt]

# Metrics
duration: 15min
completed: 2026-09-26
---

# Quick Task 260926-kvt: Block options entry when broker already holds foreign qty on the underlying

**Per-candidate broker-holding guard in `OptionsBot._try_open` that skips (and fails closed) any underlying where the broker already holds ANY non-zero option qty, closing the live 2026-09-25 TLT net-zero incident.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 (TDD: red tests, then green implementation)
- **Files modified:** 2

## Accomplishments

- Added `_OPTION_UNDERLYING_RE` (module constant in `bot/options/service.py`) that captures the full ticker before the 6-digit expiry digit block, so `US.TLTW261120P75000` is never attributed to `US.TLT`.
- Added a per-candidate broker-holding guard inside `OptionsBot._try_open`: one fresh `await self._gateway.get_option_positions()` read per sized candidate, before any DB row is inserted or the executor is called.
  - Any broker code on the candidate's underlying with non-zero qty → skip, `_logger.warning("options_entry_foreign_holding", underlying=..., codes=[...], leg_codes=[...])`, no DB rows, no executor call.
  - A broker read exception → fail closed: `_logger.error("options_entry_broker_read_failed", underlying=..., exc_info=True)`, skip, no DB rows, no executor call. `asyncio.CancelledError` is re-raised, matching the file's existing async error pattern.
  - Holdings on other underlyings (including this bot's own open legs there) never block the candidate underlying — verified by a dedicated non-over-blocking regression test.
- Five new regression tests added to the "Entry scan" section of `tests/options/test_service.py`, written red-first per the plan's TDD gate.

## Task Commits

Each task was committed atomically:

1. **Task 1: Red — add 5 broker-holding guard tests** - `f2d56f2` (test)
2. **Task 2: Green — per-candidate broker-holding guard in `OptionsBot._try_open`** - `6bf02b3` (fix)

**Plan metadata:** committed separately by the orchestrator (not by this executor per plan `<output>` instructions).

## Files Created/Modified

- `tests/options/test_service.py` - 5 new tests: broker holds a leg code, broker holds another option on the same underlying, broker holds only own legs elsewhere (no-over-blocking guard), broker read fails (fail-closed), and the underlying-regex ticker-boundary test.
- `bot/options/service.py` - `import re`; new `_OPTION_UNDERLYING_RE` module constant; broker-holding guard block inserted into `_try_open` between the `qty < 1` sizing check and `position_id = uuid4().hex`; one-sentence docstring addition documenting the SAFE-OG-01 refusal behavior.

## Decisions Made

- Guard runs once per sized candidate (not once per scan) — a prior `_try_open` in the same scan can spend minutes waiting on fills, so a scan-level snapshot would go stale by the time a later candidate is evaluated.
- No Telegram alert and no `append_audit` call on a skip — this is a non-event (the bot declining to act), not a trade action; only the structured `_logger.warning`/`_logger.error` calls fire, consistent with how the rest of the entry scan logs skip reasons (`options_entry_scan_skipped`, `options_entry_scan_no_stock_ids`, etc.).
- Left a `ponytail:` comment directly above the guard's foreign-set check, per the plan's locked design item 3, documenting the coupling to `_scan_and_open`'s `busy` set: if one-position-per-underlying is ever relaxed, this guard will need to subtract the bot's own ACTIVE leg qty before deciding a holding is "foreign."

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The options entry path now enforces the CLAUDE.md Phase 8 invariant ("the options bot never touches broker option codes not in its own option_legs") on entry, not just at reconcile time.
- Full test suite green (1156 passed, 1 skipped = 1157 collected) and `tests/options` green (193 passed), matching the plan's expected baseline exactly.
- No blockers for closing this quick task.

---
*Phase: quick-260926-kvt*
*Completed: 2026-09-26*

## Self-Check: PASSED

- FOUND: bot/options/service.py
- FOUND: tests/options/test_service.py
- FOUND: .planning/quick/260926-kvt-options-bot-block-entry-when-broker-hold/260926-kvt-SUMMARY.md
- FOUND commit: f2d56f2
- FOUND commit: 6bf02b3
