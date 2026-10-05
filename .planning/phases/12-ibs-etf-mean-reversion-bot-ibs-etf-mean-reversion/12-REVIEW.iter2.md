---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
reviewed: 2026-10-04T19:59:00-07:00
depth: standard
files_reviewed: 42
files_reviewed_list:
  - bot/ibs/__init__.py
  - bot/ibs/schema.py
  - bot/ibs/config.py
  - bot/ibs/strategy.py
  - bot/ibs/store.py
  - bot/ibs/execution.py
  - bot/ibs/service.py
  - bot/main.py
  - bot/safety/logger.py
  - bot/scanner/calendar.py
  - bot/state/migrations.py
  - rules_ibs.json
  - scripts/uat_ibs_probe.py
  - deploy/com.bot.ibs.plist
  - deploy/IBS-RUNBOOK.md
  - CLAUDE.md
  - docs/research/2026-10-04-ibs-etf-strategy-search.md
  - backtester/experimental/ibs_search/strategy_search.py
  - backtester/experimental/ibs_search/strategy_search_r2.py
  - backtester/experimental/ibs_search/ibs_robust.py
  - backtester/experimental/ibs_search/r5_robust.py
  - backtester/experimental/ibs_search/ibs_sizing.py
  - backtester/experimental/ibs_search/screen_study.py
  - tests/conftest.py
  - tests/ibs/conftest.py
  - tests/ibs/test_schema_config.py
  - tests/ibs/test_dispatch.py
  - tests/ibs/test_strategy.py
  - tests/ibs/test_parity.py
  - tests/ibs/test_store.py
  - tests/ibs/test_execution.py
  - tests/ibs/test_service.py
  - tests/ibs/test_service_entries.py
  - tests/ibs/test_lifecycle.py
  - tests/ibs/test_hygiene.py
  - tests/ibs/test_operator_tooling.py
  - tests/ibs/test_research_provenance.py
  - tests/safety/test_audit_log.py
  - tests/safety/test_logger.py
  - tests/scanner/test_calendar.py
  - tests/state/test_migrations.py
findings:
  critical: 2
  warning: 6
  info: 5
  total: 13
status: issues_found
---

# Phase 12: Code Review Report

**Depth:** standard — **Status:** issues_found

## Summary
Strategy core (`strategy.py`), schema/config validation, store SQL, additive shared-file edits and test isolation are
sound. The defects are concentrated in `bot/ibs/service.py` error paths: the broad "any exception means exposure unknown"
handling leaves positions in states the bot never recovers from, and the hard-cancel/deadline design races itself.

Confirmed clean: no non-LIMIT order path (`place_order` always uses `OrderType.NORMAL`; `IbsExecutor` rejects any side other than BUY/SELL); no code outside the bot's own `ibs_positions` rows can be traded; SQL fully parameterised (f-strings interpolate only fixed column names and marker counts); Telegram/HTML output escaped; research scripts repo-relative (no `/Users/acdc`); additive shared-file edits do not change the equity or options bots' behaviour. CR-01 and WR-01 were reproduced with a scratch script driving `IbsBot._job_decide` with mocks.

## Critical Issues

### CR-01: Any exception from `_work` permanently parks the position in NEEDS_ATTENTION, even when no order was ever placed (exits never retried)
**File:** `bot/ibs/service.py:417-426` (exit) and `:502-513` (entry); root in `bot/ibs/execution.py:74-77`
**Issue:** `_run_exits` and `_run_entries` treat every exception from `_work` as "exposure unknown". That includes
`gateway.place_order` raising (`GatewayError` for rate limit, insufficient buying power, price band, OpenD blip), where no
order id exists and nothing can be exposed.
- Result: an exit row flips `CLOSING -> NEEDS_ATTENTION`. NEEDS_ATTENTION is excluded from `_run_exits` (only `OPEN` is selected) and from steady-state reconcile, so the position is never sold again.
- That contradicts "exit_pending retried every session". It needs manual SQL, and the alert text tells the operator to "cancel any working order" that does not exist.
- This is plausible in production. `rules_ibs.json` sizes 10 x 10% = 100% of equity, and the executor escalates the limit up to +$0.20 above the sized price, on an account shared with the options bot's margin, so buying-power rejects are realistic.
- Reproduced: with `place_order` raising `RuntimeError("rate limited")` on a SELL, the row ends as `('NEEDS_ATTENTION', exit_pending=1)` with no orders recorded.
**Fix:** Track whether any order was placed for this attempt and only escalate to NEEDS_ATTENTION when one was; otherwise restore the pre-attempt state: exit with no order placed → back to `OPEN` (exit_pending stays 1 → retried next session) + alert "no order placed; retried next session"; entry with no order placed → `ABORTED` with `close_reason="entry_place_failed"`. Expose "orders placed" from `_work` (return it or raise a wrapper exception) so the callers can branch. Add tests for `place_order` raising on both sides.

### CR-02: Hard-cancel (or shutdown-free decision cancel) mid-order leaves the row stuck in CLOSING/OPENING with no alert and no recovery
**File:** `bot/ibs/service.py:560-581` (`_job_hard_cancel`), `:416-426` / `:496-513` (callers), `:300-302` (`_deadline`)
**Issue:** `CancelledError` is a `BaseException`. `_run_exits` and `_run_entries` catch `Exception` only (entries re-raise `CancelledError`), so a cancelled decision never resets the status it set earlier.
- `_job_hard_cancel` fires at exactly `_deadline` (close - hard_cancel_before_close_min), the same instant the executor's `wait_for` timeout expires. Whichever wins, the row ends badly.
- The cancel path leaves a `CLOSING` row. Reproduced: `('CLOSING', exit_pending=1)`, with the order marked `CANCELLED` and only a log line.
- A `CLOSING` row is never retried: `_run_exits` selects only `OPEN`, and steady-state `reconcile()` checks only `OPEN`. Only a restart flags it. A partial exit fill that happened before the cancel is also never recorded.
- The `OPENING` case is the same: a possibly-filled BUY is untracked and unflagged until restart.
- The timeout path gives NEEDS_ATTENTION, which has the same never-retried problem as CR-01 for what is really a clean "unfilled at the deadline" outcome.
**Fix:** After cancelling the in-flight decision in `_job_hard_cancel`, reconcile any `OPENING`/`CLOSING` rows (set NEEDS_ATTENTION and alert, or re-query the broker). Give the executor an earlier deadline than the sweep (new `rules_ibs.json` key, e.g. `execution.executor_margin_s`, validated in config; no literal in Python). Treat a TimeoutError on an exit (order cleanly cancelled by `fill_leg`) like "unfilled": set back to `OPEN` and retry next session.

## Warnings

### WR-01: A single transient failure consumes the whole day's decision (no retry inside the 9-minute window)
**File:** `bot/ibs/service.py:324-337`, `:341-347`
**Issue:** `ibs_decision_date` is written before any broker call (D-07). If `reconcile()` or `get_market_snapshot` fails (one `position_list_query` blip, `ret != 0`), `_decide` raises and the day is done: no exits (including time-stops), no entries, no retry. Exits slip a full session.
**Fix:** Retry the read-only front half (reconcile and snapshot) a bounded number of times, bounded by the deadline, before giving up. The retry count and sleep must come from `rules_ibs.json`. Only after the first order is attempted is the "do not re-run" guarantee needed, so write the meta key at that point, or clear it on a pre-order failure.

### WR-02: Skipped decisions are silent (log only), including the OpenD-disconnect case that blocks exits
**File:** `bot/ibs/service.py:304-322`, `:606-631`
**Issue:** `entries_disabled`, `kill_switch`, `past_deadline` and a mid-window restart (`ibs_job_slot_passed`) skip the entire decision, exits included, with an `info` log and no Telegram alert.
- The watchdog sets `_entries_enabled=False` on disconnect, but reconnect detection can lag 60 s plus backoff up to 300 s, so any disconnect in about the 15:40-15:50 window drops the day silently.
- A mid-window restart (including a KeepAlive crash-restart) does the same: `arm_today` skips the passed decide slot and the operator is never told.
**Fix:** Send an alert for `entries_disabled`/`past_deadline` and when `arm_today` skips `ibs_decide` on a trading day. Optionally let exits run when only `_entries_enabled` is false but the gateway is connected.

### WR-03: Order rows left WORKING after a timeout/exception cause false "cancel FAILED" alerts
**File:** `bot/ibs/service.py:359-378`, `:531-558`
**Issue:** On any `_work` exception, `close_working_orders` is skipped. `fill_leg` guarantees it already cancelled the order (CR-03 in the options code), so the DB row is stale.
- `_sweep_orders` then cancels it again. `GatewayError` on an already-cancelled order is caught and marked `CANCEL_FAILED` with the alert "cancel it in moomoo before the close".
- That is noise that trains the operator to ignore the real alert.
**Fix:** In `_sweep_orders`, call `get_order_status(oid)` first and mark terminal orders `CANCELLED`/`DONE` without a cancel call. Alternatively, in `_work` on `TimeoutError`/`CancelledError`, mark that position's WORKING rows `CANCELLED`, since `fill_leg` has already cancelled them.

### WR-04: Fill average price of 0.0 is not guarded, so entry_price and P&L are corrupted
**File:** `bot/ibs/service.py:434-436`, `:519-520`; source `bot/options/execution.py::_poll`
**Issue:** `_poll` returns `0.0` when `dealt_avg_price` is missing or empty, and the SIMULATE/broker lag makes this plausible on the TTL-partial path.
- `mark_opened(..., float(avg))` stores `entry_price=0.0`.
- Later, `record_exit_fill` computes P&L as `(exit - 0) * qty`, a huge fake gain, and the EOD unrealised figure is wrong.
- An exit with `avg=0.0` records a 100% loss trade.
**Fix:** Reject `avg <= 0` and fall back to the order's limit price, or skip the P&L figures and log, e.g. `avg = float(result[1]) or limit`.

### WR-05: SIGTERM / `launchctl unload` bypass the shutdown path
**File:** `deploy/com.bot.ibs.plist:9-11` (unload instruction), `bot/ibs/service.py:230`, `bot/safety/kill_switch.py:install`
**Issue:** `KillSwitch.install()` handles only SIGINT. The plist header advertises `launchctl unload`, which sends SIGTERM. The process dies immediately: no sweep of WORKING orders, no "stopped" alert, and rows left OPENING/CLOSING.
- The runbook (section 5) claims that stopping cancels working orders, but that holds only for the sentinel file.
**Fix:** Register a SIGTERM handler in `IbsBot.run`, e.g. `loop.add_signal_handler(signal.SIGTERM, lambda: self._kill_switch.trigger("sigterm"))`, or remove the unload line from the plist and runbook and state that the sentinel is the only supported stop.

### WR-06: `--live-1lot` probe can corrupt live bot state, and the "read-only" probe opens the production DB through `IbsStore.open()`
**File:** `scripts/uat_ibs_probe.py:99-107` (DB open), `:164-217` (`live_1lot`)
**Issue:** `live_1lot` BUYs then SELLs one share of `--symbol` on the shared account without checking:
- that the symbol is not already held at the broker;
- that it has no active row in `cfg.state_db`;
- that it is in the configured universe;
- that the IBS bot is not running.

If the symbol is held or active, the extra buy/sell shifts the broker quantity by 1, so the next reconcile flips the live position to NEEDS_ATTENTION. The default-mode probe's "never create a DB" guard still calls `IbsStore(state_db).open()`, which runs migrations (a write) and takes a lock on the file the running bot uses.
**Fix:** In `live_1lot`, call `get_positions()` first and refuse if `symbol` has a broker holding or an active `ibs_positions` row (open the DB with `sqlite3` `mode=ro`), and require `symbol in cfg.universe`. For the read-only probe, use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`.

## Info

### IN-01: Anchor limits on stale `last` (up to 900 s old)
**File:** `bot/ibs/execution.py:73-77`, `rules_ibs.json:26`
`max_snapshot_age_s=900` lets the BUY/SELL limit start from a price up to 15 minutes old. In a fast close the SELL (last - 0.05 up to -0.20) may never become marketable, so the exit is unfilled and retried next session. Consider passing the snapshot `bid_price`/`ask_price` into `fill_leg` (it already accepts bid/ask) instead of `last, last`, or tighten the age.

### IN-02: `configure_logging(force=True)` leaks the replaced handlers
**File:** `bot/safety/logger.py:~94` (`root_logger.handlers.clear()`)
The old `RotatingFileHandler` (`logs/bot.log`, opened by `bot.main`'s earlier call) is dropped without `close()`, leaving an open file descriptor and an empty `logs/bot.log` created by the IBS process. Harmless but sloppy. Fix: `for h in root_logger.handlers[:]: root_logger.removeHandler(h); h.close()`.

### IN-03: Shutdown and run-loop small defects
**File:** `bot/ibs/service.py:707`, `:744`, `:769-772`
- The audit event always records `reason: "kill_switch"`, even when `run()` exits through a startup or error path.
- The "IBS bot stopped" alert and a no-op sweep also run after a failed `_readiness_gate`.
- `self._alerter._enabled` reads a private attribute.
- Swallowing `CancelledError` in `_job_hard_cancel` (`:572`) and `_shutdown` (`:718`) also swallows a cancellation of those coroutines themselves.

### IN-04: Research scripts: hygiene and reproducibility
**File:** `backtester/experimental/ibs_search/*.py`
- `ibs_robust.py:2-6` has unused `importlib.util`, `spec`, `np`, and a no-op `sys.argv=["x"]`.
- `ibs_sizing.py`, `r5_robust.py`, `ibs_robust.py` define `ROOT` (or `HERE`) but only partly use it.
- Every script `exec()`s `strategy_search.py` split on a magic comment string (fragile; use a shared module).
- `glob(...)[-1]` raises a bare `IndexError` if `data/sp500_*.csv` (gitignored) is absent.
- `screen_study.py:17` does `os.makedirs(OUT)` at import time.
- `yfinance` data is not pinned, so results drift; the committed `assets/*.txt` are the only reproducible record.
- Paths are repo-relative, which is correct (no `/Users/acdc`).

### IN-05: Test fragility
**File:** `tests/ibs/test_dispatch.py:69`, `tests/ibs/test_operator_tooling.py:93-106`, `tests/safety/test_logger.py` (new tests)
- `Path("rules.json")` is cwd-relative; use the `parents[2]` pattern used elsewhere.
- `test_live_1lot_buys_then_sells_one_share` uses wall-clock `datetime.now(ET)` (flaky across an ET midnight) and leaks `tempfile.mkdtemp` dirs.
- The new `test_logger` tests leave the root logger pointed at a deleted tmp dir for later tests; no test pins CR-01/CR-02 behaviour, and `test_service.py:330` pins the NEEDS_ATTENTION-on-any-exception behaviour that CR-01 argues against.

---
_Reviewed: 2026-10-04_
_Reviewer: Claude (gsd-code-reviewer, sonnet, standard depth; report transcribed by the orchestrator because the reviewer harness cannot write .md files)_
