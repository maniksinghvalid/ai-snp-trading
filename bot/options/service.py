#!/usr/bin/env python3
"""
bot.options.service — OptionsBot: the always-on options process (Phase 8, D4/D5/D6).

Composes the Phase 8 parts into a bot that trades unattended: an AsyncIOScheduler
(America/New_York) drives three jobs — entry scan, manage, EOD report — around a
hard startup readiness gate (connect → reconcile → enable entries) and a graceful
kill-switch shutdown.

D4: self-contained package. TradingBot is a TEMPLATE, not a base class — its
__init__ wires BarAggregator/scanner/PositionManager, none of which an options
spread has any use for. D6: own DB, own kill file, own report dir, so this bot
and the equity bot coexist on the shared paper account.

SAFE-OG-01: reconcile only ever inspects codes that appear on a position row in
THIS bot's database. A broker option code the bot never wrote is counted, logged,
and otherwise invisible — it is never closed, adopted, or traded.

Exports: OptionsBot, main
"""
import asyncio
import html
from datetime import datetime, time as _time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.options.execution import LegExecutor
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.logger import get_logger
from bot.scanner.calendar import get_market_close_et, is_trading_day


# ============================================================
# Module-level constants
# ============================================================

_logger = get_logger(__name__)

_RET_OK = 0
_ET = ZoneInfo("America/New_York")

# Not strategy knobs, so deliberately NOT rules_options.json keys: these two keep
# the manage job out of the opening price discovery and out of the closing
# auction, where option quotes are too wide to mark a spread honestly.
_MANAGE_START_ET = _time(9, 35)
_MANAGE_CLOSE_BUFFER_MIN = 5

_BREAKER_META_KEY = "options_breaker_date"
_MISFIRE_GRACE_S = 300
_SNAPSHOT_CHUNK = 400
_CONTRACT_MULTIPLIER = 100

_ACTIVE_STATUSES = ("OPENING", "OPEN", "CLOSING", "NEEDS_ATTENTION")
_OPEN_STATUSES = ("OPEN", "OPENING")


# ============================================================
# Pure helpers (no self, no I/O)
# ============================================================

def _ivr_pct(row: dict):
    """Return the underlying's IV rank in PERCENT (0-100), or None.

    The SDK reports iv_rank/iv_percentile as FRACTIONS (0.066 = 6.6%). This is
    the single conversion point in the whole codebase — passes_entry_gate takes
    percent, so the x100 must happen exactly once, here.
    """
    value = row.get("u_iv_rank")
    return None if value is None else float(value) * 100


def _ivp_pct(row: dict):
    """Return the underlying's IV percentile in PERCENT (0-100), or None."""
    value = row.get("u_iv_percentile")
    return None if value is None else float(value) * 100


def _group_rows_by_underlying(rows) -> dict:
    """Group chain rows by u_stock_id; rows with no underlying id are dropped."""
    out: dict = {}
    for row in rows or []:
        sid = row.get("u_stock_id")
        if sid is None:
            continue
        out.setdefault(sid, []).append(row)
    return out


def _esc(value) -> str:
    """HTML-escape any value for interpolation into an alert body (T-1ie-02)."""
    return html.escape(str(value))


def _signed(value) -> str:
    """Format a dollar amount with an explicit +/- sign."""
    return f"{float(value):+,.2f}"


def _fmt_entry(pos: dict, legs) -> str:
    """Telegram body for a filled spread entry (every field escaped)."""
    ivr = pos.get("ivr_at_entry")
    lines = [
        f"<b>Options entry</b> {_esc(pos.get('underlying'))}",
        f"{_esc(pos.get('structure'))} exp {_esc(pos.get('expiry'))} "
        f"({_esc(pos.get('dte_at_entry'))} DTE)",
        f"IVR {'n/a' if ivr is None else _esc(round(float(ivr), 1))} "
        f"| qty {_esc(pos.get('qty'))}",
        f"credit {_esc(round(float(pos.get('credit_per_spread') or 0), 2))} "
        f"/ width {_esc(round(float(pos.get('width') or 0), 2))} "
        f"| max loss ${_esc(round(float(pos.get('max_loss_usd') or 0), 2))}",
    ]
    qty = pos.get("qty")
    for leg in legs or []:
        lines.append(
            f"{_esc(leg.get('side'))} {_esc(qty)}x {_esc(leg.get('code'))} "
            f"@ {_esc(leg.get('strike'))}"
        )
    return "\n".join(lines)


def _fmt_exit(pos: dict, reason, pnl_usd, pct_of_credit) -> str:
    """Telegram body for a closed spread (every field escaped)."""
    return (
        f"<b>Options exit</b> {_esc(pos.get('underlying'))}\n"
        f"reason {_esc(reason)}\n"
        f"realized ${_esc(_signed(pnl_usd))} "
        f"({_esc(round(float(pct_of_credit), 1))}% of credit)"
    )


def _fmt_summary(open_positions, closed_today, realized_today) -> str:
    """Telegram body for the EOD summary (every field escaped)."""
    lines = [
        "<b>Options EOD</b>",
        f"open {_esc(len(open_positions))} | closed today {_esc(len(closed_today))} "
        f"| realized ${_esc(_signed(realized_today))}",
    ]
    for pos in open_positions:
        lines.append(
            f"{_esc(pos.get('underlying'))} {_esc(pos.get('structure'))} "
            f"exp {_esc(pos.get('expiry'))} qty {_esc(pos.get('qty'))} "
            f"credit {_esc(round(float(pos.get('credit_per_spread') or 0), 2))}"
        )
    return "\n".join(lines)


def _options_html(open_positions, closed_today, date_str: str) -> str:
    """Build the self-contained EOD HTML document (no JS, no CDN — D-15 parity)."""
    def _cells(values):
        return "".join(f"<td>{_esc(v)}</td>" for v in values)

    open_rows = "".join(
        "<tr>" + _cells([
            p.get("underlying"), p.get("structure"), p.get("expiry"),
            p.get("qty"), p.get("credit_per_spread"), p.get("max_loss_usd"),
        ]) + "</tr>"
        for p in open_positions
    ) or "<tr><td colspan='6'>none</td></tr>"

    closed_rows = "".join(
        "<tr>" + _cells([
            p.get("underlying"), p.get("close_reason"), p.get("realized_pnl_usd"),
        ]) + "</tr>"
        for p in closed_today
    ) or "<tr><td colspan='3'>none</td></tr>"

    return (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
        f"<title>Options — {_esc(date_str)}</title>\n"
        "<style>\n"
        "  body { font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }\n"
        "  h1, h2 { color: #58a6ff; }\n"
        "  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }\n"
        "  th { background: #161b22; text-align: left; padding: 6px 12px; }\n"
        "  td { padding: 4px 12px; border-bottom: 1px solid #21262d; }\n"
        "</style></head><body>\n"
        f"<h1>Options — {_esc(date_str)}</h1>\n"
        "<h2>Open</h2><table><tr><th>Underlying</th><th>Structure</th><th>Expiry</th>"
        f"<th>Qty</th><th>Credit</th><th>Max loss</th></tr>{open_rows}</table>\n"
        "<h2>Closed today</h2><table><tr><th>Underlying</th><th>Reason</th>"
        f"<th>Realized</th></tr>{closed_rows}</table>\n"
        "</body></html>\n"
    )


# ============================================================
# OptionsBot
# ============================================================

class OptionsBot:
    """Always-on APScheduler-driven options process (D4/D5/D6).

    cfg:         OptionsConfig — every timing/threshold value (CFG-01).
    gateway:     MoomooGateway — connect/close/screen/snapshot/positions/orders.
    store:       OptionsStore — open handle on the options DB (its OWN file).
    kill_switch: KillSwitch — install + check_file + triggered.
    alerter:     TelegramAlerter — fire-and-forget send() coroutine.
    watchdog:    OpenDWatchdog or None — injected after construction (needs bot ref).
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
        # One lock serialises entry vs manage so the shared order_list_query
        # budget (10 req / 30s) is never contended (T-1ie-03).
        self._lock = asyncio.Lock()
        self._executor = LegExecutor(gateway, cfg)
        self._stock_ids: dict = {}

        # OpenDWatchdog duck-types bot._entries_enabled/_store/_position_manager/
        # _bar_agg. The options bot has neither a PositionManager nor a bar
        # aggregator; the watchdog's `is not None` guards handle the None case.
        self._position_manager = None
        self._bar_agg = None

    # --------------------------------------------------------
    # Readiness gate
    # --------------------------------------------------------

    async def _readiness_gate(self) -> None:
        """Connect, reconcile against broker truth, then enable entries.

        Entries stay disabled until every step passes — a restart must never
        open a new spread before it knows what is already on the account.
        """
        result = self._gateway.connect()
        if asyncio.iscoroutine(result):
            await result

        await self.reconcile(startup=True)

        # No kill_switch.register_flush: every options write is committed
        # synchronously through OptionsStore, so there is no in-memory state
        # that a flush could save.
        self._kill_switch.install()

        self._entries_enabled = True
        _logger.info("readiness_gate_passed")

    # --------------------------------------------------------
    # Reconcile
    # --------------------------------------------------------

    async def reconcile(self, startup: bool = False) -> None:
        """Compare DB leg quantities against the broker; flag any drift.

        On startup an OPENING/CLOSING row means the process died mid-order: it
        is flagged NEEDS_ATTENTION and alerted, never auto-resumed. In steady
        state only OPEN rows are inspected, so the OPEN → NEEDS_ATTENTION
        transition IS the alert-once guard — no extra flag is needed.

        Broker option codes that appear on no position in this DB are counted
        and logged only (SAFE-OG-01: the paper account is shared with a human).
        """
        broker = await self._gateway.get_option_positions()
        statuses = ("OPEN", "OPENING", "CLOSING") if startup else ("OPEN",)
        positions = self._store.get_option_positions(statuses)

        known_codes = set()
        for pos in positions:
            pid = pos["position_id"]
            legs = pos.get("legs") or []
            known_codes.update(leg["code"] for leg in legs)
            status = pos.get("status")

            if startup and status in ("OPENING", "CLOSING"):
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                await self._alerter.send(
                    f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                    f"restarted mid-{_esc(str(status).lower())}; close manually."
                )
                append_audit({
                    "event": "options_reconcile_incomplete",
                    "position_id": pid,
                    "underlying": pos.get("underlying"),
                    "status": status,
                })
                _logger.warning(
                    "options_reconcile_incomplete", position_id=pid, status=status,
                )
                continue

            mismatch = None
            for leg in legs:
                qty = int(leg.get("qty") or 0)
                expected = qty if leg.get("side") == "BUY" else -qty
                actual = int(broker.get(leg["code"], 0) or 0)
                if actual != expected:
                    mismatch = (leg["code"], expected, actual)
                    break

            if mismatch is None:
                continue

            code, expected, actual = mismatch
            self._store.set_position_status(pid, "NEEDS_ATTENTION")
            await self._alerter.send(
                f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                f"{_esc(code)} expected {_esc(expected)}, broker has {_esc(actual)}."
            )
            append_audit({
                "event": "options_reconcile_mismatch",
                "position_id": pid,
                "underlying": pos.get("underlying"),
                "code": code,
                "expected_qty": expected,
                "broker_qty": actual,
            })
            _logger.warning(
                "options_reconcile_mismatch",
                position_id=pid, code=code, expected=expected, broker=actual,
            )

        # Never place an order for, close, or write a row for these — they are
        # somebody else's positions on a shared account (SAFE-OG-01).
        ignored = [c for c in broker if c not in known_codes]
        _logger.debug("options_reconcile_external_ignored", count=len(ignored))

    # --------------------------------------------------------
    # Session helpers
    # --------------------------------------------------------

    def _is_rth_now(self) -> bool:
        """True only inside the manageable part of a trading session (ET)."""
        now = now_et()
        today = now.date()
        if not is_trading_day(today):
            return False
        hour, minute = (int(part) for part in get_market_close_et(today).split(":"))
        cutoff = datetime.combine(
            today, _time(hour, minute), tzinfo=_ET,
        ) - timedelta(minutes=_MANAGE_CLOSE_BUFFER_MIN)
        return _MANAGE_START_ET <= now.time() and now < cutoff

    # --------------------------------------------------------
    # Shutdown + run loop
    # --------------------------------------------------------

    async def _shutdown(self) -> None:
        """Graceful shutdown: audit, close gateway, final alert, stop scheduler."""
        _logger.info("options_shutdown_start")

        try:
            append_audit({"event": "options_bot_shutdown", "reason": "kill_switch"})
        except Exception:
            pass  # audit write must never block shutdown

        try:
            result = self._gateway.close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _logger.warning("options_gateway_close_error", exc_info=True)

        try:
            await self._alerter.send("<b>Options bot stopped</b>")
        except Exception:
            pass  # alert failure must never block shutdown

        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass

        _logger.info("options_shutdown_complete")

    async def run(self) -> None:
        """Run the options bot lifecycle until the kill switch trips."""
        try:
            await self._readiness_gate()

            if not self._alerter._enabled:
                _logger.warning(
                    "alerter_disabled_at_startup",
                    reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured",
                )

            self._register_jobs()
            self._scheduler.start()
            _logger.info("options_bot_started")

            if self._watchdog is not None:
                self._watchdog_task = asyncio.create_task(self._watchdog.run())
                _logger.info("options_watchdog_started")

            while not self._kill_switch.triggered:
                if self._kill_switch.check_file():
                    self._kill_switch.trigger("sentinel_file")
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_bot_run_error", exc_info=True)
            raise
        finally:
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            await self._shutdown()
