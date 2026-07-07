---
phase: 06-backtester
verified: 2026-07-07T03:20:01Z
status: gaps_found
score: 1/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Entries occur at bar N+1 open (not bar N close); gap, SMA200, premarket high, and RVOL are computed point-in-time with no look-ahead; a synthetic dataset with a known ahead-only signal confirms no premature entry"
    status: failed
    reason: "N+1-open fill logic itself is correct (SimulatedExecution.consume_intent uses feed.next_bar, never intent.entry_price) and proven by the single-day ahead-only fixture test. But three independent, code-confirmed look-ahead defects break point-in-time correctness the moment a backtest spans more than one day — the normal/expected use of a backtester: (1) run.py calls harness.setup_day() for every requested day BEFORE asyncio.run(harness.run()) replays any of them; SignalEngine.set_premarket_highs() unconditionally REPLACES the stored dict (docstring: 'Replaces any previously stored highs'), so by the time run() replays day 1, Gate 1 evaluates day 1's bars against day N's (the LAST day's) premarket highs — day 1's entries are gated by data that does not exist yet on day 1 (CR-01, confirmed by reading run.py:146-150 + harness.py:173 + signal_engine.py:93-99). (2) SimulatedBarFeed.next_bar has no same-session boundary check (feed.py:202-207) — a signal on the last bar of day D fills at day D+1's 09:30 open, an overnight-gap fill the live bot's 20s-TTL D-05 abandon path could never produce, and the same unbounded lookup lets stop-out exits roll into the next morning (CR-04). (3) synthetic_today_price (feed.py:227-244) builds the daily-filter TodayPrice from day_bars[-1]['close'] (the day's ~16:00 close) and the day's FULL-SESSION max high — direct look-ahead into the scan decision versus the live 08:30 premarket scan, confirmed by direct code read (CR-07)."
    artifacts:
      - path: "backtester/harness.py"
        issue: "setup_day (line 173) unconditionally calls signal_engine.set_premarket_highs(), which REPLACES the global dict; run.py's setup-all-then-replay-all ordering (run.py:146-150) means only the last requested day's premarket highs survive for the entire multi-day replay"
      - path: "backtester/feed.py"
        issue: "next_bar() (lines 202-207) has no same-session-date constraint, so entry/exit fills silently cross into the next calendar day's open; synthetic_today_price() (lines 227-244) uses the day's LAST bar close and FULL-day max high, not a point-in-time premarket-only value"
    missing:
      - "Per-day premarket-high freeze: store premarket highs keyed by day in setup_day and call signal_engine.set_premarket_highs(highs_for_day) inside replay_day(day) immediately before that day's bar loop, not once globally before all days replay"
      - "Same-session constraint in next_bar(code, after): return None (not the next day's first bar) when the next chronologically-later bar's date differs from after's date"
      - "Point-in-time synthetic_today_price built from premarket-only bars (mirrors resolve_today_price's premarket branch), never day_bars[-1] (EOD close) or a full-day max high"
      - "A multi-day regression test (2+ trading days) proving day 1's Gate 1 evaluates against day 1's own premarket high, not day 2's, and that a signal on day 1's last bar does not fill on day 2's open"
  - truth: "The performance report is written to disk and includes win rate, average R-multiple, max drawdown, profit factor, and a per-trade CSV with entry/exit price, quantity, and exit reason"
    status: failed
    reason: "compute_metrics/write_report themselves are correct and unit-tested against known fixtures (backtester/report.py, tests/backtester/test_report.py) — the formulas and CSV/JSON shape are sound. The failure is upstream: the harness never ports the live 15:51 ET force-close, so (a) a position still open at a replay day's last bar carries into the next day and is managed against the wrong session's bars (compounding CR-04's cross-session fill bug), and (b) a position still open when the entire replay ends never reaches PositionPhase.CLOSED, so _capture_closed_trades (harness.py:299-339) never records it — its realized P&L (including any completed partial legs) is silently absent from trades.csv and every metric in summary.json (CR-05, confirmed: replay_day/run in harness.py lines 181-199 contain no force-close call of any kind). A related fabrication (WR-01): when SimulatedExecution.manage_exit's next_bar lookup returns None on a genuine stop-out at the dataset's final bar, it still returns int(qty) (a 'full fill') with exit_price=None appended to exit_fills (execution.py:89-97); _capture_closed_trades's total_qty>0 guard only counts priced fills, so this specific path is defensively absorbed by the pos.entry_price fallback — but it still marks the position CLOSED via PositionManager's fill-quantity check, again outside what the live 'return 0, stay open' contract intends."
    artifacts:
      - path: "backtester/harness.py"
        issue: "replay_day()/run() (lines 181-199) never invoke a force-close-all-positions step at end-of-day or end-of-replay; positions open at range end are dropped from trade_log entirely, silently under/overstating win rate, avg R, drawdown, and profit factor for any backtest that ends with open risk"
    missing:
      - "An end-of-day (and end-of-run) force-close step in the harness that calls the equivalent of PositionManager.force_close_all with the replay clock pinned to the day's final bar time, so every position reaches CLOSED and is captured into trade_log"
      - "A test asserting zero non-CLOSED positions remain after run() completes, and that a position open at the last replayed bar appears in trades.csv"
  - truth: "Historical 5m data is sourced from yfinance (or a flat CSV/Parquet export) rather than Moomoo (BT-04), avoiding broker historical-quota limits; the loader handles yfinance's 5m date-range window (≈60 days) and the strategy's ticker-format normalization"
    status: failed
    reason: "Data is genuinely sourced from yfinance, never Moomoo (confirmed: backtester/feed.py imports only bot.scanner.fetcher/bot.scanner.universe, no broker gateway import anywhere in backtester/), and yfinance_to_moomoo ticker normalization is reused correctly. But 'the loader handles yfinance's 5m date-range window (~60 days)' is false as implemented: feed.py declares INTRADAY_5M_WINDOW_TRADING_DAYS = 58 and only raises BacktestWindowError for --start older than that (feed.py:51-56, 136-147), yet the actual data fetch (bot.scanner.fetcher.download_intraday_5m, confirmed at bot/scanner/fetcher.py:546-575) hardcodes {period: '30d', interval: '5m', prepost: False} -- ~21 trading days, not 58. A --start between ~21 and ~58 trading days back passes the window guard, the download 'succeeds', and every day in that gap silently replays zero bars (zero entries, no error) -- this is exactly the silent-clipping failure BacktestWindowError's own docstring promises never to allow ('never silently return an empty bar list for an out-of-window request'). The premarket-high fetch has the identical shape at a shorter horizon: feed.py:246-266 requests {period: '5d', prepost: True}, measured back from 'now' (fetch time) not from the requested replay day, so any replay day older than ~3-5 trading days gets an empty premarket-high dict and every entry is silently blocked by Gate 1 (CR-02/CR-03, both confirmed by direct comparison of feed.py's window constant against fetcher.py's actual download_kwargs)."
    artifacts:
      - path: "backtester/feed.py"
        issue: "INTRADAY_5M_WINDOW_TRADING_DAYS=58 (line 55) does not match the ~21-trading-day window actually downloaded via download_intraday_5m's hardcoded period='30d' (bot/scanner/fetcher.py:575); premarket_highs() (feed.py:258-266) fetches period='5d' measured from 'now', not from the requested replay day, so replay days older than ~1 week silently get zero premarket highs"
    missing:
      - "Either lower INTRADAY_5M_WINDOW_TRADING_DAYS to match the real ~21-trading-day fetch, or have the feed request yfinance's true 5m maximum (period=\"60d\") via its own _download_batch call instead of the 30d scanner wrapper"
      - "A per-requested-day coverage check after loading (cache or network): every NYSE trading day in [start, end] must have at least one bar for at least one symbol, else raise BacktestWindowError naming the uncovered days -- converts the current silent zero-bar/zero-premarket-high failure into the loud failure the module already promises"
human_verification: []
---

# Phase 6: Backtester Verification Report

**Phase Goal:** An offline CLI tool replays historical 5m data through the exact same StrategyCore and PositionState FSM used by the live bot and produces a performance report, confirming live/backtest code parity.
**Verified:** 2026-07-07T03:20:01Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Running `backtester/run.py` imports `bot/strategy/`/`bot/position/` unchanged and reads the same `rules.json` | VERIFIED | `backtester/harness.py` imports `TrendJoinLong`, `PositionManager`, `PositionPhase`/`PositionState` directly from `bot.strategy`/`bot.position` with zero modification; `backtester/run.py:29,121` calls the identical `bot.config.loader.load_strategy_config` that `bot/main.py:21,65` uses |
| 2 | Bar N+1-open entries; gap/SMA200/premarket-high/RVOL computed point-in-time, no look-ahead | FAILED | N+1-open fill mechanic itself correct and proven single-day; but confirmed multi-day look-ahead in 3 independent places: CR-01 (premarket highs globally replaced to the LAST requested day before any day replays — `run.py:146-150`, `harness.py:173`, `signal_engine.py:93-99` docstring "Replaces any previously stored highs"), CR-04 (`feed.py:202-207` `next_bar` has no same-session guard — fills cross into next day's open), CR-07 (`feed.py:227-244` `synthetic_today_price` uses the day's LAST bar close + full-day max high) |
| 3 | Performance report written to disk: win rate, avg R, max drawdown, profit factor, per-trade CSV | FAILED | `backtester/report.py` metric formulas and CSV/JSON shape are correct and unit-tested, but `backtester/harness.py:181-199` never force-closes positions at day/run end (CR-05) — positions open at replay end never reach `CLOSED` and are silently dropped from `trade_log`/`trades.csv`/`summary.json`, corrupting win rate, avg R, drawdown, and profit factor for any run ending with open risk |
| 4 | 5m data from yfinance (not Moomoo); loader handles yfinance's ~60-day 5m window + ticker normalization | FAILED | Source is genuinely yfinance-only (no broker import anywhere in `backtester/`) and `yfinance_to_moomoo` normalization is reused correctly, but the claimed 58-trading-day window (`feed.py:55`) does not match the actual ~21-trading-day fetch (`bot/scanner/fetcher.py:575`, `period="30d"`) — requests in the 21-58 trading-day gap pass the window guard and silently replay zero bars (CR-02/CR-03); `premarket_highs()`'s separate `period="5d"` fetch (`feed.py:262`) similarly zeroes out for any replay day older than ~1 week |

**Score:** 1/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backtester/feed.py` | SimulatedBarFeed 5m replay + CSV cache, window guard | ⚠️ HOLLOW (partial) | Exists, substantive, wired, single-day tests pass — but window guard constant (58 trading days) does not match actual fetch window (~21 trading days); `next_bar` crosses session boundaries; `synthetic_today_price` look-ahead |
| `backtester/execution.py` | SimulatedExecution N+1-open fills, SimulatedGateway stub | ✓ VERIFIED (mechanic correct) | `consume_intent`/`manage_exit` correctly use `next_bar`, never `intent.entry_price`; WR-01 (manage_exit "fills" a stop-out with `exit_price=None` when no next bar exists) is a minor defensive-absorption issue, not a blocker on its own |
| `backtester/report.py` | compute_metrics + CSV/JSON writers | ✓ VERIFIED (formulas correct) | Metric math and file writes are correct against the data given to them; upstream trade-log completeness is the actual defect (see harness.py) |
| `backtester/harness.py` | BacktestHarness: construction + per-day setup + replay loop + clock control + trade-log capture | ✗ STUB (multi-day path) | Single-day replay wiring (clock rebind, bar_buffer, reused pipeline construction) is genuinely correct and tested; the multi-day/session-boundary/EOD-close/watchlist-enforcement behavior implied by "replays historical 5m data" is absent |
| `backtester/run.py` | CLI entry point | ✓ VERIFIED | argparse, config load + ConfigError→exit(1), live-DB collision guard, V5 input validation, `feed→harness→write_report` wiring all present and tested; does not itself introduce new look-ahead bugs beyond calling `setup_day` for every day before any replay (the CR-01 root cause) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `backtester/harness.py` | `bot.position.manager.PositionManager` | `gateway=None` construction | WIRED | Confirmed at `harness.py:99-106` |
| `backtester/harness.py` | `bot.signal.signal_engine.now_et` | per-bar clock rebind | WIRED | Confirmed at `harness.py:193-199`, `_process_bar` sets `self._replay_clock` before any gate read |
| `backtester/harness.py` | `bot.scanner.scanner._evaluate_symbol`/`_compute_tod_baselines` | point-in-time baseline reuse | WIRED | Confirmed at `harness.py:56,142,161` |
| `backtester/run.py` | `bot.config.loader.load_strategy_config` | same rules.json loader as live bot | WIRED | Confirmed at `run.py:29,121` matches `bot/main.py:21,65` |
| `backtester/harness.py` (watchlist) | `bot.signal.signal_engine` entry gates | daily D1/D2/D3/SMA200/top-20 filter enforcement | NOT_WIRED | `setup_day` persists a ranked/capped watchlist (`harness.py:165-171`) but `SignalEngine.on_bar`'s Gates 1-7 (`signal_engine.py:483-642`) contain no watchlist-membership check — every `--symbols` code trades regardless of daily filter results (CR-06) |

### Anti-Patterns Found

No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers found in `backtester/*.py` or `tests/backtester/*.py`. The defects found are logic bugs (control-flow ordering, missing session boundary, missing force-close, missing watchlist gate, mismatched window constants), not debt markers or stubs in the literal sense — they are, however, exactly the kind of "invisible to single-day test fixtures" correctness gap the adversarial code review (`06-REVIEW.md`) was built to catch, and I independently re-traced and confirmed all 7 Critical findings by reading the actual source (not by trusting the review's prose):

| File | Finding | Severity | Confirmed |
|------|---------|----------|-----------|
| `backtester/harness.py:173`, `backtester/run.py:146-150` | CR-01: global premarket-high dict clobbered to last day before replay starts | BLOCKER | Yes — read `set_premarket_highs` docstring ("Replaces any previously stored highs") + `run.py`'s setup-all-then-replay-all loop |
| `backtester/feed.py:258-266` | CR-02: premarket-high fetch `period="5d"` measured from "now", not replay day | BLOCKER | Yes |
| `backtester/feed.py:55-56` vs `bot/scanner/fetcher.py:575` | CR-03: window guard claims 58 trading days, real fetch is `period="30d"` (~21 trading days) | BLOCKER | Yes — compared both constants directly |
| `backtester/feed.py:202-207` | CR-04: `next_bar` has no same-session-date constraint | BLOCKER | Yes — read the loop, confirmed no date check |
| `backtester/harness.py:181-199` | CR-05: no end-of-day/end-of-run force-close | BLOCKER | Yes — read `replay_day`/`run`, confirmed no force-close call exists anywhere in the module |
| `backtester/harness.py:125-175`, `bot/signal/signal_engine.py` | CR-06: persisted watchlist never gates replay entries | BLOCKER | Yes — grepped `signal_engine.py` for a watchlist/rank check across all 7 gates; none exists |
| `backtester/feed.py:227-244` | CR-07: `synthetic_today_price` built from day's last bar close + full-day max high | BLOCKER | Yes — read the function body directly |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|-----------------|--------------|--------|----------|
| BT-01 | 06-01, 06-05, 06-06 | Backtester replays historical 5m data through the exact same StrategyCore + PositionState FSM as the live bot | ⚠️ PARTIAL | Single-bar plumbing reuses the unmodified live pipeline correctly; multi-day "replay" is broken by CR-01/04/05/06, so the requirement's literal claim ("replays historical 5m data") does not hold beyond a single trading day |
| BT-02 | 06-01, 06-03, 06-05, 06-06 | Enters at bar N+1 open (no look-ahead); gap/SMA200/premarket-high/RVOL computed point-in-time | ✗ BLOCKED | CR-01, CR-04, CR-07 are confirmed, active look-ahead defects |
| BT-03 | 06-01, 06-04, 06-06 | Produces a performance report (win rate, avg R, max drawdown, profit factor, per-trade CSV) | ⚠️ PARTIAL | Report math/format correct; upstream trade capture is incomplete for any run with positions open at range end (CR-05) |
| BT-04 | 06-01, 06-02, 06-06 | Backtest historical data sourced from yfinance, not Moomoo, avoiding broker historical-quota limits at 500-symbol scale | ⚠️ PARTIAL | Source is correctly yfinance-only; the advertised ~58-trading-day window is fictional (CR-02/CR-03), silently producing empty results for a documented, in-scope portion of that window |

No orphaned requirements — all four IDs (BT-01..04) mapped to REQUIREMENTS.md are claimed across the six phase plans; `REQUIREMENTS.md` lines 86-89 mark all four `[x]` complete, but that checkbox state was set before the 2026-07-07 code review found the above defects and predates this verification.

### Behavioral Spot-Checks

`python3 -m pytest tests/backtester/ -q` → **28 passed in 0.47s**. All 28 tests exercise only single-day (2026-06-01) scenarios — confirmed by grepping `tests/backtester/test_harness.py` and `tests/backtester/test_run.py` for date/day fixtures; no multi-day dataset exists anywhere in the test suite, so none of the 7 Critical findings above are caught by the green suite. A passing test suite is not evidence against these gaps — it is evidence that the test suite's scope is single-day only.

### Probe Execution

SKIPPED — no `scripts/*/tests/probe-*.sh` convention exists in this project and none is referenced in the phase's PLAN/SUMMARY files.

### Human Verification Required

None. Every finding above was confirmed by direct source-code inspection (not inference from SUMMARY.md or the code-review's prose) — the control-flow bugs (setup-all-then-replay-all ordering, unbounded `next_bar`, missing force-close, missing watchlist gate, mismatched window constants) are deterministic and require no runtime/visual/timing judgment to verify.

### Gaps Summary

The phase goal statement is "An offline CLI tool replays historical 5m data through the exact same StrategyCore and PositionState FSM ... and produces a performance report, confirming live/backtest code parity." The single-day mechanics genuinely work and are well-tested: N+1-open fills, the replay-clock rebind, the reused unmodified `bot/strategy`/`bot/position` construction, and the report's metric formulas are all sound. But "replays historical 5m data" necessarily implies multi-day ranges — that is the entire point of a backtester and is the explicit dependency Phase 7 (07-06, "Exit-model SELECTION gate ... backtest comparison requires the Phase 6 backtester") is waiting on. Every multi-day defect the 2026-07-07 code review flagged as Critical was independently re-confirmed here by reading the actual source: premarket highs are silently wrong for every day but the last one requested (CR-01), fills and stop-outs roll across session boundaries into the next morning (CR-04), positions carry overnight with no EOD force-close and vanish from the report if still open at run end (CR-05), the daily D1/D2/D3/SMA200/top-20 filter layer is computed and persisted but never enforced during replay (CR-06), the scan-decision TodayPrice uses full-day look-ahead data (CR-07), and the advertised ~58-trading-day yfinance window is a fiction that silently produces zero-trade backtests for a documented, in-scope date range (CR-02/CR-03). The code reviewer's own conclusion stands after independent verification: "Any strategy conclusion drawn from a multi-day run of this backtester as-is would be invalid." These are BLOCKER gaps — the phase goal (a backtester that validates live/backtest parity and produces a trustworthy performance report) is not achieved for the tool's actual intended use.

None of these gaps are addressed by any later milestone phase — Phase 7's remaining work (07-06) *consumes* the Phase 6 backtester's output for a decision gate; it does not fix the backtester itself. No deferred items apply.

---

_Verified: 2026-07-07T03:20:01Z_
_Verifier: Claude (gsd-verifier)_
