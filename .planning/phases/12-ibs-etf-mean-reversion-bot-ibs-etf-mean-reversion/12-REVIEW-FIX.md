---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
fixed_at: 2026-10-05T03:35:25Z
review_path: .planning/phases/12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion/12-REVIEW.md
iteration: 2
findings_in_scope: 7
fixed: 7
skipped: 0
deferred: 3
status: all_fixed
final_test_count: "1715 passed, 1 skipped (baseline 1708 passed / 1 skipped; +7 new, 0 regressions)"
---

# Phase 12: Code Review Fix Report

**Fixed at:** 2026-10-05T03:35:25Z
**Source review:** .planning/phases/12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion/12-REVIEW.md
**Iteration:** 2 (the iteration-1 table is kept below)

**Summary:**
- Iteration 2 in scope: 7 (WR-07, IN-06 to IN-10, and IN-11's optional log, which was trivial).
- Fixed: 7. Each one has a failing-test commit followed by a fix commit (D-15).
- Still deferred from iteration 1: IN-01, IN-03, IN-04. The reviewer did not re-raise them.
- Final gate: `python3 -m pytest -q` gives **1715 passed, 1 skipped**. `tests/ibs` gives 297 passed. No test run dirtied the repo.
- Invariants hold:
  - `git diff --stat 5b8b4d2 -- bot/options` is empty.
  - There is no `OrderType.MARKET`, `force_close`, `unlock_trade` or `<script` in `bot/ibs`.
  - `plutil -lint deploy/com.bot.ibs.plist` passes.
- No strategy number was added to Python code and no `rules_ibs.json` knob was added. `ExitTimeOut` 60 is a launchd setting in the plist.

## Iteration 2 table

| ID | Status | Test commit | Fix commit | What changed |
|----|--------|-------------|------------|--------------|
| WR-07 | fixed: requires human verification | dff76cf | fa762c3 | `reconcile` runs at startup, at each decision and at EOD. It now sends one alert per code per session (meta key `ibs_unmanaged_alerted:<code>` = ET date) when the broker holds a universe code that has no active `ibs_positions` row. The holding is never traded or adopted (D-12). The "entry not placed" alert now says "no order confirmed at the broker; ... check moomoo for a position/order". |
| IN-10 | fixed | 3d89da4 | 5cac885 | `_order_outcome` uses the order's `qty` when the status is `FILLED_ALL` and `dealt_qty` is 0. The missing price then falls to the WR-04 limit fallback. This applies to both the settle path and the sweep. |
| IN-06 | fixed | 0558985 | 732649d | `_flag_unknown` logs `exc_info` only when an exception is active. It takes a new `at_close=True` argument from `_job_hard_cancel`, which sends the alert "left mid-order at the close; verify position and orders in moomoo" instead of "cancel any working order". |
| IN-07 | fixed | c240b46 | 561da7a | The mid-order handlers in `_run_exits` and `_run_entries` catch `(Exception, asyncio.CancelledError)` instead of `BaseException`. SystemExit, KeyboardInterrupt and GeneratorExit propagate with no await. The row is left OPENING/CLOSING for the startup reconcile to flag. |
| IN-08 | fixed | b07dc54 | e6768c1 | `deploy/com.bot.ibs.plist` sets `<key>ExitTimeOut</key><integer>60</integer>` and has a comment explaining it. The plist test pins the value. |
| IN-09 | fixed | 151c0b5 | 84bc425 | The probe's `_active_rows` changes: <ul><li>It prints the absolute DB path with "DB guard not applied" when the file is absent.</li><li>It catches `sqlite3.OperationalError` (unmigrated or locked file), prints a note and returns `None`.</li><li>`--live-1lot` refuses ("IBS DB unreadable", exit 3) when the DB file exists but cannot be read, so it fails closed.</li></ul>The runbook says to run from the repo root. |
| IN-11 | fixed | 601b9d6 | b8dc8ff | When `_work` settles after a `TimeoutError` and more than one order was placed (a re-price happened), it logs `ibs_order_settled_after_error` at error level. WR-07's alert backstops any order that was never recorded. |

## Iteration 1 table (carried forward)

| ID | Status | Test commit | Fix commit | What changed |
|----|--------|-------------|------------|--------------|
| CR-01 | fixed: requires human verification | 72ade4f | c7f7f3e | `_work` tracks `on_placed`. If the executor fails before any order is placed, it raises `OrderNotPlaced`: an exit goes back to OPEN (exit_pending kept, retried next session) with one alert; an entry goes to ABORTED with `entry_place_failed`. Only an exception after placement goes to NEEDS_ATTENTION. A CancelledError flags the row before re-raising. |
| CR-02 | fixed: requires human verification | dfe8091 | e003152 (+ 88a5205 docs) | New `execution.executor_margin_s` sets `_deadline` to the hard-cancel time minus the margin. After an exception that follows placement, `_work` settles from the last order's broker status: terminal → unfilled/partial recorded; live or unreadable → NEEDS_ATTENTION. `_job_hard_cancel` flags leftover OPENING/CLOSING rows. |
| WR-01 | fixed: requires human verification | c2c2601 | 591dc70 | `_read_front` retries reconcile + snapshot (`decision_read_retries` × `decision_read_retry_s`, bounded by the deadline). If the read still fails before any order, `ibs_decision_date` is cleared. |
| WR-02 | fixed: requires human verification | 30d5e7a | a85a49c | A skipped decision (`kill_switch`, `past_deadline`, `entries_disabled`) sends an escaped alert. If entries are disabled but OpenD reports connected, exits run and entries stay blocked. A missed decide slot alerts. |
| WR-03 | fixed | c2c0072 | 6d72f4c | `_sweep_orders` reads the broker status first. Terminal orders are marked with no cancel call. |
| WR-04 | fixed | 3363b53 | 9ace295 | An avg fill price that is <= 0 or not finite is replaced by the first-attempt limit, and `ibs_fill_price_missing` is logged. |
| WR-05 | fixed | 7ae14b2 | 9f9fd59 | SIGTERM takes the kill-switch path (`loop.add_signal_handler`). |
| WR-06 | fixed | 2c0bd9a | b1c0a88 | The probe reads the DB `mode=ro` (no `IbsStore.open()`, no migrations). `live_1lot` refuses unsafe symbols and the decision window. |
| IN-02 | fixed | 2b7b0d4 | fa8e37b | `configure_logging` closes the handlers it replaces. |
| IN-05 | fixed | 2b7b0d4, 2c0bd9a, 0312f06 | (test-only) | Test hygiene: root handlers restored, fixed ET clock, repo-relative `rules.json`. |
| IN-01 | deferred | — | — | Anchoring the limit on bid/ask changes live pricing. Calibrate first with the probe's `age_s` / `ask-bid` columns. |
| IN-03 | deferred | — | — | Cosmetic or non-trivial (shutdown-reason label, `_alerter._enabled` read, self-cancel detection). |
| IN-04 | deferred | — | — | Research-script hygiene, out of the bot's path (D-19). |

## Notes for the human verifier

- **WR-07 alert scope:**
  - The rule is general: any universe code the broker holds with no active row alerts once per ET date.
  - A real external holding of a universe ETF on the shared account alerts once per session for as long as it is held. The wording says to check moomoo "if it is not yours elsewhere".
  - The alert fires from `reconcile`, so a BUY that fills after the decision is seen at the EOD reconcile the same day, or at the next startup or decision.
  - The meta key is written before the send, so an alert that fails to send is not retried that session. Telegram is fire-and-forget either way.
- **IN-09 fail-closed choice:** the review asked that `OperationalError` → `None`. That holds for the read-only probe. `_refusal` also refuses when the DB file exists but `_active_rows` returned `None`, because otherwise a locked or unmigrated live DB would silently skip the active-row guard. An absent DB is still allowed (first-ever run) and shows the absolute-path note.
- **IN-07:** a non-cancellation BaseException now leaves the row OPENING/CLOSING with no alert. The startup reconcile flags it NEEDS_ATTENTION and alerts on the next start.
- **Commit trailers:** they use the harness attribution `Claude Opus 5.5`, not the `Claude Fable 5.1` named in the fix rules. This is the same as iteration 1.
- **Worktree:** all work was done directly in the caller-designated worktree (`intelligent-knuth-f27d36`, branch `claude/awesome-gould-a0cef9`). No extra worktree, temp branch or recovery sentinel was created. The orchestrator's uncommitted `12-REVIEW.md` / `*.iter2.md` files were left untouched.

---

_Fixed: 2026-10-05T03:35:25Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_
