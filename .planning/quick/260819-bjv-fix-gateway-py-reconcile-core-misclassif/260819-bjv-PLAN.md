---
phase: quick
plan: 260819-bjv
type: execute
wave: 1
depends_on: []
files_modified:
  - bot/gateway/gateway.py
  - tests/gateway/test_gateway.py
autonomous: true
requirements: [SAFE-OG-01, SAFE-03]

must_haves:
  truths:
    - "An option code held at the broker never produces a reconcile_external_position_ignored warning from the equity gateway's reconcile loop"
    - "An option code held at the broker is never adopted into the equity StateStore / PositionManager"
    - "A manual operator EQUITY position (US.NIO qty 100, not bot-owned) is still skipped and still warned about (SAFE-OG-01 preserved)"
    - "The external-equity warning is emitted once per code per process, not once per 75s reconcile cycle"
    - "Both reconcile_once and startup_reconcile get the behavior from the single shared _reconcile_core"
  artifacts:
    - path: "bot/gateway/gateway.py"
      provides: "Option-code skip + once-per-code external warning inside _reconcile_core's orphan-adoption loop"
      contains: "_OPTION_CODE_RE"
    - path: "tests/gateway/test_gateway.py"
      provides: "Regression tests for option-leg skip and preserved equity behavior"
      contains: "class TestOptionCodesSkippedByReconcile"
  key_links:
    - from: "bot/gateway/gateway.py::_reconcile_core"
      to: "bot/gateway/gateway.py::_OPTION_CODE_RE (line 114)"
      via: "reuse of the existing module-level option-code pattern (same one get_option_positions uses)"
      pattern: "_OPTION_CODE_RE\\.match"
---

<objective>
Stop the equity bot's SAFE-03 reconcile loop from classifying the options bot's own
iron-condor legs as "external positions" — 4 `reconcile_external_position_ignored`
warnings every ~75s, forever.

Root cause (already diagnosed, do NOT re-derive): `_reconcile_core`'s orphan-adoption
scan (bot/gateway/gateway.py ~line 1518-1539) applies two equity assumptions to every
broker code, including option codes: `qty > 0` (short legs fail → `not_long`) and
`store.has_pending_intent(code) or code in open_pos_codes` (long wings fail →
`not_bot_owned`, because option legs live in OptionsStore's separate
data/options_state.db, not the equity StateStore).

Fix: option codes are structurally out of scope for the equity StateStore /
PositionManager that `_reconcile_core` serves — `OptionsService.reconcile()` is the sole
owner of option-position reconciliation. Skip them at the top of the adoption loop.
Secondary: dedupe the remaining legit external-equity warnings to once per code per
process so manual operator holdings stop spamming every cycle.

Purpose: kill log spam and remove a wrong classification that could, if either guard
ever loosened, let the equity bot adopt an options leg it must never touch (CLAUDE.md
Phase 8 invariant).
Output: one guard + one dedup set in `_reconcile_core`, plus three regression tests.

Out of scope (hard constraints):
- Do NOT restart or otherwise touch the two live bot processes — code + tests only.
- Do NOT modify `bot/options/service.py`'s own reconcile path — it works.
- No new dependencies. Reuse the existing module-level `_OPTION_CODE_RE`
  (bot/gateway/gateway.py:114) — the same predicate `get_option_positions` already uses.
  Do NOT thread `position_side` through `_build_broker_map`; that row dict carries only
  `qty`/`avg_cost` and widening it is a bigger diff than the regex already in the module.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@bot/gateway/gateway.py
@tests/gateway/test_gateway.py

Key facts already verified — read the cited lines, do not re-investigate:
- `_OPTION_CODE_RE = re.compile(r"^US\.[A-Z]+\d{6}[CP]\d+$")` — gateway.py line 114.
  Matches `US.SLV260918P50500`; does not match `US.NIO`.
- `_reconcile_core` — gateway.py line 1359. Shared by `reconcile_once` (line 1623) and
  `startup_reconcile` (line 1662). Fixing it here fixes both callers.
- Orphan-adoption loop — gateway.py lines 1520-1539; the ownership/long guard and the
  `reconcile_external_position_ignored` warning are at 1526-1539.
- The earlier close/qty-drift loops in `_reconcile_core` iterate equity-store-derived
  codes (`state_codes` / `manager._positions`), so option codes can never reach them —
  the adoption loop is the only site needing the guard.
- `MoomooGateway.__init__` — gateway.py line 398; `self._bg_tasks: set = set()` at line 405
  is the placement precedent for a new instance-level set.
- Existing SAFE-OG-01 tests: `class TestOrphanOwnershipGuard` — test_gateway.py line 1196.
  `_make_gateway_with_mocks()` + `pd.DataFrame([...])` + `AsyncMock` is the fixture pattern
  to copy (see test_reconcile_once_does_not_adopt_manual_long, line 1213).
- No test in the repo currently asserts on gateway log events, so the new tests patch
  `bot.gateway.gateway._logger` with a `MagicMock` and read `.warning.call_args_list`.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add failing regression tests for option-code reconcile behavior</name>
  <files>tests/gateway/test_gateway.py</files>
  <action>
Append a new test class `TestOptionCodesSkippedByReconcile` after `TestOrphanOwnershipGuard`
(which ends around line 1500). Copy the mock scaffolding from
`test_reconcile_once_does_not_adopt_manual_long` (line 1213): `_make_gateway_with_mocks()`,
`gw.get_positions = AsyncMock(return_value=(0, broker_df))`, mock manager with empty
`_positions` / `_exiting`, mock store with `get_open_positions() -> []` and
`has_pending_intent() -> False`, `mock_alerter.send = AsyncMock()`, driven via
`asyncio.run(gw.reconcile_once(store=..., manager=..., alerter=...))`.

To observe log events, wrap each `asyncio.run` call in
`unittest.mock.patch("bot.gateway.gateway._logger")` and collect the emitted event names
from the mock's `warning.call_args_list` (first positional arg of each call).

Write three tests:

1. `test_option_short_leg_not_flagged_and_not_adopted` — broker_df holds the real short
   leg `US.SLV260918P50500`, qty -1, average_cost 0.55. Assert: no
   `reconcile_external_position_ignored` event in the captured warning events,
   `mock_store.insert_orphan_position` not called, `mock_manager.adopt_orphan` not called,
   and `result["adopted"] == []`.

2. `test_option_long_wing_not_flagged_and_not_adopted` — same shape with the long wing
   `US.SLV260918P49500`, qty +1. Same four assertions. (This is the leg that currently
   fails with reason "not_bot_owned".)

3. `test_manual_equity_still_ignored_and_warned_once` — broker_df holds `US.NIO`, qty 100,
   average_cost 4.20 (manual operator equity, not bot-owned). Assert: SAFE-OG-01 preserved —
   `insert_orphan_position` not called, `adopt_orphan` not called, `result["adopted"] == []`,
   AND `reconcile_external_position_ignored` IS present in the first cycle's warning events.
   Then run `reconcile_once` a SECOND time on the same gateway instance under a fresh
   `_logger` patch and assert the event is NOT re-emitted (per-code, per-process dedup).

Follow existing file conventions: snake_case, docstring per test explaining the regression,
no new imports beyond what the file already imports (`asyncio`, `pandas as pd`,
`MagicMock`, `AsyncMock`, `patch`) — add `patch` to the existing mock import line if absent.
  </action>
  <verify>
    <automated>python3 -m pytest tests/gateway/test_gateway.py -q -k "TestOptionCodesSkippedByReconcile" 2>&1 | tail -20</automated>
  </verify>
  <done>
The three tests exist and run. Expected RED state before Task 2: tests 1 and 2 FAIL on the
`reconcile_external_position_ignored` assertion; test 3 fails only on its second-cycle
dedup assertion (its SAFE-OG-01 assertions already pass). Record the observed failure
output — a test that passes here proves nothing and must be strengthened before proceeding.
  </done>
</task>

<task type="auto">
  <name>Task 2: Skip option codes in _reconcile_core and dedupe external warnings</name>
  <files>bot/gateway/gateway.py</files>
  <action>
Two edits, both minimal:

(a) `MoomooGateway.__init__` (line 398): after `self._bg_tasks: set = set()` (line 405),
add `self._external_ignored_logged: set = set()` with a one-line comment that it is a
session-scope dedup set for the `reconcile_external_position_ignored` warning, plus a
`# ponytail:` note that it is bounded by the number of distinct broker codes and is never
cleared (a code's classification does not change within a process run).

(b) `_reconcile_core` orphan-adoption loop (lines 1520-1539):

- Immediately after the existing `if code in exiting_codes: continue` (line 1523-1524),
  add the option-code skip:
  `if _OPTION_CODE_RE.match(code): continue`
  with a comment stating that option codes are out of scope for the equity StateStore /
  PositionManager this function serves, that `OptionsService.reconcile()` is their sole
  owner (CLAUDE.md Phase 8 invariant), and referencing the shared `_OPTION_CODE_RE`
  predicate used by `get_option_positions`. Emit at most a `_logger.debug` here — never a
  warning; these are expected, permanent, and not anomalies.

- In the existing SAFE-OG-01 guard block (lines 1526-1539), keep the `_is_long` /
  `_bot_owned` / `_reason` computation and the `continue` exactly as they are; wrap ONLY
  the `_logger.warning("reconcile_external_position_ignored", ...)` call in a
  `if code not in self._external_ignored_logged:` check that adds the code to the set
  before logging. The skip behavior itself stays unconditional.

Do not change the guard's semantics, the duplicate-code SELECT-before-INSERT guard, or any
other branch of `_reconcile_core`. Both `reconcile_once` and `startup_reconcile` inherit
the fix through this one function — no per-caller edits.
  </action>
  <verify>
    <automated>python3 -m pytest tests/gateway/test_gateway.py -q -k "TestOptionCodesSkippedByReconcile or TestOrphanOwnershipGuard or TestReconciliation" 2>&1 | tail -10 && python3 -m pytest -q 2>&1 | tail -5</automated>
  </verify>
  <done>
All three new tests pass, the pre-existing `TestOrphanOwnershipGuard` and
`TestReconciliation` classes still pass, and the full suite reports 0 failures with at
least 1130 passed / 1 skipped (was 1127 passed / 1 skipped before this plan). No live bot
process was started, stopped, or signalled; `bot/options/service.py` is unmodified
(`git diff --name-only` shows exactly bot/gateway/gateway.py and tests/gateway/test_gateway.py).
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| broker → gateway | `position_list_query` rows (codes, qty) enter `_build_broker_map` and drive adoption decisions on a paper account shared with a human operator |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-bjv-01 | Elevation of Privilege | `_reconcile_core` orphan adoption | mitigate | Option codes skipped before the ownership/long guard — the equity bot can never adopt or manage an options-bot leg even if a later change loosens `_is_long`/`_bot_owned` (CLAUDE.md Phase 8 invariant). Task 2 (b). |
| T-bjv-02 | Repudiation | external-position warning dedup | accept | Deduping to once-per-code-per-process reduces the audit trail for manual holdings to a single line per run. Accepted: classification is static within a process, `append_audit` is untouched, and adoption decisions are re-evaluated every cycle regardless of logging. |
| T-bjv-03 | Denial of Service | log volume | mitigate | The 4-lines-per-75s spam (900+ per log file) is the reported symptom; both edits reduce steady-state reconcile logging to zero for unchanged accounts. |
| T-bjv-SC | Tampering | package installs | accept | No packages installed — stdlib `re` and the existing module-level `_OPTION_CODE_RE` only. |
</threat_model>

<verification>
- `python3 -m pytest -q` → 0 failures, ≥1130 passed, 1 skipped.
- `git diff --name-only` → exactly `bot/gateway/gateway.py` and `tests/gateway/test_gateway.py`.
- `grep -n "_OPTION_CODE_RE" bot/gateway/gateway.py` → the pattern is referenced in both
  `get_option_positions` and `_reconcile_core` (no second regex was defined).
- Manual read-back: the option-code skip sits inside `_reconcile_core`, not in
  `reconcile_once` or `startup_reconcile` — one guard, both callers.
</verification>

<success_criteria>
- Option-code broker positions (short legs and long wings alike) produce neither a
  `reconcile_external_position_ignored` warning nor an adoption attempt from the equity bot.
- Manual operator equity positions are still skipped with an explicit warning, now logged
  once per code per process instead of every ~75s cycle.
- Full suite green; diff limited to two files; no live process touched.
</success_criteria>

<output>
Create `.planning/quick/260819-bjv-fix-gateway-py-reconcile-core-misclassif/260819-bjv-SUMMARY.md` when done
</output>
