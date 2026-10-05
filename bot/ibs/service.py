#!/usr/bin/env python3
"""
bot.ibs.service — IbsBot: the always-on IBS ETF mean-reversion process (Phase 12).

OD-2 / D-13: a separate process with its OWN DB, kill file, report dir and log,
so it coexists with the equity and options bots on the shared paper account.
TradingBot is a TEMPLATE, not a base class (D4): its __init__ wires a bar
aggregator, scanner and PositionManager that an end-of-day ETF strategy has no
use for.

SAFE-OG-01 analog (D-12): reconcile only inspects codes that appear on a row in
THIS bot's database. Broker holdings on any other code are counted, logged and
otherwise invisible — never closed, adopted, or written to the DB.

D-06: positions are held overnight by design. There is NO force-close and no
flattening anywhere in this bot — only rule-decided exits sell. D-08: LIMIT
orders only, through IbsExecutor. Nothing here places an order directly.

Exports: IbsBot
"""
import asyncio
import html
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.ibs.execution import IbsExecutor
from bot.ibs.store import ACTIVE_STATUSES
from bot.ibs.strategy import decide_exits, parse_snapshot, trading_days_held
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import get_market_close_et, is_trading_day, trading_days_between

_logger = get_logger(__name__)

_ET = ZoneInfo("America/New_York")
_RET_OK = 0
_DECISION_META_KEY = "ibs_decision_date"


# ============================================================
# Helpers
# ============================================================

def _esc(value) -> str:
    """HTML-escape any value for interpolation into an alert body (T-12-06)."""
    return html.escape(str(value))


def _signed(value) -> str:
    """Format a dollar amount with an explicit +/- sign."""
    return f"{float(value):+,.2f}"


def _parse_hhmm(value: str):
    """Split an "HH:MM" string into (hour, minute) ints."""
    hour, minute = str(value).split(":")
    return int(hour), int(minute)


def _close_dt(day) -> datetime:
    """The market close of `day` as an ET-aware datetime (half-days honoured)."""
    hour, minute = _parse_hhmm(get_market_close_et(day))
    return datetime.combine(day, time(hour, minute), tzinfo=_ET)


def _rows(data) -> list:
    """Normalise a gateway DataFrame (or list of dicts) to a list of dicts."""
    if data is None or len(data) == 0:
        return []
    if hasattr(data, "iterrows"):
        return [row.to_dict() for _, row in data.iterrows()]
    return list(data)


# ============================================================
# IbsBot
# ============================================================

class IbsBot:
    """Always-on APScheduler-driven IBS process.

    cfg:         IbsConfig.
    gateway:     MoomooGateway — connect/close/positions/snapshot/orders.
    store:       IbsStore — open handle on the IBS DB (its OWN file).
    kill_switch: KillSwitch — install + check_file + triggered.
    alerter:     TelegramAlerter — fire-and-forget send() coroutine.
    watchdog:    OpenDWatchdog or None — injected after construction.
    """

    def __init__(self, cfg, gateway, store, kill_switch, alerter, watchdog=None) -> None:
        self._cfg = cfg
        self._gateway = gateway
        self._store = store
        self._kill_switch = kill_switch
        self._alerter = alerter
        self._watchdog = watchdog
        self._watchdog_task = None

        self._scheduler = AsyncIOScheduler(timezone=_ET)
        self._entries_enabled: bool = False
        self._executor = IbsExecutor(gateway, cfg)
        self._decision_task = None

        # OpenDWatchdog duck-types bot._entries_enabled/_store/_position_manager/
        # _bar_agg; it guards on `is not None`. Its reconnect runs the equity
        # startup_reconcile against this store, inert because IBS tables are ibs_*.
        self._position_manager = None
        self._bar_agg = None

    # --------------------------------------------------------
    # Readiness gate
    # --------------------------------------------------------

    async def _readiness_gate(self) -> None:
        """Connect (paper guard, D-10), reconcile, install kill switch, enable entries.

        Entries stay disabled until every step passes; a connect failure propagates.
        """
        result = self._gateway.connect()
        if asyncio.iscoroutine(result):
            await result

        await self.reconcile(startup=True)

        self._kill_switch.install()

        self._entries_enabled = True
        _logger.info("ibs_readiness_gate_passed")

    # --------------------------------------------------------
    # Reconcile
    # --------------------------------------------------------

    async def _broker_shares(self) -> dict:
        """Broker holdings as {code: int shares} for non-zero positions."""
        ret, data = await self._gateway.get_positions()
        if ret != _RET_OK or data is None:
            raise RuntimeError("ibs broker position query failed")
        out = {}
        for row in _rows(data):
            qty = int(float(row.get("qty") or 0))
            if qty != 0:
                out[str(row["code"])] = qty
        return out

    async def reconcile(self, startup: bool = False) -> dict:
        """Compare this bot's rows against the broker; flag drift, never trade.

        On startup an OPENING/CLOSING row means the process died mid-order: it is
        flagged NEEDS_ATTENTION. In steady state only OPEN rows are compared, so
        the OPEN -> NEEDS_ATTENTION flip is the alert-once guard (D-11). Returns
        the broker holdings map (Plan 08 uses it for the external-holdings exclusion).
        """
        broker = await self._broker_shares()
        statuses = ("OPEN", "OPENING", "CLOSING") if startup else ("OPEN",)

        for row in self._store.get_positions(statuses):
            pid, code, status = row["position_id"], row["code"], row["status"]
            if status in ("OPENING", "CLOSING"):
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                await self._alerter.send(
                    f"<b>IBS NEEDS ATTENTION</b> {_esc(code)} — restarted mid-"
                    f"{_esc(status.lower())}; cancel any working order for it in "
                    f"moomoo, then reconcile manually."
                )
                append_audit({"event": "ibs_reconcile_incomplete", "position_id": pid,
                              "code": code, "status": status})
                _logger.warning("ibs_reconcile_incomplete", position_id=pid, code=code)
                continue

            expected = int(row["qty"])
            actual = broker.get(code, 0)
            if actual == expected:
                continue
            self._store.set_position_status(pid, "NEEDS_ATTENTION")
            await self._alerter.send(
                f"<b>IBS NEEDS ATTENTION</b> {_esc(code)} — DB {_esc(expected)}, "
                f"broker {_esc(actual)}; not trading it until fixed."
            )
            append_audit({"event": "ibs_reconcile_mismatch", "position_id": pid,
                          "code": code, "expected_qty": expected, "broker_qty": actual})
            _logger.warning("ibs_reconcile_mismatch", position_id=pid, code=code,
                            expected=expected, broker=actual)

        # D-12: somebody else's holdings on the shared account — log only.
        active = {r["code"] for r in self._store.get_positions(ACTIVE_STATUSES)}
        external = [c for c in self._cfg.universe if broker.get(c, 0) != 0 and c not in active]
        _logger.info("ibs_reconcile_external_ignored", count=len(external), codes=external)
        return broker
