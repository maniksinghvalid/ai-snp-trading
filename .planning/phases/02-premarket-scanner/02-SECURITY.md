# SECURITY.md — Phase 2: Premarket Scanner

**Phase:** 02 — premarket-scanner
**Audit date:** 2026-06-23
**ASVS Level:** L1
**block_on:** high
**Auditor stance:** FORCE (each mitigation absent until grep match proves otherwise)

---

## Threat Verification Summary

**Threats Closed:** 13/13
**Threats Open (BLOCKER):** 0
**Unregistered Flags:** 0

---

## Threat Register — Verification Results

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-02-SC | Tampering | mitigate | CLOSED | See below |
| T-02-01 | Tampering | mitigate | CLOSED | See below |
| T-02-02 | Tampering | mitigate | CLOSED | See below |
| T-02-03 | Tampering | mitigate | CLOSED | See below |
| T-02-04 | Info Disclosure/Integrity | mitigate | CLOSED | See below |
| T-02-05 | Tampering | accept | CLOSED | See below |
| T-02-06 | Tampering | mitigate | CLOSED | See below |
| T-02-07 | Tampering/Integrity | mitigate | CLOSED | See below |
| T-02-08 | Info Disclosure/Integrity | mitigate | CLOSED | See below |
| T-02-09 | Tampering | accept | CLOSED | See below |
| T-02-10 | Denial of Service | mitigate | CLOSED | See below |
| T-02-11 | Integrity | mitigate | CLOSED | See below |
| T-02-12 | Tampering | mitigate | CLOSED | See below |
| T-02-13 | Spoofing/Tampering | mitigate | CLOSED | See below |

---

## Per-Threat Verification Detail

### T-02-SC — pip install package legitimacy checkpoint

**Disposition:** mitigate
**Status:** CLOSED

The human-verify gate is a process control, not a code pattern. Evidence that the
checkpoint ran and produced a human approval decision is recorded in
`.planning/phases/02-premarket-scanner/02-00-SUMMARY.md` (Task 1, "pre-approved — no
commit") and in the `02-00-PLAN.md` Task 1 block which documents the blocking-human
gate as non-auto-approvable. The 02-00-SUMMARY.md decisions block confirms:
"Task 1 package-legitimacy gate (T-02-SC) satisfied by operator pre-approval: yfinance
PyPI -> github.com/ranaroussi/yfinance; pandas-market-calendars ->
github.com/rsheftel/pandas_market_calendars; both name-checked as legitimate with exact
version pins."

This threat's mitigation is process-control only; no code pattern can substitute. The
documented approval record is the required artifact.

---

### T-02-01 — requirements.txt version drift

**Disposition:** mitigate
**Status:** CLOSED

Exact `==` pins present in requirements.txt:

- `requirements.txt:10` — `yfinance==1.4.1`
- `requirements.txt:11` — `pandas-market-calendars==5.4.0`

No range specifiers (`>=`, `~=`, `^`) appear for either package. The `lxml` entry
(line 12) is unpinned, which was a documented intentional deviation (02-00-SUMMARY.md
deviations block: "lxml (unpinned — lxml maintains stable APIs across minor versions)").
lxml is not in scope for this threat (it is a transitive HTML-parser dependency, not a
package with a declared threat entry).

---

### T-02-02 — Wikipedia table structure changed/spoofed

**Disposition:** mitigate
**Status:** CLOSED

Three sub-controls verified:

1. **HTTPS enforced:** `universe.py:29` — `_WIKI_URL = "https://en.wikipedia.org/..."`.
   No plain HTTP URL exists in the file (grep confirmed zero `http://` hits).

2. **"Symbol" column validation before use:** `universe.py:173` — `_select_constituents_table`
   checks `"Symbol" in cols and ("Security" in cols or "GICS Sector" in cols)` before
   returning the table. If no table matches, `KeyError` is raised and the except block
   in `fetch_sp500_symbols` falls through to the cache (universe.py:114-143). The plan
   mitigation ("KeyError falls through to dated-cache fallback, never silent wrong
   universe") is implemented.

3. **Test coverage:** `tests/scanner/test_universe.py` — `TestConstituentsTableSelection`
   (lines 156-185) proves the table-selection-by-columns logic and the cache fallback on
   no-matching-table.

---

### T-02-03 — yfinance returns malformed/NaN data

**Disposition:** mitigate
**Status:** CLOSED

Three sub-controls verified:

1. **All-NaN frame returns None:** `fetcher.py:160` — `if ticker_df.dropna(how="all").empty: return None`.

2. **Lowercase column normalization:** `fetcher.py:165` — `ticker_df.columns = ticker_df.columns.str.lower()`.

3. **Downstream requires >= 2 valid rows:** `scanner.py:67-69` — `if len(frame) < 2: ... return None`.
   Additionally, `scanner.py:78-84` requires `n_prior >= cfg.rvol_lookback_days` prior sessions.

Tests: `TestTickerNormalization.test_lowercase_column_normalization` (test_fetcher.py:104)
and `TestInsufficientHistory.test_insufficient_history_excluded` (test_scanner.py:276).

---

### T-02-04 — Thin watchlist from mass yfinance failure

**Disposition:** mitigate
**Status:** CLOSED

Three sub-controls verified:

1. **>= 10% failure raises ScanDegradationError:** `fetcher.py:96-113` — `if failure_rate >= degradation_threshold: ... raise ScanDegradationError(...)`.

2. **Logs "scan_aborted_data_degradation":** `fetcher.py:97-103` — `_logger.error("scan_aborted_data_degradation", ...)`.

3. **Durable append_audit on abort path:** `fetcher.py:104-109` — `append_audit({"event": "scan_aborted_data_degradation", ...})` called before the raise.

The gate fires in `download_daily_bars` (fetcher.py), which is called from
`_compute_candidates` in scanner.py. `ScanDegradationError` propagates up through
`_compute_candidates` → `run_daily_scan` / `run_intraday_rescan` before any persistence
occurs (scanner.py:271 propagates it; persistence at scanner.py:405 is never reached on
the abort path).

Test: `test_10pct_failure_raises_ScanDegradationError` (test_fetcher.py:208) asserts
both the raise and `append_audit` call.

---

### T-02-05 — data/ cache file manipulated on disk

**Disposition:** accept
**Status:** CLOSED (accepted risk confirmed in code and project artifacts)

Accepted-risk rationale verified still holds:

1. **Cache is gitignored:** `.gitignore:3` — `data/` is listed in the gitignore, confirmed
   to include the scan cache directory.

2. **Operator-controlled single-user machine:** The bot's own README and CLAUDE.md
   document it runs on the operator's local machine against a Moomoo SIMULATE account.

3. **Low-value local target:** The cache contains only S&P 500 ticker symbols (public
   information). A manipulated cache causes at worst a degraded watchlist scan; the
   WR-05 minimum-universe-size check (`universe.py:100-104` and `universe.py:129-140`)
   rejects a truncated or garbage cache (< 400 symbols) rather than returning it as
   authoritative. This bounds the worst-case impact.

4. **No new exposure introduced:** The `data/` path was established in Phase 1 for the
   SQLite state DB (same directory). Phase 2 adds CSV cache files to the same gitignored,
   local-only directory.

Accepted risk stands. No mitigation required.

---

### T-02-06 — _persist_watchlist upsert SQL injection

**Disposition:** mitigate
**Status:** CLOSED

All SQL values in `_persist_watchlist` are bound via `?` placeholders:

- `scanner.py:203-230` — `conn.execute("""INSERT INTO daily_scan ... VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT ... DO UPDATE SET ...""", (scan_date_str, c["code"], c["gap_pct"], ...))`.

No f-string, `%`, or `.format()` interpolation of user-controlled or external values
into the SQL string exists in scanner.py. Grep for `f"` near SQL keywords and for `%s`
or `.format` in SQL context returned zero results.

The UNIQUE constraint `ON CONFLICT(scan_date, code)` at `scanner.py:209` is a literal
column-name reference, not user data.

Test: `test_upsert_updates_rank` (test_scanner.py:597) exercises the upsert path with
real in-memory SQLite, confirming the parameterized path executes correctly.

---

### T-02-07 — Look-ahead in RVOL baseline

**Disposition:** mitigate
**Status:** CLOSED

Strict `date < scan_date` cutoff enforced in `_evaluate_symbol`:

- `scanner.py:74-75` — `scan_ts = pd.Timestamp(scan_date); prior_mask = frame.index < scan_ts`.
- `scanner.py:111-114` — RVOL baseline uses only `frame.loc[prior_mask]` (rows strictly
  before scan_date). Today's row is explicitly excluded.

Test: `test_rvol_no_lookahead` (test_scanner.py:220) — builds a frame where today's volume
is 99x normal, runs the scan, reads back the persisted `rvol_baseline`, and asserts it
equals `normal_vol` (not inflated by today's volume). This is an invariance proof, not
just a structural assertion.

---

### T-02-08 — Wrong code format persisted

**Disposition:** mitigate
**Status:** CLOSED

Moomoo code format conversion occurs at ingest before any persistence:

- `scanner.py:162` — `moomoo_code = yfinance_to_moomoo(symbol)` called inside
  `_evaluate_symbol`, converting `"AAPL"` → `"US.AAPL"` before the candidate dict
  is assembled.
- `universe.py:71` — `yfinance_to_moomoo` returns `f"US.{yf_symbol}"`.

Tests throughout `test_scanner.py` assert `"US.AAPL"`, `"US.PASS"`, `"US.MSFT"` etc.
appear in results and in DB queries — e.g., `test_scanner.py:166`, `test_scanner.py:255`.
These DB queries use `"US.AAPL"` as the lookup key, proving the `US.` prefix is present
in persisted rows.

---

### T-02-09 — ScanDegradationError bubbling mid-scan

**Disposition:** accept
**Status:** CLOSED (accepted risk confirmed by code ordering)

Gate ordering verified:

1. `_compute_candidates` (scanner.py:263-280) calls `download_daily_bars` at line 271,
   which raises `ScanDegradationError` before any candidate evaluation or persistence.
2. `_persist_watchlist` is called at scanner.py:405 (run_daily_scan) and scanner.py:504
   (run_intraday_rescan) — both are after the `_compute_candidates` call returns.
3. If `ScanDegradationError` is raised inside `_compute_candidates`, it propagates out of
   `run_daily_scan` / `run_intraday_rescan` before reaching the `_persist_watchlist` call.

Therefore no partial DB write can occur: the gate raises before persistence. The accepted
risk rationale ("caller handles exception") holds. No partial write path exists.

---

### T-02-10 — Over-subscription beyond quota

**Disposition:** mitigate
**Status:** CLOSED

Both entrypoints pass only the capped top-20 list to `gateway.subscribe`:

- `run_daily_scan` (scanner.py:399) — `top20 = passing[:_WATCHLIST_CAP]` (cap = 20),
  then `result = [c["code"] for c in top20]`, then `_run_coro(gateway.subscribe(result))`
  at scanner.py:422.
- `run_intraday_rescan` (scanner.py:495-504) — `protected = protected[:_WATCHLIST_CAP]`
  before ranking and persistence; `_subscribe_new_codes(gateway, result, active_codes)`
  at scanner.py:534 passes `result` (capped list), not `yf_symbols`.

Test: `test_subscribe_top20_only` (test_scanner.py:655) — 25 candidates pass filters,
asserts `gateway.subscribe` is called exactly once with a list of length 20, and that
the called codes match the returned codes exactly.

---

### T-02-11 — Active live-feed candidate churned out on re-scan

**Disposition:** mitigate
**Status:** CLOSED

Active-code protection (D-04) implemented in `run_intraday_rescan`:

- `scanner.py:479-490` — iterates `passing` (all qualifying candidates), adds all codes
  in `active_codes` to `protected` first (front-loading), then fills remaining slots
  up to `_WATCHLIST_CAP` with non-active candidates.
- Re-scan never issues DELETE on `daily_scan` rows; persistence uses the same
  idempotent upsert path (`_persist_watchlist`).

Test: `test_active_candidate_protected` (test_scanner.py:779) — 21 new candidates
outrank the active candidate by gap; asserts the active code is still in the result and
persisted, and total count is 20.

---

### T-02-12 — Re-scan duplicates/corrupts watchlist rows

**Disposition:** mitigate
**Status:** CLOSED

Idempotent parameterized upsert on `UNIQUE(scan_date, code)`:

- `scanner.py:209` — `ON CONFLICT(scan_date, code) DO UPDATE SET ...` — all values
  bound via `?` placeholders (see T-02-06 evidence).
- No DELETE statement exists in scanner.py for `daily_scan`.

Test: `test_rescan_idempotent` (test_scanner.py:733) — runs a daily scan then an
intraday re-scan with the same symbols for the same `scan_date`; asserts row count is
unchanged after the re-scan (same value before and after re-scan call).

---

### T-02-13 — Non-RET_OK subscribe silently ignored

**Disposition:** mitigate
**Status:** CLOSED

`gateway.subscribe` fails closed on non-RET_OK:

- `gateway.py:334` — `_check_ret(ret, msg, "subscribe")` called inside
  `_subscribe_blocking()` closure within the `subscribe` method.
- `gateway.py:181-182` — `_check_ret` raises `GatewayError` whenever `ret != RET_OK`.

This is not conditional; there is no path through `subscribe` that receives a non-RET_OK
return and continues silently.

Test: `test_subscribe_raises_on_non_ret_ok` (test_gateway.py:446) — mock returns `(1, "quota exceeded")`, asserts `GatewayError` is raised.

---

## Unregistered Threat Flags

The SUMMARY.md files for all four plans (02-00, 02-01, 02-02, 02-03) each declare
"Threat Flags: None."

No threat flags were raised by the executor. No unregistered flags to record.

---

## Accepted Risks Log

| Threat ID | Risk | Rationale |
|-----------|------|-----------|
| T-02-05 | data/ cache CSV manipulated locally | Cache is gitignored; operator-controlled single-user machine; WR-05 minimum-size check rejects a truncated cache; public data only (S&P 500 tickers); low-value local target |
| T-02-09 | ScanDegradationError propagation | Gate raises before persistence; no partial DB write possible; caller is expected to handle and not re-enter the scan |

---

## Implementation Notes

Two correctness refinements were observed in the implementation that are beyond the scope
of the declared threat register but warrant documentation for completeness:

**CR-01 (scanner.py:96-107):** A symbol with >= rvol_lookback_days but < 200 prior
sessions produces NaN from `sma()`. The implementation fails CLOSED — the symbol is
excluded rather than coercing `None` to `0.0` (which would make D2 trivially true).
This is a sound correctness fix, not a new threat surface.

**CR-02 (scanner.py:127-135):** Today's row is located by matching the frame index to
`scan_date`, not via positional `iloc[-1]`. In premarket (before 09:30 ET), yfinance has
no today bar, so `frame.index[-1]` would be the prior day. The implementation skips any
symbol lacking a `scan_date`-indexed row rather than silently evaluating the wrong session.
This is a correctness fix, not a new threat surface.

Both refinements tighten the filter gate and do not introduce new trust boundaries.
