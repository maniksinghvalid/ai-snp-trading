#!/usr/bin/env python3
"""
bot.gateway.gateway — MoomooGateway broker access layer.

Wraps the moomoo SDK directly (D-02: wrap-not-import; skills/ is a CLI reference only).
Provides:
  - GatewayConfig: dataclass reading FUTU_* + PAPER_TRADING env vars
  - GatewayError: exception raised on SDK or connectivity errors (never terminates process)
  - get_gateway_config(): factory reading env vars with safe defaults
  - MoomooGateway: persistent OpenQuoteContext + OpenSecTradeContext with
      pre-flight connectivity check, paper-guard gate at connect(),
      run_in_executor wrappers for async callers, and reconciliation skeletons.

Exports: MoomooGateway, GatewayConfig, GatewayError, get_gateway_config
"""
import asyncio
import os
import socket
from dataclasses import dataclass, field
from typing import Optional

# moomoo SDK imported directly (D-02 wrap-not-import)
try:
    from moomoo import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdEnv,
        TrdMarket,
        RET_OK,
    )
except ImportError as exc:  # pragma: no cover — only when moomoo-api not installed
    raise ImportError(
        "moomoo-api is not installed. Run: pip install 'moomoo-api>=10.4.6408,<11.0'"
    ) from exc

from bot.safety.logger import get_logger
from bot.safety.paper_guard import assert_paper_account


# ============================================================
# Module Logger
# ============================================================

_logger = get_logger(__name__)


# ============================================================
# Configuration
# ============================================================

@dataclass
class GatewayConfig:
    """Gateway configuration — mirrors FutuConfig pattern in common.py (D-02).

    Reads the same FUTU_* env vars plus FUTU_ACC_ID and PAPER_TRADING.
    trd_env defaults to "SIMULATE" — never "REAL" as a default (T-01-01).
    Credentials (login_account, login_pwd) are deliberately NOT stored here
    to prevent accidental credential logging (T-01-04).
    """
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    trd_env: str = "SIMULATE"          # always default SIMULATE, never REAL
    default_market: str = "US"
    security_firm: Optional[str] = None
    acc_id: int = 0                    # must be set explicitly (D-03)
    paper_trading: bool = False        # must be set True via PAPER_TRADING=true (D-03)


def get_gateway_config() -> GatewayConfig:
    """Read GatewayConfig from environment variables with safe defaults.

    Environment variables:
      FUTU_OPEND_HOST    OpenD host (default: 127.0.0.1)
      FUTU_OPEND_PORT    OpenD port (default: 11111)
      FUTU_TRD_ENV       Trading environment (default: SIMULATE — paper-safe)
      FUTU_DEFAULT_MARKET Default trade market (default: US)
      FUTU_SECURITY_FIRM Security firm identifier (optional)
      FUTU_ACC_ID        Account ID — must be set explicitly (D-03)
      PAPER_TRADING      Must be "true" to enable bot (D-03)

    Returns:
        GatewayConfig with values from env or defaults.
    """
    paper_trading_raw = os.getenv("PAPER_TRADING", "false").strip().lower()
    paper_trading = paper_trading_raw == "true"

    acc_id_raw = os.getenv("FUTU_ACC_ID", "0").strip()
    try:
        acc_id = int(acc_id_raw)
    except ValueError:
        acc_id = 0

    return GatewayConfig(
        opend_host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
        opend_port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        trd_env=os.getenv("FUTU_TRD_ENV", "SIMULATE"),
        default_market=os.getenv("FUTU_DEFAULT_MARKET", "US"),
        security_firm=os.getenv("FUTU_SECURITY_FIRM", "") or None,
        acc_id=acc_id,
        paper_trading=paper_trading,
    )


# ============================================================
# Exceptions
# ============================================================

class GatewayError(Exception):
    """Raised for broker API errors and connectivity failures.

    The gateway raises; only bot/main.py translates this to a non-zero exit.
    """


# ============================================================
# Module-level Helpers
# ============================================================

def _check_opend_alive(host: str, port: int) -> None:
    """Verify OpenD is reachable at host:port via TCP socket.

    Mirrors common.py _check_opend_alive but raises ConnectionError
    instead of terminating the process (library raises — D-02 divergence).

    Raises:
        ConnectionError — if OpenD is not reachable (T-01-05: acceptable; bot
            is non-functional without OpenD by design).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        raise ConnectionError(
            f"Cannot connect to OpenD at {host}:{port} — connection refused. "
            "Ensure OpenD is running and logged in."
        )
    except OSError as exc:
        raise ConnectionError(
            f"Cannot connect to OpenD at {host}:{port}: {exc}"
        ) from exc
    finally:
        sock.close()


def _parse_trd_env(env_str: str) -> "TrdEnv":
    """Convert a string trading environment to the moomoo TrdEnv enum.

    Mirrors parse_trd_env from common.py. Defaults to TrdEnv.SIMULATE for
    any value that is not explicitly "REAL" (paper-safe default).
    """
    if env_str and str(env_str).upper() == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE


def _parse_market(market_str: str) -> "TrdMarket":
    """Convert a string market to the moomoo TrdMarket enum.

    Mirrors parse_market from common.py. Defaults to TrdMarket.US.
    """
    _MAP = {
        "NONE": TrdMarket.NONE,
        "US": TrdMarket.US,
        "HK": TrdMarket.HK,
        "CN": TrdMarket.CN,
        "HKCC": TrdMarket.HKCC,
    }
    return _MAP.get(str(market_str).upper(), TrdMarket.US)


def _check_ret(ret: int, data, action: str) -> None:
    """Check SDK return code; raise GatewayError on failure.

    Mirrors check_ret from common.py but raises GatewayError instead of
    terminating the process (library raises — D-02 divergence).

    Raises:
        GatewayError — if ret != RET_OK.
    """
    if ret != RET_OK:
        raise GatewayError(f"{action} failed: ret={ret}, data={data}")


def _safe_close(ctx) -> None:
    """Safely close a moomoo SDK context, swallowing any exception.

    Copied from safe_close in common.py.
    """
    try:
        if ctx:
            ctx.close()
    except Exception:
        pass


# ============================================================
# Gateway
# ============================================================

class MoomooGateway:
    """Persistent broker access layer wrapping the moomoo SDK (D-02).

    Holds open OpenQuoteContext and OpenSecTradeContext for the lifetime of
    the trading session. Blocking SDK calls are wrapped in run_in_executor
    for async callers (Anti-Pattern 5 from ARCHITECTURE.md).

    Usage:
        cfg = get_gateway_config()
        gw = MoomooGateway(cfg)
        gw.connect()          # pre-flight + paper guard + context creation
        positions = await gw.get_positions()
        gw.close()
    """

    def __init__(self, cfg: Optional[GatewayConfig] = None):
        self.cfg: GatewayConfig = cfg or get_gateway_config()
        self._quote_ctx: Optional[OpenQuoteContext] = None
        self._trade_ctx: Optional[OpenSecTradeContext] = None

    # --------------------------------------------------------
    # Context Creation (blocking, called from connect())
    # --------------------------------------------------------

    def _make_quote_ctx(self) -> "OpenQuoteContext":
        """Create a persistent OpenQuoteContext connected to OpenD."""
        return OpenQuoteContext(
            host=self.cfg.opend_host,
            port=self.cfg.opend_port,
        )

    def _make_trade_ctx(self) -> "OpenSecTradeContext":
        """Create a persistent OpenSecTradeContext connected to OpenD."""
        trd_env = _parse_trd_env(self.cfg.trd_env)
        market = _parse_market(self.cfg.default_market)
        kwargs = dict(
            host=self.cfg.opend_host,
            port=self.cfg.opend_port,
            filter_trdmarket=market,
        )
        # security_firm is optional; omit rather than passing None to avoid SDK errors
        if self.cfg.security_firm:
            kwargs["security_firm"] = self.cfg.security_firm
        return OpenSecTradeContext(**kwargs)

    # --------------------------------------------------------
    # Connection Lifecycle
    # --------------------------------------------------------

    def connect(self) -> None:
        """Connect to OpenD: pre-flight check, context creation, paper guard.

        Steps:
          1. _check_opend_alive — raises ConnectionError if OpenD unreachable
          2. _make_quote_ctx / _make_trade_ctx — create persistent contexts
          3. assert_paper_account — raises PaperGuardError if any guard fails

        The gateway is only usable after connect() returns without raising.

        Raises:
            ConnectionError — OpenD not reachable
            PaperGuardError — paper-safety guard failed (SAFE-01)
            GatewayError — SDK context creation failed
        """
        _check_opend_alive(self.cfg.opend_host, self.cfg.opend_port)
        self._quote_ctx = self._make_quote_ctx()
        self._trade_ctx = self._make_trade_ctx()
        # Paper guard must pass before the gateway is usable (SAFE-01, D-04, D-05)
        assert_paper_account(self.cfg, self._trade_ctx)

    def close(self) -> None:
        """Close both SDK contexts gracefully."""
        _safe_close(self._quote_ctx)
        _safe_close(self._trade_ctx)
        self._quote_ctx = None
        self._trade_ctx = None

    # --------------------------------------------------------
    # Async Wrappers (run_in_executor — Anti-Pattern 5)
    # --------------------------------------------------------

    async def get_acc_list(self) -> tuple:
        """Async wrapper: fetch account list from broker (non-blocking).

        Returns (ret, data) tuple from trade_ctx.get_acc_list().
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._trade_ctx.get_acc_list)

    async def get_positions(self) -> tuple:
        """Async wrapper: fetch open positions from broker (non-blocking).

        Returns (ret, data) from trade_ctx.position_list_query().
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._trade_ctx.position_list_query(),
        )

    async def subscribe(self, codes: list, subtypes: list = None) -> None:
        """Subscribe to real-time K_5M candlestick pushes for the given codes.

        Only the capped top-20 watchlist should be passed (SIG-01) — never the
        full ~500-symbol universe. Each code+subtype pair consumes 1 quota slot;
        top-20 cap keeps usage at 20 of the 100-slot minimum tier.

        Parameters:
            codes: List of Moomoo-format codes (e.g. ["US.AAPL", "US.BRK-B"]).
                   Must be <= 20 items (SIG-01).
            subtypes: List of SubType values. Defaults to [SubType.K_5M].

        Raises:
            GatewayError: if subscribe() returns non-RET_OK.
        """
        # Deferred import — avoids top-level import failure when moomoo-api
        # is not installed in the test environment (matches gateway SDK import pattern).
        from moomoo import SubType, Session

        if subtypes is None:
            subtypes = [SubType.K_5M]

        loop = asyncio.get_event_loop()

        def _subscribe_blocking():
            ret, msg = self._quote_ctx.subscribe(
                codes,
                subtypes,
                is_first_push=True,
                subscribe_push=True,
                extended_time=False,
                session=Session.NONE,
            )
            _check_ret(ret, msg, "subscribe")

        await loop.run_in_executor(None, _subscribe_blocking)
        _logger.info("subscribed_k5m", codes=codes, count=len(codes))

    # --------------------------------------------------------
    # Reconciliation Skeletons (SAFE-02 / SAFE-03)
    # --------------------------------------------------------

    async def reconcile_once(self) -> dict:
        """Reconcile bot state against broker truth (SAFE-02 skeleton).

        Reads broker positions and account state, then returns a diff dict.
        Broker truth wins in all conflict cases.

        Returns:
            dict with keys 'positions', 'accounts', and 'drift' describing
            any discrepancy between local state and broker truth.

        Note:
            # Phase 4: full reconciliation logic
            This is a skeleton — it reads broker truth but the drift-resolution
            logic (applying broker-truth corrections to StateStore) lands in Phase 4.
        """
        ret_pos, positions = await self.get_positions()
        ret_acc, accounts = await self.get_acc_list()

        # Phase 4: full reconciliation logic
        # - Compare StateStore open positions against broker positions
        # - Apply broker-truth corrections (ghost position cleanup, fill matching)
        # - Log any drift to structlog for operator visibility
        return {
            "positions": positions if ret_pos == RET_OK else None,
            "accounts": accounts if ret_acc == RET_OK else None,
            "drift": {},  # Phase 4: populate with actual drift analysis
        }

    async def reconciliation_loop(self, interval_s: float = 75.0) -> None:
        """Run reconcile_once() on a 60-90s loop (SAFE-03 skeleton).

        interval_s: sleep interval in seconds between reconciliation cycles.
            Default 75.0 is within the 60-90s range specified by SAFE-03.

        Note:
            # Phase 4: full reconciliation logic
            The loop itself is wired; the reconcile_once() payload grows in Phase 4.

        A single reconcile_once() failure (network blip, SDK error) is logged
        and the loop continues — one broker error must never permanently kill
        the SAFE-03 reconciliation loop (WR-03). asyncio.CancelledError is
        re-raised so the loop still shuts down cleanly on cancellation.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise  # propagate cancellation for clean shutdown
            except Exception:
                # Keep looping — a transient broker error must not silently
                # disable the SAFE-03 reconciliation loop (WR-03).
                _logger.error("reconcile_failed", exc_info=True)
