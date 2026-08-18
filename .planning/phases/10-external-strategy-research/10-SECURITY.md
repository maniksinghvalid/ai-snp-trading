---
phase: 10
slug: external-strategy-research
status: verified
threats_open: 0
asvs_level: 1
created: 2026-08-18
---

# Phase 10 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

This audit verifies mitigations against the implemented code and committed artifacts —
it does not accept plan/SUMMARY prose as evidence. Every row below was checked live
(grep, git log/diff/status, direct file reads, byte-level number comparisons, or a live
pytest run) against the repository at HEAD (`5979a13`) on 2026-08-18. Where a check
surfaced a discrepancy between what a SUMMARY.md claimed and what the repository
actually shows, the discrepancy is recorded in the Evidence column rather than smoothed
over.

---

## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| repo working tree -> git history | Pre-registration integrity depends on commit ordering being real, not asserted |
| local process -> yfinance HTTP | The phase's only network egress; read-only public market data, no credentials |
| local process -> Massive API | MUST NOT be crossed in this phase (shared 5 req/min budget owned by the Phase 9 warm-cache-pool) |
| `backtester/experimental/` -> `bot/` | New research code may READ pure `bot/` functions; must never write, import stateful services, or reach a broker/order path |
| `backtester/experimental/` -> `backtester/report.py` | Shared module used by TJL and the options backtester; any edit is a blast-radius risk |
| engine -> `SimulatedBarFeed` | Cache-only replay; a feed constructed with a wrong window would fetch |
| CLI args -> cache filesystem | A typo'd window/symbol is the input that could cross into a network fetch |
| run.py / run batch -> existing TJL results | Read-only; the 226-trade evidence of record must never be regenerated or overwritten |
| this phase -> Phase 9 background process (`warm-cache-pool`) | Must not be signalled, paused, or killed |
| gitignored run dirs -> committed report | The report's numbers must survive as committed artifacts, or the evidence is unverifiable later |
| pre-registered rules -> verdict assignment | The point of failure is human: softening a rule after seeing the data |
| aggregation -> `rules.json` | Must never be crossed by this phase, whatever the verdict says |
| research verdict -> production code | The gate; the only sanctioned path from evidence to `bot/` |
| feature branch -> `develop` | Must never be crossed by this phase (merging is a separate, operator-approved action) |
| config -> live order path | A new strategy must not widen the paper-trading-only execution surface |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-10-01 | Repudiation | `docs/research/2026-08-18-external-strategies-hypotheses.md`, `arms.json` | mitigate | Both files added in exactly one commit (`f8439aa`, verified via `git log --diff-filter=A -- <both paths>`); `backtester/results/experimental/` never appears anywhere in `git log --all --diff-filter=A` (gitignored per `.gitignore:47`, so no run artifact can ever precede it in history) | closed |
| T-10-02 | Tampering | `bot/`, `rules.json`, `rules_options.json` | mitigate | `git diff --stat d68ea9d^ HEAD -- bot/ rules.json rules_options.json` is empty (checked live); the full 48-file phase diff-stat contains zero entries under those three paths; `git status --porcelain` on the same paths is empty at HEAD | closed |
| T-10-03 | Denial of Service | Massive API shared 5 req/min budget | mitigate | `run.py:304-307` — real credentials (`MassiveDataSource(load_massive_api_key())`) only construct behind `--allow-fetch`; the default path is `MassiveDataSource(api_key="", ...)`, fail-closed. Direct grep of all 33 persisted run logs (`backtester/results/experimental/logs/*.log`, still on disk): zero "fetch" tokens except the self-referential success line in `postflight.log` ("no fetch token in any log"). `postflight.log`'s own cache-mtime scan found 80 new files, all `O_*`-prefixed (Phase 9's own option-contract fetches) — 0 non-`O_*` files, i.e. 0 new MEGA24 equity/TOD files from this phase. `warm-cache-pool` pid 48166 has an identical command line in `preflight.log` and `postflight.log` | closed |
| T-10-04 | Information disclosure | `backtester/cache/SPY_1d_regime.csv` | accept | See Accepted Risks Log | closed |
| T-10-05 | Tampering | `backtester/report.py::_CSV_FIELDS` | mitigate | `_CSV_FIELDS` (report.py:298-299) has exactly 8 elements (confirmed via live import); `write_report` builds `fieldnames = list(_CSV_FIELDS) + list(extra_fields or [])` (report.py:326) — a local copy, `_CSV_FIELDS` itself never mutated; repo-wide grep for `_CSV_FIELDS.extend`/`_CSV_FIELDS +=`/`_CSV_FIELDS.append` returns 0 matches. `report.py` changed exactly once across the whole phase (15 lines, plan 10-02); plan 10-05's claim of "zero further diff to report.py" holds | closed |
| T-10-06 | Tampering | `indicators.py`/`strategies.py` signal functions (silent look-ahead) | mitigate | All real `.shift(` call sites in both files use positive args only (`shift(1)`, backward-looking); explicit grep for `center=True`/`.iloc[...+1]` in `indicators.py`, `strategies.py`, `engine.py` finds none in code. `tests/backtester/experimental/test_strategies.py::test_prefix_invariance_no_lookahead_for_all_three_strategies` (5 prefix lengths x 3 strategies x every output column) passes live. **Discrepancy noted:** 10-02-SUMMARY.md claims "zero `shift(-`, verified by grep" — the actual live `grep -c 'shift(-' indicators.py` returns **1**, not 0. The sole hit is the module docstring itself (`indicators.py:8`, prose reading "no shift(-n)" while describing the invariant), not code. This is the same self-referential-docstring grep-hygiene issue that 10-03/10-05 caught and fixed for other literal strings (`StateStore`, `fromisoformat`) — it was not caught here, and the SUMMARY's claim is factually inaccurate as written. The underlying security property (no actual look-ahead in code) is independently verified true by direct inspection + the passing test, so this is a documentation-accuracy gap, not an open threat | closed (see discrepancy note) |
| T-10-07 | Information disclosure | short-side sign errors | mitigate | 4 tests found and pass live: `tests/backtester/test_report.py::test_net_pnl_short_side_sign_and_backward_compat`, `tests/backtester/experimental/test_exits.py::test_pct_ladder_short_mirror_levels` + `::test_partial_be_trail_short_075r_and_1r_breakeven`, `tests/backtester/experimental/test_engine.py::test_short_trade_row_side_and_positive_r_multiple_on_profit`. `report.py:44` (`_net_pnl`) confirmed side-aware: `sign = -1 if trade.get("side") == "short" else 1` | closed |
| T-10-08 | Spoofing | padded cache-key derivation | mitigate | `run.py:40` imports `_MASSIVE_DAILY_PAD_DAYS`/`_MASSIVE_TOD_PAD_DAYS` from `backtester/feed.py:84-85` (real constants, not reimplemented); `_required_cache_paths` (run.py:145-158) derives padding with the identical subtraction `feed.py:365-366`'s own `_load_massive` uses. Window C's derived key (`start=2024-09-02` - 30 days = `2024-08-03`) matches the literal file `backtester/cache/massive/AAPL_5m_2024-08-03_2024-12-31.csv` found on disk | closed |
| T-10-09 | Tampering | existing TJL baseline results | mitigate | `grep -c 'BacktestHarness' backtester/experimental/run.py` = 0. `_load_baseline_trades` (run.py:321) opens `trades.csv` read-only. Direct byte-level comparison: `backtester/results/strategy-audit-validation/fulluniverse/E1/base/summary.json` (mtime **2026-08-13**, predates this phase's 2026-08-18 execution) vs this phase's `backtester/results/experimental/tjl/E1/tjl_base/summary.json` — `total_trades` 35==35, `profit_factor` 1.020553845119392==1.020553845119392, exact match, not merely "close" | closed |
| T-10-10 | Repudiation | run provenance | mitigate | `run.py:228-236` `_git_sha()` is a real `git rev-parse HEAD` subprocess call, falling back to `"unknown"` only on exception. Live spot-check of an on-disk `params.json` shows a real 41-char sha; `grep -l '"git_sha": "unknown"'` across all 130 `params.json` files on disk returns 0 matches | closed |
| T-10-11 | Denial of Service | 10-minute Bash timeout truncating a long window run | accept | See Accepted Risks Log | closed |
| T-10-12 | Repudiation | verdict integrity | mitigate | Results doc `docs/research/2026-08-18-external-strategies-results.md:521`, H2 row ("0.907 vs 0.897 / 1890 / met \| 0.980 vs 0.975 / 1675 / met \| SUPPORTED") traced byte-for-byte to the committed `docs/research/assets/2026-08-18-external-strategies/results.csv` rows for `ext2_at_exit`/`ext2_base` IS+OOS (`profit_factor` 0.9072514199038461/0.89682456094728/0.9794995183713406/0.9746614782883745 round to the exact cited numbers). `git diff HEAD` on both files is empty (no post-hoc edits). `arms.json` still has exactly 15 arms, no `combo`, and 0 diff since its single `f8439aa` commit — the pre-registered combo trigger (PF>1 IS) never fired (best IS PF across all 15 arms is 0.922) and no arm was silently appended | closed |
| T-10-13 | Information disclosure | evidence durability | mitigate | `git ls-files docs/research/assets/2026-08-18-external-strategies/` lists all 5 evidence files (`results.csv`, `results.md`, `results-intrabar.csv`, `equity_curves.svg`, `pf_by_slice.svg`) as tracked/committed. `.gitignore:47` confirms the source `backtester/results/` tree (where these were copied from) stays gitignored | closed |
| T-10-14 | Denial of Service | `inf`/NaN formatting crashing the renderer | mitigate | `tests/backtester/experimental/test_aggregate.py::test_infinite_pf_never_raises_and_md_prints_inf` passes live. `aggregate.py`'s `_fmt`/`_fmt_md` helpers explicitly render `float("inf")`/`float("-inf")` as literal strings. **Implementation differs from the plan's literal text:** the mitigation plan describes "`json.load` (not strict parser) for `summary.json` files that contain the `Infinity` token" — but `aggregate.py` contains 0 occurrences of `json.load` and never re-reads any `summary.json`; it recomputes every metric from `trades.csv` via `compute_metrics` instead. This sidesteps the JSON-Infinity-token hazard architecturally rather than handling it with a lenient parser — a stronger mitigation than literally specified, verified via the passing test above | closed (stronger-than-specified mitigation, see note) |
| T-10-15 | Elevation of privilege | research code reaching production | mitigate | `10-06-PLAN.md` Task 1 (lines 100-111) is a real, unmodified verify script that mechanically parses the results doc's `## Verdict table` (not a re-interpretation). Task 2 (lines 121-178) is a genuine `type="checkpoint:decision" gate="blocking"` task — GSD's blocking human-checkpoint mechanism, not a narrative aside. `10-06-SUMMARY.md` records the live run: `GATE: TRIGGERED` via H2 (exact verify-script output reproduced in the SUMMARY), operator selected `defer` with recorded rationale ("both exit variants have base-cost PF < 1.0... zero production code should be written on a verdict that isn't actually profitable"), Task 3 explicitly recorded as skipped | closed |
| T-10-16 | Tampering | `develop` branch integrity | mitigate | Precondition verified directly: `git branch -a \| grep -i phase10` returns no matches — no `feature/phase10-*` branch was ever created, because Task 3 (the only task that would create one) never ran (Task 2 selected `defer`). Correctly closed as "precondition never occurred," not "untested" | closed (precondition never occurred) |
| T-10-17 | Tampering | default runtime behaviour | mitigate | Precondition verified directly: `git diff --stat d68ea9d^ HEAD -- bot/ rules.json rules_options.json` is empty (same live check as T-10-02). No `strategy=None` seam or any other code was ever added to `bot/signal/signal_engine.py`, because Task 3 (the only task that would add it) never ran. The regression test this threat describes was never needed because the code it would guard was never written | closed (precondition never occurred) |
| T-10-18 | Elevation of privilege | short-side/unimplemented exit model reaching the live FSM | mitigate | The pre-existing (pre-phase-10) fail-closed guard is verified intact and untouched: `bot/config/loader.py:38` `_IMPLEMENTED_EXIT_MODELS = ("partial_be_trail",)` and its `raise ConfigError` at line 276-281 have 0 diff across this phase (same evidence as T-10-02). Separately verified: `direction` is schema-required (`bot/config/schema.py:21,35`) but is not read anywhere in `bot/config/loader.py` or any `bot/*.py` file (grep confirmed) — it is decorative today, so no live code path can act on a non-`long_only` value regardless of what `rules.json` says. Task 3 (which would have added the `direction`-consuming seam and any new exit model) never ran | closed (precondition never occurred; pre-existing guard verified intact) |
| T-10-19 | Tampering | live/real-money order path | accept | See Accepted Risks Log | closed |
| T-10-SC | Tampering | npm/pip/cargo installs | mitigate | `git diff --stat d68ea9d^ HEAD -- requirements.txt setup.py setup.cfg pyproject.toml Pipfile` is empty. Every top-level import across all 7 new `backtester/experimental/*.py` modules is stdlib (`argparse`, `csv`, `os`, `sys`, `math`, `json`, `subprocess`, `datetime`, `collections`, `dataclasses`, `typing`), an already-declared dependency (`pandas`, `numpy` — both pre-existing in `requirements.txt`), or an internal `bot.`/`backtester.` module. 0 new third-party packages introduced anywhere in the phase | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

**20/20 threats closed. 0 open.**

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-10-01 | T-10-04 | `backtester/cache/SPY_1d_regime.csv` holds only public daily OHLCV for a liquid ETF (SPY) fetched from yfinance — `Date,open,high,low,close,volume` columns only, no PII, no credentials, no account data (verified by reading the file live). The file is gitignored (`.gitignore:43`), so it never enters git history regardless | Plan 10-01 (pre-registered) | 2026-08-18 |
| AR-10-02 | T-10-11 | A 10-minute Bash tool timeout could truncate a long single-window run mid-matrix. Accepted because the fallback is fully deterministic and lossless: re-issue the identical command with `run_in_background: true` and poll the log; the matrix is never shortened, and per-arm output writes are atomic (`write_report` per arm), so a truncated attempt leaves no corrupted partial state. This fired for real on window D's base run (11/15 arms completed before timeout) — the documented fallback was applied and all 15 arms completed correctly on retry (verified: `find backtester/results/experimental/runs/D/base -name summary.json \| wc -l` == 15, confirmed in 10-04-SUMMARY.md) | Plan 10-04 (pre-registered, exercised in practice) | 2026-08-18 |
| AR-10-03 | T-10-19 | No execution-path change was in scope for this phase. Accepted because it is structurally guaranteed rather than merely asserted: `bot/` has zero diff across the entire phase (`git diff --stat d68ea9d^ HEAD -- bot/` empty, verified live) — the existing SAFE-01 paper-only guard and `FUTU_TRD_ENV=SIMULATE` order-path gate were never touched by any of the 6 plans | Plan 10-06 (pre-registered) | 2026-08-18 |

*Accepted risks do not resurface in future audit runs.*

---

## Unregistered Flags

None. Every `## Threat Flags` section across the 6 plan SUMMARYs (10-04, 10-05, 10-06 have explicit sections; 10-01/02/03 have no such section but their Deviations/Issues sections were checked directly) reports "None" or documents only non-security bug fixes and docstring-wording nits already folded into their originating commits, with no new network endpoint, auth path, file-access pattern, or schema change introduced.

**Cross-reference, not a registered threat:** the independent code review (`10-REVIEW.md`, produced by a separate quality-review process on this same phase) flags two WARNING-level (non-BLOCKER) data-integrity robustness gaps in the experimental engine — WR-01 (a dormant cross-session data leak in `vwap_pb_signals`) and WR-02 (entry evaluation not independently gated by the force-close bar, relying implicitly on `SimulatedBarFeed`'s own same-session guard in `backtester/feed.py`, which is unmodified by this phase). Neither crosses a trust boundary in this phase's threat model, and the review explicitly states it found "no BLOCKER-level defect that corrupts the published numbers" — consistent with this audit's own independent byte-level verification of the published verdict numbers (T-10-09, T-10-12). Noted here for completeness since they touch the same code surface as T-10-06; not counted against `threats_open`.

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-08-18 | 20 | 20 | 0 | gsd-security-auditor |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-08-18
