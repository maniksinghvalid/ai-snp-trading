# Options backtest results (Phase 9, T-09-12, corrected T-09-15)

**Dated 2026-08-17**, same day as the pre-registration
(`docs/research/2026-08-17-options-backtest-hypotheses.md`, commit `a62c0af`, which strictly
precedes every artifact this doc cites). Code commit for the CLI this doc's runs used:
`b4ad24a` (`feat(09-04): options backtester CLI with --set overrides and effective-config
capture`), plus `e877ed5` (`chore(09-04): gitignore backtester/results/`).

**T-09-15 correction (this revision):** T-09-12's original root-cause narrative below
(measured "~5.3-5.8 req/min observed" and a ~183h projection) was INCOMPLETE — the true
constraint is a hard per-minute request cap enforced server-side (see "Corrected root cause"
below), and the data layer that produced those numbers fetched every strike-band candidate x
every expiry up front (eager design). Plan 09-05 replaced that with a lazy per-decision-day
fetch (`OptionChainSource.rows_for`) that touches an order of magnitude fewer contracts. No
hypothesis-arm backtest has been run since this correction — all three verdicts remain
INSUFFICIENT-EVIDENCE, now for a *bounded* reason (wall-clock, not infeasibility) with the
exact commands an operator needs to close that gap.

## Verdict summary

**All three hypotheses are still INSUFFICIENT-EVIDENCE.** No hypothesis-arm backtest has
completed in either the IS or OOS window as of this revision — Plan 09-05 fixed the data
layer's feasibility and two correctness defects (CR-01, WR-01/02) but did not itself run any
network fetch (offline-only per its own scope). This is exactly the outcome the hypotheses
doc's evidence-floor section pre-authorizes as valid: *"INSUFFICIENT-EVIDENCE is an expected,
valid, publishable outcome ... it is NOT a reason to loosen the floor, extend the OOS window
into untested territory, or narrow the universe further to make a target easier to hit."* No
gate was loosened. No hypothesis was evaluated on partial or unfiltered data.
`rules_options.json` is unchanged (see Config decision, below).

## Corrected root cause (T-09-15)

T-09-12's original narrative attributed the infeasibility to a *sustained throughput* limit
("~5.3-5.8 req/min observed"). Re-measured 2026-08-17 (orchestrator, live): Massive returns
exactly **5 requests, then HTTP 429 with no `Retry-After` header** — this is a **server-side
per-minute request-count tier cap**, not a gradually-degrading rate. The same tier explains
the ~24-month option-aggregates history boundary Plan 09-01 probed (both are properties of
the same Massive plan tier, not independent limits). Raw per-request latency, once past the
cap, is **0.23s** — the wall-clock cost is *entirely* the 5-req/min gate, not network or
server processing time.

**The eager data layer (Plans 09-01/09-03, frozen at the time) compounded that cap**: it
fetched a per-contract bar for every strike-band candidate x every expiry in the whole
window up front, which is why the original SPY IS-window count was 58,366+ contracts (see
the historical table below). **Plan 09-05's lazy fetch fixes the multiplier, not the cap**:
`OptionChainSource.rows_for(day, expiry, underlying_px, band_pct)` fetches bars ONLY for the
one expiry `pick_expiry` actually selects on a given decision day, and only for strikes in a
narrow OTM-side band around that day's close — never the whole strike band x every expiry.
Offline-measured (no network; using the REAL `pick_expiry` over the cached SPY contracts
reference + SPY closes, 2024-08-14 -> 2026-08-14):

| Design | Candidate contracts, full SPY history | vs 5 req/min free tier |
|---|---|---|
| Eager (pre-09-05), IS window only, tightest ±2% band | 58,366 | ~183h for ONE window |
| Lazy (09-05), ±10% band, full 2024-08->2026-08 history | **~7,500** | **~25h, once, for ALL of SPY's history** |
| Lazy (09-05), ±8% band | ~6,700 | ~22h |
| Lazy (09-05), ±15% band | ~8,900 | ~30h |

The lazy count comes from **480 decision days** (NYSE trading days across the full entitled
window) touching **55 distinct chosen expiries** — `pick_expiry` returns the same handful of
expiries repeatedly as DTE rolls forward, so the in-memory memoisation inside
`OptionChainSource` means each of those 55 expiries' in-band contracts is fetched ONCE for
the whole run, not once per decision day. This number covers the ENTIRE SPY history in one
pass — every hypothesis arm and every IS/OOS window reuses the same on-disk cache
(`backtester/massive.py`'s ticker-keyed negative-caching `cached_option_bars`, T-09-13/WR-04),
so this ~25h is paid ONCE, not once per arm.

### Runtime projections (T-09-15)

| Scenario | Symbols | `--workers` | Projected wall clock |
|---|---|---|---|
| Free tier (5 req/min cap), SPY only | US.SPY | 1 (forced by the cap) | **≈25 hours**, once |
| Free tier, 6-ETF pool (SPY,QQQ,IWM,TLT,GLD,XLE) | 6 symbols | 1 | **≈2-3 days**, once |
| Paid Massive options tier (no per-minute cap), SPY only | US.SPY | 8 | **≈5 minutes** (0.23s/req x ~7,500 / 8 workers) |

`--workers` only helps on a tier without the 5-req/min cap — on the free tier, parallel
workers just race each other into the same 429 wall faster; `--workers 1` (the CLI default)
is the correct free-tier choice.

### Operator commands to close this gap

**Step 1 — warm the cache** (one pass per underlying group, chronological order matters:
the bar cache is keyed per contract ticker, T-09-13/WR-04, so an EARLIER `--start` in a LATER
run will not backfill; always run the earliest window first). One command spanning the
earliest IV warm-up start through the OOS end already touches every ticker every later arm
needs (same `min_dte`/`max_dte`/`target_dte` across all three hypotheses' arms — only
`entry.ivr_min`/`structure.short_delta`/`structure.type` differ, none of which change which
contracts get fetched):

```bash
# SPY only (free tier, ~25h) -- H1/H2/H3 all reuse this cache afterward.
python3 -m backtester.options_run --symbols US.SPY \
  --start 2024-11-18 --end 2026-06-15 --iv-warmup-days 67 \
  --strike-band-pct 10 --workers 1 --label warm-cache-spy

# 6-ETF pool (free tier, ~2-3 days) -- only needed if the evidence floor
# (>=25 trades/arm/window) is not cleared with SPY alone.
python3 -m backtester.options_run \
  --symbols US.SPY,US.QQQ,US.IWM,US.TLT,US.GLD,US.XLE \
  --start 2024-11-18 --end 2026-06-15 --iv-warmup-days 67 \
  --strike-band-pct 10 --workers 1 --label warm-cache-pool
```

**Resuming:** if either command is interrupted, re-run the SAME command — every ticker
already fetched is a negative/positive cache hit (WR-04) and costs zero requests; only the
un-fetched remainder re-hits the API. **Reading progress:** `_ensure_bars` prints one
`[fetch] {underlying} +{n} contracts ({total} loaded)` line to stderr per call when more than
20 new tickers are fetched in one `rows_for` call — watch stderr (or redirect it to a file)
to see the run advancing.

**Step 2 — the six hypothesis arms**, each run twice (IS then OOS), SPY-only shown (repeat
with `--symbols` widened to the 6-ETF pool if SPY alone does not clear the evidence floor):

```bash
# H1 (entry.ivr_min 30 vs 20)
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --label H1-IS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --set entry.ivr_min=20 --label H1-IS-ArmB
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --label H1-OOS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --set entry.ivr_min=20 --label H1-OOS-ArmB

# H2 (structure.short_delta 0.20 vs 0.16) -- same --start/--end pairs, swap the --set
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --label H2-IS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --set structure.short_delta=0.16 --label H2-IS-ArmB
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --label H2-OOS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --set structure.short_delta=0.16 --label H2-OOS-ArmB

# H3 (structure.type iron_condor vs put_credit_spread)
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --label H3-IS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67 --strike-band-pct 10 --set structure.type=put_credit_spread --label H3-IS-ArmB
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --label H3-OOS-ArmA
python3 -m backtester.options_run --symbols US.SPY --start 2025-08-01 --end 2026-06-15 --iv-warmup-days 67 --strike-band-pct 10 --set structure.type=put_credit_spread --label H3-OOS-ArmB
```

All 12 of these reuse the Step 1 cache and should complete in seconds to minutes each (cache
hits only, assuming Step 1 already ran to completion). Each run's `summary.json` now also
carries `assumptions.open_positions_at_end` and `assumptions.end_of_window_pnl_usd`
(T-09-14/CR-01) — a position still open at `--end` is settled and included in the reported
PF/Sortino/win-rate (marked, not realized-via-broker), never silently dropped; check this
field is `0` (or small) before trusting a window's numbers, since a large residual would mean
the window ended mid-cycle for several positions.

### Historical eager-design measurements (T-09-11, superseded by the corrected root cause above, kept for provenance)

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
| 6 | Per-contract fetch rate measurement (eager design, pre-09-05) | US.SPY | 40 distinct call contracts, IS-window range | 450.8s for 40 contracts, apparent 5.32 req/min sustained (live fetch, no 429s observed) — later understood (T-09-15) to be the 5-req/min CAP, not a gradual rate. |
| 7 | `baseline-IS-probe` attempt (full plan-literal step 1: `--start 2024-11-18 --end 2025-07-31 --iv-warmup-days 67`, eager design) | US.SPY | IS window | **ABORTED after 5m38s** — stalled at the contracts-reference pagination / first per-contract fetch (0 `O_*` cache files created, 0% CPU, socket in CLOSE_WAIT). Killed; superseded by the rate measurement (step 6) and the smoke run (step 8) as the actual evidence. |
| 8 | `smoke-real-data` — end-to-end pipeline proof (eager design) | US.SPY | 2025-06-12 to 2025-06-13 (1-day IV warm-up, `--strike-band-pct 1.0`, `--set entry.min_dte=1 --set entry.max_dte=10`) | **COMPLETED, exit 0.** 330 real contracts fetched in ~57 minutes (~5.8 req/min sustained). `trades.csv`/`summary.json`/`config.json` all present at `backtester/results/options/smoke-real-data/` (gitignored, not committed — D-16). `total_trades: 0` **by design**: `--iv-warmup-days 1` is far below `IV_RANK_MIN_OBS=60`, so `iv_rank` returns `None` for both decision days and `passes_entry_gate` fails closed on every symbol/day (`bot/options/strategy.py:94-96`) — this run's purpose was proving the CLI wiring against live data (success_criteria #1), not evaluating a hypothesis. |

#### Strike-band-narrowed candidate counts, IS window (`entry.min_dte=30`/`max_dte=60`, `expiry <= 2025-09-29`), EAGER design, computed offline from the cached contracts JSON (zero additional API cost) — superseded, kept for provenance

| Strike band | Underlying close range used | Narrowed candidate count |
|---|---|---|
| ±20% (CLI default at the time) | $496.48-$637.10 -> band $397.18-$764.53 | 79,750 |
| ±10% | -> band $446.83-$700.81 | 72,310 |
| ±5% | -> band $471.66-$668.96 | 65,036 |
| ±2% (tightest reasonable) | -> band $486.55-$649.84 | 58,366 |

These counts were produced by the EAGER design (fetch every strike-band candidate x every
expiry up front) that Plan 09-05 replaced with the lazy `rows_for` design (see "Corrected
root cause" above) — they are no longer the operative projection, kept here only as the
historical record of what T-09-11 actually measured.

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
- **Limitation, corrected in Plan 09-05 (T-09-15):** T-09-11 (eager data layer) measured the
  Massive per-contract-bar architecture (D-05) as computationally INFEASIBLE to backfill for
  SPY at the free-tier rate limit (~183h for one IS window alone). Plan 09-05's lazy
  `rows_for` fetch reduces that to a BOUNDED ~25h once-ever cost for SPY's entire entitled
  history (all arms/windows share the same cache afterward) — this is now a wall-clock
  scheduling decision for the operator (run once, overnight or across a few sessions), not a
  structural blocker. The 5-req/min server-side tier cap itself (not a gradual rate) is
  unchanged and is the true limiting factor; a paid Massive options tier removes it entirely
  (~5 minutes for SPY with `--workers 8`, see the runtime-projections table above).

## What this does NOT tell us

This run gives **zero evidence, in either direction**, about whether a looser IVR gate, a
farther-OTM short strike, or a put-credit-spread structure would have performed better or
worse than the live `rules_options.json` baseline over 2024-11-18 through 2026-06-15. It also
does not tell us the pipeline is broken — the opposite: `smoke-real-data` (historical step 8,
above) proves `backtester/options_run.py` correctly wires `MassiveDataSource` ->
`OptionChainSource` -> `backtester.options.greeks` -> `OptionsBacktestEngine` ->
`write_options_report` end to end against LIVE Massive option data, with zero modification to
`bot/options/*`, producing well-formed `config.json`/`trades.csv`/`summary.json` artifacts
(success_criteria #1, satisfied literally, at a deliberately minimal scope).

As of this revision (T-09-15), what is missing is no longer "the ability to acquire enough
real contract-level price history within any practical wall-clock budget" — the lazy fetch
design bounds that to ~25h once for SPY (~2-3 days for the 6-ETF pool) on the free tier, per
the "Operator commands to close this gap" section above. What is missing is simply that
those commands have not yet been run: this plan is offline-only by its own scope (no live
Massive fetch), so the operator must run Step 1 (cache warm) then Step 2 (the twelve arm
invocations) to produce the pre-registered hypothesis matrix on real data. The two concrete
paths are: (a) run overnight/across a few sessions on the free tier (SPY first, then widen to
the pool only if SPY doesn't clear the 25-trade evidence floor), or (b) upgrade to a paid
Massive options tier for a month (~5 minutes total with `--workers 8`) — either closes this
gap without any further code change.

## Config decision

**`rules_options.json` is UNCHANGED.** Per the pre-registration's own rule (*"`rules_options.json`
changes AFTER this backtest's results exist, and only for a hypothesis whose verdict is
SUPPORTED"*) and this plan's action text (*"If and ONLY if a hypothesis is SUPPORTED ... edit
the single corresponding value ... Otherwise leave `rules_options.json` untouched"*): no
hypothesis reached SUPPORTED (or REJECTED — both require a completed backtest), so no edit is
made. `git diff --exit-code rules_options.json` is clean. `bot/options/*` is unmodified.
