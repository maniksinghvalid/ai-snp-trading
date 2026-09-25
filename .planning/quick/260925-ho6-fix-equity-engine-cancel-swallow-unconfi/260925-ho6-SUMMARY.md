---
id: 260925-ho6
status: complete
date: 2026-09-25
commits:
  - 589ab6e test(260925-ho6): add failing CR-04 cancel-unconfirmed regression tests
  - 97417b4 fix(260925-ho6): escalate unconfirmed cancels in ExecutionEngine (CR-04 parity)
  - e7596ad fix(260925-ho6): book known fills and hold exits on CancelUnconfirmedError
  - 3625cf1 fix(260925-ho6): block new entries for a code under the CR-04 exit hold
tests: 1147 passed, 1 skipped
---

# Quick Task 260925-ho6 — Summary

CR-04 parity fix for the equity bot's `ExecutionEngine`: a raised
`cancel_order` at any of its four cancel sites was swallowed by a bare
`except Exception: pass`, then treated as "order dead" — placing a second
live BUY/SELL for the same intent/position, or booking a still-growing fill
qty as final. Same defect class as the options-bot fix (260925-goi,
`git show 2cb1d0f`). Paper account 1727266 is shared with the options bot, so
a transient OpenD `modify_order` rate-limit/timeout is realistic.

## Task 1 — RED: 12 regression tests, run against the unmodified engine ✅

12 new `test_cr04_*` tests across `tests/execution/test_engine.py` (8),
`tests/service/test_bot.py` (2), `tests/position/test_manager.py` (1), and
`tests/test_main_wiring.py` (1).

Red-run command:
```
pytest -q tests/execution/test_engine.py tests/service/test_bot.py \
  tests/position/test_manager.py tests/test_main_wiring.py -k "cr04 and not preserve"
```

Result against unmodified `bot/`: **10 failed, 2 passed, 116 deselected.**

- `test_cr04_entry_ttl_cancel_unconfirmed_raises_no_second_buy` — `Failed: DID NOT RAISE` (behavioral)
- `test_cr04_entry_partial_cancel_unconfirmed_raises_with_fill` — `Failed: DID NOT RAISE` (behavioral)
- `test_cr04_exit_ttl_cancel_unconfirmed_raises_and_holds` — `Failed: DID NOT RAISE` (behavioral)
- `test_cr04_exit_partial_cancel_unconfirmed_raises_with_filled_qty` — `Failed: DID NOT RAISE` (behavioral)
- `test_cr04_entry_ttl_fill_during_failed_cancel_returns_fill` — `assert None is not None` (behavioral)
- `test_cr04_alerter_notified_once_on_unconfirmed_cancel` — `ImportError: CancelUnconfirmedError` (missing contract, expected)
- `test_cr04_process_bar_unconfirmed_with_fill_books_position` — `ImportError` (missing contract, expected)
- `test_cr04_process_bar_unconfirmed_without_fill_leaves_intent_pending` — `ImportError` (missing contract, expected)
- `test_cr04_place_exit_order_credits_filled_qty_on_cancel_unconfirmed` — `ImportError` (missing contract, expected)
- `test_cr04_main_wires_alerter_into_engine` — `AttributeError: 'ExecutionEngine' object has no attribute '_alerter'` (missing contract, expected)

The 5 engine "core-property" tests (place-a-second-order / drop-a-fill cases)
failed on **behavior**, not import errors, as required — achieved by deferring
the `CancelUnconfirmedError` import to *after* the `pytest.raises(Exception)`
block closes, so an unfixed engine that never raises fails with `DID NOT
RAISE` rather than masking that as a caught `ImportError`.

`test_cr04_preserve_exit_partial_completes_during_failed_cancel` and
`test_cr04_preserve_entry_ttl_confirmed_cancelled_proceeds` **passed** against
the unmodified engine (pins for the "cancel raised but re-read confirms the
order is actually dead" case, which must behave identically before and
after). All 117 pre-existing tests in the four files + 1 skip also passed
unchanged (`pytest ... -k "not cr04 or preserve"` → **117 passed, 1 skipped,
10 deselected**).

Committed test-only: `589ab6e`.

## Task 2 — GREEN (engine): escalate at all 4 sites, exit hold, alerter ✅

All edits confined to `bot/execution/engine.py`:

- `_TERMINAL_ORDER_STATUSES` module constant (hoisted from `consume_intent`'s
  inline set, reused by `_cancel_confirmed`) and `_cancel_confirmed(row,
  placed_qty)` helper (D2).
- `CancelUnconfirmedError(RuntimeError)` with `code`, `order_id`,
  `filled_qty` (lower bound), `avg_price`, `fill` (entry-only) (D1). The
  raise always sits outside every `except` block (including the cleanup
  retry's), so it never implicitly chains from the live gateway exception.
- `ExecutionEngine.__init__(..., alerter=None)` + `self._exit_hold: dict`
  (in-memory, ponytail-commented — see Known ceiling below).
- Shared helpers: `_reread_order`, `_emit_entry_fill` (site-1 block
  extracted unchanged), `_escalate_unconfirmed_cancel` (D3 steps 1–6:
  audit → log → best-effort cleanup retry → exit hold on SELL → best-effort
  Telegram alert with `str(err)` kept OUT of the HTML text → raise).
- **Site 1** (entry remainder cancel): re-read on cancel failure, rebuild the
  fill from the re-read (fallback to pre-cancel values), escalate if
  unconfirmed.
- **Site 2** (entry TTL): re-read on cancel failure; a fill that lands during
  the cancel now returns instead of being silently dropped and re-placed;
  escalate if unconfirmed, never reaching `place_order`/`_resolve_intent_expired`.
- **manage_exit exit hold** (D5): first statement — a held code raises
  `CancelUnconfirmedError(filled_qty=0)` with no order placed and no alert.
- **Site 3** (exit remainder cancel): the existing post-cancel re-read is now
  the same shared `_reread_order` call (no extra `get_order_status`);
  escalate if unconfirmed, before the `remaining <= 0` break.
- **Site 4** (exit TTL): re-read on cancel failure, credits any dealt qty
  as a lower bound, escalates if unconfirmed.

Verified: `pytest tests/execution/test_engine.py` → 23 passed, 1 skipped
(includes all 8 engine cr04 tests). `grep -c _TERMINAL_ORDER_STATUSES` → 3.
No `cancel_order` call is followed by a bare `except Exception: pass`.

Committed: `97417b4` (engine.py only).

## Task 3 — GREEN (callers): book/hold on CancelUnconfirmedError, wire alerter ✅

- `bot/service/bot.py` `_process_bar`: wraps `consume_intent` in try/except
  `CancelUnconfirmedError`; `fill = exc.fill` on catch. The `if fill is not
  None` branch (register_position/on_fill/arm_stop_protection) is unchanged
  byte-for-byte; the `else` became `elif not unconfirmed` so an unconfirmed
  no-fill intent stays `PENDING` instead of `ABANDONED` (SAFE-OG-01 orphan
  adoption + D-10 re-entry gate). `note_intent_resolved()` still runs
  unconditionally.
- `bot/position/manager.py` `_place_exit_order`: `except
  CancelUnconfirmedError as exc: return int(exc.filled_qty),
  float(exc.avg_price)` placed before the generic `except Exception`.
- `bot/main.py`: alerter construction moved before the engine; `engine =
  ExecutionEngine(gateway=gateway, store=store, cfg=cfg, alerter=alerter)`.

Verified:
- `pytest -k cr04` → **13 passed** (12 new CR-04 tests + 1 unrelated
  pre-existing test matched by the `cr04` substring,
  `test_multiday_replay_proves_cr01_cr04_cr05_cr06`).
- `python3 -c "import bot.main, bot.service.bot, bot.position.manager"` → clean,
  no import cycle.
- Full suite: **`pytest -q` → 1146 passed, 1 skipped** (1134 baseline + 12
  new, 0 failed).

Committed: `e7596ad` (bot.py, manager.py, main.py).

## Deviations from Plan

Executor: none — plan executed as written, D1–D6 as locked in CONTEXT.md.

Orchestrator review (commit `3625cf1`): the exit hold is keyed by code and
outlives the position. Once the stray SELL fills, reconcile marks the old
position closed and the D-10 re-entry gate reopens; a same-session re-entry
would then open a position whose every stop-out and force_close
`manage_exit` refuses. `consume_intent` now returns None (audited
`entry_blocked_cancel_unconfirmed`) for a held code, before the EXEC-04
guard. New test `test_cr04_exit_hold_blocks_new_entry_for_held_code` was run
red first (a FillEvent for ORD-2 came back instead of None), then green.
The red run for Tasks 1–5 was also re-verified independently in a throwaway
worktree at `589ab6e` (pre-fix engine): 10 failed / 2 passed, with the
engine core tests failing on behaviour (DID NOT RAISE / dropped fill).

Final full suite: **1147 passed, 1 skipped** (1134 baseline + 13 new).

## Follow-ups (out of scope — not fixed, per CONTEXT.md)

1. **Success-path race at sites 2/4**: a fill landing between the last poll
   and a *successful* TTL cancel is never re-read. Entry: an untracked
   partial fill plus a re-placed full-size BUY. Exit: the next SELL is sized
   without it, risking a short. Pre-existing; out of scope because the task
   requires the successful-cancel path to stay byte-for-byte unchanged.
2. **`PositionManager._sync_broker_stop`** swallows a failed stop-cancel then
   places a new stop unconditionally — two live stops → a double SELL is
   possible. Currently inactive: `rules.json` has `use_broker_stop_orders:
   false` in production.

## Known ceiling

The exit hold (`self._exit_hold`) is in-memory only and is never
auto-released within a session — a held code gets no further exits or
entries until the bot restarts, even if the operator manually resolves the order at the broker.
On repeated quote-tick or bar-driven retries against a held code, each
blocked attempt re-audits `exit_blocked_cancel_unconfirmed` without sending
a second Telegram alert (the alert fires once, at the original escalation).
Upgrade path (documented inline as a `ponytail:` comment in `engine.py`):
persist the hold and auto-release it once the held order is confirmed
terminal AND reconcile has re-synced the position's qty from broker truth.
