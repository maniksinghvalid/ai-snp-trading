# Phase 2: Premarket Scanner - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-23
**Phase:** 02-premarket-scanner
**Areas discussed:** Constituent source, Re-scan merge logic, Data-degradation policy, Watchlist persistence

---

## Constituent / Symbol-List Source

| Option | Description | Selected |
|--------|-------------|----------|
| Wikipedia + cached fallback | `pandas.read_html` on Wikipedia S&P 500 page daily; cache to dated file; fall back to last good cache on scrape failure | ✓ |
| Hardcoded snapshot file | Checked-in list refreshed manually every 2-3 months; zero network, but drifts | |
| Moomoo get_plate_stock | Authoritative broker fetch, but spends the broker quota the yfinance split avoids | |
| Wikipedia, no fallback | Scrape each day with no cache; simplest, but a scrape failure = missed scan | |

**User's choice:** Wikipedia + cached fallback.
**Notes:** User initially answered "get from yfinance data." Clarified that yfinance has no index-constituents endpoint — it serves bars for tickers you hand it, so a separate source must produce the 500-symbol list. After that clarification the user selected the Wikipedia-scrape + dated-cache fallback. The cache lives under the gitignored `data/` dir, filename carries the snapshot date.

---

## Intraday Re-scan Merge Logic

| Option | Description | Selected |
|--------|-------------|----------|
| Re-rank union, protect active | Re-rank old+new by gap% each pass, keep top-20, but never evict an already-subscribed/active candidate | ✓ |
| Pure re-rank top-20 | Recompute top-20 from the union every pass, evict freely; simplest but churns subscriptions | |
| Sticky / append-only | First 20 of the day hold slots; new gappers only fill freed slots; most stable but can lock out late strong gappers | |

**User's choice:** Re-rank union, protect active.
**Notes:** Idempotency enforced via the existing `daily_scan UNIQUE(scan_date, code)` constraint — a re-scan is a no-op for already-present candidates and an idempotent update for rank changes.

---

## Data-Degradation Policy

| Option | Description | Selected |
|--------|-------------|----------|
| Threshold: partial+warn, else abort | Below threshold → proceed + warn; at/above → abort scan + alert, don't publish a thin watchlist | ✓ |
| Strict abort on any failure | Any failed ticker aborts; safest but too brittle at 500-symbol scale | |
| Always proceed + warn | Scan with whatever returned, always log gaps; risks a silently-thin watchlist (the failure criterion #6 warns against) | |

**User's choice:** Threshold: partial+warn, else abort. **Threshold confirmed at 10%** (abort if ≥~50 of ~500 symbols fail).
**Notes:** Follow-up question offered 10% / 5% / 20%; user chose 10% — tolerant of normal flakiness, aborts on a Yahoo-wide outage. The "alert" is a logged/surfaced event this phase; promotion to Telegram is Phase 5.

---

## Watchlist Persistence Richness

| Option | Description | Selected |
|--------|-------------|----------|
| Store richer context for Phase 3 | Extend `daily_scan` with prior-day high, prior close, SMA200, gap%, RVOL baseline, scan pass; Phase 3 reads StateStore | ✓ |
| Keep minimal (rank + gap%) | Leave schema as-is; Phase 3 re-fetches/recomputes; smaller now but duplicates computation | |
| Store as JSON blob | Full candidate dict in a JSON column; flexible but unqueryable | |

**User's choice:** Store richer context for Phase 3.
**Notes:** Explicit typed columns (not a blob) so the watchlist stays queryable. The column additions are a new ordered migration step keyed by `PRAGMA user_version` (Phase 1 D-08 pattern), not an edit to the shipped `_MIGRATION_0001`.

---

## Claude's Discretion

- Scanner module decomposition and the callable/CLI entrypoint shape (scheduling is Phase 5).
- Exact `MoomooGateway.subscribe()` signature and `SubType.K_5M` wiring (SIG-01).
- yfinance batch-download mechanics (window, per-symbol retry/backoff) and the ~5-thread concurrency primitive.
- RVOL-baseline edge cases (insufficient prior history) — conservative handling, planner specifies.
- Dated-cache filename convention and staleness-warning behavior.
- New migration `0002` vs. amending `0001` (almost certainly a new step).

## Deferred Ideas

None — discussion stayed within phase scope.
