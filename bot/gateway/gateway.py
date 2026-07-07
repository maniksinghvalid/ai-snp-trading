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
# Rate-limit retry constants for order_list_query (RATE-01)
# ============================================================
# Moomoo caps order_list_query at 10 calls per 30 seconds.  When the cap is
# exceeded the SDK returns ret=-1 with a message containing one or both of
# these substrings.  Detection is by substring, NOT by ret value alone, because
# ret=-1 is a generic failure code shared by many unrelated SDK errors.
#
# _RATE_LIMIT_BACKOFF_SECONDS is defined as a module-level name so tests can
# patch it to 0.0 without actually sleeping.
_RATE_LIMIT_MARKERS: tuple = (
    "Maximum 10 times per 30 seconds",
    "high frequency",
)
_RATE_LIMIT_MAX_RETRIES: int = 3           # total retry attempts after first failure
_RATE_LIMIT_BACKOFF_SECONDS: float = 3.0  # asyncio.sleep between retries


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
# QuoteTickHandler — D-02 quote-tick fallback push handler
# ============================================================

class QuoteTickHandler:
    """Quote push handler for the D-02 bot-side tick-level stop fallback.

    Registered on the OpenQuoteContext via set_handler() before subscribing to
    SubType.QUOTE. Each incoming quote push invokes the callback with (code, bid_price)
    so PositionManager._on_quote can fire an immediate exit when the bid touches the
    trail_stop (D-02 quote-tick invalidation).

    Design mirrors the BarAggregator pattern: a stateless handler that bridges
    the moomoo SDK push thread to the async event loop via a callback.

    The QuoteHandlerBase mixin is imported lazily to avoid a top-level moomoo
    import failure when moomoo-api is absent (test-env compatibility).

    Args:
        callback: Callable(code: str, bid_price: float) invoked per push.
    """

    def __init__(self, callback) -> None:
        self._callback = callback

    def on_recv_rsp(self, rsp_pb):
        """SDK push callback — called on the moomoo receive thread.

        Parses the quote data to extract code and bid_price, then invokes
        the registered callback. Non-fatal: any exception is logged and
        suppressed so a bad tick does not crash the push thread.
        """
        try:
            ret, data = super(QuoteTickHandler, self).on_recv_rsp(rsp_pb)
            if ret != RET_OK or data is None or len(data) == 0:
                return
            for _, row in data.iterrows():
                code = str(row.get("code", "") or "")
                bid_price_raw = row.get("bid_price", None) or row.get("last_price", None)
                if code and bid_price_raw is not None:
                    try:
                        bid_price = float(bid_price_raw)
                    except (TypeError, ValueError):
                        continue
                    self._callback(code, bid_price)
        except Exception:
            _logger.warning("quote_tick_handler_error", exc_info=True)


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

    def __init__(self, cfg: Optional[GatewayConfig] = None, initial_stop_pct: float = 1.0):
        self.cfg: GatewayConfig = cfg or get_gateway_config()
        self._quote_ctx: Optional[OpenQuoteContext] = None
        self._trade_ctx: Optional[OpenSecTradeContext] = None
        # Retained set for fire-and-forget DRIFT alert tasks (WR-03).
        # asyncio.create_task results are added here and removed via
        # add_done_callback(self._bg_tasks.discard) to prevent GC before send.
        self._bg_tasks: set = set()
        # Finding 2.8: orphan-adoption stop percentage (CFG-01 — rules.json
        # single source of truth via StrategyConfig.initial_stop_pct). Defaults
        # to 1.0 (the pre-fix lod*0.99 behavior) as a safe fallback when the
        # caller does not thread the live strategy config through.
        self._initial_stop_pct: float = initial_stop_pct

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
            lambda: self._trade_ctx.position_list_query(
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
                refresh_cache=refresh_cache,
            ),
        )

    async def get_external_codes(self, store) -> set:
        """Return broker-held codes NOT owned by the bot (SAFE-OG-01 predicate).

        Queries the broker for all open positions, then subtracts codes the bot
        owns (open DB position row OR live pending intent). The remaining codes
        are external (manual operator holdings or short positions the strategy
        cannot manage).

        Called at scan time to exclude external codes before the top-20 cap.
        The caller decides the fail-open / fail-closed policy on GatewayError.

        Args:
            store: StateStore — used for get_open_positions() and has_pending_intent().

        Returns:
            set: Set of Moomoo-format codes held at broker but NOT bot-owned.

        Raises:
            GatewayError: When the broker position query returns ret != RET_OK
                          or data is None. Caller must handle this.
        """
        # Pitfall B: refresh_cache=True bypasses the OpenD stale cache on SIMULATE.
        ret, data = await self.get_positions(refresh_cache=True)
        if ret != RET_OK or data is None:
            raise GatewayError(
                f"get_external_codes: broker position query failed (ret={ret})"
            )

        # Build bot-ownership set from DB (open positions + pending intents).
        # This mirrors the SAFE-OG-01 predicate in reconcile_once (~972-977 / ~1136-1143).
        open_pos_codes = {r["code"] for r in store.get_open_positions()}

        external: set = set()
        for _, row in data.iterrows():
            code = row.get("code", "")
            if not code:
                continue
            bot_owned = code in open_pos_codes or store.has_pending_intent(code)
            if not bot_owned:
                external.add(code)

        return external

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

    def set_handler(self, handler) -> None:
        """Register a CurKlineHandlerBase push handler on the quote context.

        Must be called after connect() and before subscribe(). Safe to call once
        at bot startup — handler survives reconnects as long as _quote_ctx is reused.
        Calling with the same handler a second time is harmless (idempotent).

        Args:
            handler: CurKlineHandlerBase subclass instance (e.g. BarAggregator).
        """
        self._quote_ctx.set_handler(handler)
        _logger.info("push_handler_registered", handler=type(handler).__name__)

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

    async def subscribe_quote(self, codes: list, callback) -> None:
        """Subscribe to real-time bid/ask quote ticks for the D-02 fallback path.

        Requests SubType.QUOTE on the provided codes and registers a QuoteTickHandler
        that bridges each incoming bid_price to the provided callback. The callback
        signature is: callback(code: str, bid_price: float) -> None (sync or async;
        called from the QuoteTickHandler.on_recv_rsp on the push thread).

        Used by PositionManager.arm_stop_protection when use_broker_stop_orders=False
        to arm tick-level stop invalidation without a broker stop order (D-02).

        Parameters:
            codes:    List of Moomoo-format codes (e.g. ["US.AAPL"]).
            callback: Callable invoked on each quote push with (code, bid_price).

        Raises:
            GatewayError: if subscribe() returns non-RET_OK.
        """
        # Deferred import — test-env compatibility (no moomoo-api in CI).
        from moomoo import SubType

        # Register the handler BEFORE subscribing so the first push is not lost.
        handler = QuoteTickHandler(callback=callback)
        self._quote_ctx.set_handler(handler)

        loop = asyncio.get_running_loop()

        def _subscribe_blocking():
            ret, msg = self._quote_ctx.subscribe(
                codes,
                [SubType.QUOTE],
                is_first_push=True,
                subscribe_push=True,
            )
            _check_ret(ret, msg, "subscribe_quote")

        await loop.run_in_executor(None, _subscribe_blocking)
        _logger.info("subscribed_quote", codes=codes, count=len(codes))

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

    async def place_stop_order(self, code: str, qty: int, stop_price: float, trd_side) -> str:
        """Place a broker-side protective stop order and return the order_id (D-01/EXEC-02).

        Uses OrderType.STOP (the ONLY allowed stop-order path — EXEC-02 amendment D-01).
        price=0.0 and aux_price=stop_price match the moomoo SDK stop-order semantics:
        the SDK triggers the order when the market touches aux_price, then fills at
        the next available price (market). Deferred import of OrderType/TrdSide for
        test-env compatibility. Every placed stop is appended to the audit log (SAFE-05).

        Parameters:
            code:       Moomoo-format code (e.g. "US.AAPL").
            qty:        Integer share quantity to protect.
            stop_price: The trigger price for the stop (trail_stop or initial_stop).
            trd_side:   TrdSide.SELL for long-only protective stops (caller supplies).

        Returns:
            str — broker-assigned order_id from the SDK response row.

        Raises:
            GatewayError — if SDK returns non-RET_OK.
        """
        # Deferred import — test-env compatibility (moomoo-api not installed in CI).
        from moomoo import OrderType  # noqa: F401

        loop = asyncio.get_running_loop()

        def _stop_blocking():
            ret, data = self._trade_ctx.place_order(
                price=0.0,                              # ignored for STOP orders (SDK semantic)
                qty=int(qty),
                code=code,
                trd_side=trd_side,
                order_type=OrderType.STOP,              # EXEC-02 amendment D-01: only allowed STOP path
                aux_price=float(stop_price),            # trigger price for the stop
                trd_env=_parse_trd_env(self.cfg.trd_env),
                acc_id=self.cfg.acc_id,
            )
            _check_ret(ret, data, "place_stop_order")
            row = data.iloc[0] if hasattr(data, "iloc") else data[0]
            order_id = str(row.get("order_id", "") or row.get("orderID", ""))
            from bot.safety.audit_log import append_audit
            append_audit({
                "event": "stop_order_placed",
                "code": code,
                "qty": int(qty),
                "stop_price": float(stop_price),
                "order_id": order_id,
            })
            return order_id

        order_id = await loop.run_in_executor(None, _stop_blocking)
        _logger.info(
            "stop_order_placed",
            code=code,
            qty=qty,
            stop_price=stop_price,
            order_id=order_id,
        )
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

        Rate-limit resilience (RATE-01): Moomoo caps order_list_query at 10
        calls per 30 seconds.  When the cap is exceeded (ret=-1 with a message
        containing "high frequency" / "Maximum 10 times per 30 seconds") this
        method retries up to _RATE_LIMIT_MAX_RETRIES times with a backoff sleep
        of _RATE_LIMIT_BACKOFF_SECONDS between each attempt.  Only the rate-limit
        condition triggers retries; all other non-RET_OK codes still raise
        GatewayError immediately (or after exhausting retries for the rate-limit
        case).

        Parameters:
            order_id: Filter to a specific order; empty string = all orders.

        Returns:
            list of dicts with keys: order_id, code, order_status, qty,
            dealt_qty, dealt_avg_price, trd_side.

        Raises:
            GatewayError — if SDK returns non-RET_OK for a non-rate-limit reason,
                           or if the rate-limit condition persists after all retries.
        """
        loop = asyncio.get_running_loop()
        ret, data = None, None
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            ret, data = await loop.run_in_executor(
                None,
                lambda: self._trade_ctx.order_list_query(
                    order_id=order_id,
                    trd_env=_parse_trd_env(self.cfg.trd_env),
                    acc_id=self.cfg.acc_id,
                    refresh_cache=True,         # MANDATORY for SIMULATE (Pitfall B)
                ),
            )
            if ret == RET_OK:
                break
            # Detect rate-limit condition by substring on the data message.
            # ret=-1 is generic; we ONLY retry on the documented rate-limit text.
            data_str = str(data) if data is not None else ""
            is_rate_limit = any(marker in data_str for marker in _RATE_LIMIT_MARKERS)
            if is_rate_limit and attempt < _RATE_LIMIT_MAX_RETRIES:
                _logger.warning(
                    "order_list_query_rate_limit",
                    attempt=attempt + 1,
                    max_retries=_RATE_LIMIT_MAX_RETRIES,
                    backoff_seconds=_RATE_LIMIT_BACKOFF_SECONDS,
                    data=data_str,
                )
                await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                continue
            # Non-rate-limit error, or rate-limit persists after max retries → raise.
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
            float — ask price in USD (> 0).

        Raises:
            GatewayError: snapshot failed (ret != RET_OK / empty data), or the
                parsed price is 0.0 even after the last_price fallback (halted/
                illiquid). Finding 2.4: never return 0.0 — a silent 0.0 would let
                the engine compute a negative/zero limit price for a live order.
        """
        ret, data = await self.get_market_snapshot([code])
        if ret != RET_OK or data is None or len(data) == 0:
            raise GatewayError(f"get_ask_price snapshot failed for {code}: ret={ret}")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        ask = float(row.get("ask_price") or 0)
        if ask == 0.0:
            ask = float(row.get("last_price") or 0)
        if ask == 0.0:
            raise GatewayError(f"get_ask_price got zero price for {code} (halted/illiquid)")
        return ask

    async def get_bid_price(self, code: str) -> float:
        """Read the current bid price for a single code from a snapshot.

        Reads the `bid_price` column from get_market_snapshot. The column name
        `bid_price` is confirmed by the moomoo SDK snapshot response schema.
        If bid_price is null or 0 (illiquid/halted), falls back to `last_price`.
        The engine applies the -buffer; this method returns the raw market price.
        Deferred SDK import via the existing get_market_snapshot path.

        Returns:
            float — bid price in USD (> 0).

        Raises:
            GatewayError: snapshot failed (ret != RET_OK / empty data), or the
                parsed price is 0.0 even after the last_price fallback (halted/
                illiquid). Finding 2.4: never return 0.0 — a silent 0.0 would let
                the engine compute a negative/zero limit price for a live order.
        """
        ret, data = await self.get_market_snapshot([code])
        if ret != RET_OK or data is None or len(data) == 0:
            raise GatewayError(f"get_bid_price snapshot failed for {code}: ret={ret}")
        row = data.iloc[0] if hasattr(data, "iloc") else data[0]
        bid = float(row.get("bid_price") or 0)
        if bid == 0.0:
            bid = float(row.get("last_price") or 0)
        if bid == 0.0:
            raise GatewayError(f"get_bid_price got zero price for {code} (halted/illiquid)")
        return bid

    # --------------------------------------------------------
    # Reconciliation Skeletons (SAFE-02 / SAFE-03)
    # --------------------------------------------------------

    async def reconcile_once(self, store, manager, alerter) -> dict:
        """Broker-truth-wins drift reconciliation (SAFE-03 / D-07).

        Four cases handled:
          - In-flight (code in manager._exiting): skip to avoid racing manage_exit().
          - Externally-closed (in memory, flat at broker, NOT in _exiting):
            mark CLOSED in DB, remove from manager._positions, fire Telegram alert.
          - CLOSED-but-held / qty-drift (in memory AND in broker_map, but phase==CLOSED
            or remaining_quantity != broker qty): re-arm the in-memory position (set
            phase to ACTIVE if CLOSED; sync remaining to broker qty), update DB, audit
            reconcile_qty_drift, fire DRIFT alert (CR-01 reconcile half).
          - Orphan (broker has it, not in memory): INSERT OR IGNORE DB row (idempotent),
            subscribe 5m feed, call manager.adopt_orphan() to register in-memory so
            on_bar manages it the same cycle (CR-03). Audit drift_orphan_adopted ONLY
            when the INSERT actually inserted a row (rowcount==1, WR-04). On no-op
            INSERT, log reconcile_orphan_insert_noop instead.

        All DRIFT alert tasks are retained in self._bg_tasks with an add_done_callback
        so they are not GC'd before the coroutine completes (WR-03).

        This method does NOT change startup_reconcile — startup_reconcile is the
        authoritative boot-once path; reconcile_once mirrors its steady-state analog.

        Args:
            store:   StateStore instance (open).
            manager: PositionManager instance.
            alerter: TelegramAlerter for drift alerts.

        Returns:
            dict with keys 'closed', 'adopted', and 'reprotected' listing affected codes.
        """
        from bot.safety.audit_log import append_audit
        from bot.safety.et_helpers import now_et
        from bot.position.state import PositionPhase

        def _retain_task(coro):
            """Create and retain an alert task in self._bg_tasks (WR-03)."""
            t = asyncio.create_task(coro)
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
            return t

        ret_pos, broker_data = await self.get_positions(refresh_cache=True)

        # Fix 1.2: skip the reconcile cycle on a failed broker query.
        # Never derive "externally closed" from a failed/empty query — a transient
        # OpenD error must not wipe all managed positions. (T-06.2-01)
        if ret_pos != RET_OK or broker_data is None:
            _logger.error(
                "reconcile_skipped_broker_query_failed",
                ret=ret_pos,
            )
            if alerter is not None:
                try:
                    await alerter.send(
                        "WARN: position_list_query failed — reconcile cycle skipped"
                    )
                except Exception:
                    pass
            return {}

        broker_map = {}
        if len(broker_data) > 0:
            for _, row in broker_data.iterrows():
                code = str(row.get("code", "") or "")
                if not code:
                    continue
                broker_map[code] = {
                    "qty": int(float(row.get("qty", 0) or 0)),
                    "avg_cost": float(row.get("average_cost", 0) or 0),
                }

        now_ts = now_et().isoformat()
        closed_codes = []
        adopted_codes = []
        reprotected_codes = []

        # --- Check in-memory positions vs broker truth ---
        in_memory_codes = list(manager._positions.keys()) if hasattr(manager, "_positions") else []
        exiting_codes = getattr(manager, "_exiting", set())

        for code in in_memory_codes:
            if code in exiting_codes:
                _logger.debug("reconcile_skip_in_flight_exit", code=code)
                continue

            if code not in broker_map:
                # Externally closed (manual UI close or unknown fill) — D-07
                _logger.warning("reconcile_externally_closed", code=code)
                pos = manager._positions.get(code)
                if pos is not None:
                    store.mark_position_closed(pos.position_id, now_ts)
                    pos.phase = PositionPhase.CLOSED
                    del manager._positions[code]
                    append_audit({"event": "drift_closed_by_broker", "code": code})
                    # Fire Telegram alert (best-effort) — retained in _bg_tasks (WR-03)
                    _retain_task(
                        alerter.send(f"<b>DRIFT:</b> {code} closed externally — stopped managing.")
                    )
                    closed_codes.append(code)
            else:
                # Code is in BOTH memory AND broker — check for CLOSED-but-held or qty drift
                # (CR-01 reconcile half). Mirror startup_reconcile Step 3 (917-958).
                pos = manager._positions.get(code)
                if pos is None:
                    continue
                broker_qty = broker_map[code]["qty"]
                stored_qty = pos.remaining_quantity
                is_closed_but_held = (pos.phase == PositionPhase.CLOSED and broker_qty > 0)
                is_qty_drift = (stored_qty != broker_qty)

                if is_closed_but_held or is_qty_drift:
                    _logger.warning(
                        "reconcile_qty_drift",
                        code=code,
                        stored_qty=stored_qty,
                        broker_qty=broker_qty,
                        phase=str(pos.phase),
                    )
                    # Re-arm: if CLOSED, revert to ACTIVE so on_bar resumes management
                    if pos.phase == PositionPhase.CLOSED:
                        pos.phase = PositionPhase.ACTIVE
                    # Sync remaining_quantity to broker truth
                    pos.remaining_quantity = broker_qty
                    # Update DB row (remaining_quantity + phase)
                    store.update_position_qty_phase(
                        pos.position_id, broker_qty, pos.phase.value, now_ts
                    )
                    append_audit({
                        "event": "reconcile_qty_drift",
                        "code": code,
                        "stored_qty": stored_qty,
                        "broker_qty": broker_qty,
                    })
                    # Fire DRIFT alert — retained in _bg_tasks (WR-03)
                    _retain_task(
                        alerter.send(
                            f"<b>DRIFT:</b> {code} quantity/phase mismatch — "
                            f"memory qty={stored_qty}, broker qty={broker_qty}. Re-armed."
                        )
                    )
                    reprotected_codes.append(code)

        # --- Adopt orphan broker positions (CR-03 + WR-04) ---
        state_codes = set(in_memory_codes)
        # Pre-compute DB open-position codes for the ownership guard (SAFE-OG-01).
        # Covers the edge case where a position is in the DB but not yet in memory.
        open_pos_codes = {r["code"] for r in store.get_open_positions()}
        for code, bp in broker_map.items():
            if code in state_codes:
                continue
            if code in exiting_codes:
                continue

            # SAFE-OG-01: ownership + long-only guard.
            # Never adopt a position the bot has no DB record for (manual operator
            # holdings) or a short/option position the strategy cannot manage.
            _is_long = bp["qty"] > 0
            _bot_owned = store.has_pending_intent(code) or code in open_pos_codes
            if not _is_long or not _bot_owned:
                _reason = "not_long" if not _is_long else "not_bot_owned"
                _logger.warning(
                    "reconcile_external_position_ignored",
                    code=code,
                    broker_qty=bp["qty"],
                    reason=_reason,
                )
                continue

            # Finding 2.5: application-layer SELECT-before-INSERT guard. The DB
            # INSERT OR IGNORE below is keyed on position_id (a fresh UUID every
            # call), so it never collides on `code` — without this check, a code
            # whose open DB row already exists (e.g. manager._positions lost
            # track of it after a restart) would get a second, double-managed
            # row. open_pos_codes was already computed above for the ownership
            # guard; reuse it here rather than a second query.
            if code in open_pos_codes:
                _logger.warning("orphan_adoption_skipped_duplicate_code", code=code)
                continue

            _logger.warning("reconcile_orphan_adopting", code=code, broker_qty=bp["qty"])
            lod = await self._derive_lod_for_orphan(code)
            stop = self._compute_orphan_stop(lod)
            import uuid
            position_id = str(uuid.uuid4())

            # WR-04: check rowcount to suppress false audit on IGNORE collision.
            # If manager.adopt_orphan exists, we prefer to let it own the DB row
            # (DB-first write via _persist_position) — the gateway INSERT becomes
            # the idempotent guard. When rowcount==0, the position already exists
            # in the DB (prior adoption); log noop and skip.
            rowcount = store.insert_orphan_position(
                position_id, code, bp["avg_cost"], stop, bp["qty"], now_ts
            )

            if rowcount == 0:
                # INSERT OR IGNORE no-op — row already exists; do NOT audit or adopt (WR-04)
                _logger.warning(
                    "reconcile_orphan_insert_noop",
                    code=code,
                    reason="INSERT OR IGNORE had rowcount=0 (collision/pre-existing)",
                )
                continue

            # Row actually inserted (rowcount == 1) — register in-memory + audit (CR-03)
            append_audit({"event": "drift_orphan_adopted", "code": code})

            # CR-03: call manager.adopt_orphan to register in manager._positions
            # so on_bar manages it the same cycle (adopt-and-protect).
            if manager is not None and hasattr(manager, "adopt_orphan"):
                manager.adopt_orphan(
                    code=code,
                    qty=bp["qty"],
                    avg_cost=bp["avg_cost"],
                    stop=stop,
                    position_id=position_id,
                )

            # Subscribe 5m feed (gateway owns feed subscription)
            if manager is not None:
                try:
                    await self.subscribe([code])
                except Exception:
                    _logger.warning("drift_orphan_subscribe_failed", code=code, exc_info=True)

            adopted_codes.append(code)

        _logger.info(
            "reconcile_once_done",
            broker_positions=len(broker_map),
            closed=closed_codes,
            adopted=adopted_codes,
            reprotected=reprotected_codes,
        )
        return {"closed": closed_codes, "adopted": adopted_codes, "reprotected": reprotected_codes}

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
        # Use store.get_open_positions() — a guarded method that sets row_factory inside
        # the lock and returns a list of plain dicts (CR-01 / T-06.1-09-01).
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
                store.mark_position_closed(pos_row["position_id"], now_ts)
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
                    store.update_position_qty(pos_row["position_id"], broker_qty, now_ts)
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

            # SAFE-OG-01: ownership + long-only guard.
            # state_codes already excludes every position the bot DB knows about
            # (Step 3). The only remaining bot-ownership signal is a pending_intent
            # row: the bot wrote it before placing the order, then crashed before
            # the position row was persisted (crash-recovery path).
            _is_long = bp["qty"] > 0
            _bot_owned = store.has_pending_intent(code)
            if not _is_long or not _bot_owned:
                _reason = "not_long" if not _is_long else "not_bot_owned"
                _logger.warning(
                    "reconcile_external_position_ignored",
                    code=code,
                    broker_qty=bp["qty"],
                    reason=_reason,
                )
                continue

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
            store.insert_orphan_position(
                position_id, code, bp["avg_cost"], stop, bp["qty"], now_ts
            )
            # Return value (rowcount) unused here — startup_reconcile does not gate on it
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

        # ---- Step 5: Each guarded store method above already committed; no batch commit needed ----

        # ---- Step 6: Reconcile pending_intents to catch crash-between-place-and-persist ----
        # Any PENDING intent whose code already has an open broker position was likely
        # placed before the crash — do not re-enter; leave the intent as PENDING
        # so the duplicate guard in consume_intent will block it on next run (Pitfall F).
        pending_rows = store.get_pending_intent_codes("PENDING")
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
        """Derive the orphan adoption stop via LOD - initial_stop_pct% (D-10 / Finding 2.8).

        Uses self._initial_stop_pct (threaded in from StrategyConfig.initial_stop_pct
        at construction — CFG-01: rules.json is the single source of truth) rather
        than a hardcoded 1% fraction, so the orphan-adoption stop always matches the
        live strategy's initial_stop (bot/strategy/trend_join_long.py uses the same
        lod * (1 - cfg.initial_stop_pct/100) formula). Falls back to the 1.0 default
        set at __init__ when no config was threaded through (safe, matches prior
        hardcoded 1% behavior).

        Args:
            lod: Low-of-day price (0.0 if unavailable).

        Returns:
            float — stop price at lod * (1 - initial_stop_pct/100). Returns 0.0 if
            lod <= 0.
        """
        if lod <= 0:
            return 0.0
        return float(lod * (1.0 - self._initial_stop_pct / 100.0))

    async def reconciliation_loop(
        self,
        store,
        manager,
        alerter,
        interval_s: float = 75.0,
    ) -> None:
        """Run reconcile_once() on a 60-90s loop (SAFE-03).

        store:     StateStore instance.
        manager:   PositionManager instance.
        alerter:   TelegramAlerter for drift alerts.
        interval_s: cycle cadence in seconds (default 75.0 within SAFE-03 60-90s window).

        A single reconcile_once() failure (network blip, SDK error) is logged
        and the loop continues — one broker error must never permanently kill
        the SAFE-03 reconciliation loop (WR-03). asyncio.CancelledError is
        re-raised so the loop still shuts down cleanly on cancellation.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.reconcile_once(store=store, manager=manager, alerter=alerter)
            except asyncio.CancelledError:
                raise  # propagate cancellation for clean shutdown
            except Exception:
                # Keep looping — a transient broker error must not silently
                # disable the SAFE-03 reconciliation loop (WR-03).
                _logger.error("reconcile_failed", exc_info=True)
