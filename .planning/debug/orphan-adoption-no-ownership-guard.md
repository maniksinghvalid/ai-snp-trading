---
slug: orphan-adoption-no-ownership-guard
status: resolved
trigger: "Orphan-adoption in reconcile_once and startup_reconcile adopts ANY broker position not in memory, with no bot-ownership check and no long-only guard. On the shared SIMULATE paper account 1727266 (holds ~30 operator manual positions) a restart would adopt and force-close the operator's manual longs and adopt shorts/options as un-closeable zombies."
created: 2026-06-30
updated: 2026-06-30
---

# Debug Session: orphan-adoption-no-ownership-guard

## Symptoms

- **Expected behavior:** The bot only adopts/manages positions IT opened. Orphan-adoption (crash recovery) should re-adopt a position the bot itself created but lost from memory — never the operator's manual holdings or short/option positions.
- **Actual behavior:** Orphan-adoption adopts ANY broker position present at the broker but not in `manager._positions`, regardless of ownership, direction, or instrument type. No universe/allow-list filter, no long-only guard.
- **Error messages:** None — latent design defect, exposed by commit 3ad77fa (which fixed get_positions to read the correct SIMULATE account). Pre-fix the bug masked it (reconcile saw only a stale REAL-account read).
- **Timeline:** Latent since orphan-adoption was added (CR-03). Became dangerous on 2026-06-30 once get_positions correctly reads SIMULATE acc 1727266, which is shared with ~30 manual positions (AAPL/MSFT/IAU/SCHD/MARA longs + short option spreads).
- **Reproduction:** With the get_positions fix in place, restart the bot against acc 1727266 → reconcile_once / startup_reconcile would adopt all 30 positions; force_close_all would SELL the manual longs at 15:51 ET.

## Current Focus

- hypothesis: Orphan-adoption loop (reconcile_once at bot/gateway/gateway.py ~896, plus the mirrored startup_reconcile path) adopts every `code in broker_map` not already tracked, with no check that (a) the bot has a DB record for the code and (b) the position is a long. Needs a guard.
- test: Three regression tests asserting (1) a long with NO bot DB record is NOT adopted; (2) a SHORT is NOT adopted; (3) a long WITH a matching pending_intent IS adopted.
- expecting: Before fix — all three "should not adopt" cases adopt (tests fail). After fix — non-bot/short positions skipped (logged reconcile_external_position_ignored), bot-owned longs still adopted.
- next_action: Write failing regression tests, apply guard to both sites, fix test_restart_reconciliation to seed pending_intent for US.GOOG, run full suite.
- reasoning_checkpoint:
    hypothesis: "reconcile_once (line 896) and startup_reconcile (line 1048) orphan loops adopt any broker position not in memory/DB, with no has_pending_intent check and no qty>0 guard."
    confirming_evidence:
      - "grep confirms no ownership/long-only check in the orphan for-loop at either site"
      - "state_codes in reconcile_once is built from manager._positions (not DB), so a crash-recovery orphan could appear even if has_pending_intent exists"
      - "state_codes in startup_reconcile IS get_open_positions(); orphan by definition not in state_codes so only pending_intent remains as ownership signal"
      - "test_restart_reconciliation seeds US.GOOG orphan with NO pending_intent — after fix this test must insert a PENDING row"
    falsification_test: "if startup_reconcile adopted only positions WITH pending_intent, a manual-long with no DB record would not be adopted — confirmed absent by code inspection"
    fix_rationale: "add is_long=qty>0 and bot_owned=has_pending_intent(code) or code in open_pos_codes guards before adoption; log reconcile_external_position_ignored on rejection; crash-recovery preserved because pending_intent is written BEFORE place_order (engine.py docstring line 108)"
    blind_spots: "test_restart_reconciliation uses real StateStore and expects US.GOOG adoption — must insert PENDING intent row there to preserve crash-recovery semantics"
- tdd_checkpoint:

## Evidence

- timestamp: 2026-06-30 — OpenD get_accounts: only ONE SIMULATE account exists (1727266); the other 3 are REAL (FUTUCA RRSP/TFSA/MARGIN). No clean dedicated paper account available → bot must safely share 1727266.
- timestamp: 2026-06-30 — get_portfolio acc 1727266: 30 positions incl. AAPL(17), MSFT(26), IAU(100), SCHD(129), MARA(200) longs and multiple short option spreads (e.g. MARA CALL -2). None are bot trades; bot's S&P universe overlaps some (AAPL/MSFT) so a pure universe filter is insufficient.
- timestamp: 2026-06-30 — Code: reconcile_once orphan loop bot/gateway/gateway.py:896 `for code, bp in broker_map.items(): ... insert_orphan_position / manager.adopt_orphan` — no ownership/long-only guard. grep confirmed no universe/allow-list/short check in gateway.py or manager.py.
- timestamp: 2026-06-30 — StateStore (bot/state/store.py) provides has_pending_intent(code):553, get_pending_intent_codes(status):496, get_open_positions():364, get_watchlist_codes(scan_date):578. engine.py:5 docstring: OrderIntent is persisted in pending_intents before becoming orders → bot-owned positions always have a pending_intent row (crash-recovery safe).

## Eliminated

- hypothesis: Pure S&P 500 universe filter is sufficient — ELIMINATED: AAPL and MSFT are both in the S&P 500 AND held manually in acc 1727266, so a universe filter would still adopt them. Ownership must be determined by the bot's own DB records (pending_intent / position rows), not by symbol membership.

## Resolution

- root_cause: "reconcile_once (gateway.py line ~896) and startup_reconcile (line ~1048) orphan loops called store.insert_orphan_position / manager.adopt_orphan for ANY broker position not in memory/DB, with no bot-ownership check and no long-only guard. On account 1727266 (shared with ~30 manual positions including AAPL/MSFT longs and short option spreads) a restart would adopt and eventually force-close the operator's manual holdings."
- fix: |
    Added SAFE-OG-01 guard to both adoption sites in gateway.py before any DB insert:
      (a) long-only: `bp['qty'] > 0` — rejects shorts and zero-qty options
      (b) bot-ownership:
          - reconcile_once: `store.has_pending_intent(code) OR code in open_pos_codes`
            (open_pos_codes pre-computed from store.get_open_positions() before the loop)
          - startup_reconcile: `store.has_pending_intent(code)` only
            (state_codes already IS get_open_positions(); code excluded from orphan loop by construction)
      Rejection logs `reconcile_external_position_ignored` with code, broker_qty, reason.
      Crash-recovery preserved: pending_intent is written BEFORE place_order (engine.py EXEC-04 docstring).
    Also updated tests/position/test_manager.py::test_restart_reconciliation to insert a PENDING intent
    for US.GOOG (the D-10 orphan) before running startup_reconcile — reflecting correct crash-recovery semantics.
- verification: |
    5 new regression tests in tests/gateway/test_gateway.py::TestOrphanOwnershipGuard:
      - Test A: manual long (no DB record) → NOT adopted (reconcile_once) — was FAIL, now PASS
      - Test B: short qty=-2 → NOT adopted (reconcile_once) — was FAIL, now PASS
      - Test C: bot-owned long with pending_intent → IS adopted (reconcile_once) — PASS before and after
      - Test D: manual long (no DB record) → NOT adopted (startup_reconcile) — was FAIL, now PASS
      - Test E: bot-owned long with pending_intent → IS adopted (startup_reconcile) — PASS before and after
    Full suite: 472 passed, 1 skipped, 0 failed.
    Live verification (2026-06-30, read-only one-shot against a COPY of bot_state.db, no scheduler/
    reconcile/adoption/subscribe): evaluated the exact reconcile_once guard predicate
    (qty>0 AND (has_pending_intent OR code in open_pos_codes)) against the 30 LIVE broker positions
    on SIMULATE acc 1727266 + real bot DB reads → ALL 30 IGNORED, 0 would-adopt
    (manual longs AAPL/MSFT/IAU/SCHD/MARA/NIO/O/JEPQ/SVOL → not_bot_owned; short option spreads → not_long;
    bot DB open codes = {US.NVDY} only, stale ACTIVE row, NVDY flat at broker and absent from live list).
- files_changed:
    - bot/gateway/gateway.py
    - tests/gateway/test_gateway.py
    - tests/position/test_manager.py
