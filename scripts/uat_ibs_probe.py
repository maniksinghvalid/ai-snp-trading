#!/usr/bin/env python3
"""
uat_ibs_probe.py — Phase 12 (ibs_etf_mean_reversion) live UAT against OpenD.

Default mode is READ-ONLY: it runs the exact production pure functions
(parse_snapshot / decide_exits / decide_entries / size_position) over one live
snapshot and prints what the bot WOULD do, plus the facts only a live payload
settles: per-code `update_time` age (calibrates signal.max_snapshot_age_s) and
ask - bid (calibrates the limit buffers). Tune rules_ibs.json, never code.

`--live-1lot` (operator-run, places PAPER orders on the shared SIMULATE account):
BUYs 1 share of --symbol then SELLs it back through IbsExecutor (full
TTL / re-price / cancel path) on a scratch IbsStore; prints fills and friction.
It refuses (exit 3, no order) when --symbol is outside the IBS universe, held at
the broker, on an active IBS DB row, or when it is inside the bot's decision
window. There is no IBS lock file (.bot_kill_ibs only means "stop"), so those
checks are what keep a running bot's share counts untouched.

The IBS DB is only ever opened read-only (sqlite mode=ro): no migrations, no
write lock on the file the running bot uses.

Usage:
  PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 \\
      python3 scripts/uat_ibs_probe.py [--rules rules_ibs.json]
  ... --live-1lot --symbol US.XLU --confirm     # buys + sells 1 share (paper)
"""
import argparse
import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config.loader import ConfigError  # noqa: E402
from bot.gateway.gateway import MoomooGateway, get_gateway_config  # noqa: E402
from bot.ibs.config import load_ibs_config  # noqa: E402
from bot.ibs.execution import IbsExecutor  # noqa: E402
from bot.ibs.store import ACTIVE_STATUSES, IbsStore  # noqa: E402
from bot.ibs.strategy import (  # noqa: E402
    decide_entries, decide_exits, parse_snapshot, size_position, trading_days_held,
)
from bot.safety.et_helpers import now_et  # noqa: E402
from bot.scanner.calendar import get_market_close_et, is_trading_day, trading_days_between  # noqa: E402


def _p(*a):
    print(*a, flush=True)


def _hr(title):
    _p("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _rows(data):
    """Gateway DataFrame (or list of dicts) -> list of dicts."""
    if hasattr(data, "iterrows"):
        return [r.to_dict() for _, r in data.iterrows()]
    return list(data or [])


def _age(update_time, now):
    try:
        ts = datetime.strptime(str(update_time)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
        return f"{(now - ts).total_seconds():.0f}"
    except ValueError:
        return "?"


def _spread(row):
    try:
        return f"{float(row['ask_price']) - float(row['bid_price']):.3f}"
    except (KeyError, TypeError, ValueError):
        return "-"


def _active_rows(state_db):
    """Active ibs_positions rows read with sqlite mode=ro, or None (with a printed
    note naming the absolute path) when there is no DB or it is unreadable.

    Never creates the DB, never runs migrations, never takes a write lock (WR-06).
    """
    path = Path(state_db).resolve()
    if not path.exists():
        _p(f"no IBS DB at {path}; DB guard not applied (run from the repo root)")
        return None
    try:
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            marks = ", ".join("?" * len(ACTIVE_STATUSES))
            return [dict(r) for r in con.execute(
                f"SELECT * FROM ibs_positions WHERE status IN ({marks}) ORDER BY rowid",
                ACTIVE_STATUSES)]
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:  # OperationalError is a subclass; also covers "file is not a database"  # IN-09: unmigrated / locked file
        _p(f"IBS DB at {path} unreadable ({exc}); DB guard not applied")
        return None


async def _refusal(cfg, gateway, symbol, now):
    """Why --live-1lot must not trade `symbol` now, or None (WR-06)."""
    if symbol not in cfg.universe:
        return f"{symbol} is not in the IBS universe"
    if is_trading_day(now.date()):
        hh, mm = map(int, get_market_close_et(now.date()).split(":"))
        close = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # each leg below may run 2 x worst_case_order_s; keep both legs out of the window
        start = close - timedelta(minutes=cfg.decision_before_close_min,
                                  seconds=4 * cfg.worst_case_order_s)
        if start <= now <= close:
            return "inside the IBS bot's decision window"
    ret, pos = await gateway.get_positions()
    if ret != 0:
        return "broker positions unreadable"
    if any(str(r.get("code")) == symbol and int(float(r.get("qty") or 0)) != 0
           for r in _rows(pos)):
        return f"{symbol} is held at the broker"
    rows = _active_rows(cfg.state_db)
    if rows is None and os.path.exists(cfg.state_db):
        return "IBS DB unreadable"  # IN-09: an existing DB we cannot check fails closed
    if any(r["code"] == symbol for r in rows or []):
        return f"{symbol} has an active IBS DB row"
    return None


async def probe(cfg, gateway, now):
    today = now.date()
    _hr(f"1. IBS table — {len(cfg.universe)} universe ETFs (one snapshot)")
    ret, snap = await gateway.get_market_snapshot(list(cfg.universe))
    if ret != 0:
        _p("!! snapshot failed:", snap)
        return
    rows = _rows(snap)
    by_code = {str(r.get("code")): r for r in rows}
    quotes, skipped = parse_snapshot(rows, now, cfg.max_snapshot_age_s)
    _p(f"{'code':<9}{'high':>9}{'low':>9}{'last':>9}{'IBS':>7}{'age_s':>7}{'ask-bid':>9}  status")
    for code in cfg.universe:
        r = by_code.get(code)
        if r is None:
            _p(f"{code:<9}{'-':>9}{'-':>9}{'-':>9}{'-':>7}{'-':>7}{'-':>9}  missing")
            continue
        q = quotes.get(code)
        ibs = f"{q['ibs']:.2f}" if q else "-"
        _p(f"{code:<9}{str(r.get('high_price')):>9}{str(r.get('low_price')):>9}{str(r.get('last_price')):>9}"
           f"{ibs:>7}{_age(r.get('update_time'), now):>7}{_spread(r):>9}  {'ok' if q else skipped.get(code, 'skipped')}")

    _hr("2. broker share holdings")
    ret, pos = await gateway.get_positions()
    broker = {}
    if ret == 0:
        for r in _rows(pos):
            qty = int(float(r.get("qty") or 0))
            if qty != 0:
                broker[str(r["code"])] = (qty, r.get("cost_price"))
    else:
        _p("!! positions query failed")

    _hr("3. IBS DB rows")
    active = _active_rows(cfg.state_db)
    if active is not None:
        for r in active:
            _p(f"  {r['code']} qty={r['qty']} {r['status']} entry={r['entry_date']} exit_pending={r['exit_pending']}")
        if not active:
            _p("  (no active rows)")
    else:
        active = []
    active_codes = {r["code"] for r in active}
    for code, (qty, cost) in broker.items():
        tag = "external" if code in cfg.universe and code not in active_codes else (
            "IBS row" if code in active_codes else "non-universe")
        _p(f"  broker {code} qty={qty} cost={cost}  [{tag}]")
    external = {c for c in broker if c in cfg.universe and c not in active_codes}

    _hr("4. what the bot WOULD do")
    if is_trading_day(today):
        close = datetime.strptime(get_market_close_et(today), "%H:%M")
        dec = close - timedelta(minutes=cfg.decision_before_close_min)
        _p(f"today is a trading day; close {close:%H:%M} ET -> decision job {dec:%H:%M} ET")
    else:
        _p("not a trading day")
    open_rows = [r for r in active if r["status"] == "OPEN"]
    ibs = {c: q["ibs"] for c, q in quotes.items()}
    would_exit = []
    if open_rows:
        entry = {r["code"]: datetime.strptime(r["entry_date"], "%Y-%m-%d").date() for r in open_rows}
        sessions = trading_days_between(min(entry.values()), today)
        held = {c: trading_days_held(d, today, sessions) for c, d in entry.items()}
        pending = {r["code"] for r in open_rows if r.get("exit_pending")}
        would_exit = decide_exits([r["code"] for r in open_rows], ibs, held, pending, cfg)
    for code, reason in would_exit:
        _p(f"WOULD EXIT {code} ({reason})")
    if not would_exit:
        _p("WOULD EXIT nothing")
    exiting = {c for c, _ in would_exit}
    free = cfg.max_concurrent_positions - (len(active) - len(exiting))
    entries = decide_entries(ibs, active_codes | exiting | external, free, cfg, cfg.universe)
    for code in entries:
        limit = round(quotes[code]["last"] + cfg.entry_limit_buffer_usd, 2)
        qty = size_position(limit, cfg)
        _p(f"WOULD ENTER {code} IBS {ibs[code]:.2f} limit {limit} qty {qty}"
           if qty >= 1 else f"WOULD ENTER {code}: skip: qty < 1 at limit {limit}")
    if not entries:
        _p(f"WOULD ENTER nothing (free slots {free})")


async def live_1lot(cfg, gateway, symbol, now):
    """Returns False when refused (nothing placed), else None."""
    _hr(f"LIVE 1-LOT round trip on {symbol} (PAPER)")
    reason = await _refusal(cfg, gateway, symbol, now)
    if reason is not None:
        _p(f"refusing: {reason} — no order placed")
        return False
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="uat_ibs_"), "ibs.db")
    store = IbsStore(tmpdb).open()
    try:
        async def last_price():
            ret, snap = await gateway.get_market_snapshot([symbol])
            quotes, skipped = parse_snapshot(_rows(snap) if ret == 0 else [], now_et(), cfg.max_snapshot_age_s)
            if symbol not in quotes:
                _p(f"refusing: {symbol} snapshot not valid ({skipped.get(symbol, 'missing')})")
                return None
            return quotes[symbol]["last"]

        last = await last_price()
        if last is None:
            return
        ex = IbsExecutor(gateway, cfg)
        pid = uuid4().hex
        today = now_et().date().isoformat()
        store.insert_position({"position_id": pid, "code": symbol, "qty": 1, "entry_date": today,
                               "status": "OPENING", "opened_at": now_et().isoformat()})

        def placer(side):
            async def on_placed(oid):
                _p(f"  placed {side} order {oid}")
                store.insert_order({"order_id": str(oid), "position_id": pid, "code": symbol, "side": side,
                                    "qty": 1, "status": "WORKING", "session_date": today,
                                    "created_at": now_et().isoformat()})
            return on_placed

        def deadline():
            return now_et() + timedelta(seconds=cfg.worst_case_order_s * 2)

        try:
            buy = await ex.work("BUY", symbol, 1, last, deadline(), on_placed=placer("BUY"))
        except Exception as exc:
            _p(f"!! BUY outcome unknown ({exc!r}) — check moomoo for a working order / 1 share")
            return
        if not buy or int(buy[2]) <= 0:
            store.set_position_status(pid, "ABORTED", closed_at=now_et().isoformat(), close_reason="uat_unfilled")
            _p("BUY unfilled (cancelled) — nothing held")
            return
        store.mark_opened(pid, 1, float(buy[1]), str(buy[0]))
        _p(f"BUY FILLED @ {buy[1]} (signal last {last}, slippage {float(buy[1]) - last:+.3f})")

        last2 = await last_price()
        try:
            sell = await ex.work("SELL", symbol, 1, last2 if last2 is not None else last, deadline(),
                                 on_placed=placer("SELL"))
        except Exception as exc:
            sell = None
            _p(f"   SELL error: {exc!r}")
        if not sell or int(sell[2]) <= 0:
            _p(f"!! 1 share of {symbol} still held — sell it manually in moomoo. scratch db: {tmpdb}")
            return
        store.record_exit_fill(pid, uuid4().hex, 1, float(sell[1]), today, now_et().isoformat())
        _p(f"SELL FILLED @ {sell[1]}. round-trip friction {float(sell[1]) - float(buy[1]):+.3f}/share. scratch db: {tmpdb}")
    finally:
        store.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default="rules_ibs.json")
    ap.add_argument("--live-1lot", action="store_true", help="BUY then SELL ONE share of --symbol (paper)")
    ap.add_argument("--symbol", default="US.XLU")
    ap.add_argument("--confirm", action="store_true", help="required with --live-1lot")
    a = ap.parse_args(argv)

    if a.live_1lot and not a.confirm:
        _p("--live-1lot places PAPER orders; add --confirm to proceed.")
        sys.exit(2)
    try:
        cfg = load_ibs_config(a.rules)
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    gw = MoomooGateway(get_gateway_config())
    gw.connect()  # paper guard: refuses unless PAPER_TRADING=true and SIMULATE
    try:
        async def _run():
            await probe(cfg, gw, now_et())
            if a.live_1lot:
                return await live_1lot(cfg, gw, a.symbol, now_et())
        refused = asyncio.run(_run()) is False
    finally:
        gw.close()
    if refused:
        sys.exit(3)


if __name__ == "__main__":
    main()
