#!/usr/bin/env python3
"""
bot.options.service — OptionsBot: the always-on options process (Phase 8, D4/D5/D6).

Composes the Phase 8/11 parts into a bot that trades unattended: an AsyncIOScheduler
(America/New_York) drives one entry-scan job per configured strategy
(options_entry_scan_<name>[_2]) plus ONE manage job and ONE EOD report job, around a
hard startup readiness gate (connect → reconcile → enable entries) and a graceful
kill-switch shutdown. Per-strategy entries/day and concurrent-position caps are
enforced per scanning strategy; the daily-loss breaker, BP headroom and the
one-position-per-underlying rule stay global across every strategy (D-22). The
ONE manage job dispatches each open position to ITS OWN strategy's config and
decision function (manage_decision_debit for a bull_call_spread position,
manage_decision for a credit structure) by pos["strategy_name"] (D-21); a
position whose strategy has been removed from rules_options.json is failed
closed to NEEDS_ATTENTION at startup reconcile, never silently managed with
another strategy's parameters (D-29). main() composes ONE OptionsBot for
every strategy in the loaded book (load_options_book); Telegram alerts and the
EOD report name the strategy (D-24).

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
import math
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
from bot.options.config import load_options_book
from bot.options.execution import LegExecutor
from bot.options.store import OptionsStore
from bot.options.strategy import (
    manage_decision,
    manage_decision_debit,
    mark_spread,
    option_dte,
    passes_entry_gate,
    pick_expiry,
    pick_strikes,
    size_debit_position,
    size_position,
)
from bot.options.universe import read_equity_watchlist
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

# Appended to every alert that hands legs to the operator mid-order (CR-03).
# fill_leg cancels its own order on an exception, but a failed cancel (OpenD
# down, or the gateway already closed at shutdown) can leave it working.
_WORKING_ORDERS_HINT = "cancel any working orders on these codes in moomoo before closing manually."

# Inside the assignment-guard window, only this many CONSECUTIVE manage cycles
# with an unusable per-position quote hand a position to the operator (WR-06).
# At the 5-minute manage interval that is 10 minutes after the first counted
# miss, which rides out the 09:35 price-discovery gap. Not a strategy knob, so
# deliberately not a rules_options.json key (same as _MANAGE_START_ET).
_QUOTE_MISS_ESCALATE_CYCLES = 3

# A leg quote may drive a mark or decision outside the assignment-guard window
# only if ask - bid <= max(0.5 x mid, $0.10) (WR-07). This is 10x the shipped
# entry gate's 5%-of-mid and 2x its $0.05, because it rejects ABSURD quotes,
# not illiquid-but-real ones. With bid 0 the bound reduces to ask <= $0.10, so
# every quote with an ask above a dime must be two-sided. A sub-dime one-sided
# far-OTM wing moves the mid by at most $0.05. Not rules_options.json keys
# (not strategy knobs).
_MARK_MAX_SPREAD_FRAC_OF_MID = 0.5
_MARK_MAX_SPREAD_FLOOR_USD = 0.10


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


def _change_pct(row: dict):
    """Return the underlying's day change in PERCENT, or None.

    VERIFIED LIVE (2026-08-17, scripts/uat_options_probe.py): the screen's
    underlying change_ratio is a FRACTION (0.01392 = +1.39%), same convention
    as iv_rank. x100 once, here.
    """
    value = row.get("u_change_ratio")
    if value in (None, "N/A"):
        return None
    try:
        return float(value) * 100
    except (TypeError, ValueError):
        return None


def _quote_ok(q) -> bool:
    """True only for a usable two-sided numeric quote (CR-01).

    strategy._as_float maps the SDK's literal 'N/A' (and None, '', NaN) to
    0.0. That is correct for ENTRY gating, where a 0.0 mid fails closed
    (leg_is_liquid rejects bid <= 0). But when MARKING an open spread, the
    same coercion fabricates a $0 leg instead of skipping the cycle — a
    single unquoted leg must never be marked, decided on, or closed against.
    This is the base validity check. It is used ALONE only inside the
    assignment-guard window: there manage_decision and manage_decision_debit
    return "assignment_guard" whatever the mark, so a legitimately one-sided
    far-OTM wing (0.00/0.05) must never block the aggressive close. Every
    other decision goes through _quote_markable (WR-07).
    """
    if not q:
        return False
    try:
        bid, ask = float(q.get("bid")), float(q.get("ask"))
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return False
    return ask > 0 and bid >= 0 and ask >= bid


def _quote_markable(q) -> bool:
    """True only for a quote narrow enough to drive a mark or decision
    outside the assignment-guard window (WR-07).

    A one-sided (bid 0) or absurdly wide quote fabricates a mid. The
    review's 0.10/9.00 long -> mid 4.55 -> a false profit_target -> the
    short is bought back and the long never fills -> a naked long plus
    NEEDS_ATTENTION. A wide short ask inflates the mark, which can falsely
    trip the global daily-loss breaker.
    """
    if not _quote_ok(q):
        return False
    bid, ask = float(q.get("bid")), float(q.get("ask"))
    mid = (bid + ask) / 2
    return ask - bid <= max(_MARK_MAX_SPREAD_FRAC_OF_MID * mid, _MARK_MAX_SPREAD_FLOOR_USD) + 1e-9


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


def _manage_cutoff(day) -> datetime:
    """The last instant the manage job may run on `day` (ET)."""
    hour, minute = _parse_hhmm(get_market_close_et(day))
    return datetime.combine(
        day, _time(hour, minute), tzinfo=_ET,
    ) - timedelta(minutes=_MANAGE_CLOSE_BUFFER_MIN)


def _esc(value) -> str:
    """HTML-escape any value for interpolation into an alert body (T-1ie-02)."""
    return html.escape(str(value))


def _signed(value) -> str:
    """Format a dollar amount with an explicit +/- sign."""
    return f"{float(value):+,.2f}"


def _premium_label(credit_per_spread) -> str:
    """Format a signed net premium for display (D-19/D-24).

    Positive (a credit structure) -> "credit X"; negative (a debit structure,
    e.g. bull_call_spread) -> "debit X" (the sign is flipped back to a
    human-readable positive dollar amount for the debit case).
    """
    value = float(credit_per_spread)
    return f"credit {round(value, 2)}" if value >= 0 else f"debit {round(-value, 2)}"


def _fmt_entry(pos: dict, legs) -> str:
    """Telegram body for a filled spread entry (every field escaped)."""
    ivr = pos.get("ivr_at_entry")
    lines = [
        f"<b>Options entry</b> {_esc(pos.get('underlying'))} — "
        f"{_esc(pos.get('strategy_name') or '')}",
        f"{_esc(pos.get('structure'))} exp {_esc(pos.get('expiry'))} "
        f"({_esc(pos.get('dte_at_entry'))} DTE)",
        f"IVR {'n/a' if ivr is None else _esc(round(float(ivr), 1))} "
        f"| qty {_esc(pos.get('qty'))}",
        f"{_esc(_premium_label(pos.get('credit_per_spread') or 0))} "
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


def _fmt_exit(pos: dict, reason, pnl_usd, pct_of_credit, basis="credit") -> str:
    """Telegram body for a closed spread (every field escaped).

    basis names the denominator of pct_of_credit: "credit" for a credit
    structure (unchanged), "max profit" for a debit structure (a negative
    credit_per_spread makes %-of-credit meaningless — D-19).
    """
    return (
        f"<b>Options exit</b> {_esc(pos.get('underlying'))} — "
        f"{_esc(pos.get('strategy_name') or '')}\n"
        f"reason {_esc(reason)}\n"
        f"realized ${_esc(_signed(pnl_usd))} "
        f"({_esc(round(float(pct_of_credit), 1))}% of {_esc(basis)})"
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
            f"{_esc(pos.get('strategy_name') or '')} "
            f"{_esc(pos.get('underlying'))} {_esc(pos.get('structure'))} "
            f"exp {_esc(pos.get('expiry'))} qty {_esc(pos.get('qty'))} "
            f"{_esc(_premium_label(pos.get('credit_per_spread') or 0))}"
        )
    return "\n".join(lines)


def _options_html(open_positions, closed_today, date_str: str) -> str:
    """Build the self-contained EOD HTML document (no JS, no CDN — D-15 parity)."""
    def _cells(values):
        return "".join(f"<td>{_esc(v)}</td>" for v in values)

    open_rows = "".join(
        "<tr>" + _cells([
            p.get("strategy_name"), p.get("underlying"), p.get("structure"),
            p.get("expiry"), p.get("qty"), p.get("credit_per_spread"),
            p.get("max_loss_usd"),
        ]) + "</tr>"
        for p in open_positions
    ) or "<tr><td colspan='7'>none</td></tr>"

    closed_rows = "".join(
        "<tr>" + _cells([
            p.get("strategy_name"), p.get("underlying"), p.get("close_reason"),
            p.get("realized_pnl_usd"),
        ]) + "</tr>"
        for p in closed_today
    ) or "<tr><td colspan='4'>none</td></tr>"

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
        "<h2>Open</h2><table><tr><th>Strategy</th><th>Underlying</th><th>Structure</th>"
        f"<th>Expiry</th><th>Qty</th><th>Credit</th><th>Max loss</th></tr>{open_rows}</table>\n"
        "<h2>Closed today</h2><table><tr><th>Strategy</th><th>Underlying</th>"
        f"<th>Reason</th><th>Realized</th></tr>{closed_rows}</table>\n"
        "</body></html>\n"
    )


# ============================================================
# OptionsBot
# ============================================================

class OptionsBot:
    """Always-on APScheduler-driven options process (D4/D5/D6).

    cfg:         OptionsConfig — the PRIMARY strategy (the first one in the book).
                 Shared execution/service/risk values are identical across every
                 flattened OptionsConfig by construction (D-02), so LegExecutor,
                 the watchdog, the daily-loss breaker and EOD all keep reading
                 from this one config regardless of how many strategies scan.
    gateway:     MoomooGateway — connect/close/screen/snapshot/positions/orders.
    store:       OptionsStore — open handle on the options DB (its OWN file).
    kill_switch: KillSwitch — install + check_file + triggered.
    alerter:     TelegramAlerter — fire-and-forget send() coroutine.
    watchdog:    OpenDWatchdog or None — injected after construction (needs bot ref).
    strategies:  optional iterable of every OptionsConfig this process should
                 run an entry scan for (typically an OptionsBook.strategies
                 tuple). Defaults to (cfg,) — a single-strategy bot, unchanged
                 behavior. Looked up by name in self._strategies (D-20).
    """

    def __init__(
        self, cfg, gateway, store, kill_switch, alerter, watchdog=None, strategies=None,
    ) -> None:
        self._cfg = cfg
        self._gateway = gateway
        self._store = store
        self._kill_switch = kill_switch
        self._alerter = alerter
        self._watchdog = watchdog
        self._watchdog_task = None

        self._strategies = {c.name: c for c in (strategies or (cfg,))}

        self._scheduler = AsyncIOScheduler(timezone=_ET)
        self._entries_enabled: bool = False
        # One lock serialises entry vs manage so the shared order_list_query
        # budget (10 req / 30s) is never contended (T-1ie-03).
        self._lock = asyncio.Lock()
        self._executor = LegExecutor(gateway, cfg)
        # Shared code -> stock_id cache across every strategy this process
        # scans (a code is a code regardless of which strategy's universe it
        # came from, so one cache is correct and saves a repeat gateway call).
        self._stock_ids: dict = {}
        # position_id -> (ET session date, consecutive counted invalid-quote
        # manage cycles inside the guard window that session) (WR-06, IN-08).
        # A new session starts the count over. In memory by design: a restart
        # starts the count over, which costs at most N-1 extra cycles.
        # ponytail: an entry of a row that left OPEN through reconcile stays as
        # a stale tuple, bounded by the positions one process ever holds. Prune
        # it in _manage_once if that ever matters.
        self._quote_miss_streak: dict = {}
        # position_ids already sent the one-time expiry-day warning (WR-10).
        # In memory, so a restart re-arms it: at most one extra alert.
        self._expiry_warned: set = set()
        # Consecutive manage cycles with any failed snapshot chunk (WR-11).
        # It is process health, not a per-position streak, and a clean cycle
        # resets it.
        self._snapshot_outage_cycles: int = 0

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

        On startup ONLY, a row whose strategy_name is not in the loaded book
        (D-29) is also flagged NEEDS_ATTENTION and alerted — a strategy that
        was removed from rules_options.json must never leave its positions
        silently unmanaged, and the manage loop must never apply another
        strategy's parameters to it. The same startup check also flags a row
        whose structure KIND (debit bull_call_spread vs any credit structure)
        no longer matches its named strategy's configured kind (WR-01,
        D-29 completion) — an operator who renames a strategy's structure.type
        must never leave a pre-existing position decided with the other
        kind's config. This check runs before the OPENING/CLOSING branch so
        either case is reported as such even if it also died mid-order.

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

            strat = self._strategies.get(pos.get("strategy_name"))
            if strat is None:
                event = "options_unknown_strategy"
            # ponytail: kind comparison, not exact-type equality — iron_condor
            # and put_credit_spread share manage_decision and identical config
            # fields; strict equality is the upgrade path if that ever changes.
            elif (pos.get("structure") == "bull_call_spread") != (
                strat.structure_type == "bull_call_spread"
            ):
                event = "options_strategy_structure_mismatch"
            else:
                event = None

            if startup and event is not None:
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                if event == "options_unknown_strategy":
                    await self._alerter.send(
                        f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                        f"strategy {_esc(pos.get('strategy_name'))} is not configured in "
                        f"rules_options.json; close manually."
                    )
                else:
                    await self._alerter.send(
                        f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                        f"structure {_esc(pos.get('structure'))} does not match strategy "
                        f"{_esc(pos.get('strategy_name'))} (configured "
                        f"{_esc(strat.structure_type)}); close manually."
                    )
                append_audit({
                    "event": event,
                    "position_id": pid,
                    "underlying": pos.get("underlying"),
                    "strategy_name": pos.get("strategy_name"),
                    "status": status,
                    "structure": pos.get("structure"),
                    "configured_structure": strat.structure_type if strat is not None else None,
                })
                _logger.warning(
                    event,
                    position_id=pid, strategy=pos.get("strategy_name"),
                    structure=pos.get("structure"),
                )
                continue

            if startup and status in ("OPENING", "CLOSING"):
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                codes = [leg["code"] for leg in legs]
                await self._alerter.send(
                    f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                    f"restarted mid-{_esc(str(status).lower())} "
                    f"({_esc(', '.join(codes))}); {_WORKING_ORDERS_HINT}"
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
        return _MANAGE_START_ET <= now.time() and now < _manage_cutoff(today)

    # --------------------------------------------------------
    # Job registration
    # --------------------------------------------------------

    def _register_jobs(self) -> None:
        """Register per-strategy entry-scan job(s), ONE manage and ONE EOD job.

        Job ids (exact, D-20): options_entry_scan_<name> (and
        options_entry_scan_<name>_2 only when that strategy's
        second_entry_scan_et is set), options_manage, options_eod — the manage
        and EOD jobs stay singular regardless of how many strategies scan.
        Every timing value comes from rules_options.json (CFG-01).
        """
        common = dict(coalesce=True, max_instances=1, misfire_grace_time=_MISFIRE_GRACE_S)

        for strat in self._strategies.values():
            hour, minute = _parse_hhmm(strat.entry_scan_et)
            self._scheduler.add_job(
                self._job_entry_scan,
                CronTrigger(hour=hour, minute=minute, timezone=_ET),
                args=[strat.name],
                id=f"options_entry_scan_{strat.name}", **common,
            )

            if strat.second_entry_scan_et is not None:
                hour, minute = _parse_hhmm(strat.second_entry_scan_et)
                self._scheduler.add_job(
                    self._job_entry_scan,
                    CronTrigger(hour=hour, minute=minute, timezone=_ET),
                    args=[strat.name],
                    id=f"options_entry_scan_{strat.name}_2", **common,
                )

        self._scheduler.add_job(
            self._job_manage,
            IntervalTrigger(minutes=self._cfg.manage_interval_min, timezone=_ET),
            id="options_manage", **common,
        )

        hour, minute = _parse_hhmm(self._cfg.eod_report_et)
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

    async def _job_entry_scan(self, strategy_name=None) -> None:
        """Screen the chain once per right and open at most one spread per underlying."""
        try:
            cfg = self._cfg if strategy_name is None else self._strategies[strategy_name]
            today = now_et().date()

            if not is_trading_day(today):
                _logger.info(
                    "options_entry_scan_skipped", reason="not_trading_day", strategy=cfg.name,
                )
                return
            if not self._entries_enabled:
                _logger.info(
                    "options_entry_scan_skipped", reason="entries_disabled", strategy=cfg.name,
                )
                return
            if self._kill_switch.triggered:
                _logger.info(
                    "options_entry_scan_skipped", reason="kill_switch", strategy=cfg.name,
                )
                return
            # Realized-only check first so a restart (manage never ran) still trips it.
            # The breaker is GLOBAL (D-22): shared across every strategy.
            await self._check_daily_breaker(today, 0.0)
            if self._store.get_meta(_BREAKER_META_KEY) == today.isoformat():
                _logger.info(
                    "options_entry_scan_skipped", reason="daily_loss_breaker", strategy=cfg.name,
                )
                return

            # D-22: entries/day is a per-strategy cap.
            opened_today = self._store.count_opened_on(today.isoformat(), strategy_name=cfg.name)
            if opened_today >= cfg.max_new_positions_per_day:
                _logger.info(
                    "options_entry_scan_skipped", reason="per_day_cap", strategy=cfg.name,
                )
                return

            async with self._lock:
                await self._scan_and_open(cfg, today, opened_today)

        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_entry_scan_error", exc_info=True)

    async def _scan_and_open(self, cfg, today, opened_today: int) -> None:
        """Screen, select and open — the locked body of the entry scan."""
        if cfg.universe_source is None:
            codes = list(cfg.universe)
        else:
            # D-17/D-28: the bull-call universe is the equity bot's premarket
            # watchlist, read cross-process from its own (read-only) DB.
            # ponytail: this is a sub-millisecond local read on the WAL equity
            # DB, called directly on the loop; move to run_in_executor if it
            # ever blocks.
            codes = read_equity_watchlist(cfg.equity_state_db, today.isoformat())
            _logger.info("options_watchlist_loaded", strategy=cfg.name, count=len(codes))

        if not codes:
            _logger.info(
                "options_entry_scan_skipped", reason="empty_universe", strategy=cfg.name,
            )
            return

        missing = [c for c in codes if c not in self._stock_ids]
        if missing:
            self._stock_ids.update(await self._gateway.get_stock_ids(missing) or {})
        stock_ids = {c: self._stock_ids[c] for c in codes if c in self._stock_ids}
        if not stock_ids:
            _logger.warning("options_entry_scan_no_stock_ids", strategy=cfg.name)
            return
        by_id = {sid: code for code, sid in stock_ids.items()}
        ids = list(stock_ids.values())

        if cfg.structure_type == "bull_call_spread":
            # Calls only (D-23); the delta bounds here are screen BREADTH, not
            # a strategy threshold — the real knob is cfg.long_delta, which
            # pick_strikes applies to whatever the screen returns. Same
            # per-underlying screen + throttle discipline as the credit path
            # (the gateway handles the 1000-row cap per underlying).
            rows = await self._gateway.screen_options(
                ids, "C", cfg.min_dte, cfg.max_dte, 0.05, 0.50,
            )
        else:
            # The delta bounds here are screen BREADTH, not a strategy
            # threshold — the real knob is cfg.short_delta, which pick_strikes
            # applies to whatever the screen returns.
            rows = await self._gateway.screen_options(
                ids, "P", cfg.min_dte, cfg.max_dte, -0.35, -0.03,
            )
            if cfg.structure_type == "iron_condor":
                rows = list(rows or []) + list(await self._gateway.screen_options(
                    ids, "C", cfg.min_dte, cfg.max_dte, 0.03, 0.35,
                ) or [])

        # busy / open_max_loss_total stay GLOBAL across every strategy (D-22):
        # one-position-per-underlying and BP headroom bind the whole account.
        active = self._store.get_option_positions(_ACTIVE_STATUSES)
        busy = {p["underlying"] for p in active}
        # Every ACTIVE row (OPENING/OPEN/CLOSING/NEEDS_ATTENTION) counts, not
        # just OPEN/OPENING — a stuck row is still live broker exposure
        # (WR-05). Over-counting a row the operator already closed is the
        # fail-closed side; the operator clears it by resolving the row.
        open_max_loss_total = sum(float(p["max_loss_usd"] or 0) for p in active)
        # open_count is the scanning strategy's OWN concurrent-position count
        # (D-22 per-strategy cap) — a Python-side filter over the already-
        # fetched active list, not a new store query (research Open Question 1).
        # Same ACTIVE-row scope as open_max_loss_total above (WR-05): a stuck
        # position still occupies this strategy's concurrent slot.
        open_count = sum(1 for p in active if p.get("strategy_name") == cfg.name)

        grouped = _group_rows_by_underlying(_rows(rows))
        if cfg.universe_source is None:
            # tasty behavior unchanged: sorted by stock id, exactly as before.
            candidates = [
                (by_id[sid], u_rows) for sid, u_rows in sorted(grouped.items())
                if sid in by_id
            ]
        else:
            # Watchlist rank order: the scanner's best-ranked names get the
            # per-day slots first.
            candidates = [
                (code, grouped[stock_ids[code]]) for code in codes
                if code in stock_ids and stock_ids[code] in grouped
            ]

        for code, u_rows in candidates:
            if (opened_today >= cfg.max_new_positions_per_day
                    or open_count >= cfg.max_concurrent_positions):
                break
            if code in busy:
                continue

            try:
                pos = await self._try_open(cfg, code, u_rows, today, open_max_loss_total)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One malformed chain must never abort the rest of the scan.
                _logger.error(
                    "options_entry_underlying_error",
                    underlying=code, strategy=cfg.name, exc_info=True,
                )
                continue

            busy.add(code)
            if pos is not None:
                opened_today += 1
                open_count += 1
                open_max_loss_total += float(pos["max_loss_usd"])

    async def _try_open(self, cfg, code, u_rows, today, open_max_loss_total):
        """Evaluate one underlying and, if it qualifies, open the spread.

        Returns the inserted position dict on a filled open OR an incomplete
        unwind (CR-02, so the row's live exposure is counted by the current
        scan too), else None.
        """
        is_debit = cfg.structure_type == "bull_call_spread"
        head = u_rows[0]

        u = {
            "ivr_pct": _ivr_pct(head),
            "ivp_pct": _ivp_pct(head),
            "change_pct": _change_pct(head),   # fraction → percent (verified live)
        }
        # D-05: a bull call's entry gate is watchlist membership + the
        # debit/liquidity/DTE gates below — the IV-regime gate does not apply
        # to a debit structure (ivr_at_entry is still recorded when present).
        if not is_debit and not passes_entry_gate(u, cfg):
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

        if is_debit:
            debit = sel["debit"]
            qty = size_debit_position(debit, cfg, open_max_loss_total)
            if qty < 1:
                return None
            credit_per_spread = -debit                                    # D-19
            max_loss_usd = debit * _CONTRACT_MULTIPLIER * qty
        else:
            qty = size_position(sel["width"], sel["credit"], cfg, open_max_loss_total)
            if qty < 1:
                return None
            credit_per_spread = sel["credit"]
            max_loss_usd = (sel["width"] - sel["credit"]) * _CONTRACT_MULTIPLIER * qty

        position_id = uuid4().hex
        pos = {
            "position_id": position_id,
            "underlying": code,
            "structure": cfg.structure_type,
            "expiry": exp.isoformat(),
            "dte_at_entry": option_dte(exp, today),
            "ivr_at_entry": u["ivr_pct"],
            "credit_per_spread": credit_per_spread,
            "width": sel["width"],
            "qty": qty,
            "max_loss_usd": max_loss_usd,
            "status": "OPENING",
            "opened_at": now_et().isoformat(),
            "strategy_name": cfg.name,
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

        if filled is False:
            # The unwind itself left legs open on the broker — this is LIVE
            # exposure, not a clean abort. NEEDS_ATTENTION (never ABORTED),
            # no closed_at/close_reason/realized_pnl_usd, so
            # get_realized_pnl_on never books it (MSO-07).
            self._store.set_position_status(position_id, "NEEDS_ATTENTION")
            codes = [leg["code"] for leg in sel["legs"]]
            await self._alerter.send(
                f"<b>Options NEEDS ATTENTION</b> {_esc(code)} — {_esc(cfg.name)} entry "
                f"failed; UNWIND INCOMPLETE — legs still open ({_esc(', '.join(codes))}); "
                f"{_WORKING_ORDERS_HINT}"
            )
            append_audit({
                "event": "options_entry_unwind_incomplete",
                "position_id": position_id, "underlying": code,
                "strategy_name": cfg.name, "codes": codes,
            })
            _logger.warning(
                "options_entry_unwind_incomplete",
                position_id=position_id, underlying=code, strategy=cfg.name, codes=codes,
            )
            # ponytail: the leg rows (all inserted before the first order) are
            # the operator's record of which codes to check; the broker holds
            # the quantities. Recording exact per-leg remainders is WR-08's scope.
            # Returning pos makes _scan_and_open count this row against
            # opened_today, open_count and open_max_loss_total for the rest of
            # THIS scan, exactly as every later scan counts it through
            # _ACTIVE_STATUSES (D-22).
            return pos

        self._store.set_position_status(position_id, "OPEN")
        await self._alerter.send(_fmt_entry(pos, sel["legs"]))
        append_audit({
            "event": "options_position_opened",
            "position_id": position_id,
            "underlying": code,
            "structure": pos["structure"],
            "expiry": pos["expiry"],
            "qty": qty,
            "credit_per_spread": pos["credit_per_spread"],
            "max_loss_usd": pos["max_loss_usd"],
            "strategy_name": cfg.name,
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
        """Reconcile, snapshot, then mark/close each open position (locked body).

        Counts consecutive snapshot-outage cycles and alerts once at
        _QUOTE_MISS_ESCALATE_CYCLES (WR-11); a clean cycle resets the counter.
        """
        await self.reconcile()

        positions = self._store.get_option_positions(("OPEN",))
        if not positions:
            # Empty book: still arm the breaker on realized-only losses so a bad
            # morning cannot be followed by a fresh afternoon entry.
            await self._check_daily_breaker(today, 0.0)
            return

        codes = sorted({leg["code"] for p in positions for leg in p["legs"]})
        quotes = {}
        unsnapped = set()
        for chunk in _chunks(codes, _SNAPSHOT_CHUNK):
            ret, data = await self._gateway.get_market_snapshot(chunk)
            if ret != _RET_OK:
                _logger.warning("options_snapshot_failed", codes=len(chunk))
                unsnapped.update(chunk)
                continue
            for row in _rows(data):
                quotes[row["code"]] = {
                    "bid": row.get("bid_price"), "ask": row.get("ask_price"),
                }

        # Process-level: a persistent snapshot outage while OpenD stays
        # connected is otherwise invisible. OpenDWatchdog only polls
        # get_global_state (connection/login) and cannot see a quote-rights
        # error or a whole-batch rejection while connected (corrects 11-08's
        # T-11-46 rationale). The `==` fires the alert once per episode; a
        # clean cycle resets and re-arms it. Outages still never touch the
        # per-position streak (operator scope) — that is WR-10's expiry
        # warning with reason "snapshot_outage" below.
        if not unsnapped:
            self._snapshot_outage_cycles = 0
        else:
            self._snapshot_outage_cycles += 1
            if self._snapshot_outage_cycles == _QUOTE_MISS_ESCALATE_CYCLES:
                affected = sum(
                    1 for p in positions
                    if any(leg["code"] in unsnapped for leg in p["legs"])
                )
                await self._alerter.send(
                    f"<b>Options snapshot outage</b> — option quote snapshot failing "
                    f"for {_esc(self._snapshot_outage_cycles)} consecutive manage "
                    f"cycles; {_esc(affected)} open position(s) unmanaged (no exits, "
                    f"no assignment guard). Check OpenD option quote rights and the "
                    f"options log."
                )
                append_audit({
                    "event": "options_snapshot_outage_alert",
                    "cycles": self._snapshot_outage_cycles,
                    "codes": len(unsnapped),
                    "positions": affected,
                })
                _logger.error(
                    "options_snapshot_outage_alert",
                    cycles=self._snapshot_outage_cycles, codes=len(unsnapped),
                    positions=affected,
                )

        unrealized_total = 0.0
        for pos in positions:
            outage = [leg["code"] for leg in pos["legs"] if leg["code"] in unsnapped]
            if outage:
                # A whole-chunk snapshot failure is never a per-position quote
                # miss: it neither grows nor resets the WR-06 streak. On expiry
                # day the position still surfaces through the one-time expiry
                # warning (WR-10/WR-11).
                _logger.warning(
                    "options_manage_snapshot_outage",
                    position_id=pos["position_id"], codes=outage,
                )
                if option_dte(date.fromisoformat(pos["expiry"]), today) <= 0:
                    await self._warn_expiry_unmanaged(pos, outage, "snapshot_outage", today)
                continue
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
        """Mark one position and close it if ITS OWN strategy says so (D-21).

        Dispatches on pos["strategy_name"] to that strategy's config and
        decision function — manage_decision_debit for a bull_call_spread
        position, manage_decision for a credit structure — never self._cfg,
        so a mixed book always applies the right parameters to each position.
        A strategy_name not present in the loaded book (already flagged
        NEEDS_ATTENTION by the D-29 startup reconcile guard) is skipped here
        too, defense in depth against ever managing an orphaned position. A
        position whose structure KIND no longer matches its strategy's
        configured kind (WR-01) is skipped the same way, before the quote
        gate below, so it is never escalated with the wrong strategy's
        assignment_guard_dte.

        Any leg without a two-sided numeric quote (CR-01) is never marked,
        decided on, or closed against — this returns 0.0 (no action). Inside
        the assignment-guard window (dte <= the strategy's assignment_guard_dte)
        an unusable quote escalates to NEEDS_ATTENTION with an alert (Q-01)
        only after _QUOTE_MISS_ESCALATE_CYCLES consecutive counted cycles, or
        on the expiry session's final cycle; below that it logs a retry and
        does nothing else (WR-06) — a position about to expire must never be
        silently skipped forever, but one transient bad cycle must never
        cancel the automated close either. The alert fires once, because a
        NEEDS_ATTENTION row leaves the OPEN-only manage loop. A snapshot
        outage is skipped in _manage_once and never counts toward the streak.
        A close_legs exception always ends NEEDS_ATTENTION, never CLOSING.

        Per-call-site quote gate (WR-07): outside the guard window,
        _quote_markable gates mark_spread (breaker P&L), manage_decision /
        manage_decision_debit, and the non-aggressive close_legs those
        decisions trigger. Inside the window, the looser _quote_ok gates the
        assignment_guard close_legs(aggressive=True) path — there the mark
        cannot change the decision and is never returned as P&L.

        On expiry day (dte <= 0), a counted miss below the escalation
        threshold also sends the one-time expiry warning (WR-10), so a
        dropped final cycle cannot let the position expire unannounced. The
        streak itself is scoped to the ET session (IN-08).

        Returns its unrealized P&L in dollars (0.0 once a close is attempted).
        """
        pid = pos["position_id"]
        legs = pos["legs"]

        strat_cfg = self._strategies.get(pos.get("strategy_name"))
        if strat_cfg is None:
            _logger.warning(
                "options_manage_unknown_strategy",
                position_id=pid, strategy=pos.get("strategy_name"),
            )
            return 0.0

        is_debit = pos["structure"] == "bull_call_spread"
        if is_debit != (strat_cfg.structure_type == "bull_call_spread"):
            _logger.warning(
                "options_manage_structure_mismatch",
                position_id=pid, strategy=pos.get("strategy_name"),
                structure=pos["structure"], configured_structure=strat_cfg.structure_type,
            )
            return 0.0

        dte = option_dte(date.fromisoformat(pos["expiry"]), today)
        in_guard = dte <= strat_cfg.assignment_guard_dte
        gate = _quote_ok if in_guard else _quote_markable

        bad = [leg["code"] for leg in legs if not gate(quotes.get(leg["code"]))]
        if not bad:
            self._quote_miss_streak.pop(pid, None)
        if bad:
            if in_guard:
                day, prev = self._quote_miss_streak.get(pid, (today, 0))
                streak = (prev if day == today else 0) + 1
                self._quote_miss_streak[pid] = (today, streak)
                # The last automated cycle before the contract expires (WR-06):
                # ponytail: a cycle delayed by the lock can read as final one
                # cycle early, which fails toward the human; the upgrade path
                # is the scheduler's next_run_time.
                final_cycle = (
                    dte <= 0
                    and now_et() + timedelta(minutes=self._cfg.manage_interval_min)
                    >= _manage_cutoff(today)
                )
                if streak < _QUOTE_MISS_ESCALATE_CYCLES and not final_cycle:
                    _logger.warning(
                        "options_manage_unquotable_near_expiry_retry",
                        position_id=pid, codes=bad, dte=dte, streak=streak,
                    )
                    if dte <= 0:
                        await self._warn_expiry_unmanaged(pos, bad, "no_quote", today)
                    return 0.0
                self._quote_miss_streak.pop(pid, None)
                self._store.set_position_status(pid, "NEEDS_ATTENTION")
                await self._alerter.send(
                    f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                    f"no usable quote for {_esc(', '.join(bad))} at {_esc(dte)} DTE "
                    f"after {_esc(streak)} manage cycle(s) (assignment-guard window); "
                    f"close manually."
                )
                append_audit({
                    "event": "options_manage_unquotable_near_expiry",
                    "position_id": pid,
                    "underlying": pos.get("underlying"),
                    "strategy_name": pos.get("strategy_name"),
                    "codes": bad,
                    "dte": dte,
                    "streak": streak,
                    "final_cycle": final_cycle,
                })
                _logger.warning(
                    "options_manage_unquotable_near_expiry",
                    position_id=pid, codes=bad, dte=dte,
                    streak=streak, final_cycle=final_cycle,
                )
            else:
                _logger.warning("options_manage_missing_quote", position_id=pid, codes=bad)
            return 0.0

        mark = mark_spread(legs, quotes)
        credit = float(pos["credit_per_spread"])
        qty = int(pos["qty"])

        if is_debit:
            width = float(pos["width"] or 0)
            dec = manage_decision_debit(mark, -credit, width, dte, strat_cfg)  # D-19
        else:
            dec = manage_decision(mark, credit, dte, strat_cfg)
        if dec is None:
            return (credit - mark) * _CONTRACT_MULTIPLIER * qty

        self._store.set_position_status(pid, "CLOSING")
        exits = {}

        async def _on_exit_placed(leg, order_id):
            self._store.set_leg_exit(leg["leg_id"], order_id=order_id, status="CLOSING")

        async def _on_exit_filled(leg, order_id, price, filled_qty):
            exits[leg["code"]] = price
            self._store.set_leg_exit(leg["leg_id"], price=price, status="CLOSED")

        try:
            ok = await self._executor.close_legs(
                legs, quotes, aggressive=(dec == "assignment_guard"),
                on_leg_placed=_on_exit_placed, on_leg_filled=_on_exit_filled,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error("options_close_error", position_id=pid, reason=dec, exc_info=True)
            ok = False

        if not ok:
            self._store.set_position_status(pid, "NEEDS_ATTENTION")
            await self._alerter.send(
                f"<b>Options NEEDS ATTENTION</b> {_esc(pos.get('underlying'))} — "
                f"close incomplete ({_esc(', '.join(leg['code'] for leg in legs))}) — "
                f"check the account; {_WORKING_ORDERS_HINT}"
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
        if is_debit:
            max_profit = width + credit  # == width - debit (credit = -debit, D-19)
            pct = realized_per_spread / max_profit * 100 if max_profit > 0 else 0.0
            basis = "max profit"
        else:
            pct = realized_per_spread / credit * 100 if credit else 0.0
            basis = "credit"
        await self._alerter.send(_fmt_exit(pos, dec, realized_usd, pct, basis=basis))
        append_audit({
            "event": "options_position_closed",
            "position_id": pid,
            "underlying": pos.get("underlying"),
            "reason": dec,
            "realized_pnl_usd": realized_usd,
            "strategy_name": pos.get("strategy_name"),
        })
        return 0.0

    async def _warn_expiry_unmanaged(self, pos, codes, reason, today) -> None:
        """One-time heads-up that an expiry-day position could not be managed
        this cycle (WR-10/WR-11); no status change, and the automated close
        stays armed."""
        pid = pos["position_id"]
        if pid in self._expiry_warned:
            return
        self._expiry_warned.add(pid)

        cutoff = _manage_cutoff(today).strftime("%H:%M")
        await self._alerter.send(
            f"<b>Options expiry warning</b> {_esc(pos.get('underlying'))} — "
            f"{_esc(pos.get('strategy_name') or '')} position expires today and "
            f"could not be managed ({_esc(reason)}: {_esc(', '.join(codes))}). "
            f"The bot keeps retrying until {_esc(cutoff)} ET; if it is still open "
            f"then, close it manually."
        )
        append_audit({
            "event": "options_expiry_day_unmanaged",
            "position_id": pid,
            "underlying": pos.get("underlying"),
            "strategy_name": pos.get("strategy_name"),
            "codes": codes,
            "reason": reason,
        })
        _logger.warning(
            "options_expiry_day_unmanaged", position_id=pid, codes=codes, reason=reason,
        )

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
      2. load_options_book(rules_path) — every configured strategy; the first
         carries the shared execution/service/risk values — ConfigError →
         stderr + sys.exit(1)
      3. MoomooGateway(get_gateway_config()) — no initial_stop_pct (equity-only knob)
      4. OptionsStore(cfg.state_db).open() — its OWN db file (D6)
      5. TelegramAlerter from env (never log the token — Pitfall 4)
      6. KillSwitch on cfg.kill_file — the options bot's OWN sentinel (D6)
      7. OptionsBot (every strategy in the book), then OpenDWatchdog (needs
         the bot ref) injected after
      8. asyncio.run(bot.run())
    """
    configure_logging()
    _log = get_logger(__name__)

    try:
        book = load_options_book(rules_path)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    cfg = book.strategies[0]

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
        strategies=book.strategies,
    )
    bot._watchdog = OpenDWatchdog(
        gateway=gateway,
        bot=bot,
        alerter=alerter,
        cfg=cfg,
    )

    asyncio.run(bot.run())
