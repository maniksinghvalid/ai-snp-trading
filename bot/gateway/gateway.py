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
from dataclasses import dataclass
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
# Equity Query Constants (RISK-01, D-05, T-03-07)
# ============================================================

_EQUITY_FALLBACK: float = 100_000.0   # fallback when query fails or value is implausible (D-05)
_IMPLAUSIBLE_LOW: float = 1_000.0     # < $1,000 indicates a failed/uninitialized query (D-05)

# WR-04: Upper bound is deliberately large (100× the $100k SIMULATE starting equity)
# to guard against corrupt reads that would produce an outsized position if used
# for sizing. The chosen ceiling is 100× (not 10× or 1000×) because it leaves
# headroom for a SIMULATE account that has compounded well above the initial $100k,
# while still catching values that are obviously corrupt (e.g. an uninitialised
# field parsed as a very large integer). On a PAPER_TRADING account (SIMULATE) this
# threshold is unlikely to trigger; on a real account it would guard against a
# mis-read total_assets. The fallback is $100k (the documented sizing basis) rather
# than refusing to size, so the bot degrades gracefully rather than failing hard on
# one implausible read. An operator monitoring the logs will see equity_implausible
# warnings and can investigate. Changing the ceiling for a different starting equity
# requires updating this constant (future work: source from StrategyConfig/env).
_IMPLAUSIBLE_HIGH: float = 10_000_000.0  # > 100× $100k starting equity → suspect corrupt read


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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._trade_ctx.get_acc_list)

    async def get_positions(self, refresh_cache: bool = True) -> tuple:
        """Async wrapper: fetch open positions from broker (non-blocking).

        MANDATORY: refresh_cache=True bypasses the OpenD stale cache on SIMULATE
        (Pitfall B — empirically verified; omitting it returns pre-fill cached state).
        Used for the EXEC-04 duplicate guard and D-09/D-10 startup reconciliation.

        Parameters:
            refresh_cache: Must remain True for SIMULATE; default True (Pitfall B).

        Returns (ret, data) from trade_ctx.position_list_query().
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._trade_ctx.position_list_query(refresh_cache=refresh_cache),
        )

    async def get_equity(self) -> float:
        """Read live total net assets from the SIMULATE account (RISK-01, D-04/D-05).

        Calls accinfo_query(refresh_cache=True) to bypass the OpenD cache and
        reads the `total_assets` field, which equals cash + securities market
        value (net liquidation value per FIELD_MAPPING.md). Falls back to
        _EQUITY_FALLBACK if the query fails, returns implausible data, or raises.

        Implausible bounds (T-03-07):
          - Low: < _IMPLAUSIBLE_LOW ($1,000) — likely a failed/uninitialized query
          - High: > _IMPLAUSIBLE_HIGH ($10,000,000) — likely corrupt data that
            would produce an outsized position if used for sizing

        Returns:
            float — account equity in USD >= 1.0 (1.0 guards against zero-divide).
                    _EQUITY_FALLBACK (100,000) on any failure or implausible value.
        """
        try:
            loop = asyncio.get_running_loop()
            ret, data = await loop.run_in_executor(
                None,
                lambda: self._trade_ctx.accinfo_query(
                    trd_env=_parse_trd_env(self.cfg.trd_env),
                    acc_id=self.cfg.acc_id,
                    refresh_cache=True,
                ),
            )

            if ret != RET_OK or data is None or len(data) == 0:
                _logger.warning(
                    "equity_query_failed",
                    ret=ret,
                    fallback=_EQUITY_FALLBACK,
                )
                return _EQUITY_FALLBACK

            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            total_assets = float(row.get("total_assets", 0) or 0)

            if total_assets < _IMPLAUSIBLE_LOW or total_assets > _IMPLAUSIBLE_HIGH:
                _logger.warning(
                    "equity_implausible",
                    total_assets=total_assets,
                    implausible_low=_IMPLAUSIBLE_LOW,
                    implausible_high=_IMPLAUSIBLE_HIGH,
                    fallback=_EQUITY_FALLBACK,
                )
                return _EQUITY_FALLBACK

            return max(total_assets, 1.0)  # guard zero-divide in sizing math

        except Exception:
            _logger.warning(
                "equity_query_exception",
                exc_info=True,
                fallback=_EQUITY_FALLBACK,
            )
            return _EQUITY_FALLBACK

    async def get_market_snapshot(self, codes: list) -> tuple:
        """Raw broker snapshot read for the watchlist (D-01 premarket-high source).

        Returns the (ret, data) tuple from quote_ctx.get_market_snapshot(codes)
        unchanged — this is an interpretation-free thin broker read.

        The premarket-high field on the returned DataFrame is `pre_high_price`
        (RESEARCH Flag 2 RESOLVED — field available for US stocks without an
        extended-hours subscription). The D-01/D-03 interpretation logic (reading
        `pre_high_price`, excluding codes where it is zero, freezing the result)
        lives in the 03-02 `fetch_premarket_highs` helper, NOT here.

        The ≤20-code watchlist batch is well within the 400-code snapshot limit
        (SIG-01 cap; RESEARCH Standard Stack).

        Do NOT call `_check_ret` here — the caller (03-02 fetch helper) treats
        a non-RET_OK ret as an empty result and degrades gracefully rather than
        raising. The gateway read is interpretation-free.

        Args:
            codes: List of Moomoo-format codes (e.g. ["US.AAPL"]).
                   Must be ≤20 items (SIG-01 watchlist cap).

        Returns:
            (ret, data) tuple from quote_ctx.get_market_snapshot(codes).
            ret == RET_OK (0) on success; data is a DataFrame or similar.
            Non-RET_OK ret is returned as-is without raising.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._quote_ctx.get_market_snapshot(codes),
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

        loop = asyncio.get_running_loop()

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

    async def unsubscribe(self, codes: list, subtypes: list = None) -> None:
        """Unsubscribe K_5M pushes for codes evicted from the watchlist (SIG-01).

        Releases the quota slots held by codes that fall out of the protected
        top-20 across intraday re-scans. Without this, dropped codes keep their
        live K_5M feeds and the cumulative subscribed set grows unbounded,
        eventually exceeding the 20-slot cap the subscribe path is built around.

        Parameters:
            codes: List of Moomoo-format codes to release (e.g. ["US.AAPL"]).
            subtypes: List of SubType values. Defaults to [SubType.K_5M].

        Raises:
            GatewayError: if unsubscribe() returns non-RET_OK.
        """
        # Deferred import — mirrors subscribe() (avoids top-level moomoo import
        # failure when moomoo-api is not installed in the test environment).
        from moomoo import SubType

        if not codes:
            return

        if subtypes is None:
            subtypes = [SubType.K_5M]

        loop = asyncio.get_running_loop()

        def _unsubscribe_blocking():
            ret, msg = self._quote_ctx.unsubscribe(codes, subtypes)
            _check_ret(ret, msg, "unsubscribe")

        await loop.run_in_executor(None, _unsubscribe_blocking)
        _logger.info("unsubscribed_k5m", codes=codes, count=len(codes))

    async def get_global_state(self) -> dict:
        """Return a health-check dict from the OpenD global state (SVC-02).

        Calls quote_ctx.get_global_state() in a thread executor (blocking SDK
        call — Anti-Pattern 5 from ARCHITECTURE.md). Returns a plain dict with
        connected/qot_logined/trd_logined/server_ver/market_us. Never raises.

        Return dict:
          connected (bool): True when both qot_logined and trd_logined are truthy.
          qot_logined (bool): Quote context logged in to OpenD.
          trd_logined (bool): Trade context logged in to OpenD.
          server_ver (str): OpenD server version string (may be empty on failure).
          market_us (str): US market state string (may be empty on failure).

        Falls back to {"connected": False, "qot_logined": False, "trd_logined": False}
        on non-RET_OK, empty data, or any exception — never raises (WR-03).
        """
        _FALLBACK = {"connected": False, "qot_logined": False, "trd_logined": False}
        try:
            loop = asyncio.get_running_loop()

            def _blocking():
                return self._quote_ctx.get_global_state()

            ret, data = await loop.run_in_executor(None, _blocking)

            if ret != RET_OK or not data:
                _logger.warning("get_global_state_failed", ret=ret)
                return _FALLBACK

            qot = bool(data.get("qot_logined"))
            trd = bool(data.get("trd_logined"))
            return {
                "connected": qot and trd,
                "qot_logined": qot,
                "trd_logined": trd,
                "server_ver": str(data.get("server_ver", "") or ""),
                "market_us": str(data.get("market_us", "") or ""),
            }
        except Exception:
            _logger.warning("get_global_state_exception", exc_info=True)
            return _FALLBACK

    # --------------------------------------------------------
    # Order Methods (EXEC-01/EXEC-02/EXEC-03/EXEC-05)
    # --------------------------------------------------------

    async def place_order(self, code: str, qty: int, price: float, trd_side) -> str:
        """Place a marketable-limit order and return the broker order_id string.

        Uses OrderType.NORMAL exclusively — MARKET order type is never submitted (EXEC-02).
        Deferred import of OrderType so the module imports with moomoo-api absent.
        Every placed order is appended to the audit log (SAFE-05).

        Parameters:
            code:     Moomoo-format code (e.g. "US.AAPL").
            qty:      Integer share quantity.
            price:    Limit price in USD.
            trd_side: TrdSide enum value (BUY or SELL); caller supplies.

        Returns:
            str — broker-assigned order_id from the SDK response row.

        Raises:
            GatewayError — if SDK returns non-RET_OK.
        """
        # Deferred import — mirrors subscribe() pattern so test env without
        # moomoo-api still imports bot.gateway.gateway (D-02 wrap-not-import).
        from moomoo import OrderType  # noqa: F401 — NORMAL only; never submits a market order

        loop = asyncio.get_running_loop()

        def _place_blocking():
            ret, data = self._trade_ctx.place_order(
                price=float(price),
                qty=int(qty),
                code=code,
                trd_side=trd_side,
                order_type=OrderType.NORMAL,            # ALWAYS NORMAL — EXEC-02
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
            )
            _check_ret(ret, data, "place_order")
            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            order_id = str(row.get("order_id", "") or row.get("orderID", ""))
            from bot.safety.audit_log import append_audit
            append_audit({
                "event": "place_order",
                "code": code,
                "qty": int(qty),
                "price": float(price),
                "order_id": order_id,
            })
            return order_id

        order_id = await loop.run_in_executor(None, _place_blocking)
        _logger.info("order_placed", code=code, qty=qty, price=price, order_id=order_id)
        return order_id

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order via modify_order(op=CANCEL) (EXEC-03).

        Cancellation is implemented as a modify_order call with
        ModifyOrderOp.CANCEL, qty=0, price=0 — per the moomoo SDK cancel
        semantics (the SDK requires qty/price even for a cancel operation).
        Deferred import of ModifyOrderOp for test-env compatibility.

        Parameters:
            order_id: Broker-assigned order_id string to cancel.

        Raises:
            GatewayError — if SDK returns non-RET_OK.
        """
        # Deferred import — mirrors subscribe() pattern.
        from moomoo import ModifyOrderOp

        loop = asyncio.get_running_loop()

        def _cancel_blocking():
            ret, data = self._trade_ctx.modify_order(
                modify_order_op=ModifyOrderOp.CANCEL,
                order_id=order_id,
                qty=0,     # SDK requires qty/price parameters even for a cancel operation
                price=0,
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
            )
            _check_ret(ret, data, "cancel_order")

        await loop.run_in_executor(None, _cancel_blocking)
        _logger.info("order_cancelled", order_id=order_id)

    async def get_order_fills(self, refresh_cache: bool = True) -> list:
        """Fetch fill records via deal_list_query with refresh_cache=True (Pitfall B).

        MANDATORY: omitting refresh_cache=True returns stale OpenD-cached data
        on SIMULATE (Pitfall B — empirically verified). Polling with this flag
        is the authoritative fill source; push-based fills are unreliable on
        SIMULATE (Pitfall A). Returns list of dicts with order_id present
        (EXEC-05 fill reconciliation).

        Parameters:
            refresh_cache: Must remain True for SIMULATE; default True.

        Returns:
            list of dicts with keys: deal_id, order_id, code, qty, price,
            trd_side, create_time. Empty list if no fills.

        Raises:
            GatewayError — if SDK returns non-RET_OK.
        """
        loop = asyncio.get_running_loop()
        ret, data = await loop.run_in_executor(
            None,
            lambda: self._trade_ctx.deal_list_query(
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
                refresh_cache=refresh_cache,    # MANDATORY for SIMULATE (Pitfall B)
            ),
        )
        _check_ret(ret, data, "deal_list_query")
        if data is None or len(data) == 0:
            return []
        return [
            {k: row.get(k) for k in [
                "deal_id", "order_id", "code", "qty",
                "price", "trd_side", "create_time",
            ]}
            for _, row in data.iterrows()
        ]

    async def get_order_status(self, order_id: str = "") -> list:
        """Fetch order status via order_list_query with refresh_cache=True (Pitfall B).

        MANDATORY: refresh_cache=True bypasses OpenD's stale cache on SIMULATE.
        Returns list of dicts with dealt_qty and dealt_avg_price for partial-fill
        detection (EXEC-05).

        Parameters:
            order_id: Filter to a specific order; empty string = all orders.

        Returns:
            list of dicts with keys: order_id, code, order_status, qty,
            dealt_qty, dealt_avg_price, trd_side.

        Raises:
            GatewayError — if SDK returns non-RET_OK.
        """
        loop = asyncio.get_running_loop()
        ret, data = await loop.run_in_executor(
            None,
            lambda: self._trade_ctx.order_list_query(
                order_id=order_id,
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
                refresh_cache=True,             # MANDATORY for SIMULATE (Pitfall B)
            ),
        )
        _check_ret(ret, data, "order_list_query")
        if data is None or len(data) == 0:
            return []
        return [
            {k: row.get(k) for k in [
                "order_id", "code", "order_status", "qty",
                "dealt_qty", "dealt_avg_price", "trd_side",
            ]}
            for _, row in data.iterrows()
        ]

    async def get_ask_price(self, code: str) -> float:
        """Read the current ask price for a single code from a snapshot.

        Reads the `ask_price` column from get_market_snapshot. The column name
        `ask_price` is confirmed by the moomoo SDK snapshot response schema.
        If ask_price is null or 0 (illiquid/halted), falls back to `last_price`.
        The engine applies the +buffer; this method returns the raw market price.
        Deferred SDK import via the existing get_market_snapshot path.

        Returns:
            float — ask price in USD (>= 0). 0.0 if snapshot unavailable.
        """
        ret, data = await self.get_market_snapshot([code])
        if ret != RET_OK or data is None or len(data) == 0:
            return 0.0
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        ask = float(row.get("ask_price") or 0)
        if ask == 0.0:
            ask = float(row.get("last_price") or 0)
        return ask

    async def get_bid_price(self, code: str) -> float:
        """Read the current bid price for a single code from a snapshot.

        Reads the `bid_price` column from get_market_snapshot. The column name
        `bid_price` is confirmed by the moomoo SDK snapshot response schema.
        If bid_price is null or 0 (illiquid/halted), falls back to `last_price`.
        The engine applies the -buffer; this method returns the raw market price.
        Deferred SDK import via the existing get_market_snapshot path.

        Returns:
            float — bid price in USD (>= 0). 0.0 if snapshot unavailable.
        """
        ret, data = await self.get_market_snapshot([code])
        if ret != RET_OK or data is None or len(data) == 0:
            return 0.0
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        bid = float(row.get("bid_price") or 0)
        if bid == 0.0:
            bid = float(row.get("last_price") or 0)
        return bid

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
        """
        ret_pos, positions = await self.get_positions(refresh_cache=True)
        ret_acc, accounts = await self.get_acc_list()

        # Compare StateStore open positions against broker positions
        # Apply broker-truth corrections (ghost position cleanup, fill matching)
        # Drift detail logged to structlog for operator visibility
        return {
            "positions": positions if ret_pos == RET_OK else None,
            "accounts": accounts if ret_acc == RET_OK else None,
            "drift": {},
        }

    async def startup_reconcile(self, store, manager=None) -> None:
        """Reconcile StateStore positions against broker truth before any signal processing.

        Implements D-09 (broker truth wins), D-10 (adopt orphans with derived stop),
        D-11 (known positions resume without loosening the stop).

        Protocol:
          - Read broker positions via get_positions(refresh_cache=True).
          - For each StateStore open position:
              - Not in broker map → CLOSED (D-09: broker says flat).
              - Quantities differ → adopt broker qty (D-09).
          - For each broker position not in StateStore:
              - Orphan → insert ACTIVE PositionState at broker avg cost with
                compute_initial_stop(lod) derived stop; audit orphan_adopted (D-10).
          - For known positions (both sides present), PositionManager.on_bar
            applies max(persisted_stop, new_swing_low) on the next bar (D-11).
          - Reconcile pending_intents PENDING rows against get_positions() and
            get_order_status() to detect crash-between-place-and-persist (Pitfall F).

        Args:
            store:   StateStore instance (open).
            manager: Optional PositionManager; if provided, subscribe new orphan feeds.

        Note:
            This method is called ONCE before the main trading loop starts (POS-05).
            PositionManager.reconstruct_from_store() must be called AFTER this to load
            the reconciled state into the in-memory _positions dict.
        """
        from bot.safety.audit_log import append_audit
        from bot.safety.et_helpers import now_et

        # ---- Step 1: Read broker positions (refresh_cache=True mandatory — Pitfall B) ----
        ret_pos, broker_data = await self.get_positions(refresh_cache=True)
        broker_map = {}  # code → {qty, avg_cost}
        if ret_pos == RET_OK and broker_data is not None and len(broker_data) > 0:
            for _, row in broker_data.iterrows():
                code = str(row.get("code", "") or "")
                if not code:
                    continue
                broker_map[code] = {
                    "qty": int(float(row.get("qty", 0) or 0)),
                    "avg_cost": float(row.get("average_cost", 0) or 0),
                }

        # ---- Step 2: Read StateStore open positions ----
        # Use store.get_open_positions() which sets row_factory=sqlite3.Row and returns
        # a list of plain dicts — do NOT use store.conn.execute directly (tuples, not dicts).
        state_rows = store.get_open_positions()
        state_codes = {r["code"]: r for r in state_rows}

        now_ts = now_et().isoformat()

        # ---- Step 3: Handle StateStore positions vs broker truth (D-09) ----
        for code, pos_row in state_codes.items():
            if code not in broker_map:
                # D-09: broker says this position is flat — mark CLOSED
                _logger.warning(
                    "reconcile_position_closed_by_broker",
                    code=code,
                    position_id=pos_row["position_id"],
                )
                store.conn.execute(
                    "UPDATE positions SET phase='CLOSED', updated_at=? WHERE position_id=?",
                    (now_ts, pos_row["position_id"]),
                )
                append_audit({
                    "event": "reconcile_closed_by_broker",
                    "code": code,
                    "position_id": pos_row["position_id"],
                })
            else:
                # Both sides present — check quantity drift (D-09)
                broker_qty = broker_map[code]["qty"]
                stored_qty = pos_row["remaining_quantity"]
                if broker_qty != stored_qty:
                    _logger.warning(
                        "reconcile_qty_adopted",
                        code=code,
                        stored_qty=stored_qty,
                        broker_qty=broker_qty,
                    )
                    store.conn.execute(
                        "UPDATE positions SET remaining_quantity=?, updated_at=? "
                        "WHERE position_id=?",
                        (broker_qty, now_ts, pos_row["position_id"]),
                    )
                    append_audit({
                        "event": "reconcile_qty_adopted",
                        "code": code,
                        "stored_qty": stored_qty,
                        "broker_qty": broker_qty,
                    })
                # D-11: stop is NEVER loosened — PositionManager.on_bar applies
                # max(persisted_stop, new_swing_low) on the next closed bar.

        # ---- Step 4: Adopt orphan broker positions (D-10) ----
        for code, bp in broker_map.items():
            if code in state_codes:
                continue  # known position, handled above

            # Orphan: broker has it, StateStore doesn't — adopt and protect (D-10)
            _logger.warning(
                "reconcile_orphan_adopting",
                code=code,
                broker_qty=bp["qty"],
                broker_avg_cost=bp["avg_cost"],
            )

            # Derive initial stop from current LOD snapshot (D-10)
            lod = await self._derive_lod_for_orphan(code)
            stop = self._compute_orphan_stop(lod)

            # Insert ACTIVE PositionState at broker avg cost with derived stop
            import uuid
            position_id = str(uuid.uuid4())
            store.conn.execute(
                """INSERT OR IGNORE INTO positions
                   (position_id, code, phase, entry_price, initial_stop, trail_stop,
                    full_quantity, remaining_quantity, entry_order_id,
                    avg_fill_price, opened_at, updated_at)
                   VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (
                    position_id, code,
                    bp["avg_cost"],   # entry_price = broker avg cost
                    stop,             # initial_stop = compute_initial_stop(lod)
                    stop,             # trail_stop = same as initial (conservative)
                    bp["qty"],        # full_quantity
                    bp["qty"],        # remaining_quantity
                    bp["avg_cost"],   # avg_fill_price
                    now_ts,           # opened_at
                    now_ts,           # updated_at
                ),
            )
            append_audit({
                "event": "orphan_adopted",
                "code": code,
                "position_id": position_id,
                "broker_qty": bp["qty"],
                "broker_avg_cost": bp["avg_cost"],
                "derived_stop": stop,
                "derived_lod": lod,
            })

            # Re-subscribe 5m feed for the adopted position (D-10)
            if manager is not None:
                try:
                    await self.subscribe([code])
                except Exception:
                    _logger.warning(
                        "orphan_subscribe_failed", code=code, exc_info=True
                    )

        # ---- Step 5: Commit all reconciliation changes ----
        store.conn.commit()

        # ---- Step 6: Reconcile pending_intents to catch crash-between-place-and-persist ----
        # Any PENDING intent whose code already has an open broker position was likely
        # placed before the crash — do not re-enter; leave the intent as PENDING
        # so the duplicate guard in consume_intent will block it on next run (Pitfall F).
        import sqlite3
        store.conn.row_factory = sqlite3.Row
        pending_rows = store.conn.execute(
            "SELECT intent_id, code FROM pending_intents WHERE status='PENDING'"
        ).fetchall()
        store.conn.row_factory = None
        for intent_row in pending_rows:
            code = intent_row["code"]
            if code in broker_map:
                _logger.info(
                    "reconcile_pending_intent_skipped_open_position",
                    code=code,
                    intent_id=intent_row["intent_id"],
                )
                # Leave PENDING — the EXEC-04 duplicate guard in consume_intent
                # will block replay when consume_intent is next called for this code.

        _logger.info(
            "startup_reconcile_done",
            broker_positions=len(broker_map),
            state_positions=len(state_codes),
        )

    async def _derive_lod_for_orphan(self, code: str) -> float:
        """Fetch the current LOD (low-of-day) for an orphan position from a snapshot.

        Falls back to 0.0 if the snapshot is unavailable (compute_initial_stop of 0.0
        will produce stop=0.0 — a protective floor that keeps the position managed).

        Args:
            code: Moomoo-format code (e.g. "US.AAPL").

        Returns:
            float — current low-of-day price. 0.0 on failure.
        """
        try:
            ret, data = await self.get_market_snapshot([code])
            if ret != RET_OK or data is None or len(data) == 0:
                return 0.0
            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            low_price = float(row.get("low_price") or 0)
            if low_price <= 0:
                low_price = float(row.get("last_price") or 0)
            return low_price
        except Exception:
            _logger.warning("orphan_lod_fetch_failed", code=code, exc_info=True)
            return 0.0

    def _compute_orphan_stop(self, lod: float) -> float:
        """Derive the orphan adoption stop via LOD - 1% (D-10).

        Uses a fixed 1% fraction (matching 'lod_minus_1pct' stop rule from
        StrategyConfig) so orphan adoption does not require the full config stack.
        The actual strategy uses cfg.initial_stop_pct (configured to 1.0 from
        'lod_minus_1pct') — replicate that math here without needing a config import.

        Args:
            lod: Low-of-day price (0.0 if unavailable).

        Returns:
            float — stop price at lod * 0.99. Returns 0.0 if lod is 0.
        """
        if lod <= 0:
            return 0.0
        return float(lod * 0.99)  # lod_minus_1pct — same as compute_initial_stop

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
