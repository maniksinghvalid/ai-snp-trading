---
phase: quick-260926-kvt
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/options/test_service.py
  - bot/options/service.py
autonomous: true
requirements: [QUICK-260926-kvt]

must_haves:
  truths:
    - "The options bot never opens a spread on an underlying where the broker already holds any non-zero option qty (a leg code or any other option on that underlying)"
    - "A failed broker position read blocks the entry (fail closed): no DB rows, no executor call"
    - "A skipped candidate writes NO position/leg rows and calls NO LegExecutor method"
    - "Broker holdings on OTHER underlyings (including the bot's own open legs there) do not block an entry"
    - "A longer ticker (US.TLTW...) is never attributed to US.TLT"
  artifacts:
    - path: "bot/options/service.py"
      provides: "_OPTION_UNDERLYING_RE + pre-insert broker-holding guard in OptionsBot._try_open"
      contains: "options_entry_foreign_holding"
    - path: "tests/options/test_service.py"
      provides: "5 entry-scan regression tests for the broker-holding guard"
      contains: "test_entry_scan_fails_closed_when_broker_read_fails"
  key_links:
    - from: "OptionsBot._try_open"
      to: "MoomooGateway.get_option_positions"
      via: "one fresh await per sized candidate, before self._store.insert_option_position(pos)"
      pattern: "get_option_positions\\(\\)"
---

<objective>
Close the live 2026-09-25 incident on shared SIMULATE account 1727266: a second options-bot run with a fresh options_state.db opened a TLT iron condor that SOLD 6x US.TLT261120P75000 while the main DB's OPEN TLT position was LONG 6x that exact contract (broker net 0 → reconcile mismatch → NEEDS_ATTENTION). Entry currently checks one-position-per-underlying against this bot's own SQLite DB only; it never looks at the broker.

Purpose: enforce the CLAUDE.md Phase 8 invariant "the options bot never touches broker option codes not in its own option_legs" on the ENTRY path, and fail closed when the broker cannot be read.
Output: a per-candidate broker-holding guard in `OptionsBot._try_open` (bot/options/service.py only) plus 5 regression tests, written red-first.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@./CLAUDE.md
@bot/options/service.py
@tests/options/test_service.py

<interfaces>
Facts the executor needs (verified at plan time, do not re-derive):

- `MoomooGateway.get_option_positions()` (bot/gateway/gateway.py ~L813) — async, returns `{option_code: signed int qty}` (shorts negative), only codes matching `^US\.[A-Z]+\d{6}[CP]\d+$`; raises GatewayError on non-RET_OK. NOT modified by this plan.
- `bot/options/service.py`: stdlib imports at top are `asyncio, html, os, sys` (add `re` alphabetically after `os`). Module constants block ends with `_ACTIVE_STATUSES = ("OPENING", "OPEN", "CLOSING", "NEEDS_ATTENTION")` and `_OPEN_STATUSES = ("OPEN", "OPENING")`.
- `_scan_and_open` builds `busy = {p["underlying"] for p in active}` from `_ACTIVE_STATUSES` rows and `continue`s past any busy underlying BEFORE calling `_try_open(code, u_rows, today, open_max_loss_total)`. `code` is the universe stock code, e.g. "US.SPY". A `_try_open` returning None is a skip (caller still adds `code` to busy). Any exception escaping `_try_open` is caught by the caller and logged as `options_entry_underlying_error`.
- `_try_open` order today: entry gate → pick_expiry → chain/u_price → `sel = pick_strikes(...)` (`sel["legs"]` is a list of dicts each with `"code"`) → `qty = size_position(...)`; `if qty < 1: return None` → `position_id = uuid4().hex` → build `pos` → `self._store.insert_option_position(pos)` → leg rows → `self._executor.open_position(...)`.
- Existing async error pattern in this file: `except asyncio.CancelledError: raise` then `except Exception:` with `_logger.error("<event>", ..., exc_info=True)`.

Test-file facts (tests/options/test_service.py):
- Fixtures: `store` (real OptionsStore on tmp DB), `gateway` (MagicMock; `get_option_positions = AsyncMock(return_value={})` by default — so every existing entry test keeps passing once the guard reads the broker), `alerter`, `make_bot(cfg=None)`; `options_cfg` from tests/options/conftest.py (iron_condor, max_concurrent_positions=8, max_new_positions_per_day=2, sizing_equity_usd=100000, max_bp_usage_pct=25).
- Entry-scan section helpers (~L310-420): `TODAY = date(2026, 8, 17)`, `EXPIRY = "2026-10-01"`, `_chain(...)` codes are `f"US.SPY261001{r}{int(strike*1000)}"`; the scan's selected legs are exactly US.SPY261001P594000 (BUY), US.SPY261001P600000 (SELL), US.SPY261001C610000 (SELL), US.SPY261001C616000 (BUY), qty 2. `_wire_scan(bot, gateway, monkeypatch)` freezes clock at SESSION_NOON and wires universe `{"US.SPY": 1}`. `_fake_executor()` returns a MagicMock whose `open_position` is an AsyncMock. Drive with `_run(bot._job_entry_scan())`.
- `_pos(position_id, status="OPEN", **over)` default `opened_at` is "2026-08-17T10:00:00-04:00" == TODAY (counts toward `count_opened_on`); `_leg(leg_id, position_id, side, code, **over)` default right "P", qty 2. `_seed_open_spread` hardcodes SPY leg codes (LONG_P/SHORT_P) — do NOT use it for an own-QQQ position.
- `service` is imported as `import bot.options.service as service`. Do NOT add `_OPTION_UNDERLYING_RE` to the top-level `from bot.options.service import (...)` block — during the red step that ImportError would break collection of the WHOLE file. Reference it as `service._OPTION_UNDERLYING_RE` inside the test.

Baseline counts at plan time: full suite 1152 collected; tests/options 188 collected. Expected after this plan: 1157 / 193.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Red — add 5 broker-holding guard tests to the Entry-scan section</name>
  <files>tests/options/test_service.py</files>
  <behavior>
    - (a) test_entry_scan_skips_when_broker_holds_foreign_qty_in_a_leg_code: broker `{"US.SPY261001P600000": 6}` → no rows in any of OPENING/OPEN/ABORTED/NEEDS_ATTENTION, `bot._executor.open_position.assert_not_awaited()`, exactly one `_logger.warning` call with event "options_entry_foreign_holding" whose kwargs are underlying="US.SPY", codes=["US.SPY261001P600000"], leg_codes=["US.SPY261001P600000"].
    - (b) test_entry_scan_skips_when_broker_holds_other_option_on_same_underlying: broker `{"US.SPY261120P550000": -3}` (different expiry, not a leg) → same no-rows / not-awaited assertions; warning kwargs codes=["US.SPY261120P550000"], leg_codes=[].
    - (c) test_entry_scan_opens_when_broker_holds_only_own_legs_elsewhere: seed this bot's own OPEN QQQ put spread directly with `store.insert_option_position(_pos("OWN", underlying="US.QQQ", opened_at="2026-08-10T10:00:00-04:00"))` plus two legs via `_leg("OWN-L1", "OWN", side="BUY", code="US.QQQ260320P494000", strike=494.0)` and `_leg("OWN-L2", "OWN", side="SELL", code="US.QQQ260320P500000", strike=500.0)` (qty 2 each). Broker `{"US.QQQ260320P494000": 2, "US.QQQ260320P500000": -2}`. → exactly one OPEN row with underlying "US.SPY" (plus the OWN row, so 2 OPEN total), `open_position` awaited once, no "options_entry_foreign_holding" warning. Caps are fine as-is: open_count 1 < 8, opened_today 0 < 2 (opened_at moved off TODAY on purpose), bp cap 25000-800 ≫ 370/spread.
    - (d) test_entry_scan_fails_closed_when_broker_read_fails: `gateway.get_option_positions = AsyncMock(side_effect=Exception("OpenD down"))` → no rows in any of OPENING/OPEN/ABORTED/NEEDS_ATTENTION, `open_position` not awaited, a `_logger.error` call with event "options_entry_broker_read_failed" and kwargs underlying="US.SPY" (asserting the event name proves the guard caught it inside `_try_open`, not the caller's generic "options_entry_underlying_error").
    - (e) test_option_underlying_re_keeps_longer_tickers_apart: `service._OPTION_UNDERLYING_RE.match("US.TLTW261120P75000").group(1) == "US.TLTW"` and `service._OPTION_UNDERLYING_RE.match("US.TLT261120P75000").group(1) == "US.TLT"`.
  </behavior>
  <action>
Append the five tests above to the end of the "Entry scan" section of tests/options/test_service.py (after test_entry_scan_aborts_position_when_open_fails, before the "# Manage" banner), per locked design item 5. Each of (a)-(d) follows the existing pattern: `bot = make_bot()`, `_wire_scan(bot, gateway, monkeypatch)`, `bot._executor = _fake_executor()`, set `gateway.get_option_positions`, `log = MagicMock()` then `monkeypatch.setattr(service, "_logger", log)`, then `_run(bot._job_entry_scan())`. Filter log calls by event name, e.g. collect `c.kwargs` for `c in log.warning.call_args_list if c.args and c.args[0] == "options_entry_foreign_holding"`; do not assert on total warning count (the scan may log other warnings). For (c) assert on `[p for p in store.get_option_positions(("OPEN",)) if p["underlying"] == "US.SPY"]` having length 1. Reference the regex only as `service._OPTION_UNDERLYING_RE` inside test (e) — never in the module import block (see interfaces). Give each test a one-line docstring only where the intent is non-obvious ((a) cite the 2026-09-25 TLT net-zero incident; (c) say it guards against over-blocking).

Run the new tests and confirm the red state: (a), (b), (d), (e) FAIL (a/b/d because the position opens today; e with AttributeError); (c) PASSES already — it is a no-over-blocking regression guard, and passing pre-change is expected, not a red-step failure. Then commit ONLY the test file: `git add tests/options/test_service.py` and commit with message `test(260926-kvt): add failing tests for options entry broker-holding guard` followed by a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not stage any .planning/ file.
  </action>
  <verify>
    <automated>cd /Users/acdc/Documents/AI/ai-snp-trading-claude/.claude/worktrees/confident-khayyam-662076 && python3 -m pytest -q tests/options/test_service.py -k "broker_holds or broker_read_fails or underlying_re" ; test $? -ne 0 && python3 -m pytest -q tests/options/test_service.py -k "only_own_legs_elsewhere"</automated>
  </verify>
  <done>Five new tests exist; the -k run reports 4 failed / 1 passed ((c) passes); all pre-existing tests in the file still collect and pass; test-only commit `test(260926-kvt): ...` created.</done>
</task>

<task type="auto">
  <name>Task 2: Green — per-candidate broker-holding guard in OptionsBot._try_open</name>
  <files>bot/options/service.py</files>
  <action>
Implement locked design items 1-4 in bot/options/service.py ONLY (do not touch bot/options/execution.py, bot/gateway/gateway.py, or scripts/uat_options_probe.py).

1. Add `import re` to the stdlib import block (between `os` and `sys`).
2. After `_OPEN_STATUSES`, add module constant `_OPTION_UNDERLYING_RE = re.compile(r"^(US\.[A-Z]+)\d{6}[CP]\d+$")` with a one-line comment: same shape as the gateway's `_OPTION_CODE_RE`, with the underlying captured; the `\d{6}` anchor is what keeps "US.TLTW..." from matching "US.TLT".
3. In `_try_open`, immediately after `if qty < 1: return None` and BEFORE `position_id = uuid4().hex` (so a skip writes no rows and never reaches the executor — locked item 1), add the guard:
   - One fresh read: `broker = await self._gateway.get_option_positions()` inside try; `except asyncio.CancelledError: raise`; `except Exception:` → `_logger.error("options_entry_broker_read_failed", underlying=code, exc_info=True)` and `return None` (fail closed, locked item 2).
   - Build `foreign` = {broker code: int qty} for every broker item whose `_OPTION_UNDERLYING_RE.match(c)` is truthy, whose `.group(1) == code`, and whose `int(qty or 0) != 0` (treat a None broker result as empty). Plain loop is fine; no helper function.
   - If `foreign` is non-empty: `overlap` = set of `foreign` keys that are also in `{leg["code"] for leg in sel["legs"]}`; `_logger.warning("options_entry_foreign_holding", underlying=code, codes=sorted(foreign), leg_codes=sorted(overlap))`; `return None`. No Telegram alert, no append_audit (it is a skip, not a trade action — locked item 3).
   - Put this comment directly above the foreign-holding check (locked item 3 coupling note): `# ponytail: every broker holding on this underlying is foreign — _scan_and_open already skipped any underlying with an ACTIVE row in this DB (busy, from _ACTIVE_STATUSES), so none of these legs are ours. If one-position-per-underlying is ever relaxed, subtract own ACTIVE leg qty here.` Add one short line above the read explaining why it is per-candidate, not per-scan: a prior `_try_open` in the same scan can spend minutes waiting on fills, so a scan-level snapshot goes stale.
4. Extend the `_try_open` docstring by one sentence: it refuses to open (returns None) when the broker already holds any option on this underlying or cannot be read (SAFE-OG-01).

Run the Task 1 tests (all 5 green), then `python3 -m pytest -q tests/options` then the full suite `python3 -m pytest -q`, and record exact pass counts for the SUMMARY (expected 193 and 1157 at plan-time baseline; report actuals, not these). Commit ONLY bot/options/service.py with message `fix(260926-kvt): block options entry when broker holds foreign option qty on the underlying; fail closed on broker read failure` followed by a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not stage PLAN.md, SUMMARY.md, or STATE.md — the orchestrator commits docs.
  </action>
  <verify>
    <automated>cd /Users/acdc/Documents/AI/ai-snp-trading-claude/.claude/worktrees/confident-khayyam-662076 && python3 -m pytest -q tests/options && python3 -m pytest -q && git diff --name-only HEAD~2 HEAD | sort</automated>
  </verify>
  <done>All 5 new tests pass; tests/options and the full suite are fully green with exact counts recorded; `git diff --name-only HEAD~2 HEAD` lists exactly bot/options/service.py and tests/options/test_service.py; grep of bot/options/service.py finds `options_entry_foreign_holding`, `options_entry_broker_read_failed`, `_OPTION_UNDERLYING_RE`, and the `ponytail:` coupling comment.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| OpenD broker → options bot | Position list from a SHARED paper account (another bot instance, the equity bot, or a human may hold option positions) |
| Options DB → entry decision | Own SQLite DB is not the whole truth: a second instance with a fresh DB is invisible to it |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-kvt-01 | Tampering | OptionsBot._try_open order placement | mitigate | Fresh `get_option_positions()` read per candidate before any DB row/executor call; skip if any non-zero option qty on the underlying (prevents netting another holder's legs to zero, the 2026-09-25 TLT incident) |
| T-kvt-02 | Denial of Service (safety) | broker read failure during entry | mitigate | Fail closed: exception → `options_entry_broker_read_failed` error log, return None, no entry; CancelledError re-raised |
| T-kvt-03 | Spoofing | underlying attribution of broker codes | mitigate | `_OPTION_UNDERLYING_RE` captures the full ticker before the 6-digit expiry so "US.TLTW..." is never attributed to "US.TLT" (test e) |
| T-kvt-04 | Tampering | residual race between the broker read and leg placement (another process opens in that window) | accept | Window is one candidate's place latency; reconcile still flags any resulting drift NEEDS_ATTENTION; ONE options-bot instance remains the operating rule |
</threat_model>

<verification>
- `python3 -m pytest -q tests/options` green (expected 193).
- `python3 -m pytest -q` green (expected 1157).
- Only bot/options/service.py and tests/options/test_service.py changed across the two commits.
</verification>

<success_criteria>
- A candidate whose underlying has any non-zero broker option holding is skipped with a structured `options_entry_foreign_holding` warning (codes + overlapping leg_codes), writing no rows and placing no orders.
- A broker read failure blocks the entry with `options_entry_broker_read_failed`.
- Holdings on other underlyings, including the bot's own open legs, do not block an entry.
- Test commit precedes fix commit (red → green), full suite green, exact counts reported.
</success_criteria>

<output>
Create `.planning/quick/260926-kvt-options-bot-block-entry-when-broker-hold/260926-kvt-SUMMARY.md` when done (do NOT commit it; the orchestrator does the docs commit).
</output>
