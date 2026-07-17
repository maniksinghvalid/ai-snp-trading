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

    def test_reconnect_closes_prior_contexts(self):
        """A second connect() (watchdog reconnect) must close the prior contexts
        before creating new ones — otherwise orphaned SDK contexts leak sockets
        until OpenD's 128-connection cap is blown."""
        cfg = GatewayConfig(acc_id=123456789, paper_trading=True, trd_env="SIMULATE")
        gw = MoomooGateway(cfg)
        old_quote, old_trade = MagicMock(), _make_mock_ctx(acc_id=123456789)
        new_quote, new_trade = MagicMock(), _make_mock_ctx(acc_id=123456789)
        quotes = iter([old_quote, new_quote])
        trades = iter([old_trade, new_trade])

        with patch("bot.gateway.gateway._check_opend_alive"):
            with patch.object(gw, "_make_quote_ctx", side_effect=lambda: next(quotes)):
                with patch.object(gw, "_make_trade_ctx", side_effect=lambda: next(trades)):
                    with patch("bot.gateway.gateway.assert_paper_account"):
                        gw.connect()   # first connect
                        gw.connect()   # reconnect

        old_quote.close.assert_called_once()
        old_trade.close.assert_called_once()
        assert gw._quote_ctx is new_quote and gw._trade_ctx is new_trade


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
        gw.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_store = MagicMock()
        mock_store.conn = MagicMock()
        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )

        result = asyncio.run(_run())
        assert isinstance(result, dict)

    def test_reconcile_once_dict_has_required_keys(self):
        """reconcile_once dict must have 'closed' and 'adopted' keys (SAFE-03 drift result)."""
        gw = _make_gateway_with_mocks()
        gw.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_store = MagicMock()
        mock_store.conn = MagicMock()
        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )

        result = asyncio.run(_run())
        assert "closed" in result
        assert "adopted" in result

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
        mock_store = MagicMock()
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_alerter = MagicMock()

        async def flaky_reconcile(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated broker blip")
            return {"closed": [], "adopted": []}

        gw.reconcile_once = flaky_reconcile

        async def _run():
            # Tiny interval so the loop iterates quickly; cancel once it has
            # survived the first (raising) iteration and done a second one.
            task = asyncio.ensure_future(
                gw.reconciliation_loop(
                    store=mock_store, manager=mock_manager, alerter=mock_alerter,
                    interval_s=0.001,
                )
            )
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
        mock_store = MagicMock()
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_alerter = MagicMock()

        async def cancelling_reconcile(**kwargs):
            raise asyncio.CancelledError()

        gw.reconcile_once = cancelling_reconcile

        async def _run():
            with pytest.raises(asyncio.CancelledError):
                await gw.reconciliation_loop(
                    store=mock_store, manager=mock_manager, alerter=mock_alerter,
                    interval_s=0.001,
                )

        asyncio.run(_run())

    # ------------------------------------------------------------------
    # 06.1-07: CR-01 / CR-03 / WR-03 / WR-04 — in-memory effect tests
    # These drive the in-memory effects of reconcile_once, not just
    # the return-dict shape (the gap the existing tests left open).
    # ------------------------------------------------------------------

    def test_reconcile_once_reprotects_closed_but_held(self):
        """CR-01: CLOSED-but-held position is re-armed in memory (SAFE-03).

        When a code is in manager._positions with phase==CLOSED but broker still
        holds shares (present in broker_map), reconcile_once must:
          - NOT delete the position from _positions
          - Re-arm to a managed phase (phase != CLOSED after call)
          - Sync remaining_quantity to broker qty
          - Audit reconcile_qty_drift
          - Fire a DRIFT alert
        """
        from bot.position.state import PositionPhase

        gw = _make_gateway_with_mocks()
        # Broker still holds 150 shares
        broker_df = pd.DataFrame([{
            "code": "US.AAPL", "qty": 150, "average_cost": 151.0
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))

        # In-memory position is CLOSED (the bug state)
        mock_pos = MagicMock()
        mock_pos.position_id = "pos-close-001"
        mock_pos.phase = PositionPhase.CLOSED
        mock_pos.remaining_quantity = 150

        mock_manager = MagicMock()
        mock_manager._positions = {"US.AAPL": mock_pos}
        mock_manager._exiting = set()

        mock_store = MagicMock()
        mock_store.conn = MagicMock()

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        result = asyncio.run(_run())

        # Position must NOT be deleted from _positions
        assert "US.AAPL" in mock_manager._positions, (
            "CLOSED-but-held position must remain in _positions after re-arm"
        )
        # Phase must be re-armed (not CLOSED)
        assert mock_pos.phase != PositionPhase.CLOSED, (
            f"Position phase must be re-armed (not CLOSED), got {mock_pos.phase}"
        )
        # remaining_quantity must be synced to broker qty
        assert mock_pos.remaining_quantity == 150, (
            f"remaining_quantity must be synced to broker qty=150, got {mock_pos.remaining_quantity}"
        )
        # DRIFT alert must be fired
        mock_alerter.send.assert_called()

    def test_reconcile_once_qty_drift_adopts_broker_qty(self):
        """CR-01: qty drift — stored remaining != broker qty → remaining updated.

        When code is in both _positions and broker_map, phase managed (ACTIVE),
        but remaining_quantity differs, reconcile_once must update remaining_quantity
        to broker qty and audit reconcile_qty_drift.
        """
        from bot.position.state import PositionPhase

        gw = _make_gateway_with_mocks()
        # Broker has 180 shares; memory has 200
        broker_df = pd.DataFrame([{
            "code": "US.AAPL", "qty": 180, "average_cost": 150.0
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))

        mock_pos = MagicMock()
        mock_pos.position_id = "pos-drift-001"
        mock_pos.phase = PositionPhase.ACTIVE
        mock_pos.remaining_quantity = 200  # diverges from broker's 180

        mock_manager = MagicMock()
        mock_manager._positions = {"US.AAPL": mock_pos}
        mock_manager._exiting = set()

        mock_store = MagicMock()
        mock_store.conn = MagicMock()
        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        asyncio.run(_run())

        # remaining_quantity must be updated to broker qty
        assert mock_pos.remaining_quantity == 180, (
            f"remaining_quantity must be synced to 180 (broker), got {mock_pos.remaining_quantity}"
        )
        # DB update must have been issued via guarded method (CR-01 / 06.1-09)
        mock_store.update_position_qty_phase.assert_called()

    def test_reconcile_once_orphan_registers_in_memory(self):
        """CR-03: orphan broker position is registered in manager._positions via adopt_orphan.

        When broker has a code not in manager._positions, reconcile_once must:
          - Call manager.adopt_orphan(code=..., qty=..., avg_cost=..., stop=...)
          - The code must be present in manager._positions after the call
          - subscribe must be called once
          - drift_orphan_adopted audit emitted only when row actually inserted
        """
        from bot.position.state import PositionPhase

        gw = _make_gateway_with_mocks()
        # Broker has an orphan
        broker_df = pd.DataFrame([{
            "code": "US.NVDA", "qty": 100, "average_cost": 500.0
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=495.0)
        gw._compute_orphan_stop = MagicMock(return_value=490.0)
        gw.subscribe = AsyncMock()

        # Track adopt_orphan calls; wire a fake that registers in _positions
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()

        def _fake_adopt(code, qty, avg_cost, stop, position_id=None):
            pos = MagicMock()
            pos.phase = PositionPhase.ACTIVE
            pos.remaining_quantity = qty
            mock_manager._positions[code] = pos
            return pos

        mock_manager.adopt_orphan = MagicMock(side_effect=_fake_adopt)

        mock_store = MagicMock()
        # Simulate successful INSERT: insert_orphan_position returns rowcount=1
        # (CR-01 / 06.1-09: guarded store method replaces raw conn.execute)
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        asyncio.run(_run())

        # adopt_orphan must have been called
        mock_manager.adopt_orphan.assert_called_once()

        # Position must be in _positions
        assert "US.NVDA" in mock_manager._positions, (
            "Orphan must be registered in _positions after adopt_orphan call"
        )

        # subscribe must have been called
        gw.subscribe.assert_called_once()

    def test_reconcile_once_drift_alert_task_is_retained(self):
        """WR-03: DRIFT alert task is stored in self._bg_tasks (not bare create_task).

        The gateway instance must have a _bg_tasks set attribute. When
        reconcile_once fires an alert (externally-closed path), the task must be
        retained in _bg_tasks so it is not GC'd before the send completes.
        """
        gw = _make_gateway_with_mocks()
        gw.get_positions = AsyncMock(return_value=(0, pd.DataFrame()))

        mock_pos = MagicMock()
        mock_pos.position_id = "pos-wr03-001"
        mock_manager = MagicMock()
        mock_manager._positions = {"US.AAPL": mock_pos}
        mock_manager._exiting = set()

        mock_store = MagicMock()
        mock_store.conn = MagicMock()

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )

        asyncio.run(_run())

        # _bg_tasks set must exist on the gateway (WR-03 retention)
        assert hasattr(gw, "_bg_tasks"), (
            "MoomooGateway must have a _bg_tasks set (WR-03: retain alert task reference)"
        )
        assert isinstance(gw._bg_tasks, set), (
            f"_bg_tasks must be a set, got {type(gw._bg_tasks)}"
        )

    def test_reconcile_once_no_false_audit_on_insert_noop(self):
        """WR-04: INSERT OR IGNORE no-op (rowcount==0) does NOT emit drift_orphan_adopted.

        When the orphan INSERT OR IGNORE silently no-ops (collision / pre-existing row),
        cursor.rowcount == 0 → drift_orphan_adopted must NOT be audited, and
        adopt_orphan must NOT be called.
        """
        import bot.safety.audit_log as _al

        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.MSFT", "qty": 50, "average_cost": 300.0
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=295.0)
        gw._compute_orphan_stop = MagicMock(return_value=292.0)
        gw.subscribe = AsyncMock()

        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()

        mock_store = MagicMock()
        # Simulate INSERT OR IGNORE no-op: insert_orphan_position returns rowcount=0
        # (CR-01 / 06.1-09: guarded store method replaces raw conn.execute)
        mock_store.insert_orphan_position = MagicMock(return_value=0)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        # Capture audit writes
        audit_entries = []
        orig_append = _al.append_audit
        _al.append_audit = lambda entry: audit_entries.append(entry)
        try:
            async def _run():
                return await gw.reconcile_once(
                    store=mock_store, manager=mock_manager, alerter=mock_alerter
                )
            asyncio.run(_run())
        finally:
            _al.append_audit = orig_append

        # drift_orphan_adopted must NOT be in audit entries (WR-04)
        adopted_events = [e for e in audit_entries if e.get("event") == "drift_orphan_adopted"]
        assert len(adopted_events) == 0, (
            f"drift_orphan_adopted must NOT be audited on no-op INSERT; "
            f"got {adopted_events}"
        )
        # adopt_orphan must NOT be called on INSERT no-op
        if hasattr(mock_manager.adopt_orphan, "call_count"):
            assert mock_manager.adopt_orphan.call_count == 0, (
                "adopt_orphan must NOT be called on INSERT no-op (rowcount==0)"
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
# MoomooGateway.get_positions() — account routing (T-01-01 / BUG-01)
# ============================================================

class TestGetPositions:
    """get_positions() must forward trd_env and acc_id to position_list_query (T-01-01).

    BUG-01 regression guard: gateway.py:316 called position_list_query(refresh_cache=...)
    without trd_env or acc_id. The moomoo SDK defaults those to TrdEnv.REAL / acc_id=0,
    so reconciliation silently read the REAL account (NVDY +26 long) while orders
    executed on SIMULATE acc 1727266 (NVDY -130 short). Reconcile never saw the short,
    kept re-arming NVDY to +26 ACTIVE, and force-close sold another 26 each day.
    This test is the regression guard; it must FAIL before the fix and PASS after.
    """

    def test_get_positions_forwards_trd_env_and_acc_id(self):
        """get_positions() must pass trd_env and acc_id to position_list_query (T-01-01 / BUG-01).

        RED (before fix): call only has refresh_cache — trd_env and acc_id absent,
        so the SDK defaults to TrdEnv.REAL / acc_id=0, reading the wrong account.
        GREEN (after fix): all three kwargs forwarded, reading SIMULATE acc 1727266.
        """
        gw = _make_gateway_with_mocks(acc_id=1727266, broker_trd_env="SIMULATE")

        async def _run():
            await gw.get_positions()

        asyncio.run(_run())

        call_args = gw._trade_ctx.position_list_query.call_args
        assert call_args is not None, "position_list_query was not called"
        kwargs = call_args[1] if call_args[1] else {}

        assert "trd_env" in kwargs, (
            f"trd_env must be forwarded to position_list_query; got kwargs={kwargs}. "
            "Without trd_env the SDK defaults to TrdEnv.REAL, reading the wrong account "
            "(T-01-01 / BUG-01)."
        )
        assert "acc_id" in kwargs, (
            f"acc_id must be forwarded to position_list_query; got kwargs={kwargs}. "
            "Without acc_id the SDK defaults to acc_id=0, reading the wrong account "
            "(T-01-01 / BUG-01)."
        )
        assert kwargs["trd_env"] == _parse_trd_env("SIMULATE"), (
            f"trd_env must equal TrdEnv.SIMULATE (from cfg.trd_env); got {kwargs.get('trd_env')} "
            "(T-01-01 / BUG-01)."
        )
        assert kwargs["acc_id"] == 1727266, (
            f"acc_id must be 1727266 (cfg.acc_id); got {kwargs.get('acc_id')} "
            "(T-01-01 / BUG-01)."
        )

    def test_get_positions_forwards_refresh_cache(self):
        """get_positions() must also forward refresh_cache to position_list_query (Pitfall B).

        Regression guard: ensure the refresh_cache kwarg is not dropped when
        trd_env and acc_id are added to the call (minimal-diff correctness check).
        """
        gw = _make_gateway_with_mocks(acc_id=1727266, broker_trd_env="SIMULATE")

        async def _run():
            await gw.get_positions(refresh_cache=True)

        asyncio.run(_run())

        call_args = gw._trade_ctx.position_list_query.call_args
        assert call_args is not None, "position_list_query was not called"
        kwargs = call_args[1] if call_args[1] else {}

        assert kwargs.get("refresh_cache") is True, (
            f"refresh_cache=True must be forwarded to position_list_query (Pitfall B); "
            f"got kwargs={kwargs}"
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
# MoomooGateway.get_bid_price() / get_ask_price() — Finding 2.4
# A snapshot failure (or a zero price after the last_price fallback) must
# raise GatewayError, never silently return 0.0 (which would let the engine
# compute a negative/zero exit limit and silently abort a stop-out).
# ============================================================

class TestGetBidAskPriceRaisesOnFailure:
    def test_get_bid_price_raises_on_snapshot_failure(self):
        """ret != RET_OK (or empty data) must raise GatewayError, not return 0.0."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_market_snapshot.return_value = (1, None)

        async def _run():
            return await gw.get_bid_price("US.AAPL")

        with pytest.raises(GatewayError):
            asyncio.run(_run())

    def test_get_bid_price_raises_on_zero_price(self):
        """A zero bid_price with no usable last_price fallback must raise, not return 0.0."""
        gw = _make_gateway_with_mocks()
        df = pd.DataFrame([{"code": "US.AAPL", "bid_price": 0.0, "last_price": 0.0}])
        gw._quote_ctx.get_market_snapshot.return_value = (0, df)

        async def _run():
            return await gw.get_bid_price("US.AAPL")

        with pytest.raises(GatewayError):
            asyncio.run(_run())

    def test_get_bid_price_returns_valid_price(self):
        """A healthy snapshot still returns the bid_price float (no behavior change)."""
        gw = _make_gateway_with_mocks()
        df = pd.DataFrame([{"code": "US.AAPL", "bid_price": 150.25, "last_price": 150.30}])
        gw._quote_ctx.get_market_snapshot.return_value = (0, df)

        async def _run():
            return await gw.get_bid_price("US.AAPL")

        assert asyncio.run(_run()) == 150.25

    def test_get_ask_price_raises_on_snapshot_failure(self):
        """ret != RET_OK (or empty data) must raise GatewayError, not return 0.0."""
        gw = _make_gateway_with_mocks()
        gw._quote_ctx.get_market_snapshot.return_value = (1, None)

        async def _run():
            return await gw.get_ask_price("US.AAPL")

        with pytest.raises(GatewayError):
            asyncio.run(_run())

    def test_get_ask_price_returns_valid_price(self):
        """A healthy snapshot still returns the ask_price float (no behavior change)."""
        gw = _make_gateway_with_mocks()
        df = pd.DataFrame([{"code": "US.AAPL", "ask_price": 150.75, "last_price": 150.70}])
        gw._quote_ctx.get_market_snapshot.return_value = (0, df)

        async def _run():
            return await gw.get_ask_price("US.AAPL")

        assert asyncio.run(_run()) == 150.75


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

# ============================================================
# SAFE-OG-01: Orphan-adoption ownership + long-only guard
# ============================================================

class TestOrphanOwnershipGuard:
    """Regression tests: orphan-adoption must check bot-ownership AND long-only.

    Before the guard, reconcile_once and startup_reconcile adopted ANY broker
    position not in memory/DB — including manual operator positions and shorts.
    These tests enforce SAFE-OG-01: both sites must:
      (a) bot-owned: has_pending_intent(code) OR code in get_open_positions()
      (b) long-only: broker qty > 0
    On failure: skip + log reconcile_external_position_ignored.
    Crash-recovery is preserved because pending_intent is written BEFORE
    place_order (engine.py, EXEC-04 docstring line ~108).
    """

    # ----------------------------------------------------------------
    # reconcile_once tests
    # ----------------------------------------------------------------

    def test_reconcile_once_does_not_adopt_manual_long(self):
        """Test A: long broker position with no bot DB record is NOT adopted.

        Broker has US.AAPL qty=17 (manual operator long). Bot has no
        pending_intent and no open_positions row. insert_orphan_position and
        adopt_orphan must NOT be called.
        """
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.AAPL", "qty": 17, "average_cost": 195.0,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=194.0)
        gw._compute_orphan_stop = MagicMock(return_value=192.0)
        gw.subscribe = AsyncMock()

        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_manager.adopt_orphan = MagicMock()

        mock_store = MagicMock()
        mock_store.get_open_positions.return_value = []   # no DB record
        mock_store.has_pending_intent.return_value = False  # no pending intent
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        result = asyncio.run(_run())

        # Guard must reject: no DB record exists for this code
        mock_store.insert_orphan_position.assert_not_called()
        mock_manager.adopt_orphan.assert_not_called()
        assert result["adopted"] == [], (
            f"Manual long with no bot record must NOT appear in adopted; got {result['adopted']}"
        )

    def test_reconcile_once_does_not_adopt_short(self):
        """Test B: short broker position (qty < 0) is NOT adopted.

        Broker has US.MARA qty=-2 (short option). Long-only guard must
        reject it regardless of ownership.
        """
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.MARA", "qty": -2, "average_cost": 0.5,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=20.0)
        gw._compute_orphan_stop = MagicMock(return_value=19.0)
        gw.subscribe = AsyncMock()

        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_manager.adopt_orphan = MagicMock()

        mock_store = MagicMock()
        mock_store.get_open_positions.return_value = []
        mock_store.has_pending_intent.return_value = False
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        result = asyncio.run(_run())

        # Guard must reject: qty < 0
        mock_store.insert_orphan_position.assert_not_called()
        mock_manager.adopt_orphan.assert_not_called()
        assert result["adopted"] == [], (
            f"Short position must NOT appear in adopted; got {result['adopted']}"
        )

    def test_reconcile_once_adopts_bot_owned_long_with_pending_intent(self):
        """Test C: crash-recovery — bot-owned long with pending_intent IS adopted.

        Broker has US.NVDA qty=50 (long). Bot has a PENDING intent (placed
        order, crashed before DB position row was written). The guard must
        PASS and adopt_orphan must be called so crash-recovery works.
        """
        from bot.position.state import PositionPhase

        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.NVDA", "qty": 50, "average_cost": 800.0,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=798.0)
        gw._compute_orphan_stop = MagicMock(return_value=790.0)
        gw.subscribe = AsyncMock()

        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()

        def _fake_adopt(code, qty, avg_cost, stop, position_id=None):
            pos = MagicMock()
            pos.phase = PositionPhase.ACTIVE
            pos.remaining_quantity = qty
            mock_manager._positions[code] = pos
            return pos

        mock_manager.adopt_orphan = MagicMock(side_effect=_fake_adopt)

        mock_store = MagicMock()
        mock_store.get_open_positions.return_value = []   # no prior position row (crash)
        mock_store.has_pending_intent.return_value = True  # pending_intent written pre-crash
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        result = asyncio.run(_run())

        # Guard must pass: has_pending_intent=True + qty>0
        mock_manager.adopt_orphan.assert_called_once()
        mock_store.insert_orphan_position.assert_called_once()
        assert "US.NVDA" in result["adopted"], (
            f"Bot-owned long with pending_intent must be adopted; got {result['adopted']}"
        )

    def test_adopt_orphan_rejects_duplicate_code(self):
        """Finding 2.5 regression: reconcile_once must NEVER insert a second open
        position row for a code that already has one in the DB.

        Simulates the gap where manager._positions has lost track of a code
        in-memory (e.g. after a restart before reconstruct_from_store runs) but
        store.get_open_positions() still reports an open row for it. Without the
        SELECT-before-INSERT guard, insert_orphan_position's INSERT OR IGNORE
        (keyed on position_id, a fresh UUID each time) never collides on code —
        producing a second, double-managed row for the same symbol.
        """
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.NVDA", "qty": 50, "average_cost": 800.0,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=798.0)
        gw._compute_orphan_stop = MagicMock(return_value=790.0)
        gw.subscribe = AsyncMock()

        # In-memory has lost track of US.NVDA (restart gap) — NOT in _positions.
        mock_manager = MagicMock()
        mock_manager._positions = {}
        mock_manager._exiting = set()
        mock_manager.adopt_orphan = MagicMock()

        mock_store = MagicMock()
        # DB already has an open row for US.NVDA (phase != CLOSED).
        mock_store.get_open_positions.return_value = [
            {"position_id": "POS-EXISTING", "code": "US.NVDA", "phase": "ACTIVE"},
        ]
        mock_store.has_pending_intent.return_value = False
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter = MagicMock()
        mock_alerter.send = AsyncMock()

        async def _run():
            return await gw.reconcile_once(
                store=mock_store, manager=mock_manager, alerter=mock_alerter
            )
        result = asyncio.run(_run())

        # Duplicate guard must reject: an open row for this code already exists.
        mock_store.insert_orphan_position.assert_not_called()
        mock_manager.adopt_orphan.assert_not_called()
        assert result["adopted"] == [], (
            f"A code with an existing open DB row must never be re-adopted; "
            f"got {result['adopted']}"
        )

        # No-code duplicate: an unrelated open DB row for a different code must
        # NOT block adoption of a genuinely new orphan.
        broker_df2 = pd.DataFrame([{
            "code": "US.TSLA", "qty": 25, "average_cost": 250.0,
        }])
        gw2 = _make_gateway_with_mocks()
        gw2.get_positions = AsyncMock(return_value=(0, broker_df2))
        gw2._derive_lod_for_orphan = AsyncMock(return_value=248.0)
        gw2._compute_orphan_stop = MagicMock(return_value=245.0)
        gw2.subscribe = AsyncMock()

        mock_manager2 = MagicMock()
        mock_manager2._positions = {}
        mock_manager2._exiting = set()
        mock_manager2.adopt_orphan = MagicMock()

        mock_store2 = MagicMock()
        mock_store2.get_open_positions.return_value = [
            {"position_id": "POS-OTHER", "code": "US.NVDA", "phase": "ACTIVE"},
        ]
        # Bot-owned via crash-recovery pending_intent (SAFE-OG-01) — unrelated to
        # the duplicate-code guard under test here.
        mock_store2.has_pending_intent.return_value = True
        mock_store2.insert_orphan_position = MagicMock(return_value=1)

        mock_alerter2 = MagicMock()
        mock_alerter2.send = AsyncMock()

        async def _run2():
            return await gw2.reconcile_once(
                store=mock_store2, manager=mock_manager2, alerter=mock_alerter2
            )
        result2 = asyncio.run(_run2())

        mock_store2.insert_orphan_position.assert_called_once()
        assert "US.TSLA" in result2["adopted"], (
            f"A genuinely new orphan code must still be adopted; got {result2['adopted']}"
        )

    # ----------------------------------------------------------------
    # startup_reconcile tests
    # ----------------------------------------------------------------

    def test_startup_reconcile_does_not_adopt_manual_long(self):
        """Test D (startup): long broker position with no bot DB record is NOT adopted.

        startup_reconcile must not adopt US.AAPL (manual operator position)
        when has_pending_intent returns False. insert_orphan_position must NOT
        be called.
        """
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.AAPL", "qty": 17, "average_cost": 195.0,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=194.0)
        gw._compute_orphan_stop = MagicMock(return_value=192.0)
        gw.subscribe = AsyncMock()

        mock_store = MagicMock()
        mock_store.get_open_positions.return_value = []   # no DB positions
        mock_store.has_pending_intent.return_value = False  # no pending intent
        mock_store.get_pending_intent_codes.return_value = []
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_manager = MagicMock()

        async def _run():
            await gw.startup_reconcile(store=mock_store, manager=mock_manager)
        asyncio.run(_run())

        # Guard must reject: no bot DB record
        mock_store.insert_orphan_position.assert_not_called()

    def test_startup_reconcile_adopts_bot_owned_long_with_pending_intent(self):
        """Test E (startup): crash-recovery — bot-owned long with pending_intent IS adopted.

        startup_reconcile must adopt US.NVDA when has_pending_intent returns
        True (bot placed order, crashed before position row was written).
        insert_orphan_position MUST be called.
        """
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{
            "code": "US.NVDA", "qty": 50, "average_cost": 800.0,
        }])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        gw._derive_lod_for_orphan = AsyncMock(return_value=798.0)
        gw._compute_orphan_stop = MagicMock(return_value=790.0)
        gw.subscribe = AsyncMock()

        mock_store = MagicMock()
        mock_store.get_open_positions.return_value = []   # crash: no position row yet
        mock_store.has_pending_intent.return_value = True  # pending_intent written pre-crash
        mock_store.get_pending_intent_codes.return_value = []
        mock_store.insert_orphan_position = MagicMock(return_value=1)

        mock_manager = MagicMock()

        async def _run():
            await gw.startup_reconcile(store=mock_store, manager=mock_manager)
        asyncio.run(_run())

        # Guard must pass: has_pending_intent=True + qty>0
        mock_store.insert_orphan_position.assert_called_once()


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
      - mark position CLOSED via store.mark_position_closed() guarded method
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
    mock_store.mark_position_closed.assert_called()  # DB update via guarded method (CR-01 / 06.1-09)


# ============================================================
# Finding 1.2: reconcile_once must skip cycle on failed position_list_query
# ============================================================

@pytest.mark.asyncio
async def test_reconcile_once_skips_on_broker_query_failure():
    """Regression 1.2: a failed position_list_query must abort the reconcile cycle.

    When get_positions() returns ret != RET_OK (broker error), reconcile_once must:
    - Return an empty result dict immediately (no cycle)
    - NOT call any mark_position_closed / manager mutation
    - Emit the structured log event 'reconcile_skipped_broker_query_failed'

    Previously, the guard only FILLED broker_map on RET_OK but still ran the
    reconcile loop with an empty broker_map, treating all in-memory positions as
    'externally closed' and wiping them on any transient broker glitch.
    """
    gw = _make_gateway_with_mocks()

    # Inject a position into the gateway's mock so reconcile has something to check.
    from bot.position.state import PositionState, PositionPhase
    from bot.position.manager import PositionManager

    mock_manager = MagicMock()
    mock_manager._positions = {
        "US.AAPL": MagicMock(
            code="US.AAPL",
            phase=PositionPhase.ACTIVE,
            remaining_quantity=100,
            entry_order_id="ORD-111",
        )
    }
    mock_manager._exiting = set()

    mock_store = MagicMock()
    mock_alerter = MagicMock()
    mock_alerter.send = AsyncMock()

    # Make position_list_query return ret=1 (error) — simulates transient broker failure.
    gw._trade_ctx.position_list_query.return_value = (1, None)

    result = await gw.reconcile_once(mock_store, mock_manager, mock_alerter)

    # Must return empty dict without touching positions.
    assert result == {}, (
        f"reconcile_once must return empty dict on broker failure, got: {result}"
    )
    # No position must be marked closed from a failed query.
    mock_store.mark_position_closed.assert_not_called()
    # Manager positions must not have been mutated.
    assert "US.AAPL" in mock_manager._positions, (
        "reconcile_once must not remove positions on a failed query"
    )


# ============================================================
# Finding 3.1: reconcile_once and startup_reconcile share one _reconcile_core
# ============================================================

@pytest.mark.asyncio
async def test_startup_reconcile_delegates_to_shared_core():
    """Finding 3.1 regression: reconcile_once AND startup_reconcile must drive the
    SAME shared close/adopt/protect logic via a single extracted _reconcile_core,
    instead of two ~200-line near-duplicate bodies (guard-drift risk).

    Spies on gw._reconcile_core and asserts BOTH entry points call it exactly once,
    each with the broker_map built from get_positions() -- and that startup_reconcile
    passes alerter=None (no Telegram alerts at cold boot; matches its pre-refactor
    behavior of skipping alerts entirely).
    """
    gw = _make_gateway_with_mocks()
    broker_df = pd.DataFrame([{"code": "US.AAPL", "qty": 100, "average_cost": 150.0}])
    gw.get_positions = AsyncMock(return_value=(0, broker_df))

    mock_manager = MagicMock()
    mock_manager._positions = {}
    mock_manager._exiting = set()

    mock_store = MagicMock()
    mock_store.get_pending_intent_codes.return_value = []

    mock_alerter = MagicMock()
    mock_alerter.send = AsyncMock()

    expected_broker_map = {"US.AAPL": {"qty": 100, "avg_cost": 150.0}}
    gw._reconcile_core = AsyncMock(
        return_value={"closed": [], "adopted": [], "reprotected": []}
    )

    await gw.reconcile_once(store=mock_store, manager=mock_manager, alerter=mock_alerter)
    await gw.startup_reconcile(store=mock_store, manager=mock_manager)

    assert gw._reconcile_core.call_count == 2, (
        "both reconcile_once and startup_reconcile must delegate to _reconcile_core"
    )

    once_call, startup_call = gw._reconcile_core.call_args_list

    assert once_call.kwargs.get("broker_map") == expected_broker_map
    assert once_call.kwargs.get("alerter") is mock_alerter

    assert startup_call.kwargs.get("broker_map") == expected_broker_map
    assert startup_call.kwargs.get("alerter") is None, (
        "startup_reconcile must delegate with alerter=None (no alerts at cold boot)"
    )


# ============================================================
# TestGetExternalCodes — SAFE-OG-01 (260702-ick Task 1)
# ============================================================

class TestGetExternalCodes:
    """Tests for MoomooGateway.get_external_codes(store).

    SAFE-OG-01 ownership predicate: returns the set of broker-held codes that
    the bot does NOT own (i.e. manual/external holdings). Raises GatewayError
    when the broker position query fails.
    """

    def _make_store(self, open_pos_codes=(), pending_codes=()):
        """Return a mock store with configurable ownership data."""
        store = MagicMock()
        store.get_open_positions.return_value = [{"code": c} for c in open_pos_codes]
        store.has_pending_intent.side_effect = lambda code: code in set(pending_codes)
        return store

    def test_held_and_bot_owned_via_position_excluded(self):
        """A code held at broker AND in the bot DB positions is NOT external."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{"code": "US.AAPL", "qty": 100}])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store(open_pos_codes=["US.AAPL"])

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert "US.AAPL" not in result, (
            "A code owned via DB position must NOT be in external codes"
        )

    def test_held_and_bot_owned_via_pending_intent_excluded(self):
        """A code held at broker AND with a pending intent is NOT external."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{"code": "US.MSFT", "qty": 50}])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store(pending_codes=["US.MSFT"])

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert "US.MSFT" not in result, (
            "A code with a pending intent must NOT be in external codes"
        )

    def test_held_and_not_bot_owned_included(self):
        """A code held at broker with NO bot DB record is external (manual holding)."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{"code": "US.NVDA", "qty": 200}])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store()  # no ownership for NVDA

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert "US.NVDA" in result, (
            "A code held at broker without bot ownership must be in external codes"
        )

    def test_short_position_not_bot_owned_included(self):
        """A short (qty < 0) not owned by the bot is external — never bot-tradable."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{"code": "US.TSLA", "qty": -10}])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store()

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert "US.TSLA" in result, (
            "A short position not owned by the bot must be in external codes"
        )

    def test_broker_query_failure_raises_gateway_error(self):
        """When broker position query returns ret != RET_OK, GatewayError is raised."""
        gw = _make_gateway_with_mocks()
        gw.get_positions = AsyncMock(return_value=(-1, None))
        store = self._make_store()

        async def _run():
            return await gw.get_external_codes(store)

        with pytest.raises(GatewayError):
            asyncio.run(_run())

    def test_returns_set(self):
        """get_external_codes returns a set, not a list."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([{"code": "US.NVDA", "qty": 200}])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store()

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert isinstance(result, set), "get_external_codes must return a set"

    def test_mixed_owned_and_external(self):
        """Mixed broker holdings — only the unowned ones are external."""
        gw = _make_gateway_with_mocks()
        broker_df = pd.DataFrame([
            {"code": "US.AAPL", "qty": 100},   # bot-owned (position)
            {"code": "US.NVDA", "qty": 200},   # external (manual)
            {"code": "US.MSFT", "qty": 50},    # bot-owned (pending intent)
        ])
        gw.get_positions = AsyncMock(return_value=(0, broker_df))
        store = self._make_store(open_pos_codes=["US.AAPL"], pending_codes=["US.MSFT"])

        async def _run():
            return await gw.get_external_codes(store)

        result = asyncio.run(_run())
        assert "US.AAPL" not in result
        assert "US.MSFT" not in result
        assert "US.NVDA" in result


# ============================================================
# MoomooGateway._compute_orphan_stop() — Finding 2.8
# The orphan-adoption stop must be derived from rules.json's
# initial_stop_pct (CFG-01 single source of truth), not a hardcoded
# lod * 0.99 that silently diverges from the live strategy's stop.
# ============================================================

class TestComputeOrphanStopUsesConfigPct:
    def test_compute_orphan_stop_uses_config_pct(self):
        """initial_stop_pct=2.0 → stop = lod * (1 - 2/100) = lod * 0.98."""
        cfg = GatewayConfig(acc_id=123456789, paper_trading=True, trd_env="SIMULATE")
        gw = MoomooGateway(cfg, initial_stop_pct=2.0)

        assert gw._compute_orphan_stop(100.0) == 98.0

    def test_compute_orphan_stop_default_fallback(self):
        """No initial_stop_pct supplied → safe 1.0% fallback (lod * 0.99), unchanged behavior."""
        cfg = GatewayConfig(acc_id=123456789, paper_trading=True, trd_env="SIMULATE")
        gw = MoomooGateway(cfg)

        assert abs(gw._compute_orphan_stop(100.0) - 99.0) < 1e-9

    def test_compute_orphan_stop_lod_zero_or_negative_returns_zero(self):
        """lod <= 0 must still return 0.0 (unavailable-LOD guard, unchanged)."""
        cfg = GatewayConfig(acc_id=123456789, paper_trading=True, trd_env="SIMULATE")
        gw = MoomooGateway(cfg, initial_stop_pct=2.0)

        assert gw._compute_orphan_stop(0.0) == 0.0
        assert gw._compute_orphan_stop(-5.0) == 0.0

