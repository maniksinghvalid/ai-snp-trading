#!/usr/bin/env python3
"""
uat_options_probe.py — Phase 8 (tasty_credit_spreads) live UAT against OpenD.

Default mode is READ-ONLY: it exercises the exact production code path
(MoomooGateway option reads → strategy pure functions → sizing) and prints what
the bot WOULD do today, plus the three facts only a live payload can settle:

  1. `u_change_ratio` unit (percent vs fraction) — drives the "sell into fear" knob
  2. `position_list_query` short-leg sign convention (`position_side` vs negative qty)
  3. whether real 16Δ wings clear the liquidity gate

`--live-1lot` (operator-run, places PAPER orders on the shared SIMULATE account):
opens ONE put credit spread on --symbol via LegExecutor + OptionsStore on a
scratch DB, checks reconcile is clean, then closes it and prints realized P&L.
Proves the SIMULATE fill model for option limit orders (escalation to natural).

Usage:
  PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 \\
      python3 scripts/uat_options_probe.py [--rules rules_options.json] [--symbols US.SPY,US.QQQ]
  ... --live-1lot --symbol US.SPY --confirm     # places + closes 1 lot (paper)
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.gateway.gateway import MoomooGateway, get_gateway_config  # noqa: E402
from bot.options.config import load_options_config  # noqa: E402
from bot.options.execution import LegExecutor  # noqa: E402
from bot.options.store import OptionsStore  # noqa: E402
from bot.options.strategy import (  # noqa: E402
    leg_is_liquid, manage_decision, mark_spread, option_dte, passes_entry_gate,
    pick_expiry, pick_strikes, size_position,
)
from bot.safety.et_helpers import now_et  # noqa: E402


def _p(*a):
    print(*a, flush=True)


def _hr(title):
    _p("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


async def probe(cfg, gateway, symbols):
    today = now_et().date()
    _hr(f"1. stock_ids for {len(symbols)} underlyings")
    ids = await gateway.get_stock_ids(symbols)
    _p(json.dumps(ids, indent=1))
    by_id = {v: k for k, v in ids.items()}
    if not ids:
        _p("!! no stock ids — abort")
        return None

    _hr("2. option screen (puts, then calls if iron_condor) — raw underlying stats")
    puts = await gateway.screen_options(list(ids.values()), "P", cfg.min_dte, cfg.max_dte, -0.35, -0.03)
    calls = []
    if cfg.structure_type == "iron_condor":
        calls = await gateway.screen_options(list(ids.values()), "C", cfg.min_dte, cfg.max_dte, 0.03, 0.35)
    rows = list(puts) + list(calls)
    _p(f"rows: puts={len(puts)} calls={len(calls)}")
    if rows:
        _p("first row keys:", sorted(rows[0].keys()))
        _p("first row:", json.dumps({k: (str(v) if not isinstance(v, (int, float, str, type(None))) else v)
                                     for k, v in rows[0].items()}, indent=1, default=str))

    by_u = {}
    for r in rows:
        by_u.setdefault(r.get("u_stock_id"), []).append(r)

    _hr("3. per-underlying: IVR / IVP / price / change_ratio → entry gate")
    _p("NOTE u_iv_rank/u_iv_percentile are SDK fractions (x100 below); u_change_ratio unit is what we're checking:")
    _p(f"{'code':8} {'ivr%':>6} {'ivp%':>6} {'price':>9} {'u_change_ratio(raw)':>20} {'gate':>5} rows")
    gate_ok = []
    for sid, urows in sorted(by_u.items(), key=lambda kv: by_id.get(kv[0], "")):
        code = by_id.get(sid, f"id{sid}")
        h = urows[0]
        ivr = h.get("u_iv_rank"); ivp = h.get("u_iv_percentile"); chg = h.get("u_change_ratio")
        ivr_pct = None if ivr in (None, "N/A") else float(ivr) * 100
        ivp_pct = None if ivp in (None, "N/A") else float(ivp) * 100
        u = {"ivr_pct": ivr_pct, "ivp_pct": ivp_pct, "change_pct": chg if chg not in (None, "N/A") else None}
        ok = passes_entry_gate(u, cfg) if ivr_pct is not None else False
        _p(f"{code:8} {ivr_pct if ivr_pct is not None else float('nan'):6.1f} "
           f"{ivp_pct if ivp_pct is not None else float('nan'):6.1f} {float(h.get('u_price') or 0):9.2f} "
           f"{str(chg):>20} {str(ok):>5} {len(urows)}")
        if ok:
            gate_ok.append(code)
    _p(f"\nentry-gate PASS today: {gate_ok or 'none (expected in a low-IVR regime — no trade is the default)'}")

    _hr("4. what pick_expiry / pick_strikes / size_position would build (gate IGNORED, for visibility)")
    picked = {}
    for sid, urows in sorted(by_u.items(), key=lambda kv: by_id.get(kv[0], "")):
        code = by_id.get(sid)
        if code is None:
            continue
        expiries = sorted({(date.fromisoformat(r["expiry"]), int(r["dte"]))
                           for r in urows if r.get("expiry") and r.get("dte") not in (None, "N/A")})
        exp = pick_expiry(expiries, today, cfg)
        if exp is None:
            _p(f"{code}: no expiry in [{cfg.min_dte},{cfg.max_dte}] DTE"); continue
        chain = [r for r in urows if r.get("expiry") == exp.isoformat()]
        px = float(urows[0].get("u_price") or 0)
        sel = pick_strikes(chain, px, cfg.structure_type, cfg)
        if sel is None:
            # explain why: rebuild with the credit gate off (and then liquidity off) to see the raw numbers
            from dataclasses import replace as _rp
            raw = pick_strikes(chain, px, cfg.structure_type, _rp(cfg, min_credit_to_width=0.0))
            if raw is not None:
                legs = " | ".join(f"{l['side']} {l['right']}{l['strike']:g} @{l['mid']:.2f}" for l in raw["legs"])
                _p(f"{code}: expiry {exp} dte {option_dte(exp, today)} → None because credit/width "
                   f"{raw['credit']:.2f}/{raw['width']:g} = {raw['credit']/raw['width']:.2f} < {cfg.min_credit_to_width}  legs: {legs}")
            else:
                raw2 = pick_strikes(chain, px, cfg.structure_type,
                                    _rp(cfg, min_credit_to_width=0.0, max_spread_pct_of_mid=1e9, max_spread_abs_usd=1e9, min_open_interest=0))
                if raw2 is not None:
                    legs = " | ".join(f"{l['side']} {l['right']}{l['strike']:g} @{l['mid']:.2f}" for l in raw2["legs"])
                    illq = [(l['code'], next(({'bid': r['bid'], 'ask': r['ask'], 'oi': r['open_interest']} for r in chain if r['code']==l['code']), None))
                            for l in raw2['legs'] if not leg_is_liquid(next(r for r in chain if r['code']==l['code']), cfg)]
                    _p(f"{code}: expiry {exp} dte {option_dte(exp, today)} → None because ILLIQUID leg(s) {illq}; would be: {legs} credit {raw2['credit']:.2f}")
                else:
                    _p(f"{code}: expiry {exp} dte {option_dte(exp, today)} → None: no strike geometry (chain too sparse for {cfg.structure_type})")
            continue
        qty = size_position(sel["width"], sel["credit"], cfg, 0.0)
        legs = " | ".join(f"{l['side']} {l['right']}{l['strike']:g} @{l['mid']:.2f}" for l in sel["legs"])
        _p(f"{code}: expiry {exp} (dte {option_dte(exp, today)}) credit {sel['credit']:.2f} width {sel['width']:g} "
           f"credit/width {sel['credit']/sel['width']:.2f} qty {qty} maxloss ${(sel['width']-sel['credit'])*100*max(qty,1):.0f}  legs: {legs}")
        picked[code] = (sel, chain, exp)

    _hr("5. broker option positions — raw rows + get_option_positions() sign convention")
    loop = asyncio.get_running_loop()

    def _raw():
        from moomoo import TrdEnv
        ret, data = gateway._trade_ctx.position_list_query(
            trd_env=TrdEnv.SIMULATE, acc_id=gateway.cfg.acc_id, refresh_cache=True)
        return ret, data
    ret, data = await loop.run_in_executor(None, _raw)
    if ret == 0 and data is not None and len(data):
        cols = [c for c in ["code", "qty", "can_sell_qty", "position_side", "cost_price", "market_val"] if c in data.columns]
        opt = data[data["code"].str.match(r"^US\.[A-Z]+\d{6}[CP]\d+$", na=False)]
        _p(f"total broker positions {len(data)}; option rows {len(opt)}")
        _p(opt[cols].to_string() if len(opt) else "(no option positions on the account)")
    else:
        _p("position_list_query ret", ret, data)
    signed = await gateway.get_option_positions()
    _p("get_option_positions():", signed)

    _hr("6. snapshot of picked leg codes (bid/ask/delta/OI) — sanity vs screen")
    codes = [l["code"] for sel, _, _ in picked.values() for l in sel["legs"]][:40]
    if codes:
        ret, snap = await gateway.get_market_snapshot(codes)
        if ret == 0:
            cols = [c for c in ["code", "bid_price", "ask_price", "last_price", "option_delta", "option_open_interest", "option_implied_volatility"] if c in snap.columns]
            _p(snap[cols].to_string())
        else:
            _p("snapshot ret", ret, snap)
    return picked


async def live_1lot(cfg, gateway, symbol, picked):
    """Open + close ONE put credit spread on `symbol` (paper). Operator-run only."""
    from dataclasses import replace
    _hr(f"LIVE 1-LOT: {symbol} put credit spread on PAPER account {gateway.cfg.acc_id}")
    if symbol not in picked:
        _p("!! no strikes picked for", symbol, "— cannot place"); return
    sel, chain, exp = picked[symbol]
    if cfg.structure_type != "put_credit_spread":
        # force a 2-leg put spread for the probe regardless of configured structure
        sel = pick_strikes(chain, float(chain[0]["u_price"]), "put_credit_spread", replace(cfg, structure_type="put_credit_spread"))
        if sel is None:
            _p("!! put_credit_spread not buildable right now"); return
    qty = 1
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="uat_options_"), "state.db")
    store = OptionsStore(tmpdb).open()
    ex = LegExecutor(gateway, cfg)
    pid = uuid4().hex
    store.insert_option_position({
        "position_id": pid, "underlying": symbol, "structure": "put_credit_spread",
        "expiry": exp.isoformat(), "dte_at_entry": option_dte(exp, now_et().date()),
        "ivr_at_entry": 0.0, "credit_per_spread": sel["credit"], "width": sel["width"],
        "qty": qty, "max_loss_usd": (sel["width"] - sel["credit"]) * 100 * qty,
        "status": "OPENING", "opened_at": now_et().isoformat(),
    })
    leg_ids = {}
    for leg in sel["legs"]:
        lid = uuid4().hex; leg_ids[leg["code"]] = lid
        store.insert_option_leg({"leg_id": lid, "position_id": pid, "code": leg["code"], "right": leg["right"],
                                 "strike": leg["strike"], "side": leg["side"], "qty": qty, "status": "PENDING"})
    quotes = {r["code"]: {"bid": r["bid"], "ask": r["ask"]} for r in chain}

    async def on_placed(leg, oid):
        _p(f"  placed {leg['side']} {leg['code']} order {oid}")
        store.set_leg_entry(leg_ids[leg["code"]], order_id=oid, status="WORKING")

    async def on_filled(leg, oid, price, fq):
        _p(f"  FILLED {leg['side']} {leg['code']} @{price} x{fq}")
        store.set_leg_entry(leg_ids[leg["code"]], price=price, status="FILLED")

    _p("opening legs (long wing first):", [(l["side"], l["code"]) for l in sel["legs"]], "credit est", round(sel["credit"], 2))
    filled = await ex.open_position(sel["legs"], qty, quotes, on_leg_placed=on_placed, on_leg_filled=on_filled)
    if filled is None:
        store.set_position_status(pid, "ABORTED", closed_at=now_et().isoformat(), close_reason="open_failed")
        _p("!! open FAILED — legs unwound; ABORTED. Check the escalation logs above."); return
    store.set_position_status(pid, "OPEN")
    credit_real = sum(l["entry_price"] for l in filled if l["side"] == "SELL") - sum(l["entry_price"] for l in filled if l["side"] == "BUY")
    _p(f"OPEN. realized credit {credit_real:.2f} (est {sel['credit']:.2f})")

    broker = await gateway.get_option_positions()
    expected = {l["code"]: (qty if l["side"] == "BUY" else -qty) for l in sel["legs"]}
    _p("reconcile expected", expected, "broker", {c: broker.get(c) for c in expected})
    _p("reconcile", "CLEAN" if all(broker.get(c) == q for c, q in expected.items()) else "MISMATCH — check sign convention!")

    ret, snap = await gateway.get_market_snapshot(list(expected))
    q2 = {r["code"]: {"bid": r["bid_price"], "ask": r["ask_price"]} for _, r in snap.iterrows()} if ret == 0 else quotes
    mark = mark_spread(sel["legs"], q2)
    _p(f"mark {mark:.2f} → manage_decision={manage_decision(mark, credit_real, option_dte(exp, now_et().date()), cfg)} (expected None; we close anyway)")

    async def on_xplaced(leg, oid):
        _p(f"  close placed {leg['side']} {leg['code']} order {oid}")
        store.set_leg_exit(leg_ids[leg["code"]], order_id=oid, status="CLOSING")

    async def on_xfilled(leg, oid, price, fq):
        _p(f"  close FILLED {leg['side']} {leg['code']} @{price} x{fq}")
        store.set_leg_exit(leg_ids[leg["code"]], price=price, status="CLOSED")

    ok = await ex.close_legs(sel["legs"], q2, aggressive=False, on_leg_placed=on_xplaced, on_leg_filled=on_xfilled)
    pos = store.get_option_positions(("OPEN",))[0]
    exits = {l["code"]: l.get("exit_price") for l in pos["legs"]}
    if ok and all(v is not None for v in exits.values()):
        cost = sum(exits[l["code"]] for l in sel["legs"] if l["side"] == "SELL") - sum(exits[l["code"]] for l in sel["legs"] if l["side"] == "BUY")
        realized = (credit_real - cost) * 100 * qty
        store.set_position_status(pid, "CLOSED", closed_at=now_et().isoformat(), close_reason="uat", realized_pnl_usd=realized)
        _p(f"CLOSED. cost-to-close {cost:.2f}, realized ${realized:.2f} (round-trip friction). scratch db: {tmpdb}")
    else:
        store.set_position_status(pid, "NEEDS_ATTENTION")
        _p("!! close incomplete — NEEDS_ATTENTION; close the remaining legs manually in moomoo. scratch db:", tmpdb)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", default="rules_options.json")
    ap.add_argument("--symbols", default="", help="comma list; default = cfg.universe")
    ap.add_argument("--live-1lot", action="store_true", help="place+close ONE paper put credit spread on --symbol")
    ap.add_argument("--symbol", default="US.SPY")
    ap.add_argument("--confirm", action="store_true", help="required with --live-1lot")
    a = ap.parse_args()

    cfg = load_options_config(a.rules)
    symbols = [s.strip() for s in a.symbols.split(",") if s.strip()] or list(cfg.universe)
    if a.live_1lot and not a.confirm:
        _p("--live-1lot places PAPER orders; add --confirm to proceed."); sys.exit(2)

    gw = MoomooGateway(get_gateway_config())
    gw.connect()  # paper guard: refuses unless PAPER_TRADING=true and SIMULATE
    try:
        async def _run():
            picked = await probe(cfg, gw, symbols)
            if a.live_1lot and picked is not None:
                await live_1lot(cfg, gw, a.symbol, picked)
        asyncio.run(_run())
    finally:
        gw.close()


if __name__ == "__main__":
    main()
