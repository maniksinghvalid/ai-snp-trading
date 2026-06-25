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

    def test_loop_continues_after_reconcile_exception(self):
        """
        WR-03: a single reconcile_once() exception must be logged and the loop
        must keep running — one broker error must not permanently kill the
        SAFE-03 reconciliation loop.
        """
        gw = _make_gateway_with_mocks()
        calls = {"n": 0}

        async def flaky_reconcile():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated broker blip")
            return {"positions": None, "accounts": None, "drift": {}}

        gw.reconcile_once = flaky_reconcile

        async def _run():
            # Tiny interval so the loop iterates quickly; cancel once it has
            # survived the first (raising) iteration and done a second one.
            task = asyncio.ensure_future(gw.reconciliation_loop(interval_s=0.001))
            # Wait until at least 2 calls (1 failure + 1 success) happened
            for _ in range(1000):
                await asyncio.sleep(0.001)
                if calls["n"] >= 2:
                    break
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_run())
        assert calls["n"] >= 2, (
            "loop did not continue after a reconcile_once exception (WR-03)"
        )

    def test_loop_reraises_cancelled_error(self):
        """
        WR-03: asyncio.CancelledError raised inside reconcile_once must propagate
        so the loop shuts down cleanly (not swallowed as a transient error).
        """
        gw = _make_gateway_with_mocks()

        async def cancelling_reconcile():
            raise asyncio.CancelledError()

        gw.reconcile_once = cancelling_reconcile

        async def _run():
            with pytest.raises(asyncio.CancelledError):
                await gw.reconciliation_loop(interval_s=0.001)

        asyncio.run(_run())


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


# ============================================================
# MoomooGateway.subscribe() — K_5M run_in_executor wrapper (SIG-01)
# ============================================================

class TestSubscribe:
    """SIG-01: subscribe() wraps quote_ctx.subscribe for K_5M in run_in_executor."""

    def test_subscribe_wraps_executor(self):
        """gateway.subscribe(['US.AAPL']) calls quote_ctx.subscribe once with K_5M args."""
        from moomoo import SubType, Session
        gw = _make_gateway_with_mocks()
        # Wire mock quote_ctx.subscribe to return (RET_OK, "")
        gw._quote_ctx.subscribe.return_value = (0, "")

        asyncio.run(gw.subscribe(["US.AAPL"]))

        gw._quote_ctx.subscribe.assert_called_once_with(
            ["US.AAPL"],
            [SubType.K_5M],
            is_first_push=True,
            subscribe_push=True,
            extended_time=False,
            session=Session.NONE,
        )

    def test_subscribe_defaults_to_k5m(self):
        """Calling subscribe without subtypes defaults to [SubType.K_5M]."""
        from moomoo import SubType
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.subscribe.return_value = (0, "")

        asyncio.run(gw.subscribe(["US.MSFT"]))

        call_args = gw._quote_ctx.subscribe.call_args
        # Second positional arg is the subtypes list
        subtypes_used = call_args[0][1]
        assert subtypes_used == [SubType.K_5M], (
            f"Default subtypes must be [SubType.K_5M], got {subtypes_used}"
        )

    def test_subscribe_raises_on_non_ret_ok(self):
        """quote_ctx.subscribe returning non-zero must raise GatewayError."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.subscribe.return_value = (1, "quota exceeded")

        with pytest.raises(GatewayError):
            asyncio.run(gw.subscribe(["US.AAPL"]))


# ============================================================
# MoomooGateway.unsubscribe() — release evicted feeds (WR-01 / SIG-01)
# ============================================================

class TestUnsubscribe:
    """WR-01: unsubscribe() releases quota slots for evicted codes."""

    def test_unsubscribe_wraps_executor(self):
        """gateway.unsubscribe(['US.AAPL']) calls quote_ctx.unsubscribe once with K_5M."""
        from moomoo import SubType
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.unsubscribe.return_value = (0, "")

        asyncio.run(gw.unsubscribe(["US.AAPL"]))

        gw._quote_ctx.unsubscribe.assert_called_once_with(
            ["US.AAPL"],
            [SubType.K_5M],
        )

    def test_unsubscribe_empty_is_noop(self):
        """unsubscribe([]) must not touch the broker (no quota call for an empty set)."""
        gw = _make_gateway_with_mocks()

        asyncio.run(gw.unsubscribe([]))

        gw._quote_ctx.unsubscribe.assert_not_called()

    def test_unsubscribe_raises_on_non_ret_ok(self):
        """quote_ctx.unsubscribe returning non-zero must raise GatewayError."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.unsubscribe.return_value = (1, "unsub failed")

        with pytest.raises(GatewayError):
            asyncio.run(gw.unsubscribe(["US.AAPL"]))


# ============================================================
# MoomooGateway.get_equity() — live equity read (RISK-01, D-04/D-05)
# Implemented in 03-03
# ============================================================

class TestGetEquity:
    """get_equity() reads total_assets from accinfo_query (D-04/D-05)."""

    def _make_accinfo_df(self, total_assets: float) -> "pd.DataFrame":
        """Return a fake accinfo DataFrame with the given total_assets."""
        return pd.DataFrame([{"total_assets": total_assets}])

    def test_get_equity_reads_total_assets(self):
        """get_equity returns total_assets float from accinfo_query response (D-04)."""
        gw = _make_gateway_with_mocks()
        gw._trade_ctx.accinfo_query.return_value = (0, self._make_accinfo_df(250_000.0))

        async def _run():
            return await gw.get_equity()

        result = asyncio.run(_run())
        assert result == 250_000.0, (
            f"Expected 250000.0 (read total_assets), got {result}"
        )

    def test_get_equity_calls_refresh_cache(self):
        """get_equity passes refresh_cache=True to accinfo_query (RISK-01 live-not-cached, D-05)."""
        gw = _make_gateway_with_mocks()
        gw._trade_ctx.accinfo_query.return_value = (0, self._make_accinfo_df(100_500.0))

        async def _run():
            await gw.get_equity()

        asyncio.run(_run())

        call_kwargs = gw._trade_ctx.accinfo_query.call_args
        # refresh_cache=True must be present in keyword or positional args
        assert call_kwargs is not None, "accinfo_query was not called"
        all_kwargs = call_kwargs[1] if call_kwargs[1] else {}
        # Also check positional args for refresh_cache being True
        positional = call_kwargs[0] if call_kwargs[0] else ()
        assert all_kwargs.get("refresh_cache") is True or True in positional, (
            f"refresh_cache=True must be passed to accinfo_query; got kwargs={all_kwargs}, args={positional}"
        )

    def test_equity_fallback_on_ret_error(self):
        """get_equity returns 100000.0 when accinfo_query ret != RET_OK (D-05)."""
        gw = _make_gateway_with_mocks()
        # Non-zero ret indicates failure
        gw._trade_ctx.accinfo_query.return_value = (1, "error detail")

        async def _run():
            return await gw.get_equity()

        result = asyncio.run(_run())
        assert result == 100_000.0, (
            f"Expected fallback 100000.0 on ret error, got {result}"
        )

    def test_equity_fallback_on_implausible_low(self):
        """get_equity returns 100000.0 when total_assets < $1,000 (D-05, implausible-low guard)."""
        gw = _make_gateway_with_mocks()
        gw._trade_ctx.accinfo_query.return_value = (0, self._make_accinfo_df(500.0))

        async def _run():
            return await gw.get_equity()

        result = asyncio.run(_run())
        assert result == 100_000.0, (
            f"Expected fallback 100000.0 for implausible-low total_assets=500, got {result}"
        )

    def test_equity_fallback_on_implausible_high(self):
        """get_equity returns 100000.0 when total_assets > $10,000,000 (Security — corrupt read guard)."""
        gw = _make_gateway_with_mocks()
        gw._trade_ctx.accinfo_query.return_value = (0, self._make_accinfo_df(15_000_000.0))

        async def _run():
            return await gw.get_equity()

        result = asyncio.run(_run())
        assert result == 100_000.0, (
            f"Expected fallback 100000.0 for implausible-high total_assets=15000000, got {result}"
        )

    def test_equity_fallback_on_exception(self):
        """get_equity returns 100000.0 without raising when accinfo_query throws (D-05 degrade)."""
        gw = _make_gateway_with_mocks()
        gw._trade_ctx.accinfo_query.side_effect = RuntimeError("broker unavailable")

        async def _run():
            return await gw.get_equity()

        # Must not raise — must degrade gracefully
        result = asyncio.run(_run())
        assert result == 100_000.0, (
            f"Expected fallback 100000.0 on exception, got {result}"
        )


# ============================================================
# MoomooGateway.get_market_snapshot() — raw broker snapshot (D-01)
# Wave 0 stubs — implemented in 03-01 Task 3
# ============================================================

class TestGetMarketSnapshot:
    """get_market_snapshot() returns raw (ret, data) tuple unchanged (D-01)."""

    def test_get_market_snapshot_returns_tuple(self):
        """
        A mocked _quote_ctx.get_market_snapshot returning (RET_OK, df) makes
        get_market_snapshot return that exact (ret, data) tuple unchanged
        (thin broker read — no interpretation).
        """
        gw = _make_gateway_with_mocks()
        expected_df = pd.DataFrame([{"code": "US.AAPL", "pre_high_price": 155.0}])
        gw._quote_ctx.get_market_snapshot.return_value = (0, expected_df)

        async def _run():
            return await gw.get_market_snapshot(["US.AAPL"])

        ret, data = asyncio.run(_run())
        assert ret == 0, f"Expected RET_OK (0), got {ret}"
        assert data is expected_df, "Must return the exact DataFrame from the broker"

    def test_get_market_snapshot_ret_error_returned_as_is(self):
        """
        A non-RET_OK ret is returned as-is (the caller — 03-02 fetch helper —
        treats it as an empty result; the gateway never raises here).
        """
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_market_snapshot.return_value = (1, "error detail")

        async def _run():
            return await gw.get_market_snapshot(["US.AAPL"])

        ret, data = asyncio.run(_run())
        assert ret == 1, f"Non-RET_OK must be returned as-is; got {ret}"
        assert data == "error detail"

    def test_get_market_snapshot_passes_codes(self):
        """
        get_market_snapshot forwards the exact `codes` list to
        _quote_ctx.get_market_snapshot(codes) (<=20 watchlist codes).
        """
        codes = ["US.AAPL", "US.MSFT", "US.TSLA"]
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_market_snapshot.return_value = (0, pd.DataFrame())

        async def _run():
            return await gw.get_market_snapshot(codes)

        asyncio.run(_run())
        gw._quote_ctx.get_market_snapshot.assert_called_once_with(codes)


# ============================================================
# MoomooGateway.get_global_state() — health check (SVC-02)
# Added in Phase 5 Plan 00, Task 3
# ============================================================

class TestGetGlobalState:
    """get_global_state() returns a health-check dict and never raises."""

    def test_get_global_state_returns_dict_on_ret_ok(self):
        """RET_OK + both logined=True → connected:True dict."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_global_state.return_value = (
            0,
            {"qot_logined": True, "trd_logined": True, "server_ver": "10.4", "market_us": "1"},
        )

        async def _run():
            return await gw.get_global_state()

        result = asyncio.run(_run())
        assert isinstance(result, dict)
        assert result["connected"] is True
        assert result["qot_logined"] is True
        assert result["trd_logined"] is True

    def test_get_global_state_connected_false_when_only_qot_logined(self):
        """connected:False when trd_logined is False even if qot_logined is True."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_global_state.return_value = (
            0,
            {"qot_logined": True, "trd_logined": False, "server_ver": "", "market_us": ""},
        )

        async def _run():
            return await gw.get_global_state()

        result = asyncio.run(_run())
        assert result["connected"] is False

    def test_get_global_state_connected_false_on_non_ret_ok(self):
        """Non-RET_OK → connected:False without raising."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_global_state.return_value = (1, {})

        async def _run():
            return await gw.get_global_state()

        result = asyncio.run(_run())
        assert isinstance(result, dict)
        assert result["connected"] is False
        assert result["qot_logined"] is False
        assert result["trd_logined"] is False

    def test_get_global_state_never_raises_on_exception(self):
        """SDK raises → get_global_state returns fallback dict (never raises)."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_global_state.side_effect = RuntimeError("connection lost")

        async def _run():
            return await gw.get_global_state()

        # Must NOT raise — returns safe fallback
        result = asyncio.run(_run())
        assert isinstance(result, dict)
        assert result["connected"] is False

    def test_get_global_state_dict_has_required_keys(self):
        """Return dict always has connected, qot_logined, trd_logined keys."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_global_state.return_value = (
            0,
            {"qot_logined": True, "trd_logined": True, "server_ver": "10.4", "market_us": "1"},
        )

        async def _run():
            return await gw.get_global_state()

        result = asyncio.run(_run())
        assert "connected" in result
        assert "qot_logined" in result
        assert "trd_logined" in result


# ============================================================
# BLOCKER-01: set_handler delegates to quote context (SIG-01)
# ============================================================

def test_set_handler_delegates_to_quote_ctx():
    """set_handler() must call _quote_ctx.set_handler(handler) exactly once (BLOCKER-01).

    RED today: MoomooGateway has no set_handler method (AttributeError).
    Turns GREEN when Plan 06.1-02 adds the thin set_handler wrapper to MoomooGateway.
    """
    gw = _make_gateway_with_mocks()
    mock_handler = MagicMock()
    # INTENDED RED: gw.set_handler does not exist today (AttributeError)
    gw.set_handler(mock_handler)
    gw._quote_ctx.set_handler.assert_called_once_with(mock_handler)


# ============================================================
# SAFE-03: reconcile_once detects externally-closed position
# ============================================================

@pytest.mark.asyncio
async def test_reconcile_once_externally_closed():
    """reconcile_once() marks externally-closed position CLOSED and fires alert (SAFE-03).

    When broker returns empty positions while manager._positions has one ACTIVE code
    not in manager._exiting, reconcile_once must:
      - mark position CLOSED via store.conn.execute (DB update)
      - remove code from manager._positions
      - fire alerter.send (Telegram alert)
      - return the code in result["closed"]

    RED today: reconcile_once() is a no-op returning {"drift": {}} and its signature
    takes no store/manager/alerter args (TypeError on the call below).
    Turns GREEN when Plan 06.1-02 implements the drift logic and updates the signature.
    """
    gw = _make_gateway_with_mocks()
    # broker returns empty positions (externally closed)
    gw.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

    # manager has one active position for US.AAPL
    mock_pos = MagicMock()
    mock_pos.position_id = "pos-001"
    mock_pos.phase = "ACTIVE"
    mock_manager = MagicMock()
    mock_manager._positions = {"US.AAPL": mock_pos}
    mock_manager._exiting = set()

    mock_store = MagicMock()
    mock_store.conn = MagicMock()

    mock_alerter = MagicMock()
    mock_alerter.send = AsyncMock()

    # INTENDED RED: TypeError — reconcile_once() today takes no store/manager/alerter args
    result = await gw.reconcile_once(
        store=mock_store, manager=mock_manager, alerter=mock_alerter
    )

    assert "US.AAPL" in result["closed"], (
        f"US.AAPL must be in result['closed'], got: {result}"
    )
    mock_store.conn.execute.assert_called()  # DB update issued
