---
phase: 06-backtester
plan: 01
subsystem: backtester-scaffold
tags: [backtester, test-scaffold, wave-0]
dependency-graph:
  requires: []
  provides:
    - backtester (importable empty package)
    - tests.backtester.fixtures.make_ahead_only_5m_dataset
    - tests.backtester.fixtures.make_trade_log
    - tests/backtester/test_feed.py (importorskip stub, BT-04 target)
    - tests/backtester/test_execution.py (importorskip stub, BT-02 target)
    - tests/backtester/test_harness.py (importorskip stub, BT-01 target)
    - tests/backtester/test_report.py (importorskip stub, BT-03 target)
  affects:
    - backtester/feed.py (06-02, will turn test_feed.py GREEN)
    - backtester/execution.py (06-03, will turn test_execution.py GREEN)
    - backtester/report.py (06-04, will turn test_report.py GREEN)
    - backtester/harness.py (06-05, will turn test_harness.py GREEN)
tech-stack:
  added: []
  patterns:
    - "importorskip Wave-0 stub pattern (matches 03-01, 05-00 precedent)"
key-files:
  created:
    - backtester/__init__.py
    - tests/backtester/__init__.py
    - tests/backtester/fixtures.py
    - tests/backtester/test_feed.py
    - tests/backtester/test_execution.py
    - tests/backtester/test_harness.py
    - tests/backtester/test_report.py
  modified:
    - .gitignore
decisions:
  - "06-01: fixture bars use OHLC values engineered so bar N close (105.00) and bar N+1 open (103.50) are trivially distinguishable — any future look-ahead regression in SimulatedExecution will fail loudly against a hardcoded number, not a subtle float-equality coincidence"
  - "06-01: test_execution.py builds a hand-rolled _FakeFeed (not backtester.feed.SimulatedBarFeed) so it depends only on fixtures.py, matching the plan's own key_links and avoiding an inter-plan module dependency before 06-02 lands"
  - "06-01: test_harness.py imports backtester.feed via a second pytest.importorskip (not a bare import) since backtester.feed and backtester.harness are built in separate plans (06-02, 06-05) — defensive against wave-order edge cases even though 06-05 depends_on 06-02"
metrics:
  duration_minutes: 12
  completed: 2026-07-07
---

# Phase 06 Plan 01: Backtester Wave-0 Scaffold Summary

Created the `backtester/` package skeleton, the `tests/backtester/` test package, four
`pytest.importorskip`-guarded failing test stubs (one per downstream implementation plan:
06-02 feed, 06-03 execution, 06-04 report, 06-05 harness), and the shared synthetic
ahead-only-5m dataset + trade-log fixtures that the BT-02 look-ahead proof and BT-03 report
tests depend on.

## What Was Built

**Task 1 — Package skeletons + gitignore:**
- `backtester/__init__.py` — empty package marker (docstring only, mirrors `bot/__init__.py`)
- `tests/backtester/__init__.py` — empty test-package marker (mirrors `tests/scanner/__init__.py`)
- `.gitignore` — appended `backtester/cache/` and `backtester/runs/` under a new comment
  header; all prior lines preserved (append-only, verified diff)

**Task 2 — Shared fixtures + four test stubs:**
- `tests/backtester/fixtures.py`:
  - `make_ahead_only_5m_dataset()` — 6-bar deterministic 5m session for `US.TEST`. Bar index
    3 ("bar N") breaks out (close=105.00, clears the running hod of 101.00); bar index 4
    ("bar N+1") gaps down to open=103.50. Bar index 5 is the last bar and also clears the
    running hod, but has no successor — proves "signal on the last bar -> no fill."
    hod/lod/cum_volume are pre-computed as running max-high/min-low/summed-volume per bar.
  - `make_trade_log()` — 4 trades (2 winners, 2 losers) using the trade-log contract keys
    (`code`, `entry_price`, `exit_price`, `quantity`, `exit_reason`, `r_multiple`,
    `closed_at`), with hand-computed expected `win_rate=0.5`, `avg_r_multiple=0.375`,
    `profit_factor=1.2`, `max_drawdown_usd=1000.0` documented in the docstring for the 06-04
    report test to assert against.
- `tests/backtester/test_feed.py` — asserts (once `backtester.feed` exists) that
  `SimulatedBarFeed.replay()` yields chronologically-ordered bars with correct running
  hod, `next_bar()` returns the strictly-next bar or `None` at the end, and an
  out-of-window `start` raises `BacktestWindowError` (mocks `yfinance.download`).
- `tests/backtester/test_execution.py` — asserts `SimulatedExecution.consume_intent` fills
  at bar N+1's open (103.50), never `intent.entry_price` (105.00), and that a last-bar
  signal returns `None`. Uses a hand-rolled `_FakeFeed` over the fixture bars plus real
  `OrderIntent`/`SignalEvent`/`BarEvent` dataclasses (no invented fields).
- `tests/backtester/test_harness.py` — mirrors `tests/test_main_wiring.py`'s real-construction
  pattern: drives the real `BacktestHarness` with only the feed's yfinance seam patched,
  asserts `position_manager._gateway is None`, and that a full replay of the ahead-only
  fixture produces `len(harness.trade_log) >= 1`.
- `tests/backtester/test_report.py` — asserts `compute_metrics(make_trade_log())` matches
  the fixture's documented metrics, `compute_metrics([])` returns a zeroed dict without
  raising, and `write_report()` writes `trades.csv` (with `entry_price`/`exit_price`/
  `quantity`/`exit_reason` columns) + `summary.json` consistent with the metrics dict.

All four stubs open with `pytest.importorskip("backtester.<module>")` so they SKIP cleanly
(0 errors, 0 failures) until each corresponding module is built in 06-02 through 06-05.

## Verification

```
python3 -m pytest tests/backtester/ -q   → 4 skipped
python3 -m pytest tests/ -q              → 589 passed, 5 skipped (1 pre-existing skip + 4 new)
python3 -c "import backtester"           → exits 0
git check-ignore backtester/cache/ backtester/runs/  → both print (gitignore active)
```

No file under `bot/` was touched — full suite stability confirms no regression.

## Deviations from Plan

None — plan executed exactly as written. One self-inflicted false-positive fixed inline
during Task 2 verification (not a deviation from the plan's intent, a correction to match
its own acceptance criterion):

**[Rule 1 - Bug] test_execution.py docstring literal string broke its own grep acceptance check**
- **Found during:** Task 2 verification (`grep -L 'pytest.mark.asyncio' tests/backtester/test_execution.py` returned empty instead of listing the file)
- **Issue:** The module docstring explained the async convention using the literal text `@pytest.mark.asyncio` (to say the file does NOT use it), which itself satisfied the grep pattern the acceptance criterion checks against — so `grep -L` (files NOT matching) correctly found a match and excluded the file.
- **Fix:** Reworded the docstring to describe the convention without embedding the literal marker string (e.g. "no asyncio pytest marker").
- **Files modified:** tests/backtester/test_execution.py
- **Commit:** 130ff10 (included in Task 2's commit — caught before commit, not a separate fix-up)

## Known Stubs

None applicable in the stub-tracking sense — all four `test_*.py` files are Wave-0
scaffolding by design (the plan's explicit purpose), guarded by `importorskip` and
documented as such in both the plan and this summary. They are not silent/undocumented
stubs; they are the intended deliverable of this plan.

## Self-Check: PASSED

- FOUND: backtester/__init__.py
- FOUND: tests/backtester/__init__.py
- FOUND: tests/backtester/fixtures.py
- FOUND: tests/backtester/test_feed.py
- FOUND: tests/backtester/test_execution.py
- FOUND: tests/backtester/test_harness.py
- FOUND: tests/backtester/test_report.py
- FOUND commit 12458cf (Task 1)
- FOUND commit 130ff10 (Task 2)
