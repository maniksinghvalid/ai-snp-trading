---
phase: 11-multi-strategy-options-bot-bull-call-spread
verified: 2026-09-24T15:58:47Z
status: gaps_found
score: 8/9 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Bull-call and credit positions are never marked or closed against an invalid (missing/'N/A') quote — no naked-short exposure, no false daily-loss-breaker trip, no stuck CLOSING row"
    status: failed
    reason: "`_manage_position` only checks that a leg's code key is PRESENT in `quotes` (bot/options/service.py:901-903: `if any(leg[\"code\"] not in quotes for leg in legs)`). It never validates the quote VALUE. `strategy._as_float` (bot/options/strategy.py:365-378) silently converts the SDK's literal 'N/A' (seen live on untraded/thin strikes), None, '', and NaN to 0.0. Verified by source inspection (not just SUMMARY claim) — confirmed the exact code path the 11-REVIEW.md CR-01 finding describes is present, unmodified, on develop-bound HEAD. Consequences: (1) short-call-unquoted → mark overstates spread value → manage_decision_debit/manage_decision can fire an early exit; close_legs then either (a) raises ValueError on a literal 'N/A' buy-back price, which the per-position except in _run_manage_cycle swallows, leaving the row stuck in CLOSING (excluded from the manage loop and from BP headroom) with no alert, or (b) prices the buy-back at ~$0, it never fills, and the long leg is still sold — a NAKED SHORT CALL, breaking the defined-risk invariant the whole bot design (D-13/D-15, 'position for zero') depends on; (2) long-call-unquoted → unrealized loss is overstated by roughly the full debit+short-mid per position, which can trip the global $2,000 daily-loss breaker with no real loss, blocking BOTH strategies (tasty AND super_bull_call) for the rest of the day. Phase 11 makes this substantially more likely to trigger than it was before: it adds single-name S&P equity-option chains (thin, often-untraded strikes) to a manage loop that previously only ever quoted liquid ETF options. This is a pre-existing defect in bot/options/service.py, but the phase goal explicitly commits to running the bull-call book 'correctly and safely,' and this defect sits directly in the shared manage path both strategies now depend on."
    artifacts:
      - path: "bot/options/service.py"
        issue: "Lines ~901-903 (quote presence check) and ~915-936 (close_legs call + except swallow) do not validate quote value validity before marking or closing a position; no test exercises an 'N/A' or missing-value quote"
    missing:
      - "A _quote_ok() (or equivalent) check that rejects a leg whose bid/ask is missing, 'N/A', non-numeric, or crossed, causing _manage_position to skip that cycle (return 0.0, log a warning) instead of marking/closing on a synthetic $0 quote"
      - "A guard so that a close_legs exception never leaves a row silently stuck in CLOSING — fall through to the NEEDS_ATTENTION + alert branch on failure"
      - "A regression test that feeds a quote dict with an 'N/A' or missing bid/ask into manage and asserts no close is attempted and no false unrealized P&L is booked"
---

# Phase 11: Multi-strategy options bot (bull_call_spread) Verification Report

**Phase Goal:** `rules_options.json` defines multiple option strategies in a `strategies` array and the ONE options-bot process runs all of them concurrently: the existing `tasty_credit_spreads` credit book (behavior unchanged) plus a new `super_bull_call` debit book — a bull call spread (~30Δ long call, short call one width higher, ≤30% of width debit, no stop, full close at a % of max profit) sourced from "Super Bull Call Spread" (Options With Ravish), whose daily bullish universe is the equity bot's Trend Join Long premarket watchlist (read-only). Per-strategy sizing; global daily-loss breaker and BP cap.

**Verified:** 2026-09-24T15:58:47Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Method

Source inspection + offline pytest only, per SAFETY constraints (no OpenD, no live bot start, no touching `data/*.db`). Read all 6 PLAN/SUMMARY pairs, 11-CONTEXT.md (D-01..D-29), 11-VALIDATION.md, 11-REVIEW.md, REQUIREMENTS.md, ROADMAP.md Phase 11 block. Ran `python3 -m pytest -q` (full suite) and targeted subsets. Read `bot/options/{config,schema,strategy,service,store,universe}.py`, `bot/main.py`, `backtester/options_run.py`, `bot/state/migrations.py`, `rules_options.json`, and the docs/research provenance doc directly — every code claim below was confirmed by reading the actual file, not by trusting SUMMARY.md text.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria 1–8 + one derived safety truth)

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---------|--------|----------|
| 1 | `rules_options.json` ships in `strategies` shape with both strategies; `python3 -m bot --rules rules_options.json` registers one entry-scan job per strategy + one manage job + one EOD job | ✓ VERIFIED | `rules_options.json` has `strategies: [tasty_credit_spreads, super_bull_call]` (lines 2-88). `bot/options/service.py:482-521 _register_jobs` builds `options_entry_scan_<name>` per strategy (+ `_2` when `second_entry_scan_et` set), one `options_manage`, one `options_eod`. Test `test_shipped_book_registers_per_strategy_jobs` (tests/options/test_service.py:1226) and `test_register_jobs_one_entry_scan_per_strategy` (line 379) pass. |
| 2 | `load_options_config` unchanged flat contract; `tasty_credit_spreads` view equals pre-change config field-for-field; Phase 9 backtester/UAT probe/pre-existing tests pass unmodified | ✓ VERIFIED | `bot/options/config.py:488 load_options_config` delegates to `load_options_book` and returns one flat `OptionsConfig`. `tests/options/test_config.py:213 test_shipped_tasty_view_equals_pre_change_config_field_for_field` and `:338 test_legacy_and_book_default_strategy_equal_on_43_fields` both pass. `tests/backtester/options/*` (66 tests) pass with no call-site changes. |
| 3 | `backtester.options_run` accepts `--strategy NAME`; every documented `--set` arm command keeps working verbatim; a debit structure is rejected with a clear error | ✓ VERIFIED (with caveat, see WR-04 below) | `backtester/options_run.py:62 --strategy`, `:229 raw = legacy_view(raw, args.strategy)` before `apply_overrides`, `:241-248` rejects `bull_call_spread` with `[ERROR]` + exit 1. `tests/backtester/options/test_options_run.py` (19 tests) pass. Caveat: `legacy_view` (config.py:551-554) does unchecked `dict.pop()` on `raw["risk"]`/`raw["service"]` keys and raises a raw `KeyError` (not `ConfigError`) on a malformed strategies-shape file missing a relocated key — confirmed by reading the code; only affects a hand-edited malformed config, not the shipped file or any documented arm command. |
| 4 | Pure-function tests prove bull-call strike selection, the `max_debit_to_width` gate, `debit × 100` sizing, and `manage_decision_debit` sign math/ordering | ✓ VERIFIED | `bot/options/strategy.py:404 _pick_bull_call`, `:243 size_debit_position`, `:319 manage_decision_debit` read exactly per D-12..D-15. 19/19 targeted tests pass incl. `test_bull_call_nvda_worked_example`, `test_manage_decision_debit_sign_handled_once_with_mark_spread`, `test_manage_decision_debit_has_no_stop_loss`. |
| 5 | Equity watchlist read via read-only SQLite URI, capped at 20 by rank; missing/locked/empty → zero entries; no write path | ✓ VERIFIED (WARNING noted) | `bot/options/universe.py` — sole `sqlite3.connect` uses `?mode=ro` (line 51); `_WATCHLIST_CAP = 20`; every `sqlite3.Error` and empty-result path returns `[]` with a structured warning (lines 92-111); module imports nothing from `bot.state`. 11 tests in `tests/options/test_universe.py` pass. WARNING (11-REVIEW.md WR-03, confirmed by reading the query at line 87): `ORDER BY rank ASC LIMIT ?` has no tie-breaker, and the equity bot's intraday rescan (09:55+ ET) reuses rank numbers before the 10:05 bull-call scan runs, so the returned 20 codes/order are not deterministically "premarket rank" as D-17 intends. Does not violate the literal SC wording (rank ASC, capped, read-only, fail-closed) — flagged as a WARNING, not a truth failure. |
| 6 | Positions carry `strategy_name` (idempotent migration, legacy default); debit positions store negative `credit_per_spread`; close math correct for both kinds | ✓ VERIFIED | `bot/state/migrations.py:386 _migration_0007` is PRAGMA-guarded/idempotent, appended as MIGRATIONS[6] without touching 0001-0006, `CURRENT_VERSION = 7`. `bot/options/store.py` passes `strategy_name` through, omits None on insert (SQL default applies). `service.py:729 credit_per_spread = -debit` (D-19). Close math (`realized_per_spread = credit − net_exit`) is unchanged. Tests in `tests/options/test_store.py` and `tests/state/test_migrations.py` pass (63 tests). |
| 7 | Per-strategy caps (entries/day, concurrent) counted per strategy; daily-loss breaker, BP headroom, one-position-per-underlying are global | ⚠️ VERIFIED with WARNING | `service.py:632-642` computes `busy` (underlying set) and `open_max_loss_total` globally across `_ACTIVE_STATUSES`/`_OPEN_STATUSES`, and `open_count` filtered to `cfg.name`. Tests `test_bull_call_per_day_cap_is_per_strategy`, `test_bull_call_concurrent_cap_is_per_strategy`, `test_one_position_per_underlying_is_global`, `test_bp_headroom_is_global`, `test_daily_breaker_blocks_every_strategy` all pass — the tested scenarios are correct. WARNING (11-REVIEW.md WR-05, confirmed by reading lines 631-642): `open_max_loss_total`/`open_count` only sum `_OPEN_STATUSES = ("OPEN","OPENING")`, excluding `NEEDS_ATTENTION`/`CLOSING` rows that are still live broker exposure. D-29 (this phase) adds a new bulk path into `NEEDS_ATTENTION` at startup, so removing a strategy from config, or hitting the CR-01 stuck-CLOSING failure mode, silently drops that exposure from the "global" BP cap and lets new positions be sized on top of it. |
| 8 | Safety invariants unchanged (LIMIT only; longs-first open/shorts-first close; SAFE-OG-01 scope; own DB/kill/report dir; one instance; SIMULATE only); full suite green | ✓ VERIFIED | `bot/options/execution.py` (`LegExecutor`) is untouched per plan `files_modified` lists and 11-REVIEW.md confirms it is unchanged; `service.py` reconcile keeps SAFE-OG-01 scope (only this bot's `option_legs`, broker codes on no DB row are counted/logged only); `state_db`/`kill_file`/`report_dir` unchanged in `rules_options.json:110-112`. **Full suite: `python3 -m pytest -q` → 1265 passed, 1 skipped** (matches the documented after-baseline exactly; before-baseline was 1134/1). |
| 9 (derived — phase-goal safety, not a numbered SC) | Bull-call and credit positions are never marked or closed against an invalid/missing quote (no naked-short exposure, no false breaker trip, no stuck CLOSING row) | ✗ FAILED (BLOCKER) | See gaps section and CR-01 below. Confirmed by direct source reading, independent of 11-REVIEW.md's narrative. |

**Score:** 8/9 truths verified (1 BLOCKER: CR-01 quote-validity gap in the shared manage path)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bot/options/schema.py` | `STRATEGIES_SCHEMA` + `bull_call_spread` in structure enum | ✓ VERIFIED | Present; used by `load_options_book`. |
| `bot/options/config.py` | `OptionsConfig` (+6 fields), `OptionsBook`, `load_options_book`, `load_options_config(path, strategy=None)`, `legacy_view`, fail-closed checks | ✓ VERIFIED | All present at documented line ranges; D-11 fail-closed tests pass (`TestStrategiesShapeFailsClosed` in test_config.py). |
| `bot/options/strategy.py` | `_pick_bull_call`, `size_debit_position`, `_size_for_risk`, `manage_decision_debit` | ✓ VERIFIED | Present, D7 purity intact (no I/O/clock/logging added). |
| `bot/options/universe.py` | `read_equity_watchlist`, `_connect_ro` | ✓ VERIFIED | Present; no `bot.state` import; read-only URI confirmed. |
| `bot/options/store.py` | `strategy_name` pass-through, `count_opened_on(date_iso, strategy_name=None)` | ✓ VERIFIED | Present at documented lines. |
| `bot/state/migrations.py` | `_migration_0007` | ✓ VERIFIED | Present, idempotent, `CURRENT_VERSION=7`. |
| `bot/options/service.py` | per-strategy jobs, debit entry/manage, D-29 guard, book-based `main()` | ✓ VERIFIED (with the CR-01/WR-05 gaps noted above) | All wiring present and exercised by tests; the two gaps are logic bugs within otherwise-present/wired code, not missing wiring. |
| `backtester/options_run.py` | `--strategy`, `legacy_view` before `--set`, debit rejection | ✓ VERIFIED | Present; caveat WR-04 on unchecked `KeyError` for malformed input. |
| `rules_options.json` | strategies shape, `super_bull_call` block with D-16 values | ✓ VERIFIED | Matches D-16 exactly (long_delta 0.30, wing_width_pct_of_underlying 4.5, min_wing_width_usd 2.0, max_debit_to_width 0.30, target/min/max_dte 30/21/45, entry_scan_et 10:05, sizing 1.0/4/2, manage 60/null/1). |
| `docs/research/2026-09-24-super-bull-call-spread.md` | provenance + deviations (D-27) | ✓ VERIFIED | Source URL, channel, retrieval method, distilled rules table, deviations section present. |
| `bot/main.py` | dispatch widened to `"strategies" in data` | ✓ VERIFIED | Line ~80: `data.get("strategy_name","")=="tasty_credit_spreads" or "strategies" in data` routes to options service. Minor INFO gap: `data.get(...)` assumes a dict — a non-dict top-level JSON crashes uncaught rather than `[ERROR]`/exit 1 (11-REVIEW.md IN-03, confirmed by reading the line) — not a phase-goal blocker. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `config.py load_options_book` | `schema.py STRATEGIES_SCHEMA` | `_validate` dispatch on `'strategies' in data` | ✓ WIRED | Confirmed by reading `load_options_book`. |
| `backtester/options_run.py main` | `config.py legacy_view` | `raw = legacy_view(raw, args.strategy)` before `apply_overrides` | ✓ WIRED | Confirmed at line 229. |
| `bot/main.py main` | `bot/options/service.py main` | dispatch on strategies-shape or legacy strategy_name | ✓ WIRED | Confirmed at line ~80-84. |
| `service.py _scan_and_open` | `universe.py read_equity_watchlist` | `universe_source == "equity_watchlist"` branch | ✓ WIRED | Confirmed via grep + test `test_bull_call_entry_opens_debit_spread_from_watchlist`. |
| `service.py _manage_position` | `strategy.py manage_decision_debit` | `is_debit` branch, `-credit` sign flip at the one point (D-19) | ✓ WIRED | Confirmed at lines ~908-911. |
| `service.py reconcile(startup=True)` | NEEDS_ATTENTION + `options_unknown_strategy` audit | D-29 check before OPENING/CLOSING branch | ✓ WIRED | Confirmed at lines 389-410; test `test_reconcile_startup_flags_unknown_strategy` passes. |
| `service.py _manage_position` quote gate | leg quote validity | presence-only check, no value validation | ✗ NOT_WIRED (gap) | See CR-01 — the "wiring" exists (quotes dict is built and passed) but the correctness check it needs (value validity, not just key presence) is missing. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full suite regression | `python3 -m pytest -q` | 1265 passed, 1 skipped in 69.35s | ✓ PASS (matches documented after-baseline exactly) |
| Options/backtester/migration subset | `python3 -m pytest -q tests/options tests/backtester/options tests/state/test_migrations.py` | 440 passed | ✓ PASS |
| Bull-call pure-function tests | `python3 -m pytest -q tests/options/test_strategy.py -k "bull_call or manage_decision_debit or nvda"` | 19 passed | ✓ PASS |
| NVDA worked example (D-13/D-19 numbers) | inspected `test_bull_call_nvda_worked_example`, `manage_decision_debit` math | debit 1.96, width 10, 0.196 ≤ 0.30 passes; realized math unchanged | ✓ PASS |

Live-bot behavioral checks (job registration log lines, actual chain screening) were **not** run — SAFETY constraint forbids starting the bot or connecting to OpenD. See Human Verification below.

### Probe Execution

No `scripts/*/tests/probe-*.sh` conventional probes exist for this phase; `scripts/uat_options_probe.py` is explicitly a manual/live-only tool (RTH, OpenD required) and is out of scope for automated verification per the phase's own SAFETY constraint. Step 7c: SKIPPED (no offline-runnable probes; the one probe script requires OpenD + RTH).

### Requirements Coverage

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|-----------------|--------|----------|
| MSO-01 | 11-01 | ✓ SATISFIED | `strategies` array, `load_options_book`, legacy shape still loads — verified above. |
| MSO-02 | 11-01, 11-04 | ✓ SATISFIED | `load_options_config` flat contract + `--strategy`/`legacy_view` — verified above. |
| MSO-03 | 11-01 | ✓ SATISFIED | Fail-closed `ConfigError` tests pass (`TestStrategiesShapeFailsClosed`). |
| MSO-04 | 11-02, 11-05 | ✓ SATISFIED | `_pick_bull_call`, debit gate, `size_debit_position` — verified above. |
| MSO-05 | 11-02, 11-06 | ✓ SATISFIED (manage-loop quote-validity gap noted separately, see truth #9) | `manage_decision_debit` order/sign — verified above; the defect is in the caller's quote-gating, not in this pure function. |
| MSO-06 | 11-03, 11-05 | ✓ SATISFIED (WR-03 ordering caveat) | `read_equity_watchlist` — verified above. |
| MSO-07 | 11-03, 11-06 | ✓ SATISFIED | Migration + signed premium + close math — verified above. |
| MSO-08 | 11-05, 11-06 | ⚠️ SATISFIED WITH GAPS (CR-01, WR-05) | Per-strategy jobs/dispatch/caps wired and tested; global BP/breaker correctness has the two gaps above in edge cases the phase itself makes materially more likely. |
| MSO-09 | 11-02, 11-04, 11-06 | ✓ SATISFIED | Shipped file conversion + D-26 no-drift test + provenance doc — verified above. |

No orphaned requirements: all 9 MSO-01..09 IDs appear in at least one plan's `requirements:` frontmatter and in REQUIREMENTS.md § Multi-Strategy Options, and REQUIREMENTS.md's traceability table marks all 9 "Phase 11 / Complete."

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `bot/options/service.py` | ~901-936 | Quote-presence-only check before marking/closing a spread; exception on close swallowed without alert (CR-01) | 🛑 Blocker | Naked-short risk, false breaker trips, silently stuck CLOSING rows — see gap above. |
| `bot/options/service.py` | 631-642 | `open_max_loss_total`/`open_count` exclude `NEEDS_ATTENTION`/`CLOSING` rows (WR-05) | ⚠️ Warning | Global BP cap and per-strategy concurrent cap can undercount live broker exposure once a position is stuck outside `_OPEN_STATUSES`. |
| `bot/options/service.py` | 720-745 | Entry premium/`max_loss_usd` recorded at pre-trade mid, not actual fill (WR-02, pre-existing, also affects the new debit path) | ⚠️ Warning | 1/4-rule gate, BP cap and realized P&L can drift from the actual fill by the executor's escalation slippage. |
| `bot/options/universe.py` | 87 | `ORDER BY rank ASC LIMIT ?` has no tie-breaker; intraday rescans reuse ranks (WR-03) | ⚠️ Warning | Watchlist selection/order is not deterministically "premarket rank" as D-17 intends. |
| `bot/options/config.py` | 551-554 | `legacy_view` does unchecked `dict.pop()`/indexing before validation (WR-04) | ⚠️ Warning | A malformed strategies-shape file crashes `options_run` with a raw `KeyError`/`TypeError` instead of `[ERROR]`+exit 1; also lets a per-strategy global-knob override silently diverge backtest from live config. |
| `bot/options/service.py` | 271 | EOD HTML "Credit" column shows raw signed value for debit rows (IN-01) | ℹ️ Info | Cosmetic only — Telegram lines already use `_premium_label`. |
| `bot/options/store.py` | 69 | `insert_option_position` silently defaults an unset `strategy_name` to `tasty_credit_spreads` (IN-02) | ℹ️ Info | Only a latent risk for a future caller that forgets the field; no current caller does. |
| `bot/main.py` | 80 | `data.get(...)` assumes dict top level (IN-03) | ℹ️ Info | Non-dict JSON top level crashes uncaught instead of `[ERROR]`+exit 1. |
| `bot/options/config.py` / `universe.py` | — | `equity_state_db` ignores `BOT_STATE_DB` env override the equity bot honours (IN-04) | ℹ️ Info | Could silently read a stale/different DB if the equity bot uses the override. |
| `bot/options/service.py` | 586-589 | Blocking sqlite read (up to 5s busy timeout) on the asyncio loop (IN-06) | ℹ️ Info | Marked `ponytail:` — deliberate, but the timeout is longer than the comment's stated ceiling. |

No unresolved `TBD`/`FIXME`/`XXX` debt markers found in the phase's modified files.

### Human Verification Required

#### 1. Live paper run with both books registered and the bull-call scan reading the real watchlist

**Test:** During RTH after the equity bot's premarket scan has persisted `daily_scan` rows for the day, start `PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json`.
**Expected:** Startup log shows `options_jobs_registered` listing `options_entry_scan_tasty_credit_spreads`, `options_entry_scan_tasty_credit_spreads_2`, `options_entry_scan_super_bull_call`, `options_manage`, `options_eod`; at 10:05 ET a log line shows the watchlist read returning a non-empty code count (or a structured `options_watchlist_empty`/`options_watchlist_unavailable` warning if the equity scan hasn't run).
**Why human:** Requires OpenD logged in, RTH, a real populated equity watchlist, and a running live process — this verification agent is prohibited from starting the bot or connecting to OpenD (SAFETY constraint) and this is also explicitly listed as the sole Manual-Only Verification in 11-VALIDATION.md.

### Gaps Summary

Eight of the nine functional truths behind the phase goal are solidly verified against the actual code (not just SUMMARY.md claims): the loader split, the pure bull-call strike/gate/sizing/manage math (including the literal NVDA worked example), the read-only watchlist reader, the strategy-tagged/signed-premium migration, per-strategy job registration and caps, D-29's unknown-strategy guard, and the shipped-config no-drift proof are all present, wired, and covered by passing tests (1265 passed, 1 skipped — exact match to the documented after-baseline).

The one BLOCKER gap is in the shared manage loop that both the existing `tasty_credit_spreads` book and the new `super_bull_call` book now depend on: `_manage_position` only checks that a leg's quote key is *present*, never that its bid/ask are numerically valid. Because `_as_float` treats the SDK's literal `'N/A'` (returned for untraded/thin strikes) as `0.0`, a single unquoted leg can (a) trigger a false early exit that then either strands the position in `CLOSING` with a swallowed exception and no alert, or worse, leaves a **naked short call** when the buy-back never fills but the long leg is still sold — breaking the defined-risk invariant this whole design (D-13/D-15, "position for zero") depends on; or (b) overstate unrealized loss enough to trip the shared `$2,000` daily-loss breaker with no real loss, blocking *both* strategies for the rest of the day. This was a pre-existing defect in the credit path, but Phase 11 adds single-name equity option chains — thinner and far more likely to have untraded strikes than the existing ETF chains — directly into the same manage loop, materially raising the odds of hitting it. A related WARNING (global BP/concurrent-cap headroom excludes `NEEDS_ATTENTION`/`CLOSING` rows, which D-29 now populates through a new bulk path) compounds the same failure mode. Both were independently confirmed by reading `bot/options/service.py` and `bot/options/strategy.py` directly, not by trusting 11-REVIEW.md's narrative alone.

This looks like an unintentional gap (11-REVIEW.md itself flags it as the phase's one Critical finding, with a concrete fix), not an accepted deviation — no override is suggested.

---

_Verified: 2026-09-24T15:58:47Z_
_Verifier: Claude (gsd-verifier)_
