#!/usr/bin/env python3
"""
bot.safety.paper_guard — Triple fail-closed paper-trading guard (SAFE-01).

Implements assert_paper_account(), a reusable function that enforces three
independent SIMULATE assertions before any order path is reachable:
  Guard 1 — PAPER_TRADING env flag must be True (D-03)
  Guard 2 — FUTU_TRD_ENV must be "SIMULATE" (D-03)
  Guard 3 — The configured account's broker-reported trd_env must be SIMULATE
             (verified via trade_ctx.get_acc_list() for the explicit acc_id — D-03)

Any single failing guard calls _fail(), which appends a structured refusal
entry to the JSONL audit log (SAFE-05) and raises PaperGuardError.
Per D-06 and PATTERNS.md: raises PaperGuardError, never terminates the process — only bot/main.py exits.

Exports: assert_paper_account, PaperGuardError
"""
# SDK success sentinel imported directly so the guard binds to the SDK's own
# constant rather than a hardcoded literal (WR-04). Mirrors how the gateway
# imports RET_OK from moomoo.
from moomoo import RET_OK

from bot._utils import safe_get, safe_int, format_enum
from bot.safety.audit_log import append_audit

# ============================================================
# Exceptions
# ============================================================


class PaperGuardError(Exception):
    """Raised when the triple paper-trading guard fails.

    Signals that a REAL-money order path must never be reached.
    Callers should treat this as fatal and shut down cleanly.
    """


# ============================================================
# Private Helpers
# ============================================================

def _fail(reason: str) -> None:
    """Record a paper_guard_refusal audit entry and raise PaperGuardError.

    This function is the single exit point for all three guard failures (D-06).
    It:
      1. Appends a structured refusal record to the append-only JSONL audit log
         (SAFE-05) so any bypass attempt leaves an immutable trail.
      2. Raises PaperGuardError with the reason (never terminates the process).
    """
    append_audit({
        "event": "paper_guard_refusal",
        "reason": reason,
        "guard": "assert_paper_account",
    })
    raise PaperGuardError(reason)


# ============================================================
# Public API
# ============================================================

def assert_paper_account(cfg, trade_ctx) -> None:
    """Triple independent fail-closed assertion — all three must pass.

    Designed to be called at gateway startup AND before every place_order (D-05),
    making it structurally impossible to place a real-money order even if the
    startup gate were bypassed.

    cfg: GatewayConfig (or compatible object with .paper_trading, .trd_env, .acc_id)
    trade_ctx: open OpenSecTradeContext (or mock) with .get_acc_list()

    Raises:
        PaperGuardError — if any of the three guards fail.
    """

    # Guard 1: PAPER_TRADING environment flag must be explicitly True (D-03)
    if not cfg.paper_trading:
        _fail("PAPER_TRADING env var is not 'true' — set PAPER_TRADING=true to enable the bot")

    # Guard 2: FUTU_TRD_ENV must be SIMULATE (D-03)
    if str(cfg.trd_env).upper() != "SIMULATE":
        _fail(
            f"FUTU_TRD_ENV is '{cfg.trd_env}', expected 'SIMULATE' — "
            "the bot only operates in paper-trading mode"
        )

    # Guard 3: Broker-reported trd_env for the configured acc_id must be SIMULATE (D-03/D-04)
    # No auto-selection — operator must set FUTU_ACC_ID explicitly (D-03).
    ret, data = trade_ctx.get_acc_list()

    # SDK returns RET_OK on success; any other value means the broker call
    # failed. Bind to the SDK constant so a future change to its success value
    # cannot mis-classify a failed broker call as success (WR-04). Fails closed.
    if ret != RET_OK:
        _fail(
            f"get_acc_list() failed with ret={ret} — cannot verify broker account type"
        )

    # data may be a pandas DataFrame or a list; iterate defensively
    if data is None or (hasattr(data, '__len__') and len(data) == 0):
        _fail(
            f"get_acc_list() returned empty data — "
            f"account {cfg.acc_id} not found; ensure FUTU_ACC_ID is set correctly"
        )

    configured_acc_id = safe_int(cfg.acc_id)
    found = False

    rows = list(range(len(data)))
    for i in rows:
        row = data.iloc[i] if hasattr(data, "iloc") else data[i]
        row_acc_id = safe_int(safe_get(row, "acc_id", default=None))
        if row_acc_id == configured_acc_id:
            found = True
            # Read trd_env from the broker-reported row (T-01-03: no spoofing via wrong account)
            broker_trd_env = str(format_enum(safe_get(row, "trd_env", default=""))).upper()
            if broker_trd_env != "SIMULATE":
                _fail(
                    f"Account {configured_acc_id} has broker-reported trd_env='{broker_trd_env}', "
                    f"expected 'SIMULATE' — this appears to be a REAL-money account; aborting"
                )
            # All three guards passed for this account row
            return

    if not found:
        _fail(
            f"Account {configured_acc_id} not found in get_acc_list() results — "
            f"ensure FUTU_ACC_ID={configured_acc_id} is correct and the account exists"
        )
