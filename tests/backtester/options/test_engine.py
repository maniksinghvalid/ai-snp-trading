#!/usr/bin/env python3
"""
tests/backtester/options/test_engine.py — Phase 9 Plan 03 (T-09-07/T-09-08).

`_FakeChain` is a hand-built test double exposing the same public surface as
`OptionChainSource` (contracts_for_day/expiries_for_day/bar_close/bar_volume/
underlying_close/last_known_close) over plain dicts — no Massive, no network.
Option premiums in `_put_chain` are priced with the REAL `bs_price` so
`build_rows`'s own `implied_vol`/`bs_delta` round-trip is exact (no
hand-tuned delta literals), matching the round-trip precedent in
`tests/backtester/options/test_greeks.py`.

Entry-scan tests call `engine._entry_scan(day, today, change_pct)` directly
(bypassing `run_day`'s IV-series update) after pre-seeding `_iv_series` —
`iv_rank` needs a 60-observation warm-up (IV_RANK_MIN_OBS) that a full
day-by-day replay would need 60+ synthetic days to build; seeding is the
white-box-correct way to exercise the gate order without that overhead.
"""
from datetime import date, timedelta

import pytest

import bot.options.strategy as strategy_mod
from bot.options.config import load_options_config
from bot.options.strategy import option_dte

from backtester.options import engine as engine_mod
from backtester.options.engine import OptionsBacktestEngine, build_rows
from backtester.options.greeks import bs_price

_QUALIFYING_IV_SERIES = [10.0] * 59 + [50.0]  # min-max IVR -> 100.0, clears ivr_min=30


# ============================================================
# Fake chain (OptionChainSource-shaped test double)
# ============================================================

class _FakeChain:
    def __init__(self, underlying_by_day, contracts, bars):
        self._underlying_by_day = underlying_by_day
        self._contracts = contracts
        self._bars = bars
        self.fetch_calls = []  # tickers passed to rows_for, in call order (T-09-13 laziness proof)

    def rows_for(self, day, expiry, underlying_px, band_pct):
        today = date.fromisoformat(day)
        rows = []
        for c in self._contracts:
            if c["expiry"] != expiry:
                continue
            bar = self._bars.get(c["ticker"], {}).get(day)
            if bar is None:
                continue
            self.fetch_calls.append(c["ticker"])
            dte = option_dte(c["expiry"], today)
            rows.append({
                "ticker": c["ticker"], "right": c["right"], "strike": c["strike"],
                "expiry": c["expiry"], "dte": dte,
                "close": bar["close"], "volume": bar["volume"],
            })
        return rows

    def expiries_for_day(self, day):
        """Reference-based (T-09-13): no bar requirement, dte >= 0 only."""
        today = date.fromisoformat(day)
        seen = {}
        for c in self._contracts:
            dte = option_dte(c["expiry"], today)
            if dte >= 0:
                seen[c["expiry"]] = dte
        return sorted(seen.items())

    def bar_close(self, ticker, day):
        bar = self._bars.get(ticker, {}).get(day)
        return bar["close"] if bar else None

    def bar_volume(self, ticker, day):
        bar = self._bars.get(ticker, {}).get(day)
        return bar["volume"] if bar else None

    def underlying_close(self, day):
        return self._underlying_by_day.get(day)

    def last_known_close(self, ticker, day):
        per_day = self._bars.get(ticker)
        if not per_day:
            return None
        candidates = [d for d in per_day if d <= day]
        if not candidates:
            return None
        return per_day[max(candidates)]["close"]


def _put_chain(day, spot=100.0, sigma=0.45, r=0.045, dte=45):
    """One expiry, a 75-99 put strike ladder around `spot`, BS-priced for real."""
    today = date.fromisoformat(day)
    expiry = today + timedelta(days=dte)
    t_years = (expiry - today).days / 365.25
    contracts, bars = [], {}
    for strike in range(75, 100):
        ticker = f"O:TST{expiry:%y%m%d}P{int(strike * 1000):08d}"
        contracts.append({"ticker": ticker, "right": "P", "strike": float(strike), "expiry": expiry})
        price = bs_price(spot, strike, t_years, r, sigma, "P")
        bars[ticker] = {day: {"close": price, "volume": 100_000.0}}
    return _FakeChain({day: spot}, contracts, bars)


@pytest.fixture
def base_cfg():
    """rules_options.json with a put_credit_spread override (2-leg — simpler
    to hand-verify) and min_credit_to_width relaxed to fit the BS-priced
    fixture's ~0.221 credit/width ratio (same "relax one knob, keep the rest
    shipped" convention as tests/options/test_strategy.py::grid_cfg)."""
    cfg = load_options_config("rules_options.json")
    cfg.structure_type = "put_credit_spread"
    cfg.min_credit_to_width = 0.15
    return cfg


# ============================================================
# T-09-07: import identity, broker-free, row shape, cap gate order
# ============================================================

def test_imports_not_copies():
    for name in ("is_monthly_expiry", "option_dte", "pick_expiry", "passes_entry_gate",
                 "leg_is_liquid", "pick_strikes", "size_position", "mark_spread",
                 "manage_decision"):
        assert getattr(engine_mod, name) is getattr(strategy_mod, name), name


def test_no_broker_imports():
    import inspect
    src = inspect.getsource(engine_mod)
    for token in ("bot.gateway", "moomoo", "place_order"):
        assert token not in src


def test_build_rows_keys_and_drops_invalid_iv():
    day = "2025-01-02"
    today = date.fromisoformat(day)
    expiry = today + timedelta(days=45)
    t_years = (expiry - today).days / 365.25
    good = {
        "ticker": "O:X1", "right": "P", "strike": 90.0, "expiry": expiry, "dte": 45,
        "close": bs_price(100.0, 90.0, t_years, 0.045, 0.30, "P"), "volume": 1234.0,
    }
    # Below the no-arbitrage floor for any sigma -- implied_vol fails closed to None.
    bad = {
        "ticker": "O:X2", "right": "P", "strike": 90.0, "expiry": expiry, "dte": 45,
        "close": -5.0, "volume": 10.0,
    }
    rows = build_rows([good, bad], 100.0, day, 0.045, 2.0, "volume", 500)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "code", "right", "strike", "delta", "bid", "ask",
        "open_interest", "expiry", "dte", "bar_volume",
    }
    assert row["code"] == "O:X1"
    assert row["expiry"] == expiry.isoformat()
    assert row["dte"] == 45
    assert row["open_interest"] == 1234.0


def test_cap_gate_order(base_cfg):
    """Per-day cap breaks the sorted-order loop before max_concurrent_positions
    is even relevant -- CCC never gets evaluated once the day cap is hit."""
    day = "2025-01-02"
    cfg = base_cfg
    cfg.max_new_positions_per_day = 2
    cfg.max_concurrent_positions = 8
    chains = {code: _put_chain(day) for code in ("US.AAA", "US.BBB", "US.CCC")}

    engine = OptionsBacktestEngine(cfg, chains)
    for code in chains:
        engine._iv_series[code] = list(_QUALIFYING_IV_SERIES)

    engine._entry_scan(day, date.fromisoformat(day), {c: None for c in chains})

    assert set(engine._open) == {"US.AAA", "US.BBB"}
    assert "US.CCC" not in engine._open


def test_concurrent_cap_blocks_even_with_day_cap_room(base_cfg):
    day = "2025-01-02"
    cfg = base_cfg
    cfg.max_new_positions_per_day = 5
    cfg.max_concurrent_positions = 1
    engine = OptionsBacktestEngine(cfg, {"US.AAA": _put_chain(day)})
    engine._iv_series["US.AAA"] = list(_QUALIFYING_IV_SERIES)
    # A position on a DIFFERENT underlying already fills the concurrent cap.
    engine._open["US.ZZZ"] = {"max_loss_usd": 100.0}

    engine._entry_scan(day, date.fromisoformat(day), {"US.AAA": None})

    assert "US.AAA" not in engine._open


def test_already_open_underlying_is_skipped(base_cfg):
    day = "2025-01-02"
    engine = OptionsBacktestEngine(base_cfg, {"US.AAA": _put_chain(day)})
    engine._open["US.AAA"] = {"max_loss_usd": 100.0}
    engine._opened_on["US.AAA"] = "2024-12-01"
    engine._iv_series["US.AAA"] = list(_QUALIFYING_IV_SERIES)

    engine._entry_scan(day, date.fromisoformat(day), {"US.AAA": None})

    assert engine._open["US.AAA"]["max_loss_usd"] == 100.0  # untouched, never re-opened


def test_daily_loss_breaker_blocks_entries(base_cfg):
    """The breaker is evaluated BEFORE the per-day cap (D-13) -- a breach
    blocks the whole scan even though max_new_positions_per_day has room."""
    day = "2025-01-02"
    cfg = base_cfg
    engine = OptionsBacktestEngine(cfg, {"US.AAA": _put_chain(day)})
    engine._iv_series["US.AAA"] = list(_QUALIFYING_IV_SERIES)
    breach = -(cfg.daily_loss_limit_pct / 100 * cfg.sizing_equity_usd) - 1.0
    engine.trade_log.append({"closed_date": day, "pnl_usd": breach})

    engine._entry_scan(day, date.fromisoformat(day), {"US.AAA": None})

    assert engine._open == {}
    assert day in engine._breaker_days


def test_daily_loss_breaker_trips_on_unrealized_alone(base_cfg):
    """WR-02: mirrors service.py's _job_manage trip point -- an unrealized
    loss alone (zero realized loss today) breaches the limit and blocks
    entries, exactly like live's second breaker check."""
    day = "2025-01-02"
    cfg = base_cfg
    engine = OptionsBacktestEngine(cfg, {"US.AAA": _put_chain(day)})
    engine._iv_series["US.AAA"] = list(_QUALIFYING_IV_SERIES)
    engine._unrealized_today = -(cfg.daily_loss_limit_pct / 100 * cfg.sizing_equity_usd) - 1.0

    engine._entry_scan(day, date.fromisoformat(day), {"US.AAA": None})

    assert engine._open == {}
    assert day in engine._breaker_days


def test_manage_day_accumulates_unrealized_for_still_open_positions(base_cfg):
    """_manage_day populates self._unrealized_today from every position it
    does NOT close today (mark - credit, matching service.py's
    _manage_position return-value convention)."""
    day = "2025-02-01"
    expiry = date.fromisoformat(day) + timedelta(days=45)
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry, qty=2, credit=1.00, width=5.0)
    # mark stays comfortably inside manage_decision's hold band (no close).
    engine.chains["US.AAA"] = _FakeChain(
        {day: 100.0}, contracts=[],
        bars={
            "O:SHORT": {day: {"close": 0.90, "volume": 1000.0}},
            "O:LONG": {day: {"close": 0.10, "volume": 1000.0}},
        },
    )

    engine._manage_day(day)

    assert "US.AAA" in engine._open  # still open -- no close recorded
    mark = 0.90 - 0.10  # mark_spread: short mid minus long mid
    assert engine._unrealized_today == pytest.approx((1.00 - mark) * 100 * 2)


def test_manage_runs_before_entry_scan(base_cfg, monkeypatch):
    day = "2025-01-02"
    engine = OptionsBacktestEngine(base_cfg, {"US.AAA": _put_chain(day)})
    order = []
    monkeypatch.setattr(engine, "_manage_day", lambda d: order.append("manage"))
    monkeypatch.setattr(engine, "_entry_scan", lambda d, t, c: order.append("entry"))

    engine.run_day(day)

    assert order == ["manage", "entry"]


def test_deterministic_underlying_order(base_cfg):
    day = "2025-01-02"

    def _build():
        cfg = load_options_config("rules_options.json")
        cfg.structure_type = "put_credit_spread"
        cfg.min_credit_to_width = 0.15
        cfg.max_new_positions_per_day = 1
        cfg.max_concurrent_positions = 8
        chains = {code: _put_chain(day) for code in ("US.AAA", "US.BBB")}
        engine = OptionsBacktestEngine(cfg, chains)
        for code in chains:
            engine._iv_series[code] = list(_QUALIFYING_IV_SERIES)
        engine.run([day])
        return engine

    e1, e2 = _build(), _build()
    assert list(e1._open) == list(e2._open) == ["US.AAA"]
    assert e1.trade_log == e2.trade_log


# ============================================================
# T-09-08: manage, fill/close arithmetic, expiry settlement
# ============================================================

def _spread_position(day, expiry, qty=2, credit=1.00, width=5.0):
    return {
        "underlying": "US.AAA", "structure": "put_credit_spread",
        "expiry": expiry.isoformat(), "dte_at_entry": 45, "ivr_at_entry": 50.0,
        "credit_per_spread": credit, "width": width, "qty": qty,
        "max_loss_usd": (width - credit) * 100 * qty, "opened_at": "2025-01-01",
        "legs": [
            {"code": "O:SHORT", "right": "P", "strike": 100.0, "side": "SELL", "mid": 0.50},
            {"code": "O:LONG", "right": "P", "strike": 95.0, "side": "BUY", "mid": 0.10},
        ],
        "open_commission_usd": 0.65 * 2 * qty,
        "min_leg_volume": 1000,
    }


def test_fill_and_settlement(base_cfg):
    """Hand-computed literal (not recomputed from the engine's own helpers):
    mark 0.40 on a credit-1.00, qty-2 spread, slippage 0.02, commission 0.65/leg
    -> net_exit = (0.50+0.02) - (0.10-0.02) = 0.44
       pnl = (1.00-0.44)*100*2 - (0.65*2*2)*2 = 112 - 5.20 = 106.80
    """
    day = "2025-02-01"
    expiry = date.fromisoformat(day) + timedelta(days=45)
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry)
    engine.chains["US.AAA"] = _FakeChain(
        {day: 100.0}, contracts=[],
        bars={
            "O:SHORT": {day: {"close": 0.50, "volume": 1000.0}},
            "O:LONG": {day: {"close": 0.10, "volume": 1000.0}},
        },
    )

    engine._manage_day(day)

    assert "US.AAA" not in engine._open
    assert len(engine.trade_log) == 1
    row = engine.trade_log[0]
    assert row["exit_reason"] == "profit_target"
    assert row["pnl_usd"] == pytest.approx(106.80)
    assert row["credit_captured_pct"] == pytest.approx(56.0)
    assert row["carried_mark"] is False
    assert row["commission_usd"] == pytest.approx(5.20)


def test_assignment_guard_overrides_profit_target(base_cfg):
    """manage_decision's own priority order (assignment_guard first) is
    honoured because the IMPORTED function decides, not the engine."""
    day = "2025-02-01"
    expiry = date.fromisoformat(day) + timedelta(days=1)  # dte == 1 == assignment_guard_dte
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry)
    engine.chains["US.AAA"] = _FakeChain(
        {day: 100.0}, contracts=[],
        bars={
            "O:SHORT": {day: {"close": 0.50, "volume": 1000.0}},
            "O:LONG": {day: {"close": 0.10, "volume": 1000.0}},
        },
    )

    engine._manage_day(day)

    assert engine.trade_log[0]["exit_reason"] == "assignment_guard"


def test_expiry_settles_at_intrinsic(base_cfg):
    day = "2025-02-15"
    expiry = date.fromisoformat(day)  # today == expiry -> settle, not manage

    # Fully OTM: both strikes below the 100 underlying close -> settle for 0,
    # max profit retained.
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry, qty=1, credit=1.00, width=5.0)
    engine.chains["US.AAA"] = _FakeChain({day: 100.0}, contracts=[], bars={})
    engine._manage_day(day)
    row = engine.trade_log[0]
    assert row["exit_reason"] == "expired"
    assert row["pnl_usd"] == pytest.approx(1.00 * 100 * 1 - 1.30)  # credit kept, open commission only
    assert row["commission_usd"] == pytest.approx(1.30)

    # ITM short: underlying settles at 90 -> short(100) intrinsic 10, long(95) intrinsic 5.
    engine2 = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine2._open["US.AAA"] = _spread_position(day, expiry, qty=1, credit=1.00, width=5.0)
    engine2.chains["US.AAA"] = _FakeChain({day: 90.0}, contracts=[], bars={})
    engine2._manage_day(day)
    row2 = engine2.trade_log[0]
    assert row2["exit_reason"] == "expired"
    assert row2["pnl_usd"] == pytest.approx((1.00 - 5.0) * 100 * 1 - 1.30)


def test_carried_mark_on_missing_bar(base_cfg):
    day = "2025-02-01"
    prior_day = "2025-01-31"
    expiry = date.fromisoformat(day) + timedelta(days=45)
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry)
    engine.chains["US.AAA"] = _FakeChain(
        {day: 100.0}, contracts=[],
        bars={
            "O:SHORT": {prior_day: {"close": 0.50, "volume": 1000.0}},  # no bar dated `day`
            "O:LONG": {day: {"close": 0.10, "volume": 1000.0}},
        },
    )

    engine._manage_day(day)

    assert len(engine.trade_log) == 1
    assert engine.trade_log[0]["carried_mark"] is True


# ============================================================
# T-09-14: CR-01 end-of-window settlement
# ============================================================

def test_close_open_at_end_settles_residual_position(base_cfg):
    """A position still open when the replay window ends is marked and
    recorded with exit_reason='end_of_window' -- never silently dropped
    from the trade log (CR-01)."""
    day = "2025-02-01"
    expiry = date.fromisoformat(day) + timedelta(days=45)  # well past `day` -- not expiring
    engine = OptionsBacktestEngine(base_cfg, {}, slippage_usd=0.02, commission_per_leg=0.65)
    engine._open["US.AAA"] = _spread_position(day, expiry)
    engine.chains["US.AAA"] = _FakeChain(
        {day: 100.0}, contracts=[],
        bars={
            "O:SHORT": {day: {"close": 0.50, "volume": 1000.0}},
            "O:LONG": {day: {"close": 0.10, "volume": 1000.0}},
        },
    )

    engine.close_open_at_end(day)

    assert "US.AAA" not in engine._open
    assert engine.open_positions_at_end == 0
    assert len(engine.trade_log) == 1
    assert engine.trade_log[0]["exit_reason"] == "end_of_window"
    assert engine.trade_log[0]["pnl_usd"] == pytest.approx(106.80)  # same arithmetic as a normal close


def test_close_open_at_end_leaves_unmarkable_position_open():
    """A leg with no bar at all (not even a carry-forward candidate) cannot
    be marked -- close_open_at_end leaves it open and reports it via
    open_positions_at_end so the residue is visible, never silently lost."""
    cfg = load_options_config("rules_options.json")
    cfg.structure_type = "put_credit_spread"
    day = "2025-02-01"
    expiry = date.fromisoformat(day) + timedelta(days=45)
    engine = OptionsBacktestEngine(cfg, {})
    engine._open["US.AAA"] = _spread_position(day, expiry)
    engine.chains["US.AAA"] = _FakeChain({day: 100.0}, contracts=[], bars={})  # no bars at all

    engine.close_open_at_end(day)

    assert "US.AAA" in engine._open  # left open -- genuinely unmarkable
    assert engine.open_positions_at_end == 1
    assert engine.trade_log == []


# ============================================================
# T-09-13: lazy per-decision-day fetch proof (VERIFICATION gap 3, D-18)
# ============================================================

def test_update_iv_and_entry_scan_fetch_only_one_expiry_per_day(base_cfg):
    """A chain with 3 expiries -- update_iv (IV-tracking expiry, closest to
    target_dte) and _entry_scan (pick_expiry's chosen expiry) must each
    fetch bars for exactly ONE of the three expiries on a given day, never
    all three (the eager-design defect this plan fixes)."""
    day = "2025-01-02"
    today = date.fromisoformat(day)
    cfg = base_cfg
    cfg.target_dte = 45

    contracts, bars = [], {}
    for dte_offset in (30, 45, 60):  # three expiries, all inside [min_dte, max_dte]
        expiry = today + timedelta(days=dte_offset)
        for strike in range(90, 100):  # put ladder, near-ATM
            ticker = f"O:TST{expiry:%y%m%d}P{int(strike * 1000):08d}"
            contracts.append({"ticker": ticker, "right": "P", "strike": float(strike), "expiry": expiry})
            price = bs_price(100.0, strike, dte_offset / 365.25, 0.045, 0.45, "P")
            bars[ticker] = {day: {"close": price, "volume": 100_000.0}}
    chain = _FakeChain({day: 100.0}, contracts, bars)

    engine = OptionsBacktestEngine(cfg, {"US.AAA": chain})
    engine.update_iv(day)

    fetched_expiries = {
        next(c["expiry"] for c in contracts if c["ticker"] == t) for t in chain.fetch_calls
    }
    assert fetched_expiries == {today + timedelta(days=45)}  # closest to target_dte=45 -- ONE expiry only
