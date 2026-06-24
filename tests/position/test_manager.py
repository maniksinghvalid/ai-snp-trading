#!/usr/bin/env python3
"""
tests.position.test_manager — Unit tests for PositionManager (Phase 4, POS-04..05).

Test names EXACTLY match the validation map in 04-VALIDATION.md.
Implemented in 04-04.
"""
import pytest


def test_force_close_half_day():
    """POS-04: Force-close at half-day early close vs 15:51 on normal day.

    Implemented in 04-04. Mock calendar returns "13:00" for a half-day;
    PositionManager must use earlier force-close time. Normal day uses "15:51"
    derived from cfg.force_close_et (CFG-01).
    """
    assert False, "TODO: implemented in 04-0X"


def test_restart_reconciliation():
    """POS-05: Restart reconstructs FSM from StateStore; broker-truth wins; no re-entry.

    Implemented in 04-04. In-memory SQLite + mock broker; verifies that
    PositionManager.reconcile_on_startup() restores persisted phase/trail_stop,
    adopts broker quantity if different, and never re-enters (SAFE-02/03).
    """
    assert False, "TODO: implemented in 04-0X"
