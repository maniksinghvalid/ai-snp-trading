#!/usr/bin/env python3
"""
tests/safety/test_paper_guard.py — Tests for bot.safety.paper_guard.

Verifies the triple fail-closed paper-trading guard (SAFE-01/D-04):
  - All-pass → no raise
  - Guard 1 fails (paper_trading=False) → PaperGuardError raised
  - Guard 2 fails (trd_env != SIMULATE) → PaperGuardError raised
  - Guard 3 fails (broker trd_env == REAL) → PaperGuardError raised
  - Guard 3 fails (acc_id not in list) → PaperGuardError raised
  - On any failure, a paper_guard_refusal audit entry is written (SAFE-05)
  - assert_paper_account raises PaperGuardError, never SystemExit
  - No sys.exit calls in paper_guard.py
"""
import json
import os
import pandas as pd
import pytest
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from bot.safety.paper_guard import assert_paper_account, PaperGuardError


# ============================================================
# Test Helpers
# ============================================================

@dataclass
class MockConfig:
    """Minimal config object matching the GatewayConfig interface."""
    paper_trading: bool = True
    trd_env: str = "SIMULATE"
    acc_id: int = 123456789


def _make_trade_ctx(acc_id: int = 123456789, broker_trd_env: str = "SIMULATE") -> Any:
    """Return a mock trade context whose get_acc_list returns a single-row DataFrame."""
    mock = MagicMock()
    df = pd.DataFrame([{"acc_id": acc_id, "trd_env": broker_trd_env}])
    mock.get_acc_list.return_value = (0, df)  # RET_OK=0
    return mock


def _make_empty_trade_ctx() -> Any:
    """Return a mock trade context whose get_acc_list returns an empty DataFrame."""
    mock = MagicMock()
    df = pd.DataFrame(columns=["acc_id", "trd_env"])
    mock.get_acc_list.return_value = (0, df)
    return mock


def _make_failed_trade_ctx() -> Any:
    """Return a mock trade context whose get_acc_list returns a non-zero ret."""
    mock = MagicMock()
    mock.get_acc_list.return_value = (1, None)  # ret != 0
    return mock


@pytest.fixture
def tmp_audit_path(tmp_path, monkeypatch):
    """Redirect AUDIT_LOG_PATH to a tmp file and return its path."""
    audit_file = str(tmp_path / "guard_test_audit.jsonl")
    import bot.safety.audit_log as audit_mod
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", audit_file)
    return audit_file


# ============================================================
# All-Pass Case
# ============================================================

class TestAllPass:
    def test_all_guards_pass_no_raise(self, tmp_audit_path):
        """When all three guards pass, assert_paper_account returns None without raising."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="SIMULATE")
        # Should not raise
        result = assert_paper_account(cfg, ctx)
        assert result is None

    def test_all_guards_pass_no_audit_entry(self, tmp_audit_path):
        """A successful pass must not write a refusal audit entry."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="SIMULATE")
        assert_paper_account(cfg, ctx)
        # No audit file should exist (or it exists with 0 lines)
        if os.path.exists(tmp_audit_path):
            with open(tmp_audit_path) as f:
                lines = [l for l in f.readlines() if l.strip()]
            assert len(lines) == 0, "Unexpected audit entries written on success"


# ============================================================
# Guard 1: PAPER_TRADING flag
# ============================================================

class TestGuard1PaperTradingFlag:
    def test_paper_trading_false_raises(self, tmp_audit_path):
        """Guard 1: paper_trading=False must raise PaperGuardError."""
        cfg = MockConfig(paper_trading=False, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx()
        with pytest.raises(PaperGuardError) as exc_info:
            assert_paper_account(cfg, ctx)
        assert "PAPER_TRADING" in str(exc_info.value)

    def test_guard1_writes_audit_entry(self, tmp_audit_path):
        """Guard 1 failure must write a paper_guard_refusal audit entry."""
        cfg = MockConfig(paper_trading=False, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)
        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "paper_guard_refusal"
        assert entry["reason"]  # non-empty reason


# ============================================================
# Guard 2: FUTU_TRD_ENV
# ============================================================

class TestGuard2TrdEnv:
    @pytest.mark.parametrize("bad_env", ["REAL", "real", "Real", "LIVE", ""])
    def test_trd_env_not_simulate_raises(self, bad_env, tmp_audit_path):
        """Guard 2: non-SIMULATE trd_env must raise PaperGuardError."""
        cfg = MockConfig(paper_trading=True, trd_env=bad_env, acc_id=123456789)
        ctx = _make_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)

    def test_guard2_writes_audit_entry(self, tmp_audit_path):
        """Guard 2 failure must write a paper_guard_refusal audit entry."""
        cfg = MockConfig(paper_trading=True, trd_env="REAL", acc_id=123456789)
        ctx = _make_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)
        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "paper_guard_refusal"
        assert entry["reason"]


# ============================================================
# Guard 3: Broker-reported trd_env
# ============================================================

class TestGuard3BrokerTrdEnv:
    def test_broker_trd_env_real_raises(self, tmp_audit_path):
        """Guard 3: broker-reported trd_env=REAL for the acc_id must raise."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="REAL")
        with pytest.raises(PaperGuardError) as exc_info:
            assert_paper_account(cfg, ctx)
        assert "REAL" in str(exc_info.value) or "SIMULATE" in str(exc_info.value)

    def test_broker_trd_env_real_writes_audit_entry(self, tmp_audit_path):
        """Guard 3 (REAL env) failure must write a paper_guard_refusal audit entry."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="REAL")
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)
        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "paper_guard_refusal"
        assert entry["reason"]

    def test_acc_id_not_found_raises(self, tmp_audit_path):
        """Guard 3: acc_id not in get_acc_list() must raise PaperGuardError."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=999999999)
        # Trade ctx has acc_id 123456789, not the one in cfg
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="SIMULATE")
        with pytest.raises(PaperGuardError) as exc_info:
            assert_paper_account(cfg, ctx)
        assert "999999999" in str(exc_info.value) or "not found" in str(exc_info.value).lower()

    def test_acc_id_not_found_writes_audit_entry(self, tmp_audit_path):
        """Guard 3 (acc_id not found) failure must write a paper_guard_refusal audit entry."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=999999999)
        ctx = _make_trade_ctx(acc_id=123456789, broker_trd_env="SIMULATE")
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)
        with open(tmp_audit_path, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "paper_guard_refusal"
        assert entry["reason"]

    def test_empty_acc_list_raises(self, tmp_audit_path):
        """Guard 3: empty get_acc_list() result raises PaperGuardError."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_empty_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)

    def test_get_acc_list_failure_raises(self, tmp_audit_path):
        """Guard 3: non-zero ret from get_acc_list() raises PaperGuardError."""
        cfg = MockConfig(paper_trading=True, trd_env="SIMULATE", acc_id=123456789)
        ctx = _make_failed_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)


# ============================================================
# Safety Invariants
# ============================================================

class TestSafetyInvariants:
    def test_raises_paper_guard_error_not_system_exit(self, tmp_audit_path):
        """assert_paper_account must raise PaperGuardError, never SystemExit."""
        cfg = MockConfig(paper_trading=False)
        ctx = _make_trade_ctx()
        with pytest.raises(PaperGuardError):
            assert_paper_account(cfg, ctx)
        # Implicitly: if it raised SystemExit, the above would fail with a different error

    def test_paper_guard_error_is_exception(self):
        """PaperGuardError must be an Exception subclass."""
        assert issubclass(PaperGuardError, Exception)

    def test_no_sys_exit_in_source(self):
        """paper_guard.py must not call sys.exit (library raises, never exits)."""
        import inspect
        import bot.safety.paper_guard as mod
        source = inspect.getsource(mod)
        assert "sys.exit" not in source, (
            "paper_guard.py must not call sys.exit — it raises PaperGuardError instead"
        )
