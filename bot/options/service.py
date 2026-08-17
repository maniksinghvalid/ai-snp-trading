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
import os
import sys
from datetime import date, datetime, time as _time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.config.loader import ConfigError
from bot.gateway.gateway import MoomooGateway, get_gateway_config
from bot.options.config import load_options_config
from bot.options.execution import LegExecutor
from bot.options.store import OptionsStore
from bot.options.strategy import (
    manage_decision,
    mark_spread,
    option_dte,
    passes_entry_gate,
    pick_expiry,
    pick_strikes,
    size_position,
)
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.kill_switch import KillSwitch
from bot.safety.logger import configure_logging, get_logger
from bot.scanner.calendar import get_market_close_et, is_trading_day
from bot.service.alerter import TelegramAlerter
from bot.service.report import write_reports
from bot.service.watchdog import OpenDWatchdog


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


def _rows(data):
    """Normalise a gateway payload (DataFrame or list of dicts) to a list."""
    return data.to_dict("records") if hasattr(data, "to_dict") else list(data or [])


def _chunks(seq, size):
    """Yield successive slices of `seq` of at most `size` items."""
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


def _parse_hhmm(value: str):
    """Split an "HH:MM" config string into (hour, minute) ints."""
    hour, minute = str(value).split(":")
    return int(hour), int(minute)


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
        hour, minute = _parse_hhmm(get_market_close_et(today))
        cutoff = datetime.combine(
            today, _time(hour, minute), tzinfo=_ET,
        ) - timedelta(minutes=_MANAGE_CLOSE_BUFFER_MIN)
        return _MANAGE_START_ET <= now.time() and now < cutoff

    # --------------------------------------------------------
    # Job registration
    # --------------------------------------------------------

    def _register_jobs(self) -> None:
        """Register the entry-scan (x1 or x2), manage and EOD jobs.

        Job ids (exact): options_entry_scan, options_entry_scan_2 (only when
        cfg.second_entry_scan_et is set), options_manage, options_eod. Every
        timing value comes from rules_options.json (CFG-01).
        """
        cfg = self._cfg
        common = dict(coalesce=True, max_instances=1, misfire_grace_time=_MISFIRE_GRACE_S)

        hour, minute = _parse_hhmm(cfg.entry_scan_et)
        self._scheduler.add_job(
            self._job_entry_scan,
            CronTrigger(hour=hour, minute=minute, timezone=_ET),
            id="options_entry_scan", **common,
        )

        if cfg.second_entry_scan_et is not None:
            hour, minute = _parse_hhmm(cfg.second_entry_scan_et)
            self._scheduler.add_job(
                self._job_entry_scan,
                CronTrigger(hour=hour, minute=minute, timezone=_ET),
                id="options_entry_scan_2", **common,
            )

        self._scheduler.add_job(
            self._job_manage,
            IntervalTrigger(minutes=cfg.manage_interval_min, timezone=_ET),
            id="options_manage", **common,
        )

        hour, minute = _parse_hhmm(cfg.eod_report_et)
        self._scheduler.add_job(
            self._job_eod,
            CronTrigger(hour=hour, minute=minute, timezone=_ET),
            id="options_eod", **common,
        )

        _logger.info(
            "options_jobs_registered",
            job_ids=[j.id for j in self._scheduler.get_jobs()],
        )

    # --------------------------------------------------------
    # Entry scan
    # --------------------------------------------------------

    async def _job_entry_scan(self) -> None:
        """Screen the chain once per right and open at most one spread per underlying."""
        try:
            cfg = self._cfg
            today = now_et().date()

            if not is_trading_day(today):
                _logger.info("options_entry_scan_skipped", reason="not_trading_day")
                return
            if not self._entries_enabled:
                _logger.info("options_entry_scan_skipped", reason="entries_disabled")
                return
            if self._kill_switch.triggered:
                _logger.info("options_entry_scan_skipped", reason="kill_switch")
                return
            if self._store.get_meta(_BREAKER_META_KEY) == today.isoformat():
                _logger.info("options_entry_scan_skipped", reason="daily_loss_breaker")
                return

            opened_today = self._store.count_opened_on(today.isoformat())
            if opened_today >= cfg.max_new_positions_per_day:
                _logger.info("options_entry_scan_skipped", reason="per_day_cap")
                return

            async with self._lock:
                await self._scan_and_open(today, opened_today)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_entry_scan_error", exc_info=True)

    async def _scan_and_open(self, today, opened_today: int) -> None:
        """Screen, select and open — the locked body of the entry scan."""
        cfg = self._cfg

        if not self._stock_ids:
            self._stock_ids = await self._gateway.get_stock_ids(list(cfg.universe))
        if not self._stock_ids:
            _logger.warning("options_entry_scan_no_stock_ids")
            return
        by_id = {sid: code for code, sid in self._stock_ids.items()}
        ids = list(self._stock_ids.values())

        # The delta bounds here are screen BREADTH, not a strategy threshold —
        # the real knob is cfg.short_delta, which pick_strikes applies to
        # whatever the screen returns.
        rows = await self._gateway.screen_options(
            ids, "P", cfg.min_dte, cfg.max_dte, -0.35, -0.03,
        )
        if cfg.structure_type == "iron_condor":
            rows = list(rows or []) + list(await self._gateway.screen_options(
                ids, "C", cfg.min_dte, cfg.max_dte, 0.03, 0.35,
            ) or [])

        active = self._store.get_option_positions(_ACTIVE_STATUSES)
        busy = {p["underlying"] for p in active}
        open_max_loss_total = sum(
            float(p["max_loss_usd"] or 0) for p in active if p["status"] in _OPEN_STATUSES
        )
        open_count = sum(1 for p in active if p["status"] in _OPEN_STATUSES)

        for sid, u_rows in sorted(_group_rows_by_underlying(_rows(rows)).items()):
            code = by_id.get(sid)
            if code is None:
                continue
            if (opened_today >= cfg.max_new_positions_per_day
                    or open_count >= cfg.max_concurrent_positions):
                break
            if code in busy:
                continue

            try:
                pos = await self._try_open(code, u_rows, today, open_max_loss_total)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One malformed chain must never abort the rest of the scan.
                _logger.error(
                    "options_entry_underlying_error", underlying=code, exc_info=True,
                )
                continue

            busy.add(code)
            if pos is not None:
                opened_today += 1
                open_count += 1
                open_max_loss_total += float(pos["max_loss_usd"])

    async def _try_open(self, code, u_rows, today, open_max_loss_total):
        """Evaluate one underlying and, if it qualifies, open the spread.

        Returns the inserted position dict on a filled open, else None.
        """
        cfg = self._cfg
        head = u_rows[0]

        u = {
            "ivr_pct": _ivr_pct(head),
            "ivp_pct": _ivp_pct(head),
            # ASSUMPTION: u_change_ratio is already a percent. UNVERIFIED against
            # a live payload — scripts/uat_options_probe.py is the check. If it
            # turns out to be a fraction, the x100 belongs right here, next to
            # the IVR conversion above.
            "change_pct": head.get("u_change_ratio"),
        }
        if not passes_entry_gate(u, cfg):
            return None

        expiries = sorted({
            (date.fromisoformat(r["expiry"]), int(r["dte"]))
            for r in u_rows if r.get("expiry") and r.get("dte") is not None
        })
        exp = pick_expiry(expiries, today, cfg)
        if exp is None:
            return None

        chain = [r for r in u_rows if r.get("expiry") == exp.isoformat()]
        u_price = head.get("u_price")
        if not u_price:
            return None

        sel = pick_strikes(chain, float(u_price), cfg.structure_type, cfg)
        if sel is None:
            return None

        qty = size_position(sel["width"], sel["credit"], cfg, open_max_loss_total)
        if qty < 1:
            return None

        position_id = uuid4().hex
        pos = {
            "position_id": position_id,
            "underlying": code,
            "structure": cfg.structure_type,
            "expiry": exp.isoformat(),
            "dte_at_entry": option_dte(exp, today),
            "ivr_at_entry": u["ivr_pct"],
            "credit_per_spread": sel["credit"],
            "width": sel["width"],
            "qty": qty,
            "max_loss_usd": (sel["width"] - sel["credit"]) * _CONTRACT_MULTIPLIER * qty,
            "status": "OPENING",
            "opened_at": now_et().isoformat(),
        }
        self._store.insert_option_position(pos)

        leg_ids = {}
        for leg in sel["legs"]:
            leg_id = uuid4().hex
            leg_ids[leg["code"]] = leg_id
            self._store.insert_option_leg({
                "leg_id": leg_id,
                "position_id": position_id,
                "code": leg["code"],
                "right": leg["right"],
                "strike": leg["strike"],
                "side": leg["side"],
                "qty": qty,
                "status": "PENDING",
            })

        quotes = {r["code"]: {"bid": r["bid"], "ask": r["ask"]} for r in chain}

        async def _on_placed(leg, order_id):
            self._store.set_leg_entry(
                leg_ids[leg["code"]], order_id=order_id, status="WORKING",
            )

        async def _on_filled(leg, order_id, price, filled_qty):
            self._store.set_leg_entry(
                leg_ids[leg["code"]], price=price, status="FILLED",
            )

        filled = await self._executor.open_position(
            sel["legs"], qty, quotes,
            on_leg_placed=_on_placed, on_leg_filled=_on_filled,
        )

        if filled is None:
            # The executor already unwound whatever filled; the ABORTED row is
            # the operator's signal, so the leg rows are left as it left them.
            self._store.set_position_status(
                position_id, "ABORTED",
                closed_at=now_et().isoformat(), close_reason="open_failed",
            )
            await self._alerter.send(
                f"<b>Options entry failed</b> {_esc(code)} — legs unwound, "
                f"position aborted."
            )
            append_audit({
                "event": "options_position_aborted",
                "position_id": position_id, "underlying": code,
            })
            return None

        self._store.set_position_status(position_id, "OPEN")
        await self._alerter.send(_fmt_entry(pos, sel["legs"]))
        append_audit({
            "event": "options_position_opened",
            "position_id": position_id,
            "underlying": code,
            "structure": pos["structure"],
            "expiry": pos["expiry"],
            "qty": qty,
            "credit_per_spread": sel["credit"],
            "max_loss_usd": pos["max_loss_usd"],
        })
        return pos

    # --------------------------------------------------------
    # Manage
    # --------------------------------------------------------

    async def _job_manage(self) -> None:
        """Mark every open spread from one snapshot and act on the strategy's call."""
        try:
            today = now_et().date()
            if not is_trading_day(today):
                _logger.info("options_manage_skipped", reason="not_trading_day")
                return
            if not self._is_rth_now():
                _logger.info("options_manage_skipped", reason="outside_manage_window")
                return

            async with self._lock:
                await self._manage_once(today)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_manage_error", exc_info=True)

    async def _manage_once(self, today) -> None:
        """Reconcile, snapshot, then mark/close each open position (locked body)."""
        await self.reconcile()

        positions = self._store.get_option_positions(("OPEN",))
        if not positions:
            return

        codes = sorted({leg["code"] for p in positions for leg in p["legs"]})
        quotes = {}
        for chunk in _chunks(codes, _SNAPSHOT_CHUNK):
            ret, data = await self._gateway.get_market_snapshot(chunk)
            if ret != _RET_OK:
                _logger.warning("options_snapshot_failed", codes=len(chunk))
                continue
            for row in _rows(data):
                quotes[row["code"]] = {
                    "bid": row.get("bid_price"), "ask": row.get("ask_price"),
                }

        unrealized_total = 0.0
        for pos in positions:
            try:
                unrealized_total += await self._manage_position(pos, quotes, today)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error(
                    "options_manage_position_error",
                    position_id=pos.get("position_id"), exc_info=True,
                )
                continue

        await self._check_daily_breaker(today, unrealized_total)

    async def _manage_position(self, pos, quotes, today) -> float:
        """Mark one position and close it if the strategy says so.

        Returns its unrealized P&L in dollars (0.0 once a close is attempted).
        """
        cfg = self._cfg
        pid = pos["position_id"]
        legs = pos["legs"]

        if any(leg["code"] not in quotes for leg in legs):
            # mark_spread would KeyError; a stale mark is worse than no action.
            _logger.warning("options_manage_missing_quote", position_id=pid)
            return 0.0

        mark = mark_spread(legs, quotes)
        dte = option_dte(date.fromisoformat(pos["expiry"]), today)
        credit = float(pos["credit_per_spread"])
        qty = int(pos["qty"])

        dec = manage_decision(mark, credit, dte, cfg)
        if dec is None:
            return (credit - mark) * _CONTRACT_MULTIPLIER * qty

        self._store.set_position_status(pid, "CLOSING")
        exits = {}

        async def _on_exit_placed(leg, order_id):
            self._store.set_leg_exit(leg["leg_id"], order_id=order_id, status="CLOSING")

        async def _on_exit_filled(leg, order_id, price, filled_qty):
            exits[leg["code"]] = price
            self._store.set_leg_exit(leg["leg_id"], price=price, status="CLOSED")

        ok = await self._executor.close_legs(
            legs, quotes, aggressive=(dec == "assignment_guard"),
            on_leg_placed=_on_exit_placed, on_leg_filled=_on_exit_filled,
        )

        if not ok:
            self._store.set_position_status(pid, "NEEDS_ATTENTION")
            await self._alerter.send(
                f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                f"close incomplete — check the account."
            )
            append_audit({
                "event": "options_close_incomplete",
                "position_id": pid, "underlying": pos.get("underlying"), "reason": dec,
            })
            return 0.0

        # Cost to buy back the shorts minus what the wings recovered.
        net_exit = (
            sum(exits.get(leg["code"], 0.0) for leg in legs if leg["side"] == "SELL")
            - sum(exits.get(leg["code"], 0.0) for leg in legs if leg["side"] != "SELL")
        )
        realized_per_spread = credit - net_exit
        realized_usd = realized_per_spread * _CONTRACT_MULTIPLIER * qty

        self._store.set_position_status(
            pid, "CLOSED",
            closed_at=now_et().isoformat(), close_reason=dec,
            realized_pnl_usd=realized_usd,
        )
        await self._alerter.send(_fmt_exit(
            pos, dec, realized_usd,
            realized_per_spread / credit * 100 if credit else 0.0,
        ))
        append_audit({
            "event": "options_position_closed",
            "position_id": pid,
            "underlying": pos.get("underlying"),
            "reason": dec,
            "realized_pnl_usd": realized_usd,
        })
        return 0.0

    async def _check_daily_breaker(self, today, unrealized_total: float) -> None:
        """Trip the daily-loss breaker once per day (the meta key IS the guard).

        Persisted in meta, not on the instance, so a restart cannot re-arm
        entries on a day the limit was already hit.
        """
        cfg = self._cfg
        realized_today = self._store.get_realized_pnl_on(today.isoformat())
        limit = -cfg.daily_loss_limit_pct / 100 * cfg.sizing_equity_usd

        if realized_today + unrealized_total > limit:
            return
        if self._store.get_meta(_BREAKER_META_KEY) == today.isoformat():
            return

        self._store.set_meta(_BREAKER_META_KEY, today.isoformat())
        await self._alerter.send(
            "<b>Options daily loss limit hit</b> — no new entries today"
        )
        append_audit({
            "event": "options_daily_breaker",
            "date": today.isoformat(),
            "realized_usd": realized_today,
            "unrealized_usd": unrealized_total,
        })
        _logger.warning(
            "options_daily_breaker",
            realized=realized_today, unrealized=unrealized_total, limit=limit,
        )

    # --------------------------------------------------------
    # EOD
    # --------------------------------------------------------

    async def _job_eod(self) -> None:
        """Telegram summary + HTML report for the session."""
        try:
            today = now_et().date()
            if not is_trading_day(today):
                _logger.info("options_eod_skipped", reason="not_trading_day")
                return

            open_positions = self._store.get_option_positions(("OPEN",))
            # ponytail: Python-side filter on the closed_at prefix instead of a
            # new SQL helper. Ceiling: the CLOSED table is read whole — add a
            # get_closed_on() to OptionsStore if it ever passes a few thousand rows.
            closed_today = [
                p for p in self._store.get_option_positions(("CLOSED",))
                if str(p.get("closed_at") or "").startswith(today.isoformat())
            ]
            realized = self._store.get_realized_pnl_on(today.isoformat())

            await self._alerter.send(
                _fmt_summary(open_positions, closed_today, realized)
            )

            # write_reports' own mkdir is not recursive, so a nested report_dir
            # ("reports/options") has to exist before the call.
            os.makedirs(self._cfg.report_dir, exist_ok=True)
            write_reports(
                _options_html(open_positions, closed_today, today.isoformat()),
                today.isoformat(),
                report_dir=self._cfg.report_dir,
            )
            _logger.info("options_eod_done", date=today.isoformat())

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_eod_error", exc_info=True)

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


# ============================================================
# Process entry point
# ============================================================

def main(rules_path: str) -> None:
    """Compose the options bot and run it under asyncio.run.

    Construction order:
      1. configure_logging() — first, before any component logs
      2. load_options_config(rules_path) — ConfigError → stderr + sys.exit(1)
      3. MoomooGateway(get_gateway_config()) — no initial_stop_pct (equity-only knob)
      4. OptionsStore(cfg.state_db).open() — its OWN db file (D6)
      5. TelegramAlerter from env (never log the token — Pitfall 4)
      6. KillSwitch on cfg.kill_file — the options bot's OWN sentinel (D6)
      7. OptionsBot, then OpenDWatchdog (needs the bot ref) injected after
      8. asyncio.run(bot.run())
    """
    configure_logging()
    _log = get_logger(__name__)

    try:
        cfg = load_options_config(rules_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    gateway = MoomooGateway(get_gateway_config())

    os.makedirs(os.path.dirname(cfg.state_db) or ".", exist_ok=True)
    store = OptionsStore(cfg.state_db).open()

    alerter = TelegramAlerter(
        token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        logger=_log,
    )

    kill_switch = KillSwitch(sentinel_path=cfg.kill_file)

    bot = OptionsBot(
        cfg=cfg,
        gateway=gateway,
        store=store,
        kill_switch=kill_switch,
        alerter=alerter,
        watchdog=None,   # set below — the watchdog needs the bot reference
    )
    bot._watchdog = OpenDWatchdog(
        gateway=gateway,
        bot=bot,
        alerter=alerter,
        cfg=cfg,
    )

    asyncio.run(bot.run())
