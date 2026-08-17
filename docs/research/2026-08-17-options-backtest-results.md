# Options backtest results (Phase 9, T-09-12)

**Dated 2026-08-17**, same day as the pre-registration
(`docs/research/2026-08-17-options-backtest-hypotheses.md`, commit `a62c0af`, which strictly
precedes every artifact this doc cites). Code commit for the CLI this doc's runs used:
`b4ad24a` (`feat(09-04): options backtester CLI with --set overrides and effective-config
capture`), plus `e877ed5` (`chore(09-04): gitignore backtester/results/`).

## Verdict summary

**All three hypotheses are INSUFFICIENT-EVIDENCE.** Not because a completed backtest fell
short of the 25-trade evidence floor — no hypothesis-arm backtest could be COMPLETED at all
within any wall-clock budget this execution could reasonably absorb. The reason is a
data-infrastructure constraint discovered during T-09-11, detailed below with real, measured
numbers: SPY's option chain is dense enough (weekly expiries, sub-$1 to $1 strike increments)
that even the tightest reasonable strike-band narrowing still leaves tens of thousands of
individual contracts to fetch one-by-one, at Massive's observed ~5.3-5.8 requests/minute
sustained rate. This is exactly the outcome the hypotheses doc's evidence-floor section
pre-authorized as valid and non-negotiable: *"INSUFFICIENT-EVIDENCE is an expected, valid,
publishable outcome ... it is NOT a reason to loosen the floor, extend the OOS window into
untested territory, or narrow the universe further to make a target easier to hit."* No gate
was loosened. No hypothesis was evaluated on partial or unfiltered data. `rules_options.json`
is unchanged (see Config decision, below).

## Provenance

- Hypotheses doc: `docs/research/2026-08-17-options-backtest-hypotheses.md`, commit `a62c0af`
  (2026-08-17), added before any options result existed.
- CLI code commit: `b4ad24a` (T-09-10, `backtester/options_run.py` + offline tests).
- Massive entitlement window actually available: `[2024-08-15, 2026-08-17]` (Plan 09-01's
  live-probed rolling ~24-month boundary), unchanged from pre-registration.
- Run-id -> outcome table (T-09-11 findings; every row below is a REAL action taken against
  live infrastructure or the live-populated on-disk cache, not a simulation):

| Step | Action | Symbols | Window | Result |
|------|--------|---------|--------|--------|
| 1 | Ordering gate | — | — | PASS: `git log --diff-filter=A -- 'docs/research/*options-backtest-hypotheses.md'` returns `a62c0af`, predating every artifact below. |
| 2 | `.gitignore` update | — | — | `backtester/results/` added (commit `e877ed5`). |
| 3 | SPY contracts-reference probe (full entitled window) | US.SPY | 2024-08-14 to 2026-08-14 | 162,176 total contracts (live fetch, 138s, one paginated request sequence). Cached at `backtester/cache/massive/contracts_SPY_2024-08-14_2026-08-14.json`. |
| 4 | SPY contracts-reference probe (IS-window expiry range) | US.SPY | expiry <= 2025-09-29 | 86,612 contracts (live fetch). Strike-band narrowing computed offline from this cache — see table below. |
| 5 | Grouped-daily options endpoint probe (RESEARCH Open Question #3 escape hatch) | — | 2025-06-13 | `GET /v2/aggs/grouped/locale/us/market/options/{date}` -> **HTTP 400** (not available/not entitled on this Massive plan). One request, as budgeted. |
| 6 | Per-contract fetch rate measurement | US.SPY | 40 distinct call contracts, IS-window range | **450.8s for 40 contracts = 5.32 req/min** sustained (live fetch, no 429s observed). |
| 7 | `baseline-IS-probe` attempt (full plan-literal step 1: `--start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67`) | US.SPY | IS window | **ABORTED after 5m38s** — stalled at the contracts-reference pagination / first per-contract fetch (0 `O_*` cache files created, 0% CPU, socket in CLOSE_WAIT). Killed; superseded by the rate measurement (step 6) and the smoke run (step 8) as the actual evidence. |
| 8 | `smoke-real-data` — end-to-end pipeline proof | US.SPY | 2025-06-12 to 2025-06-13 (1-day IV warm-up, `--strike-band-pct 1.0`, `--set entry.min_dte=1 --set entry.max_dte=10`) | **COMPLETED, exit 0.** 330 real contracts fetched in ~57 minutes (~5.8 req/min sustained). `trades.csv`/`summary.json`/`config.json` all present at `backtester/results/options/smoke-real-data/` (gitignored, not committed — D-16). `total_trades: 0` **by design**: `--iv-warmup-days 1` is far below `IV_RANK_MIN_OBS=60`, so `iv_rank` returns `None` for both decision days and `passes_entry_gate` fails closed on every symbol/day (`bot/options/strategy.py:94-96`) — this run's purpose was proving the CLI wiring against live data (success_criteria #1), not evaluating a hypothesis. |

### Strike-band-narrowed candidate counts, IS window (`entry.min_dte=30`/`max_dte=60`, `expiry <= 2025-09-29`), computed offline from the cached contracts JSON (zero additional API cost)

| Strike band | Underlying close range used | Narrowed candidate count |
|---|---|---|
| ±20% (CLI default) | $496.48-$637.10 -> band $397.18-$764.53 | 79,750 |
| ±10% | -> band $446.83-$700.81 | 72,310 |
| ±5% | -> band $471.66-$668.96 | 65,036 |
| ±2% (tightest reasonable) | -> band $486.55-$649.84 | 58,366 |

At the measured 5.3-5.8 req/min sustained rate, the **tightest reasonable** IS-window run
(±2% band) projects to **58,366 / 5.3 ≈ 11,012 minutes ≈ 183 hours (≈7.6 days)** of
continuous, unthrottled fetching for ONE arm's ONE window — before accounting for the OOS
window, the second and third hypothesis's own arms, or D-04's multi-underlying pooling (which
would only add more contracts, since pooling was designed to raise the *trade count* of an
already-completed backtest, not to make data collection itself tractable). This is roughly
90x the plan's own explicit "~2 hours, then STOP and report" budget (T-09-11 `<action>`), and
the escape hatch it names (RESEARCH Open Question #3's grouped-daily endpoint) is confirmed
unavailable on this Massive plan (step 5, HTTP 400). No amount of within-scope tuning (band
width, D-04 universe order) closes a 90x gap — this is a structural mismatch between D-05's
locked-in per-contract-bar architecture and SPY's actual weekly-expiry chain density, not a
tunable parameter or a code defect in Plans 09-01/09-03 (both frozen, unmodified in this
plan).

## Per-hypothesis verdict

### H1 (IVR entry threshold, `entry.ivr_min` 20 vs 30): INSUFFICIENT-EVIDENCE

Neither Arm A (`ivr_min=30`) nor Arm B (`--set entry.ivr_min=20`) was backtested in the IS or
OOS window — no hypothesis-arm run could be completed at all (see above). No PF/Sortino
numbers exist for this hypothesis; none are reported, per the pre-registered rule (*"No
PF/Sortino comparison is reported as a verdict in this case"*).

### H2 (short-strike delta, `structure.short_delta` 0.16 vs 0.20): INSUFFICIENT-EVIDENCE

Same reason as H1 — no arm run completed in either window.

### H3 (structure, `structure.type` iron_condor vs put_credit_spread): INSUFFICIENT-EVIDENCE

Same reason as H1/H2 — no arm run completed in either window.

## Declared limitations

These are restated verbatim from the pre-registration and Plan 09-03's documented engine
divergences — they would have applied to any completed run, and are recorded here for
completeness even though no hypothesis-evaluation run reached them:

- **No bid/ask in Massive daily aggregates (D-11):** the fill model is entirely
  close-derived — `synthesize_bid_ask` fabricates a bid/ask from `close ± spread_pct/2` so
  `leg_is_liquid` can run unmodified.
- **Open-interest gate is not evaluable offline:** Massive's `O:` daily aggregates carry no
  `open_interest` field; `min_open_interest` is approximated from `v` (volume) when
  `--oi-source volume` (the default and the mode the smoke run used) — never silently
  upgraded to "OI gate enforced" language.
- **Deterministic `sorted(symbols)` underlying order**, not live's broker `stock_id`-sort —
  a documented, intentional divergence (`backtester/options/engine.py` module docstring).
- **IVR is min-max normalized** (tastytrade/industry "IV Rank" convention), not percentile
  rank (IVP) — A4 (whether live moomoo's own `IV_RANK` uses the same convention) remains an
  unverified assumption.
- **New limitation found in this plan (T-09-11):** the Massive per-contract-bar architecture
  (D-05) is computationally infeasible to backfill for a liquid, weekly-expiry underlying
  like SPY at the free-tier rate limit — see the candidate-count/projected-wall-clock table
  above. This is a limitation of the DATA ACQUISITION layer, not the engine, greeks, or fill
  model; it applies equally to every hypothesis and every underlying in the D-04 universe
  (adding more underlyings only adds more contracts to fetch).

## What this does NOT tell us

This run gives **zero evidence, in either direction**, about whether a looser IVR gate, a
farther-OTM short strike, or a put-credit-spread structure would have performed better or
worse than the live `rules_options.json` baseline over 2024-11-18 through 2026-06-15. It also
does not tell us the pipeline is broken — the opposite: `smoke-real-data` (step 8, above)
proves `backtester/options_run.py` correctly wires `MassiveDataSource` ->
`OptionChainSource` -> `backtester.options.greeks` -> `OptionsBacktestEngine` ->
`write_options_report` end to end against LIVE Massive option data, with zero modification to
`bot/options/*`, producing well-formed `config.json`/`trades.csv`/`summary.json` artifacts
(success_criteria #1, satisfied literally, at a deliberately minimal scope). What is missing
is exclusively the ability to acquire enough real contract-level price history, within any
practical wall-clock budget, to run the pre-registered hypothesis matrix on real data. A
future phase revisiting this would need either a higher-throughput Massive tier, a different
data vendor with a grouped-daily options endpoint (confirmed unavailable on Massive, step 5),
or a smaller/less liquid test universe from the start (D-04's QQQ/IWM/TLT/GLD/XLE were never
reached, since SPY alone already exceeded the wall-clock budget by ~90x at the tightest
reasonable band).

## Config decision

**`rules_options.json` is UNCHANGED.** Per the pre-registration's own rule (*"`rules_options.json`
changes AFTER this backtest's results exist, and only for a hypothesis whose verdict is
SUPPORTED"*) and this plan's action text (*"If and ONLY if a hypothesis is SUPPORTED ... edit
the single corresponding value ... Otherwise leave `rules_options.json` untouched"*): no
hypothesis reached SUPPORTED (or REJECTED — both require a completed backtest), so no edit is
made. `git diff --exit-code rules_options.json` is clean. `bot/options/*` is unmodified.
