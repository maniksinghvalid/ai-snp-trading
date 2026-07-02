---
phase: quick-260702-ick
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - bot/gateway/gateway.py
  - bot/scanner/scanner.py
  - bot/risk/risk_engine.py
  - bot/config/schema.py
  - bot/config/loader.py
  - rules.json
  - tests/gateway/test_gateway.py
  - tests/execution/test_engine.py
  - tests/scanner/test_scanner.py
  - tests/config/test_loader.py
  - tests/risk/test_risk_engine.py
autonomous: true
requirements: [SAFE-OG-01, SCAN-08, RISK-01, EXEC-04]

must_haves:
  truths:
    - "get_external_codes(store) returns broker-held codes with no open bot DB position row and no pending intent; raises GatewayError when the broker position query fails"
    - "Premarket scan and 30-min intraday rescan drop external codes BEFORE the top-20 cap and log symbol_excluded_manual_holding per excluded code"
    - "A broker query failure at scan time proceeds WITHOUT exclusion (watchlist intact) and logs external_exclusion_skipped_query_failed"
    - "RiskEngine sizes from rules.json risk.sizing_equity_usd when set (default 100000) and falls back to live gateway.get_equity() when null; equity_used records the basis actually used"
    - "EXEC-04 blocks an entry when the intent's code is already held at the broker without bot ownership (regression documented; no engine.py code change)"
  artifacts:
    - path: "bot/gateway/gateway.py"
      provides: "get_external_codes(store) SAFE-OG-01 ownership predicate; raises GatewayError on query failure"
      contains: "def get_external_codes"
    - path: "bot/scanner/scanner.py"
      provides: "scan-time external-code exclusion helper wired into both scan entrypoints before the cap"
      contains: "symbol_excluded_manual_holding"
    - path: "bot/risk/risk_engine.py"
      provides: "sizing basis selection: cfg.sizing_equity_usd when set, else live equity"
      contains: "sizing_equity_usd"
    - path: "bot/config/schema.py"
      provides: "risk.sizing_equity_usd schema property (number|null, optional)"
      contains: "sizing_equity_usd"
    - path: "bot/config/loader.py"
      provides: "StrategyConfig.sizing_equity_usd field + rules.json mapping (default 100000, null preserved)"
      contains: "sizing_equity_usd"
    - path: "rules.json"
      provides: "risk.sizing_equity_usd default value"
      contains: "sizing_equity_usd"
  key_links:
    - from: "bot/scanner/scanner.py run_daily_scan / run_intraday_rescan"
      to: "gateway.get_external_codes(store)"
      via: "_run_coro bridge, applied to candidate list before the top-20 cap"
      pattern: "get_external_codes"
    - from: "bot/risk/risk_engine.py on_signal"
      to: "cfg.sizing_equity_usd"
      via: "basis selection that bypasses gateway.get_equity() when set"
      pattern: "sizing_equity_usd"
---

<objective>
Implement the approved Shared SIMULATE Account Isolation design so the bot never
sizes from, adopts, or competes with the operator's manual holdings on shared
SIMULATE account 1727266.

Three pieces, exactly as the spec locks them (no scope additions):
1. New gateway helper `get_external_codes(store)` — the SAFE-OG-01 ownership
   predicate returned as a set; raises `GatewayError` on a failed broker query.
2. Scanner scan-time exclusion — drop external codes before the top-20 cap in
   both the premarket scan and the 30-minute rescan; fail-open on query error.
3. New `rules.json` key `risk.sizing_equity_usd` (default 100000, null = live
   equity) used by RiskEngine as the sizing basis instead of live equity.
Plus a TEST-ONLY EXEC-04 regression that documents the entry-time backstop.

Purpose: close the two remaining shared-account gaps (symbol collision, equity
skew) identified in the design; unblock the Phase 06.2 Tier-1 UAT on 1727266.
Output: the gateway/scanner/risk changes above, all covered by TDD tests, with
the existing 496-pass suite kept green.

Source of truth (locked): docs/superpowers/specs/2026-07-02-shared-simulate-account-isolation-design.md
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@docs/superpowers/specs/2026-07-02-shared-simulate-account-isolation-design.md
@CLAUDE.md

# Interface + pattern anchors for executors (read the cited ranges, do not re-scan)
@bot/gateway/gateway.py          # SAFE-OG-01 predicate reconcile_once ~966-985 + startup_reconcile ~1132-1150; get_positions ~334-354; GatewayError ~148; RET_OK import ~29
@bot/scanner/scanner.py          # run_daily_scan cap ~400-404; run_intraday_rescan cap ~475-504; _run_coro ~296-312; _WATCHLIST_CAP ~40
@bot/risk/risk_engine.py         # get_equity call + sizing math ~121-158
@bot/config/schema.py            # risk block ~123-137
@bot/config/loader.py            # StrategyConfig dataclass ~63-128; risk mapping ~199-202
@bot/state/store.py              # get_open_positions ~407-428; has_pending_intent ~582-599
@tests/gateway/test_gateway.py   # _make_gateway_with_mocks ~50-65; AsyncMock get_positions pattern ~434-466
@tests/scanner/test_scanner.py   # patch-based scan tests + _make_cfg ~105-131
@tests/risk/test_risk_engine.py  # _make_cfg ~77-101; _make_mock_gateway ~144-148
@tests/execution/test_engine.py  # test_duplicate_guard EXEC-04 pattern ~334-455
@tests/config/test_loader.py     # rules dict ~40-100; field assertion pattern ~103-167
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Gateway get_external_codes(store) + EXEC-04 entry-backstop regression test</name>
  <files>bot/gateway/gateway.py, tests/gateway/test_gateway.py, tests/execution/test_engine.py</files>
  <behavior>
    get_external_codes(store) — new async method on MoomooGateway (SAFE-OG-01 predicate):
    - held + bot-owned (open DB position row) → EXCLUDED from result
    - held + bot-owned (pending intent, no position row) → EXCLUDED from result
    - held + not-owned (manual holding) → INCLUDED in result
    - short position (qty < 0), not bot-owned → INCLUDED (never bot-tradable)
    - broker query returns ret != RET_OK (or data None) → raises GatewayError
    EXEC-04 regression (documents existing behavior, tests/execution):
    - consume_intent returns None and logs duplicate_entry_blocked_open_position
      when get_positions reports the intent's code already held at the broker
      without bot ownership; place_order is never called.
  </behavior>
  <action>
    RED first. In tests/gateway/test_gateway.py add a TestGetExternalCodes class using
    the existing _make_gateway_with_mocks() helper and the AsyncMock get_positions
    pattern (mirror test_reconcile_once_qty_drift_adopts_broker_qty ~434-466). Cover the
    five behaviors above. Build a mock store with get_open_positions() returning a list
    of {"code": ...} dicts and has_pending_intent(code) returning a bool, matching the
    real store.get_open_positions (store.py ~407-428) and store.has_pending_intent
    (~582-599) contracts. For the failure case, set gw.get_positions = AsyncMock(return
    value=(-1, None)) and assert pytest.raises(GatewayError). Run to confirm RED.

    Also RED: in tests/execution/test_engine.py add test_exec04_blocks_entry_for_manual_holding,
    reusing the test_duplicate_guard path (a) pattern (~334-382): build an OrderIntent for
    a code, set gw.get_positions = AsyncMock(return_value=(0, positions_df)) where
    positions_df holds that code with qty > 0, assert consume_intent returns None and
    place_order is never awaited. Docstring must state this documents the entry-time
    backstop that layers with scan-time exclusion (spec Testing section). NO change to
    bot/execution/engine.py.

    GREEN: add async get_external_codes(self, store) to MoomooGateway near get_positions
    (~355). Call ret, data = await self.get_positions(refresh_cache=True) (Pitfall B
    mandatory). If ret != RET_OK or data is None → raise GatewayError with a descriptive
    message (per spec: caller decides the fallback). Otherwise compute
    open_pos_codes = {r["code"] for r in store.get_open_positions()}, iterate broker rows,
    skip empty codes, and add code to the result set when NOT bot-owned, where
    bot_owned = code in open_pos_codes or store.has_pending_intent(code) — identical
    predicate to SAFE-OG-01 (gateway.py ~972-977 / ~1136-1143). Do NOT filter by
    long/short: any not-bot-owned broker code (including shorts) is external. Return the set.
    Reference GatewayError (~148) and RET_OK (~29), both already in module scope.
  </action>
  <verify>
    <automated>python3 -m pytest tests/gateway/test_gateway.py tests/execution/test_engine.py -q</automated>
  </verify>
  <done>get_external_codes returns the correct set for owned/not-owned/short cases and raises GatewayError on query failure; the EXEC-04 regression test passes with no engine.py change; both test modules green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Scanner scan-time exclusion of externally held codes (both scan paths)</name>
  <files>bot/scanner/scanner.py, tests/scanner/test_scanner.py</files>
  <behavior>
    - An externally held code present in the candidate list is dropped BEFORE the
      top-20 cap; symbol_excluded_manual_holding is logged for it.
    - A bot-owned code (not in the external set) is NOT dropped.
    - When gateway.get_external_codes raises GatewayError, the scan proceeds with the
      full candidate list (watchlist intact) and logs external_exclusion_skipped_query_failed.
    - gateway=None (unit-test path) is a no-op: candidate list unchanged, no broker call.
    Applies identically to run_daily_scan and run_intraday_rescan.
  </behavior>
  <action>
    RED first. In tests/scanner/test_scanner.py add tests that patch _compute_candidates
    (or reuse the existing fetch/download/get_ticker_frame patch stack) to yield a known
    candidate list including one code that will be reported external, then pass a MagicMock
    gateway whose get_external_codes is an AsyncMock. Assert: (a) external code absent from
    the returned watchlist; (b) a non-external bot code retained; (c) when get_external_codes
    side_effect is GatewayError, the full list is retained. Add analogous coverage for
    run_intraday_rescan. Import GatewayError from bot.gateway.gateway in the test. Run RED.

    GREEN: add a module-level helper _exclude_external_codes(gateway, store, passing) in
    scanner.py. If gateway is None → return passing unchanged (preserves all existing
    gateway=None tests). Otherwise wrap in try/except GatewayError: external =
    _run_coro(gateway.get_external_codes(store)) (bridge already at ~296-312); build the
    filtered list dropping candidates whose "code" is in external, logging
    _logger.info("symbol_excluded_manual_holding", code=code, scan_pass=scan_pass) per
    dropped code; return the filtered list. On GatewayError log
    _logger.warning("external_exclusion_skipped_query_failed", scan_pass=scan_pass,
    exc_info=True) and return passing unchanged (fail-open — a scan must never come up
    empty on a transient broker error; EXEC-04 is the entry backstop).

    Wire it in BOTH entrypoints, immediately after passing = _compute_candidates(...) and
    BEFORE the gap sort / cap: in run_daily_scan insert before line ~403 (passing.sort);
    in run_intraday_rescan insert before line ~478 (passing.sort). Pass store and gateway
    (both already function params) into the helper.
  </action>
  <verify>
    <automated>python3 -m pytest tests/scanner/test_scanner.py -q</automated>
  </verify>
  <done>External codes are removed from both scan paths before the cap with a per-code log; a bot-owned code is retained; a GatewayError keeps the watchlist intact with the skip warning; gateway=None remains a no-op; existing scanner tests stay green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Fixed sizing basis — rules.json risk.sizing_equity_usd + schema/loader/RiskEngine</name>
  <files>rules.json, bot/config/schema.py, bot/config/loader.py, bot/risk/risk_engine.py, tests/config/test_loader.py, tests/risk/test_risk_engine.py</files>
  <behavior>
    - StrategyConfig.sizing_equity_usd defaults to 100000 when the key is absent from
      rules.json; an explicit null in rules.json maps to None (live-equity fallback).
    - RiskEngine.on_signal uses cfg.sizing_equity_usd as the equity basis when it is not
      None (gateway.get_equity is NOT awaited); when None it awaits gateway.get_equity()
      exactly as today. intent.equity_used records whichever basis was used.
  </behavior>
  <action>
    RED first. In tests/config/test_loader.py: add a test asserting cfg.sizing_equity_usd
    == 100000 when the risk block omits the key (current rules dict ~53-58), and a test
    that adds "sizing_equity_usd": null to the risk block and asserts cfg.sizing_equity_usd
    is None. In tests/risk/test_risk_engine.py: extend the _make_cfg helper (~77-101) with a
    new sizing_equity_usd=None keyword (default None so every EXISTING risk test keeps
    exercising the live-equity path unchanged) and pass it into the StrategyConfig(...) call.
    Add two new tests: (a) fixed basis — cfg via _make_cfg(sizing_equity_usd=200000) with
    _make_mock_gateway(equity=100000); assert intent.equity_used == 200000 and
    gw.get_equity.assert_not_awaited(); (b) null fallback — _make_cfg(sizing_equity_usd=None)
    with equity=150000; assert intent.equity_used == 150000 and gw.get_equity.assert_awaited_once().
    Run RED.

    GREEN:
    - rules.json: add "sizing_equity_usd": 100000 to the risk block (source-of-truth default).
    - bot/config/schema.py: in the risk properties (~131-136) add
      "sizing_equity_usd": {"type": ["number", "null"]}. Do NOT add it to risk.required
      (absent key must fall back to the loader default).
    - bot/config/loader.py: add from typing import Optional; append field
      sizing_equity_usd: Optional[float] = 100_000.0 to the END of the StrategyConfig
      dataclass (a trailing default keeps every existing StrategyConfig(...) construction
      site valid). In the mapping (~199-202) set sizing_equity_usd=rk.get("sizing_equity_usd",
      100000) — absent → 100000, explicit null → None, both correct.
    - bot/risk/risk_engine.py: replace the unconditional equity = await
      self._gateway.get_equity() at ~121-122 with basis selection: if
      self._cfg.sizing_equity_usd is not None: equity = float(self._cfg.sizing_equity_usd)
      else: equity = await self._gateway.get_equity(). Leave the rest of the sizing math and
      equity_used=equity unchanged (equity_used already records the basis used, per spec).
  </action>
  <verify>
    <automated>python3 -m pytest tests/config/test_loader.py tests/risk/test_risk_engine.py -q</automated>
  </verify>
  <done>sizing_equity_usd loads with the 100000 default and preserves null; RiskEngine sizes from the fixed basis without calling get_equity when set and falls back to live equity when null; equity_used reflects the basis used; config + risk suites green.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Broker (OpenD) → gateway/scanner | Broker position rows are external input; a stale or failed read must never cause the bot to trade wrong or come up empty. |
| rules.json → config loader | Operator-controlled config; a new optional key must not break existing loads. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-quick-01 | Information Disclosure / Tampering | scanner exclusion on broker-query failure | accept | Spec-locked fail-open: on GatewayError the scan proceeds without exclusion and logs external_exclusion_skipped_query_failed; EXEC-04 remains the entry-time backstop (blocking any entry on a code already held at the broker). Failing all trading on a transient read is worse. |
| T-quick-02 | Tampering (wrong sizing) | RiskEngine basis selection | mitigate | Default basis is the documented $100,000; equity_used is always recorded so an audit shows the exact basis used; null path preserves the existing implausible-value guards in gateway.get_equity(). |
| T-quick-03 | Denial of Service | new optional config key | mitigate | sizing_equity_usd is optional in schema with a loader default (100000); trailing dataclass default keeps every existing construction site valid → no load regressions. |
| T-quick-SC | Tampering | npm/pip/cargo installs | n/a | No package installs in this plan; no dependency changes. |
</threat_model>

<verification>
Run the full suite and confirm no regression against the develop baseline
(496 passed, 1 skipped):

```
python3 -m pytest -q
```

Targeted checks:
- `python3 -m pytest tests/gateway/test_gateway.py tests/execution/test_engine.py tests/scanner/test_scanner.py tests/config/test_loader.py tests/risk/test_risk_engine.py -q`
- `python3 -c "from bot.config.loader import load_strategy_config; c=load_strategy_config('rules.json'); print('sizing_equity_usd=', c.sizing_equity_usd)"` prints 100000.
</verification>

<success_criteria>
- get_external_codes(store) implements the SAFE-OG-01 predicate and raises GatewayError on failed query (4 behaviors covered).
- Both scan entrypoints drop external codes before the top-20 cap with symbol_excluded_manual_holding logs; fail-open with external_exclusion_skipped_query_failed on GatewayError; gateway=None is a no-op.
- risk.sizing_equity_usd (default 100000, null = live equity) drives RiskEngine sizing; equity_used records the basis used.
- EXEC-04 regression test documents the entry backstop with NO change to bot/execution/engine.py.
- Full pytest suite green with no regression from the 496-pass baseline (new tests added).
</success_criteria>

<output>
Create `.planning/quick/260702-ick-implement-shared-simulate-account-isolat/260702-ick-SUMMARY.md` when done.
</output>
