---
phase: 260817-ask-fix-screen-options-1000-row-server-cap-s
plan: 01
subsystem: options
tags: [moomoo-sdk, gateway, options-screen, rate-limit, tasty_credit_spreads]

# Dependency graph
requires:
  - phase: 260817-1ie
    provides: OptionsBot service (entry scan/manage/eod jobs) that calls MoomooGateway.screen_options
provides:
  - MoomooGateway.screen_options issues one OptionScreenRequest per underlying instead of one shared request, so the server's 1000-row-per-request cap can no longer starve low-volume ETFs (SPY/QQQ/DIA/FXI) out of strike coverage
  - Blocking inter-call throttle (_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS) inside the same executor job, keeping get_option_screen under the live SDK's "10 times per 30 seconds" cap
  - Per-underlying row counts in the option_screen_done log for future truncation visibility
affects: [phase-9-options-backtester, options-live-uat]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-entity request closure (_build_req(stock_id)) built fresh each loop iteration because the moomoo SDK's request builder APPENDS filters rather than replacing them"
    - "Blocking time.sleep() inside an executor-thread closure to throttle a synchronous SDK call without blocking the asyncio event loop"

key-files:
  created: []
  modified:
    - bot/gateway/gateway.py
    - tests/gateway/test_gateway_options.py

key-decisions:
  - "screen_options now issues ~15 OptionScreenRequest calls per right (one per underlying) instead of 1 shared request — root-caused the 1000-row server cap rather than working around it"
  - "Rate-limit fix scoped to a sleep before every get_option_screen call (not just between underlyings) after live evidence showed deep chains (SPY/QQQ) page 2-3 times back-to-back with zero delay, alone consuming most of the 10-per-30s budget"
  - "TestScreenOptions gets one autouse pytest fixture patching the sleep constant to 0.0, keeping all 9 pre-existing tests textually unmodified while the suite stays instant"

requirements-completed: [OPT-GW-SCREEN-01]

# Metrics
duration: 15min
completed: 2026-08-17
---

# Quick Task 260817-ask: Fix screen_options 1000-row server cap Summary

**`screen_options` now screens one underlying per `OptionScreenRequest` (not one shared multi-underlying request), eliminating the server's 1000-row-per-request cap that was silently starving SPY/QQQ/DIA/FXI of strike coverage; a live-probe-driven inter-call throttle keeps the ~15x call volume under the SDK's 10-calls-per-30s rate limit.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-08-17T07:51Z (approx, first test run)
- **Completed:** 2026-08-17T08:04:45-07:00 (Task 2 commit)
- **Tasks:** 2/2 completed
- **Files modified:** 2 (`bot/gateway/gateway.py`, `tests/gateway/test_gateway_options.py`)

## Accomplishments

- Root-caused and fixed the 1000-row truncation: `screen_options` builds a fresh `OptionScreenRequest` per underlying via a `_build_req(stock_id)` closure, wraps the existing bounded paging loop in `for stock_id in stock_ids`, and merges rows from every underlying in `stock_ids` order. `page_from` resets to 0 per underlying; `_OPTION_SCREEN_MAX_PAGES`, the `last_page` break, and the empty-page defense all still apply, now per underlying.
- Added 4 new TDD tests to `TestScreenOptions` (confirmed RED against the old implementation, then GREEN): per-underlying request isolation, per-underlying paging cap, `page_from` reset per underlying, and SDK-failure-on-second-underlying raising `GatewayError`. All 9 pre-existing `TestScreenOptions` tests pass unmodified.
- Live RTH probe (read-only, `scripts/uat_options_probe.py`) surfaced a real SDK rate-limit error (`"Option Screening request failed due to high frequency. Maximum 10 times per 30 seconds."`) after the per-underlying fix increased call volume from 1 to ~15-38 per right. Root-caused with a debug instrumentation script (see below) that showed deep-chain underlyings (SPY, QQQ) paging 2-3 times back-to-back with zero delay — a sleep only *between* underlyings (as the plan's contingency literally specified) was insufficient, so the sleep was placed before *every* `get_option_screen` call instead.
- Re-ran the live probe after the throttle fix: zero rate-limit errors, 2156 put rows + 1217 call rows across the 15 underlyings, with per-underlying counts confirming no single underlying is capped near 1000 (max was SPY at 402 puts).
- Added per-underlying row counts (`rows_by_stock_id`) to the existing `option_screen_done` structlog event so future truncation is observable from production logs without a live probe.
- Fixed the two stale comments identified in the plan: the `screen_options` docstring ("one request covers every underlying") and the `_OPTION_SCREEN_MAX_PAGES` cap comment (implied one shared request across 15 ETFs).

## Task Commits

1. **Task 1: Screen per underlying inside the same executor job** - `3baa169` (fix) — `_build_req` closure, per-underlying paging loop, 4 new tests (RED confirmed before edit, GREEN after), stale docstring/comment fixes, `rows_by_stock_id` log field.
2. **Task 2: Full suite + live read-only probe, recorded in SUMMARY** - `7628ac6` (fix) — inter-call throttle (`_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS`) added after the live probe surfaced a rate-limit error; autouse test fixture keeps `TestScreenOptions` instant.

**Plan metadata:** committed separately by the orchestrator (STATE.md/SUMMARY.md not committed by the executor per this quick task's constraints).

_Note: Task 1 was TDD (test → implementation, single combined commit per this project's convention of one commit per task rather than separate RED/GREEN commits); Task 2 required a code fix once live evidence contradicted the plan's literal "sleep between underlyings" contingency._

## Files Created/Modified

- `bot/gateway/gateway.py` — `screen_options` rewritten: `_build_req(stock_id)` closure (fresh `OptionScreenRequest` per underlying — the SDK builder appends filters so reuse would accumulate STOCK_LIST entries), `_blocking()` loops `for stock_id in stock_ids`, `req.page_from = 0` and the bounded paging loop run per underlying, `rows_by_stock_id` counts merged into the `option_screen_done` log. Added `_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS = 3.2` and a `time.sleep()` before every `get_option_screen` call except the first in the executor job (blocking sleep is safe there — executor thread, not the event loop). Corrected the `screen_options` docstring and the `_OPTION_SCREEN_MAX_PAGES` comment.
- `tests/gateway/test_gateway_options.py` — 4 new tests in `TestScreenOptions` (`test_each_underlying_gets_its_own_request`, `test_paging_cap_is_per_underlying`, `test_page_from_resets_per_underlying`, `test_sdk_failure_on_second_underlying_raises`), all keying mocks off the SDK's internal `req._filter_groups[0]["underlying"][0]["value_list"]` and `req.page_from`. Added an autouse `_no_real_sleep` fixture on `TestScreenOptions` patching `_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS` to `0.0` so the throttle doesn't slow down the 9 pre-existing tests (some page up to `_OPTION_SCREEN_MAX_PAGES` times).

## Decisions Made

- **One request per underlying, not a shared multi-underlying request** — matches the plan exactly; this is the root-cause fix for the 1000-row server cap (a shared request silently starves low-volume ETFs like SPY, which got only 19 of 1000 rows in the pre-fix live probe).
- **Throttle placement: before every SDK call, not just between underlyings** — deviation from the plan's literal contingency wording. The plan said "add a small blocking `time.sleep(...)` between underlyings." Live evidence (a scratch debug script instrumenting `get_option_screen` call timing) showed that placing the sleep only between underlyings still tripped the rate limit, because deep-chain underlyings (SPY, QQQ) page 2-3 times with zero delay between pages, alone consuming most of the 10-calls-per-30s budget before the next underlying's sleep even happens. Moving the sleep to "before every call except the first" (pages and underlyings alike) is the smallest change that actually satisfies T-ask-02's mitigation goal ("no rate-limit error") — this is a Rule 1 (auto-fix bug) correction: the literal plan instruction did not achieve the stated live-verification requirement, so the root-cause fix (shared function, all callers) was applied instead of the narrower one.
- **Test speed preserved via one autouse fixture, not per-test patches** — rather than wrapping every multi-page/multi-underlying test body in a `with patch(...)` block (which would also require editing the 9 pre-existing tests, violating "existing tests MUST keep passing byte-for-byte unmodified"), a single class-scoped autouse fixture on `TestScreenOptions` patches the sleep constant to 0.0 for the whole class. Full test file runs in ~0.2s instead of the ~14-60s it would take with real sleeps.
- **Constant renamed from `_OPTION_SCREEN_INTER_UNDERLYING_SLEEP_SECONDS` to `_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS`** mid-task-2, to accurately name what it now throttles (every call, not just underlying transitions) before committing.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Sleep-between-underlyings contingency was insufficient to prevent the live rate-limit error**
- **Found during:** Task 2 (live probe run after implementing the plan's literal "sleep between underlyings" contingency)
- **Issue:** The plan's Task 2 contingency said to add a `time.sleep(...)` "between underlyings" if the live probe showed a rate-limit error. Implemented exactly that first, re-ran the live probe, and it still failed at the 11th `get_option_screen` call with `"Option Screening request failed due to high frequency. Maximum 10 times per 30 seconds."` Root cause: paging *within* a single underlying's chain (SPY, QQQ needed 2-3 pages each) makes back-to-back calls with zero delay, which alone can burn 8-9 of the 10-call budget before the next underlying's sleep is even reached.
- **Fix:** Moved the sleep to apply before every `get_option_screen` call except the very first one in the executor job — covering both inter-page and inter-underlying gaps. Renamed the constant to `_OPTION_SCREEN_INTER_CALL_SLEEP_SECONDS` to match. Verified via a scratch debug script (outside the repo, in the session scratchpad — not committed) that instrumented real SDK call timestamps before and after the fix.
- **Files modified:** `bot/gateway/gateway.py`, `tests/gateway/test_gateway_options.py` (autouse fixture to keep tests fast)
- **Verification:** Re-ran the live probe with the corrected throttle — zero rate-limit errors, full puts+calls screen completed (2156 + 1217 rows) across all 15 underlyings.
- **Committed in:** `7628ac6` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — literal plan instruction didn't satisfy its own stated live-verification requirement; fixed at the root, one throttle point covering all callers of the SDK call).
**Impact on plan:** No scope creep — same file, same function, same executor-thread constraint the plan specified ("Blocking sleep is correct there — it is an executor thread, not the event loop"). Only the throttle's placement within `_blocking()` changed from the plan's literal wording.

## Live Probe Evidence (recorded per plan requirement)

Run: `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/uat_options_probe.py` — read-only, no `--live-1lot`, RTH open 2026-08-17.

**Section 2 — option screen row counts (verbatim):**
```
rows: puts=2156 calls=1217
```
Per-underlying counts from the `option_screen_done` structlog line:
- Puts: `{202805(SPY): 402, 203290(QQQ): 366, 205180(IWM): 183, 203017(DIA): 182, 205078(GLD): 271, 203074(SLV): 108, 205671(TLT): 42, 201909(XLE): 55, 202939(XLF): 52, 202955(XLK): 77, 202370(EEM): 112, 202423(EFA): 85, 201440(FXI): 34, 201965(GDX): 88, 203067(USO): 99}`
- Calls: `{202805(SPY): 169, 203290(QQQ): 148, 205180(IWM): 88, 203017(DIA): 155, 205078(GLD): 78, 203074(SLV): 111, 205671(TLT): 38, 201909(XLE): 23, 202939(XLF): 38, 202955(XLK): 36, 202370(EEM): 49, 202423(EFA): 46, 201440(FXI): 39, 201965(GDX): 93, 203067(USO): 106}`

Before this fix (2026-08-17 pre-fix live probe, per plan context): puts+calls were both pinned at exactly 1000 rows total (server cap on the single shared request), with SPY getting only 19 of those 1000 put rows and QQQ 87, while IWM got 273 and SLV 218. The cap is now gone: every underlying gets its own request, so SPY correctly returns 402 puts / 169 calls and none of the 15 underlyings are anywhere near a 1000-row ceiling.

**Section 4 — strike geometry verdict for SPY/QQQ/DIA/FXI:**
- `US.SPY`: expiry 2026-09-18 (dte 32) — built successfully: `credit 2.17 width 8 credit/width 0.27 qty 1 maxloss $584` — legs `BUY P740 @3.25 | BUY C808 @1.42 | SELL P748 @4.16 | SELL C800 @2.67`
- `US.QQQ`: expiry 2026-09-18 (dte 32) — built successfully: `credit 1.83 width 7 credit/width 0.26 qty 1 maxloss $517` — legs `BUY P691 @4.59 | BUY C775 @3.02 | SELL P698 @5.56 | SELL C770 @3.88`
- `US.DIA`: expiry 2026-09-18 (dte 32) — strike geometry **was built** (would be: `BUY P515 @2.23 | BUY C557 @1.09 | SELL P520 @3.00 | SELL C552 @1.81 credit 1.49`), gated out only for illiquidity (`oi` as low as 18-26 on some legs) — not a "no strike geometry" failure.
- `US.FXI`: expiry 2026-09-18 (dte 32) — strike geometry **was built** (would be: `BUY P31 @0.05 | BUY C39 @0.06 | SELL P33 @0.19 | SELL C37 @0.23 credit 0.32`), gated out only for illiquidity — not a "no strike geometry" failure.

All 15 underlyings now produce a strike-geometry attempt in section 4 (either a full spread or an explicit "None because ..." reason such as illiquidity or credit/width ratio). None report the pre-fix "no strike geometry" symptom that motivated this task — SPY/QQQ/DIA/FXI all had strikes to pick from once the per-underlying request stopped starving them.

## Issues Encountered

- The plan's literal Task 2 contingency wording ("sleep between underlyings") did not fully solve the live rate-limit error on the first attempt — see Deviations above. Root-caused with an out-of-repo scratch debug script (`debug_screen.py`, session scratchpad, not committed) that instrumented real SDK call timestamps to see the actual page-burst pattern.
- macOS's default shell has no `timeout`/`gtimeout` binary; the live probe was run without a wrapper timeout (it completed in ~2 minutes, well within a reasonable bound).

## Next Phase Readiness

- `screen_options` is now correct for the full 15-ETF universe at both put and call screens; the options bot's entry-scan job (`bot/options/service.py`, unmodified per plan constraint) will see complete strike coverage for every configured underlying going forward.
- Phase 9 (options backtester, if planned) can rely on `screen_options` returning per-underlying-complete data rather than a globally-capped 1000 rows.
- Operator `--live-1lot --confirm` UAT (placing one paper spread) remains a separate, out-of-scope step for this quick task — not touched here per the "do not modify `scripts/uat_options_probe.py`" constraint.

---

*Quick task: 260817-ask-fix-screen-options-1000-row-server-cap-s*
*Completed: 2026-08-17*

## Self-Check: PASSED

- FOUND: commit `3baa169` (Task 1)
- FOUND: commit `7628ac6` (Task 2)
- FOUND: `bot/gateway/gateway.py`
- FOUND: `tests/gateway/test_gateway_options.py`
- FOUND: this SUMMARY.md
- `python3 -m pytest tests/gateway/test_gateway_options.py -q` → 36 passed in 0.18s
- `python3 -m pytest -q` (full suite) → 965 passed, 1 skipped in 51.09s

## Orchestrator Addendum (post-executor review)

3. **Throttle hardening** - `f44837e` (fix) — the executor's sleep skipped the first call of each `screen_options` invocation (`calls_made` guard), so the last put-screen call and the first call-screen call fired back-to-back; at 3.2s spacing that is exactly 10 calls per 30s window plus one — zero margin. Now sleeps unconditionally before every `get_option_screen` call at 3.5s (≤9 calls/30s across invocations), `calls_made` removed. Cost: +3.5s per invocation (2 per entry scan).

**Live re-run (read-only probe, RTH 2026-08-17 ~11:09-11:20 ET, after f44837e):** zero rate-limit errors.
- `rows: puts=2163 calls=1212`
- `rows_by_stock_id` P: SPY 403, QQQ 370, IWM 186, DIA 181, GLD 270, SLV 108, TLT 42, XLE 56, XLF 52, XLK 77, EEM 112, EFA 85, FXI 34, GDX 88, USO 99; C: SPY 169, QQQ 148, DIA 155, FXI 39 (…).
- Section 4: SPY builds a full IC (`credit 2.23 width 8 credit/width 0.28 qty 1 maxloss $577`); QQQ, DIA, FXI all build strike geometry and are gated only by the real ILLIQUID leg check (QQQ P699 oi 292; DIA C557/C552 oi 18/26; FXI P31/P33 wide) — no "no strike geometry" verdicts remain. IWM/SLV still build full ICs.
- Scan wall-clock: puts ≈ 70s, calls ≈ 55s per entry scan (sequential, ~36 throttled calls) — acceptable for a once/twice-daily scheduled job.

Full suite after hardening: 965 passed, 1 skipped.
