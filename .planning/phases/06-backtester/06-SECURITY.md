---
phase: 06
slug: backtester
status: verified
threats_open: 0
asvs_level: 1
created: 2026-07-06
---

# Phase 06 — Backtester — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

Register authored across six executed plans (06-01..06-06), each with its own
`<threat_model>` block. Three gap-closure plans (06-07/08/09) exist but are **not yet
executed** (no SUMMARY.md) — their threat entries are listed separately at the bottom and
are NOT verified against the codebase in this audit (nothing to verify; the code doesn't
exist yet).

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| yfinance → `backtester/feed.py` | Untrusted external market data (may be NaN/empty/partial/wrong-shape) | 5m/daily OHLCV frames |
| CSV cache file ↔ `backtester/feed.py` | Local file read/write; path derived from operator-supplied `--symbols` string | Cached OHLCV frames |
| backtest pipeline → "broker" | In the live bot this boundary is a real broker; in backtest it must be a pure simulator (`SimulatedExecution`/`SimulatedGateway`), zero order/quote/account calls | Order intents / fills |
| report → `--output-dir` filesystem | Writes CSV/JSON to an operator-supplied output directory | Trade log, metrics |
| harness → StateStore file | Opens a SQLite DB; must be a scratch path, never `data/bot_state.db` | Watchlist rows, position rows |
| replay clock → reused decision logic | Wall clock is monkeypatched per bar; a leak could corrupt later in-process live code | `now_et()` return value |
| operator CLI args → backtester | Untrusted operator input (symbols, dates, output dir) enters at the CLI | argv strings |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-06-01 | Tampering | `.gitignore` edit (06-01) | mitigate | Append-only edit preserving prior lines | **closed** |
| T-06-02 | Tampering / Info Disclosure | Malformed/partial yfinance frame (06-02, `feed.py`) | mitigate | All frames routed through `get_ticker_frame`; `BacktestWindowError` raised before any silent-empty fetch | **closed** |
| T-06-03 | Tampering | CSV cache path traversal from symbol string (06-02, `feed.py`) | mitigate | `SimulatedBarFeed.__init__` validates every prefix-stripped symbol against `_SYMBOL_RE` (`[A-Z0-9.\-]+` fullmatch) and raises `ValueError` before any cache dir/path is created (remediated 2026-07-06, see below) | **closed** |
| T-06-04 | Spoofing | Broker path in a data-source module (06-02, `feed.py`) | mitigate | `feed.py` imports only `bot.scanner.fetcher`/`universe` + pandas — no gateway import | **closed** |
| T-06-05 | Spoofing / Elevation | Real broker path in backtest (06-03, `execution.py`) | mitigate | `SimulatedExecution`/`SimulatedGateway` import nothing from `bot.gateway`/moomoo | **closed** |
| T-06-06 | Tampering | Look-ahead fill at signal-bar close (06-03, `execution.py`) | mitigate | `consume_intent` fills strictly at `feed.next_bar(...).open`, never `intent.entry_price`; proven by test | **closed*** (see caveat) |
| T-06-07 | Tampering | Report reads the empty `trades` DB table (06-04, `report.py`) | mitigate | `compute_metrics`/`write_report` operate only on the harness-supplied trade-log list, never `StateStore.get_closed_trades` | **closed** |
| T-06-08 | Tampering | `output_dir` path traversal (06-04, `report.py`) | accept | Single-operator local CLI; operator-chosen path | **closed** (logged below) |
| T-06-09 | Tampering (self-inflicted) | StateStore DB-path collision with live `data/bot_state.db` (06-05, `harness.py`) | mitigate | Scratch `db_path` required at construction; CLI-level equality guard in `run.py` | **closed** |
| T-06-10 | Spoofing | Real gateway in replay (06-05, `harness.py`) | mitigate | `PositionManager(gateway=None)`; `SignalEngine`/`RiskEngine` get `SimulatedGateway` only | **closed** |
| T-06-11 | Tampering | Look-ahead via wall-clock `now_et` (06-05, `harness.py`) | mitigate | Per-bar clock rebind in try/finally; test flips entry-window gate on replay-clock value | **closed** |
| T-06-12 | Tampering | Silent-wrong trail if `bar_buffer` unpopulated (06-05, `harness.py`) | mitigate | `bar_buffer` appended before `on_bar`; test asserts non-empty buffer at `on_bar` time | **closed** |
| T-06-13 | Tampering (self-inflicted) | Backtest opens/mutates live `data/bot_state.db` (06-06, `run.py`) | mitigate | Hard guard refuses when resolved DB path equals `DEFAULT_DB_PATH` | **closed** |
| T-06-14 | Tampering / DoS | Malformed CLI input reaching yfinance (06-06, `run.py`) | mitigate | `--start`/`--end` strptime + non-empty `--symbols` validated before config load / fetch | **closed** |
| T-06-15 | Spoofing / Elevation | Broker path from the CLI (06-06, `run.py`) | mitigate | `run.py` constructs `SimulatedBarFeed`+`BacktestHarness(gateway=None)` only; zero `MoomooGateway` refs | **closed** |
| T-06-16 | Info integrity | Silent empty report on out-of-window start (06-06, `run.py`) | mitigate | `BacktestWindowError` → `[ERROR]` + non-zero exit; test asserts loud failure | **closed*** (see caveat) |
| T-06-SC (×6, one per plan 06-01..06-06) | Tampering | Package installs | accept | Zero new package installs across the phase | **closed** (logged below) |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Verification Evidence (mitigate threats)

| Threat ID | Evidence |
|-----------|----------|
| T-06-01 | `git log -p -- .gitignore` (commit `12458cf`) diff hunk is pure addition (`+backtester/cache/`, `+backtester/runs/`), no `-` lines against prior content; `git check-ignore backtester/cache/ backtester/runs/` prints both paths. |
| T-06-02 | `backtester/feed.py:38-44` imports `get_ticker_frame`; used at `feed.py:123` (5m load) and `feed.py:271` (premarket fetch). `_enforce_window()` (`feed.py:136-147`) raises `BacktestWindowError` before any network call on a cache miss outside the window. `tests/backtester/test_feed.py:72-79` (`test_out_of_window_start_raises_backtest_window_error_not_silent_empty`) passes. |
| T-06-04 | `grep -n "^import\|^from" backtester/feed.py` shows only `os`, `datetime`, `typing`, `pandas`, `bot.safety.et_helpers.ET`, `bot.scanner.fetcher.*`, `bot.scanner.universe.yfinance_to_moomoo`. `grep -niE "gateway" backtester/feed.py` matches only the docstring's prose ("broker gateway layer") and the word "moomoo" appears only as a code-format string / function name (`yfinance_to_moomoo`), never an import. |
| T-06-05 | `backtester/execution.py:24-29` imports only `typing`, `uuid`, `pandas`, `bot.execution.events.FillEvent`. No `bot.gateway`/moomoo import; no `place_order`/`subscribe` defined anywhere in the file. |
| T-06-06 | `backtester/execution.py:57-71` (`consume_intent`): `avg_fill_price = next_bar["open"] + self._slippage`, sourced from `self._feed.next_bar(...)`, never `intent.entry_price`. `tests/backtester/test_execution.py:66-84` (`test_consume_intent_fills_at_bar_n_plus_1_open_not_intent_entry_price`) asserts `fill.avg_fill_price == bar_n_plus_1["open"]` and `!= intent.entry_price`; passes. |
| T-06-07 | `backtester/report.py` has no `StateStore`/`get_closed_trades` import; `compute_metrics(trades)`/`write_report(trades, output_dir)` take the trade list as a parameter (`report.py:23,77`). `grep -L 'get_closed_trades' backtester/report.py` lists the file (absent). |
| T-06-09 | `backtester/harness.py:72-81` requires the caller to supply an already-opened `store`; `backtester/run.py:81-86,126-133` (`_scratch_db_path` + collision guard) refuses when the resolved path equals `bot.state.store.DEFAULT_DB_PATH` (`data/bot_state.db`). `tests/backtester/test_harness.py:165-171` and `tests/backtester/test_run.py:78-88` (`test_db_collision_guard_refuses_live_db_path`) pass. |
| T-06-10 | `backtester/harness.py:99-106` constructs `PositionManager(..., gateway=None)`. No `MoomooGateway` import anywhere in `harness.py`. `tests/backtester/test_harness.py:155-162` (`test_harness_builds_position_manager_with_gateway_none`) passes. |
| T-06-11 | `backtester/harness.py:186-199` (`run()`) saves `original_now_et`, rebinds `_signal_engine_module.now_et` for the loop duration, restores in `finally`. `tests/backtester/test_harness.py:228-242` (`test_replay_clock_drives_entry_window_gate`) flips `_in_entry_window()` by changing the patched clock value and passes. |
| T-06-12 | `backtester/harness.py:245-249`: `buf.append(bar_data)` occurs before `await self.position_manager.on_bar(bar)`. `tests/backtester/test_harness.py:265-267` asserts `len(harness._bar_buffer["US.TEST"]) > 0` after a full replay. |
| T-06-13 | `backtester/run.py:126-133`: guard compares `os.path.normpath(db_path)` against `os.path.normpath(DEFAULT_DB_PATH)` and returns 1 with `[ERROR]` on match. `tests/backtester/test_run.py:78-88` monkeypatches `_scratch_db_path` to return `DEFAULT_DB_PATH` and asserts non-zero exit + `[ERROR]` + the path string in stderr; passes. |
| T-06-14 | `backtester/run.py:108-115`: `_parse_date`/`_parse_symbols` run and raise/return 1 before `configure_logging()`/`load_strategy_config()`/any feed construction. `tests/backtester/test_run.py:37-60` (`test_bad_start_date_exits_nonzero_before_fetch`, `test_empty_symbols_exits_nonzero`) pass. |
| T-06-15 | `grep -c 'MoomooGateway' backtester/run.py` → 0. `tests/backtester/test_run.py:91-95` (`test_run_py_never_constructs_a_broker_gateway`) inspects `inspect.getsource(run_mod)` for the literal string and asserts absence; passes. |
| T-06-16 | `backtester/run.py:140-144`: `BacktestWindowError` caught, printed as `[ERROR] ...` to stderr, returns 1. `tests/backtester/test_run.py:102-112` (`test_out_of_window_start_exits_nonzero_with_error`) passes. |

**Caveat on T-06-06 / T-06-16 (honesty check against 06-VERIFICATION.md, not double-counted as new threats):**
`06-VERIFICATION.md` (status `gaps_found`, 2026-07-07) independently confirmed three multi-day
look-ahead defects (CR-01, CR-04, CR-07) and a window-guard/actual-fetch mismatch (CR-02/CR-03)
that are *related to but narrower than* what T-06-06/T-06-16 specifically declare:
- T-06-06 declares only "fill never equals `intent.entry_price`" — this remains true and is
  proven by the cited test. It does **not** declare "next_bar respects same-session boundaries";
  CR-04 (feed.py `next_bar` has no same-session-date check, so a signal on day D's last bar can
  fill on day D+1's open) is a real gap but is **outside T-06-06's literal claim** and is already
  tracked as a gap-closure item (06-07-PLAN.md, T-06-07-03 in the *unexecuted* register below).
- T-06-16 declares only "a `BacktestWindowError` is surfaced loudly, never silently swallowed" —
  this mechanism is real and tested. It does **not** declare "the window guard's 58-trading-day
  constant matches the actual ~21-trading-day yfinance fetch window." CR-03 shows that mismatch
  means a `--start` in the 21–58 trading-day gap passes the guard and silently replays zero bars
  for the uncovered days — a real silent-empty-result path the module's own docstring promises
  never to allow, but through a different code path than the one T-06-16's mitigation covers.
  Already tracked as gap-closure (06-07-PLAN.md, T-06-07-01/02).

Both are left **closed** here because the literal, narrowly-scoped mitigation each threat ID
declares is present and test-proven; the broader correctness gaps are pre-existing, independently
documented BLOCKER findings in `06-VERIFICATION.md` already routed to gap-closure plans 06-07/08/09
(see bottom section) — re-flagging them here as this audit's own new findings would double-count
work already tracked.

---

## REMEDIATED GAP — T-06-03 (closed 2026-07-06)

**Original audit finding (preserved for the trail):** the plan-declared validation was absent.

**Remediation (2026-07-06, operator chose "Fix now" at the secure-phase gate):**
`backtester/feed.py` now defines `_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]+")` and
`SimulatedBarFeed.__init__` raises `ValueError` for any code whose prefix-stripped symbol
fails `_SYMBOL_RE.fullmatch(...)` — before `os.makedirs`, before any cache path construction,
before any network call. The allowed alphabet contains no path separator, so no `../` or
absolute-path segment can reach `os.path.join`. This single choke point covers the CLI
(`run.py --symbols`) and every other constructor of `SimulatedBarFeed` (tests, future
parameter-sweep drivers). Regression tests added to `tests/backtester/test_feed.py`:
- `test_traversal_symbol_rejected_before_any_cache_or_network_access` — `US.../../evil`
  raises `ValueError`, `yfinance.download` never called, cache dir left empty (proven RED
  before the guard existed, GREEN after).
- `test_dashed_and_dotted_real_tickers_still_accepted` — `US.BRK-B` constructs normally.
Full suite: 619 passed, 1 skipped.

**Original finding detail (as audited before the fix):**

**Declared mitigation (06-02-PLAN.md `<threat_model>`):** "Cache filenames are built from
validated yfinance symbols (already `^[A-Z.\-]+$`-shaped from universe normalization) written
under the fixed `backtester/cache/` dir; no operator-supplied path segment is interpolated."

**Verification result: mitigation absent.** No such regex/format validation exists anywhere in
the actual code path from operator CLI input to the cache file path:

- `backtester/run.py:73-78` (`_parse_symbols`) only comma-splits and strips whitespace — no
  format check (no `^[A-Z.\-]+$` or equivalent) is applied to `--symbols` before it becomes
  `SimulatedBarFeed.codes`.
- `backtester/feed.py:95-98` (`_yf_symbol`) only strips a `"US."` prefix — no format check.
- `backtester/feed.py:100-101` (`_cache_path`) interpolates that unvalidated string directly
  into `os.path.join(self.cache_dir, f"{symbol}_{interval}_{self.start}_{self.end}.csv")`, and
  `feed.py:126` (`frame.to_csv(self._cache_path(sym, "5m"))`) writes to it.
- `bot/scanner/universe.py` (`yfinance_to_moomoo`, `wiki_to_yfinance`) — the functions the
  mitigation cites as the source of "already-validated" symbols — perform **no** regex/format
  validation; they are pure string transforms (`.replace(".", "-")`, `f"US.{yf_symbol}"`).
  Confirmed by reading the full module: no `re.match`/`re.fullmatch`/format-guard exists.
- Grep across `tests/backtester/*.py` and `backtester/*.py` for `traversal`, `regex`,
  `fullmatch`, `re.match` returns zero hits outside an unrelated docstring — no test anywhere
  exercises or asserts rejection of a malformed/traversal `--symbols` value.
- Reproduced the escape independently (path construction only, no network/write executed):
  `os.path.join("backtester/cache", "../../../../tmp/pwned_evidence_5m_....csv")` normalizes to
  `../../tmp/pwned_evidence_5m_....csv` — outside `backtester/cache/`.

**Impact:** An operator (or anything invoking `backtester/run.py --symbols` programmatically,
e.g. a future orchestration script or Phase 7's parameter-sweep driver) supplying a `--symbols`
value containing `../` segments (or absolute-path-like content) causes `SimulatedBarFeed` to
write/read CSV cache files outside `backtester/cache/` at a path the operator's own string
controls. Consistent with the project's "single-operator local CLI" trust model this is a
lower-severity finding than a networked service would carry, but it does not match the
plan's own declared `mitigate` disposition and control — the specific validation control the
plan asserts exists (regex-shaped symbols) is not present in the code, is not enforced by any
of the three plans that touch symbol input (06-02 feed, 06-06 run.py CLI), and is not tested
anywhere in `tests/backtester/`.

**Next:** Either (a) add explicit format validation of `--symbols` entries against
`^[A-Z]{1,6}(-[A-Z])?$`-style constraints (or reuse a real regex check, not just prefix-strip)
in `backtester/run.py::_parse_symbols` before constructing `SimulatedBarFeed`, and/or in
`SimulatedBarFeed._yf_symbol`/`_cache_path`, with a test proving a `../`-containing symbol is
rejected before any cache path is built — or explicitly re-disposition T-06-03 as `accept`
(matching the "single-operator local CLI, no untrusted-remote input" rationale already used for
T-06-08) and log it in the Accepted Risks Log below with an operator sign-off.

**Resolution:** option (a) was implemented (validation in `SimulatedBarFeed.__init__` + tests,
see remediation note at the top of this section). T-06-03 is **CLOSED**.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|--------------|------|
| AR-06-01 | T-06-SC (06-01) | Zero new package installs in plan 06-01 (test scaffold only); nothing to slopcheck | gsd-security-auditor (phase-time disposition, plan 06-01) | 2026-07-06 |
| AR-06-02 | T-06-SC (06-02) | Zero new package installs in plan 06-02 (`yfinance`/`pandas` already vetted Phase 2) | gsd-security-auditor (phase-time disposition, plan 06-02) | 2026-07-06 |
| AR-06-03 | T-06-SC (06-03) | Zero new package installs in plan 06-03 | gsd-security-auditor (phase-time disposition, plan 06-03) | 2026-07-06 |
| AR-06-04 | T-06-08 | `output_dir` is an operator-chosen local path on their own machine (single-operator local CLI trust model, 06-RESEARCH Security Domain); `os.makedirs(exist_ok=True)` only, no untrusted-remote input | gsd-security-auditor (phase-time disposition, plan 06-04) | 2026-07-06 |
| AR-06-05 | T-06-SC (06-04) | Stdlib `csv`/`json` only; zero new package installs in plan 06-04 | gsd-security-auditor (phase-time disposition, plan 06-04) | 2026-07-06 |
| AR-06-06 | T-06-SC (06-05) | Zero new package installs in plan 06-05 | gsd-security-auditor (phase-time disposition, plan 06-05) | 2026-07-06 |
| AR-06-07 | T-06-SC (06-06) | Zero new package installs in plan 06-06 (`pandas-market-calendars` already pinned Phase 2, commit `45d41e6`) | gsd-security-auditor (phase-time disposition, plan 06-06) | 2026-07-06 |

*Accepted risks do not resurface in future audit runs.*

---

## Pending Execution — Gap-Closure Plans 06-07 / 06-08 / 06-09

Plans 06-07, 06-08, 06-09 exist (`06-07-PLAN.md`, `06-08-PLAN.md`, `06-09-PLAN.md`) but have
**no corresponding SUMMARY.md** — they have not been executed. Their `<threat_model>` blocks are
PLAN-TIME dispositions for code not yet written. **Not verified in this audit** (there is nothing
in `backtester/*.py` yet to check them against); listed here so a re-run of `/gsd-secure-phase 06`
after `/gsd-execute-phase 06 --gaps-only` picks them up.

| Threat ID | Category | Component | Disposition | Mitigation Plan (unexecuted) |
|-----------|----------|-----------|-------------|-------------------------------|
| T-06-07-01 | Tampering | yfinance 5m frames (NaN/wrong-day/missing rows) | mitigate | `get_ticker_frame` + `_enforce_coverage` raising `BacktestWindowError` naming zero-bar trading days |
| T-06-07-02 | Denial of Service | out-of-window date range | mitigate | `_enforce_window` cutoff corrected to 60 calendar days; coverage guard names missing days (closes CR-02/CR-03) |
| T-06-07-03 | Information disclosure | look-ahead leakage into point-in-time values | mitigate | `next_bar` same-session constraint; premarket-only `premarket_highs`/`synthetic_today_price` (closes CR-04/CR-07) |
| T-06-07-SC | Tampering | package installs | accept | No new packages (`pandas`/`pandas_market_calendars`/`yfinance` already deps) |
| T-06-08-01 | Tampering | `manage_exit` fabricating a full fill on no-next-bar | mitigate | WR-01 fix: return 0, append no fill, position stays open for EOD force-close |
| T-06-08-02 | Repudiation | trade log missing/incorrect exit records | mitigate | Force-close fill records a real priced exit into `exit_fills` |
| T-06-08-03 | Elevation of privilege | broker/order path leaking into backtest | accept | `execution.py` still imports nothing from the moomoo gateway layer |
| T-06-08-SC | Tampering | package installs | accept | No new packages |
| T-06-09-01 | Tampering | cross-day premarket-high clobber | mitigate | Per-day highs (`_premarket_highs_by_day`) applied at top of `replay_day` (closes CR-01) |
| T-06-09-02 | Elevation of privilege | unfiltered `--symbols` trading despite daily filter | mitigate | Entry branch gated on capped/ranked persisted watchlist (closes CR-06) |
| T-06-09-03 | Repudiation | positions open at range end silently dropped from report | mitigate | EOD/end-of-run `force_close_all` (closes CR-05) |
| T-06-09-04 | Tampering | replay-clock rebind leaking to other code | mitigate | Both `now_et` patches saved/restored in `run()`'s `finally` |
| T-06-09-SC | Tampering | package installs | accept | No new packages |

---

## Unregistered Flags

None. All six executed plans' SUMMARY.md files report `## Threat Flags: None` — each stated
that its only new surface was exactly the surface its own `<threat_model>` already registered.
Independently reviewed: no new network endpoint, auth path, schema change, or unregistered
attack surface was found beyond what the register above covers (the T-06-03 finding is a
verification failure of an *already-registered* threat, not new surface).

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-07-06 | 23 (16 mitigate + 7 accept) | 22 | 1 | gsd-security-auditor |
| 2026-07-06 (remediation) | 23 (16 mitigate + 7 accept) | 23 | 0 | orchestrator (operator-approved "Fix now"; T-06-03 guard + 2 regression tests, suite 619 passed/1 skipped) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-07-06 — all 23 executed-plan threats closed (T-06-03 remediated
same-day with operator approval). Gap-closure plans 06-07/08/09 remain unexecuted; re-run
`/gsd-secure-phase 06` after `/gsd-execute-phase 06 --gaps-only` to verify their 13
plan-time threat entries (see Pending Execution section).
