---
slug: premarket-scan-abort-stale
status: awaiting_human_verify
trigger: "Fix two equity-scanner production bugs found in screen validation 2026-10-03. BUG 1 (critical): Premarket scan (08:30 ET) aborts with ScanDegradationError on 17/19 trading days since 2026-09-08 (logs/bot.log*: intraday_scan_aborted_data_degradation, 14-32% failed of 503). Root cause: bot/scanner/fetcher.py _detect_failed counts all-NaN 1m frames as download failures; at 08:30 many thin-premarket S&P names (AIZ, FRT, GPC, MTB, PNW...) simply have no premarket prints yet, tripping the 10% gate in download_intraday_1m. yfinance 1.4.1 unchanged since Jun 23. Proposed fix: in premarket phase the 1m degradation gate should only fire on whole-universe failure (scanner already has the WR-01 whole-universe backstop in bot/scanner/scanner.py _compute_candidates ~line 450 anticipating a high/disabled threshold). BUG 2 (high): Stale cross-day state on empty-watchlist days. (a) bot/service/bot.py _job_market_open_subscribe (~line 710) skips fetch_premarket_highs when watchlist is empty, so SignalEngine._premarket_highs keeps yesterday's dict; fetch_and_merge_premarket_highs (bot/signal/signal_engine.py ~281) then skips codes already present, so a code re-qualifying today keeps yesterday's premarket high for I1. (b) Yesterday's K_5M subscriptions are never released, so off-watchlist leftovers are evaluated all day (blocked only incidentally by per-date TOD baseline: signal_skipped_no_tod_baseline 77x/code on 09-25, 09-30, 10-02). (c) Rescan-added codes get HOD/LOD/session-volume seeded from first post-subscribe push (bot/signal/bar_aggregator.py ~224), no backfill of 09:30->subscribe bars — I2 looser, LOD-1% stop tighter (oversized positions), RVOL numerator undercounted. Requirements: TDD (failing test first), root-cause fixes, full suite green, paper-only safety invariants preserved."
created: 2026-10-03
updated: 2026-10-03
---

# Debug Session: premarket-scan-abort-stale

## Symptoms

- **Expected behavior:**
  1. The 08:30 ET premarket scan (`run_daily_scan` → `_compute_candidates`) completes every trading day and persists a gap-ranked top-20 watchlist. Symbols with no premarket 1m prints yet are simply skipped (`symbol_skipped_no_intraday_price`), not treated as a data outage. A genuine whole-universe yfinance outage must still fail loudly.
  2. Each trading session starts with clean per-session signal state: `SignalEngine._premarket_highs` holds ONLY today's frozen premarket highs (never a prior day's), and K_5M subscriptions left over from prior sessions that are not on today's watchlist and not managed (open position / pending intent) are released so they are not evaluated.
  3. A code subscribed mid-session by the intraday rescan has session HOD / LOD / cumulative session volume that reflect the WHOLE regular session from 09:30 ET, not just bars pushed after the subscribe call.
- **Actual behavior:**
  1. 17 of 19 trading days since 2026-09-08 the premarket scan raised `ScanDegradationError` ("Intraday data degradation: N/503 symbols failed", N = 73–159, 14.5–31.6%) → `premarket_scan_error`; no premarket watchlist, no 09:30 subscriptions; watchlist only appears at the 09:55+ rescans. Only 09-17 and 09-18 succeeded. Failing names are consistently thin-premarket ones (AIZ, FRT, GPC, MTB, PNW, RJF, UHS, URI, VLTO, AFL, CBRE, …). Before 09-08 (08-24..09-04) the scan succeeded daily and these names surfaced as `symbol_skipped_no_intraday_price` (59 events) — i.e. upstream started returning them all-NaN instead of stale, flipping them from "skipped" to "failed".
  2. On empty-watchlist days the market-open job logs `market_open_subscribe_empty_watchlist` and never calls `fetch_premarket_highs`/`set_premarket_highs`, so yesterday's `_premarket_highs` survive. Yesterday's subscribed codes keep pushing K_5M bars all day; on 2026-09-25 (US.P), 09-30 (AMAT, BE, GLW, KLAC, LRCX — all rescan-added 09-29) and 10-02 (IT, MCK, NOW — rescan-added 10-01) each was evaluated on every bar and logged `signal_skipped_no_tod_baseline` 77×/code. The per-date TOD-baseline key is the ONLY thing stopping a trade on an off-watchlist name with a stale premarket high. If such a code re-qualifies in today's rescan, `fetch_and_merge_premarket_highs` skips it (already present) → I1 runs against YESTERDAY's premarket high.
  3. `BarAggregator` seeds `_hod/_lod` from the first post-subscribe push and `_session_volume` from 0 (bar_aggregator.py ~224/~294) — no backfill. For a code added at 09:55, I2 compares against a partial HOD (looser), the initial stop LOD−1% uses a partial LOD (tighter → larger position up to the 10% cap), and RVOL-TOD's cumulative-volume numerator omits 09:30–09:55 volume vs a full-session baseline (stricter). On premarket-abort days this applies to 100% of watchlist codes.
- **Error messages:** `ScanDegradationError: Intraday data degradation: 142/503 symbols failed (28.2% >= 10% threshold)` raised from `bot/scanner/fetcher.py::_download_batch` via `download_intraday_1m`, caught in `bot/service/bot.py::_job_premarket_scan` as `premarket_scan_error`. Bug 2 is silent (no errors).
- **Timeline:** Bug 1 since 2026-09-08 (yfinance 1.4.1 installed 2026-06-23, unchanged → Yahoo-side change). Bug 2(a)/(b) latent since market-open seeding / rescan subscribe were written; exposed daily once Bug 1 empties the premarket watchlist. Bug 2(c) latent since intraday rescan subscribes were added (always affected rescan-added codes).
- **Reproduction:**
  1. Unit-level: feed `download_intraday_1m` / `_compute_candidates` a 1m batch where >10% of symbols are all-NaN (no premarket prints) at a premarket `now_et` (e.g. 08:30 ET) → `ScanDegradationError` today; expected: scan completes, those symbols skipped. Whole-universe all-NaN must still raise.
  2. Unit-level: SignalEngine with day-1 premarket highs frozen; day-2 market-open job with an empty watchlist → `_premarket_highs` still contains day-1 values. Then `fetch_and_merge_premarket_highs([code_from_day1])` → no refresh.
  3. Unit-level: BarAggregator receives first push for a code at 09:55 → `hod`/`lod`/`cum_volume` exclude 09:30–09:55 bars.
  Live evidence: main repo `logs/bot.log`, `logs/bot.log.1` .. `.5` (events `intraday_scan_aborted_data_degradation`, `premarket_scan_error`, `fetch_retry_attempt`, `signal_skipped_no_tod_baseline`, `tod_baseline_stored`, `rescan_complete`).

## Constraints

- Work in THIS worktree: /Users/acdc/Documents/AI/ai-snp-trading-claude/.claude/worktrees/intelligent-knuth-f27d36 (branch `claude/awesome-gould-a0cef9`, fast-forwarded to local develop @5a30a28). Commit atomically on this branch; do NOT merge into develop, do NOT push, do NOT restart the bot, do NOT touch the main checkout or production logs/DB.
- TDD: a failing test first for each fix (red → green). Full suite `python3 -m pytest -q` must be green at the end (baseline before changes: run it first and record count).
- Safety invariants unchanged: paper-only (`FUTU_TRD_ENV=SIMULATE`, paper guard), no `unlock_trade`, fail-closed behavior on genuinely missing data, a whole-universe yfinance outage must still abort the scan loudly.
- Moomoo/OpenD API facts: K_5M subscription quota is limited (20-name watchlist cap, SIG-01); `get_cur_kline(code, num, ktype)` requires an active subscription to that ktype but costs no history quota; moomoo rejects unsubscribe within 60s of subscribe. Use the gateway's existing async/executor patterns (bot/gateway/gateway.py). The options bot (`bot/options/`) must be unaffected.
- Keep diffs minimal and in the codebase's style (CLAUDE.md conventions); fix shared functions once rather than patching each caller.

## Current Focus

- hypothesis: (1) the 1m degradation gate conflates "no premarket prints yet" with "download failed"; (2a) `_premarket_highs` is never reset per session when the watchlist is empty; (2b) no market-open release of stale non-managed subscriptions; (2c) no intraday backfill of session HOD/LOD/volume for codes subscribed after 09:30.
- test: per Reproduction 1–3 above.
- expecting: each reproduction test fails against current code.
- next_action: all four fixes committed on claude/awesome-gould-a0cef9 (d2720f1, 568e310, 6cda8cc, 9bf3222); awaiting operator merge to develop + restart + live UAT (not performed here by constraint)

## Evidence

- timestamp: 2026-10-03 (baseline)
  checked: `python3 -m pytest -q` in worktree (bot imported from worktree path, verified via `bot.__file__`)
  found: 1373 passed, 1 skipped (55.7s)
  implication: baseline for before/after counts.
- timestamp: 2026-10-03 (bug 1 RED)
  checked: tests/scanner/test_scanner.py::TestPremarket1mDegradationGate (real download_intraday_1m, fake yf.download, 25/100 all-NaN frames, now_et=08:30 ET)
  found: `ScanDegradationError: Intraday data degradation: 25/100 symbols failed (25.0% >= 10% threshold)` raised from fetcher._download_batch; whole-universe-outage and 09:55-rescan guard tests already pass (they pin behaviour that must not change).
  implication: root cause confirmed — the premarket 10% gate conflates all-NaN "no prints yet" with download failure.
- timestamp: 2026-10-03 (bug 1 GREEN)
  checked: same tests after fix (scanner passes degradation_threshold=1.0 to download_intraday_1m when now_et < RTH_OPEN) + full suite
  found: 3/3 green; full suite 1376 passed, 1 skipped (+3 new tests, 0 regressions)
  implication: partial all-NaN premarket names are skipped (symbol_skipped_no_intraday_price); whole-universe failure still raises (fetcher gate at rate 1.0, audited) and 09:30+ rescans keep the 10% gate.
- timestamp: 2026-10-03 (bug 2a RED)
  checked: tests/service/test_bot.py::test_market_open_empty_watchlist_clears_prior_session_premarket_highs / ..._requalifying_code_gets_todays_high / test_market_open_failed_seed_leaves_no_prior_session_highs (real SignalEngine, day-1 highs {US.IT: 111.0}, run _job_market_open_subscribe)
  found: `stale highs survived: {'US.IT': 111.0}`; re-qualifying merge returned {} instead of {US.IT: 130.0}; a raising seed also leaves {'US.IT': 111.0}
  implication: confirmed — reset only happened inside fetch_premarket_highs, i.e. only on the non-empty + non-raising path.
- timestamp: 2026-10-03 (bug 2a GREEN)
  checked: bot/service/bot.py _job_market_open_subscribe now calls signal_engine.set_premarket_highs({}) right after bar_agg.reset_session(), before the watchlist read/seed
  found: 3/3 green; full suite 1379 passed, 1 skipped
  implication: _premarket_highs holds only today's highs on every path (empty watchlist, failed seed); Gate 1 fails closed until seeded.
- timestamp: 2026-10-03 (bug 2b RED)
  checked: tests/service/test_bot.py::test_market_open_releases_only_stale_unmanaged_subscriptions / ..._empty_watchlist_still_releases_stale_subscriptions; tests/gateway/test_gateway.py::TestGetSubscribedK5mCodes
  found: `Expected unsubscribe to have been awaited once. Awaited 0 times.`; `AttributeError: 'MoomooGateway' object has no attribute 'get_subscribed_k5m_codes'`. SDK source (moomoo query_subscription) confirms sub_list keys are SubType strings ("K_5M") and is_all_conn=False limits to own connection; SDK also keeps its own _sub_record and re-subscribes it on socket reconnect, which is why leftovers persist.
  implication: no code path ever releases a K_5M feed except the (now always-empty) rescan eviction; nothing in the bot tracks subscriptions, so OpenD's query_subscription is the authoritative source.
- timestamp: 2026-10-03 (bug 2b GREEN)
  checked: gateway.get_subscribed_k5m_codes() (query_subscription(is_all_conn=False) -> K_5M list) + TradingBot._managed_codes() (shared with the rescan worker) + TradingBot._release_stale_subscriptions(watchlist) called from _job_market_open_subscribe after the watchlist read; full suite
  found: 8/8 new tests green; full suite 1387 passed, 1 skipped. Mutation check: removing the managed-code exclusion fails the 2 release tests; removing the try/except fails the 2 fail-open tests (and the 2 legacy MagicMock-gateway open-job tests) -> guards have teeth.
  implication: stale feeds (not on today's watchlist, no open position/pending intent) are released at 09:30 ET, also on empty-watchlist days; managed codes can never be released; any failure (incl. moomoo's 60s unsubscribe lock) is logged and the job proceeds.
- timestamp: 2026-10-03 (bug 2c RED)
  checked: tests/service/test_bot.py::test_rescan_backfills_new_code_session_stats_before_merging_premarket_highs (real BarAggregator, first push at 09:55, fake get_cur_kline returning 09:30-09:40 bars), tests/signal/test_bar_aggregator.py::TestSessionBackfill (10 tests), tests/gateway/test_gateway.py::TestGetCurKline
  found: `AssertionError: HOD not backfilled: 101.0` (expected 103.0 from the 09:30 bar); `AttributeError: 'BarAggregator' object has no attribute 'backfill_session'`; `'MoomooGateway' object has no attribute 'get_cur_kline'`
  implication: symptom reproduced at the bot level (partial HOD for a rescan-added code); no backfill path exists anywhere.
- timestamp: 2026-10-03 (bug 2c GREEN)
  checked: BarAggregator.backfill_session/is_backfilled (+ threading.Lock around _handle_row/reset_session/backfill), gateway.get_cur_kline(code, num) (KLType.K_5M), TradingBot._backfill_session_stats called in _job_intraday_rescan after the rescan worker and BEFORE fetch_and_merge_premarket_highs; full suite
  found: all 14 new tests green; full suite 1401 passed, 1 skipped. Mutation matrix on the aggregator (each reverted afterwards): removing the cutoff (3 fail), push lock (1), idempotency (1), session-day guard (1), max/min merge (1), malformed-row filter (1), RTH-open filter (1, after adding a pre-open row test) -> every guard has a killing test; swapping backfill/merge order in the rescan job fails the ordering test.
  implication: rescan-added codes now carry whole-session HOD/LOD/cum_volume from the first bar they can be evaluated on; backfill is idempotent, monotonic, never double-counts the in-flight bar, and fails open.

## Resolution

root_cause: |
  Bug 1 — bot/scanner/fetcher.py::_detect_failed counts an all-NaN 1m frame as a failed download (necessarily: yfinance 1.4.1 leaves shared._ERRORS empty), and download_intraday_1m's 10% D-06 gate was applied unchanged at the 08:30 premarket scan, where since 2026-09-08 14-32% of the universe (thin-premarket names) legitimately has no prints -> ScanDegradationError on 17/19 days -> no premarket watchlist.
  Bug 2a — SignalEngine is process-lifetime but _premarket_highs was only replaced inside fetch_premarket_highs, which _job_market_open_subscribe skips for an empty watchlist (and which can raise); fetch_and_merge_premarket_highs then skips codes already present, so a re-qualifying code kept yesterday's premarket high for I1.
  Bug 2b — nothing ever releases K_5M feeds (rescan eviction is a no-op for non-managed codes since 260824-avx; the SDK re-subscribes its own record on reconnect); leftovers were evaluated all day.
  Bug 2c — BarAggregator seeds HOD/LOD from the first post-subscribe push and session volume from 0, with no backfill of 09:30 -> subscribe bars.
fix: |
  1 (d2720f1) _compute_candidates passes degradation_threshold=1.0 to download_intraday_1m when now_et < RTH_OPEN (hoisted to fetcher.RTH_OPEN): premarket aborts only on whole-universe failure (still loud + audited); rescans keep 10%.
  2a (568e310) _job_market_open_subscribe calls signal_engine.set_premarket_highs({}) right after bar_agg.reset_session(), before the watchlist read.
  2b (6cda8cc) gateway.get_subscribed_k5m_codes() (query_subscription, own connection, K_5M); TradingBot._managed_codes() (shared with rescan) and _release_stale_subscriptions(watchlist) in the open job: unsubscribe subscribed - watchlist - managed, fail-open.
  2c (9bf3222) gateway.get_cur_kline; BarAggregator.backfill_session/is_backfilled + lock; TradingBot._backfill_session_stats in _job_intraday_rescan before the premarket-high merge.
verification: |
  Full suite 1373 passed/1 skipped (baseline) -> 1401 passed/1 skipped (+28 tests, 0 regressions). Each fix RED then GREEN; mutation checks on the guards. NOT verified live (no merge/restart/OpenD allowed): live UAT pending — see report for the live-check list.
files_changed:
  - bot/scanner/fetcher.py
  - bot/scanner/scanner.py
  - bot/service/bot.py
  - bot/gateway/gateway.py
  - bot/signal/bar_aggregator.py
  - tests/scanner/test_scanner.py
  - tests/service/test_bot.py
  - tests/gateway/test_gateway.py
  - tests/signal/test_bar_aggregator.py
