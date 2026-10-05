#!/usr/bin/env python3
"""
tests/ibs/test_parity.py — D-16 research-vs-production parity.

The REAL research `simulate` (backtester/experimental/ibs_search/strategy_search.py)
is AST-extracted and executed against synthetic frames; the production pure
functions in bot.ibs.strategy are replayed over the same bars; the multisets of
(entry_date, exit_date, ret) trades must be identical.

The single deliberate divergence is orchestrator ruling 3 (same-day re-entry):
after a time exit while IBS < ibs_entry_max, the research sim re-buys the same
session; production forbids it (a same-session sell/re-buy round trip) and
re-enters no earlier than the next session. Scenario B pins that.

The research module is never imported: it downloads market data at import.
"""
import ast
import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd

from bot.ibs.strategy import compute_ibs, decide_entries, decide_exits, trading_days_held

REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH = REPO_ROOT / "backtester" / "experimental" / "ibs_search" / "strategy_search.py"

CODES = ["US.A", "US.B", "US.C", "US.D", "US.E"]
BASES = [50.0, 80.0, 120.0, 200.0, 300.0]
N_ = None  # no-range bar (high == low): no signal in either implementation


def _load_research_simulate():
    """Build `simulate` from its FunctionDef only (never import the module)."""
    tree = ast.parse(RESEARCH.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "simulate")
    ns = {"np": np, "pd": pd}
    exec(compile(ast.Module([fn], []), str(RESEARCH), "exec"), ns)
    return ns["simulate"]


def _frames(ibs_rows, codes, bases):
    """Per-day IBS targets (None = no-range bar) -> (C, H, L) frames with exactly that IBS."""
    dates = pd.bdate_range("2026-01-05", periods=len(ibs_rows))
    C = pd.DataFrame(index=dates, columns=codes, dtype=float)
    H, L = C.copy(), C.copy()
    for t, row in enumerate(ibs_rows):
        for k, code in enumerate(codes):
            # quarter-dollar prices, range 4.0 and sixteenth-multiple IBS targets are all
            # exact binary floats, so IBS ties are exact (as in real ties) not noise
            c = round(bases[k] * (1 + 0.004 * (((t * 7 + k * 3) % 11) - 5)) * 4) / 4  # moves within +-5%
            target = row[k]
            C.iloc[t, k] = c
            if target is None:
                H.iloc[t, k] = L.iloc[t, k] = c
            else:
                L.iloc[t, k] = c - target * 4.0
                H.iloc[t, k] = L.iloc[t, k] + 4.0
    return C, H, L


def _research_trades(C, H, L, slots, max_hold):
    ibs = (C - L) / (H - L)
    px = {"Close": C, "Open": C, "High": H, "Low": L}
    _, tr = _load_research_simulate()(px, (ibs < 0.2).fillna(False), (ibs > 0.8).fillna(False),
                                      ibs.fillna(99), 0.0, slots=slots, max_hold=max_hold)
    return [(r.entry, r.exit, r.ret) for r in tr.itertuples()]


def _replay_production(C, H, L, cfg, universe):
    """Drive the production pure functions bar by bar (close fills, ruling-3 exclusion)."""
    sessions = [d.date() for d in C.index]
    held, trades, exclusion_bound = {}, [], False
    for t in range(len(C)):
        ibs_by_code = {}
        for c in universe:
            v = compute_ibs(C[c].iloc[t], H[c].iloc[t], L[c].iloc[t])
            if v is not None:
                ibs_by_code[c] = v
        held_days = {c: trading_days_held(sessions[i], sessions[t], sessions) for c, i in held.items()}
        exits = decide_exits(list(held), ibs_by_code, held_days, set(), cfg)
        for c, _reason in exits:
            i = held.pop(c)
            trades.append((C.index[i], C.index[t], C[c].iloc[t] / C[c].iloc[i] - 1))
        exited = {c for c, _ in exits}
        if any(ibs_by_code.get(c, 1.0) < cfg.ibs_entry_max for c in exited):
            exclusion_bound = True
        for c in decide_entries(ibs_by_code, set(held) | exited,
                                cfg.max_concurrent_positions - len(held), cfg, universe):
            held[c] = t
    return trades, exclusion_bound


def _norm(trades):
    return sorted((e, x, round(float(r), 10)) for e, x, r in trades)


# Rows = sessions, columns = A..E. Slots 3, max_hold 3. IBS targets are exact binary
# fractions: .0625 / .125 / .1875 (< 0.20 entry), .5, .875 (> 0.80 exit).
SCENARIO_A = [
    [.125, .0625, .125, .1875, .5],   # 0: 4 candidates, 3 slots -> B, A, C (D skipped)
    [.5, .5, .5, .125, .125],       # 1: no free slot
    [.875, .5, .5, .125, .125],       # 2: A IBS exit; freed slot filled same session; D/E tie -> D
    [.125, .5, N_, .5, N_],       # 3: B time exit (IBS .5), C time exit (no-range), E flat no-range not entered; A enters
    [.5, .125, .125, .5, .125],       # 4: B/C/E tie, one free slot -> B
    [.5, .5, .125, .5, .125],       # 5: D time exit; C/E tie -> C
    [.5, .5, .5, .125, .125],       # 6: A time exit; D/E tie -> D
    [.125, .5, .5, .5, .125],       # 7: B time exit; A/E tie -> A
    [.5, .1875, .5, .5, .125],      # 8: C time exit; E (.1) beats B (.15)
    [.5, .1875, .5, .5, .5],      # 9: D time exit; B enters
    [.5, .5, .125, .5, .5],       # 10: A time exit; C enters
    [.5] * 5,                   # 11: E time exit
    [.5] * 5,                   # 12: B time exit
    [.875] * 5, [.875] * 5, [.875] * 5, [.875] * 5, [.875] * 5,  # 13+: C IBS exit, nothing left open
]


def test_parity_multi_day_scenario(ibs_cfg):
    C, H, L = _frames(SCENARIO_A, CODES, BASES)
    cfg = dataclasses.replace(ibs_cfg, max_concurrent_positions=3, max_hold_trading_days=3)
    research = _research_trades(C, H, L, slots=3, max_hold=3)
    prod, exclusion_bound = _replay_production(C, H, L, cfg, CODES)
    assert len(research) >= 6
    assert not exclusion_bound, "scenario A must contain no same-day re-entry"
    assert _norm(research) == _norm(prod)


def test_divergence_same_day_reentry_ruling_3(ibs_cfg):
    """Ruling 3: research re-buys the code on the day of its time exit; production waits a session."""
    max_hold = 3
    C, H, L = _frames([[.125]] * 10, ["US.A"], [50.0])
    cfg = dataclasses.replace(ibs_cfg, max_concurrent_positions=3, max_hold_trading_days=max_hold)
    research = sorted(_research_trades(C, H, L, slots=3, max_hold=max_hold))
    prod, exclusion_bound = _replay_production(C, H, L, cfg, ["US.A"])
    prod = sorted(prod)
    assert research[1][0] == research[0][1]        # research: trade 2 enters on trade 1's exit date
    assert exclusion_bound                          # production: the same-day exclusion bound
    assert prod[1][0] == C.index[C.index.get_loc(prod[0][1]) + 1]  # next session, never the same one
