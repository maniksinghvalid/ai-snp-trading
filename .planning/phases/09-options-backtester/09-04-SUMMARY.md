---
phase: 09-options-backtester
plan: 04
subsystem: backtester-options-cli
tags: [cli, composition-root, massive-api, pre-registration, insufficient-evidence]
dependency-graph:
  requires: [backtester/options/data.py::OptionChainSource, backtester/options/greeks.py::atm_iv, backtester/options/engine.py::OptionsBacktestEngine, backtester/options/report.py::write_options_report, bot/options/config.py::load_options_config, docs/research/2026-08-17-options-backtest-hypotheses.md]
  provides: [backtester/options_run.py::main, backtester/options_run.py::apply_overrides, docs/research/2026-08-17-options-backtest-results.md]
  affects: []
tech-stack:
  added: []
  patterns: [validate-before-fetch (V5), effective-config-to-run-dir (D-15/D-16), warm-up-priming-without-entry (private-attr composition)]
key-files:
  created:
    - backtester/options_run.py
    - tests/backtester/options/test_options_run.py
    - docs/research/2026-08-17-options-backtest-results.md
  modified:
    - .gitignore
    - .planning/phases/09-options-backtester/09-VALIDATION.md
decisions:
  - "Warm-up priming reaches engine._iv_series/engine.chains directly from the CLI (_prime_iv_series) rather than feeding warm-up days through engine.run() -- OptionsBacktestEngine.run_day combines IV-update+manage+entry in one loop with no public warm-up-only API, and splitting it is out of this plan's scope (engine.py is frozen/unmodified)"
  - "T-09-11 stopped per its own pre-authorized wall-clock budget: SPY's IS-window strike-band-narrowed candidate count (58,366-79,750) times the live-measured Massive fetch rate (5.3-5.8 req/min) projects to ~183 hours for one arm's one window -- ~90x the plan's ~2hr stop-and-report budget. Grouped-daily options endpoint (RESEARCH Open Question #3 escape hatch) probed once, live: HTTP 400, not available"
  - "All three hypotheses (H1/H2/H3) report INSUFFICIENT-EVIDENCE for this reason -- no hypothesis-arm backtest could be completed at all, not merely a trade count below the 25-trade floor. rules_options.json is unchanged (no hypothesis reached SUPPORTED)"
metrics:
  duration: "~90 minutes (incl. ~70 min of live Massive API probing/measurement/smoke run)"
  completed: 2026-08-17
---

# Phase 9 Plan 04: Options backtester CLI, real-data attempt, and results doc Summary

Built `backtester/options_run.py` (the D-01 CLI entry point), then attempted the
pre-registered real-data hypothesis backtests against live Massive data. The CLI works
correctly end to end (proven against live data via a deliberately minimal smoke run); the
full hypothesis matrix could not be completed within any practical wall-clock budget because
SPY's option chain is far denser than Massive's per-contract-bar architecture (D-05, frozen)
can backfill at the observed free-tier rate. All three hypotheses report INSUFFICIENT-EVIDENCE
with the underlying infrastructure numbers documented; `rules_options.json` is unchanged.

## What Was Built

- **`backtester/options_run.py`** (T-09-10): `build_arg_parser`/`apply_overrides`/`main`,
  a composition root only (no strategy logic). Validates dates/symbols/numeric flags BEFORE
  any config load, filesystem write or network call (V5); applies `--set KEY=VALUE` dotted-path
  overrides to an in-memory copy of `rules_options.json`, writes the EFFECTIVE config to
  `<out>/config.json`, then loads it through the real `load_options_config` schema validator
  (an out-of-range override fails there, not mid-run) — `rules_options.json` on disk is never
  rewritten (D-15, verified by a byte-identical-before/after test). `_warmup_start`/
  `_prime_iv_series` handle the `--iv-warmup-days` warm-up: each underlying's ATM-IV series is
  fed for the warm-up days ONLY (mirroring `OptionsBacktestEngine.run_day`'s own IV-update
  loop, without running manage/entry), so a warm-up day can structurally never open a position
  — `passes_entry_gate` already fails closed on `ivr_pct is None` (`bot/options/strategy.py:94-96`),
  which is what makes this work without touching the frozen `engine.py`.
- **`tests/backtester/options/test_options_run.py`** (8 tests, all offline): override capture
  + repo-file-untouched, unknown-path rejection, validate-before-config-load ordering,
  missing-API-key exit, an offline CLI smoke run via a fake `OptionsBacktestEngine` (D-16
  artifact shape), and `--help`.
- **Real-data attempt (T-09-11)**: ordering gate confirmed (`a62c0af` precedes every result
  artifact); `.gitignore` extended with `backtester/results/`; live-measured SPY option chain
  density and Massive fetch rate (see Deviations); one real end-to-end smoke run completed.
- **`docs/research/2026-08-17-options-backtest-results.md`** (T-09-12): all three hypotheses
  verdicted INSUFFICIENT-EVIDENCE with the measured infrastructure numbers as justification,
  declared limitations restated from Plan 09-03, and an explicit "what this does NOT tell us"
  section. `rules_options.json` left unchanged per the pre-registered rule.

## Real-data run table (T-09-11)

| Step | Action | Symbols / Window | Result |
|------|--------|-------------------|--------|
| 1 | Ordering gate | — | PASS — `a62c0af` precedes all artifacts |
| 2 | `.gitignore` | — | `backtester/results/` added, commit `e877ed5` |
| 3 | SPY contracts-reference probe (full 2yr window) | US.SPY, 2024-08-14→2026-08-14 | 162,176 contracts (live, 138s) |
| 4 | SPY contracts-reference probe (IS-window expiry range) | US.SPY, expiry≤2025-09-29 | 86,612 contracts (live) |
| 5 | Grouped-daily options endpoint probe | — | `HTTP 400` — not available on this Massive plan (1 request, as budgeted) |
| 6 | Per-contract fetch rate measurement | US.SPY, 40 contracts | 450.8s → 5.32 req/min sustained |
| 7 | `baseline-IS-probe` (plan-literal full IS run) | US.SPY, 2024-11-18→2025-07-31 | ABORTED after 5m38s (stalled pre-first-fetch); superseded by steps 6+8 |
| 8 | `smoke-real-data` — end-to-end pipeline proof | US.SPY, 2025-06-12→2025-06-13, 1-day warmup, ±1% band, dte 1-10 | **COMPLETED, exit 0** — 330 real contracts, ~57min (~5.8 req/min), `total_trades: 0` by design (warmup below `IV_RANK_MIN_OBS=60`) |

Full numeric detail (strike-band-narrowed candidate counts at 20%/10%/5%/2%, and the
projected ~183-hour wall clock for one arm's one window) is in the results doc.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — plan text ambiguity resolved against verified behavior] Warm-up priming via a
dedicated CLI-side loop, not `engine.run(trading_days(warmup_start, end))`**
- **Found during:** T-09-10 implementation — the plan's literal text ("run it over
  `trading_days(start, end)`... the warm-up days feed the IV series, never an entry") is
  self-contradictory if taken as "call `engine.run()` only for `[start, end]`": `_iv_series`
  is populated exclusively inside `run_day`, so warm-up days never processed by `run_day`
  would leave `_iv_series` permanently empty and `iv_rank` would always return `None` — zero
  entries ever, defeating the entire run. The alternative reading ("feed warm-up days through
  `engine.run()` too") also fails the hypotheses doc's explicit "no entries evaluated" warm-up
  claim, since `passes_entry_gate` could start passing partway through the warm-up window
  (as soon as `IV_RANK_MIN_OBS=60` observations accumulate, which the 67-day warm-up window
  is sized to happen a few days before the window's own end).
- **Fix:** `_prime_iv_series` replicates `run_day`'s IV-update half only (via
  `chain.underlying_close`/`chain.contracts_for_day`/`greeks.atm_iv`, all already-public,
  already-imported functions) for warm-up days, writing directly to `engine._iv_series`.
  `engine.run()` itself is called only with `trading_days(start, end)`, matching the plan's
  literal text for the decision-day loop. `engine.py` (frozen, Plan 09-03) is unmodified.
- **Files modified:** `backtester/options_run.py`
- **Commit:** `b4ad24a`

### Escalated (would normally be Rule 4, but the plan pre-authorizes this exact outcome)

**2. [Data-infrastructure infeasibility, pre-authorized by the plan's own `<action>` text]
T-09-11's real hypothesis-arm backtests could not be completed**
- **Found during:** T-09-11 — attempting the plan-literal SPY baseline
  (`--start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67`).
- **Issue:** SPY's option chain (weekly expiries, ~$1 strike increments across a wide range)
  yields 58,366-79,750 strike-band-narrowed candidates for the IS window alone, at any
  reasonable band width. At the live-measured 5.3-5.8 req/min Massive fetch rate, that is
  ~183 hours (~7.6 days) for ONE arm's ONE window — ~90x the plan's own explicit "~2 hours,
  then STOP and report" budget (T-09-11 `<action>`). This is a structural mismatch between
  D-05's locked-in per-contract-bar architecture and SPY's actual chain density, not a bug in
  Plans 09-01/09-03 (both frozen, unmodified) or in `options_run.py`.
- **Action taken (no unauthorized change made):** Per the plan's own text — *"If SPY alone
  exceeds roughly 2 hours of wall clock, STOP and report the observed rate rather than
  grinding"* and *"if a run cannot finish, record INSUFFICIENT-EVIDENCE honestly rather than
  loosening any gate"* — probed the one permitted escape hatch (grouped-daily options
  endpoint, RESEARCH Open Question #3): `HTTP 400`, not available. Ran one deliberately
  minimal real-data smoke test to prove the CLI pipeline is correct end to end against live
  data (satisfying success_criteria #1). Did NOT loosen the evidence floor, widen the OOS
  window, narrow the D-04 universe to manufacture trades, or fabricate additional shallow
  runs to hit the plan's `>=6 summary.json` mechanical acceptance-criteria count — all three
  hypotheses are reported INSUFFICIENT-EVIDENCE with the real numbers, as the plan's own
  evidence-floor rule requires when a gate cannot be honestly cleared.
- **Files modified:** none (`bot/options/*` and `rules_options.json` both unchanged, verified
  by `git status --porcelain` in the results doc's provenance)
- **Commit:** `90ac8a2` (results doc), `8530879` (VALIDATION.md status flip)

## Self-Check

- `backtester/options_run.py` — FOUND
- `tests/backtester/options/test_options_run.py` — FOUND
- `docs/research/2026-08-17-options-backtest-results.md` — FOUND
- `.gitignore` (backtester/results/ entry) — FOUND
- Commit `b4ad24a` (T-09-10) — FOUND
- Commit `e877ed5` (.gitignore) — FOUND
- Commit `90ac8a2` (T-09-12 results doc) — FOUND
- Commit `8530879` (09-VALIDATION.md) — FOUND

## Verification

- `pytest tests/backtester/options/test_options_run.py -x -q` — 8 passed
- `pytest tests/backtester/options/ -x -q` — 62 passed
- `pytest -q` (full suite) — **1027 passed, 1 skipped** (1019 baseline + 8 new; no regression)
- `python3 -m backtester.options_run --help` — exit 0, lists `--rules`/`--symbols`/`--start`/`--end`/`--set`/`--out`
- `python3 -m backtester.options_run --start 20xx --end 2026-01-01 ...` (malformed date) —
  exit 1, `[ERROR]` on stderr, no network call
- `git diff --exit-code rules_options.json` — clean
- `git status --porcelain bot/options/` — empty
- `grep -v '^#' backtester/options_run.py | grep -c 'bot.gateway\|moomoo\|place_order'` — 0
- `grep -Ec 'H[123].*(SUPPORTED|REJECTED|INSUFFICIENT-EVIDENCE)' docs/research/2026-08-17-options-backtest-results.md` — 3
- Ordering gate: `git log --diff-filter=A --format=%h -- 'docs/research/*options-backtest-hypotheses.md'` — returns `a62c0af`, precedes every artifact this plan produced

## Known Stubs

None — `backtester/options_run.py` has no hardcoded/placeholder data paths; every code path
either runs the real pipeline or exits 1 with a real error message.

## Threat Flags

None — this plan wires already-reviewed modules (Plans 09-01/09-03) behind a CLI. No new
network destination, auth path, or trust boundary beyond what the phase's threat model
already covers (`--symbols`/`--out`/`--set` guarded per T-09-T1/T-09-T2, MASSIVE_API_KEY
handling unchanged from `backtester/massive.py`).

## Self-Check: PASSED
