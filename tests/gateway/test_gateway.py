#!/usr/bin/env python3
"""
tests/gateway/test_gateway.py — Tests for bot.gateway.gateway (MoomooGateway).

Verifies (no network — all SDK contexts are mocked):
  - get_gateway_config reads env vars with correct defaults
  - _check_opend_alive raises ConnectionError (not SystemExit) on refused port
  - connect() calls assert_paper_account with the trade context
  - reconcile_once and reconciliation_loop exist and return correct types
  - MoomooGateway does NOT import from skills/
  - GatewayConfig defaults trd_env to SIMULATE (never REAL)
"""
import asyncio
import inspect
import os
import socket
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import pandas as pd

# ============================================================
# Imports under test
# ============================================================
from bot.gateway.gateway import (
    MoomooGateway,
    GatewayConfig,
    GatewayError,
    get_gateway_config,
    _check_opend_alive,
    _parse_trd_env,
    _parse_market,
    _check_ret,
)
from bot.safety.paper_guard import PaperGuardError


# ============================================================
# Helpers
# ============================================================

def _make_mock_ctx(acc_id: int = 123456789, broker_trd_env: str = "SIMULATE"):
    """Create a mock SDK trade context with get_acc_list() wired."""
    mock = MagicMock()
    df = pd.DataFrame([{"acc_id": acc_id, "trd_env": broker_trd_env}])
    mock.get_acc_list.return_value = (0, df)
    mock.position_list_query.return_value = (0, pd.DataFrame())
    return mock


def _make_gateway_with_mocks(
    cfg: GatewayConfig = None,
    acc_id: int = 123456789,
    broker_trd_env: str = "SIMULATE",
) -> MoomooGateway:
    """Return a MoomooGateway with pre-injected mock contexts (no connect() needed)."""
    if cfg is None:
        cfg = GatewayConfig(
            acc_id=acc_id,
            paper_trading=True,
            trd_env="SIMULATE",
        )
    gw = MoomooGateway(cfg)
    gw._quote_ctx = MagicMock()
    gw._trade_ctx = _make_mock_ctx(acc_id=acc_id, broker_trd_env=broker_trd_env)
    return gw


# ============================================================
# GatewayConfig / get_gateway_config
# ============================================================

class TestGatewayConfig:
    def test_default_trd_env_is_simulate(self):
        """GatewayConfig must default trd_env to 'SIMULATE'."""
        cfg = GatewayConfig()
        assert cfg.trd_env == "SIMULATE"

    def test_no_real_default_anywhere(self):
        """The default value for trd_env must never be 'REAL'."""
        cfg = GatewayConfig()
        assert cfg.trd_env.upper() != "REAL"

    def test_default_paper_trading_is_false(self):
        """paper_trading defaults to False — must be explicitly enabled (D-03)."""
        cfg = GatewayConfig()
        assert cfg.paper_trading is False

    def test_get_gateway_config_reads_env_vars(self, monkeypatch):
        """get_gateway_config must read all FUTU_* and PAPER_TRADING env vars."""
        monkeypatch.setenv("FUTU_OPEND_HOST", "192.168.1.10")
        monkeypatch.setenv("FUTU_OPEND_PORT", "22222")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_DEFAULT_MARKET", "HK")
        monkeypatch.setenv("FUTU_ACC_ID", "987654321")
        monkeypatch.setenv("PAPER_TRADING", "true")

        cfg = get_gateway_config()

        assert cfg.opend_host == "192.168.1.10"
        assert cfg.opend_port == 22222
        assert cfg.trd_env == "SIMULATE"
        assert cfg.default_market == "HK"
        assert cfg.acc_id == 987654321
        assert cfg.paper_trading is True

    def test_get_gateway_config_defaults(self, monkeypatch):
        """get_gateway_config returns safe defaults when env vars are absent."""
        for var in ["FUTU_OPEND_HOST", "FUTU_OPEND_PORT", "FUTU_TRD_ENV",
                    "FUTU_DEFAULT_MARKET", "FUTU_ACC_ID", "PAPER_TRADING",
                    "FUTU_SECURITY_FIRM"]:
            monkeypatch.delenv(var, raising=False)

        cfg = get_gateway_config()

        assert cfg.opend_host == "127.0.0.1"
        assert cfg.opend_port == 11111
        assert cfg.trd_env == "SIMULATE"
        assert cfg.paper_trading is False

    def test_paper_trading_false_when_env_not_set(self, monkeypatch):
        """PAPER_TRADING unset must result in paper_trading=False."""
        monkeypatch.delenv("PAPER_TRADING", raising=False)
        cfg = get_gateway_config()
        assert cfg.paper_trading is False

    def test_paper_trading_true_when_env_lowercase_true(self, monkeypatch):
        """PAPER_TRADING=true (lowercase) must set paper_trading=True."""
        monkeypatch.setenv("PAPER_TRADING", "true")
        cfg = get_gateway_config()
        assert cfg.paper_trading is True


# ============================================================
# _check_opend_alive
# ============================================================

class TestCheckOpendAlive:
    def test_raises_connection_error_on_refused_port(self):
        """_check_opend_alive must raise ConnectionError (not SystemExit) on refused port."""
        # Use a port that is almost certainly not listening (reserved ephemeral range)
        with pytest.raises(ConnectionError):
            _check_opend_alive("127.0.0.1", 19999)

    def test_does_not_raise_system_exit(self):
        """_check_opend_alive must not raise SystemExit on connection failure."""
        try:
            _check_opend_alive("127.0.0.1", 19999)
        except ConnectionError:
            pass  # Expected
        except SystemExit:
            pytest.fail("_check_opend_alive raised SystemExit — it must raise ConnectionError")

    def test_error_message_includes_host_port(self):
        """ConnectionError message must mention the host and port for operator clarity."""
        with pytest.raises(ConnectionError) as exc_info:
            _check_opend_alive("127.0.0.1", 19999)
        msg = str(exc_info.value)
        assert "127.0.0.1" in msg
        assert "19999" in msg

    def test_no_sys_exit_in_source(self):
        """_check_opend_alive source must not call sys.exit."""
        source = inspect.getsource(_check_opend_alive)
        assert "sys.exit" not in source


# ============================================================
# connect() — paper guard invocation
# ============================================================

class TestConnect:
    def test_connect_calls_assert_paper_account(self):
        """connect() must invoke assert_paper_account with the trade context (SAFE-01)."""
        cfg = GatewayConfig(
            acc_id=123456789,
            paper_trading=True,
            trd_env="SIMULATE",
        )
        gw = MoomooGateway(cfg)
        mock_quote = MagicMock()
        mock_trade = _make_mock_ctx(acc_id=123456789, broker_trd_env="SIMULATE")

        with patch.object(gw, "_check_opend_alive_pre_connect", create=True):
            with patch("bot.gateway.gateway._check_opend_alive"):
                with patch.object(gw, "_make_quote_ctx", return_value=mock_quote):
                    with patch.object(gw, "_make_trade_ctx", return_value=mock_trade):
                        with patch("bot.gateway.gateway.assert_paper_account") as mock_guard:
                            gw.connect()
                            mock_guard.assert_called_once_with(cfg, mock_trade)

    def test_connect_paper_guard_failure_propagates(self):
        """If assert_paper_account raises PaperGuardError, connect() must propagate it."""
        cfg = GatewayConfig(
            acc_id=123456789,
            paper_trading=False,  # This will cause paper guard to fail
            trd_env="SIMULATE",
        )
        gw = MoomooGateway(cfg)
        mock_quote = MagicMock()
        mock_trade = _make_mock_ctx(acc_id=123456789, broker_trd_env="SIMULATE")

        with patch("bot.gateway.gateway._check_opend_alive"):
            with patch.object(gw, "_make_quote_ctx", return_value=mock_quote):
                with patch.object(gw, "_make_trade_ctx", return_value=mock_trade):
                    with pytest.raises(PaperGuardError):
                        gw.connect()

    def test_connect_pre_flight_fires_before_contexts(self):
        """_check_opend_alive must be called before context creation in connect()."""
        cfg = GatewayConfig(acc_id=123456789, paper_trading=True)
        gw = MoomooGateway(cfg)
        call_order = []

        with patch("bot.gateway.gateway._check_opend_alive",
                   side_effect=lambda h, p: call_order.append("preflight")):
            with patch.object(gw, "_make_quote_ctx",
                              side_effect=lambda: call_order.append("quote_ctx") or MagicMock()):
                with patch.object(gw, "_make_trade_ctx",
                                  side_effect=lambda: call_order.append("trade_ctx") or _make_mock_ctx()):
                    with patch("bot.gateway.gateway.assert_paper_account"):
                        gw.connect()

        assert call_order[0] == "preflight", (
            "Pre-flight check must fire before context creation"
        )


# ============================================================
# Reconciliation Skeletons
# ============================================================

class TestReconciliation:
    def test_reconcile_once_exists_and_is_async(self):
        """reconcile_once must exist and be a coroutine function."""
        assert inspect.iscoroutinefunction(MoomooGateway.reconcile_once)

    def test_reconciliation_loop_exists_and_is_async(self):
        """reconciliation_loop must exist and be a coroutine function."""
        assert inspect.iscoroutinefunction(MoomooGateway.reconciliation_loop)

    def test_reconcile_once_returns_dict(self):
        """reconcile_once must return a dict (broker truth payload)."""
        gw = _make_gateway_with_mocks()

        async def _run():
            return await gw.reconcile_once()

        result = asyncio.run(_run())
        assert isinstance(result, dict)

    def test_reconcile_once_dict_has_required_keys(self):
        """reconcile_once dict must have 'positions', 'accounts', 'drift' keys."""
        gw = _make_gateway_with_mocks()

        async def _run():
            return await gw.reconcile_once()

        result = asyncio.run(_run())
        assert "positions" in result
        assert "accounts" in result
        assert "drift" in result

    def test_reconciliation_loop_default_interval_in_range(self):
        """reconciliation_loop default interval_s must be between 60 and 90 seconds."""
        import inspect
        sig = inspect.signature(MoomooGateway.reconciliation_loop)
        default_interval = sig.parameters["interval_s"].default
        assert 60.0 <= default_interval <= 90.0, (
            f"reconciliation_loop default interval {default_interval}s is outside 60-90s range (SAFE-03)"
        )


# ============================================================
# D-02 Compliance: no import from skills/
# ============================================================

class TestD02Compliance:
    def test_gateway_does_not_import_skills(self):
        """gateway.py must not import from skills/ (D-02: wrap, don't import)."""
        import bot.gateway.gateway as gw_mod
        source = inspect.getsource(gw_mod)
        assert "from skills" not in source
        assert "import skills" not in source

    def test_gateway_does_not_import_from_common(self):
        """gateway.py must not import common.py from the skills directory."""
        import bot.gateway.gateway as gw_mod
        source = inspect.getsource(gw_mod)
        # Should not contain any sys.path manipulation to skills dir
        assert "skills/moomooapi" not in source


# ============================================================
# GatewayError
# ============================================================

class TestGatewayError:
    def test_gateway_error_is_exception(self):
        """GatewayError must be an Exception subclass."""
        assert issubclass(GatewayError, Exception)

    def test_check_ret_raises_gateway_error_on_failure(self):
        """_check_ret must raise GatewayError when ret != RET_OK."""
        with pytest.raises(GatewayError):
            _check_ret(1, "error_data", "test_operation")

    def test_check_ret_does_not_raise_on_success(self):
        """_check_ret must not raise when ret == RET_OK (0)."""
        _check_ret(0, {}, "test_operation")  # must not raise

    def test_no_sys_exit_in_gateway_source(self):
        """gateway.py must not call sys.exit."""
        import bot.gateway.gateway as gw_mod
        source = inspect.getsource(gw_mod)
        # Filter out any comment lines that might reference sys.exit conceptually
        # Only care about actual sys.exit( calls
        import re
        # Find actual calls, not just mentions in comments/strings
        non_comment_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#")
            and "sys.exit" in line
        ]
        assert len(non_comment_lines) == 0, (
            f"gateway.py has sys.exit call(s): {non_comment_lines}"
        )


# ============================================================
# close()
# ============================================================

class TestClose:
    def test_close_closes_both_contexts(self):
        """close() must close both quote_ctx and trade_ctx."""
        gw = _make_gateway_with_mocks()
        mock_quote = gw._quote_ctx
        mock_trade = gw._trade_ctx

        gw.close()

        mock_quote.close.assert_called_once()
        mock_trade.close.assert_called_once()

    def test_close_sets_contexts_to_none(self):
        """close() must set both context attributes to None."""
        gw = _make_gateway_with_mocks()
        gw.close()
        assert gw._quote_ctx is None
        assert gw._trade_ctx is None
