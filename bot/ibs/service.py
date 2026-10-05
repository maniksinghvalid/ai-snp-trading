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

Exports: IbsBot, main
"""
import asyncio
import html
import math
import os
import signal
import sqlite3
import sys
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from bot.config.loader import ConfigError
from bot.gateway.gateway import MoomooGateway, get_gateway_config
from bot.ibs.config import load_ibs_config
from bot.ibs.execution import IbsExecutor
from bot.ibs.store import ACTIVE_STATUSES, IbsStore
from bot.ibs.strategy import (
    decide_entries, decide_exits, parse_snapshot, size_position, trading_days_held,
)
from bot.safety.audit_log import append_audit
from bot.safety.et_helpers import now_et
from bot.safety.kill_switch import KillSwitch
from bot.safety.logger import configure_logging, get_logger
from bot.scanner.calendar import get_market_close_et, is_trading_day, trading_days_between
from bot.service.alerter import TelegramAlerter
from bot.service.report import write_reports
from bot.service.watchdog import OpenDWatchdog

_logger = get_logger(__name__)

_ET = ZoneInfo("America/New_York")
_RET_OK = 0
_DECISION_META_KEY = "ibs_decision_date"
# moomoo order_status values after which an order can no longer fill (same set as
# bot.execution.engine._TERMINAL_ORDER_STATUSES, which bot/ibs must not import).
_TERMINAL_ORDER_STATUSES = frozenset({
    "FILLED_ALL", "CANCELLED_ALL", "CANCELLED_PART", "FAILED", "DELETED", "EXPIRED",
})


# ============================================================
# Helpers
# ============================================================

class OrderNotPlaced(Exception):
    """_work failed before any order reached the broker: nothing is exposed (CR-01)."""


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


def _marks(rows) -> dict:
    """{code: last_price} for finite positive prices — a report mark only.

    Deliberately NOT the decision-grade parse_snapshot gate: a stale mark on an
    EOD report is harmless, a missing one just renders "n/a".
    """
    out = {}
    for row in rows:
        try:
            price = float(row.get("last_price"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            out[str(row.get("code"))] = price
    return out


def _fmt_eod(strategy_name, date_str, open_rows, marks, trades_today,
             realized, unrealized) -> str:
    """Telegram body for the EOD summary (every field escaped, T-12-06)."""
    lines = [
        f"<b>IBS EOD</b> {_esc(strategy_name)} {_esc(date_str)}",
        f"open {_esc(len(open_rows))} | closed today {_esc(len(trades_today))} "
        f"| realized ${_esc(_signed(realized))} | unrealized ${_esc(_signed(unrealized))}",
    ]
    for r in open_rows:
        mark = marks.get(r["code"])
        line = (f"{_esc(r['code'])} {_esc(r['qty'])} @ {_esc(r.get('entry_price'))} "
                f"since {_esc(r.get('entry_date'))} mark "
                f"{_esc(mark if mark is not None else 'n/a')} {_esc(r.get('status'))}")
        if r.get("exit_pending"):
            line += " exit pending"
        lines.append(line)
    return "\n".join(lines)


def _ibs_html(strategy_name, date_str, open_rows, marks, trades_today,
              realized, unrealized) -> str:
    """Self-contained EOD HTML document: no JS, no CDN, every cell escaped."""
    def _cells(values):
        return "".join(f"<td>{_esc(v)}</td>" for v in values)

    def _unreal(r):
        mark = marks.get(r["code"])
        if mark is None or r.get("entry_price") is None:
            return "n/a"
        return _signed((mark - float(r["entry_price"])) * int(r["qty"]))

    open_html = "".join(
        "<tr>" + _cells([
            r["code"], r["qty"], r.get("entry_price"), r.get("entry_date"),
            marks.get(r["code"], "n/a"), _unreal(r),
            f"{r.get('status')}{' (exit pending)' if r.get('exit_pending') else ''}",
        ]) + "</tr>"
        for r in open_rows
    ) or "<tr><td colspan='7'>none</td></tr>"

    closed_html = "".join(
        "<tr>" + _cells([
            t["code"], t["qty"], t.get("entry_price"), t.get("exit_price"),
            t.get("reason"), _signed(t.get("pnl_usd") or 0.0),
        ]) + "</tr>"
        for t in trades_today
    ) or "<tr><td colspan='6'>none</td></tr>"

    return (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
        f"<title>IBS - {_esc(date_str)}</title>\n"
        "<style>\n"
        "  body { font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }\n"
        "  h1, h2 { color: #58a6ff; }\n"
        "  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }\n"
        "  th { background: #161b22; text-align: left; padding: 6px 12px; }\n"
        "  td { padding: 4px 12px; border-bottom: 1px solid #21262d; }\n"
        "</style></head><body>\n"
        f"<h1>{_esc(strategy_name)} - {_esc(date_str)}</h1>\n"
        f"<p>open {_esc(len(open_rows))} | closed today {_esc(len(trades_today))} | "
        f"realized ${_esc(_signed(realized))} | unrealized ${_esc(_signed(unrealized))}</p>\n"
        "<h2>Open</h2><table><tr><th>Code</th><th>Qty</th><th>Entry</th><th>Entry date</th>"
        f"<th>Mark</th><th>Unrealized</th><th>Status</th></tr>{open_html}</table>\n"
        "<h2>Closed today</h2><table><tr><th>Code</th><th>Qty</th><th>Entry</th><th>Exit</th>"
        f"<th>Reason</th><th>P&amp;L</th></tr>{closed_html}</table>\n"
        "</body></html>\n"
    )


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

        # D-12: a universe holding with no active row is never traded or adopted.
        # WR-07: it may be ours (a BUY recorded as not placed / unfilled that did
        # reach OpenD, or an orphan re-price order), so alert once per code per session.
        active = {r["code"] for r in self._store.get_positions(ACTIVE_STATUSES)}
        external = [c for c in self._cfg.universe if broker.get(c, 0) != 0 and c not in active]
        _logger.info("ibs_reconcile_external_ignored", count=len(external), codes=external)
        day = now_et().date().isoformat()
        for code in external:
            key = f"ibs_unmanaged_alerted:{code}"
            if self._store.get_meta(key) == day:
                continue
            self._store.set_meta(key, day)
            await self._alerter.send(
                f"<b>IBS unmanaged holding</b> {_esc(code)} — broker holds "
                f"{_esc(broker[code])} with no IBS row; not traded by this bot. If it is "
                f"not yours elsewhere, check moomoo for a position/order.")
        return broker

    # --------------------------------------------------------
    # Daily decision job
    # --------------------------------------------------------

    def _deadline(self, day) -> datetime:
        """Executor deadline: executor_margin_s BEFORE the hard-cancel sweep (CR-02), so
        the executor's own cancel and settle finish before the sweep can race them."""
        return (_close_dt(day) - timedelta(minutes=self._cfg.hard_cancel_before_close_min)
                - timedelta(seconds=self._cfg.executor_margin_s))

    async def _job_decide(self) -> None:
        """Run the once-per-day decision (D-07: idempotent across restarts / double fires)."""
        now = now_et()
        today = now.date()
        reason = None
        if not is_trading_day(today):
            reason = "not_trading_day"
        elif self._kill_switch.triggered:
            reason = "kill_switch"
        elif self._store.get_meta(_DECISION_META_KEY) == today.isoformat():
            reason = "already_decided"
        elif now >= self._deadline(today):
            # Ruling 8: defence in depth for a misfired job inside the grace window.
            reason = "past_deadline"
        elif not self._entries_enabled:
            # WR-02: the watchdog can lag a reconnect by minutes. If OpenD is back,
            # run the exits anyway; _decide keeps entries blocked.
            state = await self._gateway.get_global_state()
            if state.get("connected"):
                _logger.warning("ibs_decision_exits_only", date=today.isoformat())
                await self._alerter.send(
                    f"<b>IBS entries blocked</b> {_esc(today)} — OpenD reconnect pending; "
                    f"exits only today.")
            else:
                reason = "entries_disabled"
        if reason is not None:
            _logger.info("ibs_decision_skipped", reason=reason, date=today.isoformat())
            if reason not in ("not_trading_day", "already_decided"):
                await self._alerter.send(
                    f"<b>IBS decision skipped</b> {_esc(today)} — {_esc(reason)}; "
                    f"no exits or entries today.")
            return

        # D-07: the meta key is written BEFORE any broker call (cleared again if the
        # read-only front half fails before any order, WR-01).
        self._store.set_meta(_DECISION_META_KEY, today.isoformat())
        self._decision_task = asyncio.create_task(self._decide(today, self._deadline(today)))
        try:
            await self._decision_task
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_decision_error", exc_info=True, date=today.isoformat())
            append_audit({"event": "ibs_decision_error", "date": today.isoformat()})
            # Never interpolate exception or broker text into an alert (T-12-06).
            await self._alerter.send(
                f"<b>IBS decision error</b> {_esc(today)} — see logs/ibs.log"
            )
        finally:
            self._decision_task = None

    async def _read_front(self, deadline):
        """WR-01: reconcile + ONE batched snapshot, retried on a transient failure.

        Read-only, so a retry is safe; bounded by decision_read_retries and by the
        deadline. Returns the snapshot data; the last failure propagates.
        """
        cfg = self._cfg
        for attempt in range(cfg.decision_read_retries + 1):
            try:
                await self.reconcile()
                ret, data = await self._gateway.get_market_snapshot(list(cfg.universe))
                if ret != _RET_OK:
                    raise RuntimeError("ibs snapshot failed")
                return data
            except Exception:
                if (attempt >= cfg.decision_read_retries
                        or (deadline - now_et()).total_seconds() <= cfg.decision_read_retry_s):
                    raise
                _logger.warning("ibs_decision_read_retry", attempt=attempt + 1, exc_info=True)
                await asyncio.sleep(cfg.decision_read_retry_s)

    async def _decide(self, today, deadline):
        """reconcile -> ONE batched snapshot -> exits -> entries (D-04: entries strictly after exits)."""
        try:
            data = await self._read_front(deadline)
        except Exception:
            # WR-01: no order was attempted, so the failure does not consume the day.
            self._store.set_meta(_DECISION_META_KEY, "")
            raise
        quotes, skipped = parse_snapshot(_rows(data), now_et(), self._cfg.max_snapshot_age_s)
        for code, why in skipped.items():
            _logger.info("ibs_snapshot_skipped", code=code, reason=why)
        _logger.info("ibs_snapshot_parsed", valid=len(quotes), skipped=len(skipped))
        exited = await self._run_exits(today, deadline, quotes)
        if self._kill_switch.triggered or not self._entries_enabled:
            _logger.info("ibs_entry_skipped",
                         reason="kill_switch" if self._kill_switch.triggered else "entries_disabled")
        else:
            await self._run_entries(today, deadline, quotes, exited)
        return quotes, exited

    async def _work(self, side, row, qty, last, deadline):
        """Work one order via IbsExecutor, recording every placed order id (T-12-03d).

        Raises OrderNotPlaced when it failed before any order was placed (e.g.
        place_order raised: rate limit, buying power) — nothing is exposed.
        An exception after placement (deadline TimeoutError, unconfirmed cancel) is
        settled from the broker: fill_leg cancels its own order before raising, so
        when the last order reads terminal it returns like an unfilled / partial
        result (CR-02). Otherwise the state is unknown: the exception propagates
        and the rows stay WORKING for the hard-cancel sweep.
        """
        placed = []

        async def on_placed(order_id):
            placed.append(str(order_id))
            self._store.insert_order({
                "order_id": str(order_id), "position_id": row["position_id"],
                "code": row["code"], "side": side, "qty": int(qty), "status": "WORKING",
                "session_date": now_et().date().isoformat(),
                "created_at": now_et().isoformat(),
            })

        try:
            result = await self._executor.work(
                side, row["code"], qty, last, deadline, on_placed=on_placed)
        except Exception as exc:
            if not placed:
                raise OrderNotPlaced(f"{side} {row['code']}: no order placed") from exc
            # Earlier attempts were confirmed dead by fill_leg's TTL path; only the
            # last order can still be live.
            outcome = await self._order_outcome(placed[-1])
            if outcome is None:
                raise
            _logger.warning("ibs_order_settled_after_error", code=row["code"], side=side,
                            order_id=placed[-1], filled_qty=outcome[2], exc_info=True)
            result = outcome if outcome[2] > 0 else None
        if result is not None and not (math.isfinite(float(result[1])) and float(result[1]) > 0):
            # WR-04: _poll reports 0.0 when dealt_avg_price is missing; book the
            # first-attempt limit instead so P&L is never computed from 0.
            buf = (self._cfg.entry_limit_buffer_usd if side == "BUY"
                   else -self._cfg.exit_limit_buffer_usd)
            limit = round(float(last) + buf, 2)
            _logger.warning("ibs_fill_price_missing", code=row["code"], side=side,
                            order_id=result[0], reported=result[1], used=limit)
            result = (result[0], limit, result[2])
        ids = self._store.close_working_orders(
            row["position_id"], "DONE" if result else "CANCELLED")
        append_audit({"event": "ibs_order_done", "position_id": row["position_id"],
                      "code": row["code"], "side": side, "order_ids": ids,
                      "filled_qty": result[2] if result else 0})
        return result

    async def _order_outcome(self, order_id):
        """(order_id, avg_price, dealt_qty) if the broker reports order_id terminal,
        else None (unreadable or still working: state unknown)."""
        try:
            rows = await self._gateway.get_order_status(order_id)
        except Exception:
            _logger.error("ibs_order_status_unreadable", order_id=order_id, exc_info=True)
            return None
        for r in rows or []:
            if (str(r.get("order_id")) == str(order_id)
                    and str(r.get("order_status")) in _TERMINAL_ORDER_STATUSES):
                return (order_id, float(r.get("dealt_avg_price") or 0.0),
                        int(float(r.get("dealt_qty") or 0)))
        return None

    async def _flag_unknown(self, pid, code, what) -> None:
        """An order was placed and its outcome is unknown: NEEDS_ATTENTION (D-11).

        The DB write comes first so a second cancellation during the alert
        cannot leave the row OPENING/CLOSING.
        """
        _logger.error(f"ibs_{what}_unknown", code=code, exc_info=True)
        self._store.set_position_status(pid, "NEEDS_ATTENTION")
        append_audit({"event": f"ibs_{what}_unknown", "position_id": pid, "code": code})
        await self._alerter.send(
            f"<b>IBS NEEDS ATTENTION</b> {_esc(code)} — {_esc(what)} order state unknown; "
            f"cancel any working order for it in moomoo, then reconcile manually.")

    async def _run_exits(self, today, deadline, quotes) -> set:
        """D-04: work rule-decided exits sequentially (Pitfall 7); returns attempted codes.

        D-06: nothing here force-closes — only decide_exits output is sold.
        """
        rows = self._store.get_positions(("OPEN",))  # D-11: NEEDS_ATTENTION never traded
        if not rows:
            return set()
        by_code = {r["code"]: r for r in rows}
        entry = {c: datetime.strptime(r["entry_date"], "%Y-%m-%d").date()
                 for c, r in by_code.items()}
        sessions = trading_days_between(min(entry.values()), today)
        held = {c: trading_days_held(d, today, sessions) for c, d in entry.items()}
        pending = {c for c, r in by_code.items() if r.get("exit_pending")}
        ibs = {c: q["ibs"] for c, q in quotes.items()}
        exits = decide_exits(list(by_code), ibs, held, pending, self._cfg)

        for code, reason in exits:
            row = by_code[code]
            pid = row["position_id"]
            self._store.mark_exit_pending(pid, reason, today.isoformat())
            if self._kill_switch.triggered:
                break  # pending persists; retried next session
            quote = quotes.get(code)
            if quote is None:
                await self._alerter.send(
                    f"<b>IBS exit deferred</b> {_esc(code)} — no valid quote; "
                    f"retried next session.")
                continue
            if (deadline - now_et()).total_seconds() < self._cfg.worst_case_order_s:
                await self._alerter.send(
                    f"<b>IBS exit deferred</b> {_esc(code)} — no time left; "
                    f"retried next session.")
                continue
            shown = row.get("exit_reason") or reason
            qty = int(row["qty"])
            self._store.set_position_status(pid, "CLOSING")
            try:
                result = await self._work("SELL", row, qty, quote["last"], deadline)
            except OrderNotPlaced:
                _logger.error("ibs_exit_not_placed", code=code, exc_info=True)
                self._store.set_position_status(pid, "OPEN")  # exit_pending kept
                append_audit({"event": "ibs_exit_not_placed", "position_id": pid, "code": code})
                await self._alerter.send(
                    f"<b>IBS exit not placed</b> {_esc(code)} — no order reached the "
                    f"broker; retried next session.")
                continue
            except BaseException as exc:
                await self._flag_unknown(pid, code, "exit")
                if not isinstance(exc, Exception):
                    raise  # CancelledError: row flagged first
                continue

            filled = int(result[2]) if result else 0
            if filled <= 0:
                self._store.set_position_status(pid, "OPEN")
                await self._alerter.send(
                    f"<b>IBS exit unfilled</b> {_esc(code)} — retried next session.")
                continue
            avg = float(result[1])
            remaining = self._store.record_exit_fill(
                pid, uuid4().hex, filled, avg, today.isoformat(), now_et().isoformat())
            if remaining > 0:
                await self._alerter.send(
                    f"<b>IBS exit partial</b> {_esc(code)} {_esc(filled)}/{_esc(qty)} "
                    f"filled; {_esc(remaining)} retried next session.")
            else:
                pnl = (avg - float(row["entry_price"] or 0.0)) * filled
                await self._alerter.send(
                    f"<b>IBS exit</b> {_esc(code)} SELL {_esc(filled)} @ {_esc(avg)} "
                    f"({_esc(shown)}) P&amp;L ${_esc(_signed(pnl))}")
        return {code for code, _ in exits}


    async def _run_entries(self, today, deadline, quotes, exited) -> None:
        """D-03/D-04/D-05/D-09/D-12: enter the lowest-IBS eligible codes into free slots.

        Runs after the exit batch with a FRESH broker read (260926-kvt analog): a
        universe code the broker holds with no active row of ours is external and is
        never entered; an unreadable broker skips ALL entries (fail closed). Orders are
        worked sequentially (Pitfall 7).
        """
        try:
            broker = await self._broker_shares()
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_entries_broker_unreadable", exc_info=True)
            await self._alerter.send(
                f"<b>IBS entries skipped</b> {_esc(today)} — broker positions unreadable.")
            return

        active = self._store.get_active_positions()
        active_codes = {r["code"] for r in active}
        external = {c for c in self._cfg.universe
                    if broker.get(c, 0) != 0 and c not in active_codes}
        _logger.info("ibs_external_holdings", count=len(external), codes=sorted(external))
        working = {o["code"] for o in self._store.get_orders(("WORKING",))}

        # Ruling 3: a code exited this session is excluded; D-04: slots from post-exit rows.
        excluded = active_codes | set(exited) | external | working
        free = self._cfg.max_concurrent_positions - len(active)
        ibs = {c: q["ibs"] for c, q in quotes.items()}
        entries = decide_entries(ibs, excluded, free, self._cfg, self._cfg.universe)

        for code in entries:
            if self._kill_switch.triggered or not self._entries_enabled:
                _logger.info("ibs_entry_skipped", code=code, reason="halted")
                break
            last = quotes[code]["last"]
            limit = round(last + self._cfg.entry_limit_buffer_usd, 2)
            qty = size_position(limit, self._cfg)
            if qty < 1:
                _logger.info("ibs_entry_skipped", code=code, reason="qty_lt_1")
                continue
            if (deadline - now_et()).total_seconds() < self._cfg.worst_case_order_s:
                _logger.info("ibs_entry_skipped", code=code, reason="no_time")
                break
            row = {"position_id": uuid4().hex, "code": code, "qty": qty,
                   "entry_date": today.isoformat(), "status": "OPENING",
                   "opened_at": now_et().isoformat()}
            try:
                self._store.insert_position(row)  # before the BUY (D-05)
            except sqlite3.IntegrityError:
                _logger.info("ibs_entry_skipped", code=code, reason="already_active")
                continue
            pid = row["position_id"]
            try:
                result = await self._work("BUY", row, qty, last, deadline)
            except OrderNotPlaced:
                _logger.error("ibs_entry_not_placed", code=code, exc_info=True)
                self._store.set_position_status(
                    pid, "ABORTED", closed_at=now_et().isoformat(),
                    close_reason="entry_place_failed")
                append_audit({"event": "ibs_entry_not_placed", "position_id": pid, "code": code})
                await self._alerter.send(
                    f"<b>IBS entry not placed</b> {_esc(code)} — no order confirmed at the "
                    f"broker; skipped today; check moomoo for a position/order.")
                continue
            except BaseException as exc:
                await self._flag_unknown(pid, code, "entry")
                if not isinstance(exc, Exception):
                    raise  # CancelledError: row flagged first
                continue
            if result is None:  # D-09: unfilled entry is skipped today, no carry-over
                self._store.set_position_status(
                    pid, "ABORTED", closed_at=now_et().isoformat(), close_reason="entry_unfilled")
                _logger.info("ibs_entry_skipped", code=code, reason="unfilled")
                continue
            oid, avg, filled = result
            self._store.mark_opened(pid, int(filled), float(avg), str(oid))
            msg = (f"<b>IBS entry</b> {_esc(code)} BUY {_esc(filled)} @ {_esc(avg)} "
                   f"(IBS {_esc(f'{ibs[code]:.2f}')})")
            if int(filled) < qty:
                msg += f" — partial {_esc(filled)}/{_esc(qty)}"
            await self._alerter.send(msg)

    # --------------------------------------------------------
    # Hard-cancel sweep (D-09)
    # --------------------------------------------------------

    async def _sweep_orders(self, reason) -> int:
        """Cancel every WORKING ibs_orders row (any session); returns rows processed.

        D-09 primary mechanism; the DAY time-in-force at the broker is only the
        backstop (Pitfall 9). The broker status is read first (WR-03): an order that
        is already terminal (fill_leg cancelled it, or it filled) is marked without a
        cancel call. A cancel that still fails is marked CANCEL_FAILED and alerted.
        """
        n = 0
        for row in self._store.get_orders(("WORKING",)):
            oid, code = row["order_id"], row["code"]
            n += 1
            outcome = await self._order_outcome(oid)
            if outcome is not None:
                status = "DONE" if outcome[2] > 0 else "CANCELLED"
                self._store.set_order_status(oid, status)
                append_audit({"event": "ibs_order_already_closed", "order_id": oid,
                              "code": code, "status": status, "reason": reason})
                continue
            try:
                await self._gateway.cancel_order(oid)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error("ibs_order_cancel_failed", order_id=oid, code=code, exc_info=True)
                self._store.set_order_status(oid, "CANCEL_FAILED")
                append_audit({"event": "ibs_order_cancel_failed", "order_id": oid,
                              "code": code, "reason": reason})
                await self._alerter.send(
                    f"<b>IBS cancel FAILED</b> order {_esc(oid)} {_esc(code)} — "
                    f"cancel it in moomoo before the close.")
                continue
            self._store.set_order_status(oid, "CANCELLED")
            append_audit({"event": "ibs_order_cancel", "order_id": oid,
                          "code": code, "reason": reason})
        return n

    async def _job_hard_cancel(self) -> None:
        """close - 1 min: cancel an in-flight decision, flag rows it left mid-order,
        then cancel every WORKING order (D-09)."""
        today = now_et().date()
        if not is_trading_day(today):
            _logger.info("ibs_hard_cancel_skipped", reason="not_trading_day")
            return
        try:
            task = self._decision_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                _logger.warning("ibs_decision_cancelled", date=today.isoformat())
                append_audit({"event": "ibs_decision_cancelled", "date": today.isoformat()})
            # CR-02: nothing is mid-order now; a row still OPENING/CLOSING is unknown.
            for row in self._store.get_positions(("OPENING", "CLOSING")):
                await self._flag_unknown(row["position_id"], row["code"],
                                         "entry" if row["status"] == "OPENING" else "exit")
            n = await self._sweep_orders("hard_cancel")
            _logger.info("ibs_hard_cancel_done", cancelled=n)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_hard_cancel_error", exc_info=True)

    # --------------------------------------------------------
    # Job registration + daily arming (D-07, ruling 8)
    # --------------------------------------------------------

    def _register_jobs(self) -> None:
        """Register the daily ibs_arm cron. D-06: no force-close job, ever."""
        hour, minute = _parse_hhmm(self._cfg.arm_time_et)
        self._scheduler.add_job(
            self._job_arm, CronTrigger(hour=hour, minute=minute, timezone=_ET),
            id="ibs_arm", coalesce=True, max_instances=1,
            misfire_grace_time=self._cfg.misfire_grace_s, replace_existing=True,
        )
        _logger.info("ibs_jobs_registered", job_ids=["ibs_arm"])

    def arm_today(self) -> list:
        """Arm today's one-shot decide / hard-cancel / EOD jobs from the NYSE close.

        Must run AFTER scheduler.start(): a pending job is not deduplicated by
        replace_existing. DateTriggers (not cron) because half-day closes move
        the times. A slot already in the past is skipped, so a mid-day restart
        after the decision time makes no decision today. There is deliberately
        no force-close job (D-06). Returns the armed (job_id, run_at) pairs.
        """
        now = now_et()
        today = now.date()
        if not is_trading_day(today):
            _logger.info("ibs_arm_skipped", reason="not_trading_day", date=today.isoformat())
            return []
        cfg = self._cfg
        close = _close_dt(today)
        slots = (
            ("ibs_decide", self._job_decide, close - timedelta(minutes=cfg.decision_before_close_min)),
            ("ibs_hard_cancel", self._job_hard_cancel, close - timedelta(minutes=cfg.hard_cancel_before_close_min)),
            ("ibs_eod", self._job_eod, close + timedelta(minutes=cfg.eod_report_after_close_min)),
        )
        armed = []
        for job_id, fn, run_at in slots:
            if run_at <= now:
                _logger.info("ibs_job_slot_passed", job_id=job_id, run_at=run_at.isoformat())
                continue
            self._scheduler.add_job(
                fn, DateTrigger(run_date=run_at, timezone=_ET), id=job_id,
                coalesce=True, max_instances=1,
                misfire_grace_time=cfg.misfire_grace_s, replace_existing=True,
            )
            armed.append((job_id, run_at))
        _logger.info("ibs_jobs_armed", date=today.isoformat(),
                     jobs={j: t.isoformat() for j, t in armed})
        return armed

    async def _arm_and_alert(self) -> None:
        """arm_today, then alert when today's decide slot had already passed (WR-02)."""
        armed = self.arm_today()
        today = now_et().date()
        if (is_trading_day(today) and "ibs_decide" not in {j for j, _ in armed}
                and self._store.get_meta(_DECISION_META_KEY) != today.isoformat()):
            await self._alerter.send(
                f"<b>IBS decision missed</b> {_esc(today)} — started after the decision "
                f"time; no exits or entries today.")

    async def _job_arm(self) -> None:
        """Daily cron body (async so APScheduler runs it on the loop)."""
        try:
            await self._arm_and_alert()
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_arm_error", exc_info=True)

    # --------------------------------------------------------
    # EOD (D-14)
    # --------------------------------------------------------

    async def _job_eod(self) -> None:
        """close + 5 min: reconcile, one snapshot for marks, Telegram + HTML report."""
        try:
            today = now_et().date()
            if not is_trading_day(today):
                _logger.info("ibs_eod_skipped", reason="not_trading_day")
                return
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error("ibs_eod_reconcile_error", exc_info=True)

            marks = {}
            try:
                ret, data = await self._gateway.get_market_snapshot(list(self._cfg.universe))
                if ret == _RET_OK:
                    marks = _marks(_rows(data))
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error("ibs_eod_snapshot_error", exc_info=True)

            day = today.isoformat()
            open_rows = self._store.get_positions(("OPEN", "NEEDS_ATTENTION"))
            unrealized = sum(
                (marks[r["code"]] - float(r["entry_price"])) * int(r["qty"])
                for r in open_rows
                if r["code"] in marks and r.get("entry_price") is not None
            )
            trades = self._store.get_trades_on(day)
            realized = self._store.get_realized_pnl_on(day)
            name = self._cfg.strategy_name

            await self._alerter.send(
                _fmt_eod(name, day, open_rows, marks, trades, realized, unrealized))
            # write_reports' own mkdir is not recursive ("reports/ibs").
            os.makedirs(self._cfg.report_dir, exist_ok=True)
            write_reports(
                _ibs_html(name, day, open_rows, marks, trades, realized, unrealized),
                day, report_dir=self._cfg.report_dir)
            _logger.info("ibs_eod_done", date=day)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_eod_error", exc_info=True)

    # --------------------------------------------------------
    # Shutdown + run loop (D-14, ruling 8)
    # --------------------------------------------------------

    async def _shutdown(self) -> None:
        """Kill-switch shutdown, ruling-8 order; each step isolated from the next.

        cancel in-flight decision -> sweep WORKING orders -> gateway.close() ->
        "stopped" alert -> scheduler shutdown. Positions are left held (D-06,
        D-14); every store write already committed synchronously.
        """
        _logger.info("ibs_shutdown_start")
        try:
            append_audit({"event": "ibs_bot_shutdown", "reason": "kill_switch"})
        except Exception:
            pass  # audit write must never block shutdown

        # (1) Cancel the decision first so LegExecutor's shielded cancel runs while
        # the gateway is still open.
        task = self._decision_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self._sweep_orders("shutdown")  # (2)
        except Exception:
            _logger.error("ibs_shutdown_sweep_error", exc_info=True)
        try:
            result = self._gateway.close()  # (3)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _logger.warning("ibs_gateway_close_error", exc_info=True)
        try:
            await self._alerter.send("<b>IBS bot stopped</b>")  # (4)
        except Exception:
            pass
        try:
            self._scheduler.shutdown(wait=False)  # (5) never-started scheduler raises
        except Exception:
            pass
        _logger.info("ibs_shutdown_complete")

    async def run(self) -> None:
        """Run the IBS lifecycle until the kill switch trips (sentinel, SIGINT or SIGTERM)."""
        # WR-05: launchctl unload / kill send SIGTERM; take the same graceful path as
        # the kill file (cancel the decision, sweep orders, alert). SIGINT is KillSwitch's.
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGTERM, self._kill_switch.trigger, "SIGTERM")
        try:
            await self._readiness_gate()
            if not self._alerter._enabled:
                _logger.warning(
                    "alerter_disabled_at_startup",
                    reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured")
            self._register_jobs()
            self._scheduler.start()
            await self._arm_and_alert()  # after start (ruling 8): pending jobs are not deduplicated
            _logger.info("ibs_bot_started")

            if self._watchdog is not None:
                self._watchdog_task = asyncio.create_task(self._watchdog.run())

            while not self._kill_switch.triggered:
                if self._kill_switch.check_file():
                    self._kill_switch.trigger("sentinel_file")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_bot_run_error", exc_info=True)
            raise
        finally:
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self._shutdown()


# ============================================================
# Process entry point
# ============================================================

def main(rules_path: str) -> None:
    """Compose the IBS bot and run it under asyncio.run.

    Construction order:
      1. load_ibs_config(rules_path) — ConfigError -> stderr + sys.exit(1)
      2. configure_logging(log_name=cfg.log_file, force=True) — bot.main already
         configured bot.log before the dispatch peek (D-13)
      3. MoomooGateway(get_gateway_config())
      4. IbsStore(cfg.state_db).open() — its OWN db file
      5. TelegramAlerter from env (never log the token)
      6. KillSwitch on cfg.kill_file — the IBS bot's OWN sentinel
      7. IbsBot, then OpenDWatchdog (needs the bot ref) injected after
      8. asyncio.run(bot.run())
    """
    try:
        cfg = load_ibs_config(rules_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    configure_logging(log_name=cfg.log_file, force=True)
    _log = get_logger(__name__)

    gateway = MoomooGateway(get_gateway_config())

    os.makedirs(os.path.dirname(cfg.state_db) or ".", exist_ok=True)
    store = IbsStore(cfg.state_db).open()

    alerter = TelegramAlerter(
        token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        logger=_log,
    )
    kill_switch = KillSwitch(sentinel_path=cfg.kill_file)

    bot = IbsBot(cfg=cfg, gateway=gateway, store=store, kill_switch=kill_switch,
                 alerter=alerter, watchdog=None)
    bot._watchdog = OpenDWatchdog(gateway=gateway, bot=bot, alerter=alerter, cfg=cfg)

    asyncio.run(bot.run())
