---
phase: 260817-ask-fix-screen-options-1000-row-server-cap-s
plan: 01
type: execute
wave: 1
depends_on: [260817-1ie]
files_modified:
  - bot/gateway/gateway.py
  - tests/gateway/test_gateway_options.py
autonomous: true
requirements: [OPT-GW-SCREEN-01]

must_haves:
  truths:
    - "screen_options issues one option-screen request per underlying, so no single request can be truncated by the server's 1000-row cap."
    - "Rows from every underlying are merged into one returned list, in stock_ids order."
    - "The bounded paging loop (max pages, last_page break, empty-page break) still applies — now per underlying."
    - "page_from restarts at 0 for each underlying."
    - "An SDK failure on ANY underlying raises GatewayError (no silent partial result)."
    - "The live read-only probe run is recorded in SUMMARY.md — either section 4 now shows strike geometry for SPY/QQQ/DIA/FXI, or the reason it could not be verified is written down explicitly."
  artifacts:
    - path: "bot/gateway/gateway.py"
      provides: "screen_options with a per-underlying request builder + loop inside one executor job"
      contains: "_build_req"
    - path: "tests/gateway/test_gateway_options.py"
      provides: "per-underlying request, per-underlying paging cap, page_from reset, second-underlying failure coverage"
      contains: "test_each_underlying_gets_its_own_request"
  key_links:
    - from: "bot/gateway/gateway.py:screen_options"
      to: "OptionScreenRequest (one fresh instance per underlying)"
      via: "_build_req(stock_id) closure called inside the _blocking loop"
      pattern: "for stock_id in stock_ids"
    - from: "tests/gateway/test_gateway_options.py"
      to: "req._filter_groups[0]['underlying'][0]['value_list']"
      via: "side_effect keyed on the STOCK_LIST filter of the request under test"
      pattern: "_filter_groups"
---

<objective>
`Gateway.screen_options` sends ONE `OptionScreenRequest` carrying a STOCK_LIST filter with
all 15 ETFs. The live RTH probe on 2026-08-17 came back with exactly 1000 put rows and 1000
call rows — the server caps a single screen at 1000 and sets `last_page` there. The cap is
consumed unevenly (IWM 273, SLV 218 vs SPY 19, QQQ 87), so probe section 4 falsely reports
"no strike geometry" for SPY/QQQ/DIA/FXI and the live bot would silently skip those
underlyings.

Purpose: remove the truncation at its root — screen one underlying at a time so the 1000-row
cap is per underlying instead of per universe. Everything else (single executor job, bounded
paging, last_page/empty-page defenses, row normalisation) stays exactly as-is.

Output: fixed `screen_options`, four new tests, a green full suite, and a recorded live probe run.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

@bot/gateway/gateway.py
@tests/gateway/test_gateway_options.py
@scripts/uat_options_probe.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Screen per underlying inside the same executor job</name>
  <files>tests/gateway/test_gateway_options.py, bot/gateway/gateway.py</files>

  <behavior>
Write these four tests FIRST, in `TestScreenOptions` in tests/gateway/test_gateway_options.py,
and confirm they fail against the current single-request implementation before editing the
gateway. Mocks key off the SDK's internal filter storage: the STOCK_LIST value list lives at
`req._filter_groups[0]['underlying'][0]['value_list']` (a list of int); `req.page_from` is a
plain int attribute (the existing `test_page_from_advances_by_rows_returned` already reads it).
Use `gw._quote_ctx.get_option_screen.side_effect = lambda req: ...` and branch on that value list.

  - `test_each_underlying_gets_its_own_request` — stock_ids `[202805, 202806]`; each request
    returns a single-row last_page page whose row carries `underlying["stock_id"]` equal to the
    id that was requested. Assert `call_count == 2`, that EACH observed request's STOCK_LIST
    value_list held exactly one id, and that the merged result's `u_stock_id` values are
    `[202805, 202806]` in stock_ids order.
  - `test_paging_cap_is_per_underlying` — two stock_ids, server never sets last_page and always
    returns a non-empty page. Assert `call_count == 2 * _OPTION_SCREEN_MAX_PAGES`.
  - `test_page_from_resets_per_underlying` — two stock_ids; the first underlying serves a 2-row
    page (not last) then a 1-row last page, the second serves one last page. Record `req.page_from`
    at each call and assert the sequence is `[0, 2, 0]`.
  - `test_sdk_failure_on_second_underlying_raises` — first id returns a good last page, second
    returns `(-1, "boom")`. Assert `pytest.raises(GatewayError)`.

The nine existing `TestScreenOptions` tests pass a single stock_id `[202805]` and MUST keep
passing byte-for-byte unmodified — with one underlying the new loop runs exactly one pass, so
every existing `call_count` assertion (including `test_paging_is_capped`) still holds. If any
existing test needs editing to go green, the implementation is wrong; fix the implementation.
  </behavior>

  <action>
In `bot/gateway/gateway.py::screen_options` (currently lines ~686-774), move the request
construction that today runs once — `OptionScreenRequest(...)`, `add_underlying_filter(STOCK_LIST, ...)`,
the OPTION_TYPE/LEFT_DAY/DELTA option filters, `add_sort`, and both retrieve loops — into a local
closure `_build_req(stock_id)` defined after the deferred moomoo import and returning a fresh
request whose STOCK_LIST `values` is `[stock_id]` (a one-element list, not the full universe). A
fresh instance per underlying is mandatory: the SDK builder APPENDS filters, so a reused request
would accumulate one STOCK_LIST filter per iteration.

Rewrite `_blocking()` to own one shared `rows: list` and wrap the existing bounded paging loop in
`for stock_id in stock_ids:` — per underlying, call `req = _build_req(stock_id)`, set
`req.page_from = 0`, then run the UNCHANGED inner loop: `for _ in range(_OPTION_SCREEN_MAX_PAGES)`,
`get_option_screen(req)` -> `_check_ret(...)` -> extend `rows` with `_normalise_option_row(dict(row), right)`
-> break on `last_page or page_len == 0` -> else `req.page_from += page_len`. Keep the comment
explaining that the empty-page break defends against a server that never sets last_page. Still ONE
`run_in_executor(None, _blocking)` call — do not await per underlying and do not add concurrency.

Fix the two now-false comments:
  - The `screen_options` docstring opens with "One request covers every underlying at once (the
    screener takes a STOCK_LIST filter), so a full universe scan costs 1 call per right." Replace it
    with the truth: one request PER underlying, ~15 calls per right, because the server caps a single
    screen at 1000 rows and a shared request silently starves the low-volume underlyings (observed
    live 2026-08-17: SPY got 19 of 1000 rows).
  - The `_OPTION_SCREEN_MAX_PAGES` comment at lines ~91-94 must say the cap is PER UNDERLYING and
    drop the "15 ETFs" arithmetic, which no longer describes a single request.

Add the per-underlying row counts to the existing `option_screen_done` log so a future truncation is
visible without a probe: keep the current `right`/`underlyings`/`rows` fields and add one compact
field mapping stock_id -> row count (build it from the rows already collected; do not add a second
pass over the SDK). Change nothing else — `_normalise_option_row`, `_check_ret`, the method
signature, and every caller stay untouched.
  </action>

  <verify>
    <automated>python3 -m pytest tests/gateway/test_gateway_options.py -q</automated>
  </verify>

  <done>
All 4 new tests pass, all 9 pre-existing `TestScreenOptions` tests pass unmodified, and
`grep -n "One request covers every underlying" bot/gateway/gateway.py` returns nothing.
  </done>
</task>

<task type="auto">
  <name>Task 2: Full suite + live read-only probe, recorded in SUMMARY</name>
  <files>.planning/quick/260817-ask-fix-screen-options-1000-row-server-cap-s/260817-ask-SUMMARY.md, bot/gateway/gateway.py</files>

  <action>
Run the full suite: `python3 -m pytest -q` (961 tests before this change; expect 965 after the
four additions). Any failure outside tests/gateway/test_gateway_options.py means the change leaked
— fix it before continuing.

Then run the read-only live probe (needs OpenD logged in on 127.0.0.1:11111, and RTH — it is
2026-08-17, market is open):

    PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 scripts/uat_options_probe.py

Do NOT pass `--live-1lot`; this run places no orders. Capture the section 2 line
(`rows: puts=... calls=...`), the section 3 per-underlying row counts, and section 4's strike
geometry verdicts.

Expected: puts/calls are no longer pinned at exactly 1000 each, per-underlying counts for the
low-volume names (SPY, QQQ, DIA, FXI) are non-zero, and section 4 no longer reports them as
"no strike geometry".

Two contingencies, both of which must be written into SUMMARY.md rather than swallowed:
  - OpenD unreachable / outside RTH / probe otherwise cannot run -> record the exact error under an
    "Unverified" heading in SUMMARY.md and state that the live confirmation is still owed. Do not
    mark the probe as passing and do not delete the item.
  - The probe surfaces an SDK rate-limit error from `get_option_screen` (message contains
    "high frequency" or "Maximum N times per 30 seconds" — the gateway's `_RATE_LIMIT_MARKERS`
    retry path covers only the market-snapshot call, not this one) -> add a small blocking
    `time.sleep(...)` between underlyings inside `_blocking` with a `# ponytail:` comment naming the
    ceiling and the upgrade path (shared rate-limit handling if more read paths need it). Blocking
    sleep is correct there — it is an executor thread, not the event loop. Do NOT build a retry
    framework, and do not add the sleep speculatively if the probe shows no rate-limit error.

Do not modify `bot/options/service.py` or `scripts/uat_options_probe.py`. If the probe exposes a
bug in either, record it in SUMMARY.md as a follow-up; it is out of scope for this task.

Write the SUMMARY per the standard template, including the verbatim `rows: puts=... calls=...`
line and the section 4 verdict for SPY/QQQ/DIA/FXI as the evidence that the 1000-row cap is gone.
  </action>

  <verify>
    <automated>python3 -m pytest -q</automated>
    <human-check>Probe output for section 2 row counts and section 4 strike geometry is pasted into SUMMARY.md (or the failure to run it is recorded under "Unverified").</human-check>
  </verify>

  <done>
Full suite green; SUMMARY.md exists and contains either the probe evidence showing the cap is
lifted, or an explicit Unverified entry naming the blocker.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| OpenD -> gateway | Broker-supplied option chain rows cross into strategy inputs |
| gateway -> broker | Read-only in this task; no order path is touched |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ask-01 | Denial of Service | `screen_options` paging loop | mitigate | `_OPTION_SCREEN_MAX_PAGES` bound retained per underlying plus the empty-page break; a server that never sets `last_page` cannot spin the executor thread |
| T-ask-02 | Denial of Service | OpenD screen rate limit | mitigate | Call count rises from 1 to ~15 per right; Task 2 probes live and adds a bounded inter-underlying sleep only if the SDK reports rate limiting |
| T-ask-03 | Information Disclosure | truncated chain -> silent bad decisions | mitigate | Per-underlying row counts added to the `option_screen_done` log so future truncation is observable without a live probe |
| T-ask-04 | Tampering | broker order path | accept | Read-only change; no order placement, no `_OPTION_CODE_RE` / SAFE-OG-01 surface touched |
| T-ask-SC | Tampering | npm/pip installs | n/a | No dependencies added or upgraded in this task |
</threat_model>

<verification>
- `python3 -m pytest tests/gateway/test_gateway_options.py -q` green (13 tests in `TestScreenOptions`).
- `python3 -m pytest -q` green (~965 tests).
- `grep -n "for stock_id in stock_ids" bot/gateway/gateway.py` matches inside `_blocking`.
- Live probe output recorded in SUMMARY.md (result or explicit blocker).
</verification>

<success_criteria>
- One `OptionScreenRequest` per underlying, one `run_in_executor` job for the whole scan.
- Paging cap, `last_page` break, empty-page break, and `page_from` advancement preserved per underlying.
- Existing `TestScreenOptions` tests unmodified and passing.
- Stale "one request covers every underlying" docstring and the `_OPTION_SCREEN_MAX_PAGES` comment corrected.
- No change to `bot/options/service.py` or `scripts/uat_options_probe.py`.
- Commit message uses the `fix(options):` prefix.
</success_criteria>

<output>
Create `.planning/quick/260817-ask-fix-screen-options-1000-row-server-cap-s/260817-ask-SUMMARY.md` when done.
</output>
