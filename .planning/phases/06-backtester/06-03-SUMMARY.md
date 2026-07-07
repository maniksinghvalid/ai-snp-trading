---
phase: 06-backtester
plan: 03
subsystem: backtester-execution
tags: [backtester, execution, wave-2, BT-02]
dependency-graph:
  requires:
    - backtester (06-01 empty package)
    - tests.backtester.fixtures.make_ahead_only_5m_dataset (06-01)
  provides:
    - backtester.execution.SimulatedExecution
    - backtester.execution.SimulatedGateway
  affects:
    - backtester/harness.py (06-05, will construct SimulatedExecution/SimulatedGateway and call execution.on_bar() every closed bar)
tech-stack:
  added: []
  patterns:
    - "N+1-open fill rule (BT-02 anti-look-ahead) via feed.next_bar(code, after=<signal bar time_key>)"
    - "on_bar() bar-buffer-style hook (06-RESEARCH Pitfall 6 precedent) so a signature-locked method (manage_exit) can still anchor a next-bar lookup"
key-files:
  created:
    - backtester/execution.py
  modified:
    - tests/backtester/test_execution.py
decisions:
  - "06-03: manage_exit's live signature (code, qty, side, escalation_step, escalation_cadence, ttl) carries no bar/time_key, so SimulatedExecution exposes a public on_bar(bar) hook that records the latest bar per code -- the future harness (06-05) must call it every closed bar (mirrors the bar_buffer wiring discipline already required for PositionManager's swing-low trail, 06-RESEARCH Pitfall 6) so manage_exit has an anchor for its own next_bar(code, after=...) lookup"
  - "06-03: FillEvent.fill_time and the exit_fills/fills dict rows store bar time_key STRINGS (not datetime objects) -- matches every other backtester module's time_key convention (feed.py, fixtures.py) even though the live FillEvent dataclass annotates fill_time: datetime; the dataclass does not enforce the annotation at runtime and the plan's own action step specifies fill_time=next_bar[\"time_key\"]"
  - "06-03: reworded the module docstring after Task 2 to avoid a self-matching grep false positive on the acceptance-criteria check 'grep -L bot.gateway|MoomooGateway|place_order' -- the docstring's own negation language (e.g. 'no place_order') satisfied the very pattern its acceptance grep checks for absence of, the same pitfall documented in 06-01's SUMMARY for test_execution.py's docstring"
metrics:
  duration_minutes: 20
  completed: 2026-07-07
---

# Phase 06 Plan 03: SimulatedExecution + SimulatedGateway Summary

Built `backtester/execution.py`: `SimulatedExecution` (the BT-02 N+1-open fill mechanic,
proven against the ahead-only look-ahead fixture) and `SimulatedGateway` (a two-method
broker stub satisfying only `get_positions`/`get_equity`, the two calls `SignalEngine`/
`RiskEngine` make against the reused live pipeline). Both classes import zero broker code
(no `bot.gateway`, no moomoo SDK) — the entire "no broker in a backtest" trust boundary
(T-06-05) is satisfied by construction.

## What Was Built

**Task 1 — `SimulatedExecution`:**
- `consume_intent(intent)`: looks up `feed.next_bar(intent.code, after=intent.source_signal.bar.time_key)`;
  fills at `next_bar["open"] + slippage_usd`, never `intent.entry_price` (the signal bar's
  close) — the BT-02 look-ahead proof. Returns `None` when no next bar exists (signal on the
  last bar of the dataset), matching the live D-05 abandon path.
- `manage_exit(code, qty, side, escalation_step, escalation_cadence, ttl)`: matches
  `ExecutionEngine.manage_exit`'s live signature exactly (no TTL/escalation replay — no
  wall-clock in a backtest). Fills symmetrically at the next bar's open + slippage
  (Assumption A2). Since this signature carries no bar/time_key, added a small `on_bar(bar)`
  hook (a harness-facing contract, analogous to `bar_buffer` wiring) that records the latest
  bar seen per code as the anchor for `manage_exit`'s own `next_bar(code, after=...)` lookup.
- Every entry and exit fill is appended to `self.fills`; exits are additionally recorded in
  `self.exit_fills` — the harness's future trade-log builder reads these, never a `trades` DB
  row (bot/ code never writes one for backtest runs).
- Reuses the real `bot.execution.events.FillEvent` dataclass unchanged (no invented fields).
- Added the manage_exit test the plan's Task 1 acceptance criteria required (not present in
  the 06-01 Wave-0 stub): asserts an int return and a captured `exit_fills` row at the correct
  N+1 open price.

**Task 2 — `SimulatedGateway`:**
- `get_positions(refresh_cache=True)`: resolves a zero-arg `position_manager_ref` callable,
  reads its `_positions` dict, returns `(0, pd.DataFrame(rows))` for every code whose
  `phase.value` is not `AWAITING_FILL`/`CLOSED` (ret=0 == RET_OK, matching the live
  `gateway.get_positions()` contract `SignalEngine` Gate 4 expects).
- `get_equity()`: returns fixed `100_000.0` (only reached if `cfg.sizing_equity_usd is None`
  — current `rules.json` sets it explicitly, so this path is bypassed in practice).
- No other broker methods exist on the class (verified by grep in acceptance criteria).
- Added a test constructing `SimulatedGateway` over a fake manager (`SimpleNamespace` with
  `_positions`) holding one `ACTIVE` and one `CLOSED` position — asserts only the `ACTIVE`
  code is returned, and `get_equity() == 100_000.0`.

## Verification

```
python3 -m pytest tests/backtester/test_execution.py -q   → 5 passed
python3 -m pytest tests/backtester/ -q                     → 12 passed, 2 skipped (test_feed already GREEN from 06-02; test_harness/test_report still importorskip-guarded until 06-04/06-05)
python3 -m pytest tests/ -q                                → 601 passed, 3 skipped (no regression, no bot/ file touched)
grep -n 'from bot.execution.events import FillEvent' backtester/execution.py   → present
grep -c 'asyncio.sleep' backtester/execution.py                                → 0
grep -nE 'def (place_order|get_ask_price|subscribe|place_stop_order|cancel_order)' backtester/execution.py → no matches
grep -L 'bot.gateway|MoomooGateway|place_order' backtester/execution.py        → lists backtester/execution.py (no broker path)
wc -l backtester/execution.py                                                  → 128 (>= min_lines 90)
```

No file under `bot/` was modified — full suite stability confirms no regression.

## Deviations from Plan

**1. [Rule 2 - missing critical functionality] Added `on_bar(bar)` hook to `SimulatedExecution`**
- **Found during:** Task 1, implementing `manage_exit`
- **Issue:** `manage_exit`'s live signature (fixed, must match `ExecutionEngine.manage_exit`
  exactly per the plan) carries no bar or time_key argument, so there is no way for the
  method itself to know "the next bar" to fill at without some external anchor.
- **Fix:** Added a small public `on_bar(bar)` method that records `bar["time_key"]` per
  `bar["code"]`; `manage_exit` uses the most recently recorded time_key as its `after` anchor
  for `feed.next_bar(...)`. This mirrors the `bar_buffer` wiring discipline the plan's own
  06-RESEARCH Pitfall 6 already requires for `PositionManager`'s swing-low trail — the
  harness (06-05) must call this every closed bar, documented in the method's docstring and
  the `__init__`'s inline comment so 06-05 does not miss the wiring requirement.
- **Files modified:** backtester/execution.py
- **Commit:** 11c39e9

**2. [Rule 1 - Bug] Reworded execution.py module docstring to avoid grep false positive**
- **Found during:** Task 2 verification (`grep -L 'bot.gateway\|MoomooGateway\|place_order' backtester/execution.py`
  printed nothing instead of listing the file)
- **Issue:** The module docstring explained the "no broker surface" contract using the
  literal substrings `place_order` and `bot.gateway` (to say these do NOT exist here) — this
  satisfied the acceptance grep's pattern, so `grep -L` (files NOT matching) correctly
  excluded the file from its output. Same class of self-inflicted false positive documented
  in 06-01's SUMMARY for `test_execution.py`'s docstring.
- **Fix:** Reworded the two docstring lines to describe the same guarantee without embedding
  the literal marker strings.
- **Files modified:** backtester/execution.py
- **Commit:** b416a4f

## Known Stubs

None — both classes are fully wired to their real, described behavior; no placeholder
values or empty-data paths beyond what the plan's own contract specifies (e.g. `get_equity`
returning a fixed `100_000.0` is the plan's explicit, intentional behavior, not a stub).

## Threat Flags

None — this plan's only new surface (`SimulatedExecution`/`SimulatedGateway`) is exactly the
surface named in the plan's own `<threat_model>` (T-06-05 accidental-broker-path, T-06-06
look-ahead), both of which are the mitigations this plan implements, not new unmitigated
surface.

## Self-Check: PASSED

- FOUND: backtester/execution.py
- FOUND commit 11c39e9 (Task 1)
- FOUND commit b416a4f (Task 2)
