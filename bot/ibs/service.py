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
import sqlite3
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.ibs.execution import IbsExecutor
from bot.ibs.store import ACTIVE_STATUSES
from bot.ibs.strategy import (
    decide_entries, decide_exits, parse_snapshot, size_position, trading_days_held,
)
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

    # --------------------------------------------------------
    # Daily decision job
    # --------------------------------------------------------

    def _deadline(self, day) -> datetime:
        """Hard-cancel time: no order is worked past close - hard_cancel_before_close_min."""
        return _close_dt(day) - timedelta(minutes=self._cfg.hard_cancel_before_close_min)

    async def _job_decide(self) -> None:
        """Run the once-per-day decision (D-07: idempotent across restarts / double fires)."""
        now = now_et()
        today = now.date()
        reason = None
        if not is_trading_day(today):
            reason = "not_trading_day"
        elif not self._entries_enabled:
            reason = "entries_disabled"
        elif self._kill_switch.triggered:
            reason = "kill_switch"
        elif self._store.get_meta(_DECISION_META_KEY) == today.isoformat():
            reason = "already_decided"
        elif now >= self._deadline(today):
            # Ruling 8: defence in depth for a misfired job inside the grace window.
            reason = "past_deadline"
        if reason is not None:
            _logger.info("ibs_decision_skipped", reason=reason, date=today.isoformat())
            return

        # D-07: the meta key is written BEFORE any broker call.
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

    async def _decide(self, today, deadline):
        """reconcile -> ONE batched snapshot -> exits -> entries (D-04: entries strictly after exits)."""
        await self.reconcile()
        ret, data = await self._gateway.get_market_snapshot(list(self._cfg.universe))
        if ret != _RET_OK:
            raise RuntimeError("ibs snapshot failed")
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

        Exceptions propagate: their orders stay WORKING for the hard-cancel sweep.
        """
        async def on_placed(order_id):
            self._store.insert_order({
                "order_id": str(order_id), "position_id": row["position_id"],
                "code": row["code"], "side": side, "qty": int(qty), "status": "WORKING",
                "session_date": now_et().date().isoformat(),
                "created_at": now_et().isoformat(),
            })

        result = await self._executor.work(
            side, row["code"], qty, last, deadline, on_placed=on_placed)
        ids = self._store.close_working_orders(row["position_id"])
        append_audit({"event": "ibs_order_done", "position_id": row["position_id"],
                      "code": row["code"], "side": side, "order_ids": ids,
                      "filled_qty": result[2] if result else 0})
        return result

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
            except Exception:
                _logger.error("ibs_exit_unknown", code=code, exc_info=True)
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                append_audit({"event": "ibs_exit_unknown", "position_id": pid, "code": code})
                await self._alerter.send(
                    f"<b>IBS NEEDS ATTENTION</b> {_esc(code)} — exit order state unknown; "
                    f"cancel any working order for it in moomoo, then reconcile manually.")
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
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.error("ibs_entry_unknown", code=code, exc_info=True)
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                append_audit({"event": "ibs_entry_unknown", "position_id": pid, "code": code})
                await self._alerter.send(
                    f"<b>IBS NEEDS ATTENTION</b> {_esc(code)} — entry order outcome "
                    f"unknown; check moomoo.")
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
        backstop (Pitfall 9). A cancel of an order that has just filled fails, which
        is the safe side: it is marked CANCEL_FAILED and alerted for manual review.
        """
        n = 0
        for row in self._store.get_orders(("WORKING",)):
            oid, code = row["order_id"], row["code"]
            n += 1
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
        """close - 1 min: cancel an in-flight decision, then every WORKING order (D-09)."""
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
            n = await self._sweep_orders("hard_cancel")
            _logger.info("ibs_hard_cancel_done", cancelled=n)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("ibs_hard_cancel_error", exc_info=True)
