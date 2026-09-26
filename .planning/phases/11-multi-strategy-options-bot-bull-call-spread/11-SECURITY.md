---
phase: 11
slug: multi-strategy-options-bot-bull-call-spread
status: verified
threats_open: 0
asvs_level: 1
created: 2026-09-26
---

# Phase 11 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
>
> Register built from the `<threat_model>` blocks of 11-01-PLAN.md .. 11-09-PLAN.md
> plus the in-scope quick task 260925-goi-fix-cr-04-fill-leg-ttl-cancel-swallow
> (`260925-goi-PLAN.md`). 67 unique threat IDs (T-11-01..T-11-67) after merging
> duplicate appearances across plans; two IDs (T-11-38, T-11-46) carry an explicit
> "(CORRECTED)" re-disposition from 11-09 that supersedes 11-08's original entry,
> and T-11-58 is split — its EX-03 half was re-dispositioned from accept to
> mitigate by the quick task, its WR-09 half remains accept. Every row below was
> verified against the current code (grep + direct read), not against plan prose.
> `python3 -m pytest -q` re-run during this audit: **1350 passed, 1 skipped**
> (matches the quick task's documented phase-gate count).

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|----------------|
| operator-edited rules_options.json → loader | Untrusted-by-construction config file; every value becomes live-trading risk parameters | strategy params, sizing, DB paths |
| loader → consumers (service, backtester, probe) | Consumers trust OptionsConfig to be semantically valid | flat OptionsConfig |
| broker option chain rows → pure strategy core | Quote fields may be missing, 'N/A', crossed or stale | option chain rows |
| strategy core → order execution (via service) | The legs/qty returned here become real (paper) LIMIT orders | legs, qty |
| options process → equity bot's SQLite DB (data/bot_state.db) | Cross-process read of another bot's live state; the options bot must never write there | daily_scan codes |
| equity daily_scan codes → options entry scan | Codes produced by another process become screen inputs for paper orders | equity codes |
| options DB schema migration | Shared runner migrates both DB files | schema DDL |
| rules file → bot/main.py dispatch | Decides which composition root (equity vs options) acts on the file | top-level JSON shape |
| rules file → backtester CLI | Operator `--strategy`/`--set` input shapes the effective config | CLI args |
| options DB rows → manage loop | Stored strategy_name/structure decide which parameters/decision function act on real (paper) positions | position rows |
| options DB rows + config → startup reconcile | A book that no longer contains a strategy must not leave its positions unmanaged or mis-managed | position rows |
| broker snapshot → manage loop | Untrusted quote values ('N/A', None, '', NaN, 0/0, crossed) decide marks, exits, escalations, breaker P&L | quote snapshot |
| manage loop / entry unwind → LegExecutor | Quotes set LIMIT prices of close orders; per-leg fill outcome decides whether protective wings are sold | order prices |
| LegExecutor → broker (place/poll/cancel) | An order placed on the broker outlives any exception in the bot's await chain unless the bot cancels it | order_id, cancel confirmation |
| service → operator (Telegram / audit) | The operator's only signal that legs need a manual close; a false "unwound" message is the failure mode | alert text, audit events |
| APScheduler (max_instances=1) → manage cycles | Fires can be dropped while a slow cycle runs; safety must not depend on any single fire | scheduled job cycles |
| position/config strings → Telegram alert | Underlying, strategy name, option codes, reason tokens interpolated into HTML alert bodies | HTML alert text |
| service → store row status | ABORTED/CLOSED rows leave reconcile's scope; a wrong terminal status hides live exposure | row status |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-11-01 | Tampering | config.py equity_state_db check; universe.py read-only connect; service.py sole call site | mitigate | `config.py:468-475` `ConfigError` when `abspath(equity_state_db) == abspath(state_db)`; `universe.py:42-51` `_connect_ro` opens `mode=ro` URI only, module imports nothing from `bot.state`; `service.py:702` is the only `read_equity_watchlist` call site. Tests: `test_universe.py::test_connection_is_read_only`, `::test_module_does_not_import_equity_state`, `::test_locked_db_returns_empty` | closed |
| T-11-02 | Tampering | config.py `_check_strategy` relocation guard; shipped rules_options.json | mitigate | `config.py:300-305` rejects a relocated global knob set inside a strategy block; shipped `rules_options.json` verified in strategies shape with both strategies (`tasty_credit_spreads`, `super_bull_call`) via direct read. Test: `test_config.py::TestShippedRulesOptionsJson` | closed |
| T-11-03 | Tampering | bot/main.py dispatch; service.py `main()` | mitigate | `bot/main.py:80` dispatch condition `data.get("strategy_name","")=="tasty_credit_spreads" or "strategies" in data`; `service.py:1487-1495` `load_options_book` raises `ConfigError` → `[ERROR]` + `sys.exit(1)` before `MoomooGateway(...)` is constructed. Test: `test_service.py::test_main_builds_one_bot_for_every_strategy` | closed |
| T-11-04 | Tampering / Repudiation | migrations.py 0007; store.py insert; service.py D-29 reconcile + `_manage_position` | mitigate | `migrations.py:382` `strategy_name TEXT NOT NULL DEFAULT 'tasty_credit_spreads'`; `store.py` omits None fields from INSERT (SQL default applies); `service.py:480-522` unknown-strategy → `NEEDS_ATTENTION` + alert + `options_unknown_strategy` audit; `service.py:1141-1147` `_manage_position` returns 0.0 for an unknown strategy. Tests: `test_store.py::test_insert_without_strategy_name_defaults_to_tasty_credit_spreads`, `test_service.py::test_reconcile_startup_flags_unknown_strategy`, `::test_manage_skips_position_with_unknown_strategy` | closed |
| T-11-05 | Tampering | config.py `_IMPLEMENTED_STRUCTURES` guard | mitigate | `config.py:47,321-326` guard read at call time (module-level constant, monkeypatchable), independent of the schema enum. Test class: `test_config.py::TestStrategiesShapeFailsClosed` (39 tests selected by `-k fail`, collected and confirmed) | closed |
| T-11-06 | Tampering | config.py `_CREDIT_KEYS`/`_DEBIT_KEYS` cross-checks | mitigate | `config.py:328-353` rejects credit-only keys on a debit structure and debit-only keys on a credit structure | closed |
| T-11-07 | Denial of Service | config.py `load_options_config` strategy selection | mitigate | `config.py:503-511` unknown strategy name raises `ConfigError` rather than silently returning `book.strategies[0]` | closed |
| T-11-08 | Tampering | strategy.py `_pick_bull_call` debit gate | mitigate | `strategy.py:437` `0 < debit <= cfg.max_debit_to_width * width`; illiquid/garbage quotes coerce to 0.0 via `_as_float`. Tests: `test_strategy.py` bull-call debit-gate cases (file confirmed present) | closed |
| T-11-09 | Tampering | strategy.py leg ordering | mitigate | `strategy.py:441` returns `[BUY long, SELL short]` — BUY first | closed |
| T-11-10 | Denial of Service (capital) | strategy.py `size_debit_position`/`_size_for_risk` | mitigate | `strategy.py:254-269` floors on `debit*100` risk plus global BP headroom cap, never negative | closed |
| T-11-11 | Tampering | strategy.py `manage_decision_debit` sign | mitigate | `strategy.py:319-358` the `-mark - debit` flip lives in exactly one function; `mark_spread` itself is unchanged | closed |
| T-11-12 | Information Disclosure | docs/research provenance doc | accept | See Accepted Risks Log | accepted |
| T-11-13 | Denial of Service | universe.py `read_equity_watchlist` | mitigate | `universe.py:54-102` `timeout_s=5.0` default, catches `sqlite3.Error` → `[]`. Tests: `test_universe.py::test_locked_db_returns_empty`, `::test_corrupt_file_returns_empty`, `::test_missing_table_returns_empty` | closed |
| T-11-14 | Tampering | watchlist codes as screen input | accept | See Accepted Risks Log | accepted |
| T-11-15 | Tampering | migration 0007 on the equity DB | accept | See Accepted Risks Log | accepted |
| T-11-16 | Information Disclosure | warning logs include db_path | accept | See Accepted Risks Log | accepted |
| T-11-17 | Tampering | backtester/options_run.py debit-structure rejection | mitigate | `options_run.py:242-249` explicit `[ERROR]` + `return 1` for `structure.type == "bull_call_spread"`, before the temp-file validation and before any run dir is created | closed |
| T-11-18 | Denial of Service | backtester/options_run.py `--strategy` typo handling | mitigate | `options_run.py:228-232` `legacy_view` raises `ConfigError` for an unknown name → `[ERROR]` + `return 1` | closed |
| T-11-19 | Elevation of Privilege (capital) | service.py per-strategy vs global caps | mitigate | `service.py:676-682` (per-strategy daily cap), `744-756` (global busy/BP over `_ACTIVE_STATUSES`), `773-776` (per-strategy concurrent cap). Tests: `test_service.py::test_bull_call_per_day_cap_is_per_strategy`, `::test_bull_call_concurrent_cap_is_per_strategy`, `::test_daily_breaker_blocks_every_strategy` | closed |
| T-11-20 | Tampering | bull-call entry skips the IV gate | accept | See Accepted Risks Log | accepted |
| T-11-21 | Tampering (HTML injection) | alert HTML via `_esc` | mitigate | `service.py:232` `_esc` (html.escape); confirmed applied to every interpolated value at all `alerter.send(...)` call sites read (reconcile, entry, manage, EOD). Test: `test_service.py::test_formatters_escape_strategy_name` | closed |
| T-11-22 | Denial of Service | 20-name watchlist → 20 screen requests | accept | See Accepted Risks Log | accepted |
| T-11-23 | Tampering | service.py `_manage_position` decision dispatch | mitigate | `service.py:1149-1222` dispatches on `pos["strategy_name"]`/`pos["structure"]`, never `self._cfg`. Test: `test_service.py::test_manage_dispatches_each_position_to_its_own_strategy` | closed |
| T-11-24 | Tampering | service.py realized P&L for debit positions | mitigate | `service.py:1260-1276` unchanged close math with D-19 signed premium (`credit = -debit`); `max_profit = width + credit` (never a negative credit). Test: `test_service.py::test_manage_closes_bull_call_at_profit_target_of_max` | closed |
| T-11-25 | Elevation of Privilege | service.py composition root | mitigate | `service.py:373-391` ONE `OptionsBot`, one `gateway`, one `store`, one `kill_switch`, one `LegExecutor`; `main()` opens exactly one `OptionsStore(cfg.state_db)`; `bot/gateway/gateway.py` SIMULATE-default guard unchanged. Test: `test_service.py::test_main_builds_one_bot_for_every_strategy` | closed |
| T-11-26 | Tampering | service.py `_manage_position` quote gate | mitigate | `service.py:154-193` `_quote_ok`/`_quote_markable`; `1160-1162` gates every leg before `mark_spread`, decision or breaker P&L | closed |
| T-11-27 | Elevation of Privilege (defined-risk invariant) | short buy-back priced from a synthetic quote | mitigate | `service.py:1162-1212` returns 0.0 before `mark_spread`/`close_legs` when any leg fails the gate — `close_legs` is never reached on a bad quote | closed |
| T-11-28 | Repudiation | `close_legs` exception leaves a row silently CLOSING | mitigate | `service.py:1236-1245` try/except around `close_legs` → `options_close_error` + `ok=False` → NEEDS_ATTENTION branch; `CancelledError` re-raised | closed |
| T-11-29 | Tampering | structure-kind mismatch (position vs strategy) | mitigate | `service.py:486-489,501-507` (startup reconcile) and `1149-1156` (`_manage_position`, before any decision). Tests: `test_service.py::test_reconcile_startup_flags_structure_mismatch`, `::test_manage_skips_structure_mismatch` | closed |
| T-11-30 | Denial of Service (risk-limit bypass) | BP headroom / concurrent-cap undercount | mitigate | `service.py:744-756` sums/counts over every `_ACTIVE_STATUSES` row (`OPENING, OPEN, CLOSING, NEEDS_ATTENTION`) | closed |
| T-11-31 | Denial of Service | unquotable leg near expiry left unmanaged | mitigate | `service.py:1159-1212` (Q-01 window) + `1291-1318` (`_warn_expiry_unmanaged`) | closed |
| T-11-32 | Denial of Service | resolved NEEDS_ATTENTION row still consumes BP/slot | accept | See Accepted Risks Log | accepted |
| T-11-33 | Tampering (HTML injection) | new alert texts (structure mismatch, unquotable near expiry) | mitigate | Every value in the alerts at `service.py:496-506, 1189-1194` passes through `_esc` | closed |
| T-11-34 | Elevation of Privilege (defined-risk invariant) | `close_legs` sells a long after a short buy-back failed | mitigate | `execution.py:369-375` any short-close failure blocks every subsequent long via `close_longs_skipped_short_open` + `return False`. Test present in `test_execution.py` (asserted at l.641/674) | closed |
| T-11-35 | Repudiation | `open_position` discards `close_legs`' result | accept | See Accepted Risks Log (superseded in practice by T-11-36/37/63, which route the same failure to NEEDS_ATTENTION — original 11-07 scope excluded fixing it) | accepted |
| T-11-36 | Repudiation (false operator alert) | `_try_open` reports a failed unwind as clean | mitigate | `service.py:935-955` `open_position` returning `False` → "UNWIND INCOMPLETE" alert + `options_entry_unwind_incomplete` audit. Test: `test_service.py::test_incomplete_unwind_alert_warns_about_working_orders` | closed |
| T-11-37 | Denial of Service (risk-limit bypass) | stranded legs invisible to BP/busy/cap | mitigate | `NEEDS_ATTENTION` is in `_ACTIVE_STATUSES` (`service.py:92`); `_try_open` `return pos` at l.963 so the current scan counts it immediately | closed |
| T-11-38 (CORRECTED by 11-09) | Repudiation / Tampering | exception inside the entry unwind | mitigate | Re-dispositioned in 11-09: `execution.py:185-223` `except BaseException` → shielded `cancel_order` → re-raise (CR-03); alerts carry `_WORKING_ORDERS_HINT` (see T-11-51/52) | closed |
| T-11-39 | Tampering (operator tool) | UAT probe treats `False` as a filled open | mitigate | `scripts/uat_options_probe.py:209-213` explicit `if filled is False:` → `NEEDS_ATTENTION` + operator message | closed |
| T-11-40 | Elevation of Privilege (SAFE-OG-01) | a later cycle acts on stranded codes | mitigate | `service.py:470` reconcile scope is `("OPEN","OPENING","CLOSING")` at startup / `("OPEN",)` steady-state; `1011` manage loop queries `("OPEN",)` only — never places orders on a NEEDS_ATTENTION/ABORTED row | closed |
| T-11-41 | Denial of Service | one transient bad cycle escalates / cancels automated close | mitigate | `service.py:1166-1209` per-position streak, escalates at `_QUOTE_MISS_ESCALATE_CYCLES=3` (l.104) or on the expiry session's final cycle | closed |
| T-11-42 | Denial of Service | failed snapshot chunk escalates every position at once | mitigate | `service.py:1070-1082` per-position outage skip (`options_manage_snapshot_outage`), never counted toward the streak | closed |
| T-11-43 | Tampering | one-sided/wide quote fabricates a mid | mitigate | `service.py:179-193` `_quote_markable` (`ask-bid <= max(0.5*mid, $0.10)`) | closed |
| T-11-44 | Denial of Service | strict gate blocks a legitimate assignment-guard close | mitigate | `service.py:1159-1162,1238` inside the window the gate is `_quote_ok`; assignment_guard close is `aggressive=True` independent of mark | closed |
| T-11-45 | Denial of Service (BP under-count) | incomplete-unwind row's `max_loss_usd` can be stale | accept | See Accepted Risks Log | accepted |
| T-11-46 (CORRECTED by 11-09) | Denial of Service | whole-session snapshot outage never triggers escalation | mitigate | Re-dispositioned in 11-09 (11-08's "OpenDWatchdog covers it" claim shown false): `service.py:1032-1066` process-level `_snapshot_outage_cycles` alert at 3 consecutive cycles, re-arms on a clean cycle | closed |
| T-11-47 | Denial of Service | missing quote blocks decisions outside the window | accept | See Accepted Risks Log | accepted |
| T-11-48 | Tampering (pricing) | in-window aggressive close at a wide posted bid | accept | See Accepted Risks Log | accepted |
| T-11-49 | Denial of Service | NEEDS_ATTENTION rows permanently consume BP/slots | accept | See Accepted Risks Log | accepted |
| T-11-50 | Tampering (HTML injection) | new alert texts (unwind incomplete, streak) | mitigate | Every interpolated value passes through `_esc`, confirmed at `service.py:925-928, 942-945` | closed |
| T-11-51 | Tampering (naked short from bot's own resting order) | `LegExecutor.fill_leg` exit paths after `place_order` | mitigate | `execution.py:185-223` per-attempt try; `except GeneratorExit: raise`; `except BaseException:` → `asyncio.shield(cancel_order)` → re-raise (never a return value) | closed |
| T-11-52 | Repudiation (hand-off without working-order warning) | UNWIND INCOMPLETE / close-incomplete / restart alerts | mitigate | `_WORKING_ORDERS_HINT` (`service.py:97`) used at 3 alert sites (l.530, 945, 1252) plus the definition — confirmed by direct grep (4 occurrences total) | closed |
| T-11-53 | Denial of Service (silent expiry) | `_manage_position` retry / outage branch at dte<=0 | mitigate | `service.py:1291-1318` `_warn_expiry_unmanaged`, called from l.1081 (outage) and l.1185 (no-quote), gated `dte<=0`, one-time via `self._expiry_warned` set (l.406) | closed |
| T-11-54 | Denial of Service / false assurance | `_manage_once` snapshot loop | mitigate | `service.py:1040-1066` `self._snapshot_outage_cycles` alerts once at 3 consecutive cycles, resets on a clean cycle | closed |
| T-11-55 | Denial of Service (premature hand-off) | near-expiry streak carries overnight | mitigate | `service.py:1166-1169` `(today, count)` keyed on `now_et().date()` | closed |
| T-11-56 | Tampering (residual resting order) | shielded `cancel_order` itself fails, or kill-switch closes gateway before job cancel | accept | See Accepted Risks Log | accepted |
| T-11-57 | Denial of Service | process restarts inside the last manage interval before cutoff | accept | See Accepted Risks Log | accepted |
| T-11-58 | Denial of Service (split) | EX-03 (fill_leg exception while OPENING) / WR-09 (unresolvable NEEDS_ATTENTION) | mitigate (EX-03 half, re-dispositioned by quick task T-11-63) / accept (WR-09 half) | EX-03: `service.py:895-916` `_try_open`'s `except Exception` around `open_position` (see T-11-63). WR-09: still no resolve tool — see Accepted Risks Log | closed (EX-03) / accepted (WR-09) |
| T-11-59 | Denial of Service | manage-cycle exception before/around snapshot not counted by WR-11 | accept | See Accepted Risks Log | accepted |
| T-11-60 | Denial of Service (alert flood) | new alerts | mitigate | Expiry warning: once per position (`self._expiry_warned`, l.406); outage alert: once per episode (`==`, l.1044) | closed |
| T-11-61 | Tampering (HTML injection) | new/changed alert texts | mitigate | `_esc` on every interpolated value; `_WORKING_ORDERS_HINT` is a constant literal (no injection surface) | closed |
| T-11-62 | Tampering (naked short from stale order) | `fill_leg` TTL path: failed cancel, then next attempt or partial-as-final (CR-04) | mitigate | `execution.py:141-169` raises `RuntimeError` chained from the gateway error whenever the TTL cancel raised and `dealt_qty < qty`; raise sits inside the per-attempt try so the T-11-51 shielded retry runs. Tests: `test_execution.py::test_ttl_cancel_failure_raises_and_places_no_next_attempt`, `::test_open_position_short_leg_ttl_cancel_failure_raises_never_clean_unwind`, `::test_unwind_ttl_cancel_failure_returns_false_not_none` | closed |
| T-11-63 | Repudiation / Denial of Service (silent OPENING row) | `_try_open` when `open_position` raises (EX-03) | mitigate | `service.py:900-916` `except asyncio.CancelledError: raise` then `except Exception:` → `options_entry_open_error` (exc_info) + `filled=False`, reaching the CR-02 NEEDS_ATTENTION branch. Test: `test_service.py::test_entry_scan_open_position_error_flags_needs_attention` | closed |
| T-11-64 | Denial of Service (false alarm) | TTL cancel raises for an order the broker already ended | accept | See Accepted Risks Log | accepted |
| T-11-65 | Tampering (residual working order) | both the TTL cancel and shielded retry fail | accept | See Accepted Risks Log | accepted |
| T-11-66 | Tampering | EX-03 leaves already-filled opening legs open, no auto-unwind | accept | See Accepted Risks Log | accepted |
| T-11-67 | Tampering (HTML injection) | alert text (quick task) | mitigate | No new alert text added; reused CR-02 alert already passes every value through `_esc`; new log event is structured logging | closed |

*Status: open · closed · accepted*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|--------------|------|
| AR-01 | T-11-12 | Provenance doc (`docs/research/2026-09-24-super-bull-call-spread.md`) contains only public video metadata (title, channel, URL, publish date) and paraphrased rules — no transcript, no credentials. | Plan 11-02 (operator scope) | 2026-09-24 |
| AR-02 | T-11-14 | Watchlist codes are only passed to `get_stock_ids`/`screen_options`; broker-side resolution rejects unknown codes; same-machine, same-operator process; values are bound parameters, never interpolated into SQL. | Plan 11-03 | 2026-09-24 |
| AR-03 | T-11-15 | `_migration_0007` adds one unused column to the equity DB's already-unused `option_positions` table on its next `StateStore.open()` (same pattern as migration 0006); no equity-owned table is touched. | Plan 11-03 | 2026-09-24 |
| AR-04 | T-11-16 | `db_path` in warning logs is a local file path in local logs only; no secrets. | Plan 11-03 | 2026-09-24 |
| AR-05 | T-11-20 | By design (D-05): the bull-call entry gate is watchlist membership + debit/liquidity/DTE gates, not an IV filter; risk is capped by `size_debit_position`'s defined-risk sizing regardless. | Plan 11-05 | 2026-09-24 |
| AR-06 | T-11-22 | The gateway already throttles per underlying (1000-row cap + 3.5s throttle, Phase 8); misfire grace 300s; the entry lock serialises with the tasty scan. | Plan 11-05 | 2026-09-24 |
| AR-07 | T-11-32 | Deliberate fail-closed direction: over-counting a resolved NEEDS_ATTENTION row blocks new entries (safe); under-counting (the WR-05 bug class) would over-lever the account (unsafe). Operator resolves by clearing the row (WR-09, deferred, is the resolve-tool backlog item). | Plan 11-07 | 2026-09-25 |
| AR-08 | T-11-35 | Out of 11-07's approved scope (fixing it needs an `open_position` return-contract change plus a service change). In practice superseded: 11-08's CR-02 and the quick task's EX-03 route the same underlying failure mode (a discarded/False unwind result) to NEEDS_ATTENTION instead of a silent ABORTED. Recorded as backlog EX-02. | Plan 11-07 | 2026-09-25 |
| AR-09 | T-11-45 | Bounded, defined-risk: EX-01 (T-11-34) keeps every long wing, so the stranded remainder can exceed the recorded `max_loss_usd` by at most about one leg premium x qty x 100. Exact re-sizing needs a second execution-contract change and a store-writer change (store frozen for 11-08). Same class as WR-02/WR-08 (deferred). | Plan 11-08 | 2026-09-25 |
| AR-10 | T-11-47 | Deliberate trade-off: a stricter gate would fire a spurious exit (false accept) outside the assignment-guard window. Logged every cycle as `options_manage_missing_quote`. The breaker-blindness half is IN-07 (deferred, tracked in code review). | Plan 11-08 | 2026-09-25 |
| AR-11 | T-11-48 | Operator decision: inside the assignment-guard window, closing at any valid price beats pin/assignment risk. This pricing behavior is pre-existing Phase 8 code, unchanged by Phase 11. | Plan 11-08 | 2026-09-25 |
| AR-12 | T-11-49 | WR-09 (an operator resolve command for NEEDS_ATTENTION rows) is deferred backlog. Fail-closed direction preferred: over-counting blocks entries; under-counting over-levers the account. | Plan 11-08 | 2026-09-25 |
| AR-13 | T-11-56 | Logged as `leg_cancel_on_error_failed` (error level) and audited with the order_id; the exception still propagates so the row ends NEEDS_ATTENTION (or stays OPENING/CLOSING, surfaced by the next startup reconcile alert with `_WORKING_ORDERS_HINT`). Upgrade path (deferred): cancel and await job tasks before `gateway.close` in `_shutdown`. | Plan 11-09 | 2026-09-25 |
| AR-14 | T-11-57 | Same failure class as the bot being down entirely; a readiness-gate check that warns on any OPEN row with dte<=0 at startup is the deferred upgrade path. | Plan 11-09 | 2026-09-25 |
| AR-15 | T-11-58 (WR-09 half) | This plan (and the quick task) add no NEW path into NEEDS_ATTENTION beyond what CR-02/EX-03 already cover; the `set_position_status(pid, "NEEDS_ATTENTION")` call-site count is unchanged. The resolve-tool gap itself is WR-09 (deferred), same as AR-12. | Plan 11-09 / quick 260925-goi | 2026-09-25 |
| AR-16 | T-11-59 | Out of WR-11's scope (that counter is for snapshot outages only); a manage-cycle exception before/around the snapshot is a distinct, already-logged (`options_manage_error`) failure mode. On a flapping outage that never reaches 3 consecutive: positions are still managed on every clean cycle, and expiry-day positions still get the per-position expiry warning regardless. | Plan 11-09 | 2026-09-25 |
| AR-17 | T-11-64 | Fails closed by design: the entry or close ends NEEDS_ATTENTION instead of a clean ABORTED/escalation. `order_status` is deliberately not consulted (would cost a second `order_list_query` per TTL expiry against a rate budget shared with the equity bot). Preservation test (`test_ttl_cancel_failure_after_full_fill_still_returns_the_fill`) plus a mutation check guarantee a fill-during-cancel is NOT a false alarm. | Quick task 260925-goi | 2026-09-25 |
| AR-18 | T-11-65 | Same class as AR-13 (T-11-56); logged at error level, audited as `leg_cancel_on_error_failed` with the order_id; the alert tells the operator to cancel working orders on the named codes. | Quick task 260925-goi | 2026-09-25 |
| AR-19 | T-11-66 | Legs open longs-first (pick_strikes invariant, unchanged); with the CR-04 fix at most ONE order per leg is outstanding for the leg's own qty, so whatever filled or can still fill is covered by a same-qty wing — defined risk. Operator is alerted with the codes and `_WORKING_ORDERS_HINT`. | Quick task 260925-goi | 2026-09-25 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-09-26 | 67 (unique; 74 raw appearances across 10 plans, merged) | 48 mitigate-closed + 19 accepted = 67 | 0 | gsd-security-auditor |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-09-26

### Notes for the record

- CR-04 (11-REVIEW.md's fourth-review BLOCKER, escalated in 11-VERIFICATION.md as a
  human-decision item) was fixed by the in-scope quick task before this audit ran —
  confirmed by direct code read of `bot/options/execution.py:141-169` (the
  `RuntimeError` raise on an unconfirmed TTL cancel) and by re-running
  `python3 -m pytest -q` (1350 passed, 1 skipped, matching the quick task's own
  documented count).
- Deferred code-review findings tracked elsewhere and NOT part of this phase's
  STRIDE register (referenced only where a register mitigation depended on them):
  WR-02, WR-03, WR-04, WR-08, WR-09, IN-01..IN-13, and the equity-bot sibling bug
  in `bot/execution/engine.py`'s TTL cancel path (noted as out-of-scope backlog by
  the quick task). None of these block this phase's threat register — see AR-07,
  AR-09, AR-12, AR-15, AR-16 above for the specific dependencies.
- Two Human Verification items remain open per `11-VERIFICATION.md`/`11-UAT.md`
  (a live paper run with both books registered, and the now-resolved CR-04 decision)
  — the live-run item is an operational UAT step, not a threat-mitigation gap, and
  does not block this audit.
