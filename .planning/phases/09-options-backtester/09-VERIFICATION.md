---
phase: 09-options-backtester
verified: 2026-08-17T19:38:52Z
status: gaps_found
score: 6/9 must-haves verified
overrides_applied: 0
gaps:
  - truth: "The phase produces offline evidence answering the pre-registered hypothesis set (H1 IVR 20 vs 30, H2 16Δ vs 20Δ, H3 IC vs PCS), enabling a data-driven rules_options.json decision"
    status: failed
    reason: "docs/research/2026-08-17-options-backtest-results.md reports all three hypotheses INSUFFICIENT-EVIDENCE. No hypothesis-arm backtest completed in either the IS or OOS window — zero PF/Sortino numbers exist for any arm. This is the phase's core deliverable (the roadmap goal text is 'Produce offline evidence for/against the tasty_credit_spreads strategy... and answer a pre-registered hypothesis set'), and it was not produced. The infrastructure and pipeline exist and are individually correct, but the stated goal — actionable evidence — does not exist in the codebase."
    artifacts:
      - path: "docs/research/2026-08-17-options-backtest-results.md"
        issue: "All three verdicts are INSUFFICIENT-EVIDENCE with zero real trades; results doc itself states 'no hypothesis-arm run could be completed at all'"
    missing:
      - "A completed hypothesis-arm backtest (IS + OOS) for at least one hypothesis, clearing the >=25-trade evidence floor, OR a documented decision to close/re-scope the phase given the data-layer infeasibility"
  - truth: "The replay engine correctly accounts for every position opened during the backtest window (no silent survivorship bias)"
    status: failed
    reason: "09-REVIEW.md CR-01 (critical, unfixed): positions still open at --end are never marked, settled, or reported — engine.py's run()/run_day() only appends to trade_log via _record_close, and OptionsBacktestEngine._open is discarded when the loop ends. Because manage_decision books winners early (50% profit target) and lets losers ride toward manage_dte/expiry, any completed run's win_rate/PF/Sortino/drawdown would be biased optimistically. summary.json also carries no open_positions_at_end count. Confirmed absent in current code: no 'end_of_window', 'close_open_at_end', or 'open_positions_at_end' token anywhere in backtester/options/*.py or backtester/options_run.py."
    artifacts:
      - path: "backtester/options/engine.py"
        issue: "No end-of-window settlement/reporting of positions still in self._open when run(days) completes (lines ~166-169, ~306)"
      - path: "backtester/options/report.py"
        issue: "write_options_report has no open_positions_at_end / unrealized_open_usd field"
    missing:
      - "close_open_at_end (or equivalent) called after engine.run(...) in options_run.main, marking every residual open position with a distinct exit_reason and surfacing the count in summary.json's extra_assumptions"
  - truth: "The data layer can acquire enough real option contract history within a practical operator wall-clock budget to run a hypothesis-arm backtest"
    status: failed
    reason: "backtester/massive.py's fetch_contracts requests expired=true only (WR-03, confirmed present at line 192 — no expired=false pass), and cached_option_bars never caches empty-frame results and keys on the exact warmup-inclusive window (WR-04, confirmed — 'if not frame.empty' gates the cache write at both cached_bars:174 and cached_option_bars:246). Combined with OptionChainSource.load's eager per-contract-bar fetch across the full strike-band candidate set, one IS-window SPY run at the tightest reasonable ±2% strike band was measured at 58,366 candidate contracts against a live-measured 5.3-5.8 req/min sustained rate -- ~183 hours (~90x the plan's own 2-hour stop budget) for ONE arm's ONE window. This is why gap #1 exists: the pipeline is individually correct but cannot acquire data at a workable rate."
    artifacts:
      - path: "backtester/massive.py"
        issue: "fetch_contracts (line ~190-195): expired=true only, missing expired=false union; cached_option_bars/cached_bars: empty results never cached, so re-runs and IS/OOS arms re-hit the rate-limited API for the same thin contracts"
      - path: "backtester/options/data.py"
        issue: "OptionChainSource.load eagerly fetches option bars for every strike-band candidate across every expiry rather than lazily fetching only near-ATM/target-delta contracts per decision day"
    missing:
      - "Lazy per-day/near-ATM-only contract-bar fetching (fetch only the handful of strikes pick_strikes actually needs, not the full strike-band candidate set up front)"
      - "Cache negative (empty) results so re-runs and overlapping IS/OOS windows don't re-fetch the same thin contracts"
      - "A higher-throughput Massive tier, parallel --workers, or a smaller/less liquid test universe if the free-tier rate ceiling is fixed"
---

# Phase 9: Options backtester Verification Report

**Phase Goal:** Produce offline evidence for/against the `tasty_credit_spreads` strategy before it earns real capital: replay `bot/options/strategy.py`'s pure functions unchanged over Massive historical option data (contracts reference incl. `expired=true` + `O:…` daily aggregates), with Black-Scholes IV/delta derived from closes and IVR from an own daily ATM-IV series, and answer a pre-registered hypothesis set (IVR 20 vs 30, 16Δ vs 20Δ, IC vs PCS) so any change to `rules_options.json` is data-driven rather than default.
**Verified:** 2026-08-17T19:38:52Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | CLI (`python3 -m backtester.options_run ...`) runs end-to-end on ≥1 underlying (SPY) with no modification to `bot/options/strategy.py`, proven by an import-identity test | ✓ VERIFIED | `test_imports_not_copies` (tests/backtester/options/test_engine.py:121) asserts `getattr(engine_mod, name) is getattr(strategy_mod, name)` for 9 functions; `smoke-real-data` run completed exit 0 against live Massive data (330 real contract fetches, per 09-04-SUMMARY.md/results doc); `git diff --exit-code bot/options/strategy.py` clean for the phase |
| 2 | Look-ahead test proves entry/strike decisions never see bars after the decision date | ✓ VERIFIED | `test_no_lookahead_contracts_for_day` (tests/backtester/options/test_data.py:220) — leak/future-only/past-only fixture, passes |
| 3 | Black-Scholes IV/delta round-trip + delta monotonicity + IVR fixture tests pass | ✓ VERIFIED | `test_iv_roundtrip`, `test_delta_monotonic`, `test_ivr_fixture` (tests/backtester/options/test_greeks.py:24,36,142) all pass in green suite |
| 4 | Hypotheses doc committed BEFORE the first real-data run (git history proves ordering) | ✓ VERIFIED | `git log --diff-filter=A -- 'docs/research/2026-08-17-options-backtest-hypotheses.md'` → `a62c0af`; results doc `90ac8a2` and the smoke run both postdate it |
| 5 | Full test suite green | ✓ VERIFIED | `pytest tests/backtester/options/ -q` → 62 passed; `pytest -q` → 1027 passed, 1 skipped |
| 6 | `rules_options.json` changed only if a hypothesis is SUPPORTED with OOS confirmation | ✓ VERIFIED (vacuously) | No hypothesis reached SUPPORTED; `git diff --exit-code rules_options.json` clean |
| 7 | **The phase produces offline evidence answering the pre-registered hypothesis set** | ✗ FAILED | All three hypotheses report INSUFFICIENT-EVIDENCE with zero completed arm-runs — see gap #1 |
| 8 | **The replay engine correctly accounts for every position opened (no survivorship bias)** | ✗ FAILED | CR-01 (09-REVIEW.md, critical, unfixed) — see gap #2 |
| 9 | **The data layer can acquire enough real data within a practical wall-clock budget to run a hypothesis-arm backtest** | ✗ FAILED | WR-03/WR-04 (09-REVIEW.md, unfixed) + measured 58,366-contract / ~183-hour projection — see gap #3 |

**Score:** 6/9 truths verified

Note: truths 1-6 map to the literal ROADMAP.md Success Criteria (1-4, 6, and the config-unchanged half of criterion 5) and all pass. Truths 7-9 are derived from the phase's stated GOAL text ("Produce offline evidence... and answer a pre-registered hypothesis set") and from the code-review findings already on record (09-REVIEW.md) — they fail. Per goal-backward methodology, task/infrastructure completion is not goal achievement: the roadmap's own criterion 5 ("Result doc reports each hypothesis as SUPPORTED/REJECTED/INSUFFICIENT-EVIDENCE with the numbers") is satisfied only in its most literal, degenerate form — every hypothesis reports INSUFFICIENT-EVIDENCE with zero numbers, which the pre-registration itself designed as a valid escape hatch but which does not deliver the phase's actual purpose.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backtester/options/data.py` | `OptionChainSource`, imports `bot.options.strategy`, no-look-ahead | ✓ VERIFIED | Present, tested; WR-03 (expired=true only) and eager full-strike-band fetch are real defects but don't invalidate correctness of what IS fetched |
| `backtester/massive.py` | `cached_contracts`/`cached_option_bars` on `MassiveDataSource` | ⚠️ PARTIAL | Exists and functions, but empty-result caching (WR-04) and `expired=true`-only (WR-03) are unfixed and are the direct cause of gap #3 |
| `backtester/options/greeks.py` | BS price/delta/IV, IVR | ✓ VERIFIED | `bs_price`, `bs_delta`, `implied_vol`, `atm_iv`, `iv_rank` present, tested; WR-01 (NaN input returns ~5.0 instead of None) is a real defect but is a latent edge case, not exercised by the smoke run |
| `backtester/options/engine.py` | `OptionsBacktestEngine`, imports strategy, cap order, fills, settlement | ⚠️ PARTIAL | Import identity, cap order, fill/settlement arithmetic all verified by tests — but CR-01 (open positions never settled at `--end`) is a structural correctness gap that would bias any completed run |
| `backtester/options/report.py` | Report glue reusing `backtester/report.py` ratio helpers | ✓ VERIFIED | `from backtester.report import` present; no per-share stock P&L pipeline imported |
| `backtester/options_run.py` | CLI entry point, `--set` overrides, effective-config capture | ✓ VERIFIED | `main`, `apply_overrides` present; tested offline; ran successfully against live data (smoke run) |
| `docs/research/2026-08-17-options-backtest-hypotheses.md` | Pre-registered H1/H2/H3, evidence floor, IS/OOS windows | ✓ VERIFIED | Committed `a62c0af`, before any real-data run |
| `docs/research/2026-08-17-options-backtest-results.md` | Per-hypothesis verdicts with numbers | ⚠️ HOLLOW | Exists, format-correct, honestly reported — but carries zero numbers because no arm-run completed; does not deliver evidence |
| `tests/backtester/options/*.py` | Full offline coverage, no live network | ✓ VERIFIED | 62 tests, all offline, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `backtester/options/engine.py` | `bot.options.strategy` | direct import, identity-asserted | ✓ WIRED | `test_imports_not_copies` passes |
| `backtester/options/data.py` | `backtester.massive.MassiveDataSource` | constructor injection | ✓ WIRED | `cached_contracts`/`cached_option_bars`/`cached_bars` called |
| `backtester/options_run.py` | `backtester.options.engine.OptionsBacktestEngine` | composition root | ✓ WIRED | proven by smoke-real-data run (exit 0) |
| `backtester/options/report.py` | `backtester.report._sortino_ratio`/`_win_loss_stats` | private helper reuse | ✓ WIRED | grep confirms import, no `_net_pnl`/`compute_metrics` reuse |
| `docs/research/*-results.md` | real backtest numbers | hypothesis-arm run output | ✗ NOT WIRED | No arm-run ever produced trade data to report — the results doc is honest about this but the link genuinely does not exist |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full options test suite | `pytest tests/backtester/options/ -q` | `62 passed in 0.27s` | ✓ PASS |
| Full project test suite | `pytest -q` | `1027 passed, 1 skipped in 51.59s` | ✓ PASS |
| Import-not-copy identity | `pytest tests/backtester/options/test_engine.py::test_imports_not_copies -q` | pass (part of full run) | ✓ PASS |
| Look-ahead fixture | `pytest tests/backtester/options/test_data.py::test_no_lookahead_contracts_for_day -q` | pass (part of full run) | ✓ PASS |
| Hypotheses doc precedes results (git ordering) | `git log --diff-filter=A --format=%h -- 'docs/research/*options-backtest-hypotheses.md'` | `a62c0af` (predates `90ac8a2`) | ✓ PASS |
| `rules_options.json` unchanged | `git diff --exit-code rules_options.json` | clean, exit 0 | ✓ PASS |
| CR-01 fix presence | `grep -rn "end_of_window\|open_positions_at_end\|close_open_at_end" backtester/options/*.py backtester/options_run.py` | no matches | ✗ FAIL (confirms gap #2) |
| WR-03 fix presence (expired=false union) | `grep -n "expired=false" backtester/massive.py` | no matches | ✗ FAIL (confirms gap #3) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| OBT-01 | 09-01, 09-03, 09-04 | Import-not-copy, `rules_options.json` unchanged during runs | ✓ SATISFIED | `test_imports_not_copies`, smoke run, config-byte-identity test |
| OBT-02 | 09-01 | Massive data layer, on-disk cache, no look-ahead | ⚠️ SATISFIED w/ known defects | Look-ahead proven; but `expired=true`-only + no negative-result caching (WR-03/WR-04) are unfixed and are the root cause of gap #3 |
| OBT-03 | 09-02 | BS IV/delta from closes | ✓ SATISFIED | Round-trip + monotonicity tests pass; WR-01 NaN-handling defect noted but not blocking |
| OBT-04 | 09-02 | IVR from own rolling ATM-IV series | ✓ SATISFIED | `iv_rank`/`atm_iv` + fixture test pass |
| OBT-05 | 09-03 | Fill model, commissions, expiry settlement | ⚠️ SATISFIED w/ known defect | Correct for trades that close within the window; CR-01 means trades that DON'T close within the window are silently dropped, not settled |
| OBT-06 | 09-02, 09-04 | Pre-registered hypotheses, committed before first real run | ⚠️ SATISFIED (process only) | Doc exists, correctly ordered — but the hypotheses were never actually answered with data (gap #1) |
| OBT-07 | 09-03, 09-04 | Output = trade log + summary reusing report.py conventions | ⚠️ SATISFIED (structure only) | Artifact shape correct; content biased by CR-01 whenever a run does complete; results doc has zero real numbers |

No orphaned requirements — all 7 OBT-01..07 IDs appear in at least one plan's `requirements:` frontmatter and are individually traced above.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `backtester/options/engine.py` | ~166-169, 306 | Open positions dropped from trade_log at end of replay window (CR-01) | 🛑 Blocker | Any completed hypothesis-arm run would report biased win_rate/PF/Sortino/DD |
| `backtester/massive.py` | ~190-195 | `fetch_contracts` requests `expired=true` only (WR-03) | ⚠️ Warning | Any `--end` reaching into "still-live" expiries silently loses chain data |
| `backtester/massive.py` | 174, 246 | Empty-frame results never cached (WR-04) | ⚠️ Warning | Every re-run re-hits the rate-limited API for the same thin/illiquid contracts, compounding the infeasibility documented in gap #3 |
| `backtester/options/greeks.py` | 97-125 | `implied_vol` returns ~5.0 instead of `None` for NaN price/spot (WR-01) | ⚠️ Warning | Latent — could poison IVR series if a NaN close reaches it; not observed to have fired in the smoke run |
| `backtester/options/engine.py` | 313-324 | Daily-loss breaker ignores unrealized P&L (WR-02) | ⚠️ Warning | Diverges from live's two-trip-point breaker; overstates trade count on worst days if a full run executes |

No unreferenced `TBD`/`FIXME`/`XXX` debt markers found in the phase's modified files.

### Human Verification Required

None. The remaining questions (whether to accept the data-layer infeasibility as a phase-closing outcome, or to scope a follow-up phase for lazy/parallel fetching) are project-management decisions, not verifiable code behavior, and are already surfaced as structured gaps above for `/gsd-plan-phase --gaps` to act on.

### Gaps Summary

Phase 9 built a structurally correct, well-tested options-backtester pipeline (data layer, Black-Scholes greeks/IVR, daily replay engine, CLI, report glue) — all 6 literal ROADMAP.md success criteria that concern code artifacts and tests pass, and the full suite (1027 passed, 1 skipped) is green. However, the phase's actual GOAL — "Produce offline evidence for/against the `tasty_credit_spreads` strategy... and answer a pre-registered hypothesis set" — was NOT achieved. All three pre-registered hypotheses report INSUFFICIENT-EVIDENCE because no hypothesis-arm backtest could be completed at all: SPY's option chain density (58,366+ strike-band-narrowed candidate contracts at the tightest reasonable band) combined with Massive's measured ~5.3-5.8 req/min free-tier rate and the data layer's eager per-contract-bar fetch strategy projects to ~183 hours for a single arm's single window — about 90x the plan's own stop-and-report budget. This is honestly documented in the results doc and was a pre-authorized stop condition, but it means zero real evidence exists to inform `rules_options.json`, which is the entire reason this phase exists ahead of the strategy earning real capital.

Compounding this, the 09-REVIEW.md code review (same day) found CR-01 (critical, unfixed): the engine silently drops positions still open at `--end` from every reported metric, which would bias results toward looking better than reality on any run that DOES complete — this must be fixed before the infrastructure can be trusted for a future completed run. WR-03 (missing `expired=false` union) and WR-04 (no negative-result caching, window-exact cache keys) are also unfixed and are the direct mechanical cause of the data-acquisition infeasibility — fixing WR-04 alone (cache per-contract-ticker rather than per-window, cache empty results) would materially reduce the re-fetch cost across the IS/OOS/multi-hypothesis-arm matrix, though it would not alone close a 90x gap.

**Recommendation:** This phase should not be considered complete until either (a) a follow-up plan fixes CR-01 + the data-layer feasibility gaps (lazy per-decision-day/near-ATM fetching instead of eager full-strike-band, negative-result caching, ticker-keyed cache) and produces at least one completed hypothesis-arm run, or (b) the project owner explicitly accepts INSUFFICIENT-EVIDENCE as the terminal outcome for this phase (an override should be recorded if so) and defers real-data hypothesis evaluation to a later phase with a higher-throughput data source.

---

_Verified: 2026-08-17T19:38:52Z_
_Verifier: Claude (gsd-verifier)_
