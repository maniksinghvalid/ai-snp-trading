---
phase: quick-260824-avx
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - bot/scanner/scanner.py
  - bot/gateway/gateway.py
  - tests/scanner/test_scanner.py
  - tests/gateway/test_gateway.py
autonomous: true
requirements: [SCAN-07, SIG-01]

must_haves:
  truths:
    - "An active code (open position or pending intent) that FAILS the intraday re-filter stays in the returned watchlist and keeps its K_5M feed"
    - "gateway.unsubscribe never raises when OpenD replies 'has not been subscribed'; a different error message still raises GatewayError"
    - "run_intraday_rescan returns its watchlist on every tick, so the service's premarket-high seeding for rescan-added codes always runs"
    - "Carried active codes are NOT re-persisted to daily_scan with fabricated gap_pct/rank"
  artifacts:
    - path: "bot/scanner/scanner.py"
      provides: "Unconditional active-code protection in run_intraday_rescan step 5"
      contains: "carried_active"
    - path: "bot/gateway/gateway.py"
      provides: "Idempotent unsubscribe — benign 'not been subscribed' handling"
      contains: "unsubscribe_not_subscribed"
  key_links:
    - from: "bot/scanner/scanner.py run_intraday_rescan"
      to: "_unsubscribe_evicted_codes"
      via: "evicted = active_codes - result, now always empty for managed codes"
      pattern: "_unsubscribe_evicted_codes"
    - from: "bot/gateway/gateway.py unsubscribe"
      to: "_check_ret"
      via: "message-substring guard before the raise"
      pattern: "not been subscribed"
---

<objective>
Fix the intraday-rescan crash that silently disabled entries for rescan-added symbols.

Two independent defects, both fixed here:
- **A (intent):** `run_intraday_rescan` only re-includes active codes that still pass the re-filter, so a managed symbol whose gap collapses (live: `US.DLR`) is dropped from the watchlist and then unsubscribed.
- **B (idempotency):** `MoomooGateway.unsubscribe` raises `GatewayError` on OpenD's benign `ret=-1 "... has not been subscribed. Cannot unsubscribe."`, aborting the whole rescan before `return result` — so `rescan_watchlist` stays `[]` in the service worker and the premarket-high seeding at `bot/service/bot.py:823-824` is skipped, leaving rescan-added codes (live: `US.STLD`) stuck at Gate 1 `signal_skipped_no_premarket_high` forever.

Purpose: a managed symbol must never lose its 5m feed, and feed cleanup must never be able to abort a scan.
Output: two source fixes, two rewritten stale tests, three new regression tests.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md
@bot/scanner/scanner.py
@bot/gateway/gateway.py

Verified facts (do not re-derive):
- Candidate dict shape (`_evaluate_symbol`, scanner.py:358-365): `code, gap_pct, prior_day_high, prior_close, sma200, rvol_baseline`. `rank` is assigned by the caller.
- `store.persist_watchlist` (bot/state/store.py:807-863) requires `code`, `gap_pct`, `rank` and `.get()`s the rest. A code absent from `passing` has NO candidate dict.
- Nothing in `bot/`, `backtester/` or `scripts/` reads back the `daily_scan` table (`get_scan_codes` / `get_rvol_baseline` have zero callers) — it is an audit-only write target.
- `get_external_codes` (gateway.py:495-525) returns broker holdings MINUS bot-owned codes (open positions + pending intents), i.e. exactly the complement of `active_codes`. SAFE-OG-01 exclusion and D-04 protection can never contend for the same code.
- `RET_OK` is already module-level in gateway.py (used by `_check_ret`, line 236).
- Gateway test helper `_make_gateway_with_mocks()` (tests/gateway/test_gateway.py:50) injects a MagicMock `_quote_ctx`; no connect() needed.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Unconditionally protect managed codes in run_intraday_rescan (Fix A)</name>
  <files>bot/scanner/scanner.py, tests/scanner/test_scanner.py</files>
  <behavior>
    - Active code that FAILS the re-filter (absent from `passing`) is present in the returned watchlist.
    - That code is NOT passed to `gateway.unsubscribe` (with only managed codes active, `unsubscribe` is not called at all).
    - That code gets NO `daily_scan` row written by this pass (no fabricated gap_pct/rank).
    - A `active_code_carried` warning is logged naming the carried code.
    - Active code that still passes keeps its existing D-04 front-loaded behavior and its persisted row.
  </behavior>
  <action>
Rewrite step 5 of `run_intraday_rescan` (bot/scanner/scanner.py, currently lines ~751-785) so active codes are protected unconditionally.

Build `passing_codes = {c["code"] for c in passing}` right after the sort. Keep the existing loop that front-loads active codes that ARE in `passing` (they carry a real candidate dict and stay in `protected`, the list handed to `persist_watchlist`). Add `carried_active = sorted(c for c in active_codes if c not in passing_codes)` — the managed codes that failed the re-filter — and seed `filled_codes` with them so the filler loop cannot double-add.

Carried codes have no candidate dict, so they must NOT enter `protected` and must NOT be persisted: fabricating `gap_pct`/`rank` would clobber the code's real `daily_scan` row from the premarket pass (the ON CONFLICT UPDATE overwrites gap_pct/rank/rvol_baseline). Instead compose the return value as `result = [c["code"] for c in protected] + carried_active`.

Cap math: filler slots become `_WATCHLIST_CAP - len(protected) - len(carried_active)`. DELETE the `if len(protected) > _WATCHLIST_CAP: protected = protected[:_WATCHLIST_CAP]` truncation — managed codes hold real feeds and must never be truncated away; the only way `result` can exceed 20 now is if the managed set itself exceeds 20, which `max_concurrent_positions` makes unreachable.

Replace the WR-02 `active_code_evicted` warning loop (lines ~787-800): with unconditional protection, `active_codes - set(result)` is now always empty, so that loop is dead. Log the genuinely interesting event instead — one `_logger.warning("active_code_carried", code=..., scan_date=..., scan_pass=...)` per entry in `carried_active` (a managed symbol retained despite failing the re-filter). In the `rescan_complete` info log, replace `evicted_active=len(evicted_active)` with `carried_active=len(carried_active)`.

Comments to update so the code stops describing the old semantics: the step 5 block comment, the `run_intraday_rescan` docstring bullet "Active candidates that still pass filters are guaranteed in the top-20" (drop the "that still pass filters" condition), and the `active_codes` param line. State in the step 5 comment that SAFE-OG-01 exclusion can never resurrect an externally-held code here because `active_codes` are bot-DB-owned and `get_external_codes` returns exactly the complement.

Leave `_unsubscribe_evicted_codes` and its step 9 call in place as a safety net, but add a `# ponytail:` note on its docstring recording the known ceiling: since Finding 2.2 changed `active_codes` to mean "managed" rather than "subscribed", this path no longer releases quota slots for genuinely dropped watchlist codes — out of scope here.

Then fix the two existing tests that assert the OLD (now-wrong) contract in tests/scanner/test_scanner.py:
- `test_rescan_unsubscribes_evicted_active_code` (line ~1012) → rename to `test_rescan_never_evicts_managed_active_code`. Keep the KEEP/DROP fixture as-is (DROP fails D3 with gap 1% < 3%). Invert the assertions: `US.DROP` IS in `result`, `gw.unsubscribe.assert_not_called()`, and query `daily_scan` for `scan_date` to assert `US.DROP` has no row (proving nothing fabricated was persisted) while `US.KEEP` does.
- `test_rescan_logs_active_code_eviction` (line ~1065) → rename to `test_rescan_logs_carried_active_code` and assert exactly one `active_code_carried` warning naming `US.DROP`, and zero `active_code_evicted` warnings.

Do not touch `test_active_candidate_protected` (line ~900) — with 1 passing active + 19 filler slots it still yields exactly 20.
  </action>
  <verify>
    <automated>python3 -m pytest tests/scanner/test_scanner.py -q</automated>
  </verify>
  <done>A managed code failing the re-filter is returned in the watchlist, never unsubscribed, never persisted with fabricated values; scanner test module green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Make MoomooGateway.unsubscribe idempotent (Fix B) + full-suite gate</name>
  <files>bot/gateway/gateway.py, tests/gateway/test_gateway.py</files>
  <behavior>
    - `quote_ctx.unsubscribe` returning `(-1, "KL_5Min for US.DLR has not been subscribed. Cannot unsubscribe.")` → no exception, one `unsubscribe_not_subscribed` warning.
    - `quote_ctx.unsubscribe` returning `(-1, "quota exceeded")` → still raises `GatewayError` (discrimination is on the message, not the ret code).
    - Existing `(1, "unsub failed")` → still raises; `(0, "")` → still logs `unsubscribed_k5m`; `unsubscribe([])` → still a no-op.
  </behavior>
  <action>
In `MoomooGateway.unsubscribe` (bot/gateway/gateway.py:910-942), change `_unsubscribe_blocking` so a non-`RET_OK` return whose message contains `"not been subscribed"` (case-insensitive substring match on `str(msg).lower()` — OpenD's exact wording is `"KL_5Min for US.DLR has not been subscribed. Cannot unsubscribe."`) logs `_logger.warning("unsubscribe_not_subscribed", codes=codes, msg=str(msg))` and returns instead of calling `_check_ret`. Every other non-`RET_OK` return still goes through `_check_ret` and raises `GatewayError`.

Match defensively on the substring only — do not gate on the specific ret value (`-1`), and do not swallow other failures.

Update the docstring: unsubscribing a code OpenD does not consider subscribed is a no-op, not a failure; raising there aborted the entire intraday rescan mid-flight (260824-avx). Keep the `Raises: GatewayError` line, scoped to genuine failures.

Add two tests to `TestUnsubscribe` in tests/gateway/test_gateway.py (mirror the existing `_make_gateway_with_mocks()` + `asyncio.run` pattern):
- `test_unsubscribe_not_subscribed_is_benign`: `gw._quote_ctx.unsubscribe.return_value = (-1, "KL_5Min for US.DLR has not been subscribed. Cannot unsubscribe.")`; `asyncio.run(gw.unsubscribe(["US.DLR"]))` must not raise; patch the gateway module `_logger` with a MagicMock (`patch.object(gateway_mod, "_logger", MagicMock())`) and assert an `unsubscribe_not_subscribed` warning was emitted.
- `test_unsubscribe_raises_on_other_error_with_same_ret`: `(-1, "quota exceeded")` must still raise `GatewayError`.
  </action>
  <verify>
    <automated>python3 -m pytest -q</automated>
  </verify>
  <done>Benign "not been subscribed" replies warn and continue, genuine failures still raise, and the full suite (~1130 tests) is green.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| OpenD → bot | Broker-supplied `ret`/`msg` strings cross into control flow in `unsubscribe` |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-avx-01 | Tampering | `MoomooGateway.unsubscribe` error-message match | accept | Broker channel is localhost OpenD; worst case a genuine failure is downgraded to a warning on a read-only unsubscribe call — no order or money path touched |
| T-avx-02 | Denial of Service | `run_intraday_rescan` feed retention | mitigate | Managed codes are exempt from the top-20 cap, but the managed set is bounded by `max_concurrent_positions` (≪ 20), so the 20-slot quota cannot be exhausted |
| T-avx-SC | Tampering | package installs | n/a | No new dependencies |
</threat_model>

<verification>
- `python3 -m pytest -q` — full suite green (~1130 tests + 3 new).
- `grep -n "active_code_evicted" bot/scanner/scanner.py` — no matches (dead path removed).
- `grep -n "not been subscribed" bot/gateway/gateway.py` — the benign guard exists.
</verification>

<success_criteria>
- A managed symbol (open position / pending intent) that fails the intraday re-filter remains in the returned watchlist and keeps its K_5M subscription.
- `run_intraday_rescan` completes and returns its list even when a code's feed is already gone at OpenD, so `_job_intraday_rescan` reaches the premarket-high seeding call.
- No fabricated `daily_scan` rows for carried codes.
- Full test suite green.
</success_criteria>

<output>
Create `.planning/quick/260824-avx-fix-intraday-rescan-crash-protect-manage/260824-avx-SUMMARY.md` when done
</output>
