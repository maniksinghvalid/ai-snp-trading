#!/usr/bin/env python3
"""
tests.backtester.fixtures — shared synthetic-data fixtures for Phase 6 backtester tests.

Pure functions only — NO network access, NO import of any `backtester.*` module — so this
file is always importable, even before backtester/feed.py, execution.py, harness.py, or
report.py exist (Wave 0 scaffold, plan 06-01).

  make_ahead_only_5m_dataset() — deterministic 5m bar sequence for one code/session,
    engineered so the ONLY bar clearing entry conditions is bar N, and bar N+1's open is
    materially different from bar N's close (BT-02 look-ahead proof: the fill must use
    bar N+1's open, never the signal bar's close / intent.entry_price). The last bar in the
    sequence also clears entry conditions but has no N+1 bar, proving "signal on the last
    bar -> no fill" (D-05 abandon).

  make_trade_log() — small closed-trade list (project trade-log contract keys: code,
    entry_price, exit_price, quantity, exit_reason, r_multiple, closed_at) with hand-computed
    expected metrics documented below, for the 06-04 report test.
"""


def make_ahead_only_5m_dataset():
    """Return a deterministic list of BarEvent-shaped dicts for code "US.TEST".

    One session, 5m bars 09:30-09:55 ET. hod/lod/cum_volume are pre-computed as the
    session running max-high / min-low / summed-volume through and including each bar
    (matching bot.signal.events.BarEvent's field semantics).

    Bar index 3 (09:45) is "bar N": close=105.00 breaks out above the prior running hod
    (101.00) -> this is the only bar an entry signal could fire on before the breakout.
    Bar index 4 (09:50) is "bar N+1": open=103.50 -- a gap DOWN from bar N's close, so a
    fill correctly using bar N+1's open (103.50) is trivially distinguishable from a
    look-ahead bug that would fill at bar N's close (105.00) / intent.entry_price.
    Bar index 5 (09:55) is the LAST bar: close=107.50 also clears the running hod, but
    there is no bar 6, so a signal sourced from this bar must yield no fill.
    """
    raw = [
        # (time_key, open, high, low, close, volume)
        ("2026-06-01 09:30:00", 100.00, 100.50, 99.50, 100.20, 10_000),
        ("2026-06-01 09:35:00", 100.20, 100.80, 100.00, 100.60, 9_000),
        ("2026-06-01 09:40:00", 100.60, 101.00, 100.30, 100.90, 8_000),
        ("2026-06-01 09:45:00", 100.90, 105.50, 100.80, 105.00, 50_000),  # bar N
        ("2026-06-01 09:50:00", 103.50, 104.00, 103.00, 103.80, 20_000),  # bar N+1 (gap down)
        ("2026-06-01 09:55:00", 103.80, 108.00, 103.70, 107.50, 30_000),  # last bar, no N+1
    ]
    bars = []
    running_hod = float("-inf")
    running_lod = float("inf")
    cum_volume = 0
    for time_key, o, h, l, c, v in raw:
        running_hod = max(running_hod, h)
        running_lod = min(running_lod, l)
        cum_volume += v
        bars.append({
            "code": "US.TEST",
            "time_key": time_key,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "hod": running_hod,
            "lod": running_lod,
            "cum_volume": cum_volume,
        })
    return bars


def make_trade_log():
    """Return 4 closed-trade dicts (2 winners, 2 losers) with documented expected metrics.

    Trade-log contract keys: code, entry_price, exit_price, quantity, exit_reason,
    r_multiple, closed_at. realized_pnl is DERIVED by compute_metrics as
    (exit_price - entry_price) * quantity (not stored here).

    Hand-computed expected metrics (chronological order by closed_at, as required for
    max-drawdown):
        Trade 1 US.AAA: pnl=(110-100)*100=+1000.00  (win)
        Trade 2 US.BBB: pnl=(95-100)*100=-500.00    (loss)
        Trade 3 US.CCC: pnl=(54-50)*200=+800.00     (win)
        Trade 4 US.DDD: pnl=(45-50)*200=-1000.00    (loss)

        win_rate       = 2/4               = 0.5
        avg_r_multiple = (2.0-1.0+1.5-1.0)/4 = 0.375
        gross_profit   = 1000+800          = 1800.00
        gross_loss     = abs(-500-1000)    = 1500.00
        profit_factor  = 1800/1500         = 1.2
        total_trades   = 4

        Running cumulative PnL / peak / drawdown (chronological):
            after T1: cum=1000  peak=1000  dd=0
            after T2: cum=500   peak=1000  dd=500
            after T3: cum=1300  peak=1300  dd=500   (peak updates before dd each step)
            after T4: cum=300   peak=1300  dd=1000  <- max_drawdown_usd = 1000.00
    """
    return [
        {
            "code": "US.AAA", "entry_price": 100.00, "exit_price": 110.00, "quantity": 100,
            "exit_reason": "TARGET", "r_multiple": 2.0, "closed_at": "2026-06-01 10:00:00",
        },
        {
            "code": "US.BBB", "entry_price": 100.00, "exit_price": 95.00, "quantity": 100,
            "exit_reason": "STOP", "r_multiple": -1.0, "closed_at": "2026-06-01 11:00:00",
        },
        {
            "code": "US.CCC", "entry_price": 50.00, "exit_price": 54.00, "quantity": 200,
            "exit_reason": "TRAIL", "r_multiple": 1.5, "closed_at": "2026-06-02 10:00:00",
        },
        {
            "code": "US.DDD", "entry_price": 50.00, "exit_price": 45.00, "quantity": 200,
            "exit_reason": "STOP", "r_multiple": -1.0, "closed_at": "2026-06-02 11:00:00",
        },
    ]
