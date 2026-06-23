---
phase: 01-foundation
reviewed: 2026-06-23T00:00:00Z
depth: standard
files_reviewed: 15
files_reviewed_list:
  - bot/_utils.py
  - bot/config/loader.py
  - bot/config/schema.py
  - bot/gateway/gateway.py
  - bot/safety/audit_log.py
  - bot/safety/et_helpers.py
  - bot/safety/kill_switch.py
  - bot/safety/logger.py
  - bot/safety/paper_guard.py
  - bot/state/migrations.py
  - bot/state/store.py
  - bot/strategy/core.py
  - bot/strategy/indicators.py
  - bot/strategy/trend_join_long.py
  - rules.json
findings:
  critical: 2
  warning: 6
  info: 5
  total: 13
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-06-23
**Depth:** standard
**Files Reviewed:** 15
**Status:** issues_found

## Summary

Reviewed the foundation phase of a safety-critical paper-trading bot at standard
depth, with focus on the triple fail-closed paper guard, atomic state-write crash
safety, RVOL no-look-ahead correctness, config-drivenness, and the kill-switch
shutdown path.

The paper guard is well-constructed and genuinely fails closed on every branch.
The RVOL look-ahead enforcement and the swing-low pivot bounds are correct. The
audit log is correctly append-only.

However, two correctness/safety defects rise to BLOCKER:

1. The initial-stop calculation reads the wrong config field — it derives the
   stop distance from `max_risk_per_trade_pct` (a position-sizing budget) instead
   of the declared `initial_stop_rule`. This silently couples stop placement to
   the risk budget and violates the config-drivenness contract (CFG-01/D-12).
2. The "crash-safe" atomic JSON writer never calls `fsync`, so its docstring
   claim of crash safety is not met under power loss / OS crash.

Warnings cover a kill-switch signal-handler deadlock risk on the shutdown path,
no range validation in the config schema, and an unguarded infinite reconciliation
loop. Details below.

## Critical Issues

### CR-01: Initial stop derived from wrong config parameter — strategy stop is not config-driven

**File:** `bot/strategy/trend_join_long.py:146-164`
**Issue:**
`compute_initial_stop` computes the stop as
`lod * (1 - max_risk_per_trade_pct / 100)`. But `rules.json` declares the stop
rule in a *separate* field, `exit.initial_stop_rule = "lod_minus_1pct"`, while
`risk.max_risk_per_trade_pct = 1.0` is the per-trade capital budget used for
position sizing. These are semantically different parameters that merely share
the value `1.0` today.

Consequences:
- The actual source-of-truth field (`exit.initial_stop_rule`) is **never read**
  by any code in this phase — it is dead config. The schema does not even expose
  it through `StrategyConfig`.
- If the operator raises `max_risk_per_trade_pct` to `2.0` to risk more capital
  per trade (its documented purpose), the stop **silently moves to 2% below LOD**,
  changing where the strategy exits — not just how many shares it buys. This is a
  strategy-behavior change driven by editing an unrelated risk knob.
- This directly violates the config-drivenness requirement the file's own
  docstring claims (CFG-01 / D-12): "swapping the StrategyConfig changes filter
  outputs without any Python code edits." Here the wrong knob changes the wrong
  output.

**Fix:** Add the stop rule to the schema/`StrategyConfig` and parse it, then
derive the stop fraction from the *stop* parameter, not the risk budget. Minimal
form — add a dedicated config field for the stop percentage:

```python
# loader.py StrategyConfig
initial_stop_pct: float   # exit.initial_stop_rule parsed to a percentage

# trend_join_long.py
def compute_initial_stop(self, lod: float) -> float:
    stop_fraction = self._cfg.initial_stop_pct / 100.0
    return float(lod * (1.0 - stop_fraction))
```

If the stop rule string must remain free-form, parse `"lod_minus_1pct"` into a
numeric percentage at load time and fail `ConfigError` on an unrecognized rule —
never silently fall back to the risk budget.

### CR-02: Atomic JSON writer omits fsync — "crash safety" claim not met

**File:** `bot/state/store.py:47-103`
**Issue:**
The docstring asserts "a crash mid-write never corrupts the prior state" and the
function is the designated crash-safety primitive (D-10 / PITFALLS #10). It
writes to a temp file, parse-validates by re-reading, then `os.replace`. But it
never calls `f.flush()` + `os.fsync(fd)` on the temp file before replacing, and
never fsyncs the containing directory after `os.replace`.

`os.replace` guarantees the *rename* is atomic, but not that the temp file's
**data blocks are durably on disk** before the rename's metadata is. On a power
loss or OS crash, the directory entry can point at a file whose data was never
flushed, yielding a zero-length or truncated `path` — i.e. exactly the corruption
the function promises to prevent. The in-process parse-validate read-back only
catches truncation produced within the same process run; it cannot catch
post-replace power-loss reordering.

**Fix:** fsync the file before replace, and fsync the directory after:

```python
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
    f.flush()
    os.fsync(f.fileno())     # durably persist temp file data
fd = None
# ... parse-validate ...
os.replace(tmp, path)
tmp = None
# durably persist the rename
dir_fd = os.open(dir_, os.O_DIRECTORY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
```

(If the docstring's crash-safety guarantee is intentionally weakened to
process-crash-only, the docstring must be corrected to not claim durability
across OS/power crashes — but for a state store this should be a real fsync.)

## Warnings

### WR-01: Kill-switch trigger can deadlock when SIGINT races a lock holder

**File:** `bot/safety/kill_switch.py:100-101, 144-156`
**Issue:**
`register_flush` and `_trigger` both acquire the non-reentrant `self._lock`.
Python delivers SIGINT to the main thread between bytecodes; `_handle_signal`
runs synchronously in that thread and calls `_trigger`, which tries to acquire
`self._lock`. If SIGINT arrives while the main thread is inside `register_flush`
(or any future code holding `self._lock`), the signal handler re-enters and
blocks forever on the same non-reentrant lock held by the interrupted frame —
a hard deadlock on the **shutdown path**, which is the one path that must never
hang for a safety-critical bot.

Additionally, the SIGINT handler performs file I/O (`append_audit`) and structlog
emission inside the signal handler, which is fragile for the same reentrancy
reason.

**Fix:** Keep the signal handler minimal — set the Event and a flag only, then
do the heavy flush/audit work from the main loop after it observes `triggered`.
If the lock must stay, use `threading.RLock()` to avoid same-thread re-entry
deadlock, and move `append_audit`/logging out of the handler context.

### WR-02: Config schema validates types but not ranges — invalid values pass

**File:** `bot/config/schema.py:109-135`; `rules.json:30-43`
**Issue:**
The schema only constrains JSON types. Values that are type-valid but
semantically dangerous pass validation, e.g. `partial_profit_fraction: 5.0`
(sells 500%), `max_risk_per_trade_pct: -1.0` or `100.0`, `max_concurrent_positions:
0`, `breakeven_trigger_R: -1`. For a config that drives real order sizing and
risk, missing bounds is a robustness gap that can produce nonsensical or unsafe
sizing downstream.

**Fix:** Add `minimum`/`maximum`/`exclusiveMinimum` to numeric fields, e.g.:

```python
"partial_profit_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
"max_risk_per_trade_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
"max_concurrent_positions": {"type": "integer", "minimum": 1},
"max_position_size_pct_of_portfolio": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
```

### WR-03: Reconciliation loop has no exception handling — one broker error kills the safety loop

**File:** `bot/gateway/gateway.py:325-337`
**Issue:**
`reconciliation_loop` is `while True: sleep; await reconcile_once()` with no
try/except. `reconcile_once` calls broker APIs that can raise (network blip,
SDK error). A single raised exception terminates the loop permanently, silently
disabling the SAFE-03 reconciliation that is supposed to keep bot state honest
against broker truth. Although flagged a "Phase 4 skeleton," the loop structure
itself ships now and the failure mode is silent.

**Fix:** Wrap the body so transient errors are logged and the loop continues:

```python
while True:
    await asyncio.sleep(interval_s)
    try:
        await self.reconcile_once()
    except Exception:
        self._logger.error("reconcile_failed", exc_info=True)  # keep looping
```

(Also consider `asyncio.CancelledError` re-raise for clean shutdown.)

### WR-04: get_acc_list / RET_OK uses bare literal 0 instead of the SDK constant

**File:** `bot/safety/paper_guard.py:89`
**Issue:**
Guard 3 checks `if ret != 0`. The SDK's success sentinel is `RET_OK`, which the
gateway imports but the guard hardcodes as `0` with only a comment to justify it.
If the SDK ever changes its success value, this paper guard would mis-classify a
*failed* broker call as success and proceed toward the order path. For the single
most safety-critical function in the codebase, the success check should bind to
the SDK's own constant.

**Fix:** Import and use `RET_OK`:

```python
from moomoo import RET_OK
...
if ret != RET_OK:
    _fail(...)
```

### WR-05: `to_et` treats naive datetimes as UTC — can silently mis-time strategy decisions

**File:** `bot/safety/et_helpers.py:49-54`
**Issue:**
A naive datetime is silently assumed UTC. Strategy timing is ET-critical
(entry windows, force-close). If a caller passes a naive ET-local or
broker-local timestamp (a very common mistake when reading bar `time_key`
strings), it will be misinterpreted as UTC and shifted by 4-5 hours, potentially
moving a bar into/out of the entry window or force-close. The docstring documents
this as intentional (PITFALLS #6), but "silently assume UTC" is itself a footgun
for ET-sensitive logic.

**Fix:** Prefer raising on naive input in the strategy-timing path, or add a
strict variant `to_et_strict(dt)` that raises `ValueError` on naive input, and
use it wherever bar timestamps are converted.

### WR-06: RVOL date comparison is type-fragile (date vs Timestamp / tz)

**File:** `bot/strategy/indicators.py:81`
**Issue:**
`volume_frame["date"] < signal_date` assumes both sides are comparable
`pd.Timestamp` values with consistent (or absent) timezone and no time component.
If the `date` column carries intraday times or a tz while `signal_date` is a
plain date (or vice-versa), the strict `<` can wrongly include the current
session (look-ahead) or drop a valid prior session (insufficient-data None).
Given the function's whole purpose is no-look-ahead correctness, the comparison
should be normalized.

**Fix:** Normalize both sides to date granularity before comparing, e.g.
`volume_frame["date"].dt.normalize() < pd.Timestamp(signal_date).normalize()`,
and assert/normalize tz-awareness explicitly.

## Info

### IN-01: Logger sets a formatter that is immediately overwritten (dead assignment)

**File:** `bot/safety/logger.py:110-112, 143-156`
**Issue:** `plain_fmt` is assigned to both handlers (lines 111-112), then both
are replaced with `ProcessorFormatter` (lines 143-156). The first `setFormatter`
calls are dead code.
**Fix:** Remove the `plain_fmt` block; keep only the `ProcessorFormatter` setup.

### IN-02: `sma` returning NaN is indistinguishable from a failed filter

**File:** `bot/strategy/indicators.py:40-43`; `bot/strategy/trend_join_long.py:61-64`
**Issue:** When fewer than `period` closes exist, `sma` returns `float('nan')`,
and `_check_d2` then does `prior_close > nan`, which is always `False`. So
"insufficient SMA200 history" is silently reported as "D2 failed" rather than
"cannot evaluate." This can mask data-availability bugs.
**Fix:** Have callers check for NaN explicitly and short-circuit with a distinct
"insufficient data" result/log instead of folding it into a normal filter False.

### IN-03: `acc_id` default of 0 is a silent value, not an explicit-required guard

**File:** `bot/gateway/gateway.py:79-83`; `bot/safety/paper_guard.py:101`
**Issue:** D-03 requires the operator to set `FUTU_ACC_ID` explicitly, but an
unset/invalid `FUTU_ACC_ID` silently becomes `0`. The guard then fails closed
(account 0 not found), which is safe, but the failure message blames a "missing
account" rather than "FUTU_ACC_ID was never set." Minor diagnosability gap.
**Fix:** When `FUTU_ACC_ID` is absent or unparolseable, surface that explicitly
(e.g. distinguish unset from 0) so the operator sees the real cause.

### IN-04: Migration runner relies on `executescript` implicit commit ordering

**File:** `bot/state/migrations.py:120-124`
**Issue:** `executescript` issues an implicit COMMIT before running, and
`PRAGMA user_version = i` is set after. A crash between the two leaves schema
committed but `user_version` unchanged. This is currently harmless because the
migration uses `CREATE TABLE IF NOT EXISTS` (re-run is idempotent), but the
invariant is implicit — a future non-idempotent migration would break it.
**Fix:** Document the "all migrations must be idempotent" invariant, or wrap
schema change + `user_version` bump so they commit atomically together.

### IN-05: `safe_get` multi-key fallback order is undocumented at call sites

**File:** `bot/_utils.py:16-22`; `bot/safety/paper_guard.py:107,111`
**Issue:** `safe_get(row, "acc_id", default=None)` works, but `safe_get`'s
multi-key fallback semantics (first non-None wins) are subtle; the paper-guard
relies on the exact broker column name `acc_id`/`trd_env`. If the SDK column name
differs across versions, the guard reads `default` and (for trd_env) fails
closed — safe, but worth an explicit comment/assertion on the expected column
names.
**Fix:** Add a brief comment at the call site naming the expected SDK columns,
or assert their presence once.

---

_Reviewed: 2026-06-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
