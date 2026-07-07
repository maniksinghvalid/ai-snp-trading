---
slug: open-risk-stop-key-bug
status: resolved
trigger: "Open Risk metric in daily summary is computed wrong. In bot/service/alerter.py:206-210, format_daily_summary computes open_risk = sum((entry_price - r.get(\"stop\")) * remaining_quantity), but position rows from StateStore.get_open_positions() (SELECT * FROM positions) have no \"stop\" column — they have initial_stop and trail_stop. So r.get(\"stop\") is always None->0.0, the stop subtraction never happens, and \"Open Risk\" is actually sum(entry_price * remaining_quantity) = gross entry notional, NOT risk-to-stop. The docstring at line 192 also falsely claims a \"stop\" key. Fix so Open Risk uses the correct stop (trail_stop if set, else initial_stop) and reports true risk-to-stop."
created: 2026-06-30
updated: 2026-06-30
---

# Debug Session: open-risk-stop-key-bug

## Symptoms

- **Expected behavior:** The "Open Risk: $X" line in the Telegram Daily Summary should report the bot's true risk-to-stop across open positions — i.e. sum over open positions of (entry_price - effective_stop) * remaining_quantity, where effective_stop = trail_stop if set, else initial_stop. For a long at breakeven (trail_stop == entry) this is 0; for a long whose stop is below entry it is the dollars at risk if every stop fills.
- **Actual behavior:** Open Risk is computed as sum(entry_price * remaining_quantity) — gross entry notional, because the per-share stop term evaluates to 0 for every row. The number is mislabeled: it is NOT risk-to-stop.
- **Error messages:** None — silent numeric defect. No exception; the summary renders a plausible-looking dollar figure (e.g. "$17197.46") that is wrong.
- **Timeline:** Latent since `format_daily_summary` was written (ALERT-03). Never surfaced because there was no automated assertion tying the formula to actual stop values, and the dollar figure looks reasonable at a glance.
- **Reproduction:** Have >=1 non-CLOSED row in the `positions` table, run the EOD report routine (bot/service/bot.py:692-705), and observe the Telegram Daily Summary "Open Risk" line. Compare against a hand-computed sum of (entry_price - COALESCE(trail_stop, initial_stop)) * remaining_quantity — they differ by exactly the dropped stop term.

## Current Focus

- hypothesis: In bot/service/alerter.py:206-210, `format_daily_summary` reads `r.get("stop")` but the dicts returned by `StateStore.get_open_positions()` (bot/state/store.py:364-385, `SELECT * FROM positions`) expose `initial_stop` and `trail_stop` columns — there is no `stop` key. `r.get("stop")` therefore returns None, `or 0.0` coerces it to 0.0, and the subtraction `(entry_price - 0.0)` collapses Open Risk to `sum(entry_price * remaining_quantity)` (gross notional, not risk-to-stop).
- test: A unit test on `format_daily_summary` that passes open_positions rows mirroring the real schema (keys: entry_price, initial_stop, trail_stop, remaining_quantity) and asserts the rendered "Open Risk" equals sum((entry_price - effective_stop) * remaining_quantity) with effective_stop = trail_stop if not None else initial_stop. The test must FAIL against current code (which ignores the stop) and PASS after the fix.
- expecting: Before fix — assertion fails because Open Risk == sum(entry_price * qty). After fix — Open Risk reflects the stop term.
- next_action: Write failing test in tests/service/test_alerter.py, then apply fix to bot/service/alerter.py lines 192-210.
- reasoning_checkpoint:
    hypothesis: "format_daily_summary computes open_risk using r.get('stop') which is always None
      because get_open_positions() returns SELECT * FROM positions rows that have initial_stop
      and trail_stop columns — no 'stop' column — so open_risk = sum(entry_price * qty)
      (gross notional) instead of sum((entry_price - effective_stop) * qty)."
    confirming_evidence:
      - "bot/service/alerter.py:207 reads r.get('stop') verbatim — directly observed in source."
      - "bot/state/store.py:380-385 returns dict(r) for SELECT * FROM positions — no aliasing."
      - "bot/state/migrations confirms positions table has initial_stop + trail_stop, no stop column."
      - "bot/service/bot.py:692-705 passes self._store.get_open_positions() with no remapping."
    falsification_test: "A unit test that passes rows with initial_stop/trail_stop (no 'stop' key)
      and checks Open Risk == sum((entry - effective_stop) * qty). If the test passes BEFORE the
      fix, the hypothesis is wrong. It must fail before the fix and pass after."
    fix_rationale: "Replace r.get('stop') with effective_stop computed per-row from trail_stop
      if not None else initial_stop. Clamp per-position contribution at max(0, ...) so a
      locked-profit trailing stop (trail > entry) doesn't produce negative 'risk' offsetting others."
    blind_spots: "If caller ever remaps rows to add a 'stop' key before calling format_daily_summary
      in a path other than bot.py:692-705, fix would not affect those paths — but evidence shows
      no such remapping exists."
- tdd_checkpoint:
    test_file: "tests/service/test_alerter.py"
    tests:
      - test_format_daily_summary_open_risk_uses_correct_stop_columns
      - test_format_daily_summary_open_risk_clamped_at_zero_for_locked_profit
    status: "red — both fail as expected before fix"
    failure_output: |
      FAILED test_format_daily_summary_open_risk_uses_correct_stop_columns
        Expected 'Open Risk: $300.00' but got 'Open Risk: $25000.00'
        (gross notional 150*100 + 200*50 confirms r.get('stop')==None->0.0)
      FAILED test_format_daily_summary_open_risk_clamped_at_zero_for_locked_profit
        Expected 'Open Risk: $100.00' but got 'Open Risk: $8000.00'

## Evidence

- timestamp: 2026-06-30 — bot/service/alerter.py:206-210 source confirmed: `open_risk = sum(((r.get("entry_price") or 0.0) - (r.get("stop") or 0.0)) * (r.get("remaining_quantity") or r.get("quantity") or 0) for r in open_positions)`. Reads key "stop".
- timestamp: 2026-06-30 — bot/state/store.py:364-385 `get_open_positions()` runs `SELECT * FROM positions WHERE phase != 'CLOSED'` and returns `[dict(r) for r in rows]`. The positions table schema (bot/state/migrations) has columns `initial_stop`, `trail_stop` — NO column named `stop`. Therefore `r.get("stop")` is always None for every row.
- timestamp: 2026-06-30 — Caller bot/service/bot.py:692-705 (EOD report routine) passes `self._store.get_open_positions()` directly as `open_positions` to `format_daily_summary` — no remapping to a `stop` key in between. Confirmed the rows reach the formula with their raw column names.
- timestamp: 2026-06-30 — Live DB data/bot_state.db at investigation time held 6 non-CLOSED rows (all adopted orphan short-option contracts, separate issue tracked in orphan-adoption-no-ownership-guard.md). The buggy formula on those 6 rows yields approx -$4.49 (sum entry*qty with negative qty); the reported "$17197.46" predates them (generated when larger stock-orphan rows were present). Either way the figure is gross notional, not risk-to-stop.
- timestamp: 2026-06-30 — Docstring bot/service/alerter.py:190-193 states "open_positions: list of dicts — each dict has keys entry_price, stop, remaining_quantity" — this documents a `stop` key the schema never produces, which is the source of the mistake.
- timestamp: 2026-06-30 — ADJACENT (out of primary scope, note for fix consideration): `StateStore.get_closed_trades()` (bot/state/store.py:340-362) caps results at `LIMIT 20`, so on a >20-trade day both the "Trades:" count and "Realized PnL:" in the same summary undercount. Decide whether to fold into this fix or track separately.

## Design considerations for the fix

- effective_stop = `trail_stop if trail_stop is not None else initial_stop`. trail_stop overrides initial_stop once trailing begins; a BREAKEVEN row has trail_stop == entry so its risk contribution is correctly 0.
- Per-position risk floor: when a trailing stop sits ABOVE entry (locked-in profit), `(entry - stop) < 0` yields negative "risk" that would offset other positions. Consider clamping each position's contribution at max(0, ...) so Open Risk never reports below the true downside — confirm intended semantics during the fix.
- Long-only assumption: the formula assumes long positions (entry > stop, qty > 0). Shorts/zero-qty options should not be present post orphan-adoption guard (SAFE-OG-01, commit c910909); no extra handling needed here, but do not let a stray short silently distort the total.

## Eliminated

## Resolution

root_cause: "format_daily_summary (bot/service/alerter.py:206-210) computed open_risk
  using r.get('stop') which is always None — the positions table (and therefore
  StateStore.get_open_positions() rows) has initial_stop and trail_stop columns,
  not a 'stop' column. Consequently open_risk = sum(entry_price * qty) (gross
  notional) rather than sum((entry_price - effective_stop) * qty) (risk-to-stop).
  The docstring at lines 190-193 also falsely documented a 'stop' key."

fix: "bot/service/alerter.py — replaced the single r.get('stop') expression with a
  _position_risk() helper that computes effective_stop = trail_stop if trail_stop is
  not None else initial_stop, and clamps each position's contribution at
  max(0, (entry_price - effective_stop) * qty) so a locked-profit trailing stop
  (trail > entry) contributes 0 rather than a negative value. Fixed the docstring to
  correctly document initial_stop, trail_stop, and remaining_quantity.
  Two regression tests added to tests/service/test_alerter.py."

verification: "TDD: both tests were RED before fix (test 1 got $25000.00 gross notional;
  test 2 got $8000.00 unclamped). After fix both are GREEN. Full suite: 474 passed,
  1 skipped — no regressions."

files_changed:
  - bot/service/alerter.py
  - tests/service/test_alerter.py

live_verification: "2026-06-30 — Orchestrator ran format_daily_summary against the
  REAL data/bot_state.db open positions (the 6 short-option orphans) and against
  synthetic long rows, comparing each to an independent hand-calc with identical
  semantics. LIVE DB → Open Risk $0.00 (shorts clamp to 0; old bug reported notional)
  — MATCH. Synthetic longs (entry/stop 100/95×50 + 50/48-trail×100 + locked-profit
  105-trail clamped) → Open Risk $450.00 (=250+200+0) — MATCH. Human-verify checkpoint
  satisfied."
