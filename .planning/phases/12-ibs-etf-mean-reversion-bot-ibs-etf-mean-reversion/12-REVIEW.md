---
phase: 12-ibs-etf-mean-reversion-bot-ibs-etf-mean-reversion
reviewed: 2026-10-04T20:30:00-07:00
depth: standard
iteration: 2
diff_base: a4b35c5
files_reviewed: 19
files_reviewed_list:
  - bot/ibs/service.py
  - bot/ibs/execution.py
  - bot/ibs/store.py
  - bot/ibs/config.py
  - bot/ibs/schema.py
  - rules_ibs.json
  - scripts/uat_ibs_probe.py
  - bot/safety/logger.py
  - deploy/com.bot.ibs.plist
  - deploy/IBS-RUNBOOK.md
  - tests/ibs/conftest.py
  - tests/ibs/test_service.py
  - tests/ibs/test_service_entries.py
  - tests/ibs/test_lifecycle.py
  - tests/ibs/test_execution.py
  - tests/ibs/test_schema_config.py
  - tests/ibs/test_operator_tooling.py
  - tests/safety/test_logger.py
  - bot/options/execution.py
findings:
  critical: 0
  warning: 1
  info: 6
  total: 7
status: issues_found
---

# Phase 12: Code Review Report (iteration 2, re-review of fixes a4b35c5..HEAD)

**Depth:** standard. **Status:** issues_found, with no blockers. The only warning is a narrow, silent position-tracking gap that the CR-01 fix opens for entries. Iteration-1 report preserved as `12-REVIEW.iter2.md`.

## Summary

CR-01, CR-02 and WR-01 to WR-06 (iteration 1) are genuinely closed. Every exit and entry error path in `_run_exits`, `_run_entries`, `_work`, `_order_outcome`, `_job_hard_cancel` and `_sweep_orders` was traced, and the real `IbsExecutor` + `LegExecutor` were run against a mock gateway with a real-time clock (the existing tests use a frozen clock, so this exercises the deadline path they never reach):
- Deadline lands mid-order → `wait_for` times out, `fill_leg` cancels its order, `_work` settles from broker status → row `OPEN`, `exit_pending=1`, order row `CANCELLED`, one "exit unfilled" alert.
- `place_order` raises on the re-price attempt after a TTL cancel → same settle path → `OPEN`, `exit_pending=1`.

Verification: `python3 -m pytest tests/ibs tests/safety/test_logger.py` 305 passed; full suite 1708 passed, 1 skipped; no test run dirtied the repo; hygiene grep (`OrderType\.MARKET|force_close|unlock_trade|<script` in bot/ibs) empty; `git diff a4b35c5..HEAD -- bot/options` empty.

### Iteration-1 findings verified CLOSED

| ID | Verdict | Evidence |
|----|---------|----------|
| CR-01 | CLOSED | `service.py:430-435`: `OrderNotPlaced` raised only when `placed` is empty; `on_placed` appends the id before the DB insert so a failing insert still counts as placed. Exits → `OPEN` (`exit_pending` kept) + alert (`:527-534`); entries → `ABORTED` `entry_place_failed` (`:617-626`). Exception chain preserved; broker text never reaches the alert. Reproduced with the real executor. |
| CR-02 | CLOSED | `_deadline` = close − `hard_cancel_before_close_min` − `executor_margin_s` (`:310-314`) → 15:58:30 vs sweep 15:59:00. `wait_for` waits for `fill_leg`'s shielded cancel before raising `TimeoutError`. `_work` settles from broker status: terminal → unfilled/partial recorded; live or unreadable → `NEEDS_ATTENTION`. `CancelledError` flags the row then re-raises (`:535-539`, `:627-631`). `_job_hard_cancel` flags leftover `OPENING`/`CLOSING` rows (`:704-706`), which also backstops a non-`_work` failure such as `record_exit_fill` raising. |
| WR-01 | CLOSED | `_read_front` (`:367-386`) bounded by retry count and deadline; read-only so retries cannot re-place orders; already-flagged rows are not alerted twice; config validates retries × retry_s against the order window. |
| WR-02 | CLOSED | Alerts for `kill_switch`, `past_deadline`, `entries_disabled` with no exception text; exits-only runs only when `get_global_state()` reports connected (never raises); `_decide` (`:401`) and `_run_entries` (`:594`) both still block entries (test confirms no BUY); `_arm_and_alert` silent when today's decision already ran. |
| WR-03 | CLOSED | `_sweep_orders` reads status first; terminal orders marked `DONE`/`CANCELLED` with no cancel call. |
| WR-04 | CLOSED | `:444-452` handles `<=0` and NaN; the settle path goes through the same guard. |
| WR-05 | CLOSED | `add_signal_handler(SIGTERM, kill_switch.trigger, "SIGTERM")`; kill switch idempotent; clean exit 0 + `KeepAlive SuccessfulExit=false` → no restart. |
| WR-06 | CLOSED (see IN-09) | Read-only `mode=ro` DB access, no `IbsStore`, no migrations. `live_1lot` refuses (exit 3, no order) on symbol outside universe / held at broker / active row / unreadable positions / inside the decision window (start = close − decision_min − 4 × worst_case_order_s = 15:43:20, covering both legs). |
| IN-02, IN-05 | CLOSED | Handlers removed and closed; tests repo-relative and pin CR-01/CR-02. |
| IN-01, IN-03, IN-04 | Still open | Explicitly deferred by the fixer; not re-raised. |

New-knob validation: schema `executor_margin_s` `_pos()`, `decision_read_retries` `_int(0)`, `decision_read_retry_s` `_pos()`; fail-closed cross-checks (`config.py:112-125`): margin < decide→hard-cancel window, worst-case order + margin < window, retries × retry_s + worst-case order + margin < window; mirrored in the `conftest` `ibs_rules` literal and `test_schema_config` `_LEAVES`/bad-value cases; shipped-file drift guard passes; no strategy literal added to Python.

## Warnings

### WR-07: A BUY that actually reached the broker can be recorded as ABORTED and then ignored forever (CR-01 fix opens a silent gap for entries)
**File:** `bot/ibs/service.py:430-435` (wrapper), `:617-626` (entry ABORTED), `:582-584` (external holdings only logged)
**Issue:** `OrderNotPlaced` assumes "`place_order` raised, so nothing is exposed". The moomoo SDK reports a request timeout as a non-RET_OK return, which `_check_ret` turns into `GatewayError` — OpenD may already have accepted the order. For an entry, the row becomes `ABORTED` `entry_place_failed`; if that BUY then fills, the holding is a universe code with no active row: `_run_entries` classifies it as `external`, logs it, excludes it from entries and never alerts; it is never sold either (D-12). The exit side is safe (next `reconcile()` sees DB qty vs broker 0 → `NEEDS_ATTENTION`). The documented ceiling (orphan order from a re-price in flight at the deadline) has the same shape.
**Fix:** In `_run_entries`, alert when an `external` universe holding has an `ABORTED` row with `close_reason` in (`entry_place_failed`, `entry_unfilled`) from the last couple of sessions; dedupe with a meta key per code/day. More generally, alert once per code whenever a universe code is held at the broker with no active row. Reword the "entry not placed" alert to "no order confirmed at the broker; check moomoo for a position/order".

## Info

### IN-06: `_flag_unknown` logs `exc_info=True` outside an exception when called from the hard-cancel sweep, and its alert wording is wrong there
**File:** `bot/ibs/service.py:481`, callers `:704-706`
From `_job_hard_cancel` there is no active exception (log carries `NoneType: None`); the alert says "cancel any working order" but the following sweep cancels it. **Fix:** pass `exc_info` only when an exception is active; alert variant "left mid-order at the close; verify position and orders in moomoo".

### IN-07: `except BaseException` handlers `await` inside non-cancellation paths
**File:** `bot/ibs/service.py:535-539`, `:627-631`
Intent is `CancelledError`; `BaseException` also catches `GeneratorExit` (awaiting there raises "coroutine ignored GeneratorExit"), `KeyboardInterrupt`, `SystemExit`. **Fix:** `except (Exception, asyncio.CancelledError) as exc:` and re-raise when not an `Exception`.

### IN-08: launchd's default 20 s ExitTimeOut can cut the SIGTERM graceful path short
**File:** `deploy/com.bot.ibs.plist`
Shutdown = shielded cancel + per-order status/cancel calls (`get_order_status` can add ~9 s of rate-limit backoff each); launchd SIGKILLs after 20 s by default. **Fix:** `<key>ExitTimeOut</key><integer>60</integer>`.

### IN-09: Probe DB guard silently passes on a wrong cwd or an unmigrated file
**File:** `scripts/uat_ibs_probe.py:81-96`, `:117`
`state_db` is relative; from another cwd `_active_rows` returns `None`, which `_refusal` treats as "no active rows". A file without the `ibs_positions` table raises `sqlite3.OperationalError` and aborts even read-only mode. **Fix:** print "no IBS DB at <abs path>, DB guard not applied"; catch `OperationalError` → `None`; runbook: run from the repo root.

### IN-10: Settle-by-status treats a terminal order with `dealt_qty == 0` as unfilled even when the status is FILLED_ALL
**File:** `bot/ibs/service.py:468-473`
Broker lag with `dealt_qty` not yet populated on a `FILLED_ALL` order → entry `ABORTED` with shares held (see WR-07) or exit `OPEN` (self-corrects via reconcile; the entry does not). **Fix:** for `FILLED_ALL`, take filled qty from the order's `qty` when `dealt_qty` is 0.

### IN-11: Documented ceiling confirmed, not a regression
**File:** `bot/ibs/execution.py:76-79`, `bot/options/execution.py:117-118`
If the deadline's `wait_for` cancellation lands during a re-price `place_order` thread call, the thread still places the order; `_work` settles on the previous (dead) order; the new order is untracked until DAY TIF expiry / next reconcile / the WR-07 alert. Window is small (an order only starts with ≥ `worst_case_order_s` remaining; margin 30 s). **Fix:** none required beyond WR-07; optionally log `ibs_order_settled_after_error` when the exception was a `TimeoutError` and `placed` had more than one id.

---
_Reviewed: 2026-10-04 (iteration 2)_
_Reviewer: Claude (gsd-code-reviewer, sonnet, standard depth; transcribed by the orchestrator because the reviewer harness cannot write .md files)_
