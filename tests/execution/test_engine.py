#!/usr/bin/env python3
"""
tests.execution.test_engine — Unit tests for ExecutionEngine (Phase 4, EXEC-01..05).

These stubs collect RED during Wave 0 (04-01) and are implemented in 04-03.
Test names EXACTLY match the validation map in 04-VALIDATION.md.
"""
import pytest


def test_entry_placed_simulate():
    """EXEC-01: Entry order placed in SIMULATE env; FillEvent emitted.

    Implemented in 04-03. Requires mock gateway with order_type=NORMAL and
    SIMULATE environment; verifies FillEvent is emitted with correct fields.
    """
    assert False, "TODO: implemented in 04-03"


def test_no_market_orders():
    """EXEC-02: No market order ever placed; force-close uses marketable limit.

    Implemented in 04-03. AST + behavioral check that OrderType.NORMAL is
    always used; OrderType.MARKET is never submitted.
    """
    assert False, "TODO: implemented in 04-03"


def test_ttl_cancel_replace():
    """EXEC-03: TTL cancel-replace fires; abandon after max retries.

    Implemented in 04-03. Synthetic fill mock; config-swap confirms TTL and
    max_retries from rules.json execution block control behavior (CFG-01).
    """
    assert False, "TODO: implemented in 04-03"


def test_duplicate_guard():
    """EXEC-04: Broker get_positions() blocks duplicate entry (broker-truth guard).

    Implemented in 04-04. Mocked get_positions() returns existing position;
    ExecutionEngine must refuse to place a second entry for the same code.
    """
    assert False, "TODO: implemented in 04-0X"


def test_fill_by_order_id():
    """EXEC-05: Fill matched by order_id; partial exit != stop-out.

    Implemented in 04-03. Synthetic deal rows with matching/non-matching
    order_ids; verifies that a partial exit fill (is_entry=False) does not
    resolve as a full stop-out.
    """
    assert False, "TODO: implemented in 04-03"
