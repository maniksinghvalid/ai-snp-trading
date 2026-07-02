# Shared SIMULATE Account Isolation — Design

**Date:** 2026-07-02
**Status:** Approved
**Account:** Moomoo SIMULATE 1727266 (shared: bot + operator manual trading)

## Problem

The bot and the operator's manual trading share one SIMULATE account. The bot must
never touch, adopt, size from, or compete with manual holdings. The prior incident
(2026-06-30: RYLD/CLOV/SPCE adopted and exited by the bot) was fixed by SAFE-OG-01,
but two gaps remain:

1. **Symbol collision.** If the bot enters a symbol the operator already holds
   manually, Moomoo merges them into one broker position (aggregate qty, blended
   avg cost). The D-09 "broker truth wins" qty-drift rule would then fold manual
   shares into the bot's position, and the bot would sell them at exit/force-close.
2. **Equity skew.** `RiskEngine` sizes 1%-risk from `gateway.get_equity()` (live
   account equity), so manual holdings' value and P&L skew sizing away from the
   intended $100,000 paper basis.

Operator decision: collision defense is required regardless of current holdings
("not sure / want safety anyway" — manual holdings may include S&P 500 names).

## Existing protections (verified, no changes)

| Guard | Location | Behavior |
|-------|----------|----------|
| SAFE-OG-01 | `gateway.py` `reconcile_once` + `startup_reconcile` | Broker positions with no bot DB record (no open position row, no pending intent) are ignored — logged `reconcile_external_position_ignored`. Manual holdings are never adopted. Short positions never adopted (long-only). |
| EXEC-04 | `execution/engine.py` `consume_intent` | Entry aborts (`duplicate_entry_blocked_open_position`) if the broker already holds the intent's code — collision with a manual holding is blocked before any order is placed. Fails open on query error (accepted: rare, and blocking all trading on transient errors is worse; scan-time exclusion plus this guard are layered). |
| Force-close scope | `manager.py` `force_close_all` | Iterates only bot-managed in-memory positions (`self._positions`), never the broker position list. Manual holdings untouched at close. |

## New work

### 1. Scan-time exclusion of externally held codes

- **New gateway helper** `get_external_codes() -> set[str]`: codes held at the
  broker for which the bot has no open DB position row and no pending intent —
  the same ownership predicate as SAFE-OG-01. On a failed broker query
  (ret != RET_OK or data None) it raises `GatewayError`; the caller decides the
  fallback.
- **Scanner integration:** in the premarket scan and the 30-minute intraday
  rescan, drop external codes from the candidate list **before** the top-20 cap
  is applied. Log `symbol_excluded_manual_holding` per excluded code.
- **Error handling:** if the broker position query fails at scan time, proceed
  **without** exclusion and log a warning (`external_exclusion_skipped_query_failed`).
  A scan must never come up empty because of a transient broker error — EXEC-04
  remains the entry-time backstop.

Rationale: EXEC-04 already prevents the dangerous outcome; scan-time exclusion
stops manual holdings from wasting top-20 watchlist slots and from generating
signals/intents that would be noisily rejected at entry.

### 2. Fixed sizing basis

- **New `rules.json` key** `risk.sizing_equity_usd`: number or `null`.
  Default `100000`. When set, `RiskEngine.size()` uses it as the equity basis
  instead of calling `gateway.get_equity()`. When `null`, live-equity behavior
  is unchanged (path preserved for a future dedicated account).
- Schema + loader (`bot/config/schema.py`, `bot/config/loader.py`) updated;
  `equity_used` in the audit event records the basis actually used.

## Testing

- **Gateway:** `get_external_codes()` — held+bot-owned (excluded from result),
  held+not-owned (included), short position (included/never bot-tradable),
  query failure raises `GatewayError`.
- **Scanner:** externally held code dropped before the cap; a bot-owned code is
  not dropped; broker-failure path keeps the watchlist intact and logs the skip.
- **Risk:** sizing uses `sizing_equity_usd` when set; falls back to live equity
  when `null`; audit records the basis used.
- **Regression (documents existing behavior):** EXEC-04 blocks an entry when the
  intent's code is already held at the broker without bot ownership.
- **Live UAT addition:** start the bot on 1727266 with manual holdings present;
  grep logs for `reconcile_external_position_ignored` for each manual holding;
  confirm none appear on the watchlist, in intents, or in EOD reports.

## Out of scope

- No changes to reconcile/adoption logic (SAFE-OG-01 already correct).
- No static denylist config (operator chose dynamic broker-derived exclusion).
- No changes to force-close, alerter, or backtester.
- Real-money paths (project is SIMULATE-only this milestone).

## Consequence for Phase 06.2 UAT

With this design implemented, the pending Tier 1 UAT checkpoint (Plan 06.2-01
Task 6) can run on shared account 1727266 directly instead of requiring a
dedicated SIMULATE account.
