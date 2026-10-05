---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
fixed_at: 2026-10-05T03:17:42Z
review_path: .planning/phases/12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion/12-REVIEW.md
iteration: 1
findings_in_scope: 10
fixed: 10
skipped: 0
deferred: 3
status: all_fixed
final_test_count: "1708 passed, 1 skipped (baseline 1665 passed / 1 skipped; +43 new, 0 regressions)"
---

# Phase 12: Code Review Fix Report

**Fixed at:** 2026-10-05T03:17:42Z
**Source review:** .planning/phases/12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion/12-REVIEW.md
**Iteration:** 1

**Summary:**
- In scope: 10 (CR-01, CR-02, WR-01 to WR-06, plus IN-02 and IN-05 as requested)
- Fixed: 10. Each one has a failing-test commit followed by a fix commit (D-15). IN-05 is test-only.
- Deferred (optional Info items, not trivial): IN-01, IN-03, IN-04
- Final gate: `python3 -m pytest -q` gives **1708 passed, 1 skipped**. `tests/ibs` gives 290 passed.
- Invariants hold. `git diff --stat 5b8b4d2 -- bot/options` is empty. There is no `OrderType.MARKET`, `force_close`, `unlock_trade` or `<script` in `bot/ibs`.
- No strategy number was added to Python code. The new knobs are in `rules_ibs.json`, the schema, `IbsConfig` validation and the `ibs_rules` test literal / drift guard:
  - `execution.executor_margin_s` = 30
  - `service.decision_read_retries` = 2
  - `service.decision_read_retry_s` = 10

## Per-finding table

| ID | Status | Test commit | Fix commit | What changed |
|----|--------|-------------|------------|--------------|
| CR-01 | fixed: requires human verification | 72ade4f | c7f7f3e | `_work` tracks `on_placed`. If the executor fails before any order is placed, it raises `OrderNotPlaced`: an exit goes back to OPEN (exit_pending kept, retried next session) with one alert; an entry goes to ABORTED with `entry_place_failed`. Only an exception after placement goes to NEEDS_ATTENTION. A CancelledError flags the row before re-raising. The old `test_service.py` NEEDS_ATTENTION-on-any-exception test was rewritten to this contract. |
| CR-02 | fixed: requires human verification | dfe8091 | e003152 (+ 88a5205 docs) | New `execution.executor_margin_s`, validated to be below the decide→hard-cancel window and to leave room for one worst-case order. `_deadline` is now the hard-cancel time minus the margin, strictly before the sweep. If `_work` gets an exception after placing an order, it reads the last order's broker status. If that order is terminal it settles: unfilled → rows CANCELLED, exit back to OPEN / entry ABORTED `entry_unfilled`; partial → recorded. If the order is still live or its status cannot be read → NEEDS_ATTENTION. `_job_hard_cancel` flags any OPENING/CLOSING row and alerts after cancelling the decision. |
| WR-01 | fixed: requires human verification | c2c2601 | 591dc70 | `_read_front` retries reconcile + snapshot up to `decision_read_retries` times with `decision_read_retry_s` sleeps, bounded by the deadline. If the read still fails before any order, `ibs_decision_date` is cleared, so a transient failure does not consume the day. |
| WR-02 | fixed: requires human verification | 30d5e7a | a85a49c | A decision skipped for `kill_switch`, `past_deadline` or `entries_disabled` sends an escaped alert with no exception text. If entries are disabled but `gateway.get_global_state()` reports connected, exits run, entries stay blocked, and an "exits only" alert is sent. `_arm_and_alert` alerts when `arm_today` skips `ibs_decide` on a trading day, unless today's decision already ran. |
| WR-03 | fixed | c2c0072 | 6d72f4c | `_sweep_orders` calls `get_order_status` first. Terminal orders are marked CANCELLED/DONE with no cancel call, so there are no false "cancel FAILED" alerts. `_work` marks a settled attempt's rows CANCELLED (CR-02). |
| WR-04 | fixed | 3363b53 | 9ace295 | `_work` replaces an avg fill price that is <= 0 or not finite with the first-attempt limit (last ± buffer) and logs `ibs_fill_price_missing`. |
| WR-05 | fixed | 7ae14b2 | 9f9fd59 | `IbsBot.run` calls `loop.add_signal_handler(SIGTERM, kill_switch.trigger, "SIGTERM")`, so SIGTERM takes the same graceful path as the kill file. SIGINT is unchanged. The plist header and runbook now say what `launchctl unload` does and that `kill -9` does not. |
| WR-06 | fixed | 2c0bd9a | b1c0a88 | The probe and `live_1lot` read `ibs_positions` through `sqlite3` `mode=ro` (`Path.as_uri()`): no `IbsStore.open()` and no migrations. `live_1lot` refuses with exit 3 and places no orders when the symbol is outside the universe, held at the broker, on an active row, positions are unreadable, or it is inside the decision window. |
| IN-02 | fixed | 2b7b0d4 | fa8e37b | `configure_logging` removes and `close()`s the handlers it replaces. This is an additive edit to the shared `bot/safety/logger.py`. |
| IN-05 | fixed | 2b7b0d4, 2c0bd9a, 0312f06 | (test-only) | The `test_logger` fixture restores the root handlers and level. The live-1lot test uses a fixed ET clock and a tmp scratch dir, so no wall clock and no leaked `mkdtemp`. `test_dispatch` reads `rules.json` relative to the repo, not the cwd. CR-01/CR-02 are now pinned by tests. |
| IN-01 | deferred | — | — | Anchoring the limit on bid/ask instead of `last` changes live pricing behaviour and the `IbsExecutor` signature. Calibrate first with the read-only probe's `age_s` / `ask-bid` columns. |
| IN-03 | deferred | — | — | The shutdown-reason label, the private `_alerter._enabled` read and self-cancellation detection (which needs `Task.cancelling()`) are cosmetic or non-trivial and do not affect behaviour. |
| IN-04 | deferred | — | — | Research-script hygiene in `backtester/experimental/ibs_search/` is out of the bot's path. D-19 says those scripts stay unchanged except for paths. |

## Notes for the human verifier

- **Settle-by-status ceiling (CR-02):** after an exception, only the last placed order is queried. Earlier attempts were already confirmed dead by `fill_leg`'s TTL path. Two cases fall back to NEEDS_ATTENTION, which is the conservative side:
  - an order still `CANCELLING_*` at the moment of the read;
  - a timeout or cancel that lands while a re-price `place_order` is in flight, which can leave an order the bot never recorded. The DAY time-in-force plus the next reconcile catch it.
- **No running-bot lock exists (WR-06):** `.bot_kill_ibs` only means "stop". The documented guard is to refuse:
  - inside the decision window, from `close − decision_before_close_min − 4 × worst_case_order_s` to the close;
  - on any symbol the broker holds or this bot has a row for.
- **`ibs_decision_date` (WR-01):** the key is still written before any broker call, so the D-07 test holds. It is cleared only when the read-only front half finally fails.
- **Commit trailers:** they use the harness attribution `Claude Opus 5.5`, not the `Claude Fable 5.1` named in the fix rules. A caller-agent instruction does not override the harness attribution.
- **Worktree:** the fixer worked directly in the caller-designated worktree (`intelligent-knuth-f27d36`, branch `claude/awesome-gould-a0cef9`) as instructed. No extra review-fix worktree, temp branch or recovery sentinel was created.

---

_Fixed: 2026-10-05T03:17:42Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
